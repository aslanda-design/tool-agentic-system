"""LLM client abstraction — the only place an agent talks to a model. Two
backends ship here: Ollama's native /api/chat (OllamaChatClient) and any
OpenAI-compatible /chat/completions endpoint (OpenAiCompatibleChatClient —
covers Groq, OpenAI itself, Together, Fireworks, a local vLLM/llama.cpp
server, ...). build_chat_client() picks one from settings.agent_*, so
switching models or providers is a config change (AGENT_PROVIDER,
AGENT_MODEL, AGENT_BASE_URL, AGENT_API_KEY — see backend/.env.example),
never a code change. Not imported by anything under app/ (see
backend/ai/AGENTS.md rule 1); AGENT_ENABLED=false skips this entirely."""

from __future__ import annotations

import json
from typing import Any, Protocol

import httpx
from ollama import Client as _OllamaSdkClient

from app.config import settings


class ChatClient(Protocol):
    """What ai.common.agent_loop.run_agent needs from a model backend —
    everything else (which provider, which model, auth) is the client's
    own concern, decided once at construction (see build_chat_client)."""

    def chat(self, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        """One tool-calling turn. Synchronous and blocking (a plain HTTP
        call) — run_agent calls it directly from its async loop rather than
        via a thread pool, same as this project's Phase 6 code always has;
        safe because SecurityResolverAgent.run() drives its own dedicated
        event loop (asyncio.run), never one shared with other async work.

        Args:
            messages: The running conversation so far, in the shape
                ai.common.agent_loop builds it: {"role", "content"} plus
                "tool_calls": [{"name","arguments"}] on an assistant turn
                that called tools, and "tool_name" on the "tool" role
                messages that follow it (one per call, same order). Every
                concrete ChatClient translates this shape to whatever its
                own wire format needs.
            tools: Tool schemas in OpenAI/Groq function-calling shape (see
                ai.common.mcp_client.McpToolSession.tool_schemas).

        Returns:
            {"content": str, "tool_calls": [{"name","arguments"}, ...],
            "prompt_tokens": int, "completion_tokens": int}.
        """
        ...


class OllamaChatClient:
    """Ollama's native /api/chat, via the `ollama` package. Ollama has no
    tool_call_id concept (unlike OpenAiCompatibleChatClient, no id/
    tool_call_id translation is needed), but its SDK still pydantic-
    validates outbound messages against its own Message model, whose
    tool_calls entries require a nested {"function": {"name","arguments"}}
    — not this project's flat {"name","arguments"} — so _to_ollama_messages
    still does one small translation. (Caught by an actual qwen3:8b run
    that called a tool and continued the conversation — a fake/mock chat
    client in a unit test can't reproduce this, since it never exercises
    the ollama package's own pydantic validation.)"""

    def __init__(self, model: str, base_url: str, num_ctx: int = 8192, temperature: float = 0) -> None:
        self._model = model
        self._client = _OllamaSdkClient(host=base_url)
        self._num_ctx = num_ctx
        self._temperature = temperature

    def chat(self, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        response = self._client.chat(
            model=self._model,
            messages=_to_ollama_messages(messages),
            tools=tools,
            options={"temperature": self._temperature, "num_ctx": self._num_ctx},
        )
        tool_calls = [
            {"name": call.function.name, "arguments": dict(call.function.arguments or {})}
            for call in (response.message.tool_calls or [])
        ]
        return {
            "content": response.message.content or "",
            "tool_calls": tool_calls,
            "prompt_tokens": response.prompt_eval_count or 0,
            "completion_tokens": response.eval_count or 0,
        }


def _to_ollama_messages(messages: list[dict]) -> list[dict]:
    """Wrap each assistant tool_calls entry's {"name","arguments"} under a
    "function" key, matching ollama._types.Message.ToolCall's required
    shape — everything else (including "tool_name" on tool-result
    messages, which Ollama's Message model accepts directly) passes
    through unchanged."""
    converted = []
    for message in messages:
        if message.get("tool_calls"):
            converted.append(
                {
                    **message,
                    "tool_calls": [
                        {"function": {"name": call["name"], "arguments": call["arguments"]}}
                        for call in message["tool_calls"]
                    ],
                }
            )
        else:
            converted.append(message)
    return converted


class OpenAiCompatibleChatClient:
    """Any OpenAI-compatible /chat/completions endpoint: Groq
    (base_url="https://api.groq.com/openai/v1"), OpenAI itself, Together,
    Fireworks, a local vLLM/llama.cpp server exposing the same API, etc.
    Plain httpx (already a dependency) rather than the `openai` package —
    one POST doesn't need a whole SDK.

    Unlike Ollama, this wire format links each tool result back to the
    tool_call that produced it via `tool_call_id`, and expects
    `arguments` as a JSON *string*, not an object. This project's internal
    message shape (built by ai.common.agent_loop, shared with
    OllamaChatClient) carries neither, so _to_wire_messages/_from_wire_response
    translate in both directions — see their docstrings.
    """

    def __init__(self, model: str, base_url: str, api_key: str = "", temperature: float = 0) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._temperature = temperature

    def chat(self, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": _to_wire_messages(messages),
            "temperature": self._temperature,
        }
        if tools:
            payload["tools"] = tools
        response = httpx.post(f"{self._base_url}/chat/completions", json=payload, headers=headers, timeout=120)
        response.raise_for_status()
        return _from_wire_response(response.json())


def _to_wire_messages(messages: list[dict]) -> list[dict]:
    """Translate this project's Ollama-flavored, id-less message history
    into OpenAI's wire shape. An assistant turn's tool_calls need a
    generated `id`; each "tool" role message that follows needs the
    matching `tool_call_id`. ai.common.agent_loop always emits exactly one
    "tool" message per tool_calls entry, immediately after the assistant
    turn, in the same order — so ids are assigned deterministically from
    position, no name-matching required."""
    wire: list[dict] = []
    pending_ids: list[str] = []
    next_id = 0
    for message in messages:
        role = message["role"]
        if role == "assistant" and message.get("tool_calls"):
            ids = [f"call_{next_id + i}" for i in range(len(message["tool_calls"]))]
            next_id += len(ids)
            wire.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
                        }
                        for call_id, call in zip(ids, message["tool_calls"], strict=True)
                    ],
                }
            )
            pending_ids = list(ids)
        elif role == "tool":
            call_id = pending_ids.pop(0) if pending_ids else None
            wire.append({"role": "tool", "tool_call_id": call_id, "content": message.get("content", "")})
        else:
            wire.append({"role": role, "content": message.get("content", "")})
    return wire


