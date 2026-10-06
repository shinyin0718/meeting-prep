import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session

import mcp_server as srv

ROOT = Path(__file__).resolve().parent.parent
TOOL_NAMES = {
    "list_upcoming_meetings",
    "get_meeting",
    "get_person_profile",
    "get_interaction_history",
    "get_open_items",
}

AISHA = "aisha.rahman@harbourviewhealth.com"
TOM = "tom.becker@harbourviewhealth.com"
JOHN = "john.smith@solvane-energy.com"
PRIYA = "priya.raman@lumora-analytics.com"


# ---------- seed data covers the spec scenarios ----------


def _load(name):
    return json.loads((ROOT / "data" / f"{name}.json").read_text())


def test_seed_counts():
    assert len(_load("meetings")) == 5
    assert len(_load("people")) == 10
    assert len(_load("companies")) == 4
    assert 13 <= len(_load("interactions")) <= 17
    assert 5 <= len(_load("open_items")) <= 7


def test_seed_has_people_without_history_and_one_own_company():
    with_history = {i["person_email"] for i in _load("interactions")}
    without = [p for p in _load("people") if p["email"] not in with_history]
    assert len(without) >= 2
    assert [c["domain"] for c in _load("companies")].count("lumora-analytics.com") == 1


def test_seed_data_has_common_name_first_timer():
    assert srv.get_person_profile(JOHN)["name"] == "John Smith"
    assert srv.get_person_profile(JOHN)["first_meeting"] is True


def test_scenario_m_001_normal():
    m = srv.get_meeting("m_001")
    assert m["agenda"]
    assert all(not srv.get_person_profile(a["email"])["first_meeting"] for a in m["attendees"])
    assert any(srv.get_open_items(a["email"]) for a in m["attendees"] if not a["is_internal"])


def test_scenario_m_002_one_first_time_external():
    m = srv.get_meeting("m_002")
    first = [a["email"] for a in m["attendees"] if srv.get_person_profile(a["email"])["first_meeting"]]
    assert first == [TOM]


def test_scenario_m_003_no_agenda():
    assert srv.get_meeting("m_003")["agenda"] == []


def test_scenario_m_004_two_from_same_external_company():
    external = [a for a in srv.get_meeting("m_004")["attendees"] if not a["is_internal"]]
    assert len(external) == 2
    assert len({a["email"].split("@")[1] for a in external}) == 1


def test_scenario_m_005_internal_only():
    assert all(a["is_internal"] for a in srv.get_meeting("m_005")["attendees"])


# ---------- list_upcoming_meetings ----------


def test_list_upcoming_default_returns_all_five_sorted():
    meetings = srv.list_upcoming_meetings()
    assert [m["id"] for m in meetings] == ["m_001", "m_002", "m_003", "m_004", "m_005"]
    assert set(meetings[0]) == {"id", "title", "start"}
    assert meetings[0]["start"].startswith("2026-01-16T10:00")


def test_list_upcoming_respects_days_ahead():
    assert [m["id"] for m in srv.list_upcoming_meetings(days_ahead=2)] == ["m_001", "m_002"]


def test_list_upcoming_empty_window_returns_empty_list():
    assert srv.list_upcoming_meetings(days_ahead=0) == []


def test_list_upcoming_empty_data_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETING_PREP_DATA_DIR", str(tmp_path))
    assert srv.list_upcoming_meetings() == []


def test_list_upcoming_negative_days_is_actionable_error():
    with pytest.raises(ToolError, match="days_ahead must be 0 or greater"):
        srv.list_upcoming_meetings(days_ahead=-1)


def test_dates_follow_today(monkeypatch):
    monkeypatch.setenv("MEETING_PREP_TODAY", "2030-06-01")
    assert srv.list_upcoming_meetings()[0]["start"].startswith("2030-06-02T10:00")


# ---------- get_meeting ----------


def test_get_meeting_returns_details():
    m = srv.get_meeting("m_001")
    assert m["title"] == "Harbourview Health: Q4 renewal review"
    assert m["start"].startswith("2026-01-16T10:00")
    assert m["end"].startswith("2026-01-16T10:45")
    assert [a["email"] for a in m["attendees"]] == [
        PRIYA,
        AISHA,
        "grace.liu@harbourviewhealth.com",
    ]
    assert m["attendees"][1] == {
        "email": AISHA,
        "name": "Aisha Rahman",
        "company": "Harbourview Health",
        "role": "Chief Information Officer",
        "is_internal": False,
    }
    assert m["attendees"][0]["is_internal"] is True
    assert len(m["agenda"]) == 4


def test_get_meeting_is_case_insensitive():
    assert srv.get_meeting(" M_002 ")["id"] == "m_002"


def test_get_meeting_not_found():
    with pytest.raises(ToolError, match="No meeting found with id 'm_999'.*list_upcoming_meetings"):
        srv.get_meeting("m_999")


# ---------- get_person_profile ----------


def test_profile_known_external_contact():
    p = srv.get_person_profile(AISHA)
    assert p["name"] == "Aisha Rahman"
    assert p["company"] == "Harbourview Health"
    assert p["role"] == "Chief Information Officer"
    assert p["first_meeting"] is False
    assert p["is_internal"] is False
    assert p["last_interaction_date"] == "2026-01-09"


