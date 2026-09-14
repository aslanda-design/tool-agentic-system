"""Tests for the pluggable LLM client layer (ai/common/llm.py) — the piece
that makes the model/provider "parametrizable" (Ollama, Groq, or any other
OpenAI-compatible endpoint) without touching ai.common.agent_loop or any
agent. No real network/Ollama needed: OllamaChatClient is exercised only
through build_chat_client's construction (no network call happens until
.chat() is actually invoked); OpenAiCompatibleChatClient's HTTP call is
replaced with a fake, and its message/tool_call translation — the part
that can't be checked against a live Groq account — is tested directly."""

from __future__ import annotations

import json

import pytest
from ollama._types import Message as _OllamaMessage

from ai.common import llm


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.raised = False

    def raise_for_status(self) -> None:
        self.raised = True

    def json(self) -> dict:
        return self._payload


# --- _to_wire_messages: this project's id-less shape -> OpenAI's id-linked shape ---


def test_to_wire_messages_assigns_and_links_a_single_tool_call_id():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [{"name": "validate_listing", "arguments": {"symbol": "AAPL"}}]},
        {"role": "tool", "tool_name": "validate_listing", "content": '{"currency": "USD"}'},
    ]

    wire = llm._to_wire_messages(messages)

    assert wire[0] == {"role": "system", "content": "sys"}
    assistant = wire[2]
    assert assistant["role"] == "assistant"
    assert len(assistant["tool_calls"]) == 1
    call = assistant["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "validate_listing"
    assert json.loads(call["function"]["arguments"]) == {"symbol": "AAPL"}
    tool_message = wire[3]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == call["id"]
    assert tool_message["content"] == '{"currency": "USD"}'


def test_to_wire_messages_links_multiple_tool_calls_in_one_turn_positionally():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"name": "validate_listing", "arguments": {"symbol": "AAPL"}},
                {"name": "validate_listing", "arguments": {"symbol": "MSFT"}},
            ],
        },
        {"role": "tool", "tool_name": "validate_listing", "content": "AAPL result"},
        {"role": "tool", "tool_name": "validate_listing", "content": "MSFT result"},
    ]

    wire = llm._to_wire_messages(messages)

    ids = [c["id"] for c in wire[0]["tool_calls"]]
    assert len(set(ids)) == 2  # distinct ids even though both calls share a name
    assert wire[1]["tool_call_id"] == ids[0]
    assert wire[1]["content"] == "AAPL result"
    assert wire[2]["tool_call_id"] == ids[1]
    assert wire[2]["content"] == "MSFT result"


def test_to_wire_messages_ids_stay_unique_across_multiple_turns():
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"name": "a", "arguments": {}}]},
        {"role": "tool", "tool_name": "a", "content": "1"},
        {"role": "assistant", "content": "", "tool_calls": [{"name": "b", "arguments": {}}]},
        {"role": "tool", "tool_name": "b", "content": "2"},
    ]

    wire = llm._to_wire_messages(messages)

    first_id = wire[0]["tool_calls"][0]["id"]
    second_id = wire[2]["tool_calls"][0]["id"]
    assert first_id != second_id
    assert wire[1]["tool_call_id"] == first_id
    assert wire[3]["tool_call_id"] == second_id


def test_to_wire_messages_passes_through_plain_text_turns():
    messages = [{"role": "user", "content": "You must finish by calling one of: flag_for_review."}]

    wire = llm._to_wire_messages(messages)

    assert wire == [{"role": "user", "content": "You must finish by calling one of: flag_for_review."}]


# --- _from_wire_response: OpenAI's response shape -> this project's shape ---


def test_from_wire_response_parses_tool_calls_and_token_usage():
    data = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"id": "call_0", "type": "function", "function": {"name": "flag_for_review", "arguments": '{"reason": "ambiguous"}'}}
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 123, "completion_tokens": 45},
    }

    result = llm._from_wire_response(data)

    assert result["content"] == ""
    assert result["tool_calls"] == [{"name": "flag_for_review", "arguments": {"reason": "ambiguous"}}]
    assert result["prompt_tokens"] == 123
    assert result["completion_tokens"] == 45


def test_from_wire_response_handles_a_plain_text_reply_with_no_tool_calls():
    data = {"choices": [{"message": {"content": "thinking out loud"}}], "usage": {}}

    result = llm._from_wire_response(data)

    assert result["content"] == "thinking out loud"
    assert result["tool_calls"] == []
    assert result["prompt_tokens"] == 0
    assert result["completion_tokens"] == 0


