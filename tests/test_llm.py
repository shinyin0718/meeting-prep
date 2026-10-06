import asyncio

import httpx
import pytest
from google.genai import errors, types

from meeting_prep.llm import (
    GeminiLLM,
    LLMError,
    ModelSwitchError,
    ToolCall,
    ToolResult,
    function_declarations,
)

TOOLS = [{"name": "get_meeting", "description": "Look up a meeting.",
          "inputSchema": {"type": "object", "properties": {"meeting_id": {"type": "string"}},
                          "required": ["meeting_id"]}}]


def response(*parts):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=list(parts)))])


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    async def generate_content(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": list(contents), "config": config})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, outcomes):
        self.aio = type("Aio", (), {})()
        self.aio.models = FakeModels(outcomes)


def make(outcomes, sleeps=None):
    async def sleep(seconds):
        if sleeps is not None:
            sleeps.append(seconds)

    client = FakeClient(outcomes)
    return GeminiLLM(model="gemini-test", client=client, sleep=sleep), client.aio.models


def test_mcp_tools_become_function_declarations():
    (decl,) = function_declarations(TOOLS)
    assert decl.name == "get_meeting"
    assert decl.parameters_json_schema["required"] == ["meeting_id"]


def test_function_call_round_trip_keeps_model_turn_and_call_id():
    call = types.Part(function_call=types.FunctionCall(id="c1", name="get_meeting", args={"meeting_id": "m_001"}),
                      thought_signature=b"sig")
    llm, models = make([response(call), response(types.Part(text='{"ok": true}'))])
    chat = llm.start_chat("system text", TOOLS)

    reply = asyncio.run(chat.send("hello"))
    assert reply.tool_calls == [ToolCall("get_meeting", {"meeting_id": "m_001"}, id="c1")]
    cfg = models.requests[0]["config"]
    assert cfg.system_instruction == "system text"
    assert cfg.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.AUTO

    reply = asyncio.run(chat.send([ToolResult(reply.tool_calls[0], {"id": "m_001"})]))
    assert reply.text == '{"ok": true}' and reply.tool_calls == []
    sent = models.requests[1]["contents"]
    assert sent[1].parts[0].thought_signature == b"sig"
    fr = sent[2].parts[0].function_response
    assert (fr.id, fr.name, fr.response) == ("c1", "get_meeting", {"result": {"id": "m_001"}})


def test_tool_errors_are_sent_as_error_responses():
    llm, models = make([response(types.Part(text="{}"))])
    chat = llm.start_chat("s", TOOLS)
    asyncio.run(chat.send([ToolResult(ToolCall("get_meeting", {}, id="x"), "No meeting", is_error=True)]))
    assert models.requests[0]["contents"][0].parts[0].function_response.response == {"error": "No meeting"}


def test_tools_can_be_disabled_for_final_answer():
    llm, models = make([response(types.Part(text="{}"))])
    asyncio.run(llm.start_chat("s", TOOLS).send("answer now", allow_tools=False))
    mode = models.requests[0]["config"].tool_config.function_calling_config.mode
    assert mode == types.FunctionCallingConfigMode.NONE


def test_thought_parts_are_not_returned_as_text():
    llm, _ = make([response(types.Part(text="thinking...", thought=True), types.Part(text="{}"))])
    assert asyncio.run(llm.start_chat("s", TOOLS).send("hi")).text == "{}"


def test_transient_errors_are_retried_with_backoff():
    busy = errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
    limited = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    sleeps = []
    llm, _ = make([busy, limited, response(types.Part(text="{}"))], sleeps)
    assert asyncio.run(llm.start_chat("s", TOOLS).send("hi")).text == "{}"
    assert sleeps == [2, 4]


def test_daily_quota_with_no_other_model_fails_fast_with_clear_message(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "")
    daily = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED",
        "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})
    sleeps = []
    llm, _ = make([daily], sleeps)
    with pytest.raises(LLMError, match="Every free Gemini model") as err:
        asyncio.run(llm.start_chat("s", TOOLS).send("hi"))
    assert sleeps == []
    assert "gemini-test: out of free requests for today" in str(err.value)


def test_non_retryable_error_raises_llm_error():
    bad = errors.ClientError(400, {"error": {"code": 400, "message": "bad request", "status": "INVALID_ARGUMENT"}})
    llm, _ = make([bad])
    with pytest.raises(LLMError, match="bad request"):
        asyncio.run(llm.start_chat("s", TOOLS).send("hi"))


def test_model_defaults_and_env_override(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert GeminiLLM(client=object()).model == "gemini-3.8-flash"
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")
    assert GeminiLLM(client=object()).model == "gemini-3.6-flash"


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("slow"), TimeoutError()])
def test_timeouts_fail_fast_with_clear_message(exc):
    sleeps = []
    llm, models = make([exc], sleeps)
    with pytest.raises(LLMError, match="didn't answer in time"):
        asyncio.run(llm.start_chat("s", TOOLS).send("hi"))
    assert sleeps == [] and len(models.requests) == 1


