"""Private vs shared assets (ADR 0005, migration 0011, docs/saas/02 §4.4).

A user's free-text import is a PRIVATE stub. Only the system (resolver /
Operator) may create or change SHARED assets; users read them but can never
modify them, and cannot promote their own stub. The resolver then promotes a
verified stub to shared, or merges it into an existing shared twin.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.adapters.persistence.repositories import (
    SqlAssetRepo,
    SqlPortfolioRepo,
    SqlResolutionRepo,
)
from app.adapters.persistence.session import scoped_session
from app.application.resolve_security import ResolveSecurityUseCase
from app.domain.listings import FigiListing, ResolutionStatus
from app.domain.models import AssetClass, IdentifierScheme
from app.ports.market_data import AssetSearchResult
from tests.test_resolve_security import FakeMarketData, FakeSecurityMaster, _listing

PREFIX = "ZZOWN"


@pytest.fixture(autouse=True)
def _clean_assets(user_a, user_b):
    yield
    # Holdings/transactions reference assets without ON DELETE CASCADE, so the
    # users' accounts (which cascade to them) must go first. Explicit owner
    # filter, as in conftest: cleanup never relies on RLS alone.
    for uid in (user_a, user_b):
        db = scoped_session(uid)
        try:
            db.execute(text("DELETE FROM accounts WHERE user_id = :u"), {"u": uid})
            db.commit()
        finally:
            db.close()
    sysdb = scoped_session(system=True)
    try:
        sysdb.execute(text("DELETE FROM assets WHERE symbol LIKE :p"), {"p": f"{PREFIX}%"})
        sysdb.commit()
    finally:
        sysdb.close()


def _shared_asset(symbol=f"{PREFIX}-SHARED", ident=None):
    db = scoped_session(system=True)
    try:
        repo = SqlAssetRepo(db)
        a = repo.create(symbol, "Shared", AssetClass.ETF, "EUR", shared=True)
        if ident:
            repo.add_identifier(a.id, IdentifierScheme.YFINANCE, ident)
        db.commit()
        return a.id
    finally:
        db.close()


def _private_asset(uid, symbol, isin=None, needs_mapping=True):
    db = scoped_session(uid)
    try:
        a = SqlAssetRepo(db).create(
            symbol, "Stub", AssetClass.ETF, "EUR", isin=isin, needs_mapping=needs_mapping
        )
        db.commit()
        return a.id
    finally:
        db.close()


def _hold(uid, asset_id, account_ext="H1"):
    db = scoped_session(uid)
    try:
        acc = SqlPortfolioRepo(db).get_or_create_account("ibkr", account_ext, "Main", "EUR", "manual")
        db.execute(
            text(
                "INSERT INTO holdings (account_id, asset_id, quantity, avg_cost_price, "
                "cost_currency, as_of, source) VALUES (:a, :s, 3, 10, 'EUR', now(), 'manual')"
            ),
            {"a": acc.id, "s": asset_id},
        )
        db.commit()
    finally:
        db.close()


def _visible_ids(uid):
    db = scoped_session(uid)
    try:
        return set(db.execute(text("SELECT id FROM assets")).scalars())
    finally:
        db.close()


def test_new_assets_are_private_to_their_creator(user_a, user_b):
    a_id = _private_asset(user_a, f"{PREFIX}-A")
    assert a_id in _visible_ids(user_a)
    assert a_id not in _visible_ids(user_b)


def test_shared_assets_are_visible_to_everyone(user_a, user_b):
    s_id = _shared_asset()
    assert s_id in _visible_ids(user_a) and s_id in _visible_ids(user_b)


def test_users_cannot_modify_or_delete_a_shared_asset(user_a):
    s_id = _shared_asset()
    db = scoped_session(user_a)
    try:
        upd = db.execute(text("UPDATE assets SET name = 'pwned' WHERE id = :i"), {"i": s_id})
        dele = db.execute(text("DELETE FROM assets WHERE id = :i"), {"i": s_id})
        assert upd.rowcount == 0 and dele.rowcount == 0
        db.commit()
    finally:
        db.close()
    sysdb = scoped_session(system=True)
    try:
        assert sysdb.execute(text("SELECT name FROM assets WHERE id=:i"), {"i": s_id}).scalar_one() == "Shared"
    finally:
        sysdb.close()


def test_users_cannot_create_a_shared_asset(user_a):
    db = scoped_session(user_a)
    try:
        with pytest.raises(DBAPIError):
            db.execute(
                text(
                    "INSERT INTO assets (symbol, name, asset_class, currency, owner_user_id) "
                    "VALUES ('ZZOWN-EVIL', 'x', 'etf', 'EUR', NULL)"
                )
            )
    finally:
        db.rollback()
        db.close()


def test_users_cannot_promote_their_own_asset_to_shared(user_a):
    a_id = _private_asset(user_a, f"{PREFIX}-SELFPROMO")
    db = scoped_session(user_a)
    try:
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE assets SET owner_user_id = NULL WHERE id = :i"), {"i": a_id})
    finally:
        db.rollback()
        db.close()


def test_no_identity_and_no_system_flag_cannot_create_assets(anon_db):
    with pytest.raises(DBAPIError):
        anon_db.execute(
            text(
                "INSERT INTO assets (symbol, name, asset_class, currency) "
                "VALUES ('ZZOWN-ANON', 'x', 'etf', 'EUR')"
            )
        )


def test_two_users_can_each_have_a_private_asset_with_the_same_symbol(user_a, user_b):
    _private_asset(user_a, f"{PREFIX}-DUP")
    _private_asset(user_b, f"{PREFIX}-DUP")


def test_local_search_does_not_reveal_assets_other_users_hold(user_a, user_b):
    shared = _shared_asset(f"{PREFIX}-SEARCH")
    _hold(user_a, shared)
    for uid, expected in ((user_a, True), (user_b, False)):
        db = scoped_session(uid)
        try:
            found = [a.symbol for a in SqlAssetRepo(db).search_local(f"{PREFIX}-SEARCH")]
        finally:
            db.close()
        assert (f"{PREFIX}-SEARCH" in found) is expected


# --- the resolver's promote / merge, end to end on real Postgres -----------------


def _resolve_as_system(owner, asset_id):
    """What the resolver job does: system mode, acting as the asset's owner."""
    figi = FigiListing(
        ticker="ZZOWN", exch_code="GY", name="X", security_type="ETP", share_class_figi=None
    )
    market = FakeMarketData(
        search_results={
            "IE00B4L5Y983": [
                AssetSearchResult(
                    symbol="ZZOWN.DE", name="X", exchange=None, asset_class="ETF", currency="EUR"
                )
            ]
        },
        listings={"ZZOWN.DE": _listing("ZZOWN.DE", "EUR")},
    )
    db = scoped_session(owner, system=True)
    try:
        use_case = ResolveSecurityUseCase(
            SqlAssetRepo(db),
            SqlPortfolioRepo(db),
            SqlResolutionRepo(db),
            FakeSecurityMaster({"IE00B4L5Y983": [figi]}),
            market,
            False,
        )
        resolution = use_case.resolve_asset(asset_id)
        db.commit()
        return resolution
    finally:
        db.close()


