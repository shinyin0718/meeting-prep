import json
import re
import subprocess
from pathlib import Path

import pytest

from meeting_prep import agent, cli
from meeting_prep.fakes import FixtureSearch, ScriptedLLM
from meeting_prep.llm import LLMReply, ToolCall
from meeting_prep.mcp_client import _server_env

ROOT = Path(__file__).resolve().parent.parent
SECTIONS = [
    "## Meeting at a glance",
    "## Who's in the room",
    "## History and open items",
    "## Likely asks",
    "## Suggested questions and talking points",
    "## Risks and watch-outs",
]
OMITTED = ["## Background on new attendees", "## From your materials"]
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


class FakeLLM(ScriptedLLM):
    def __init__(self, *script):
        super().__init__(*(script or [LLMReply(text=json.dumps(SYNTHESIS))]))


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "search"
NEWS_DIR = ROOT / "tests" / "fixtures" / "news"


FakeSearch = FixtureSearch


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
    assert "## Company snapshot" not in text


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


def test_verbose_model_output_is_clipped_and_trimmed_to_one_page(tmp_path):
    long = " ".join(["word"] * 80)
    item = {"text": long, "based_on": long}
    verbose = {**SYNTHESIS, "purpose": long, "desired_outcome": long, "relationships": {AISHA: long},
               "likely_asks": [{"party": "Us", "ask": long, "based_on": long}] * 6,
               "questions": [item] * 5, "risks": [item] * 6}
    rc, _ = run(tmp_path, "--meeting-id", "m_001", llm=FakeLLM(final(verbose)))
    assert rc == 0
    text = brief(tmp_path, "m_001")
    assert len(text.split()) < 700
    assert all(h in text for h in SECTIONS)
    assert max(len(line.split()) for line in text.splitlines() if "word" in line) <= 45
    assert "Return BAA redlines on data retention clauses" in text  # records are never trimmed
    questions = text.split("## Suggested questions and talking points")[1].split("## ")[0]
    assert sum(line[:2] in {f"{n}." for n in range(1, 6)} for line in questions.splitlines()) >= 3


def test_short_model_output_is_not_trimmed(tmp_path):
    rc, _ = run(tmp_path, "--meeting-id", "m_001")
    assert rc == 0
    assert "…" not in brief(tmp_path, "m_001")


def test_llm_client_is_closed_inside_the_event_loop(tmp_path):
    class ClosingLLM(FakeLLM):
        closed = False

        async def aclose(self):
            import asyncio
            asyncio.get_running_loop()
            self.closed = True

    llm = ClosingLLM()
    rc, _ = run(tmp_path, "--meeting-id", "m_005", llm=llm)
    assert rc == 0 and llm.closed


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
    return {r["url"] for d in (FIXTURE_DIR, NEWS_DIR) for p in d.glob("*.json")
            for r in json.loads(p.read_text())["results"]}


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
    assert search.person_calls == []
    text = brief(tmp_path, "m_001")
    assert "## Background on new attendees" not in text
    assert "tom-becker" not in text.split("## Sources")[1]  # sources are company news only


def test_first_time_contact_triggers_capped_search_on_name_company_role_domain(tmp_path):
    search = FakeSearch()
    run(tmp_path, "--meeting-id", "m_002", searcher=search)
    assert 3 <= len(search.person_calls) <= 5
    assert all("Tom Becker" in q for q in search.person_calls)
    assert all(w in search.person_calls[0] for w in ("Harbourview Health", "Data Platform Lead"))
    assert any("harbourviewhealth.com" in q for q in search.person_calls)


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
    # [1]-[3] are the company snapshot, which comes first.
    assert "January 2026. [4]" in section and "lakehouse migrations. [4][5]" in section
    assert "Invented claim" not in text and "evil.example.com" not in text and "no source at all" not in text
    urls = set(URL_RE.findall(text))
    assert {u for u in urls if "tom-becker" in u} == {NEWSROOM, SUMMIT}
    assert urls <= fixture_urls()
    sources = text.split("## Sources")[1]
    assert "4. [Harbourview Health appoints Tom Becker as Data Platform Lead]" in sources and "2026-01-05" in sources
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
    assert any("Compliance Manager" in q and "Grace Liu" in q for q in search.person_calls)
    assert any("harbourviewhealth.com" in q for q in search.person_calls)
    assert any('"Jane Doe" "Acme Corp"' in q for q in search.person_calls)
    assert len(search.person_calls) <= 10
    section = brief(tmp_path, "m_001").split("## Background on new attendees")[1].split("## History")[0]
    assert "**Grace Liu** (Harbourview Health), researched on request: no public results found." in section
    assert "**Jane Doe** (Acme Corp), researched on request" in section


