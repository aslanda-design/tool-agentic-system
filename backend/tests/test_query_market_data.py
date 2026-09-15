"""Tests for QueryMarketDataUseCase (Phase 8a of
plans/agentic_asset_mapping_phase7_8.md) — the use case behind the
`market_data` MCP server. Uses fake ports so these stay fast and DB-free."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.application.query_market_data import QueryMarketDataUseCase
from app.domain.models import AssetClass, IdentifierScheme
from tests.fakes import FakeAssetRepo, FakeMarketDataRepo, FakePortfolioRepo

D = Decimal


def _use_case(asset_repo=None, portfolio_repo=None, market_data_repo=None) -> QueryMarketDataUseCase:
    asset_repo = asset_repo or FakeAssetRepo()
    return QueryMarketDataUseCase(
        asset_repo, portfolio_repo or FakePortfolioRepo(asset_repo), market_data_repo or FakeMarketDataRepo()
    )


# --- get_asset -----------------------------------------------------------


def test_get_asset_by_id_uses_the_persisted_quote():
    asset_repo = FakeAssetRepo()
    asset = asset_repo.create("AAPL", "Apple Inc.", AssetClass.EQUITY, "USD")
    market_data_repo = FakeMarketDataRepo()
    market_data_repo.upsert_quote(asset.id, D("230.50"), D("228.10"), "USD", datetime.now(timezone.utc), "yfinance")
    use_case = _use_case(asset_repo, market_data_repo=market_data_repo)

    result = use_case.get_asset(asset_id=asset.id)

    assert result.symbol == "AAPL"
    assert result.last_price == D("230.50")
    assert result.prev_close == D("228.10")


def test_get_asset_by_symbol_resolves_via_yfinance_identifier():
    asset_repo = FakeAssetRepo()
    asset = asset_repo.create("VWCE.DE", "Vanguard FTSE All-World", AssetClass.ETF, "EUR")
    asset_repo.add_identifier(asset.id, IdentifierScheme.YFINANCE, "VWCE.DE")
    use_case = _use_case(asset_repo)

    result = use_case.get_asset(symbol="VWCE.DE")

    assert result.asset_id == asset.id


def test_get_asset_falls_back_to_last_price_bar_when_no_quote():
    asset_repo = FakeAssetRepo()
    asset = asset_repo.create("AAPL", "Apple Inc.", AssetClass.EQUITY, "USD")
    market_data_repo = FakeMarketDataRepo()
    market_data_repo.upsert_bars(asset.id, [{"date": date(2026, 9, 1), "close": D("225.00")}], "yfinance")
    use_case = _use_case(asset_repo, market_data_repo=market_data_repo)

    result = use_case.get_asset(asset_id=asset.id)

    assert result.last_price == D("225.00")
    assert result.prev_close is None


def test_get_asset_returns_none_for_unknown_id():
    assert _use_case().get_asset(asset_id=999) is None


def test_get_asset_requires_exactly_one_argument():
    use_case = _use_case()
    with pytest.raises(ValueError, match="exactly one"):
        use_case.get_asset()
    with pytest.raises(ValueError, match="exactly one"):
        use_case.get_asset(asset_id=1, symbol="AAPL")


# --- get_price_history -----------------------------------------------------


def test_get_price_history_returns_bars_unchanged_when_under_max_points():
    asset_repo = FakeAssetRepo()
    asset = asset_repo.create("AAPL", "Apple", AssetClass.EQUITY, "USD")
    market_data_repo = FakeMarketDataRepo()
    bars = [{"date": date(2026, 9, d), "close": D(100 + d)} for d in range(1, 6)]
    market_data_repo.upsert_bars(asset.id, bars, "yfinance")
    use_case = _use_case(asset_repo, market_data_repo=market_data_repo)

    result = use_case.get_price_history(asset.id, date(2026, 9, 1), date(2026, 9, 5), max_points=120)

    assert len(result) == 5
    assert result[0].date == date(2026, 9, 1)
    assert result[-1].date == date(2026, 9, 5)


def test_get_price_history_downsamples_and_always_keeps_the_last_point():
    asset_repo = FakeAssetRepo()
    asset = asset_repo.create("AAPL", "Apple", AssetClass.EQUITY, "USD")
    market_data_repo = FakeMarketDataRepo()
    bars = [{"date": date(2026, 1, 1) + timedelta(days=i), "close": D(100 + i)} for i in range(300)]
    market_data_repo.upsert_bars(asset.id, bars, "yfinance")
    use_case = _use_case(asset_repo, market_data_repo=market_data_repo)

    result = use_case.get_price_history(asset.id, bars[0]["date"], bars[-1]["date"], max_points=50)

    assert len(result) <= 50
    assert result[-1].date == bars[-1]["date"]
    assert result[0].date == bars[0]["date"]


# --- get_fx_rate -----------------------------------------------------------


def test_get_fx_rate_returns_known_rate():
    market_data_repo = FakeMarketDataRepo()
    market_data_repo.upsert_fx_rates("USD", "EUR", {date(2026, 9, 1): D("0.92")})
    use_case = _use_case(market_data_repo=market_data_repo)

    result = use_case.get_fx_rate("USD", "EUR", date(2026, 9, 1))

    assert result == {"base": "USD", "quote": "EUR", "rate": D("0.92"), "on_date": date(2026, 9, 1)}


def test_get_fx_rate_returns_none_for_unknown_pair():
    assert _use_case().get_fx_rate("USD", "JPY", date(2026, 9, 1)) is None


# --- get_data_freshness ------------------------------------------------


def test_get_data_freshness_flags_stale_and_missing_prices():
    asset_repo = FakeAssetRepo()
    fresh = asset_repo.create("AAPL", "Apple", AssetClass.EQUITY, "USD")
    stale = asset_repo.create("OLD", "Old Fund", AssetClass.FUND, "EUR")
    unmapped = asset_repo.create("UNKNOWN", "Unknown", AssetClass.OTHER, "EUR", needs_mapping=True)
    portfolio_repo = FakePortfolioRepo(asset_repo)
    account = portfolio_repo.get_or_create_account("ibkr", "U1", "Test", "USD", "api")
    portfolio_repo.upsert_holding(account.id, fresh.id, D(1), D(100), "USD", datetime.now(timezone.utc), "api")
    portfolio_repo.upsert_holding(account.id, stale.id, D(1), D(100), "EUR", datetime.now(timezone.utc), "api")
    market_data_repo = FakeMarketDataRepo()
    market_data_repo.upsert_bars(fresh.id, [{"date": date.today(), "close": D(100)}], "yfinance")
    market_data_repo.upsert_bars(stale.id, [{"date": date(2020, 1, 1), "close": D(50)}], "yfinance")
    use_case = _use_case(asset_repo, portfolio_repo, market_data_repo)

    result = use_case.get_data_freshness()

    stale_ids = {p.asset_id for p in result.stale_positions}
    assert stale.id in stale_ids
    assert fresh.id not in stale_ids
    assert {a.asset_id for a in result.unmapped_assets} == {unmapped.id}
