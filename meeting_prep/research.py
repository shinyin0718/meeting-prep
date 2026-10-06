"""First-time contact research: who to research (deterministic), what to search, which results to trust."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .web_search import SearchResult, WebSearch

MAX_SEARCHES_PER_PERSON = 5
QUERIES_PER_PERSON = 3
RESULTS_PER_QUERY = 5
MAX_SOURCES_PER_PERSON = 4
assert 3 <= QUERIES_PER_PERSON <= MAX_SEARCHES_PER_PERSON


@dataclass(frozen=True)
class Target:
    name: str
    company: str
    role: str | None = None
    domain: str | None = None
    email: str | None = None
    manual: bool = False


def parse_research_request(value: str) -> tuple[str, str]:
    name, sep, company = value.partition(",")
    if not sep or not name.strip() or not company.strip():
        raise ValueError(f'--research expects "Name, Company", got {value!r}.')
    return name.strip(), company.strip()


def targets_for(context: dict[str, Any], requests: list[tuple[str, str]] | tuple = ()) -> list[Target]:
    """First-time external attendees (from the `first_meeting` flag), then manual --research requests."""
    profiles = [p["profile"] for p in context["people"]]
    targets: dict[str, Target] = {}

    def from_profile(p: dict, manual: bool) -> Target:
        return Target(p["name"], p["company"], p.get("role"), p.get("company_domain"), p["email"], manual)

    for p in profiles:
        if p["first_meeting"]:
            targets.setdefault(p["name"].lower(), from_profile(p, manual=False))
    for name, company in requests:
        match = next((p for p in profiles if p["name"].lower() == name.lower()), None)
        target = from_profile(match, manual=True) if match else Target(name, company, manual=True)
        targets.setdefault(name.lower(), target)
    return list(targets.values())


def queries(t: Target) -> list[str]:
    """Name, company, role and email domain together: a common name alone finds the wrong person."""
    first = " ".join([f'"{t.name}"', f'"{t.company}"', *([t.role] if t.role else [])])
    second = f'"{t.name}" {t.domain}' if t.domain else f'"{t.name}" {t.company} profile'
    third = f'"{t.name}" {t.company} interview OR talk OR article'
    return [first, second, third][:QUERIES_PER_PERSON]


def is_match(t: Target, r: SearchResult) -> bool:
    text = f"{r.title} {r.content} {r.url}".lower()
    affiliations = {t.company.lower()}
    if t.domain:
        affiliations |= {t.domain.lower(), t.domain.lower().split(".")[0]}
    return t.name.lower() in text and any(a in text for a in affiliations if a)


async def research(searcher: WebSearch, targets: list[Target]) -> list[dict[str, Any]]:
    """Returns one entry per target. Only dated results that match name + company become citable sources."""
    entries = []
    source_no = 0
    for t in targets:
        found: dict[str, SearchResult] = {}
        searches = queries(t)
        for q in searches:
            for r in await searcher.search(q, max_results=RESULTS_PER_QUERY):
                found.setdefault(r.url, r)
        matched = [r for r in found.values() if is_match(t, r)]
        dated = [r for r in matched if r.published_date]
        if dated:
            status = "confirmed"
        elif matched:
            status = "undated"
        else:
            status = "unconfirmed" if found else "no_results"
        sources = []
        for r in dated[:MAX_SOURCES_PER_PERSON]:
            source_no += 1
            sources.append({"id": f"S{source_no}", "url": r.url, "title": r.title,
                            "date": r.published_date, "content": r.content})
        entries.append({**asdict(t), "status": status, "searches": len(searches),
                        "sources": sources, "returned_urls": sorted(found)})
    return entries
