# meeting-prep

Command-line agent that turns an upcoming meeting into a one-page prep brief. See [SPEC.md](SPEC.md) for the full build spec and [NOTES.md](NOTES.md) for build progress.

> Status: session 2 of 6. The mock data, MCP server and agent/CLI are done. Web research (sessions 3–4) and file attachments (session 5) come next.

## Setup

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node.js/npx (only for the MCP Inspector).

```bash
uv sync                  # creates .venv from uv.lock
cp .env.example .env     # then fill in values; never commit .env
```

| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Google Gemini API key, free tier ([get one](https://aistudio.google.com/apikey)); used from session 2 |
| `GEMINI_MODEL` | Gemini model id, default `gemini-3.8-flash` |
| `TAVILY_API_KEY` | Tavily Search API key, free plan ([get one](https://app.tavily.com)); used for web research from session 3 |
| `OWN_COMPANY_DOMAINS` | Comma-separated email domains of your own company; seed data uses `lumora-analytics.com` |
| `MEETING_PREP_TODAY` | Optional `YYYY-MM-DD` to pin "today" for reproducible runs |
| `MEETING_PREP_DATA_DIR` | Optional path to an alternative data directory |

## Commands

```bash
uv run main.py prep --meeting-id m_001   # write output/prep_m_001.md (needs GEMINI_API_KEY)
uv run main.py prep --next               # brief for the earliest meeting in the next 7 days
uv run pytest                    # run tests (Gemini is faked; no key needed)
uv run mcp dev mcp_server.py     # open the MCP Inspector to browse/try the tools
uv run mcp_server.py             # run the MCP server on stdio
```

## Mock data

`data/*.json` holds 5 meetings, 10 people, 4 companies, 16 interactions and 6 open items. Dates are stored relative to today (`start_in_days`, `days_ago`, `due_in_days`) and resolved when a tool is called, so the meetings are always 1–5 days in the future.

## How a brief is made

1. `main.py` starts `mcp_server.py` over stdio and reads the meeting, every attendee's profile, history and open items through the MCP tools.
2. Gemini gets `skills/meeting-prep/SKILL.md` as its instructions plus those records (wrapped in `<internal_records>` tags and treated as data). It may call the same read-only tools (up to 10 rounds), then returns purpose, likely asks, questions and risks as JSON.
3. The program renders the Markdown: factual sections (agenda, who's in the room, history, open items) straight from the records, so history is never invented, and the judgment sections from Gemini's answer.
