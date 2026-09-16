"""Tests for PortfolioAssistantAgent (Phase 8f of
plans/agentic_asset_mapping_phase7_8.md) — session memory (history loaded
and replayed), session history (transcript persisted, auto-titled), and
the same fallback-on-failure discipline as every other agent. No Ollama or
real MCP subprocess needed: open_mcp_servers and run_agent are replaced
with fakes/monkeypatches."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from ai.agents.portfolio_assistant import agent as agent_module
from ai.common.agent_loop import AgentRunResult
from tests.fakes import FakeChatRepo, FakeResolutionRepo


class _FakeSession:
    async def call_tool(self, name, arguments):
        return {}

    async def tool_schemas(self):
        return [{"type": "function", "function": {"name": "get_portfolio_summary", "description": "", "parameters": {}}}]


def _patch_mcp_servers(monkeypatch) -> None:
    @asynccontextmanager
    async def fake_open_mcp_servers(servers):
        yield _FakeSession()

    monkeypatch.setattr(agent_module, "open_mcp_servers", fake_open_mcp_servers)


def _patch_repos(monkeypatch, chat_repo: FakeChatRepo, resolution_repo: FakeResolutionRepo) -> None:
    monkeypatch.setattr(agent_module, "chat_repo", lambda db: chat_repo)
    monkeypatch.setattr(agent_module, "resolution_repo", lambda db: resolution_repo)


def _fake_run_agent(result: AgentRunResult, capture: list | None = None):
    async def fake(**kwargs):
        if capture is not None:
            capture.append(kwargs)
        return result

    return fake


def test_send_message_persists_user_message_and_reply(monkeypatch):
    chat_repo, resolution_repo = FakeChatRepo(), FakeResolutionRepo()
    session_id = chat_repo.create_session()
    _patch_mcp_servers(monkeypatch)
    _patch_repos(monkeypatch, chat_repo, resolution_repo)
    monkeypatch.setattr(
        agent_module, "run_agent", _fake_run_agent(AgentRunResult(status="REPLIED", steps=1, final_message="€15,744."))
    )

    result = agent_module.PortfolioAssistantAgent().send_message(session_id, "what's my total value?")

    assert result.status == "REPLIED"
    messages = chat_repo.list_messages(session_id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "what's my total value?"
    assert messages[1].content == "€15,744."


def test_first_turn_auto_titles_the_session(monkeypatch):
    chat_repo, resolution_repo = FakeChatRepo(), FakeResolutionRepo()
    session_id = chat_repo.create_session()  # title == ""
    _patch_mcp_servers(monkeypatch)
    _patch_repos(monkeypatch, chat_repo, resolution_repo)
    monkeypatch.setattr(agent_module, "run_agent", _fake_run_agent(AgentRunResult(status="REPLIED", steps=1, final_message="ok")))

    agent_module.PortfolioAssistantAgent().send_message(session_id, "what's my currency exposure?")

    assert chat_repo.get_session(session_id).title == "what's my currency exposure?"


def test_second_turn_does_not_rename_the_session(monkeypatch):
    chat_repo, resolution_repo = FakeChatRepo(), FakeResolutionRepo()
    session_id = chat_repo.create_session()
    chat_repo.add_message(session_id, "user", "first question")
    chat_repo.add_message(session_id, "assistant", "first answer")
    chat_repo.rename_session(session_id, "first question")
    _patch_mcp_servers(monkeypatch)
    _patch_repos(monkeypatch, chat_repo, resolution_repo)
    monkeypatch.setattr(agent_module, "run_agent", _fake_run_agent(AgentRunResult(status="REPLIED", steps=1, final_message="ok")))

    agent_module.PortfolioAssistantAgent().send_message(session_id, "a completely different second question")

    assert chat_repo.get_session(session_id).title == "first question"


def test_session_memory_is_loaded_and_passed_to_run_agent(monkeypatch):
    chat_repo, resolution_repo = FakeChatRepo(), FakeResolutionRepo()
    session_id = chat_repo.create_session()
    chat_repo.add_message(session_id, "user", "what's my total value?")
    chat_repo.add_message(session_id, "assistant", "€15,744.")
    _patch_mcp_servers(monkeypatch)
    _patch_repos(monkeypatch, chat_repo, resolution_repo)
    captured: list[dict] = []
    monkeypatch.setattr(
        agent_module, "run_agent", _fake_run_agent(AgentRunResult(status="REPLIED", steps=1, final_message="ok"), captured)
    )

    agent_module.PortfolioAssistantAgent().send_message(session_id, "and my P&L?")

    assert captured[0]["history"] == [
        {"role": "user", "content": "what's my total value?"},
        {"role": "assistant", "content": "€15,744."},
    ]
    assert captured[0]["user_message"] == "and my P&L?"
    assert captured[0]["terminal_tools"] is None  # conversational mode


def test_fresh_session_has_no_history(monkeypatch):
    chat_repo, resolution_repo = FakeChatRepo(), FakeResolutionRepo()
    session_id = chat_repo.create_session()
    _patch_mcp_servers(monkeypatch)
    _patch_repos(monkeypatch, chat_repo, resolution_repo)
    captured: list[dict] = []
    monkeypatch.setattr(
        agent_module, "run_agent", _fake_run_agent(AgentRunResult(status="REPLIED", steps=1, final_message="ok"), captured)
    )

    agent_module.PortfolioAssistantAgent().send_message(session_id, "hi")

    assert captured[0]["history"] == []


def test_records_an_audit_trail_row_with_no_resolution_id(monkeypatch):
    chat_repo, resolution_repo = FakeChatRepo(), FakeResolutionRepo()
    session_id = chat_repo.create_session()
    _patch_mcp_servers(monkeypatch)
    _patch_repos(monkeypatch, chat_repo, resolution_repo)
    monkeypatch.setattr(agent_module, "run_agent", _fake_run_agent(AgentRunResult(status="REPLIED", steps=1, final_message="ok")))

    agent_module.PortfolioAssistantAgent().send_message(session_id, "hi")

    assert len(resolution_repo.agent_runs) == 1
    assert resolution_repo.agent_runs[0].agent == "portfolio_assistant"
    assert resolution_repo.agent_runs[0].resolution_id is None


def test_writes_a_fallback_reply_on_max_steps(monkeypatch):
    chat_repo, resolution_repo = FakeChatRepo(), FakeResolutionRepo()
    session_id = chat_repo.create_session()
    _patch_mcp_servers(monkeypatch)
    _patch_repos(monkeypatch, chat_repo, resolution_repo)
    monkeypatch.setattr(agent_module, "run_agent", _fake_run_agent(AgentRunResult(status="MAX_STEPS", steps=8)))

    result = agent_module.PortfolioAssistantAgent().send_message(session_id, "a very complicated question")

    assert result.status == "MAX_STEPS"
    messages = chat_repo.list_messages(session_id)
    assert messages[1].role == "assistant"
    assert "try" in messages[1].content.lower() or "sorry" in messages[1].content.lower()


def test_writes_a_fallback_reply_on_timeout(monkeypatch):
    chat_repo, resolution_repo = FakeChatRepo(), FakeResolutionRepo()
    session_id = chat_repo.create_session()
    _patch_mcp_servers(monkeypatch)
    _patch_repos(monkeypatch, chat_repo, resolution_repo)

    async def hangs_forever(**kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(agent_module, "run_agent", hangs_forever)
    monkeypatch.setattr(agent_module.settings, "agent_timeout_seconds", 0.05)

    result = agent_module.PortfolioAssistantAgent().send_message(session_id, "hi")

    assert result.status == "TIMEOUT"
    assert len(chat_repo.list_messages(session_id)) == 2


def test_writes_a_fallback_reply_on_unexpected_error(monkeypatch):
    chat_repo, resolution_repo = FakeChatRepo(), FakeResolutionRepo()
    session_id = chat_repo.create_session()
    _patch_repos(monkeypatch, chat_repo, resolution_repo)

    @asynccontextmanager
    async def raises(servers):
        raise RuntimeError("MCP subprocess died")
        yield  # pragma: no cover - unreachable, makes this a generator

    monkeypatch.setattr(agent_module, "open_mcp_servers", raises)

    result = agent_module.PortfolioAssistantAgent().send_message(session_id, "hi")

    assert result.status == "ERROR"
    messages = chat_repo.list_messages(session_id)
    assert messages[1].role == "assistant"
    assert "sorry" in messages[1].content.lower() or "wrong" in messages[1].content.lower()


# --- pure helpers ---------------------------------------------------------


def test_select_tools_returns_the_fixed_allowlist_across_four_servers():
    servers = agent_module._select_tools("anything")

    assert set(servers) == {
        "ai.mcp_servers.portfolio",
        "ai.mcp_servers.market_data",
        "ai.mcp_servers.analytics",
        "ai.mcp_servers.quant",
    }
    assert "get_portfolio_summary" in servers["ai.mcp_servers.portfolio"]
    assert servers["ai.mcp_servers.quant"] == {"list_models", "recommend_model", "explain_run"}


def test_auto_title_truncates_a_long_first_line():
    long_message = "x" * 100
    title = agent_module._auto_title(long_message)
    assert len(title) <= 61
    assert title.endswith("…")


def test_auto_title_uses_only_the_first_line():
    title = agent_module._auto_title("first line\nsecond line")
    assert title == "first line"
