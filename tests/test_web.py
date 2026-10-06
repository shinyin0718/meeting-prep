import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meeting_prep import web
from meeting_prep.fakes import FixtureSearch, ScriptedLLM, json_reply

SYNTHESIS = {
    "purpose": "Review the renewal.",
    "desired_outcome": "Agree terms.",
    "questions": [{"text": "What's the budget?", "based_on": "2026-07-23 call"},
                  {"text": "Who signs?", "based_on": "attendee list"},
                  {"text": "When?", "based_on": "agenda item 4"}],
    "materials": {"F1": ["Pricing rises 5% in year two."]},
}
NOTES = ("files", ("notes.txt", b"Renewal deck: pricing rises 5% in year two.", "text/plain"))


def make_client(tmp_path, synthesis=SYNTHESIS, llm_factory=None):
    llm_factory = llm_factory or (lambda: ScriptedLLM(json_reply(synthesis)))
    return TestClient(web.create_app(llm_factory=llm_factory, searcher_factory=FixtureSearch,
                                     output_dir=tmp_path / "out"))


def attachments() -> Path:
    return Path(os.environ["MEETING_PREP_ATTACHMENTS_DIR"])


def test_index_page_is_served(tmp_path):
    res = make_client(tmp_path).get("/")
    assert res.status_code == 200
    assert "Prepare brief" in res.text and "Drag files here" in res.text


def test_meetings_lists_upcoming_meetings_with_attendees_and_limits(tmp_path):
    body = make_client(tmp_path).get("/api/meetings").json()
    assert [m["id"] for m in body["meetings"]] == ["m_001", "m_002", "m_003", "m_004", "m_005"]
    m001 = body["meetings"][0]
    assert {"name": "Aisha Rahman", "company": "Harbourview Health", "is_internal": False} in m001["attendees"]
    assert m001["files"] == []
    assert body["limits"] == {"max_files": 10, "max_total_bytes": 20 * 1024 * 1024,
                              "types": [".pdf", ".docx", ".txt", ".md"], "default_news_days": 90}


def test_prep_with_an_uploaded_file_returns_the_brief(tmp_path):
    client = make_client(tmp_path)
    res = client.post("/api/prep", data={"meeting_id": "m_001"}, files=[NOTES])
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["meeting_id"] == "m_001" and body["filename"] == "prep_m_001.md"
    assert "## From your materials" in body["markdown"]
    assert "- Pricing rises 5% in year two. _(file: `notes.txt`)_" in body["markdown"]
    assert "<h2>From your materials</h2>" in body["html"]
    assert body["files"] == ["notes.txt"]
    assert (attachments() / "m_001" / "notes.txt").is_file()
    assert (tmp_path / "out" / "prep_m_001.md").read_text() == body["markdown"]
    listed = client.get("/api/meetings").json()["meetings"][0]
    assert listed["files"] == ["notes.txt"]


def test_prep_without_files_works(tmp_path):
    res = make_client(tmp_path).post("/api/prep", data={"meeting_id": "m_005"})
    assert res.status_code == 200, res.text
    assert "## From your materials" not in res.json()["markdown"]


def test_news_days_is_passed_through(tmp_path):
    search = FixtureSearch()
    client = TestClient(web.create_app(llm_factory=lambda: ScriptedLLM(json_reply(SYNTHESIS)),
                                       searcher_factory=lambda: search, output_dir=tmp_path))
    res = client.post("/api/prep", data={"meeting_id": "m_001", "news_days": "30"})
    assert res.status_code == 200, res.text
    assert "last 30 days" in res.json()["markdown"]
    assert search.news_calls and all(r["start_date"] == "2025-12-16" for r in search.news_calls)


@pytest.mark.parametrize("files, message", [
    ([("files", ("sheet.xlsx", b"x", "application/octet-stream"))], "unsupported file type: sheet.xlsx"),
    ([("files", (f"n{i}.txt", b"x", "text/plain")) for i in range(11)], "11 files attached; the limit is 10"),
    ([NOTES, NOTES], "Two files are named notes.txt"),
])
def test_bad_uploads_fail_clearly_and_store_nothing(tmp_path, files, message):
    res = make_client(tmp_path).post("/api/prep", data={"meeting_id": "m_001"}, files=files)
    assert res.status_code == 400
    assert message in res.json()["detail"]
    assert not (attachments() / "m_001").exists()


