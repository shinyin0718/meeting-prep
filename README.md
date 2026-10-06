# meeting-prep

Command-line agent that turns an upcoming meeting into a one-page prep brief. See [SPEC.md](SPEC.md) for the full build spec and [NOTES.md](NOTES.md) for build progress.

> Status: session 1 of 6 — mock data and the MCP server are done; the agent/CLI (`main.py prep ...`) arrives in session 2.

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
uv run pytest                    # run tests
uv run mcp dev mcp_server.py     # open the MCP Inspector to browse/try the tools
uv run mcp_server.py             # run the MCP server on stdio
```

## Mock data

`data/*.json` holds 5 meetings, 10 people, 4 companies, 16 interactions and 6 open items. Dates are stored relative to today (`start_in_days`, `days_ago`, `due_in_days`) and resolved when a tool is called, so the meetings are always 1–5 days in the future.
