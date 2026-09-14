"""SqlResolutionRepo against real Postgres. Needs a running Postgres (see
backend/AGENTS.md: `docker compose up -d db`) — same requirement as
test_health.py. Uses a session rolled back at the end of each test rather
than committed, so nothing is left behind in the dev DB.
"""

from __future__ import annotations

import pytest

from app.adapters.persistence.repositories import SqlAssetRepo, SqlResolutionRepo
from app.adapters.persistence.session import SessionLocal
from app.domain.listings import (
    AgentRunRecord,
    Candidate,
    ListingInfo,
    ResolutionContext,
    ResolutionStatus,
)
from app.domain.models import AssetClass


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _make_asset(db, symbol="test-resolution-repo-1"):
    return SqlAssetRepo(db).create(
        symbol=symbol, name="Test", asset_class=AssetClass.EQUITY, currency="EUR", needs_mapping=True
    )


def _ctx(asset_id: int) -> ResolutionContext:
    return ResolutionContext(
        asset_id=asset_id,
        isin="IE00B3XXRP09",
        broker_symbol="VUSA",
        broker_name="Vanguard S&P 500 UCITS ETF",
        broker_exchange="LSEETF",
        broker_mic="XLON",
        currency="GBP",
        broker_key="interactive_brokers",
    )


def _candidate(symbol="VUSA.L", score=79) -> Candidate:
    return Candidate(
        symbol=symbol,
        found_by={"openfigi", "yahoo_isin"},
        info=ListingInfo(
            symbol=symbol,
            name="Vanguard S&P 500 UCITS ETF",
            currency="GBP",
            quote_type="ETF",
            last_close=None,
            last_trade_date=None,
            avg_volume=None,
        ),
        mic="XLON",
        features={"currency_match": True, "has_recent_price": True},
        score=score,
    )


def test_create_and_get_round_trips_context_and_candidates(db):
    asset = _make_asset(db)
    repo = SqlResolutionRepo(db)
    candidates = [_candidate("VUSA.L", 79), _candidate("VUSA.DE", 30)]

    rid = repo.create(_ctx(asset.id), ResolutionStatus.NEEDS_REVIEW, None, "no candidate scored high enough", "rules-v1", candidates)
    assert candidates[0].id is not None and candidates[1].id is not None  # ids backfilled onto the input objects

    fetched = repo.get(rid)
    assert fetched.asset_id == asset.id
    assert fetched.status == ResolutionStatus.NEEDS_REVIEW
    assert fetched.context.isin == "IE00B3XXRP09"
    assert fetched.context.broker_mic == "XLON"
    assert {c.symbol for c in fetched.candidates} == {"VUSA.L", "VUSA.DE"}
    assert fetched.selected_candidate_id is None


def test_get_open_for_asset_and_supersede(db):
    asset = _make_asset(db, "test-resolution-repo-2")
    repo = SqlResolutionRepo(db)
    first_id = repo.create(_ctx(asset.id), ResolutionStatus.NEEDS_REVIEW, None, "", "rules-v1", [])
    assert repo.get_open_for_asset(asset.id).id == first_id

    repo.supersede_open(asset.id)
    assert repo.get(first_id).status == ResolutionStatus.SUPERSEDED
    assert repo.get_open_for_asset(asset.id) is None

    second_id = repo.create(_ctx(asset.id), ResolutionStatus.AUTO_ACCEPTED, "rules", "", "rules-v1", [])
    assert repo.get_open_for_asset(asset.id).id == second_id


def test_select_candidate_marks_exactly_one(db):
    asset = _make_asset(db, "test-resolution-repo-3")
    repo = SqlResolutionRepo(db)
    candidates = [_candidate("A.L", 90), _candidate("B.L", 80)]
    rid = repo.create(_ctx(asset.id), ResolutionStatus.NEEDS_AGENT, None, "", "rules-v1", candidates)

    repo.select_candidate(rid, candidates[0].id)
    fetched = repo.get(rid)
    assert fetched.selected_candidate_id == candidates[0].id
    selected_flags = {c.symbol: c.id == fetched.selected_candidate_id for c in fetched.candidates}
    assert selected_flags == {"A.L": True, "B.L": False}

    # selecting the other candidate flips the flag rather than adding a second selected row
    repo.select_candidate(rid, candidates[1].id)
    assert repo.get(rid).selected_candidate_id == candidates[1].id


