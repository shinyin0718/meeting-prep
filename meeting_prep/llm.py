"""Gemini wrapper behind a small chat interface so tests can swap in a fake."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_MODEL = "gemini-3.8-flash"
FALLBACK_MODEL = "gemini-3.6-flash"
RETRY_DELAYS = (2, 4, 8, 16)
DEFAULT_TIMEOUT_SECONDS = 90


class MissingAPIKeyError(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str | None = None


@dataclass
class ToolResult:
    call: ToolCall
    result: Any
    is_error: bool = False


@dataclass
class LLMReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class Chat(Protocol):
    async def send(self, message: str | list[ToolResult], *, allow_tools: bool = True) -> LLMReply: ...


class LLM(Protocol):
    def start_chat(self, system: str, tools: list[dict]) -> Chat: ...


def require_gemini_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise MissingAPIKeyError(
            "GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey, "
            "then add GEMINI_API_KEY=... to .env (see .env.example)."
        )
    return key


def _is_retryable(exc: Exception) -> bool:
    from google.genai import errors

    if isinstance(exc, errors.ServerError):
        return True
    return isinstance(exc, errors.ClientError) and exc.code == 429 and not _is_daily_quota(exc)


def _is_timeout(exc: Exception) -> bool:
    import httpx

    return isinstance(exc, (TimeoutError, httpx.TimeoutException))


def _is_daily_quota(exc: Exception) -> bool:
    """Per-day quota 429s won't clear within any sensible backoff, so retrying only wastes requests."""
    details = (getattr(exc, "details", None) or {}).get("error", {}).get("details", [])
    return any(
        "PerDay" in v.get("quotaId", "")
        for d in details if isinstance(d, dict)
        for v in d.get("violations", []) if isinstance(v, dict)
    )


class GeminiLLM:
    def __init__(self, api_key: str | None = None, model: str | None = None, client: Any = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        if client is None:
            from google import genai
            from google.genai import types

            timeout = float(os.environ.get("GEMINI_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS)
            client = genai.Client(api_key=api_key or require_gemini_key(),
                                  http_options=types.HttpOptions(timeout=int(timeout * 1000)))
        self.client = client
        self.model = model or os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL
        self.sleep = sleep

    def start_chat(self, system: str, tools: list[dict]) -> GeminiChat:
        return GeminiChat(self, system, tools)

    async def aclose(self) -> None:
        aio = getattr(self.client, "aio", None)
        if hasattr(aio, "aclose"):
            await aio.aclose()


def function_declarations(tools: list[dict]) -> list:
    """MCP tool listings ({name, description, inputSchema}) -> Gemini declarations."""
    from google.genai import types

    return [
        types.FunctionDeclaration(
            name=t["name"], description=t.get("description") or "",
            parameters_json_schema=t.get("inputSchema") or {"type": "object", "properties": {}},
        )
        for t in tools
    ]


class GeminiChat:
    def __init__(self, llm: GeminiLLM, system: str, tools: list[dict]):
        self.llm = llm
        self.system = system
        self.declarations = function_declarations(tools)
        self.history: list = []

    def _config(self, allow_tools: bool):
        from google.genai import types

        cfg: dict[str, Any] = {"system_instruction": self.system}
        if self.declarations:
            cfg["tools"] = [types.Tool(function_declarations=self.declarations)]
            mode = types.FunctionCallingConfigMode.AUTO if allow_tools else types.FunctionCallingConfigMode.NONE
            cfg["tool_config"] = types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode=mode))
            cfg["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
        return types.GenerateContentConfig(**cfg)

    @staticmethod
    def _user_content(message: str | list[ToolResult]):
        from google.genai import types

        if isinstance(message, str):
            return types.Content(role="user", parts=[types.Part(text=message)])
        parts = [
            types.Part(function_response=types.FunctionResponse(
                id=r.call.id, name=r.call.name,
                response={"error": r.result} if r.is_error else {"result": r.result},
            ))
            for r in message
        ]
        return types.Content(role="user", parts=parts)

    async def send(self, message: str | list[ToolResult], *, allow_tools: bool = True) -> LLMReply:
        self.history.append(self._user_content(message))
        response = await self._generate(self._config(allow_tools))
        candidates = response.candidates or []
        if not candidates or candidates[0].content is None:
            raise LLMError(f"Gemini returned no content (finish reason: {getattr(candidates[0], 'finish_reason', None) if candidates else 'none'}).")
        content = candidates[0].content
        # Appending the model's own content keeps thought signatures intact for the next turn.
        self.history.append(content)
        text = "".join(p.text for p in content.parts or [] if p.text and not p.thought)
        calls = [
            ToolCall(name=p.function_call.name, args=dict(p.function_call.args or {}), id=p.function_call.id)
            for p in content.parts or [] if p.function_call
        ]
        return LLMReply(text=text, tool_calls=calls if allow_tools else [])

    async def _generate(self, config):
        for attempt, delay in enumerate((*RETRY_DELAYS, None)):
            try:
                return await self.llm.client.aio.models.generate_content(
                    model=self.llm.model, contents=self.history, config=config)
            except Exception as exc:
                if _is_daily_quota(exc):
                    other = FALLBACK_MODEL if self.llm.model == DEFAULT_MODEL else DEFAULT_MODEL
                    raise LLMError(
                        f"Gemini's free daily request limit for {self.llm.model} is used up. Try again tomorrow, "
                        f"or set GEMINI_MODEL to another model (e.g. {other}); each model has its own limit."
                    ) from exc
                if _is_timeout(exc):
                    # Not retried: a stuck request may already count against the small free quota.
                    raise LLMError(f"Gemini didn't answer in time ({exc.__class__.__name__}); it may be busy. "
                                   "Try again in a few minutes.") from exc
                if delay is None or not _is_retryable(exc):
                    raise LLMError(f"Gemini request failed: {exc}") from exc
                await self.llm.sleep(delay)
