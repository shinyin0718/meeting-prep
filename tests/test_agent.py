import json
import re
import subprocess
from pathlib import Path

from meeting_prep import agent, cli
from meeting_prep.llm import LLMReply, ToolCall
from meeting_prep.mcp_client import _server_env
from meeting_prep.web_search import parse_results

ROOT = Path(__file__).resolve().parent.parent
SECTIONS = [
    "## Meeting at a glance",
    "## Who's in the room",
    "## History and open items",
    "## Likely asks",
    "## Suggested questions and talking points",
    "## Risks and watch-outs",
]
OMITTED = ["## Company snapshot", "## Background on new attendees", "## From your materials"]
AISHA = "aisha.rahman@harbourviewhealth.com"
TOM = "tom.becker@harbourviewhealth.com"

SYNTHESIS = {
    "purpose": "Review usage and agree renewal terms with Harbourview Health.",
    "desired_outcome": "Agreement on a renewal term and a signature timeline.",
    "relationships": {AISHA: "Main sponsor; engaged on renewal pricing.", TOM: "Long-time friend from university."},
    "likely_asks": [{"party": "Aisha Rahman", "ask": "Two-year pricing option.", "based_on": "email on renewal pricing"}],
    "questions": [
        {"text": "Which term length works for budget?", "based_on": "pricing request"},
        {"text": "When can BAA redlines come back?", "based_on": "overdue open item"},
        {"text": "Who is the new data platform lead?", "based_on": "last call"},
    ],
    "risks": [{"text": "BAA redlines are overdue.", "based_on": "open item"}],
}


class FakeChat:
    def __init__(self, owner):
        self.owner = owner

    async def send(self, message, *, allow_tools=True):
        self.owner.sent.append((message, allow_tools))
        script = self.owner.script
        reply = script.pop(0) if len(script) > 1 else script[0]
        return reply(message) if callable(reply) else reply


class FakeLLM:
    def __init__(self, *script):
        self.script = list(script) or [LLMReply(text=json.dumps(SYNTHESIS))]
        self.sent = []
        self.system = None
        self.tools = None

    def start_chat(self, system, tools):
        self.system, self.tools = system, tools
        return FakeChat(self)


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "search"


class FakeSearch:
    """Returns tests/fixtures/search/<first_last>.json when that person's name is in the query."""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def search(self, query, *, max_results=5, topic="general"):
        self.calls.append(query)
        if self.error:
            raise self.error
        for path in sorted(FIXTURE_DIR.glob("*.json")):
            if path.stem.replace("_", " ") in query.lower():
                return parse_results(json.loads(path.read_text()))[:max_results]
        return []


def final(data=SYNTHESIS):
    return LLMReply(text="```json\n" + json.dumps(data) + "\n```")


def run(tmp_path, *args, llm=None, searcher=None):
    llm = llm or FakeLLM()
    rc = cli.main(["prep", *args, "--output-dir", str(tmp_path)], llm=llm, searcher=searcher or FakeSearch())
    return rc, llm


def brief(tmp_path, meeting_id):
    return (tmp_path / f"prep_{meeting_id}.md").read_text()


# ---------- checkpoint: m_001 ----------


def test_m001_has_every_required_section_in_order(tmp_path):
    rc, _ = run(tmp_path, "--meeting-id", "m_001")
    assert rc == 0
    text = brief(tmp_path, "m_001")
    positions = [text.index(s) for s in SECTIONS]
    assert positions == sorted(positions)
    for heading in OMITTED:
        assert heading not in text


def test_m001_lists_every_attendee_and_open_item(tmp_path):
    run(tmp_path, "--meeting-id", "m_001")
    text = brief(tmp_path, "m_001")
    room = text.split("## Who's in the room")[1].split("## History")[0]
    for name in ("Priya Raman", "Aisha Rahman", "Grace Liu"):
        assert name in room
    items = text.split("**Open items**")[1]
    assert "Return BAA redlines on data retention clauses" in items
    assert "overdue" in items
    assert "Send revised SSO security questionnaire" in items


def test_m001_brief_is_one_page(tmp_path):
    run(tmp_path, "--meeting-id", "m_001")
    assert len(brief(tmp_path, "m_001").split()) < 700


