"""Local web page: pick a meeting, drop in files, get the brief.

    uv run web.py      # then open http://127.0.0.1:8000

It runs the same pipeline as `main.py prep` (cli.run_prep) and only listens on this computer by default.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from dotenv import load_dotenv
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from markdown_it import MarkdownIt

from .agent import AgentError
from .cli import ROOT, run_prep
from .llm import LLM, GeminiLLM, LLMError, MissingAPIKeyError, require_gemini_key
from .materials import (
    MAX_FILES,
    MAX_TOTAL_BYTES,
    SUPPORTED,
    AttachmentError,
    stored,
    validate,
)
from .mcp_client import ToolCallError, connect
from .mydata import DataError, add_meeting, company_names, delete_meeting, is_mine
from .news import DEFAULT_NEWS_DAYS
from .web_search import SearchError, WebSearch

STATIC = Path(__file__).resolve().parent / "static"
LIST_DAYS = 365
# Raw HTML in the brief (model or web text) is escaped, and javascript: links are refused.
_markdown = MarkdownIt("commonmark", {"html": False})


def _safe_name(filename: str | None) -> str:
    """Just the file's own name: browsers may send folders, and '../' must not escape the meeting folder."""
    name = Path((filename or "").replace("\\", "/")).name.strip()
    return "" if name.startswith(".") else name


async def _tools(calls: Callable[[Any], Any]) -> Any:
    # Errors are re-raised outside the MCP context so they don't arrive wrapped in an ExceptionGroup.
    error: ToolCallError | None = None
    async with connect() as tools:
        try:
            return await calls(tools)
        except ToolCallError as exc:
            error = exc
    raise HTTPException(404, str(error))


def create_app(*, llm_factory: Callable[[], LLM] | None = None,
               searcher_factory: Callable[[], WebSearch] | None = None, output_dir: Path | None = None) -> FastAPI:
    """llm_factory / searcher_factory default to real Gemini / Tavily; tests pass fakes."""
    load_dotenv(ROOT / ".env")
    app = FastAPI(title="Meeting prep", docs_url=None, redoc_url=None, openapi_url=None)
    out = output_dir or ROOT / "output"

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/meetings")
    async def meetings() -> dict[str, Any]:
        async def fetch(tools):
            upcoming = await tools.call_ok("list_upcoming_meetings", {"days_ahead": LIST_DAYS})
            return [await tools.call_ok("get_meeting", {"meeting_id": m["id"]}) for m in upcoming]

        return {
            "meetings": [{
                "id": m["id"], "title": m["title"], "start": m["start"], "end": m["end"],
                "attendees": [{"name": a["name"], "company": a["company"], "is_internal": a["is_internal"]}
                              for a in m["attendees"]],
                "files": [p.name for p in stored(m["id"])],
                "mine": is_mine(m["id"]),
            } for m in await _tools(fetch)],
            "companies": company_names(),
            "limits": {"max_files": MAX_FILES, "max_total_bytes": MAX_TOTAL_BYTES, "types": list(SUPPORTED),
                       "default_news_days": DEFAULT_NEWS_DAYS},
        }

    @app.post("/api/meetings")
    def create_meeting(form: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        try:
            return add_meeting(form)
        except DataError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/meetings/{meeting_id}")
    def remove_meeting(meeting_id: str) -> dict[str, str]:
        try:
            delete_meeting(meeting_id)
        except DataError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"deleted": meeting_id}

    @app.post("/api/prep")
    async def prep(meeting_id: Annotated[str, Form()], news_days: Annotated[int, Form()] = DEFAULT_NEWS_DAYS,
                   files: Annotated[list[UploadFile] | None, File()] = None) -> dict[str, Any]:
        if news_days < 1:
            raise HTTPException(400, "The news window must be 1 day or more.")
        with tempfile.TemporaryDirectory() as tmp:
            paths: list[Path] = []
            total = 0
            for upload in files or []:
                name = _safe_name(upload.filename)
                if not name:
                    if upload.filename:
                        raise HTTPException(400, f"Can't use the file name {upload.filename!r}; rename the file.")
                    continue  # an empty file field
                path = Path(tmp) / name
                if path in paths:
                    raise HTTPException(400, f"Two files are named {name}; rename one of them.")
                with path.open("wb") as f:
                    shutil.copyfileobj(upload.file, f)
                total += path.stat().st_size
                if total > MAX_TOTAL_BYTES:
                    raise HTTPException(400, f"The files total more than {MAX_TOTAL_BYTES // 1024 // 1024} MB; "
                                             "attach fewer or smaller files.")
                paths.append(path)
            try:
                validate(paths)
                llm = llm_factory() if llm_factory else GeminiLLM(api_key=require_gemini_key())
                try:
                    brief = await run_prep(meeting_id, llm=llm, output_dir=out, news_days=news_days, files=paths,
                                           searcher=searcher_factory() if searcher_factory else None)
                finally:
                    aclose = getattr(llm, "aclose", None)
                    if aclose is not None:
                        await aclose()
            except (AttachmentError, MissingAPIKeyError) as exc:
                raise HTTPException(400, str(exc)) from exc
            except ToolCallError as exc:
                raise HTTPException(404, str(exc)) from exc
            except (AgentError, LLMError, SearchError) as exc:
                raise HTTPException(502, str(exc)) from exc
        markdown = brief.read_text(encoding="utf-8")
        resolved_id = brief.stem.removeprefix("prep_")
        return {"meeting_id": resolved_id, "filename": brief.name, "markdown": markdown,
                "html": _markdown.render(markdown), "files": [p.name for p in stored(resolved_id)]}

    @app.delete("/api/meetings/{meeting_id}/files/{name}")
    async def remove_file(meeting_id: str, name: str) -> dict[str, Any]:
        meeting = await _tools(lambda tools: tools.call_ok("get_meeting", {"meeting_id": meeting_id}))
        match = [p for p in stored(meeting["id"]) if p.name == name]
        if not match:
            raise HTTPException(404, f"No attached file named {name} for {meeting['id']}.")
        match[0].unlink()
        return {"files": [p.name for p in stored(meeting["id"])]}

    return app


def main() -> None:
    import uvicorn

    load_dotenv(ROOT / ".env")
    host = os.environ.get("MEETING_PREP_HOST", "127.0.0.1")
    port = int(os.environ.get("MEETING_PREP_PORT", "8000"))
    if not os.environ.get("OWN_COMPANY_DOMAINS", "").strip():
        print("warning: OWN_COMPANY_DOMAINS isn't set, so your colleagues count as external and your own "
              "company's news is searched. Add OWN_COMPANY_DOMAINS=... to .env (see .env.example).")
    print(f"Meeting prep is running at http://{host}:{port}  (press Ctrl+C to stop)")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
