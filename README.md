# meeting-prep

Command-line agent that turns an upcoming meeting into a one-page prep brief. See [SPEC.md](SPEC.md) for the full build spec and [NOTES.md](NOTES.md) for build progress.

> Status: session 4 of 6. The mock data, MCP server, agent/CLI, first-time contact research and company news are done. File attachments (session 5) come next.

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
uv run main.py prep --meeting-id m_001 --research "Grace Liu, Harbourview Health"   # also research anyone by hand
uv run main.py prep --meeting-id m_001 --news-days 30   # company news window (default 90 days)
uv run pytest                    # run tests (Gemini and Tavily are faked; no keys needed)
uv run mcp dev mcp_server.py     # open the MCP Inspector to browse/try the tools
uv run mcp_server.py             # run the MCP server on stdio
```

## Mock data

`data/*.json` holds 5 meetings, 10 people, 4 companies, 16 interactions and 6 open items. Dates are stored relative to today (`start_in_days`, `days_ago`, `due_in_days`) and resolved when a tool is called, so the meetings are always 1–5 days in the future.

## How a brief is made

1. `main.py` starts `mcp_server.py` over stdio and reads the meeting, every attendee's profile, history and open items through the MCP tools.
2. Each external company (one per email domain; `OWN_COMPANY_DOMAINS` skipped) gets 3 Tavily searches limited to the news window. Only items that name the company or its domain and carry a date inside the window are kept; there is no fallback to older news. Items from neither the company's own site, a press wire nor an established outlet, or framed as rumor ("reportedly", "in talks"…), are labeled unconfirmed.
3. Attendees flagged `first_meeting` (external, no history) and anyone passed with `--research` are searched on Tavily: 3 searches each, on name, company, role and email domain together. Only dated results that mention the name *and* the company or domain count; if none do, the brief says "couldn't confirm identity" and states nothing about the person. `TAVILY_API_KEY` is only needed when this step runs.
4. Gemini gets `skills/meeting-prep/SKILL.md` as its instructions plus those records (wrapped in `<internal_records>` tags and treated as data). Matching search results go in `<web_results>` tags as numbered ids (`S1`, `S2`…) with title, date and snippet but no URL. It may call the same read-only tools (up to 10 rounds), then returns purpose, likely asks, questions, risks background facts (each citing result ids) and, per news item, a summary, why it matters and a 0–3 relevance as JSON.
5. The program renders the Markdown: factual sections (agenda, who's in the room, history, open items) straight from the records, so history is never invented, and the judgment sections from Gemini's answer. News is ranked by relevance, then date (at most 5 per company; relevance 0 drops an item). News items and background facts become inline `[n]` references to a Sources list (title, link, date) built from the search results, so every URL in the brief came from Tavily; facts citing no valid result, or containing a URL, are dropped.