# --- _to_ollama_messages: this project's flat shape -> Ollama SDK's own
# pydantic Message model. Regression coverage for a real bug: a fake/mock
# chat client can't catch this because it never exercises the `ollama`
# package's own validation — only a real model that actually calls a tool
# and continues the conversation did (qwen3:8b; mistral:latest never
# called a tool successfully, so it never round-tripped this path). These
# tests validate the translated output against the real ollama._types
# .Message model, not a guess at its shape. ---


def test_to_ollama_messages_wraps_tool_calls_under_a_function_key():
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"name": "lookup_isin", "arguments": {"isin": "ES0144580Y14"}}],
        },
        {"role": "tool", "tool_name": "lookup_isin", "content": "[]"},
    ]

    converted = llm._to_ollama_messages(messages)

    assert converted[0] == {"role": "user", "content": "hi"}
    assistant = converted[1]
    assert assistant["tool_calls"] == [{"function": {"name": "lookup_isin", "arguments": {"isin": "ES0144580Y14"}}}]
    assert converted[2] == {"role": "tool", "tool_name": "lookup_isin", "content": "[]"}
    # The actual failure mode this regresses: ollama's SDK pydantic-validates
    # every outbound message against its own Message model before sending —
    # the untranslated flat shape raises here (missing required "function").
    for message in converted:
        _OllamaMessage.model_validate(message)


def test_to_ollama_messages_leaves_messages_without_tool_calls_untouched():
    messages = [{"role": "system", "content": "sys"}, {"role": "assistant", "content": "no tools here"}]

    converted = llm._to_ollama_messages(messages)

    assert converted == messages
    for message in converted:
        _OllamaMessage.model_validate(message)


def test_to_ollama_messages_handles_multiple_tool_calls_in_one_turn():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"name": "validate_listing", "arguments": {"symbol": "AAPL"}},
                {"name": "validate_listing", "arguments": {"symbol": "MSFT"}},
            ],
        }
    ]

    converted = llm._to_ollama_messages(messages)

    assert len(converted[0]["tool_calls"]) == 2
    assert converted[0]["tool_calls"][0]["function"]["name"] == "validate_listing"
    _OllamaMessage.model_validate(converted[0])


# --- OpenAiCompatibleChatClient: the HTTP call itself ---


def test_openai_compatible_client_posts_the_expected_payload_and_headers(monkeypatch):
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse({"choices": [{"message": {"content": "ok"}}], "usage": {}})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    client = llm.OpenAiCompatibleChatClient(model="llama-3.3-70b-versatile", base_url="https://api.groq.com/openai/v1/", api_key="secret")

    result = client.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "x"}}])

    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"  # trailing slash on base_url stripped
    assert captured["json"]["model"] == "llama-3.3-70b-versatile"
    assert captured["json"]["tools"] == [{"type": "function", "function": {"name": "x"}}]
    assert captured["headers"] == {"Authorization": "Bearer secret"}
    assert result["content"] == "ok"


def test_openai_compatible_client_omits_auth_header_without_an_api_key(monkeypatch):
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["headers"] = headers
        return _FakeResponse({"choices": [{"message": {"content": "ok"}}], "usage": {}})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    client = llm.OpenAiCompatibleChatClient(model="local-model", base_url="http://localhost:8000/v1")

    client.chat([{"role": "user", "content": "hi"}], tools=[])

    assert captured["headers"] == {}


# --- build_chat_client: the one place a provider/model gets selected ---


def test_build_chat_client_defaults_to_settings_and_ollama():
    client = llm.build_chat_client()

    assert isinstance(client, llm.OllamaChatClient)


def test_build_chat_client_overrides_take_precedence_over_settings():
    client = llm.build_chat_client(provider="openai", model="llama-3.3-70b-versatile", base_url="https://api.groq.com/openai/v1", api_key="k")

    assert isinstance(client, llm.OpenAiCompatibleChatClient)
    assert client._model == "llama-3.3-70b-versatile"
    assert client._base_url == "https://api.groq.com/openai/v1"
    assert client._api_key == "k"


def test_build_chat_client_rejects_an_unknown_provider():
    with pytest.raises(ValueError, match="AGENT_PROVIDER"):
        llm.build_chat_client(provider="anthropic")