def test_skill_is_loaded_into_system_prompt(tmp_path):
    _, llm = run(tmp_path, "--meeting-id", "m_001")
    assert "## Sections, in order" in llm.system
    assert "never" in llm.system and "instructions" in llm.system
    assert {t["name"] for t in llm.tools} == {
        "list_upcoming_meetings", "get_meeting", "get_person_profile",
        "get_interaction_history", "get_open_items"}
    first_message = llm.sent[0][0]
    assert "<internal_records>" in first_message and "</internal_records>" in first_message


def test_next_picks_earliest_upcoming_meeting(tmp_path):
    rc, _ = run(tmp_path, "--next")
    assert rc == 0
    assert (tmp_path / "prep_m_001.md").exists()


# ---------- scenarios ----------


def test_no_history_attendee_gets_no_record_and_no_invented_relationship(tmp_path):
    rc, _ = run(tmp_path, "--meeting-id", "m_002")
    assert rc == 0
    text = brief(tmp_path, "m_002")
    records = text.split("## Background on new attendees")[0] + text.split("## History and open items")[1]
    tom = [line for line in records.splitlines() if "Tom Becker" in line]
    assert tom and all("no prior interactions on record" in line.lower() for line in tom)
    assert "university" not in text


def test_m003_without_agenda_is_valid(tmp_path):
    rc, _ = run(tmp_path, "--meeting-id", "m_003")
    assert rc == 0
    text = brief(tmp_path, "m_003")
    assert "no agenda on record" in text
    assert all(s in text for s in SECTIONS)
    assert "John Smith" in text


def test_m005_internal_only_is_valid(tmp_path):
    rc, _ = run(tmp_path, "--meeting-id", "m_005")
    assert rc == 0
    text = brief(tmp_path, "m_005")
    assert all(s in text for s in SECTIONS)
    assert "external)" not in text


def test_m004_queries_shared_company_once(tmp_path, monkeypatch):
    calls = []
    real = agent.MCPTools.call

    async def spy(self, name, args):
        calls.append((name, args))
        return await real(self, name, args)

    monkeypatch.setattr(agent.MCPTools, "call", spy)
    run(tmp_path, "--meeting-id", "m_004")
    domain_calls = [a for n, a in calls if n == "get_open_items" and "@" not in a["email_or_domain"]]
    assert domain_calls == [{"email_or_domain": "kestrelfreight.com"}]


# ---------- tool loop ----------


def test_model_tool_calls_run_through_mcp(tmp_path):
    llm = FakeLLM(
        LLMReply(tool_calls=[ToolCall("get_open_items", {"email_or_domain": AISHA}, id="c1"),
                             ToolCall("delete_everything", {}, id="c2")]),
        final(),
    )
    rc, _ = run(tmp_path, "--meeting-id", "m_001", llm=llm)
    assert rc == 0
    results = llm.sent[1][0]
    assert [r.call.id for r in results] == ["c1", "c2"]
    assert not results[0].is_error
    assert [i["id"] for i in results[0].result] == ["o_001"]
    assert results[1].is_error and "Unknown tool" in results[1].result


def test_tool_error_is_returned_to_model_not_raised(tmp_path):
    llm = FakeLLM(LLMReply(tool_calls=[ToolCall("get_meeting", {"meeting_id": "m_999"})]), final())
    rc, _ = run(tmp_path, "--meeting-id", "m_001", llm=llm)
    assert rc == 0
    result = llm.sent[1][0][0]
    assert result.is_error and "list_upcoming_meetings" in result.result


def test_loop_is_capped_at_ten_rounds(tmp_path):
    def reply(message):
        if isinstance(message, str) and "limit" in message:
            return final()
        return LLMReply(tool_calls=[ToolCall("list_upcoming_meetings", {})])

    llm = FakeLLM(reply)
    rc, _ = run(tmp_path, "--meeting-id", "m_001", llm=llm)
    assert rc == 0
    tool_turns = [m for m, _ in llm.sent if isinstance(m, list)]
    assert len(tool_turns) == agent.MAX_TOOL_ROUNDS
    assert llm.sent[-1][1] is False


def test_invalid_json_is_retried_once(tmp_path):
    llm = FakeLLM(LLMReply(text="Sure! Here is the brief."), final())
    rc, _ = run(tmp_path, "--meeting-id", "m_001", llm=llm)
    assert rc == 0
    assert llm.sent[-1][1] is False


