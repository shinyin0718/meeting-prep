"""Live smoke test against the real Gemini API, skipped by default:

    MEETING_PREP_LIVE=1 uv run pytest tests/test_live.py

It uses 1–2 of the free tier's ~20 daily requests per model. m_005 is internal-only, so no Tavily
search runs and no TAVILY_API_KEY is needed."""

import os

import pytest

from meeting_prep import cli

pytestmark = pytest.mark.skipif(os.environ.get("MEETING_PREP_LIVE") != "1",
                                reason="live smoke test; set MEETING_PREP_LIVE=1 and GEMINI_API_KEY to run")

SECTIONS = ["## Meeting at a glance", "## Who's in the room", "## History and open items", "## Likely asks",
            "## Suggested questions and talking points", "## Risks and watch-outs"]


def test_live_gemini_writes_a_complete_brief(tmp_path, capsys):
    if not os.environ.get("GEMINI_API_KEY"):
        pytest.fail("MEETING_PREP_LIVE=1 but GEMINI_API_KEY is not set")
    rc = cli.main(["prep", "--meeting-id", "m_005", "--output-dir", str(tmp_path)])
    assert rc == 0, capsys.readouterr().err
    text = (tmp_path / "prep_m_005.md").read_text()
    positions = [text.index(h) for h in SECTIONS]
    assert positions == sorted(positions)
    assert "**Purpose:** No record." not in text  # Gemini's judgment parts came back
    assert len(text.split()) < 700
