import asyncio

import pytest
from google.genai import errors, types

from meeting_prep.llm import (
    GeminiLLM,
    LLMError,
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


def test_daily_quota_fails_fast_with_clear_message():
    daily = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED",
        "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})
    sleeps = []
    llm, _ = make([daily], sleeps)
    with pytest.raises(LLMError, match="daily request limit for gemini-test"):
        asyncio.run(llm.start_chat("s", TOOLS).send("hi"))
    assert sleeps == []


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
