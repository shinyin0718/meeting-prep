"""Tavily-backed `web_search` behind a small interface so tests can swap in a fake."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx

from .llm import MissingAPIKeyError

TAVILY_URL = "https://api.tavily.com/search"

_HTTP_ERRORS = {
    401: "Tavily rejected TAVILY_API_KEY (401). Check the key at https://app.tavily.com.",
    429: "Tavily rate limit hit (429). Wait a minute and try again.",
    432: "Tavily plan limit reached (432): this month's free credits are used up. See https://app.tavily.com.",
    433: "Tavily pay-as-you-go limit reached (433). Raise it on the Tavily dashboard.",
}


class SearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    content: str = ""
    published_date: str | None = None


class WebSearch(Protocol):
    async def search(self, query: str, *, max_results: int = 5, topic: str = "general",
                     start_date: str | None = None) -> list[SearchResult]: ...


def require_tavily_key() -> str:
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        raise MissingAPIKeyError(
            "TAVILY_API_KEY is not set, and this brief needs a web search. Get a free key at "
            "https://app.tavily.com, then add TAVILY_API_KEY=... to .env (see .env.example)."
        )
    return key


def _iso_date(value: Any) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(str(value)).date().isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def parse_results(payload: dict[str, Any]) -> list[SearchResult]:
    results = []
    for r in payload.get("results") or []:
        if isinstance(r, dict) and r.get("url") and r.get("title"):
            results.append(SearchResult(url=str(r["url"]), title=" ".join(str(r["title"]).split()),
                                        content=str(r.get("content") or ""),
                                        published_date=_iso_date(r.get("published_date"))))
    return results


class TavilySearch:
    def __init__(self, api_key: str | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 20.0):
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    async def search(self, query: str, *, max_results: int = 5, topic: str = "general",
                     start_date: str | None = None) -> list[SearchResult]:
        key = self._api_key or require_tavily_key()
        body = {
            "query": query,
            "topic": topic,
            "search_depth": "basic",
            "max_results": max_results,
            "include_published_date": True,
            "include_answer": False,
            "include_raw_content": False,
        }
        if start_date:
            # Tavily drops results dated before start_date, and undated ones, when this filter is on.
            body |= {"start_date": start_date, "filter_by_published_date": True}
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
                response = await client.post(TAVILY_URL, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as exc:
            raise SearchError(f"Tavily request failed: {exc.__class__.__name__}: {exc}") from exc
        if response.status_code in _HTTP_ERRORS:
            raise SearchError(_HTTP_ERRORS[response.status_code])
        if response.status_code >= 400:
            raise SearchError(f"Tavily search failed ({response.status_code}): {response.text[:200]}")
        try:
            return parse_results(response.json())
        except ValueError as exc:
            raise SearchError("Tavily returned a response that isn't JSON.") from exc