def test_real_client_gets_a_request_timeout(monkeypatch):
    from google import genai

    captured = {}
    monkeypatch.setattr(genai, "Client", lambda **kw: captured.update(kw) or object())
    GeminiLLM(api_key="k")
    assert captured["http_options"].timeout == 90_000
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "30")
    GeminiLLM(api_key="k")
    assert captured["http_options"].timeout == 30_000


def busy():
    return errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})


def daily():
    return errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED",
        "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})


async def no_sleep(_seconds):
    return None


def test_model_chain_defaults_and_override(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_FALLBACK_MODELS", raising=False)
    assert GeminiLLM(client=object()).models == ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
                                                 "gemini-3.5-flash", "gemini-flash-latest",
                                                 "gemini-3.5-flash-lite", "gemini-flash-lite-latest"]
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", " a , gemini-3.6-flash,,b")
    assert GeminiLLM(client=object()).models == ["gemini-3.6-flash", "a", "b"]


def test_busy_model_switches_to_the_next_one(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "backup-a,backup-b")
    sleeps, notices = [], []

    async def sleep(seconds):
        sleeps.append(seconds)

    client = FakeClient([busy(), busy(), busy(), response(types.Part(text="{}"))])
    llm = GeminiLLM(model="gemini-test", client=client, sleep=sleep, notice=notices.append)
    assert asyncio.run(llm.start_chat("s", TOOLS).send("hi")).text == "{}"
    assert [r["model"] for r in client.aio.models.requests] == ["gemini-test"] * 3 + ["backup-a"]
    assert sleeps == [2, 4] and notices == ["Gemini gemini-test is busy; trying backup-a"]


def test_daily_quota_switches_at_once_and_is_skipped_for_the_rest_of_the_day(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "backup-a")
    client = FakeClient([daily(), response(types.Part(text="{}"))])
    llm = GeminiLLM(model="gemini-test", client=client, sleep=no_sleep)
    assert asyncio.run(llm.start_chat("s", TOOLS).send("hi")).text == "{}"
    assert [r["model"] for r in client.aio.models.requests] == ["gemini-test", "backup-a"]
    assert GeminiLLM(model="gemini-test", client=object()).model == "backup-a"


def test_every_model_busy_gives_one_clear_error(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "backup-a")
    llm = GeminiLLM(model="gemini-test", client=FakeClient([daily(), busy(), busy(), busy()]), sleep=no_sleep)
    with pytest.raises(LLMError, match="gemini-test: out of free requests for today; backup-a: busy"):
        asyncio.run(llm.start_chat("s", TOOLS).send("hi"))


def test_switch_mid_conversation_asks_the_caller_to_start_again(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "backup-a")
    llm, _ = make([response(types.Part(text="first")), daily()])
    chat = llm.start_chat("s", TOOLS)
    asyncio.run(chat.send("hi"))
    with pytest.raises(ModelSwitchError):
        asyncio.run(chat.send("again"))
    assert llm.model == "backup-a"