def test_research_flag_needs_name_and_company(tmp_path):
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, "--meeting-id", "m_001", "--research", "Grace Liu")
    assert exc.value.code == 2


def test_missing_tavily_key_errors_only_when_a_search_is_needed(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    assert cli.main(["prep", "--meeting-id", "m_005", "--output-dir", str(tmp_path)], llm=FakeLLM()) == 0
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


# ---------- company developments (session 4) ----------

HV_CDO = "https://www.harbourviewhealth.com/newsroom/chief-digital-officer"
HV_BAYSIDE = "https://www.fiercehealthcare.com/providers/harbourview-health-completes-bayside-acquisition"
HV_HIE = "https://www.businesswire.com/news/home/20251110005123/en/harbourview-health-hie-partnership"
DATED_LINE = re.compile(r"^- \d{4}-\d{2}-\d{2} · .+ \[\d+\]$")
HV_NEWS = {
    "N3": {"summary": "Signed a data-exchange partnership with the state HIE.",
           "why": "Touches the integration requirements on the agenda.", "relevance": 3},
    "N1": {"summary": "Named a first Chief Digital Officer.", "why": "A new sign-off on data spend.", "relevance": 2},
    "N2": {"summary": "Closed its Bayside Clinics acquisition.", "why": "More sites could mean more seats.",
           "relevance": 2},
}


def with_news(news):
    return FakeLLM(final({**SYNTHESIS, "news": news}))


def snapshot(text):
    return text.split("## Company snapshot")[1].split("## Who's in the room")[0]


def news_lines(text):
    return [line for line in snapshot(text).splitlines() if line.startswith("- ")]


def dates(lines):
    return [line.split(" · ")[0][2:] for line in lines]


def test_company_with_three_recent_items_shows_them_ranked_with_date_and_link(tmp_path):
    rc, _ = run(tmp_path, "--meeting-id", "m_001", llm=with_news(HV_NEWS))
    assert rc == 0
    text = brief(tmp_path, "m_001")
    order = [text.index(h) for h in ("## Meeting at a glance", "## Company snapshot", "## Who's in the room")]
    assert order == sorted(order)
    section = snapshot(text)
    assert "**Harbourview Health** (harbourviewhealth.com)" in section
    assert "Public web sources, last 90 days" in section
    lines = news_lines(text)
    assert len(lines) == 3 and all(DATED_LINE.match(line) for line in lines)
    assert dates(lines) == ["2025-11-10", "2026-01-08", "2025-12-02"]  # relevance 3, then 2s newest first
    assert "state HIE" in lines[0] and "_Why it matters:_ Touches the integration" in lines[0]
    assert [line[-3:] for line in lines] == ["[1]", "[2]", "[3]"]
    sources = text.split("## Sources")[1]
    assert f"]({HV_HIE}), 2025-11-10" in sources and sources.index(HV_HIE) < sources.index(HV_CDO)
    assert "[Harbourview Health completes acquisition of Bayside Clinics]" in sources
    urls = set(URL_RE.findall(text))
    assert urls == {HV_CDO, HV_BAYSIDE, HV_HIE} and urls <= fixture_urls()
    for dropped in ("FY2024", "Careers at", "Harbourview Capital"):
        assert dropped not in text
    assert "unconfirmed" not in section
    assert len(text.split()) < 700


def test_without_model_notes_items_are_newest_first_with_title_as_summary(tmp_path):
    run(tmp_path, "--meeting-id", "m_001")
    lines = news_lines(brief(tmp_path, "m_001"))
    assert dates(lines) == ["2026-01-08", "2025-12-02", "2025-11-10"]
    assert "Harbourview Health names Dr. Mei Chen Chief Digital Officer" in lines[0]


def test_model_can_drop_items_but_cannot_add_items_or_urls(tmp_path):
    news = {"N1": {"relevance": 0},
            "N2": {"summary": "See https://evil.example.com/x", "why": "www.evil.example.com", "relevance": 2},
            "N9": {"summary": "Invented item.", "relevance": 3}}
    run(tmp_path, "--meeting-id", "m_001", llm=with_news(news))
    text = brief(tmp_path, "m_001")
    lines = news_lines(text)
    assert dates(lines) == ["2025-12-02", "2025-11-10"]
    assert "Harbourview Health completes acquisition of Bayside Clinics [1]" == lines[0].split(" · ")[1]
    assert "evil.example.com" not in text and "Invented item" not in text and HV_CDO not in text


def test_company_with_nothing_in_window_says_no_notable_developments(tmp_path):
    run(tmp_path, "--meeting-id", "m_003")
    text = brief(tmp_path, "m_003")
    section = snapshot(text)
    assert "**Solvane Energy** (solvane-energy.com)" in section
    assert news_lines(text) == ["- No notable developments found in the last 90 days."]
    assert "solar" not in text.lower()


def test_news_days_flag_narrows_the_window_and_never_falls_back(tmp_path):
    search = FakeSearch()
    run(tmp_path, "--meeting-id", "m_001", "--news-days", "30", searcher=search)
    assert {r["start_date"] for r in search.news_calls} == {"2025-12-16"}
    text = brief(tmp_path, "m_001")
    assert dates(news_lines(text)) == ["2026-01-08"]
    assert "last 30 days" in snapshot(text)
    run(tmp_path, "--meeting-id", "m_003", "--news-days", "30")
    assert "No notable developments found in the last 30 days." in brief(tmp_path, "m_003")


@pytest.mark.parametrize("value", ["0", "-5", "soon"])
def test_news_days_must_be_a_positive_whole_number(tmp_path, value):
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, "--meeting-id", "m_001", "--news-days", value)
    assert exc.value.code == 2


