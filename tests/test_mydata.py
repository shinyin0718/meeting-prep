import json
import os
from pathlib import Path

import pytest

import mcp_server as server
from meeting_prep.mydata import DataError, add_meeting, delete_meeting, is_mine

ROOT = Path(__file__).resolve().parent.parent


def form(**overrides):
    base = {
        "title": "Acme: kickoff", "date": "2026-01-20", "time": "15:00", "duration_minutes": 45,
        "agenda": ["Goals", "  ", "Timeline"],
        "attendees": [
            {"name": "Priya Raman", "email": "priya.raman@lumora-analytics.com"},
            {"name": "Jo Bloggs", "email": "Jo@Acme.example", "company": "Acme Corp", "role": "COO"},
        ],
        "history": [{"email": "jo@acme.example", "date": "2026-01-10", "type": "call", "summary": "Intro call."}],
        "open_items": [{"email": "jo@acme.example", "description": "Send proposal", "owner": "Priya Raman",
                        "due_date": "2026-01-25"}],
    }
    return {**base, **overrides}


def mine(name):
    path = Path(os.environ["MEETING_PREP_USER_DATA_DIR"]) / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else []


def test_added_meeting_is_served_by_the_mcp_tools_with_real_dates():
    meeting = add_meeting(form())
    assert meeting["id"] == "m_006" and is_mine("m_006") and not is_mine("m_001")
    got = server.get_meeting("m_006")
    assert got["start"].startswith("2026-01-20T15:00") and got["duration_minutes"] == 45
    assert got["agenda"] == ["Goals", "Timeline"]
    assert [a["email"] for a in got["attendees"]] == ["priya.raman@lumora-analytics.com", "jo@acme.example"]
    assert got["attendees"][1]["company"] == "Acme Corp" and got["attendees"][0]["is_internal"]
    assert "m_006" in [m["id"] for m in server.list_upcoming_meetings(days_ahead=30)]
    profile = server.get_person_profile("jo@acme.example")
    assert profile["role"] == "COO" and profile["first_meeting"] is False
    assert server.get_interaction_history("jo@acme.example")[0]["date"] == "2026-01-10"
    item = server.get_open_items("acme.example")[0]
    assert (item["description"], item["due_date"], item["overdue"]) == ("Send proposal", "2026-01-25", False)


def test_known_people_and_companies_are_reused_and_sample_data_is_untouched():
    before = (ROOT / "data" / "meetings.json").read_text()
    add_meeting(form(attendees=[{"name": "Someone Else", "email": "aisha.rahman@harbourviewhealth.com"}],
                     history=[], open_items=[]))
    assert mine("people") == [] and mine("companies") == []
    assert server.get_meeting("m_006")["attendees"][0]["name"] == "Aisha Rahman"
    assert (ROOT / "data" / "meetings.json").read_text() == before


def test_ids_keep_counting_up():
    add_meeting(form())
    second = add_meeting(form(attendees=[{"email": "jo@acme.example"}]))
    assert second["id"] == "m_007"
    assert [i["id"] for i in mine("interactions")] == ["i_017", "i_018"]
    assert len(mine("people")) == 1


