"""Tests for ImportReviewerAgent (Phase 8d of
plans/agentic_asset_mapping_phase7_8.md) — same fallback-on-failure
discipline as test_security_resolver_agent.py: a run must always leave a
trace (a note, plus an agent_runs row) even when the model/loop fails. No
Ollama or real MCP subprocess needed: open_mcp_servers and run_agent are
replaced with fakes/monkeypatches."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from ai.agents.import_reviewer import agent as agent_module
from ai.common.agent_loop import AgentRunResult
from tests.fakes import FakeNoteRepo, FakeResolutionRepo


class _FakeMultiSession:
    def __init__(self, mispriced=None, freshness=None, pending=None) -> None:
        self._results = {
            "check_import_prices": mispriced or [],
            "get_data_freshness": freshness or {"as_of": "2026-09-15", "stale_positions": [], "unmapped_assets": []},
            "list_pending_resolutions": pending or [],
        }

    async def call_tool(self, name, arguments):
        return self._results[name]

    async def tool_schemas(self):
        return [{"type": "function", "function": {"name": "save_note", "description": "", "parameters": {}}}]


def _patch_mcp_servers(monkeypatch, **kwargs) -> _FakeMultiSession:
    session = _FakeMultiSession(**kwargs)

    @asynccontextmanager
    async def fake_open_mcp_servers(servers):
        yield session

    monkeypatch.setattr(agent_module, "open_mcp_servers", fake_open_mcp_servers)
    return session


def _patch_repos(monkeypatch, note_repo: FakeNoteRepo, resolution_repo: FakeResolutionRepo) -> None:
    monkeypatch.setattr(agent_module, "note_repo", lambda db: note_repo)
    monkeypatch.setattr(agent_module, "resolution_repo", lambda db: resolution_repo)


def _fake_run_agent(result: AgentRunResult):
    async def fake(**kwargs):
        return result

    return fake


def test_records_the_run_and_writes_no_fallback_note_on_success(monkeypatch):
    note_repo, resolution_repo = FakeNoteRepo(), FakeResolutionRepo()
    _patch_mcp_servers(monkeypatch)
    _patch_repos(monkeypatch, note_repo, resolution_repo)
    monkeypatch.setattr(agent_module, "run_agent", _fake_run_agent(AgentRunResult(status="SAVED", steps=1)))

    result = agent_module.ImportReviewerAgent().run(account_id=7)

    assert result.status == "SAVED"
    # SAVED means the model's own save_note call already wrote the real
    # note (not simulated by this fake loop) — the fallback path must not
    # also write one on top of a run that already terminated successfully.
    assert note_repo.list() == []
    assert len(resolution_repo.agent_runs) == 1
    assert resolution_repo.agent_runs[0].agent == "import_reviewer"
    assert resolution_repo.agent_runs[0].resolution_id is None
    assert resolution_repo.agent_runs[0].status == "SAVED"


def test_writes_a_fallback_note_when_the_loop_hits_max_steps(monkeypatch):
    note_repo, resolution_repo = FakeNoteRepo(), FakeResolutionRepo()
    _patch_mcp_servers(monkeypatch)
    _patch_repos(monkeypatch, note_repo, resolution_repo)
    monkeypatch.setattr(agent_module, "run_agent", _fake_run_agent(AgentRunResult(status="MAX_STEPS", steps=8)))

    result = agent_module.ImportReviewerAgent().run(account_id=7)

    assert result.status == "MAX_STEPS"
    notes = note_repo.list(scope="account")
    assert len(notes) == 1
    assert notes[0].account_id == 7
    assert notes[0].agent == "import_reviewer"
    assert "MAX_STEPS" in notes[0].body


def test_writes_a_fallback_note_on_timeout(monkeypatch):
    note_repo, resolution_repo = FakeNoteRepo(), FakeResolutionRepo()
    _patch_mcp_servers(monkeypatch)
    _patch_repos(monkeypatch, note_repo, resolution_repo)

    async def hangs_forever(**kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(agent_module, "run_agent", hangs_forever)
    monkeypatch.setattr(agent_module.settings, "agent_timeout_seconds", 0.05)

    result = agent_module.ImportReviewerAgent().run(account_id=7)

    assert result.status == "TIMEOUT"
    assert len(note_repo.list(scope="account")) == 1
    assert len(resolution_repo.agent_runs) == 1
    assert resolution_repo.agent_runs[0].status == "TIMEOUT"


def test_writes_a_fallback_note_on_unexpected_error(monkeypatch):
    note_repo, resolution_repo = FakeNoteRepo(), FakeResolutionRepo()
    _patch_repos(monkeypatch, note_repo, resolution_repo)

    @asynccontextmanager
    async def raises(servers):
        raise RuntimeError("MCP subprocess died")
        yield  # pragma: no cover - unreachable, makes this a generator

    monkeypatch.setattr(agent_module, "open_mcp_servers", raises)

    result = agent_module.ImportReviewerAgent().run(account_id=7)

    assert result.status == "ERROR"
    assert "MCP subprocess died" in result.error
    assert len(note_repo.list(scope="account")) == 1


# --- render_user_message (pure) -----------------------------------------


def test_render_user_message_includes_every_finding():
    message = agent_module.render_user_message(
        account_id=7,
        mispriced=[{"symbol": "IB1T", "executed_price": 6.86, "market_close": 8.01, "pct_diff": -0.143}],
        freshness={"stale_positions": [{"symbol": "OLD", "last_price_date": None}], "unmapped_assets": [{"symbol": "NEW"}]},
        pending=[{"id": 1}],
    )

    assert "IB1T" in message
    assert "OLD" in message
    assert "NEW" in message
    assert "1 security resolution(s)" in message


def test_render_user_message_says_nothing_wrong_when_everything_is_clean():
    message = agent_module.render_user_message(
        account_id=7, mispriced=[], freshness={"stale_positions": [], "unmapped_assets": []}, pending=[],
    )

    assert "No mispriced trades found." in message
