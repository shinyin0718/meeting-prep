"""Meeting prep MCP server.

Read-only access to the mock calendar, contact and interaction data in data/.
Dates in the data are stored relative to "today" (e.g. start_in_days, days_ago)
and resolved at call time, so the seed data never goes stale.
"""

import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

load_dotenv()

DEFAULT_DATA_DIR = Path(__file__).parent / "data"

mcp = FastMCP("meeting-prep", log_level=os.environ.get("FASTMCP_LOG_LEVEL", "INFO").upper())


def _data_dir() -> Path:
    return Path(os.environ.get("MEETING_PREP_DATA_DIR") or DEFAULT_DATA_DIR)


def _load(name: str) -> list[dict[str, Any]]:
    path = _data_dir() / f"{name}.json"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _today() -> date:
    override = os.environ.get("MEETING_PREP_TODAY")
    return date.fromisoformat(override) if override else date.today()


def _now() -> datetime:
    if os.environ.get("MEETING_PREP_TODAY"):
        return datetime.combine(_today(), time.min).astimezone()
    return datetime.now().astimezone()


def _own_domains() -> set[str]:
    raw = os.environ.get("OWN_COMPANY_DOMAINS", "")
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def _normalize(value: str) -> str:
    return value.strip().lower()


def _meeting_start(meeting: dict[str, Any]) -> datetime:
    day = _today() + timedelta(days=meeting["start_in_days"])
    return datetime.combine(day, time.fromisoformat(meeting["start_time"])).astimezone()


def _companies_by_domain() -> dict[str, dict[str, Any]]:
    return {_normalize(c["domain"]): c for c in _load("companies")}


def _find_person(email: str) -> dict[str, Any] | None:
    key = _normalize(email)
    return next((p for p in _load("people") if _normalize(p["email"]) == key), None)


def _require_person(email: str) -> dict[str, Any]:
    if not email or not email.strip():
        raise ToolError("email is required; pass an attendee email address such as one returned by get_meeting.")
    person = _find_person(email)
    if person is None:
        raise ToolError(
            f"No person found for email '{email}'; check the address, or call get_meeting to see attendee emails."
        )
    return person


def _history(email: str) -> list[dict[str, Any]]:
    key = _normalize(email)
    today = _today()
    rows = [
        {
            "id": i["id"],
            "date": (today - timedelta(days=i["days_ago"])).isoformat(),
            "type": i["type"],
            "summary": i["summary"],
        }
        for i in _load("interactions")
        if _normalize(i["person_email"]) == key
    ]
    return sorted(rows, key=lambda r: r["date"], reverse=True)


def _domain_of(email: str) -> str:
    return _normalize(email).rsplit("@", 1)[-1]


@mcp.tool()
def list_upcoming_meetings(
    days_ahead: Annotated[
        int, Field(description="How many days ahead to look, counting from today. 0 means today only.")
    ] = 7,
) -> list[dict[str, Any]]:
    """List meetings that have not started yet and start within the next `days_ahead` days.

    Returns a list of {id, title, start} sorted by start time (earliest first); start is an
    ISO 8601 datetime with UTC offset. Returns an empty list when nothing is scheduled.
    Use get_meeting with an id for attendees and agenda.
    """
    if days_ahead < 0:
        raise ToolError("days_ahead must be 0 or greater; use 7 for the coming week.")
    now = _now()
    last_day = _today() + timedelta(days=days_ahead)
    upcoming = []
    for m in _load("meetings"):
        start = _meeting_start(m)
        if start >= now and start.date() <= last_day:
            upcoming.append({"id": m["id"], "title": m["title"], "start": start.isoformat()})
    return sorted(upcoming, key=lambda m: m["start"])


