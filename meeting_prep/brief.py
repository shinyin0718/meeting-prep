"""Renders the Markdown brief. Factual sections come straight from tool results."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

NO_HISTORY = "no prior interactions on record"
SHOWN_HISTORY = 3
TAG = "_(internal record)_"
UNCONFIRMED = "couldn't confirm identity"
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
NO_NEWS = "No notable developments found in the last {days} days."
MAX_NEWS_ITEMS = 5
MAX_MATERIAL_POINTS = 3
MATERIAL_POINT_BUDGET = 9  # across all files, to stay on one page


def _when(meeting: dict) -> str:
    start = datetime.fromisoformat(meeting["start"])
    end = datetime.fromisoformat(meeting["end"])
    return f"{start:%a %d %b %Y, %H:%M}–{end:%H:%M} {start:%Z}".strip() + f" ({meeting['duration_minutes']} min)"


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def _items(entries: list, render) -> list[str]:
    lines = [render(e) for e in entries if isinstance(e, dict)]
    lines = [line for line in lines if line]
    return lines or ["- No record."]


def _based_on(entry: dict) -> str:
    basis = _clean(entry.get("based_on"))
    return f" _(based on: {basis})_" if basis else ""


def _claims(entry: dict, background: dict[str, Any], cites: dict[str, int]) -> list[str]:
    allowed = {s["id"] for s in entry["sources"]}
    lines = []
    for claim in background.get(entry["name"].lower()) or []:
        if not isinstance(claim, dict):
            continue
        text = _clean(claim.get("text"))
        ids = claim.get("sources") or []
        ids = [ids] if isinstance(ids, str) else ids
        ids = [i for i in dict.fromkeys(str(i) for i in ids) if i in allowed]
        if not text or not ids or URL_RE.search(text):
            continue
        refs = "".join(f"[{cites.setdefault(i, len(cites) + 1)}]" for i in ids)
        lines.append(f"  - {text} {refs}")
    return lines


def _background(research: list[dict], synthesis: dict[str, Any], cites: dict[str, int]) -> list[str]:
    if not research:
        return []
    background = {str(k).strip().lower(): v for k, v in (synthesis.get("background") or {}).items()}
    out = ["## Background on new attendees", "", "_Public web sources, unverified._", ""]
    for entry in research:
        head = f"- **{entry['name']}** ({entry['company']})" + (", researched on request" if entry["manual"] else "")
        status = entry["status"]
        if status == "confirmed":
            lines = _claims(entry, background, cites)
            out.append(head + ":" if lines else head + ": nothing stated (no fact could be tied to a dated source).")
            out += lines
        elif status == "unconfirmed":
            out.append(f"{head}: {UNCONFIRMED}. Public results didn't match name and company together, "
                       "so nothing is stated about this person.")
        elif status == "undated":
            out.append(f"{head}: nothing stated (matching public results carried no publication date).")
        else:
            out.append(f"{head}: no public results found.")
    out.append("")
    return out


def _news_rows(entry: dict, notes: dict[str, Any]) -> list[dict]:
    """In-window items minus those the model rated 0, ranked by relevance, then newest first; at most 5."""
    rows = []
    for item in sorted(entry["items"], key=lambda i: i["date"], reverse=True):
        note = notes.get(item["id"])
        note = note if isinstance(note, dict) else {}
        try:
            relevance = int(note.get("relevance", 1))
        except (TypeError, ValueError):
            relevance = 1
        if relevance <= 0:
            continue
        summary, why = _clean(note.get("summary")), _clean(note.get("why"))
        rows.append({**item, "relevance": relevance,
                     "summary": summary if summary and not URL_RE.search(summary) else _clean(item["title"]),
                     "why": "" if URL_RE.search(why) else why,
                     "unconfirmed": item["unconfirmed"] or note.get("unconfirmed") is True})
    rows.sort(key=lambda r: -r["relevance"])  # stable, so newest first within a relevance level
    return rows[:MAX_NEWS_ITEMS]


def _snapshot(news: list[dict], rows: dict[str, list[dict]], cites: dict[str, int]) -> list[str]:
    if not news:
        return []
    days = news[0]["days"]
    out = ["## Company snapshot", "", f"_Public web sources, last {days} days; ranked by relevance, then date._", ""]
    for entry in news:
        out += [f"**{entry['company']}** ({entry['domain']})", ""]
        if not rows[entry["domain"]]:
            out.append(f"- {NO_NEWS.format(days=days)}")
        for r in rows[entry["domain"]]:
            label = "_(unconfirmed)_ " if r["unconfirmed"] else ""
            why = f" _Why it matters:_ {r['why']}" if r["why"] else ""
            out.append(f"- {r['date']} · {label}{r['summary']}{why} [{cites.setdefault(r['id'], len(cites) + 1)}]")
        out.append("")
    return out


def _sources(research: list[dict], news: list[dict], cites: dict[str, int]) -> list[str]:
    if not cites:
        return []
    by_id = {s["id"]: s for entry in research for s in entry["sources"]}
    by_id |= {i["id"]: i for entry in news for i in entry["items"]}
    out = ["## Sources", ""]
    for source_id, n in sorted(cites.items(), key=lambda kv: kv[1]):
        s = by_id[source_id]
        out.append(f"{n}. [{_clean(s['title'])}]({s['url']}), {s['date']}")
    out.append("")
    return out


def _filename(name: str) -> str:
    return "`" + _clean(name).replace("`", "'") + "`"


def _materials(materials: list[dict], synthesis: dict[str, Any]) -> list[str]:
    if not materials:
        return []
    notes = {str(k).strip().upper(): v for k, v in (synthesis.get("materials") or {}).items()}
    per_file = max(1, min(MAX_MATERIAL_POINTS, MATERIAL_POINT_BUDGET // len(materials)))
    out = ["## From your materials", ""]
    for m in materials:
        name = _filename(m["file"])
        if m["status"] != "ok":
            out.append(f"- {name}: couldn't read this file ({m['reason']}); skipped.")
            continue
        points = notes.get(m["id"])
        points = [points] if isinstance(points, str) else points if isinstance(points, list) else []
        points = [p for p in (_clean(x) for x in points if isinstance(x, str)) if p and not URL_RE.search(p)]
        out += [f"- {p} _(file: {name})_" for p in points[:per_file]] or [f"- {name}: no key points for this meeting."]
        if m["truncated"]:
            out.append(f"- Only the first {m['chars']:,} characters of {name} were read.")
    return out + [""]


def render_brief(context: dict[str, Any], synthesis: dict[str, Any]) -> str:
    meeting = context["meeting"]
    people = context["people"]
    names = {p["profile"]["email"]: p["profile"]["name"] for p in people}
    relationships = {k.lower(): v for k, v in (synthesis.get("relationships") or {}).items()}
    research = context.get("research") or []
    news = context.get("news") or []
    notes = {str(k).strip().upper(): v for k, v in (synthesis.get("news") or {}).items()}
    rows = {entry["domain"]: _news_rows(entry, notes) for entry in news}
    cites: dict[str, int] = {}
    out: list[str] = [f"# Prep brief: {meeting['title']}", "", f"{_when(meeting)} · meeting `{meeting['id']}`", ""]

    out += ["## Meeting at a glance", ""]
    out.append(f"- **Purpose:** {_clean(synthesis.get('purpose')) or 'No record.'}")
    out.append(f"- **Desired outcome:** {_clean(synthesis.get('desired_outcome')) or 'No record.'}")
    if meeting["agenda"]:
        out.append(f"- **Agenda** {TAG}:")
        out += [f"  {n}. {_clean(item)}" for n, item in enumerate(meeting["agenda"], 1)]
    else:
        out.append(f"- **Agenda:** no agenda on record {TAG}.")
    out.append("")

    out += _snapshot(news, rows, cites)

    out += ["## Who's in the room", ""]
    for person in people:
        p = person["profile"]
        side = "our side" if p["is_internal"] else "external"
        tenure = _clean(p.get("tenure")).rstrip(".")
        line = f"- **{p['name']}** — {p['role']}, {p['company']} ({side})." + (f" {tenure}." if tenure else "")
        if person["history"]:
            last = person["history"][0]
            line += f" Last interaction: {last['date']} ({last['type']}) {TAG}."
            rel = _clean(relationships.get(p["email"].lower()))
            if rel and rel.lower() != "no record":
                line += f" Relationship: {rel}"
        else:
            line += f" First meeting: {NO_HISTORY}." if p["first_meeting"] else f" {NO_HISTORY.capitalize()}."
        out.append(line)
    out.append("")

    out += _background(research, synthesis, cites)

    out += ["## History and open items", "", "**Recent interactions**", ""]
    for person in people:
        name = person["profile"]["name"]
        if not person["history"]:
            out.append(f"- {name}: {NO_HISTORY}.")
        for h in person["history"][:SHOWN_HISTORY]:
            out.append(f"- {h['date']} · {name} · {h['type']}: {_clean(h['summary'])} {TAG}")
    out += ["", "**Open items**", ""]
    if not context["open_items"]:
        out.append("- No open items on record.")
    for item in context["open_items"]:
        who = names.get(item.get("person_email") or "", None)
        about = f" (re: {who})" if who else ""
        overdue = " **— overdue**" if item.get("overdue") else ""
        out.append(f"- {_clean(item['description'])}{about} — owner {item['owner']}, due {item['due_date']}{overdue} {TAG}")
    out.append("")

    out += _materials(context.get("materials") or [], synthesis)

    out += ["## Likely asks", ""]
    out += _items(synthesis.get("likely_asks", []), lambda e: (
        f"- **{_clean(e.get('party')) or 'Unknown'}:** {_clean(e.get('ask'))}{_based_on(e)}" if _clean(e.get("ask")) else ""))
    out += ["", "## Suggested questions and talking points", ""]
    questions = [e for e in synthesis.get("questions", []) if isinstance(e, dict) and _clean(e.get("text"))][:5]
    out += [f"{n}. {_clean(e['text'])}{_based_on(e)}" for n, e in enumerate(questions, 1)] or ["- No record."]
    out += ["", "## Risks and watch-outs", ""]
    unconfirmed = [f"- {UNCONFIRMED.capitalize()} of {e['name']} from public sources; confirm their role "
                   "and background directly. _(based on: web search)_"
                   for e in research if e["status"] == "unconfirmed"]
    risks = [line for line in _items(synthesis.get("risks", []), lambda e: (
        f"- {_clean(e.get('text'))}{_based_on(e)}" if _clean(e.get("text")) else "")) if line != "- No record."]
    unconfirmed += [f"- Unconfirmed report on {e['company']}: {r['summary'].rstrip('.')}. Don't present it as fact. "
                    "_(based on: web search)_" for e in news for r in rows[e["domain"]] if r["unconfirmed"]]
    out += unconfirmed + risks or ["- No record."]
    out.append("")
    out += _sources(research, news, cites)
    return "\n".join(out)