def _from_wire_response(data: dict) -> dict[str, Any]:
    """The reverse of _to_wire_messages, for one response: OpenAI's
    tool_calls carry `arguments` as a JSON string and an `id` this project
    doesn't need (it re-derives ids from position on the next turn)."""
    message = data["choices"][0]["message"]
    tool_calls = [
        {"name": call["function"]["name"], "arguments": json.loads(call["function"]["arguments"] or "{}")}
        for call in (message.get("tool_calls") or [])
    ]
    usage = data.get("usage") or {}
    return {
        "content": message.get("content") or "",
        "tool_calls": tool_calls,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
    }


def build_chat_client(
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> ChatClient:
    """Build the chat client settings.agent_* configure by default. Pass
    overrides to sweep models/providers without touching .env — see
    ai/agents/security_resolver/evaluation/evaluate.py's --model/--provider."""
    provider = provider or settings.agent_provider
    model = model or settings.agent_model
    base_url = base_url or settings.agent_base_url
    if provider == "ollama":
        return OllamaChatClient(model=model, base_url=base_url, num_ctx=settings.agent_num_ctx)
    if provider == "openai":
        return OpenAiCompatibleChatClient(model=model, base_url=base_url, api_key=api_key or settings.agent_api_key)
    raise ValueError(f"Unknown AGENT_PROVIDER {provider!r} (expected 'ollama' or 'openai')")
