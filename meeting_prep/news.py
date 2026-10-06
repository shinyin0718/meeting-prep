"""Company latest developments: one search pass per external company, kept only if dated and in the window."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from .mydata import real_domain
from .web_search import SearchResult, WebSearch

DEFAULT_NEWS_DAYS = 90
SEARCHES_PER_COMPANY = 3
MAX_SEARCHES_PER_COMPANY = 5
RESULTS_PER_SEARCH = 10
MAX_CANDIDATES = 8
assert 3 <= SEARCHES_PER_COMPANY <= MAX_SEARCHES_PER_COMPANY

# Press wires and filings count as primary sources alongside the company's own domain.
PRIMARY_HOSTS = ("sec.gov", "businesswire.com", "prnewswire.com", "globenewswire.com")
ESTABLISHED_HOSTS = (
    "reuters.com", "bloomberg.com", "ft.com", "wsj.com", "apnews.com", "cnbc.com", "nytimes.com", "bbc.com",
    "bbc.co.uk", "theguardian.com", "economist.com", "forbes.com", "axios.com", "techcrunch.com",
    "fiercehealthcare.com", "statnews.com", "healthcaredive.com", "freightwaves.com", "supplychaindive.com",
    "joc.com", "utilitydive.com", "spglobal.com",
)
RUMOR_RE = re.compile(
    r"\b(rumou?r(?:s|ed)?|reportedly|sources (?:say|said)|people familiar with|said to be|in talks|"
    r"speculat\w*|unconfirmed)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Company:
    name: str
    domain: str


def today() -> date:
    override = os.environ.get("MEETING_PREP_TODAY")
    return date.fromisoformat(override) if override else datetime.now().astimezone().date()


def companies_for(context: dict[str, Any]) -> list[Company]:
    """External companies, one per email domain (already deduplicated, own domains excluded)."""
    names: dict[str, str] = {}
    for person in context["people"]:
        p = person["profile"]
        names.setdefault((p.get("company_domain") or "").lower(), p["company"])
    return [Company(names.get(d, d), d) for d in context["external_domains"]]


def queries(c: Company) -> list[tuple[str, str]]:
    """(query, Tavily topic). The domain disambiguates companies with common names."""
    domain = f" {real_domain(c.domain)}" if real_domain(c.domain) else ""
    return [
        (f'"{c.name}"{domain} announcement OR press release', "general"),
        (f'"{c.name}" funding OR acquisition OR partnership OR earnings OR launch', "news"),
        (f'"{c.name}" CEO OR leadership OR layoffs OR restructuring OR lawsuit OR regulator', "news"),
    ][:SEARCHES_PER_COMPANY]


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _on(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_about(c: Company, r: SearchResult) -> bool:
    text = f"{r.title} {r.content} {r.url}".lower()
    return c.name.lower() in text or c.domain.lower() in text


def source_type(c: Company, r: SearchResult) -> str:
    host = _host(r.url)
    if _on(host, (c.domain.lower(), *PRIMARY_HOSTS)):
        return "primary"
    return "established" if _on(host, ESTABLISHED_HOSTS) else "other"


def is_rumor(r: SearchResult) -> bool:
    return bool(RUMOR_RE.search(f"{r.title} {r.content}"))


async def company_news(searcher: WebSearch, companies: list[Company], *, days: int, today: date) -> list[dict[str, Any]]:
    """One entry per company with its dated, in-window items, newest first. Ranking happens in the renderer."""
    start = (today - timedelta(days=days)).isoformat()
    end = today.isoformat()
    entries = []
    item_no = 0
    for c in companies:
        found: dict[str, SearchResult] = {}
        searches = queries(c)
        for query, topic in searches:
            for r in await searcher.search(query, max_results=RESULTS_PER_SEARCH, topic=topic, start_date=start):
                found.setdefault(r.url, r)
        # Undated items are dropped: recency can't be judged. Older items are dropped: no fallback to old news.
        in_window = [r for r in found.values()
                     if is_about(c, r) and r.published_date and start <= r.published_date <= end]
        in_window.sort(key=lambda r: r.published_date or "", reverse=True)
        items = []
        for r in in_window[:MAX_CANDIDATES]:
            item_no += 1
            kind = source_type(c, r)
            items.append({"id": f"N{item_no}", "url": r.url, "title": r.title, "date": r.published_date,
                          "content": r.content, "source_type": kind,
                          "unconfirmed": kind == "other" or is_rumor(r)})
        entries.append({"company": c.name, "domain": c.domain, "days": days, "searches": len(searches),
                        "items": items, "returned_urls": sorted(found)})
    return entries
