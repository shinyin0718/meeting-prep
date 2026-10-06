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


def _sources(research: list[dict], cites: dict[str, int]) -> list[str]:
    if not cites:
        return []
    by_id = {s["id"]: s for entry in research for s in entry["sources"]}
    out = ["## Sources", ""]
    for source_id, n in sorted(cites.items(), key=lambda kv: kv[1]):
        s = by_id[source_id]
        out.append(f"{n}. [{_clean(s['title'])}]({s['url']}), {s['date']}")
    out.append("")
    return out


def render_brief(context: dict[str, Any], synthesis: dict[str, Any]) -> str:
    meeting = context["meeting"]
    people = context["people"]
    names = {p["profile"]["email"]: p["profile"]["name"] for p in people}
    relationships = {k.lower(): v for k, v in (synthesis.get("relationships") or {}).items()}
    research = context.get("research") or []
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
    out += unconfirmed + risks or ["- No record."]
    out.append("")
    out += _sources(research, cites)
    return "\n".join(out)