def test_add_candidate_onto_existing_resolution(db):
    asset = _make_asset(db, "test-resolution-repo-4")
    repo = SqlResolutionRepo(db)
    rid = repo.create(_ctx(asset.id), ResolutionStatus.NEEDS_AGENT, None, "", "rules-v1", [_candidate("A.L", 60)])

    new_candidate = _candidate("C.L", 95)
    new_id = repo.add_candidate(rid, new_candidate)
    assert new_candidate.id == new_id

    fetched = repo.get(rid)
    assert {c.symbol for c in fetched.candidates} == {"A.L", "C.L"}


def test_set_status_and_list_by_status(db):
    asset = _make_asset(db, "test-resolution-repo-5")
    repo = SqlResolutionRepo(db)
    rid = repo.create(_ctx(asset.id), ResolutionStatus.NEEDS_REVIEW, None, "no candidates", "rules-v1", [])

    open_ones = repo.list_by_status([ResolutionStatus.NEEDS_REVIEW, ResolutionStatus.NEEDS_AGENT])
    assert rid in {r.id for r in open_ones}

    repo.set_status(rid, ResolutionStatus.RESOLVED_BY_USER, "user", "picked manually")
    fetched = repo.get(rid)
    assert fetched.status == ResolutionStatus.RESOLVED_BY_USER
    assert fetched.decided_by == "user"
    assert fetched.note == "picked manually"

    still_open = repo.list_by_status([ResolutionStatus.NEEDS_REVIEW])
    assert rid not in {r.id for r in still_open}


def test_add_agent_run(db):
    asset = _make_asset(db, "test-resolution-repo-6")
    repo = SqlResolutionRepo(db)
    rid = repo.create(_ctx(asset.id), ResolutionStatus.NEEDS_AGENT, None, "", "rules-v1", [])

    run_id = repo.add_agent_run(
        AgentRunRecord(
            resolution_id=rid,
            agent="security_resolver",
            model="qwen3:8b",
            status="SAVED",
            steps=3,
            tool_calls=[{"name": "validate_listing", "arguments": {"symbol": "VUSA.L"}, "ok": True}],
            final_message="Chose VUSA.L — same currency and exchange as the broker.",
            prompt_tokens=512,
            completion_tokens=64,
            duration_ms=2400,
        )
    )
    assert run_id is not None


def test_list_assets_to_resolve_includes_needs_mapping_without_open_resolution(db):
    asset = _make_asset(db, "test-resolution-repo-7")
    repo = SqlResolutionRepo(db)

    assert asset.id in repo.list_assets_to_resolve()

    repo.create(_ctx(asset.id), ResolutionStatus.NEEDS_AGENT, None, "", "rules-v1", [_candidate()])
    # has a live (non-empty, non-stale) open resolution now — not up for a fresh attempt
    assert asset.id not in repo.list_assets_to_resolve()


def test_list_assets_to_resolve_retries_stale_empty_review(db):
    asset = _make_asset(db, "test-resolution-repo-8")
    repo = SqlResolutionRepo(db)
    repo.create(_ctx(asset.id), ResolutionStatus.NEEDS_REVIEW, None, "no candidates found", "rules-v1", [])

    # freshly created empty NEEDS_REVIEW — too soon to retry
    assert asset.id not in repo.list_assets_to_resolve(retry_empty_after_hours=24)
    # but with a 0-hour grace period it's immediately eligible
    assert asset.id in repo.list_assets_to_resolve(retry_empty_after_hours=0)


def test_list_assets_to_resolve_excludes_mapped_assets(db):
    asset_repo = SqlAssetRepo(db)
    resolution_repo = SqlResolutionRepo(db)
    asset = asset_repo.create(
        symbol="test-resolution-repo-9", name="Test", asset_class=AssetClass.EQUITY, currency="EUR", needs_mapping=False
    )
    assert asset.id not in resolution_repo.list_assets_to_resolve()
