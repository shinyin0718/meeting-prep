"""Your own meetings, people and notes, saved from the web page's "Add meeting" form.

They live in data/mine/ (gitignored, so they never reach GitHub) next to the sample data, and use
real dates (start_date, date, due_date) instead of the samples' relative offsets. mcp_server reads both.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
INTERACTION_TYPES = ("call", "email", "meeting")
MAX_ATTENDEES = 30


class DataError(ValueError):
    pass


def data_dir() -> Path:
    return Path(os.environ.get("MEETING_PREP_DATA_DIR") or ROOT / "data")


def user_dir() -> Path:
    return Path(os.environ.get("MEETING_PREP_USER_DATA_DIR") or data_dir() / "mine")


def _read(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _mine(name: str) -> list[dict[str, Any]]:
    return _read(user_dir() / f"{name}.json")


def _all(name: str) -> list[dict[str, Any]]:
    return _read(data_dir() / f"{name}.json") + _mine(name)


def _write(name: str, rows: list[dict[str, Any]]) -> None:
    folder = user_dir()
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, folder / f"{name}.json")


def _next_id(prefix: str, rows: list[dict[str, Any]]) -> str:
    numbers = [int(m.group(1)) for r in rows if (m := re.fullmatch(rf"{prefix}_(\d+)", str(r.get("id", ""))))]
    return f"{prefix}_{max(numbers, default=0) + 1:03d}"


def _today() -> date:
    override = os.environ.get("MEETING_PREP_TODAY")
    return date.fromisoformat(override) if override else datetime.now().astimezone().date()


def _now() -> datetime:
    if os.environ.get("MEETING_PREP_TODAY"):
        return datetime.combine(_today(), time.min).astimezone()
    return datetime.now().astimezone()


def _text(value: Any, label: str, *, required: bool = True, limit: int = 300) -> str:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise DataError(f"{label} is required.")
    if len(text) > limit:
        raise DataError(f"{label} is too long (at most {limit} characters).")
    return text


def _date(value: Any, label: str) -> date:
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        raise DataError(f"{label} must be a date like 2026-10-20.") from None


def is_mine(meeting_id: str) -> bool:
    return any(m["id"] == meeting_id for m in _mine("meetings"))


def add_meeting(form: dict[str, Any]) -> dict[str, Any]:
    """Validate everything first, then save. Fields: title, date, time, duration_minutes, agenda [str],
    attendees [{name, email, company, role}], history [{email, date, type, summary}],
    open_items [{email, description, owner, due_date}]. Known people and companies are reused as they are."""
    title = _text(form.get("title"), "Meeting title")
    start_date = _date(form.get("date"), "Date")
    try:
        start_time = time.fromisoformat(str(form.get("time") or "")).replace(second=0, microsecond=0)
    except ValueError:
        raise DataError("Time must be like 14:30.") from None
    if datetime.combine(start_date, start_time).astimezone() < _now():
        raise DataError("That date and time have already passed; pick a time in the future.")
    try:
        duration = int(form.get("duration_minutes") or 30)
    except (TypeError, ValueError):
        duration = 0
    if not 5 <= duration <= 600:
        raise DataError("Length must be between 5 and 600 minutes.")
    agenda = [a for a in (_text(x, "Agenda item", required=False) for x in form.get("agenda") or []) if a]

    attendees = form.get("attendees") or []
    if not attendees:
        raise DataError("Add at least one attendee.")
    if len(attendees) > MAX_ATTENDEES:
        raise DataError(f"At most {MAX_ATTENDEES} attendees.")
    known_people = {p["email"].lower() for p in _all("people")}
    known_companies = {c["domain"].lower() for c in _all("companies")}
    people, companies = _mine("people"), _mine("companies")
    emails: list[str] = []
    for i, a in enumerate(attendees, 1):
        email = _text(a.get("email"), f"Attendee {i}'s email").lower()
        if not EMAIL.match(email):
            raise DataError(f"Attendee {i}'s email doesn't look right: {email}")
        if email in emails:
            raise DataError(f"{email} is listed twice.")
        emails.append(email)
        domain = email.rsplit("@", 1)[1]
        if domain not in known_companies:
            companies.append({"name": _text(a.get("company"), f"Company for {email}"), "domain": domain})
            known_companies.add(domain)
        if email not in known_people:
            people.append({"name": _text(a.get("name"), f"Attendee {i}'s name"), "email": email,
                           "company_domain": domain,
                           "role": _text(a.get("role"), f"Attendee {i}'s role", required=False) or None})
            known_people.add(email)

    interactions = _mine("interactions")
    all_interactions = _all("interactions")
    for i, h in enumerate(form.get("history") or [], 1):
        label = f"Past conversation {i}"
        email = str(h.get("email") or "").lower()
        if email not in emails:
            raise DataError(f"{label}: pick who it was with.")
        when = _date(h.get("date"), f"{label}'s date")
        if when > _today():
            raise DataError(f"{label}'s date is in the future.")
        kind = str(h.get("type") or "")
        if kind not in INTERACTION_TYPES:
            raise DataError(f"{label}: type must be one of {', '.join(INTERACTION_TYPES)}.")
        row = {"id": _next_id("i", all_interactions), "person_email": email, "date": when.isoformat(),
               "type": kind, "summary": _text(h.get("summary"), f"{label}'s summary", limit=500)}
        interactions.append(row)
        all_interactions.append(row)

    open_items = _mine("open_items")
    all_items = _all("open_items")
    for i, o in enumerate(form.get("open_items") or [], 1):
        label = f"To-do {i}"
        email = str(o.get("email") or "").lower()
        if email not in emails:
            raise DataError(f"{label}: pick who it's for.")
        row = {"id": _next_id("o", all_items), "person_email": email, "company_domain": None,
               "description": _text(o.get("description"), f"{label}'s description"),
               "owner": _text(o.get("owner"), f"{label}'s owner"),
               "due_date": _date(o.get("due_date"), f"{label}'s due date").isoformat()}
        open_items.append(row)
        all_items.append(row)

    meeting = {"id": _next_id("m", _all("meetings")), "title": title, "start_date": start_date.isoformat(),
               "start_time": start_time.strftime("%H:%M"), "duration_minutes": duration,
               "attendee_emails": emails, "agenda": agenda}
    _write("companies", companies)
    _write("people", people)
    _write("interactions", interactions)
    _write("open_items", open_items)
    _write("meetings", [*_mine("meetings"), meeting])
    return meeting


def delete_meeting(meeting_id: str) -> None:
    """Only meetings you added can be deleted. People and past conversations are kept for other meetings."""
    meetings = _mine("meetings")
    remaining = [m for m in meetings if m["id"] != meeting_id]
    if len(remaining) == len(meetings):
        raise DataError(f"{meeting_id} isn't one of your meetings; only meetings you added can be deleted.")
    _write("meetings", remaining)
