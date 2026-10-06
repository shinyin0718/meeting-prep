"""Your own meetings, people and notes, saved from the web page's "Add meeting" form.

They live in data/mine/ (gitignored, so they never reach GitHub) next to the sample data, and use
real dates (start_date, date, due_date) instead of the samples' relative offsets. mcp_server reads both.
"""

from __future__ import annotations

import itertools
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
# People added without an email get a stand-in key; for a new company the domain is made up under
# the reserved .invalid TLD, which searches and the brief leave out (see real_domain).
PLACEHOLDER_TLD = ".invalid"


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


def real_domain(domain: str | None) -> str | None:
    return domain if domain and not domain.lower().endswith(PLACEHOLDER_TLD) else None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unknown"


def _stand_in_email(name: str, domain: str, people: dict[str, dict[str, Any]]) -> str:
    """The same name at the same company is the same person; otherwise a new, unused key."""
    for email, p in people.items():
        if p["name"].lower() == name.lower() and p["company_domain"].lower() == domain:
            return email
    local = _slug(name).replace("-", ".")
    for n in itertools.count(1):
        email = f"{local}{n if n > 1 else ''}@{domain}"
        if email not in people:
            return email
    raise AssertionError("unreachable")


def _who(entry: dict[str, Any], emails: list[str]) -> str | None:
    """History and to-dos point at an attendee by position in the form (or by email)."""
    index = entry.get("attendee")
    if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(emails):
        return emails[index]
    email = str(entry.get("email") or "").lower()
    return email if email in emails else None


def company_names() -> list[str]:
    return sorted({c["name"] for c in _all("companies")}, key=str.lower)


def is_mine(meeting_id: str) -> bool:
    return any(m["id"] == meeting_id for m in _mine("meetings"))


def add_meeting(form: dict[str, Any]) -> dict[str, Any]:
    """Validate everything first, then save. Fields: title, date, time, duration_minutes, agenda [str],
    attendees [{name, email, company, role}], history [{attendee, date, type, summary}],
    open_items [{attendee, description, owner, due_date}] (attendee = index into attendees).
    Email is optional: without one, the company name decides the domain. Known people and companies are
    reused as they are."""
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
    known_people = {p["email"].lower(): p for p in _all("people")}
    known_companies = {c["domain"].lower() for c in _all("companies")}
    domain_by_name = {c["name"].lower(): c["domain"].lower() for c in _all("companies")}
    people, companies = _mine("people"), _mine("companies")
    emails: list[str] = []
    for i, a in enumerate(attendees, 1):
        email = _text(a.get("email"), f"Attendee {i}'s email", required=False).lower()
        name = _text(a.get("name"), f"Attendee {i}'s name", required=False)
        company = _text(a.get("company"), f"Attendee {i}'s company", required=False)
        has_email = bool(email)
        if has_email:
            if not EMAIL.match(email):
                raise DataError(f"Attendee {i}'s email doesn't look right: {email}")
            domain = email.rsplit("@", 1)[1]
        else:
            if not name:
                raise DataError(f"Attendee {i} needs a name or an email.")
            if not company:
                raise DataError(f"Add {name}'s email or company, so the brief knows where they work.")
            domain = domain_by_name.get(company.lower()) or f"{_slug(company)}{PLACEHOLDER_TLD}"
            email = _stand_in_email(name, domain, known_people)
        if email in emails:
            raise DataError(f"{email if has_email else name} is listed twice.")
        emails.append(email)
        if domain not in known_companies:
            if not company:
                raise DataError(f"Company for {email} is required.")
            companies.append({"name": company, "domain": domain})
            known_companies.add(domain)
            domain_by_name.setdefault(company.lower(), domain)
        if email not in known_people:
            if not name:
                raise DataError(f"Attendee {i}'s name is required.")
            person = {"name": name, "email": email, "company_domain": domain,
                      "role": _text(a.get("role"), f"Attendee {i}'s role", required=False) or None}
            if not has_email:
                person["email_unknown"] = True
            people.append(person)
            known_people[email] = person

    interactions = _mine("interactions")
    all_interactions = _all("interactions")
    for i, h in enumerate(form.get("history") or [], 1):
        label = f"Past conversation {i}"
        email = _who(h, emails)
        if email is None:
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
        email = _who(o, emails)
        if email is None:
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
