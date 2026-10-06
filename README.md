# meeting-prep

Command-line agent that turns an upcoming meeting into a one-page Markdown prep brief, to read just before you walk in. It pulls internal records (calendar, contacts, history, open items) from a local MCP server, researches first-time contacts and recent company news on the web, reads files you attach, and has Google Gemini write the judgment parts: purpose, likely asks, questions and risks.

Every fact is tagged by origin. Internal records are tagged as such, web facts get a numbered link and date, and file facts get the filename. Records are mock data in `data/`. See [SPEC.md](SPEC.md) for the build spec and [NOTES.md](NOTES.md) for design notes from each build session.

## Setup

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node.js/npx (only for the MCP Inspector).

```bash
uv sync                  # creates .venv from uv.lock
cp .env.example .env     # then fill in values; never commit .env
```

| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Google Gemini API key, free tier ([get one](https://aistudio.google.com/apikey)). Needed for every real brief |
| `GEMINI_MODEL` | Gemini model id, default `gemini-3.8-flash` |
| `TAVILY_API_KEY` | Tavily Search API key, free plan, 1,000 credits a month ([get one](https://app.tavily.com)). Needed when a meeting has an external attendee |
| `OWN_COMPANY_DOMAINS` | Comma-separated email domains of your own company; seed data uses `lumora-analytics.com` |
| `MEETING_PREP_TODAY` | Optional `YYYY-MM-DD` to pin "today" for reproducible runs |
| `MEETING_PREP_DATA_DIR` | Optional path to an alternative data directory |
| `MEETING_PREP_ATTACHMENTS_DIR` | Optional folder for attached files, default `data/attachments` |
| `MEETING_PREP_USER_DATA_DIR` | Optional folder for meetings you add on the web page, default `data/mine` |
| `MEETING_PREP_LIVE` | Set to `1` to run the live smoke test |

## Commands

```bash
uv run web.py                           # web page at http://127.0.0.1:8000 (see below)
uv run main.py prep --meeting-id m_001   # write output/prep_m_001.md
uv run main.py prep --next               # brief for the earliest meeting in the next 7 days
uv run main.py prep --meeting-id m_001 --research "Grace Liu, Harbourview Health"   # also research anyone by hand
uv run main.py prep --meeting-id m_001 --news-days 30   # company news window (default 90 days)
uv run main.py prep --meeting-id m_001 --files deck.pdf notes.docx   # attach PDF/DOCX/TXT/MD files
uv run mcp dev mcp_server.py     # open the MCP Inspector to browse and try the tools (needs Node/npx)
uv run mcp_server.py             # run the MCP server on stdio
```

`--files` accepts at most 10 files and 20 MB in total. Files are copied to `data/attachments/<meeting_id>/` and reused the next time you prep that meeting. To stop using a file, delete it from that folder.

## Web page

`uv run web.py` starts a local page at http://127.0.0.1:8000. Leave the terminal open while you use it, and press Ctrl+C to stop.

1. **Pick a meeting**, or click **+ Add a meeting** to add your own. Fill in the title, date and time, who's coming, and optionally the agenda, past conversations and open to-dos, then click **Save meeting**. Your meetings get a "Yours" label and a **Delete** link. People and companies are added the first time you use them and reused after that. Everything you add is saved in `data/mine/` on your computer only (it's gitignored) and uses real dates.
2. **Add your files** (optional) by dragging them onto the dashed box or clicking it. The same rules as `--files` apply: PDF, DOCX, TXT or MD, at most 10 files and 20 MB per meeting. Files already attached to the meeting are listed with a **Remove** button.
3. Click **Prepare brief**. The brief appears on the page with **Download (.md)** and **Copy text** buttons, and is also saved to `output/prep_<id>.md`.

It runs the same steps as `main.py prep` and needs the same keys in `.env`. The page has no login, so it only listens on this computer. Set `MEETING_PREP_HOST` / `MEETING_PREP_PORT` to change that, but only on a network you trust.

## Tests and evals

```bash
uv run pytest                    # unit and end-to-end tests; Gemini and Tavily are faked, so no keys are needed
uv run evals.py                  # eval script: 3 scenarios (normal, new contact, no agenda), key-string checks
uv run evals.py --live           # the same scenarios against real Gemini + Tavily (spends free-tier quota)
MEETING_PREP_LIVE=1 uv run pytest tests/test_live.py   # live smoke test, skipped by default
uvx ruff check .                 # lint
```

- `tests/` covers the MCP tools, agent, CLI, research, news and files. Mocked search results and test files are in `tests/fixtures/`, and the fakes are in `meeting_prep/fakes.py`.
- `evals.py` runs `prep` for m_001, m_002 and m_003 and checks each brief for:
  - the required sections, in order;
  - attendee names and open items;
  - "no prior interactions on record", "no agenda on record" and "couldn't confirm identity";
  - under 700 words;
  - in mocked mode, that every URL came from the search results.

  It exits 1 and lists failed checks when any check fails. Checks that depend on fixture data are skipped with `--live`.
- The live smoke test writes a brief for m_005, an internal meeting, with real Gemini. It needs no Tavily key.

## Mock data

`data/*.json` holds 5 meetings, 10 people, 4 companies, 16 interactions and 6 open items. Dates are stored relative to today (`start_in_days`, `days_ago`, `due_in_days`) and resolved when a tool is called, so the meetings are always 1–5 days in the future.

## How a brief is made

1. `main.py` starts `mcp_server.py` over stdio and reads the meeting, every attendee's profile, history and open items through the MCP tools.
2. Each external company (one per email domain; `OWN_COMPANY_DOMAINS` skipped) gets 3 Tavily searches limited to the news window. Only items that name the company or its domain and carry a date inside the window are kept; there is no fallback to older news. Items from neither the company's own site, a press wire nor an established outlet, or framed as rumor ("reportedly", "in talks"…), are labeled unconfirmed.
3. Attendees flagged `first_meeting` (external, no history) and anyone passed with `--research` are searched on Tavily: 3 searches each, on name, company, role and email domain together. Only dated results that mention the name *and* the company or domain count; if none do, the brief says "couldn't confirm identity" and states nothing about the person. `TAVILY_API_KEY` is only needed when this step runs.
4. Files passed with `--files` (PDF, DOCX, TXT, MD; at most 10 and 20 MB total) are copied to `data/attachments/<meeting_id>/`, and every file in that folder is read on each run. Files that can't be read (corrupt, password-protected) are skipped and named in the brief. File text goes to Gemini inside `<materials>` tags, with any delimiter tags in it neutralised.
5. Gemini gets `skills/meeting-prep/SKILL.md` as its instructions plus those records (wrapped in `<internal_records>` tags and treated as data). Matching search results go in `<web_results>` tags as numbered ids (`S1`, `S2`…) with title, date and snippet but no URL. It may call the same read-only tools (up to 10 rounds), then returns purpose, likely asks, questions, risks background facts (each citing result ids) per news item, a summary, why it matters and a 0–3 relevance, and up to 3 key points per attached file, as JSON.
6. The program renders the Markdown: factual sections (agenda, who's in the room, history, open items) straight from the records, so history is never invented, and the judgment sections from Gemini's answer. News is ranked by relevance, then date (at most 5 per company; relevance 0 drops an item). News items and background facts become inline `[n]` references to a Sources list (title, link, date) built from the search results, so every URL in the brief came from Tavily; facts citing no valid result, or containing a URL, are dropped.

## Project layout

```
main.py                 CLI entry point (meeting_prep/cli.py)
web.py                  web page entry point (meeting_prep/web.py + meeting_prep/static/index.html)
mcp_server.py           FastMCP server: the five read-only tools over data/*.json
meeting_prep/
  agent.py              gathers context through MCP, prompts Gemini, parses its JSON
  brief.py              renders the Markdown brief
  research.py           first-time contact research and identity matching
  news.py               company news: targeting, date window, source labels
  materials.py          attached files: limits, copying, text extraction
  mydata.py             meetings you add on the web page (saved to data/mine/)
  web_search.py         Tavily client     llm.py   Gemini client and retries
  mcp_client.py         stdio MCP client  fakes.py scripted Gemini and fixture search
skills/meeting-prep/SKILL.md   brief format and sourcing rules, loaded into the system prompt
evals.py                eval script
data/                   mock records; data/attachments/ and output/ are gitignored
tests/                  pytest suite and fixtures
```

## Safety and limits

- The agent can only read. The MCP tools look up records, there are no tools to send, edit or delete anything, and web searches are run by code, not the model.
- Web results and file text are wrapped in `<web_results>` / `<materials>` tags and treated as data. Any of those tags inside the text are neutralised. A test file with an injection attempt (`tests/fixtures/files/injection.txt`) leaves the brief unchanged.
- The model never sees or writes URLs. Every link in a brief comes from a stored search result.
- Only the first 12,000 characters of each file are sent to Gemini, and the brief says so when a file is longer.

## Troubleshooting

- **"free daily request limit … is used up"**: Gemini's free tier allows about 20 requests a day per model, and a brief uses 1–3 of them. Try again tomorrow, or set `GEMINI_MODEL` to another model (e.g. `gemini-3.6-flash`).
- **503 "high demand"**: Gemini's free tier is busy. The client retries a few times; if it still fails, try later.
- **MCP Inspector shows 404**: clear the npm cache (`npm cache clean --force`) and rerun `uv run mcp dev mcp_server.py`.
- **"TAVILY_API_KEY is not set"**: any meeting with an external attendee searches company news. Add the key, or prep an internal meeting such as m_005.