@mcp.tool()
def get_meeting(
    meeting_id: Annotated[str, Field(description="Meeting id, e.g. 'm_001', as returned by list_upcoming_meetings.")],
) -> dict[str, Any]:
    """Get one meeting's details: title, start/end time, attendees and agenda.

    Each attendee has email, name, company, role and is_internal (true when their email domain
    is in OWN_COMPANY_DOMAINS). agenda is a list of agenda items, or an empty list when the
    meeting has no agenda. Use get_person_profile on an attendee email for relationship details.
    """
    key = _normalize(meeting_id)
    meeting = next((m for m in _load("meetings") if _normalize(m["id"]) == key), None)
    if meeting is None:
        raise ToolError(
            f"No meeting found with id '{meeting_id}'; call list_upcoming_meetings to see valid meeting ids."
        )
    companies = _companies_by_domain()
    own = _own_domains()
    attendees = []
    for email in meeting["attendee_emails"]:
        person = _find_person(email) or {}
        domain = _domain_of(email)
        attendees.append(
            {
                "email": email,
                "name": person.get("name"),
                "company": companies.get(domain, {}).get("name"),
                "role": person.get("role"),
                "is_internal": domain in own,
            }
        )
    start = _meeting_start(meeting)
    return {
        "id": meeting["id"],
        "title": meeting["title"],
        "start": start.isoformat(),
        "end": (start + timedelta(minutes=meeting["duration_minutes"])).isoformat(),
        "duration_minutes": meeting["duration_minutes"],
        "attendees": attendees,
        "agenda": meeting.get("agenda") or [],
    }


@mcp.tool()
def get_person_profile(
    email: Annotated[str, Field(description="The person's email address, e.g. an attendee email from get_meeting.")],
) -> dict[str, Any]:
    """Get a person's profile: name, company, role, tenure and relationship flags.

    is_internal is true when the email domain is in OWN_COMPANY_DOMAINS (a colleague).
    first_meeting is true for an external person with no prior interactions on record;
    this is the deterministic trigger for public-web research on new contacts.
    last_interaction_date is the most recent interaction (ISO date) or null if there is none.
    """
    person = _require_person(email)
    history = _history(person["email"])
    domain = _normalize(person["company_domain"])
    is_internal = domain in _own_domains()
    return {
        "name": person["name"],
        "email": person["email"],
        "company": _companies_by_domain().get(domain, {}).get("name"),
        "company_domain": person["company_domain"],
        "role": person["role"],
        "tenure": person.get("tenure"),
        "is_internal": is_internal,
        "first_meeting": not is_internal and not history,
        "last_interaction_date": history[0]["date"] if history else None,
    }


@mcp.tool()
def get_interaction_history(
    email: Annotated[str, Field(description="The person's email address.")],
    limit: Annotated[int, Field(description="Maximum number of interactions to return (newest first).")] = 10,
) -> list[dict[str, Any]]:
    """Get past interactions with a person, newest first.

    Each item has id, date (ISO date), type (call, email or meeting) and summary.
    Returns an empty list when there are no prior interactions on record; do not infer history.
    """
    if limit < 1:
        raise ToolError("limit must be at least 1; use 10 for the default.")
    person = _require_person(email)
    return _history(person["email"])[:limit]


@mcp.tool()
def get_open_items(
    email_or_domain: Annotated[
        str,
        Field(
            description=(
                "A person's email (items tied to that person) or a company domain such as "
                "'kestrelfreight.com' (company-level items plus items for everyone at that company)."
            )
        ),
    ],
) -> list[dict[str, Any]]:
    """Get open action items for a person or a company, soonest due first.

    Each item has id, description, owner, due_date (ISO date), overdue (true if past due),
    person_email and company_domain (whichever the item is tied to).
    Returns an empty list when nothing is open.
    """
    query = _normalize(email_or_domain or "")
    if not query:
        raise ToolError("email_or_domain is required; pass an attendee email or a company domain.")
    if "@" in query:
        person = _require_person(query)
        emails = {_normalize(person["email"])}
        domains: set[str] = set()
    else:
        if query not in _companies_by_domain():
            raise ToolError(
                f"No company found for domain '{email_or_domain}'; check the domain "
                "(e.g. the part after @ in an attendee email) or pass a person's email instead."
            )
        domains = {query}
        emails = {_normalize(p["email"]) for p in _load("people") if _normalize(p["company_domain"]) == query}

    today = _today()
    items = []
    for o in _load("open_items"):
        person_email = _normalize(o["person_email"]) if o.get("person_email") else None
        company_domain = _normalize(o["company_domain"]) if o.get("company_domain") else None
        if person_email in emails or company_domain in domains:
            due = today + timedelta(days=o["due_in_days"])
            items.append(
                {
                    "id": o["id"],
                    "description": o["description"],
                    "owner": o["owner"],
                    "due_date": due.isoformat(),
                    "overdue": due < today,
                    "person_email": o.get("person_email"),
                    "company_domain": o.get("company_domain"),
                }
            )
    return sorted(items, key=lambda i: i["due_date"])


if __name__ == "__main__":
    mcp.run(transport="stdio")