def test_verified_private_stub_is_promoted_to_shared(user_a, user_b):
    a_id = _private_asset(user_a, f"{PREFIX}-STUB", isin="IE00B4L5Y983")
    assert a_id not in _visible_ids(user_b)

    resolution = _resolve_as_system(user_a, a_id)

    assert resolution.status is ResolutionStatus.AUTO_ACCEPTED
    assert a_id in _visible_ids(user_b), "verified asset must now be shared with everyone"
    sysdb = scoped_session(system=True)
    try:
        assert SqlAssetRepo(sysdb).get_owner(a_id) is None
    finally:
        sysdb.close()


def test_second_users_stub_is_merged_into_the_shared_twin(user_a, user_b):
    # user A's stub resolves first and becomes the shared asset (carries ZZOWN.DE)
    a_id = _private_asset(user_a, f"{PREFIX}-STUBA", isin="IE00B4L5Y983")
    _resolve_as_system(user_a, a_id)

    # user B imports the same security under a different broker symbol and holds it
    b_stub = _private_asset(user_b, f"{PREFIX}-STUBB", isin="IE00B4L5Y983")
    _hold(user_b, b_stub, account_ext="B1")

    resolution = _resolve_as_system(user_b, b_stub)

    assert resolution.status is ResolutionStatus.AUTO_ACCEPTED
    assert "merged into shared asset" in resolution.note
    db = scoped_session(user_b)
    try:
        # B's holding now points at the shared asset, and B's stub is gone
        assert db.execute(text("SELECT asset_id FROM holdings")).scalar_one() == a_id
        assert db.execute(text("SELECT count(*) FROM assets WHERE id = :i"), {"i": b_stub}).scalar_one() == 0
    finally:
        db.close()
    # and A's data is untouched
    da = scoped_session(user_a)
    try:
        assert da.execute(text("SELECT count(*) FROM holdings")).scalar_one() == 0
    finally:
        da.close()


def test_merge_is_refused_when_the_account_already_holds_the_shared_asset(user_a, user_b):
    a_id = _private_asset(user_a, f"{PREFIX}-STUBA2", isin="IE00B4L5Y983")
    _resolve_as_system(user_a, a_id)  # now shared

    b_stub = _private_asset(user_b, f"{PREFIX}-STUBB2", isin="IE00B4L5Y983")
    _hold(user_b, b_stub, account_ext="B2")
    _hold(user_b, a_id, account_ext="B2")  # same account already holds the shared asset

    resolution = _resolve_as_system(user_b, b_stub)

    assert resolution.status is ResolutionStatus.NEEDS_REVIEW
    db = scoped_session(user_b)
    try:  # nothing was lost: the stub and both holdings still exist
        assert db.execute(text("SELECT count(*) FROM holdings")).scalar_one() == 2
        assert db.execute(text("SELECT count(*) FROM assets WHERE id = :i"), {"i": b_stub}).scalar_one() == 1
    finally:
        db.close()
