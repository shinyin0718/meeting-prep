"""Eval script: runs three scenarios end to end and checks each brief for key strings.

    uv run evals.py                        # Gemini and Tavily mocked (default; no keys needed)
    uv run evals.py --live                 # real Gemini + Tavily; uses free-tier quota
    uv run evals.py --scenario no_agenda   # one scenario only

Exit code 0 when every check passes, 1 otherwise. Briefs are written to output/evals/.
"""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from meeting_prep import cli
from meeting_prep.fakes import FixtureSearch, ScriptedLLM, json_reply

ROOT = Path(__file__).resolve().parent
MOCK_TODAY = "2026-01-15"  # the search and news fixtures are dated around this day
SEED_OWN_DOMAINS = "lumora-analytics.com"
MAX_WORDS = 700
CORE_SECTIONS = ["## Meeting at a glance", "## Who's in the room", "## History and open items", "## Likely asks",
                 "## Suggested questions and talking points", "## Risks and watch-outs"]
URL_RE = re.compile(r"https?://[^\s)\]>]+")
TOM = "tom.becker@harbourviewhealth.com"

BASE_SYNTHESIS = {
    "purpose": "Prepare for the meeting using the records on file.",
    "desired_outcome": "Agree concrete next steps and owners.",
    "relationships": {},
    "likely_asks": [{"party": "Us", "ask": "Confirm next steps.", "based_on": "open items"}],
    "questions": [{"text": "What would make this a success for you?", "based_on": "agenda"},
                  {"text": "Who else needs to sign off?", "based_on": "attendee list"},
                  {"text": "What is the timeline?", "based_on": "open items"}],
    "risks": [{"text": "Overdue items may come up.", "based_on": "open items"}],
}


@dataclass
class Scenario:
    name: str
    meeting_id: str
    description: str
    must_include: list[str]
    must_not_include: list[str] = field(default_factory=list)
    mocked_only: list[str] = field(default_factory=list)  # depend on fixture data, so skipped with --live
    synthesis: dict = field(default_factory=dict)  # the mocked Gemini reply


SCENARIOS = [
    Scenario(
        "normal", "m_001", "Renewal review with known contacts",
        must_include=["Aisha Rahman", "Grace Liu", "Priya Raman", "## Company snapshot",
                      "Return BAA redlines on data retention clauses", "**— overdue**",
                      "Deliver renewal pricing proposal"],
        must_not_include=["## Background on new attendees", "## From your materials"],
        mocked_only=["Named a first Chief Digital Officer.", "## Sources"],
        synthesis={**BASE_SYNTHESIS, "news": {"N1": {"summary": "Named a first Chief Digital Officer.",
                                                     "why": "A new sign-off on data spend.", "relevance": 3}}},
    ),
    Scenario(
        "new_contact", "m_002", "Pilot scoping with first-time attendee Tom Becker",
        must_include=["Tom Becker", "First meeting: no prior interactions on record", "## Background on new attendees",
                      "_Public web sources, unverified._"],
        must_not_include=["university"],  # an invented relationship must be dropped
        mocked_only=["Joined Harbourview Health as Data Platform Lead", "## Sources"],
        synthesis={**BASE_SYNTHESIS, "relationships": {TOM: "Long-time friend from university."},
                   "background": {"Tom Becker": [{"text": "Joined Harbourview Health as Data Platform Lead.",
                                                  "sources": ["S1"]}]}},
    ),
    Scenario(
        "no_agenda", "m_003", "Intro call with no agenda and an ambiguous name (John Smith)",
        must_include=["no agenda on record", "Carlos Mendes", "John Smith", "couldn't confirm identity",
                      "Send grid demand forecasting case study"],
        mocked_only=["No notable developments found in the last 90 days."],
        synthesis=BASE_SYNTHESIS,
    ),
]


@contextmanager
def _env(**values: str) -> Iterator[None]:
    old = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def check_brief(s: Scenario, text: str, *, live: bool) -> list[str]:
    """Failed checks for one brief, as readable lines (empty when it passes)."""
    failures = []
    missing = [h for h in CORE_SECTIONS if h not in text]
    if missing:
        failures.append(f"missing sections: {missing}")
    elif [text.index(h) for h in CORE_SECTIONS] != sorted(text.index(h) for h in CORE_SECTIONS):
        failures.append("sections out of order")
    expected = s.must_include + ([] if live else s.mocked_only)
    failures += [f"must include: {x!r}" for x in expected if x not in text]
    failures += [f"must not include: {x!r}" for x in s.must_not_include if x in text]
    words = len(text.split())
    if words >= MAX_WORDS:
        failures.append(f"{words} words; must be under {MAX_WORDS}")
    if not live:
        stray = set(URL_RE.findall(text)) - FixtureSearch().urls()
        if stray:
            failures.append(f"URLs not from search results: {sorted(stray)}")
    for key in ("GEMINI_API_KEY", "TAVILY_API_KEY"):
        if os.environ.get(key) and os.environ[key] in text:
            failures.append(f"{key} appears in the brief")
    return failures


def run_scenario(s: Scenario, out_dir: Path, *, live: bool) -> tuple[Path, list[str]]:
    argv = ["prep", "--meeting-id", s.meeting_id, "--output-dir", str(out_dir)]
    if live:
        rc = cli.main(argv)
    else:
        rc = cli.main(argv, llm=ScriptedLLM(json_reply(s.synthesis)), searcher=FixtureSearch())
    path = out_dir / f"prep_{s.meeting_id}.md"
    if rc != 0:
        return path, [f"prep exited with code {rc} (see the error above)"]
    return path, check_brief(s, path.read_text(encoding="utf-8"), live=live)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.py", description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="Use real Gemini and Tavily (needs both keys).")
    parser.add_argument("--scenario", choices=[s.name for s in SCENARIOS], help="Run one scenario only.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output" / "evals")
    args = parser.parse_args(argv)
    load_dotenv(ROOT / ".env")
    scenarios = [s for s in SCENARIOS if args.scenario in (None, s.name)]
    # The scenarios use the seed data, whose own company is Lumora Analytics, in both modes.
    env = {"OWN_COMPANY_DOMAINS": os.environ.get("OWN_COMPANY_DOMAINS") or SEED_OWN_DOMAINS}
    if not args.live:
        env["MEETING_PREP_TODAY"] = MOCK_TODAY
    passed = 0
    # An empty attachments folder, so files attached in earlier real runs don't change the result.
    with tempfile.TemporaryDirectory() as attachments, _env(MEETING_PREP_ATTACHMENTS_DIR=attachments, **env):
        print(f"Running {len(scenarios)} scenario(s), {'live' if args.live else 'mocked'}.")
        for s in scenarios:
            path, failures = run_scenario(s, args.output_dir, live=args.live)
            passed += not failures
            print(f"{'PASS' if not failures else 'FAIL'}  {s.name} ({s.meeting_id}): {s.description} -> {path}")
            for f in failures:
                print(f"      - {f}")
    print(f"{passed}/{len(scenarios)} scenarios passed")
    return 0 if passed == len(scenarios) else 1


if __name__ == "__main__":
    raise SystemExit(main())
