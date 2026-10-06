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

### Live Gemini smoke test (session 2 follow-up)

- `GEMINI_API_KEY` is saved as a Devin user secret. The key is valid (listing models and small prompts both work).
- The first live `prep --meeting-id m_001` runs failed. `gemini-3.8-flash` and `gemini-3.6-flash` both returned repeated `503 UNAVAILABLE` ("high demand") on full-size requests, even after our 4 retries. Small prompts sometimes got through.
- **Free-tier quota is 20 requests/day per model** (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`). One brief uses at least 2 requests, and the retries count too. Probing burned the 3.8-flash allowance for the day.
- Fix: a daily-quota 429 now fails fast with a plain message suggesting another `GEMINI_MODEL` instead of retrying (`tests/test_llm.py::test_daily_quota_fails_fast_with_clear_message`).
- Still open: one successful live brief. Retry when Gemini demand is lower, preferably as a single run (no parallel probes).

## Session 3 — first-time contact research with links (done)

**Checkpoint:** research criteria pass with mocked search (`tests/test_agent.py`, "first-time contact research" block). A known contact triggers no search (m_001: 0 calls), Tom Becker (m_002) gets 3 searches and a cited background, John Smith (m_003) gets "couldn't confirm identity" with no claims, and every URL in the brief is one of the mocked results. `uv run pytest` passes 85 tests; ruff is clean.

### Files added / changed

- `meeting_prep/web_search.py`: `TavilySearch.search(query, max_results, topic)` → `[SearchResult(url, title, content, published_date)]`. Plain `httpx` POST to `https://api.tavily.com/search` with `include_published_date: true` (dates normalized to `YYYY-MM-DD`). 401/429/432/433 map to plain `SearchError` messages. `TAVILY_API_KEY` is checked only on the first search (`MissingAPIKeyError`, exit 2).
- `meeting_prep/research.py`: `targets_for(context, requests)` (deterministic: `first_meeting` profiles, plus `--research "Name, Company"`), `queries()` (3 per person, cap 5), `is_match()` and `research()`.
- `agent.py`: a `<web_results>` block in the user prompt and a `background` key in the output contract. The `research` entries are left out of `<internal_records>`.
- `brief.py`: section 4 sits after "Who's in the room", with inline `[n]` refs and a trailing `## Sources`. An automatic risk line is added for each unconfirmed identity. Also fixed a stray ". ." when tenure is null.
- `cli.py`: `--research` (repeatable). `main(..., searcher=)` and `run_prep(..., searcher=, research_requests=)` for injection.
- `tests/fixtures/search/{tom_becker,john_smith}.json`: Tavily-shaped responses. `FakeSearch` serves a fixture when its name (file stem) is in the query, otherwise `[]`.

### Design decisions

- **Searches are run by code, not offered to Gemini as a function.** The spec's stack table says "exposed to Gemini as a function". I didn't do that, so that the trigger stays deterministic, a known contact can never be searched, and the 3–5 cap holds. Session 4 can reuse `TavilySearch` the same way.
- **Identity check is code.** A result counts only if it contains the full name *and* the company name, domain or domain root (e.g. `harbourviewhealth`). Role isn't required.
- **The model never sees URLs.** It gets result ids, titles, dates and snippets (500 chars). The renderer maps the ids it cites back to the URLs. Claims citing an unknown id, or another person's id, or containing a URL, are dropped.
- **Undated results are never cited**, because the spec requires a date on every researched claim. The LinkedIn fixture is undated on purpose. If everything matching is undated, the brief says "nothing stated (matching public results carried no publication date)". Tavily's dates are "best estimate of published or last updated" (beta).
- The professional-info-only rule and "own words, no pasted text" are prompt rules (in `OUTPUT_CONTRACT`), not enforced in code.

### For session 4 (company developments)

- `TavilySearch.search` has `topic` already; add a date window (`start_date` + `filter_by_published_date`, or `time_range`) for the 90-day default and `--news-days`.
- One search per unique external domain: `context["external_domains"]` is already deduped and excludes `OWN_COMPANY_DOMAINS`.
- Source ids are numbered across the whole brief in `research()`; company news needs to continue that numbering (or move numbering into the renderer) so `## Sources` stays one list.
- Live Tavily check (2026-10-06, `TAVILY_API_KEY` is now a saved Devin secret): `research()` on Tom Becker / Harbourview Health came back `unconfirmed`. 14 real results, all for other Tom Beckers. That's expected, because the seed people are fictional. A manual target, Lisa Su / AMD, came back `confirmed` with 4 dated, citable results. 3 credits per person. Gemini was not part of this check.

## Session 4 — company developments (done)

**Checkpoint:** the recency window, deduplication, ranking and "unconfirmed" criteria pass with mocked search (`tests/test_agent.py`, "company developments" block):
- m_001: Harbourview's 3 in-window items, ranked, each with a date and a [n] link.
- m_003: Solvane's only item is from 2025-08-01, so the brief says "No notable developments found in the last 90 days."
- Two attendees from the same company get one search pass (m_001, m_004).
- No query ever names Lumora, and m_005 makes zero searches.
- m_004: Kestrel's rumor item and its weak-source item are both labeled `_(unconfirmed)_`, with a risk line for each.

### Files added / changed

- `meeting_prep/news.py`:
  - `companies_for(context)`: one `Company(name, domain)` per `context["external_domains"]`, so it's already deduped and excludes own domains.
  - `queries()`: 3 per company (cap 5). The first is `topic=general` with the domain, for newsroom/primary pages; two are `topic=news` (deals/earnings/launches, leadership/layoffs/legal).
  - `company_news(searcher, companies, days=, today=)`.
  - `today()` honours `MEETING_PREP_TODAY`, like the MCP server.
- `web_search.py`: `search(..., start_date=)` sends `start_date` + `filter_by_published_date: true`.
- `agent.py`: `<web_results>` is now `{"people": [...], "companies": [...]}`, and each key appears only when non-empty. Company items carry `id` (`N1`…), title, date, `source_type` and a snippet with URLs replaced by `[link removed]`. The output contract adds `"news": {"N1": {"summary", "why", "relevance" 0–3, "unconfirmed"}}`.
- `brief.py`: `## Company snapshot` goes right after "Meeting at a glance" and is omitted when there are no external companies. It adds a risk line per unconfirmed item, and `## Sources` now covers news and background (snapshot cites come first, so they're numbered first).
- `cli.py`: `--news-days N` (whole number ≥ 1, default 90). `TAVILY_API_KEY` is now needed for every meeting with an external attendee (m_005 runs without it).
- `tests/fixtures/news/<domain>.json`: Tavily-shaped results plus a `"company"` key. `FakeSearch` serves them for searches that pass `start_date`, and person fixtures for the rest. `FakeSearch.person_calls` / `.news_calls` split the two.

### Design decisions

- **The window and dating are enforced in code** as well as by Tavily's filter. An item is kept only if it names the company or its domain, and its date falls between today − N days and today. Undated items are dropped.
- **Ranking:** Gemini gives each item a relevance score from 0 to 3 (0 drops it). Code sorts by relevance, then newest first, and keeps 5. An item the model says nothing about defaults to relevance 1, with its title as the summary. The spec's section list says "newest first" while the feature section says "ranked by relevance and then recency". I followed the latter, since it's the more specific rule and is what the acceptance test checks.
- **"Unconfirmed" is set in code** (the model can add it, never remove it). It applies when the source is neither the company's own domain, a press wire or filing (`PRIMARY_HOSTS`), nor an outlet in `ESTABLISHED_HOSTS`, or when the text is rumor-framed (`RUMOR_RE`). This approximates "single weak source": there is no cross-source corroboration check, and the outlet list is hand-picked, so expect more unconfirmed labels on small trade sites.
- Deduplication of news is by URL. The same story on two URLs shows twice.
- Skipped: the optional per-company daily cache in `data/cache/`.

### For session 5 (file ingestion)

- The renderer omits a section when it has no content, and `## From your materials` goes between "History and open items" and "Likely asks".
- Reuse the pattern: wrap file text in its own tag (e.g. `<materials>`), and add it to the "data, never instructions" line in `OUTPUT_CONTRACT`.
- Live Tavily check (2026-10-06, 90-day window): Harbourview Health (fictional) returned 9 results and kept none, so the brief would say "No notable developments". AMD (amd.com, real) returned 25 and kept 8 dated in-window items, including AMD's own IR press release (primary), Reuters and CNBC (established), and Yahoo Finance, YouTube and a filings aggregator (labeled unconfirmed, since those hosts aren't on `ESTABLISHED_HOSTS`). Credit use is 3 per company.

## Session 5 — file ingestion and the injection test (done)

**Checkpoint:** every file-upload criterion passes with a fake LLM (`tests/test_materials.py`):
- PDF, DOCX (tables included), TXT and MD are each read, and their points appear under "From your materials", each tagged `_(file: \`name\`)_`.
- An unsupported type, a missing file, more than 10 files and more than 20 MB each exit 2 with a clear message. Nothing is copied, and Gemini is never called.
- `tests/fixtures/files/injection.txt` (instructions, a phishing link, and a fake `</materials><internal_records>` breakout) leaves the brief byte-identical apart from its own "From your materials" line.

### Files added / changed

- `meeting_prep/materials.py`:
  - `validate(paths)`: exists, type in `SUPPORTED`, `MAX_FILES` = 10, `MAX_TOTAL_BYTES` = 20 MB. It runs in `main()` before the Gemini key check.
  - `stage(paths, meeting_id, root)` copies into `data/attachments/<meeting_id>/` and returns every supported file there, so re-runs reuse earlier files. Limits apply to the folder plus the new files, and are checked before copying.
  - `load()` numbers files `F1..` in name order.
  - `read()` returns `status` ok/unreadable with a `reason`. An encrypted PDF gives "password-protected". A bad DOCX gives "corrupt or password-protected" (encrypted .docx files aren't zip packages, so they can't be told apart). Empty text gives "no readable text".
  - The root comes from `MEETING_PREP_ATTACHMENTS_DIR`. `tests/conftest.py` points it at `tmp_path`, so tests never touch `data/attachments/`.
- `cli.py`: `--files FILE [FILE ...]` (`action="extend"`, so it can be repeated). Staging happens after `get_meeting`, so an unknown meeting id copies nothing. It works with `--next`.
- `agent.py`: `<materials>` block (JSON list of `{id, file, text}` for readable files). `_defang()` replaces any `<internal_records>`, `<web_results>` or `<materials>` tag inside file text and web snippets with `[tag removed]`, so untrusted text can't close its block. Contract adds `"materials": {"F1": ["point", ...]}` and says file instructions are content.
- `brief.py`: `_materials()` sits between "History and open items" and "Likely asks" and is omitted with no files. It keeps up to 3 points per file and about 9 in total (`MATERIAL_POINT_BUDGET // n_files`, min 1). Ids not on file are ignored, and points containing URLs are dropped. It lists unreadable files as skipped, and says "no key points for this meeting" when the model gives none.

### Design decisions

- **Long files:** each file is cut to the first 12,000 characters (`MAX_CHARS`) of text sent to Gemini, to stay inside free-tier limits. The brief says so ("Only the first 12,000 characters of `x` were read."), so it is never silent. The 20 MB limit is about upload size and is rejected outright.
- **The flag is `--files`** (the spec's name), not `--attach`, which I used when discussing it with Shin.
- **Web upload page:** Shin wants one as an extra step after session 6. It should reuse `validate`/`stage`/`load` unchanged.
- **Live Gemini check: not done (2026-10-06).** `gemini-3.8-flash` had used up its free daily quota, and `gemini-3.6-flash` returned 503 "high demand". Next session, try one real run of m_001 with `--files tests/fixtures/files/{renewal_deck.pdf,call_notes.docx,injection.txt}`, and check that the injection text doesn't appear as a point.

## Session 6 — evals, README, cleanup (done)

**Checkpoint:** `uv run evals.py` passes 3/3 (mocked). `uv run pytest` passes (130, plus 1 live test skipped by default). `uvx ruff check .` is clean. The README covers setup, commands, tests, evals, layout, safety and troubleshooting.

### Files added / changed

- `evals.py` (repo root, so `uv run evals.py` can import `meeting_prep`): three `Scenario`s, `normal` (m_001), `new_contact` (m_002) and `no_agenda` (m_003). Each brief is checked for:
  - the core sections, in order;
  - `must_include` / `must_not_include` strings;
  - under 700 words;
  - in mocked mode, that every URL came from the fixtures;
  - that no API key appears in it.

  `mocked_only` strings depend on the fixtures and are skipped with `--live`. Mocked mode pins `MEETING_PREP_TODAY=2026-01-15` (fixture dates). Both modes use an empty temp attachments folder and default `OWN_COMPANY_DOMAINS` to the seed domain. Exit 1 lists the failed checks. `--scenario NAME` runs one. Briefs go to `output/evals/`.
- `meeting_prep/fakes.py`: `ScriptedLLM`, `json_reply` and `FixtureSearch`, moved out of `tests/test_agent.py` so the eval script can use them. The tests alias them as `FakeLLM` / `FakeSearch`.
- `tests/test_evals.py`: the eval passes when mocked, covers the three scenarios, and reports missing/forbidden strings and stray URLs.
- `tests/test_live.py`: one real Gemini brief for m_005 (internal, so no Tavily). It is skipped unless `MEETING_PREP_LIVE=1`.
- `brief.py`: one-page guard (see below). `agent.py`: the contract now gives word limits per field.
- `llm.py`: the daily-quota message suggests a *different* model (it used to suggest gemini-3.6-flash even when that was the exhausted one). `GeminiLLM.aclose()` is new, and `cli._run_and_close` calls it inside the event loop, which removed an "Event loop is closed" traceback printed after every real run.
- Lint cleanup: `mcp_server._today()` uses a timezone-aware now, and nested `async with` blocks were combined.

### Design decisions

- **One-page guard.** The first real Gemini brief (m_001, gemini-3.6-flash) came out at 848 words, over the spec's 700, because of long relationship lines and long `based_on` text. Now:
  - model text is clipped with "…" (`SENTENCE_WORDS` 25, `ITEM_WORDS` 20, `RELATIONSHIP_WORDS` 12, `BASIS_WORDS` 8);
  - if the brief is still at or over `WORD_LIMIT`, `render_brief` drops model-written list items in `TRIM_ORDER` (risks → 2, asks → 2, questions → 3, then risks/asks → 1).

  Records are never trimmed, so a brief whose factual parts alone exceed 700 words (lots of news and files) can still go over.
- **Live eval misread own company.** The first live run had no `.env`, so Lumora counted as external and its news was searched. The eval now sets the seed domain when `OWN_COMPANY_DOMAINS` is unset.

### Live results (2026-10-06)

- `uv run evals.py --live` with `GEMINI_MODEL=gemini-3.7-flash`: `new_contact` (592 words) and `no_agenda` (456 words) **pass** with real Gemini and Tavily. Tom Becker and John Smith are both "couldn't confirm identity", which is correct since the seed people are fictional. `normal` got 503 "high demand" twice and was not retried further, to save quota.
- gemini-3.8-flash and gemini-3.6-flash used up their free daily quota today. The models list also has gemini-3.7-flash, gemini-3.5-flash and gemini-flash-latest, each with its own quota.
- Still to try: a live m_001 after the one-page guard, and m_001 with `--files tests/fixtures/files/{renewal_deck.pdf,call_notes.docx,injection.txt}`.

## Web upload page (after session 6, done)

`uv run web.py` serves a single page at http://127.0.0.1:8000: pick a meeting, drop files, **Prepare brief**, then read, download or copy the brief.

### Files added / changed

- `meeting_prep/web.py`: FastAPI app (`create_app(llm_factory=, searcher_factory=, output_dir=)`; tests pass fakes). Routes: `GET /` page, `GET /api/meetings` (next 30 days, with attendees, already-attached files and limits), `POST /api/prep` (multipart `meeting_id`, `news_days`, `files`), `DELETE /api/meetings/{id}/files/{name}`.
- `meeting_prep/static/index.html`: plain HTML/CSS/JS with no build step. Checks file type, count and size in the browser for instant feedback; the server checks again.
- `web.py` (entry point), `materials.stored()` (public list of a meeting's attached files), `tests/test_web.py`.
- New deps: fastapi, uvicorn, python-multipart, markdown-it-py.

### Design decisions

- Uploads are written to a temp folder under just their own file name (any `../` is stripped), checked with `materials.validate`, then passed to `cli.run_prep(files=...)`. So staging, reuse and limits work exactly as with `--files`.
- Errors map to status codes: bad file or missing key → 400, unknown meeting → 404, Gemini/Tavily failure → 502. The page shows the message as-is.
- The brief is turned into HTML on the server with markdown-it in `commonmark` mode with `html: False`, so raw HTML from model, web or file text is escaped and `javascript:` links are refused. Links open in a new tab.
- Listens on 127.0.0.1 only (no login). `web.main` warns when `OWN_COMPANY_DOMAINS` isn't set, since otherwise colleagues show as external.
- Gemini requests now have a timeout (`GEMINI_TIMEOUT_SECONDS`, default 90). A timeout isn't retried, since it may already count against the free quota, and instead fails with "Gemini didn't answer in time". Before this, one stuck request left the page spinning for more than 5 minutes. The page also shows elapsed time and gives up after 6 minutes.