def test_oversize_upload_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "MAX_TOTAL_BYTES", 10)
    res = make_client(tmp_path).post("/api/prep", data={"meeting_id": "m_001"}, files=[NOTES])
    assert res.status_code == 400 and "more than" in res.json()["detail"]


def test_unknown_meeting_is_404(tmp_path):
    res = make_client(tmp_path).post("/api/prep", data={"meeting_id": "m_999"})
    assert res.status_code == 404
    assert "list_upcoming_meetings" in res.json()["detail"]


def test_missing_gemini_key_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(web, "load_dotenv", lambda *a, **k: None)
    client = TestClient(web.create_app(searcher_factory=FixtureSearch, output_dir=tmp_path))
    res = client.post("/api/prep", data={"meeting_id": "m_005"})
    assert res.status_code == 400 and "GEMINI_API_KEY is not set" in res.json()["detail"]


def test_model_errors_are_reported_not_crashed(tmp_path):
    from meeting_prep.llm import LLMError

    def failing(_):
        raise LLMError("Gemini request failed: 503 UNAVAILABLE")

    res = make_client(tmp_path, llm_factory=lambda: ScriptedLLM(failing)).post("/api/prep", data={"meeting_id": "m_005"})
    assert res.status_code == 502 and "503" in res.json()["detail"]


def test_folder_names_in_uploads_are_stripped(tmp_path):
    files = [("files", ("../../evil.txt", b"hello", "text/plain"))]
    res = make_client(tmp_path).post("/api/prep", data={"meeting_id": "m_001"}, files=files)
    assert res.status_code == 200, res.text
    assert res.json()["files"] == ["evil.txt"]
    assert (attachments() / "m_001" / "evil.txt").is_file()
    assert not (attachments().parent / "evil.txt").exists()


def test_html_from_model_text_is_escaped(tmp_path):
    synthesis = {**SYNTHESIS, "purpose": "<script>alert(1)</script> [x](javascript:alert(1))"}
    res = make_client(tmp_path, synthesis).post("/api/prep", data={"meeting_id": "m_005"})
    html = res.json()["html"]
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert 'href="javascript:' not in html


def test_attached_file_can_be_removed(tmp_path):
    client = make_client(tmp_path)
    client.post("/api/prep", data={"meeting_id": "m_001"}, files=[NOTES])
    res = client.delete("/api/meetings/m_001/files/notes.txt")
    assert res.status_code == 200 and res.json() == {"files": []}
    assert not (attachments() / "m_001" / "notes.txt").exists()
    assert client.delete("/api/meetings/m_001/files/notes.txt").status_code == 404
    assert client.delete("/api/meetings/m_999/files/notes.txt").status_code == 404


NEW_MEETING = {"title": "Ops sync", "date": "2026-01-16", "time": "09:30", "duration_minutes": 30,
               "attendees": [{"name": "Jo Bloggs", "email": "jo.bloggs@lumora-analytics.com"}]}


def test_meeting_added_from_the_page_can_be_listed_prepped_and_deleted(tmp_path):
    client = make_client(tmp_path)
    res = client.post("/api/meetings", json=NEW_MEETING)
    assert res.status_code == 200, res.text
    assert res.json()["id"] == "m_006"
    listed = {m["id"]: m for m in client.get("/api/meetings").json()["meetings"]}
    assert listed["m_006"]["mine"] is True and listed["m_001"]["mine"] is False
    brief = client.post("/api/prep", data={"meeting_id": "m_006"}).json()["markdown"]
    assert "Ops sync" in brief and "**Jo Bloggs** — Lumora Analytics" in brief and "None" not in brief
    assert client.delete("/api/meetings/m_006").status_code == 200
    assert "m_006" not in [m["id"] for m in client.get("/api/meetings").json()["meetings"]]
    res = client.delete("/api/meetings/m_001")
    assert res.status_code == 404 and "only meetings you added" in res.json()["detail"]


def test_bad_meeting_form_is_a_clear_400(tmp_path):
    res = make_client(tmp_path).post("/api/meetings", json={**NEW_MEETING, "title": ""})
    assert res.status_code == 400 and res.json()["detail"] == "Meeting title is required."


def test_meetings_include_known_company_names_for_suggestions(tmp_path):
    companies = make_client(tmp_path).get("/api/meetings").json()["companies"]
    assert companies == ["Harbourview Health", "Kestrel Freight", "Lumora Analytics", "Solvane Energy"]
