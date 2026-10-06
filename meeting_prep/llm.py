"""Gemini wrapper behind a small chat interface so tests can swap in a fake."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

DEFAULT_MODEL = "gemini-3.8-flash"
# Free-tier models are often busy, and each has its own small daily limit, so a request moves on to the
# next model instead of waiting. Override with GEMINI_FALLBACK_MODELS (comma-separated; empty = none).
# The "lite" models come last: weaker, but on separate quotas.
DEFAULT_FALLBACK_MODELS = ("gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-latest",
                           "gemini-3.5-flash-lite", "gemini-flash-lite-latest")
RETRY_DELAYS = (2, 4)  # per model, for "busy" errors, before moving on
BUSY = "busy"
OUT_OF_QUOTA = "out of free requests for today"
DEFAULT_TIMEOUT_SECONDS = 90


class MissingAPIKeyError(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


class ModelSwitchError(LLMError):
    """The model changed mid-conversation; the caller should start the conversation again."""


# Models whose daily free quota ran out, so later requests the same day skip them. The quota resets at
# midnight Pacific time; a fixed UTC-8 offset is close enough and needs no time-zone database.
_out_of_quota: dict[str, date] = {}


def _pacific_today() -> date:
    return datetime.now(timezone(timedelta(hours=-8))).date()


def model_chain(model: str | None = None) -> list[str]:
    first = model or os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL
    env = os.environ.get("GEMINI_FALLBACK_MODELS")
    rest = env.split(",") if env is not None else [DEFAULT_MODEL, *DEFAULT_FALLBACK_MODELS]
    return list(dict.fromkeys(m.strip() for m in [first, *rest] if m.strip()))


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
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 notice: Callable[[str], None] | None = None):
        if client is None:
            from google import genai
            from google.genai import types

            timeout = float(os.environ.get("GEMINI_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS)
            client = genai.Client(api_key=api_key or require_gemini_key(),
                                  http_options=types.HttpOptions(timeout=int(timeout * 1000)))
        self.client = client
        chain = model_chain(model)
        self.models = [m for m in chain if _out_of_quota.get(m) != _pacific_today()] or chain
        self.model = self.models[0]
        self.sleep = sleep
        self.notice = notice
        self.unavailable: list[str] = []

    def switch_model(self, reason: str) -> None:
        """Moves to the next model in the chain; raises LLMError when there is none left."""
        if reason == OUT_OF_QUOTA:
            _out_of_quota[self.model] = _pacific_today()
        self.unavailable.append(f"{self.model}: {reason}")
        index = self.models.index(self.model)
        if index + 1 >= len(self.models):
            raise LLMError("Every free Gemini model is busy or out of free requests right now ("
                           + "; ".join(self.unavailable) + "). Try again in a few minutes; "
                           "daily limits reset at midnight Pacific time.")
        old, self.model = self.model, self.models[index + 1]
        if self.notice:
            self.notice(f"Gemini {old} is {reason}; trying {self.model}")

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
        delays = iter(RETRY_DELAYS)
        while True:
            try:
                return await self.llm.client.aio.models.generate_content(
                    model=self.llm.model, contents=self.history, config=config)
            except Exception as exc:
                if _is_timeout(exc):
                    # Not retried: a stuck request may already count against the small free quota.
                    raise LLMError(f"Gemini didn't answer in time ({exc.__class__.__name__}); it may be busy. "
                                   "Try again in a few minutes.") from exc
                if _is_daily_quota(exc):
                    reason = OUT_OF_QUOTA
                elif _is_retryable(exc):
                    delay = next(delays, None)
                    if delay is not None:
                        await self.llm.sleep(delay)
                        continue
                    reason = BUSY
                else:
                    raise LLMError(f"Gemini request failed: {exc}") from exc
                self.llm.switch_model(reason)
                delays = iter(RETRY_DELAYS)
                if any(getattr(c, "role", None) == "model" for c in self.history):
                    # A conversation started on one model isn't continued on another: start over.
                    raise ModelSwitchError(f"Switched to {self.llm.model}; starting the brief again.") from exc
