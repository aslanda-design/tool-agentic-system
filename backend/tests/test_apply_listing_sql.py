"""SqlAssetRepo.apply_listing against real Postgres — the fakes-based tests
in test_apply_listing.py prove the same logic against an in-memory dict, not
the actual identifier/price/quote cascades. Needs a running Postgres (see
backend/AGENTS.md: `docker compose up -d db`) — same requirement as
test_health.py. Uses a session that's rolled back at the end of each test
rather than committed, so nothing is left behind in the dev DB.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.adapters.persistence.repositories import SqlAssetRepo, SqlMarketDataRepo
from app.adapters.persistence.session import SessionLocal
from app.domain.errors import AssetConflictError
from app.domain.models import AssetClass, IdentifierScheme


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_apply_listing_updates_currency_exchange_and_clears_needs_mapping(db):
    repo = SqlAssetRepo(db)
    asset = repo.create(
        symbol="test-apply-listing-1",
        name="Test Asset",
        asset_class=AssetClass.EQUITY,
        currency="EUR",
        needs_mapping=True,
    )

    repo.apply_listing(asset.id, "VUSA.L", "gbp", "XLON", AssetClass.ETF, "BBG000BLNNH6")

    updated = repo.get(asset.id)
    assert updated.currency == "GBP"
    assert updated.exchange == "XLON"
    assert updated.needs_mapping is False
    assert updated.asset_class == AssetClass.ETF
    assert updated.share_class_figi == "BBG000BLNNH6"
    assert repo.find_by_identifier(IdentifierScheme.YFINANCE, "VUSA.L").id == asset.id


def test_remapping_deletes_old_yfinance_identifier_and_stale_prices(db):
    asset_repo = SqlAssetRepo(db)
    market_data_repo = SqlMarketDataRepo(db)
    asset = asset_repo.create(
        symbol="test-apply-listing-2", name="Test", asset_class=AssetClass.EQUITY, currency="EUR", needs_mapping=True
    )
    asset_repo.apply_listing(asset.id, "OLD.DE", "EUR", "XETR", None, None)
    market_data_repo.upsert_bars(
        asset.id,
        [
            {
                "date": date(2026, 1, 1),
                "open": Decimal(10),
                "high": Decimal(10),
                "low": Decimal(10),
                "close": Decimal(10),
                "adj_close": Decimal(10),
                "volume": Decimal(0),
            }
        ],
        "yfinance",
    )
    market_data_repo.upsert_quote(asset.id, Decimal(10), Decimal("9.9"), "EUR", datetime.now(UTC), "yfinance")
    assert market_data_repo.get_bars(asset.id, date(2026, 1, 1), date(2026, 1, 1))
    assert market_data_repo.get_quote(asset.id) is not None

    asset_repo.apply_listing(asset.id, "NEW.L", "GBP", "XLON", None, None)

    assert asset_repo.find_by_identifier(IdentifierScheme.YFINANCE, "OLD.DE") is None
    mapped = asset_repo.find_by_identifier(IdentifierScheme.YFINANCE, "NEW.L")
    assert mapped is not None and mapped.id == asset.id
    # stale listing data for the OLD symbol must not survive the remap
    assert market_data_repo.get_bars(asset.id, date(2026, 1, 1), date(2026, 1, 1)) == []
    assert market_data_repo.get_quote(asset.id) is None


def test_reapplying_same_symbol_keeps_prices(db):
    asset_repo = SqlAssetRepo(db)
    market_data_repo = SqlMarketDataRepo(db)
    asset = asset_repo.create(
        symbol="test-apply-listing-3", name="Test", asset_class=AssetClass.EQUITY, currency="EUR", needs_mapping=True
    )
    asset_repo.apply_listing(asset.id, "SAME.DE", "EUR", "XETR", None, None)
    market_data_repo.upsert_quote(asset.id, Decimal(10), None, "EUR", datetime.now(UTC), "yfinance")

    asset_repo.apply_listing(asset.id, "SAME.DE", "EUR", "XETR", None, None)

    assert market_data_repo.get_quote(asset.id) is not None


def test_apply_listing_conflict_raises_and_does_not_partially_apply(db):
    repo = SqlAssetRepo(db)
    first = repo.create(
        symbol="test-apply-listing-4a", name="A", asset_class=AssetClass.EQUITY, currency="EUR", needs_mapping=True
    )
    second = repo.create(
        symbol="test-apply-listing-4b", name="B", asset_class=AssetClass.EQUITY, currency="EUR", needs_mapping=True
    )
    repo.apply_listing(first.id, "SHARED.L", "GBP", "XLON", None, None)

    with pytest.raises(AssetConflictError):
        repo.apply_listing(second.id, "SHARED.L", "GBP", "XLON", None, None)

    assert repo.get(second.id).needs_mapping is True
    assert repo.find_by_identifier(IdentifierScheme.YFINANCE, "SHARED.L").id == first.id
