"""`main.py prep --meeting-id <id> | --next` -> output/prep_<id>.md"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .agent import AgentError, gather_context, synthesize
from .brief import render_brief
from .llm import LLM, GeminiLLM, LLMError, MissingAPIKeyError, require_gemini_key
from .mcp_client import ToolCallError, connect

ROOT = Path(__file__).resolve().parent.parent
NEXT_WINDOW_DAYS = 7


def own_domains() -> set[str]:
    return {d.strip().lower() for d in os.environ.get("OWN_COMPANY_DOMAINS", "").split(",") if d.strip()}


async def run_prep(meeting_id: str | None, *, llm: LLM, output_dir: Path) -> Path:
    # Errors are re-raised outside the MCP context so they don't arrive wrapped in an ExceptionGroup.
    error: Exception | None = None
    async with connect() as tools:
        try:
            if meeting_id is None:
                upcoming = await tools.call_ok("list_upcoming_meetings", {"days_ahead": NEXT_WINDOW_DAYS})
                if not upcoming:
                    raise ToolCallError(
                        f"No meetings in the next {NEXT_WINDOW_DAYS} days; pass --meeting-id <id> instead.")
                meeting_id = upcoming[0]["id"]
            context = await gather_context(tools, meeting_id, own_domains())
            synthesis = await synthesize(llm, tools, context)
        except (ToolCallError, AgentError, LLMError) as exc:
            error = exc
    if error is not None:
        raise error
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"prep_{context['meeting']['id']}.md"
    path.write_text(render_brief(context, synthesis), encoding="utf-8")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py", description="Meeting prep brief generator.")
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prep", help="Write a one-page prep brief for a meeting.")
    target = prep.add_mutually_exclusive_group(required=True)
    target.add_argument("--meeting-id", help="Meeting id, e.g. m_001.")
    target.add_argument("--next", action="store_true", help=f"Earliest meeting in the next {NEXT_WINDOW_DAYS} days.")
    prep.add_argument("--output-dir", type=Path, default=ROOT / "output", help="Where to write prep_<id>.md.")
    return parser


def main(argv: list[str] | None = None, llm: LLM | None = None) -> int:
    load_dotenv(ROOT / ".env")
    args = build_parser().parse_args(argv)
    try:
        if llm is None:
            llm = GeminiLLM(api_key=require_gemini_key())
        path = asyncio.run(run_prep(None if args.next else args.meeting_id, llm=llm, output_dir=args.output_dir))
    except MissingAPIKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (ToolCallError, AgentError, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {path}")
    return 0
