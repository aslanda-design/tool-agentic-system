"""Tests for the `notes` MCP server (Phase 8c of
plans/agentic_asset_mapping_phase7_8.md — backend/ai/mcp_servers/notes).
Same shape as test_mcp_security_server.py — save_note is the second (and
simplest) legitimate write path an MCP tool has, alongside security's
apply_listing-backed writes (see backend/ai/AGENTS.md rule 6)."""

from __future__ import annotations

import asyncio
import json

from ai.mcp_servers.notes import server
from ai.mcp_servers.notes.tools import list_notes as list_notes_tool
from ai.mcp_servers.notes.tools import save_note as save_note_tool
from tests.fakes import FakeNoteRepo


def test_save_note_persists_and_echoes_back(monkeypatch):
    repo = FakeNoteRepo()
    monkeypatch.setattr(save_note_tool, "note_repo", lambda db: repo)

    result = save_note_tool.save_note("weekly_report", "portfolio", "Weekly summary", "Nothing to report.")

    assert result["title"] == "Weekly summary"
    assert result["id"] is not None
    saved = repo.list(scope="portfolio")
    assert len(saved) == 1
    assert saved[0].agent == "weekly_report"
    assert saved[0].body == "Nothing to report."


def test_save_note_with_account_scope_stores_the_account_id(monkeypatch):
    repo = FakeNoteRepo()
    monkeypatch.setattr(save_note_tool, "note_repo", lambda db: repo)

    save_note_tool.save_note("import_reviewer", "account", "Mispriced trade", "IB1T looks off.", account_id=7)

    saved = repo.list(scope="account")
    assert saved[0].account_id == 7


def test_list_notes_returns_jsonable_notes_most_recent_first(monkeypatch):
    repo = FakeNoteRepo()
    repo.add("weekly_report", "portfolio", "Older", "body")
    repo.add("weekly_report", "portfolio", "Newer", "body")
    monkeypatch.setattr(list_notes_tool, "note_repo", lambda db: repo)

    result = list_notes_tool.list_notes(scope="portfolio")

    assert [n["title"] for n in result] == ["Newer", "Older"]
    assert result[0]["dismissed_at"] is None


def test_list_notes_respects_limit(monkeypatch):
    repo = FakeNoteRepo()
    for i in range(5):
        repo.add("weekly_report", "portfolio", f"Note {i}", "body")
    monkeypatch.setattr(list_notes_tool, "note_repo", lambda db: repo)

    assert len(list_notes_tool.list_notes(scope="portfolio", limit=2)) == 2


def test_server_registers_both_tools():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == {"save_note", "list_notes"}


def test_server_call_tool_round_trips_through_the_registered_function(monkeypatch):
    repo = FakeNoteRepo()
    monkeypatch.setattr(save_note_tool, "note_repo", lambda db: repo)

    result = asyncio.run(
        server.mcp.call_tool("save_note", {"agent": "weekly_report", "scope": "portfolio", "title": "t", "body": "b"})
    )

    assert result.is_error is False
    assert json.loads(result.content[0].text)["title"] == "t"
    assert len(repo.list(scope="portfolio")) == 1
