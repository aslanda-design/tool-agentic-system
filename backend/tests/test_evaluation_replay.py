"""Tests for the evaluation harness's replay engine (Phase 6 of
plans/agentic_asset_mapping.md, ai/agents/security_resolver/evaluation/).
No Ollama needed — these exercise ReplaySession directly against the real
case files in evaluation/cases/, and against the guard logic that must
mirror ResolveSecurityUseCase.accept()'s own guards exactly."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ai.agents.security_resolver.evaluation.replay import ReplaySession

CASES_DIR = Path("ai/agents/security_resolver/evaluation/cases")
CASE_PATHS = sorted(CASES_DIR.glob("*.json"))


def _load(name: str) -> dict:
    return json.loads((CASES_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", CASE_PATHS, ids=lambda p: p.stem)
def test_every_case_file_has_the_required_shape(path: Path):
    case = json.loads(path.read_text(encoding="utf-8"))
    assert case["id"] == path.stem
    assert "resolution" in case and "candidates" in case["resolution"] and "context" in case["resolution"]
    assert case["expected"]["final_tool"] in ("save_security_mapping", "flag_for_review")
    if case["expected"]["final_tool"] == "save_security_mapping":
        assert case["expected"]["symbol"], f"{path.name}: save cases must set expected.symbol"
    # ReplaySession must construct without error from every real case file.
    ReplaySession(case)


def test_validate_listing_returns_the_fixture_verbatim():
    session = ReplaySession(_load("currency_decides_lse_vs_xetra.json"))

    result = asyncio.run(session.call_tool("validate_listing", {"symbol": "VUSA.L"}))

    assert result["currency"] == "GBP"


def test_validate_listing_returns_none_for_a_symbol_with_no_fixture():
    session = ReplaySession(_load("currency_decides_lse_vs_xetra.json"))

    assert asyncio.run(session.call_tool("validate_listing", {"symbol": "NOPE"})) is None


def test_unknown_tool_name_returns_an_error():
    session = ReplaySession(_load("currency_decides_lse_vs_xetra.json"))

    result = asyncio.run(session.call_tool("delete_everything", {}))

    assert "error" in result


def test_add_candidate_rescopes_with_the_real_scorer():
    session = ReplaySession(_load("missing_candidate_via_search.json"))

    result = asyncio.run(session.call_tool("add_candidate", {"resolution_id": 9003, "symbol": "IBE.MC"}))

    assert result["symbol"] == "IBE.MC"
    assert result["score"] > 0
    assert result["features"]["currency_match"] is True
    assert result["id"] is not None


def test_add_candidate_rejects_a_duplicate_symbol():
    session = ReplaySession(_load("currency_decides_lse_vs_xetra.json"))

    result = asyncio.run(session.call_tool("add_candidate", {"resolution_id": 9001, "symbol": "VUSA.L"}))

    assert "error" in result


def test_save_security_mapping_rejects_a_stale_candidate():
    session = ReplaySession(_load("all_candidates_stale_must_flag.json"))

    result = asyncio.run(
        session.call_tool("save_security_mapping", {"resolution_id": 9004, "candidate_id": 702, "reason": "x"})
    )

    assert "error" in result
    assert session.outcome.final_tool is None  # the guard rejected it — no outcome recorded


def test_save_security_mapping_accepts_a_valid_candidate_and_records_the_outcome():
    session = ReplaySession(_load("currency_decides_lse_vs_xetra.json"))

    result = asyncio.run(
        session.call_tool("save_security_mapping", {"resolution_id": 9001, "candidate_id": 501, "reason": "same currency"})
    )

    assert "error" not in result
    assert session.outcome.final_tool == "save_security_mapping"
    assert session.outcome.symbol == "VUSA.L"
    assert session.outcome.reason == "same currency"


def test_flag_for_review_records_the_outcome():
    session = ReplaySession(_load("all_candidates_stale_must_flag.json"))

    result = asyncio.run(session.call_tool("flag_for_review", {"resolution_id": 9004, "reason": "all stale"}))

    assert "error" not in result
    assert session.outcome.final_tool == "flag_for_review"
    assert session.outcome.symbol is None
