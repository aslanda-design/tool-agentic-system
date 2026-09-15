"""Tests for the generic agent loop (ai/common/agent_loop.py) — Phase 6 of
plans/agentic_asset_mapping.md. No real model backend needed: a fake
ChatClient returns canned replies in sequence, matching the shape any real
ChatClient.chat() returns (see ai/common/llm.py). Provider-agnostic by
design — this proves the loop itself doesn't care whether a real
ChatClient talks to Ollama, Groq, or anything else."""

from __future__ import annotations

import asyncio

from ai.common.agent_loop import run_agent

TOOLS = [
    {"type": "function", "function": {"name": "save_security_mapping", "description": "", "parameters": {}}},
    {"type": "function", "function": {"name": "flag_for_review", "description": "", "parameters": {}}},
    {"type": "function", "function": {"name": "validate_listing", "description": "", "parameters": {}}},
]
TERMINAL = {"save_security_mapping": "SAVED", "flag_for_review": "FLAGGED"}


def _reply(content="", tool_calls=None, prompt_tokens=10, completion_tokens=5):
    return {
        "content": content,
        "tool_calls": tool_calls or [],
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


class _FakeChatClient:
    """A ChatClient (see ai.common.llm.ChatClient) that returns canned
    replies in sequence, ignoring the actual messages/tools it's given."""

    def __init__(self, replies: list[dict]) -> None:
        self._replies = iter(replies)

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        return next(self._replies)


async def _ok_call_tool(name, arguments):
    return {"ok": True}


def test_terminates_on_successful_terminal_tool():
    chat_client = _FakeChatClient(
        [_reply(tool_calls=[{"name": "save_security_mapping", "arguments": {"resolution_id": 1}}])]
    )

    result = asyncio.run(
        run_agent("system", "user", TOOLS, _ok_call_tool, chat_client, max_steps=5, terminal_tools=TERMINAL)
    )

    assert result.status == "SAVED"
    assert result.steps == 1
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["ok"] is True
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5


def test_does_not_terminate_when_the_terminal_tool_call_fails():
    chat_client = _FakeChatClient(
        [
            _reply(tool_calls=[{"name": "save_security_mapping", "arguments": {}}]),
            _reply(tool_calls=[{"name": "flag_for_review", "arguments": {}}]),
        ]
    )

    async def failing_then_ok(name, arguments):
        if name == "save_security_mapping":
            return {"error": "candidate has no recent price"}
        return {"ok": True}

    result = asyncio.run(
        run_agent("system", "user", TOOLS, failing_then_ok, chat_client, max_steps=5, terminal_tools=TERMINAL)
    )

    assert result.status == "FLAGGED"
    assert result.steps == 2
    assert result.tool_calls[0]["ok"] is False
    assert result.tool_calls[0]["error"] == "candidate has no recent price"


def test_nudges_once_then_errors_on_a_second_text_only_reply():
    chat_client = _FakeChatClient([_reply(content="thinking..."), _reply(content="still thinking...")])

    result = asyncio.run(
        run_agent("system", "user", TOOLS, _ok_call_tool, chat_client, max_steps=5, terminal_tools=TERMINAL)
    )

    assert result.status == "ERROR"
    assert result.steps == 2
    assert "terminal tool" in result.error


def test_recovers_after_the_nudge_if_the_model_then_calls_a_terminal_tool():
    chat_client = _FakeChatClient(
        [_reply(content="thinking..."), _reply(tool_calls=[{"name": "flag_for_review", "arguments": {}}])]
    )

    result = asyncio.run(
        run_agent("system", "user", TOOLS, _ok_call_tool, chat_client, max_steps=5, terminal_tools=TERMINAL)
    )

    assert result.status == "FLAGGED"
    assert result.steps == 2


def test_max_steps_when_nothing_terminates_the_run():
    replies = [_reply(tool_calls=[{"name": "validate_listing", "arguments": {"symbol": "AAPL"}}])] * 3
    chat_client = _FakeChatClient(replies)

    result = asyncio.run(
        run_agent("system", "user", TOOLS, _ok_call_tool, chat_client, max_steps=3, terminal_tools=TERMINAL)
    )

    assert result.status == "MAX_STEPS"
    assert result.steps == 3
    assert len(result.tool_calls) == 3


def test_unknown_tool_call_returns_an_error_without_crashing():
    chat_client = _FakeChatClient(
        [
            _reply(tool_calls=[{"name": "delete_everything", "arguments": {}}]),
            _reply(tool_calls=[{"name": "flag_for_review", "arguments": {}}]),
        ]
    )

    result = asyncio.run(
        run_agent("system", "user", TOOLS, _ok_call_tool, chat_client, max_steps=5, terminal_tools=TERMINAL)
    )

    assert result.status == "FLAGGED"
    assert result.tool_calls[0]["name"] == "delete_everything"
    assert result.tool_calls[0]["ok"] is False
    assert "unknown tool" in result.tool_calls[0]["error"]


# --- conversational mode (terminal_tools=None) — portfolio_assistant ---


def test_conversational_mode_ends_on_the_first_text_only_reply_no_nudge():
    chat_client = _FakeChatClient([_reply(content="Your portfolio is worth €15,744.")])

    result = asyncio.run(
        run_agent("system", "user", TOOLS, _ok_call_tool, chat_client, max_steps=5, terminal_tools=None)
    )

    assert result.status == "REPLIED"
    assert result.steps == 1
    assert result.final_message == "Your portfolio is worth €15,744."
    assert result.error is None


def test_conversational_mode_still_calls_tools_before_replying():
    chat_client = _FakeChatClient(
        [
            _reply(tool_calls=[{"name": "validate_listing", "arguments": {"symbol": "AAPL"}}]),
            _reply(content="AAPL is currently priced at $230.50."),
        ]
    )

    result = asyncio.run(
        run_agent("system", "user", TOOLS, _ok_call_tool, chat_client, max_steps=5, terminal_tools=None)
    )

    assert result.status == "REPLIED"
    assert result.steps == 2
    assert len(result.tool_calls) == 1
    assert result.final_message == "AAPL is currently priced at $230.50."


def test_conversational_mode_still_respects_max_steps():
    replies = [_reply(tool_calls=[{"name": "validate_listing", "arguments": {}}])] * 3
    chat_client = _FakeChatClient(replies)

    result = asyncio.run(
        run_agent("system", "user", TOOLS, _ok_call_tool, chat_client, max_steps=3, terminal_tools=None)
    )

    assert result.status == "MAX_STEPS"


def test_security_resolver_style_call_is_unaffected_by_the_new_default(monkeypatch):
    """Regression: terminal_tools now defaults to None, but every existing
    agent passes it explicitly as a dict — confirm the nudge-then-ERROR
    behavior (not the new conversational ending) still applies when it does."""
    chat_client = _FakeChatClient([_reply(content="thinking..."), _reply(content="still thinking...")])

    result = asyncio.run(
        run_agent("system", "user", TOOLS, _ok_call_tool, chat_client, max_steps=5, terminal_tools=TERMINAL)
    )

    assert result.status == "ERROR"  # not "REPLIED" — terminal_tools was given, so the nudge path still applies


# --- history (session memory) — portfolio_assistant ---


def test_history_is_replayed_before_the_new_user_message():
    captured_messages: list[list[dict]] = []

    class _CapturingChatClient:
        def chat(self, messages, tools):
            captured_messages.append([dict(m) for m in messages])
            return _reply(content="ok")

    history = [
        {"role": "user", "content": "what's my total value?"},
        {"role": "assistant", "content": "€15,744."},
    ]

    asyncio.run(
        run_agent(
            "system", "and my P&L?", TOOLS, _ok_call_tool, _CapturingChatClient(),
            max_steps=5, terminal_tools=None, history=history,
        )
    )

    first_call = captured_messages[0]
    assert first_call[0] == {"role": "system", "content": "system"}
    assert first_call[1] == history[0]
    assert first_call[2] == history[1]
    assert first_call[3] == {"role": "user", "content": "and my P&L?"}


def test_no_history_behaves_exactly_like_before():
    chat_client = _FakeChatClient([_reply(content="ok")])

    result = asyncio.run(
        run_agent("system", "user", TOOLS, _ok_call_tool, chat_client, max_steps=5, terminal_tools=None, history=None)
    )

    assert result.status == "REPLIED"
