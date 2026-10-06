"""Spawns mcp_server.py over stdio and exposes its tools to the agent."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_PATH = Path(__file__).resolve().parent.parent / "mcp_server.py"


class ToolCallError(RuntimeError):
    pass


def _server_env() -> dict[str, str]:
    # The server needs config like OWN_COMPANY_DOMAINS, but never API keys.
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env.setdefault("FASTMCP_LOG_LEVEL", "WARNING")
    return env


def _decode(result) -> Any:
    if result.structuredContent is not None:
        data = result.structuredContent
        return data["result"] if set(data) == {"result"} else data
    text = "".join(getattr(c, "text", "") for c in result.content)
    try:
        return json.loads(text)
    except ValueError:
        return text


class MCPTools:
    def __init__(self, session: ClientSession, tools: list[dict]):
        self.session = session
        self.tools = tools
        self.names = {t["name"] for t in tools}

    async def call(self, name: str, args: dict[str, Any]) -> tuple[Any, bool]:
        """Returns (data, is_error). Unknown tools come back as errors, not exceptions."""
        if name not in self.names:
            return f"Unknown tool '{name}'. Available tools: {', '.join(sorted(self.names))}.", True
        result = await self.session.call_tool(name, args)
        if result.isError:
            return "".join(getattr(c, "text", "") for c in result.content), True
        return _decode(result), False

    async def call_ok(self, name: str, args: dict[str, Any]) -> Any:
        data, is_error = await self.call(name, args)
        if is_error:
            raise ToolCallError(data)
        return data


@asynccontextmanager
async def connect(server_path: Path = SERVER_PATH) -> AsyncIterator[MCPTools]:
    params = StdioServerParameters(command=sys.executable, args=[str(server_path)], env=_server_env())
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listing = await session.list_tools()
        tools = [
            {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
            for t in listing.tools
        ]
        yield MCPTools(session, tools)