def test_two_attendees_from_one_company_trigger_one_search_pass(tmp_path):
    for meeting_id, company, domain in (("m_001", "Harbourview Health", "harbourviewhealth.com"),
                                        ("m_004", "Kestrel Freight", "kestrelfreight.com")):
        search = FakeSearch()
        run(tmp_path, "--meeting-id", meeting_id, searcher=search)
        assert 3 <= len(search.news_calls) <= 5
        assert all(company in r["query"] for r in search.news_calls)
        assert any(domain in r["query"] for r in search.news_calls)


def test_own_company_triggers_no_search(tmp_path):
    for meeting_id in ("m_001", "m_002", "m_003", "m_004", "m_005"):
        search = FakeSearch()
        run(tmp_path, "--meeting-id", meeting_id, searcher=search)
        assert not any("lumora" in r["query"].lower() for r in search.requests)
    assert search.requests == []  # m_005 is internal-only
    assert "## Company snapshot" not in brief(tmp_path, "m_005")


def test_rumor_and_weak_single_source_items_are_labeled_unconfirmed(tmp_path):
    run(tmp_path, "--meeting-id", "m_004", llm=with_news({"N2": {"unconfirmed": False, "relevance": 3}}))
    text = brief(tmp_path, "m_004")
    lines = dict(zip(dates(news_lines(text)), news_lines(text)))
    assert "_(unconfirmed)_" in lines["2025-12-29"]  # rumor framing; the model can't clear the label
    assert "_(unconfirmed)_" in lines["2025-12-10"]  # single weak source
    assert "_(unconfirmed)_" not in lines["2026-01-06"]  # company's own newsroom
    risks = text.split("## Risks and watch-outs")[1]
    assert risks.count("Unconfirmed report on Kestrel Freight") == 2
    assert "regional carrier. Don't present it as fact." in risks


def test_model_sees_company_news_as_tagged_data_without_urls(tmp_path):
    _, llm = run(tmp_path, "--meeting-id", "m_001")
    prompt = llm.sent[0][0]
    web = json.loads(prompt.split("<web_results>")[1].split("</web_results>")[0])
    (company,) = web["companies"]
    assert company["company"] == "Harbourview Health"
    assert [i["id"] for i in company["items"]] == ["N1", "N2", "N3"]
    assert {i["source_type"] for i in company["items"]} == {"primary", "established"}
    assert "[link removed]" in company["items"][0]["snippet"]
    assert not URL_RE.search(prompt) and "www." not in prompt
    assert "FY2024" not in prompt and "Harbourview Capital" not in prompt
    assert '"news"' in llm.system and "rumor" in llm.system
