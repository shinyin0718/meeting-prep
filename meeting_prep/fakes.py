"""Stand-ins for Gemini and Tavily, shared by the tests and `evals.py` (mocked mode)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .llm import LLMReply
from .web_search import SearchResult, parse_results

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


class ScriptedChat:
    def __init__(self, owner: ScriptedLLM):
        self.owner = owner

    async def send(self, message, *, allow_tools=True):
        self.owner.sent.append((message, allow_tools))
        script = self.owner.script
        reply = script.pop(0) if len(script) > 1 else script[0]
        return reply(message) if callable(reply) else reply


class ScriptedLLM:
    """Each send() returns the next scripted reply (an LLMReply, or a callable taking the message).
    The last reply repeats. `sent`, `system` and `tools` record what the agent passed in."""

    def __init__(self, *script):
        if not script:
            raise ValueError("ScriptedLLM needs at least one reply")
        self.script = list(script)
        self.sent: list = []
        self.system: str | None = None
        self.tools: list | None = None

    def start_chat(self, system, tools):
        self.system, self.tools = system, tools
        return ScriptedChat(self)


def json_reply(data: dict[str, Any]) -> LLMReply:
    return LLMReply(text="```json\n" + json.dumps(data) + "\n```")


class FixtureSearch:
    """Person searches: <search_dir>/<first_last>.json when that name is in the query.
    Company news searches (the ones passing start_date): <news_dir>/<domain>.json when the fixture's
    company is in the query. Neither filters by date, so the date checks are the code's own."""

    def __init__(self, error: Exception | None = None, search_dir: Path = FIXTURES / "search",
                 news_dir: Path = FIXTURES / "news"):
        self.requests: list[dict] = []
        self.error = error
        self.search_dir, self.news_dir = search_dir, news_dir

    @property
    def person_calls(self) -> list[str]:
        return [r["query"] for r in self.requests if r["start_date"] is None]

    @property
    def news_calls(self) -> list[dict]:
        return [r for r in self.requests if r["start_date"] is not None]

    async def search(self, query, *, max_results=5, topic="general", start_date=None) -> list[SearchResult]:
        self.requests.append({"query": query, "topic": topic, "start_date": start_date, "max_results": max_results})
        if self.error:
            raise self.error
        if start_date is None:
            for path in sorted(self.search_dir.glob("*.json")):
                if path.stem.replace("_", " ") in query.lower():
                    return parse_results(json.loads(path.read_text()))[:max_results]
            return []
        for path in sorted(self.news_dir.glob("*.json")):
            data = json.loads(path.read_text())
            if data["company"].lower() in query.lower():
                return parse_results(data)[:max_results]
        return []

    def urls(self) -> set[str]:
        """Every URL in the fixtures: a brief's links must be a subset."""
        return {r["url"] for d in (self.search_dir, self.news_dir) for p in d.glob("*.json")
                for r in json.loads(p.read_text())["results"]}
