"""Transport-level tests for the real provider adapters.

No network and no API key: each SDK is given a fake HTTP transport that checks
the outgoing request and replies with a canned, correctly shaped response.
This verifies request building, structured parsing, streaming and error mapping.
"""

from __future__ import annotations

import json

import httpx2
import pytest

from story_agent.llm.anthropic_llm import FALLBACK_BETA, AnthropicLLM
from story_agent.llm.base import LLMError
from story_agent.llm.openai_llm import OpenAILLM
from story_agent.schemas import IntentReading

DECISION = {
    "intent_type": "action",
    "interpreted_intent": "Open the door",
    "player_action": "I open the door slowly.",
    "action": "advance",
    "rationale": "Clear in-story action.",
    "clarifying_question": "",
    "clarifying_options": [],
    "revision_scope": "none",
    "revision_instructions": "",
    "plot_change_request": "",
}


def sse(events: list[tuple[str | None, dict | str]]) -> bytes:
    out = []
    for name, data in events:
        payload = data if isinstance(data, str) else json.dumps(data)
        out.append((f"event: {name}\n" if name else "") + f"data: {payload}\n\n")
    return "".join(out).encode()


# --------------------------------------------------------------------------- Anthropic


def anthropic_message(text: str, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


@pytest.fixture()
def anthropic_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


def test_anthropic_structured_request_and_parse(anthropic_env):
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta", "")
        return httpx2.Response(200, json=anthropic_message(json.dumps(DECISION)))

    llm = AnthropicLLM("claude-opus-5-5", {"intent_interpreter": "low"}, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    decision = llm.structured(role="intent_interpreter", system="sys", prompt="hi", schema=IntentReading)

    assert decision.action == "advance"
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default" and FALLBACK_BETA in seen["beta"]
    assert "thinking" not in body and "temperature" not in body


def test_anthropic_streams_text(anthropic_env):
    events = [
        ("message_start", {"type": "message_start", "message": {**anthropic_message(""), "content": [], "stop_reason": None}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "The door "}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "creaks."}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 4}}),
        ("message_stop", {"type": "message_stop"}),
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx2.Response(200, content=sse(events), headers={"content-type": "text/event-stream"})

    llm = AnthropicLLM("claude-opus-5-5", {}, refusal_fallback=False,
                       http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    chunks: list[str] = []
    text = llm.text(role="narrator", system="sys", prompt="go", on_delta=chunks.append)
    assert text == "The door creaks." and chunks == ["The door ", "creaks."]


def test_anthropic_refusal_becomes_llm_error(anthropic_env):
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=anthropic_message("", stop_reason="refusal"))

    llm = AnthropicLLM("claude-opus-5-5", {}, refusal_fallback=False,
                       http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    with pytest.raises(LLMError, match="declined"):
        llm.structured(role="intent_interpreter", system="s", prompt="p", schema=IntentReading)


# --------------------------------------------------------------------------- OpenAI


@pytest.fixture()
def openai_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


def chat_completion(content: str) -> dict:
    return {
        "id": "c1", "object": "chat.completion", "created": 0, "model": "gpt-4.1",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content, "refusal": None}}],
    }


def test_openai_structured_uses_strict_schema(openai_env):
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json=chat_completion(json.dumps(DECISION)))

    llm = OpenAILLM("gpt-4.1", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    decision = llm.structured(role="intent_interpreter", system="s", prompt="p", schema=IntentReading)
    assert decision.player_action == "I open the door slowly."
    fmt = seen["body"]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True


def test_openai_falls_back_to_json_mode_for_local_servers(openai_env):
    calls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        calls.append(body["response_format"]["type"])
        if body["response_format"]["type"] == "json_schema":
            return httpx2.Response(400, json={"error": {"message": "json_schema not supported", "type": "invalid_request_error"}})
        if len(calls) == 2:
            return httpx2.Response(200, json=chat_completion('{"action": "advance"}'))  # incomplete: triggers repair
        return httpx2.Response(200, json=chat_completion(json.dumps(DECISION)))

    llm = OpenAILLM("llama3", base_url="http://localhost:11434/v1",
                    http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    decision = llm.structured(role="intent_interpreter", system="s", prompt="p", schema=IntentReading)
    assert decision.action == "advance"
    assert calls == ["json_schema", "json_object", "json_object"]


def test_openai_streams_text(openai_env):
    def chunk(text):
        return (None, {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "gpt-4.1",
                       "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]})

    body = sse([chunk("Snow "), chunk("falls."), (None, "[DONE]")])

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    llm = OpenAILLM("gpt-4.1", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    chunks: list[str] = []
    assert llm.text(role="narrator", system="s", prompt="p", on_delta=chunks.append) == "Snow falls."
    assert chunks == ["Snow ", "falls."]


def test_openai_quota_error_becomes_llm_error(openai_env):
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429, json={"error": {"message": "no credits", "type": "insufficient_quota"}})

    llm = OpenAILLM("gpt-4.1", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    llm.client = llm.client.with_options(max_retries=0)
    with pytest.raises(LLMError, match="429"):
        llm.structured(role="intent_interpreter", system="s", prompt="p", schema=IntentReading)
