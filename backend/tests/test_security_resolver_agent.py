"""Tests for SecurityResolverAgent's deterministic fallback (Phase 6 of
plans/agentic_asset_mapping.md) — a resolution must never stay stuck in
NEEDS_AGENT because the model/loop failed; it's always handed to a human
via flag_for_review. No Ollama or real MCP subprocess needed:
open_mcp_server and run_agent are replaced with fakes/monkeypatches."""

from __future__ import annotations

from contextlib import asynccontextmanager

from ai.agents.security_resolver import agent as agent_module
from ai.common.agent_loop import AgentRunResult
from app.domain.listings import ResolutionStatus
from tests.fakes import FakeResolutionRepo
from tests.test_resolve_security import _ctx, _use_case


class _FakeMcpSession:
    def __init__(self, resolution: dict | None) -> None:
        self._resolution = resolution

    async def call_tool(self, name, arguments):
        assert name == "get_resolution"
        return self._resolution

    async def tool_schemas(self):
        return []


def _patch_mcp_server(monkeypatch, resolution: dict | None) -> None:
    @asynccontextmanager
    async def fake_open_mcp_server(module, tool_names=None):
        yield _FakeMcpSession(resolution)

    monkeypatch.setattr(agent_module, "open_mcp_server", fake_open_mcp_server)


def _resolution_dict(resolution_id: int) -> dict:
    return {
        "id": resolution_id,
        "asset_id": 1,
        "context": {
            "asset_id": 1,
            "isin": "IE00B4L5Y983",
            "broker_symbol": "EUNL",
            "broker_name": "iShares Core MSCI World",
            "broker_exchange": None,
            "broker_mic": None,
            "currency": "EUR",
            "broker_key": None,
        },
        "status": "NEEDS_AGENT",
        "decided_by": None,
        "note": "",
        "scorer_version": "rules-v1",
        "candidates": [],
        "selected_candidate_id": None,
    }


def _patch_container(monkeypatch, repo: FakeResolutionRepo) -> None:
    use_case = _use_case(resolution_repo=repo)
    monkeypatch.setattr(agent_module, "build_resolve_security_use_case", lambda db: use_case)
    monkeypatch.setattr(agent_module, "resolution_repo", lambda db: repo)


def test_flags_for_review_when_the_loop_returns_max_steps(monkeypatch):
    repo = FakeResolutionRepo()
    resolution_id = repo.create(_ctx(), ResolutionStatus.NEEDS_AGENT, None, "", "rules-v1", [])
    _patch_container(monkeypatch, repo)
    _patch_mcp_server(monkeypatch, _resolution_dict(resolution_id))
    monkeypatch.setattr(agent_module, "run_agent", _fake_run_agent(AgentRunResult(status="MAX_STEPS", steps=8)))

    result = agent_module.SecurityResolverAgent().run(resolution_id)

    assert result.status == "MAX_STEPS"
    updated = repo.get(resolution_id)
    assert updated.status == ResolutionStatus.NEEDS_REVIEW
    assert updated.note == "agent MAX_STEPS"
    assert len(repo.agent_runs) == 1
    assert repo.agent_runs[0].status == "MAX_STEPS"
    assert repo.agent_runs[0].resolution_id == resolution_id


def test_flags_for_review_on_timeout_too(monkeypatch):
    repo = FakeResolutionRepo()
    resolution_id = repo.create(_ctx(), ResolutionStatus.NEEDS_AGENT, None, "", "rules-v1", [])
    _patch_container(monkeypatch, repo)
    _patch_mcp_server(monkeypatch, _resolution_dict(resolution_id))

    async def hangs_forever(**kwargs):
        import asyncio

        await asyncio.sleep(10)

    monkeypatch.setattr(agent_module, "run_agent", hangs_forever)
    monkeypatch.setattr(agent_module.settings, "agent_timeout_seconds", 0.05)

    result = agent_module.SecurityResolverAgent().run(resolution_id)

    assert result.status == "TIMEOUT"
    assert repo.get(resolution_id).status == ResolutionStatus.NEEDS_REVIEW


def test_does_not_flag_again_when_the_loop_already_terminated(monkeypatch):
    repo = FakeResolutionRepo()
    resolution_id = repo.create(_ctx(), ResolutionStatus.NEEDS_AGENT, None, "", "rules-v1", [])
    _patch_container(monkeypatch, repo)
    _patch_mcp_server(monkeypatch, _resolution_dict(resolution_id))
    monkeypatch.setattr(agent_module, "run_agent", _fake_run_agent(AgentRunResult(status="SAVED", steps=2)))

    agent_module.SecurityResolverAgent().run(resolution_id)

    # The loop reporting SAVED means its own save_security_mapping tool call
    # already applied the mapping (not simulated here) — the point of this
    # test is only that the fallback flag_for_review is NOT also called on
    # top of a run that already terminated successfully.
    assert repo.get(resolution_id).status == ResolutionStatus.NEEDS_AGENT


def test_records_error_and_flags_when_the_resolution_is_missing(monkeypatch):
    repo = FakeResolutionRepo()
    _patch_container(monkeypatch, repo)
    _patch_mcp_server(monkeypatch, None)

    result = agent_module.SecurityResolverAgent().run(999)

    assert result.status == "ERROR"
    assert "999" in result.error
    assert len(repo.agent_runs) == 1
    assert repo.agent_runs[0].status == "ERROR"


def _fake_run_agent(result: AgentRunResult):
    async def fake(**kwargs):
        return result

    return fake