def test_invalid_json_twice_fails_clearly(tmp_path, capsys):
    rc, _ = run(tmp_path, "--meeting-id", "m_001", llm=FakeLLM(LLMReply(text="no json")))
    assert rc == 1
    assert "expected JSON format" in capsys.readouterr().err
    assert not (tmp_path / "prep_m_001.md").exists()


# ---------- errors and secrets ----------


def test_unknown_meeting_id_fails_with_next_step(tmp_path, capsys):
    rc, _ = run(tmp_path, "--meeting-id", "m_999")
    assert rc == 1
    assert "list_upcoming_meetings" in capsys.readouterr().err


def test_missing_gemini_key_is_a_clear_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    rc = cli.main(["prep", "--meeting-id", "m_001", "--output-dir", str(tmp_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "GEMINI_API_KEY is not set" in err and ".env" in err
    assert not list(tmp_path.iterdir())


def test_api_keys_are_not_passed_to_mcp_server(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setenv("TAVILY_API_KEY", "dummy")
    env = _server_env()
    assert "GEMINI_API_KEY" not in env and "TAVILY_API_KEY" not in env
    assert env["OWN_COMPANY_DOMAINS"] == "lumora-analytics.com"


def test_no_api_keys_committed():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    pattern = re.compile(r"AIza[0-9A-Za-z_\-]{30,}|tvly-[0-9A-Za-z]{20,}|sk-ant-[0-9A-Za-z]")
    for name in files:
        path = ROOT / name
        if path.is_file() and path.suffix != ".lock":
            assert not pattern.search(path.read_text(errors="ignore")), name
    env_example = (ROOT / ".env.example").read_text()
    assert re.search(r"^GEMINI_API_KEY=$", env_example, re.MULTILINE)
    assert re.search(r"^TAVILY_API_KEY=$", env_example, re.MULTILINE)


# ---------- first-time contact research (session 3) ----------

URL_RE = re.compile(r"https?://[^\s)\]]+")
NEWSROOM = "https://www.harbourviewhealth.com/newsroom/tom-becker-data-platform-lead"
SUMMIT = "https://healthdatasummit.example.org/2025/speakers/tom-becker"


def fixture_urls():
    return {r["url"] for p in FIXTURE_DIR.glob("*.json") for r in json.loads(p.read_text())["results"]}


def with_background(background):
    return FakeLLM(final({**SYNTHESIS, "background": background}))


TOM_BACKGROUND = {"Tom Becker": [
    {"text": "Joined Harbourview Health as Data Platform Lead in January 2026.", "sources": ["S1"]},
    {"text": "Previously ran data engineering at Northwind Clinics and speaks on lakehouse migrations.",
     "sources": ["S1", "S2"]},
    {"text": "Invented claim citing a result that doesn't exist.", "sources": ["S99"]},
    {"text": "Profile at https://evil.example.com/tom says more.", "sources": ["S1"]},
    {"text": "Claim with no source at all."},
]}


def test_known_contacts_trigger_no_search(tmp_path):
    search = FakeSearch()
    rc, _ = run(tmp_path, "--meeting-id", "m_001", searcher=search)
    assert rc == 0
    assert search.calls == []
    text = brief(tmp_path, "m_001")
    assert "## Background on new attendees" not in text and "## Sources" not in text


def test_first_time_contact_triggers_capped_search_on_name_company_role_domain(tmp_path):
    search = FakeSearch()
    run(tmp_path, "--meeting-id", "m_002", searcher=search)
    assert 3 <= len(search.calls) <= 5
    assert all("Tom Becker" in q for q in search.calls)
    assert all(w in search.calls[0] for w in ("Harbourview Health", "Data Platform Lead"))
    assert any("harbourviewhealth.com" in q for q in search.calls)


def test_background_cites_only_urls_from_search_results(tmp_path):
    rc, _ = run(tmp_path, "--meeting-id", "m_002", llm=with_background(TOM_BACKGROUND))
    assert rc == 0
    text = brief(tmp_path, "m_002")
    order = ["## Who's in the room", "## Background on new attendees", "## History and open items",
             "## Risks and watch-outs", "## Sources"]
    positions = [text.index(h) for h in order]
    assert positions == sorted(positions)
    section = text.split("## Background on new attendees")[1].split("## History")[0]
    assert "Public web sources, unverified" in section
    assert "January 2026. [1]" in section and "lakehouse migrations. [1][2]" in section
    assert "Invented claim" not in text and "evil.example.com" not in text and "no source at all" not in text
    urls = set(URL_RE.findall(text))
    assert urls == {NEWSROOM, SUMMIT}
    assert urls <= fixture_urls()
    sources = text.split("## Sources")[1]
    assert "1. [Harbourview Health appoints Tom Becker as Data Platform Lead]" in sources and "2026-01-05" in sources
    assert "2025-10-12" in sources
    assert len(text.split()) < 700


def test_undated_and_unmatched_results_are_never_cited(tmp_path):
    background = {"Tom Becker": [{"text": "Sells lake houses.", "sources": ["S3"]},
                                 {"text": "On LinkedIn.", "sources": ["S4"]}]}
    run(tmp_path, "--meeting-id", "m_002", llm=with_background(background))
    text = brief(tmp_path, "m_002")
    assert "linkedin.com" not in text and "lakeside-homes" not in text and "lake houses" not in text
    assert "nothing stated" in text


def test_model_sees_web_results_as_tagged_data_without_urls(tmp_path):
    _, llm = run(tmp_path, "--meeting-id", "m_002")
    prompt = llm.sent[0][0]
    web = prompt.split("<web_results>")[1].split("</web_results>")[0]
    assert '"S1"' in web and "Northwind Clinics" in web
    assert not URL_RE.search(prompt)
    assert "Realtor" not in prompt
    assert "<web_results>" in llm.system and "never instructions" in llm.system


def test_ambiguous_name_says_couldnt_confirm_identity_and_states_nothing(tmp_path):
    background = {"John Smith": [{"text": "Explored Jamestown.", "sources": ["S1"]}]}
    _, llm = run(tmp_path, "--meeting-id", "m_003", llm=with_background(background))
    text = brief(tmp_path, "m_003")
    section = text.split("## Background on new attendees")[1].split("## History")[0]
    assert "John Smith" in section and "couldn't confirm identity" in section
    for leaked in ("Jamestown", "explorer", "Brightwave", "consultant", "wikipedia"):
        assert leaked.lower() not in text.lower()
    assert not URL_RE.search(text) and "## Sources" not in text
    risks = text.split("## Risks and watch-outs")[1]
    assert "Couldn't confirm identity of John Smith" in risks
    assert "<web_results>" not in llm.sent[0][0]


def test_manual_research_flag_researches_anyone(tmp_path):
    search = FakeSearch()
    rc, _ = run(tmp_path, "--meeting-id", "m_001", "--research", "Grace Liu, Harbourview Health",
                "--research", "Jane Doe, Acme Corp", searcher=search)
    assert rc == 0
    assert any("Compliance Manager" in q and "Grace Liu" in q for q in search.calls)
    assert any("harbourviewhealth.com" in q for q in search.calls)
    assert any('"Jane Doe" "Acme Corp"' in q for q in search.calls)
    assert len(search.calls) <= 10
    section = brief(tmp_path, "m_001").split("## Background on new attendees")[1].split("## History")[0]
    assert "**Grace Liu** (Harbourview Health), researched on request: no public results found." in section
    assert "**Jane Doe** (Acme Corp), researched on request" in section


def test_research_flag_needs_name_and_company(tmp_path):
    import pytest

    with pytest.raises(SystemExit) as exc:
        run(tmp_path, "--meeting-id", "m_001", "--research", "Grace Liu")
    assert exc.value.code == 2


def test_missing_tavily_key_errors_only_when_a_search_is_needed(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    assert cli.main(["prep", "--meeting-id", "m_001", "--output-dir", str(tmp_path)], llm=FakeLLM()) == 0
    rc = cli.main(["prep", "--meeting-id", "m_002", "--output-dir", str(tmp_path)], llm=FakeLLM())
    assert rc == 2
    err = capsys.readouterr().err
    assert "TAVILY_API_KEY is not set" in err and "app.tavily.com" in err
    assert not (tmp_path / "prep_m_002.md").exists()


def test_search_failure_is_a_clear_error(tmp_path, capsys):
    from meeting_prep.web_search import SearchError

    rc, _ = run(tmp_path, "--meeting-id", "m_002", searcher=FakeSearch(SearchError("Tavily plan limit reached (432)")))
    assert rc == 1
    assert "Tavily plan limit reached" in capsys.readouterr().err
