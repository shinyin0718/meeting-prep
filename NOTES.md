# NOTES

Read this first in every session. Update it at the end of every session.

## Session 1 — scaffold, mock data, MCP server, tests (done)

**Checkpoint:** `uv run pytest` → 38 passed. `uv run mcp dev mcp_server.py` starts the MCP Inspector (http://127.0.0.1:6274) and lists all five tools with descriptions.

### File layout

```
SPEC.md              build spec (source of truth)
NOTES.md             this file
README.md            setup + commands (stub; completed in session 6)
pyproject.toml       uv project, Python >=3.12, pytest config (pythonpath=".")
uv.lock
.env.example         ANTHROPIC_API_KEY, CLAUDE_MODEL, OWN_COMPANY_DOMAINS (+ optional overrides)
mcp_server.py        FastMCP server, stdio transport, 5 read-only tools
data/
  companies.json     4 companies (name, domain)
  people.json        10 people (name, email, company_domain, role, tenure)
  meetings.json      5 meetings m_001..m_005 (start_in_days, start_time, duration_minutes, attendee_emails, agenda|null)
  interactions.json  16 interactions (person_email, days_ago, type, summary)
  open_items.json    6 open items (person_email|company_domain, description, owner, due_in_days)
tests/
  conftest.py        autouse fixture: MEETING_PREP_TODAY=2026-01-15, OWN_COMPANY_DOMAINS=lumora-analytics.com
  test_mcp_server.py every tool (happy path, not-found, empty), seed scenarios, protocol + stdio checks
  fixtures/          empty; for mocked web-search responses (sessions 3–4)
```

### Key decisions

- **mcp pinned to `>=1.20,<2`** (1.30.0 locked). mcp 2.x renamed `FastMCP` to `MCPServer`; the spec and the Anthropic Academy course use FastMCP (`from mcp.server.fastmcp import FastMCP`). Don't upgrade to 2.x without migrating.
- **Dates are relative.** Data stores `start_in_days` / `days_ago` / `due_in_days`; tools resolve them against today at call time (local timezone). Meetings are always 1–5 days ahead, so `list_upcoming_meetings()` and `--next` (earliest upcoming) work on any real date. Set `MEETING_PREP_TODAY=YYYY-MM-DD` to pin today (tests do).
- **`first_meeting` = external AND no interactions on record.** Colleagues (domain in `OWN_COMPANY_DOMAINS`) are never `first_meeting`, so the session-3 research trigger can use the flag directly. `is_internal` is also returned by `get_person_profile` and on each attendee in `get_meeting`.
- **`OWN_COMPANY_DOMAINS` is read at call time** (`.env` loaded via python-dotenv). If unset, everyone is external — set it to `lumora-analytics.com` for the seed data.
- **Errors** raise `ToolError` (MCP result `isError: true`) with a next step, e.g. "No person found for email '…'; check the address, or call get_meeting to see attendee emails." Empty results are `[]`.
- Only `mcp[cli]` and `python-dotenv` are installed. Add `anthropic` (session 2) and `pypdf`, `python-docx` (session 5) when needed.

### Tool contract (what session 2 builds on)

| Tool | Input | Returns |
| --- | --- | --- |
| `list_upcoming_meetings` | `days_ahead=7` (0 = today only) | `[{id, title, start}]`, earliest first |
| `get_meeting` | `meeting_id` (case-insensitive) | `{id, title, start, end, duration_minutes, attendees:[{email,name,company,role,is_internal}], agenda:[str]}` (`agenda` is `[]` when none) |
| `get_person_profile` | `email` (case-insensitive) | `{name, email, company, company_domain, role, tenure, is_internal, first_meeting, last_interaction_date}` |
| `get_interaction_history` | `email`, `limit=10` | `[{id, date, type, summary}]`, newest first |
| `get_open_items` | `email_or_domain` | email → that person's items; domain → company items + items of everyone at that company. `[{id, description, owner, due_date, overdue, person_email, company_domain}]`, soonest due first |

### Seed scenarios

| Meeting | Scenario | Attendees (internal = Lumora Analytics) |
| --- | --- | --- |
| `m_001` | Normal: all known, agenda, open items | Priya (int), Aisha Rahman + Grace Liu (Harbourview Health) — Grace's item o_003 is overdue |
| `m_002` | One first-time external | Daniel (int), Aisha, **Tom Becker** (first time, Harbourview) |
| `m_003` | No agenda | Priya (int), Carlos Mendes, **John Smith** (first time, very common name, Solvane Energy) |
| `m_004` | Two from same external company | Daniel (int), Marcus Webb + Elena Petrova (Kestrel Freight) |
| `m_005` | Internal only | Priya, Daniel, Mei Lin Tan |

People with no history: Tom Becker, John Smith. Own company: Lumora Analytics (`lumora-analytics.com`). Companies are fictional; web-search results for them must come from `tests/fixtures/`.

### For sessions 2+

- Tests import `mcp_server` directly (functions stay plain callables after `@mcp.tool()`); use `mcp.shared.memory.create_connected_server_and_client_session(srv.mcp._mcp_server)` for in-process protocol tests, or `stdio_client` to spawn `mcp_server.py` like the agent will.
- `mcp dev` needs Node/npx and runs the latest `@modelcontextprotocol/inspector` (2.9.0 verified). Set `DANGEROUSLY_OMIT_AUTH=true` to skip the Inspector auth token (local only). If the Inspector page shows "404 Not Found", the npx cache is corrupt (happens when two `npx @modelcontextprotocol/inspector` runs install at once): delete the matching `~/.npm/_npx/<hash>` dir and rerun. Headless check: `npx @modelcontextprotocol/inspector --cli uv run mcp_server.py --method tools/list`.
- The spec says to follow the Anthropic Academy course project structure (MCP client, Claude wrapper, CLI app); that reference repo was not available in session 1 — provide it for session 2.
- `.gitignore` already excludes `.env`, `output/`, `data/cache/`, `data/attachments/`.
- Skipped in session 1 (by scope): agent, CLI, SKILL.md, web research, file ingestion, evals.