@pytest.mark.parametrize("overrides, message", [
    ({"title": " "}, "Meeting title is required"),
    ({"date": "20/01/2026"}, "Date must be a date like"),
    ({"time": "3pm"}, "Time must be like 14:30"),
    ({"date": "2026-01-14"}, "already passed"),
    ({"duration_minutes": 1000}, "Length must be between"),
    ({"attendees": []}, "Add at least one attendee"),
    ({"attendees": [{"name": "X", "email": "not-an-email"}]}, "doesn't look right"),
    ({"attendees": [{"name": "X", "email": "x@new.example"}]}, "Company for x@new.example is required"),
    ({"attendees": [{"email": "x@acme.example", "company": "Acme"}]}, "Attendee 1's name is required"),
    ({"attendees": [{"email": "priya.raman@lumora-analytics.com"}] * 2}, "listed twice"),
    ({"attendees": [{"name": "Jo", "company": "Acme"}] * 2}, "Jo is listed twice"),
    ({"attendees": [{"name": "Jo"}]}, "Add Jo's email or company"),
    ({"attendees": [{"company": "Acme"}]}, "Attendee 1 needs a name or an email"),
    ({"history": [{"email": "nobody@x.com", "date": "2026-01-10", "type": "call", "summary": "x"}]}, "who it was with"),
    ({"history": [{"email": "jo@acme.example", "date": "2026-02-10", "type": "call", "summary": "x"}]}, "in the future"),
    ({"history": [{"email": "jo@acme.example", "date": "2026-01-10", "type": "fax", "summary": "x"}]}, "type must be"),
    ({"open_items": [{"email": "", "description": "x", "owner": "me", "due_date": "2026-01-20"}]}, "who it's for"),
])
def test_bad_forms_fail_clearly_and_save_nothing(overrides, message):
    with pytest.raises(DataError, match=message):
        add_meeting(form(**overrides))
    assert not Path(os.environ["MEETING_PREP_USER_DATA_DIR"]).exists()


def test_delete_only_your_own_meetings():
    add_meeting(form())
    delete_meeting("m_006")
    assert mine("meetings") == [] and len(mine("people")) == 1
    with pytest.raises(DataError, match="only meetings you added"):
        delete_meeting("m_001")


def test_email_is_optional_and_the_company_name_is_used_instead():
    meeting = add_meeting(form(
        attendees=[{"name": "Priya Raman", "company": "lumora analytics"},
                   {"name": "Mei Lin", "company": "Lumora Analytics"},
                   {"name": "Jordan Lee", "company": "Acme Corp", "role": "VP"}],
        history=[{"attendee": 2, "date": "2026-01-10", "type": "call", "summary": "Intro."}],
        open_items=[{"attendee": 2, "description": "Send deck", "owner": "me", "due_date": "2026-01-25"}]))
    assert meeting["attendee_emails"] == ["priya.raman@lumora-analytics.com", "mei.lin@lumora-analytics.com",
                                          "jordan.lee@acme-corp.invalid"]
    got = server.get_meeting(meeting["id"])["attendees"]
    assert [a["is_internal"] for a in got] == [True, True, False]
    assert got[2]["company"] == "Acme Corp"
    assert {p["name"]: p.get("email_unknown") for p in mine("people")} == {"Mei Lin": True, "Jordan Lee": True}
    assert server.get_interaction_history("jordan.lee@acme-corp.invalid")[0]["summary"] == "Intro."
    assert server.get_open_items("acme-corp.invalid")[0]["description"] == "Send deck"


def test_same_person_without_email_is_recognised_next_time():
    add_meeting(form(attendees=[{"name": "Jordan Lee", "company": "Acme Corp"}], history=[], open_items=[]))
    second = add_meeting(form(attendees=[{"name": "jordan lee", "company": "acme corp"}], history=[], open_items=[]))
    assert second["attendee_emails"] == ["jordan.lee@acme-corp.invalid"]
    assert len(mine("people")) == 1 and len(mine("companies")) == 1


def test_made_up_domains_stay_out_of_searches_and_the_brief():
    from meeting_prep import brief, news, research

    assert all(".invalid" not in q for q, _ in news.queries(news.Company("Acme Corp", "acme-corp.invalid")))
    target = research.Target("Jordan Lee", "Acme Corp", domain="acme-corp.invalid")
    assert all(".invalid" not in q for q in research.queries(target))
    snapshot = "\n".join(brief._snapshot([{"company": "Acme Corp", "domain": "acme-corp.invalid", "days": 90}],
                                          {"acme-corp.invalid": []}, {}))
    assert "**Acme Corp**" in snapshot and ".invalid" not in snapshot
