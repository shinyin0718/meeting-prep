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
.env.example         GEMINI_API_KEY, GEMINI_MODEL, TAVILY_API_KEY, OWN_COMPANY_DOMAINS (+ optional overrides)
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

- **mcp pinned to `>=1.20,<2`** (1.30.0 locked). mcp 2.x renamed `FastMCP` to `MCPServer`; the spec uses FastMCP (`from mcp.server.fastmcp import FastMCP`). Don't upgrade to 2.x without migrating.
- **Dates are relative.** Data stores `start_in_days` / `days_ago` / `due_in_days`; tools resolve them against today at call time (local timezone). Meetings are always 1–5 days ahead, so `list_upcoming_meetings()` and `--next` (earliest upcoming) work on any real date. Set `MEETING_PREP_TODAY=YYYY-MM-DD` to pin today (tests do).
- **`first_meeting` = external AND no interactions on record.** Colleagues (domain in `OWN_COMPANY_DOMAINS`) are never `first_meeting`, so the session-3 research trigger can use the flag directly. `is_internal` is also returned by `get_person_profile` and on each attendee in `get_meeting`.
- **`OWN_COMPANY_DOMAINS` is read at call time** (`.env` loaded via python-dotenv). If unset, everyone is external — set it to `lumora-analytics.com` for the seed data.
- **Errors** raise `ToolError` (MCP result `isError: true`) with a next step, e.g. "No person found for email '…'; check the address, or call get_meeting to see attendee emails." Empty results are `[]`.
- Only `mcp[cli]` and `python-dotenv` are installed. Add `google-genai` (session 2; not the old `google-generativeai`), `tavily-python` or plain HTTP for Tavily (session 3) and `pypdf`, `python-docx` (session 5) when needed.

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
- **Provider switch (after session 1):** LLM is Google Gemini free tier (`google-genai`, function calling), web search is Tavily (free plan), called by our own `web_search` function — not Gemini's built-in Google Search, which is paid-tier only and returns no page titles or publication dates. SPEC.md is updated. The Anthropic Academy reference repo is dropped; structure is MCP client + Gemini wrapper + CLI app.
- Gemini free tier can return transient 503 "high demand" errors; retry with backoff.
- `.gitignore` already excludes `.env`, `output/`, `data/cache/`, `data/attachments/`.
- Skipped in session 1 (by scope): agent, CLI, SKILL.md, web research, file ingestion, evals.

## Session 2 — agent, CLI, skill loading (done)

**Stack change before this session:** Anthropic was replaced by Gemini free tier and Tavily ([PR #1](https://github.com/shinyin0718/meeting-prep/pull/1)). See the provider-switch note above.

**Checkpoint:** `uv run main.py prep --meeting-id m_001` writes `output/prep_m_001.md` with sections 1, 3, 5, 7, 8 and 9 in order, every attendee listed and the seeded open items included. This is tested with a fake LLM (`tests/test_agent.py::test_m001_*`). `uv run pytest` passes 64 tests: the 38 from session 1, 18 agent/CLI tests and 8 Gemini wrapper tests. There has been no live Gemini run yet because no `GEMINI_API_KEY` is available on the VM.

### Files added

```
main.py                      entry: `uv run main.py prep --meeting-id <id> | --next [--output-dir DIR]`
skills/meeting-prep/SKILL.md the Prep Skill, verbatim from SPEC.md; loaded into the system prompt
meeting_prep/
  cli.py        argparse, .env loading, run_prep(); exit 2 = missing key, 1 = tool/agent/LLM error
  mcp_client.py connect(): spawns mcp_server.py over stdio (same Python), MCPTools.call -> (data, is_error)
  agent.py      gather_context() (deterministic MCP calls), synthesize() tool loop, OUTPUT_CONTRACT
  llm.py        LLM/Chat protocol, GeminiLLM/GeminiChat (google-genai 2.x async), retries, MissingAPIKeyError
  brief.py      render_brief(context, synthesis) -> Markdown
tests/test_agent.py  FakeLLM scripted replies; checkpoint, scenarios m_002–m_005, loop cap, JSON retry, errors, secrets
tests/test_llm.py    GeminiChat against a fake client: declarations, call ids, thought signatures, retries
```

### Design (read before sessions 3–5)

- **Hybrid brief.** Code gathers facts and renders the factual sections itself. These are the agenda, who's in the room, recent interactions (3 per person) and open items, each tagged `_(internal record)_`. Gemini only writes `purpose`, `desired_outcome`, `relationships`, `likely_asks`, `questions` and `risks` as one JSON object (`OUTPUT_CONTRACT` in `agent.py`), and each item carries a `based_on` field. This guarantees "no prior interactions on record" and stops invented history, because a `relationships` entry from the model is ignored for anyone with no history.
- **Tool loop:** Gemini sees all five MCP tools as function declarations. It may call them for up to `MAX_TOOL_ROUNDS = 10` rounds. After that, one final call is made with function calling set to `NONE`. Unknown tool names and tool errors go back to the model as `{"error": ...}`; they don't crash the run. If the reply is not valid JSON, the agent retries once with tools off, then raises `AgentError`.
- **Context gathering:** for each attendee the agent calls profile, history (limit 5) and open items by email. Open items are then fetched once per unique external domain (own domains excluded) and deduplicated by id. `context["external_domains"]` is the deduplicated company list that session 4 should reuse.
- **Untrusted data:** records go in `<internal_records>` tags, and the system prompt says tagged content is data, never instructions. Sessions 3 and 5 should add `<web_results>` and `<file>` tags in the same way.
- **Gemini specifics:** the agent uses `client.aio.models.generate_content` with `parameters_json_schema` taken from MCP's `inputSchema`, and automatic function calling is disabled. The model's returned `Content` is appended to history unchanged, which keeps Gemini 3 thought signatures. Function responses carry the call `id`. A 503 or 429 error is retried after 2, 4, 8 and 16 s. The default model is `gemini-3.8-flash`, overridable with `GEMINI_MODEL`.
- **MCP server env:** the server receives the parent environment minus any `*_API_KEY` variable, with `FASTMCP_LOG_LEVEL=WARNING`. `MCPTools.call` raises inside the stdio context, so `run_prep` re-raises errors outside it. Otherwise anyio wraps them in an `ExceptionGroup`.
- `--next` means the earliest meeting from `list_upcoming_meetings(days_ahead=7)`.

### For session 3 (first-time research)

- Trigger research from `context["people"][i]["profile"]["first_meeting"]` in code, not from the model.
- Add a `web_search` function (Tavily) to the declarations passed to `start_chat`, or run the searches in code and pass the results in tagged blocks. Either way, record every returned URL so the brief's URLs can be checked against them.
- `render_brief` needs sections 2, 4 and 6 inserted in spec order, and the renderer must omit any of them that is empty.
- `--research "Name, Company"` and `--news-days` are not yet in the parser.