def test_profile_first_time_contact():
    p = srv.get_person_profile(TOM)
    assert p["first_meeting"] is True
    assert p["last_interaction_date"] is None


def test_profile_internal_colleague_is_never_first_meeting(tmp_path, monkeypatch):
    for name in ("people", "companies"):
        (tmp_path / f"{name}.json").write_text((ROOT / "data" / f"{name}.json").read_text())
    monkeypatch.setenv("MEETING_PREP_DATA_DIR", str(tmp_path))  # no interactions at all
    p = srv.get_person_profile(PRIYA)
    assert p["is_internal"] is True
    assert p["first_meeting"] is False
    assert srv.get_person_profile(AISHA)["first_meeting"] is True


def test_profile_lookup_is_case_insensitive():
    assert srv.get_person_profile("  Aisha.Rahman@HarbourviewHealth.com ")["name"] == "Aisha Rahman"


def test_profile_not_found():
    with pytest.raises(ToolError, match="No person found for email 'nobody@example.com'; check the address"):
        srv.get_person_profile("nobody@example.com")


def test_profile_empty_email():
    with pytest.raises(ToolError, match="email is required"):
        srv.get_person_profile("  ")


# ---------- get_interaction_history ----------


def test_history_newest_first():
    h = srv.get_interaction_history(AISHA)
    assert [i["id"] for i in h] == ["i_004", "i_003", "i_002", "i_001"]
    assert h[0] == {
        "id": "i_004",
        "date": "2026-01-09",
        "type": "call",
        "summary": h[0]["summary"],
    }
    assert [i["date"] for i in h] == sorted((i["date"] for i in h), reverse=True)


def test_history_limit():
    assert [i["id"] for i in srv.get_interaction_history(AISHA, limit=2)] == ["i_004", "i_003"]


def test_history_empty_returns_empty_list():
    assert srv.get_interaction_history(TOM) == []
    assert srv.get_interaction_history(JOHN) == []


def test_history_not_found():
    with pytest.raises(ToolError, match="No person found for email"):
        srv.get_interaction_history("ghost@kestrelfreight.com")


def test_history_invalid_limit():
    with pytest.raises(ToolError, match="limit must be at least 1"):
        srv.get_interaction_history(AISHA, limit=0)


# ---------- get_open_items ----------


def test_open_items_by_email():
    items = srv.get_open_items(AISHA)
    assert [i["id"] for i in items] == ["o_001"]
    assert items[0]["owner"] == "Daniel Okafor"
    assert items[0]["due_date"] == "2026-01-17"
    assert items[0]["overdue"] is False


def test_open_items_by_domain_includes_company_and_people_items_sorted():
    items = srv.get_open_items("harbourviewhealth.com")
    assert [i["id"] for i in items] == ["o_003", "o_002", "o_001"]
    assert items[0]["overdue"] is True


def test_open_items_empty_returns_empty_list():
    assert srv.get_open_items(TOM) == []
    assert srv.get_open_items("lumora-analytics.com") == []


def test_open_items_unknown_email():
    with pytest.raises(ToolError, match="No person found for email"):
        srv.get_open_items("nobody@example.com")


def test_open_items_unknown_domain():
    with pytest.raises(ToolError, match="No company found for domain 'example.com'; check the domain"):
        srv.get_open_items("example.com")


def test_open_items_empty_input():
    with pytest.raises(ToolError, match="email_or_domain is required"):
        srv.get_open_items("")


# ---------- MCP protocol level ----------


@pytest.mark.anyio
async def test_lists_all_five_tools_with_descriptions():
    tools = await srv.mcp.list_tools()
    assert {t.name for t in tools} == TOOL_NAMES
    for t in tools:
        assert t.description and len(t.description) > 40, t.name
        for prop, schema in t.inputSchema["properties"].items():
            assert schema.get("description"), f"{t.name}.{prop}"


@pytest.mark.anyio
async def test_protocol_error_is_actionable_tool_error():
    async with create_connected_server_and_client_session(srv.mcp._mcp_server) as client:
        result = await client.call_tool("get_person_profile", {"email": "nobody@example.com"})
    assert result.isError
    assert "No person found for email 'nobody@example.com'; check the address" in result.content[0].text


@pytest.mark.anyio
async def test_protocol_empty_list_is_not_an_error():
    async with create_connected_server_and_client_session(srv.mcp._mcp_server) as client:
        result = await client.call_tool("get_interaction_history", {"email": TOM})
    assert not result.isError
    assert result.structuredContent == {"result": []}


@pytest.mark.anyio
async def test_stdio_transport_lists_tools():
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "mcp_server.py")],
        env={"OWN_COMPANY_DOMAINS": "lumora-analytics.com", "MEETING_PREP_TODAY": "2026-01-15"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("get_meeting", {"meeting_id": "m_005"})
    assert {t.name for t in tools.tools} == TOOL_NAMES
    assert not result.isError
    assert all(a["is_internal"] for a in result.structuredContent["attendees"])
