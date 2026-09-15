"""Tests for QueryAnalyticsUseCase (Phase 8b of
plans/agentic_asset_mapping_phase7_8.md) — the use case behind
`GET /api/analytics/*` and the `analytics` MCP server. Uses fake ports so
these stay fast and DB-free; the pure math itself is covered in
test_analytics.py, so these focus on composition (scope filtering, joining
positions with symbols, passing filters through to the repo)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.application.query_analytics import QueryAnalyticsUseCase
from app.application.query_portfolio import QueryPortfolioUseCase
from app.domain.models import AccountSource, AssetClass, Transaction, TransactionType
from tests.fakes import FakeAssetRepo, FakeMarketDataRepo, FakePortfolioRepo

D = Decimal
TODAY = date(2026, 9, 14)
YESTERDAY = TODAY - timedelta(days=1)


class _FixedDate(date):
    """Freezes query_portfolio.py's `date.today()` so returns/history math
    is deterministic regardless of the real wall-clock date — a plain
    subclass rather than a mock so `date(y, m, d)` construction elsewhere
    in that module keeps working unchanged."""

    @classmethod
    def today(cls):
        return TODAY


def _freeze_today(monkeypatch) -> None:
    import app.application.query_portfolio as qp

    monkeypatch.setattr(qp, "date", _FixedDate)


def _setup(base_currency="EUR"):
    asset_repo = FakeAssetRepo()
    portfolio_repo = FakePortfolioRepo(asset_repo)
    market_data_repo = FakeMarketDataRepo()
    portfolio_uc = QueryPortfolioUseCase(portfolio_repo, market_data_repo, asset_repo, base_currency)
    analytics_uc = QueryAnalyticsUseCase(portfolio_uc, asset_repo, portfolio_repo, market_data_repo)
    return asset_repo, portfolio_repo, market_data_repo, analytics_uc


def _hold(asset_repo, portfolio_repo, market_data_repo, symbol, currency, quantity, last_price, yesterday_price):
    asset = asset_repo.create(symbol, symbol, AssetClass.EQUITY, currency)
    account = portfolio_repo.get_or_create_account("test", symbol, "Test", currency, "api")
    portfolio_repo.upsert_holding(account.id, asset.id, D(quantity), D(last_price), currency, datetime.now(timezone.utc), "api")
    market_data_repo.upsert_quote(asset.id, D(last_price), None, currency, datetime.now(timezone.utc), "yfinance")
    market_data_repo.upsert_bars(
        asset.id,
        [{"date": YESTERDAY, "close": D(yesterday_price)}, {"date": TODAY, "close": D(last_price)}],
        "yfinance",
    )
    return asset, account


# --- get_returns -----------------------------------------------------------


def test_get_returns_weights_by_market_value(monkeypatch):
    _freeze_today(monkeypatch)
    asset_repo, portfolio_repo, market_data_repo, analytics_uc = _setup()
    _hold(asset_repo, portfolio_repo, market_data_repo, "A", "EUR", 1, 100, 100)  # flat: 0% return
    _hold(asset_repo, portfolio_repo, market_data_repo, "B", "EUR", 1, 330, 300)  # +10% return, same market value weight

    result = analytics_uc.get_returns(scope="portfolio", periods=["1d"])

    # equal market values (100 and 330 -> not quite equal; recompute expected)
    expected = (D(100) * D(0) + D(330) * (D(330) - D(300)) / D(300)) / (D(100) + D(330))
    assert result["1d"] == expected


def test_get_returns_scope_filters_to_one_asset(monkeypatch):
    _freeze_today(monkeypatch)
    asset_repo, portfolio_repo, market_data_repo, analytics_uc = _setup()
    asset_a, _ = _hold(asset_repo, portfolio_repo, market_data_repo, "A", "EUR", 1, 100, 100)
    _hold(asset_repo, portfolio_repo, market_data_repo, "B", "EUR", 1, 330, 300)

    result = analytics_uc.get_returns(scope=str(asset_a.id), periods=["1d"])

    assert result["1d"] == D(0)


def test_get_returns_defaults_to_the_five_standard_periods():
    _, _, _, analytics_uc = _setup()
    result = analytics_uc.get_returns()  # no holdings at all -> every period None
    assert set(result) == {"1d", "1w", "1m", "ytd", "1y"}


# --- get_concentration -------------------------------------------------


def test_get_concentration_computes_hhi_over_every_holding_and_lists_top_n(monkeypatch):
    _freeze_today(monkeypatch)
    asset_repo, portfolio_repo, market_data_repo, analytics_uc = _setup()
    _hold(asset_repo, portfolio_repo, market_data_repo, "SMALL", "EUR", 1, 100, 100)
    _hold(asset_repo, portfolio_repo, market_data_repo, "MID", "EUR", 1, 300, 300)
    _hold(asset_repo, portfolio_repo, market_data_repo, "BIG", "EUR", 1, 600, 600)

    result = analytics_uc.get_concentration(top_n=2)

    assert result.hhi == D("0.01") + D("0.09") + D("0.36")
    assert [h.symbol for h in result.holdings] == ["BIG", "MID"]


def test_get_concentration_empty_portfolio_is_zero():
    _, _, _, analytics_uc = _setup()
    result = analytics_uc.get_concentration()
    assert result.holdings == []
    assert result.hhi == D("0")


# --- get_currency_exposure ---------------------------------------------


def test_get_currency_exposure_breaks_down_by_currency(monkeypatch):
    _freeze_today(monkeypatch)
    asset_repo, portfolio_repo, market_data_repo, analytics_uc = _setup()
    _hold(asset_repo, portfolio_repo, market_data_repo, "EURSTOCK", "EUR", 1, 100, 100)
    _hold(asset_repo, portfolio_repo, market_data_repo, "USDSTOCK", "USD", 1, 100, 100)

    result = analytics_uc.get_currency_exposure()

    assert {s.label for s in result} == {"EUR", "USD"}


# --- get_drawdown ------------------------------------------------------


def test_get_drawdown_is_all_none_when_no_snapshot_history_exists_yet():
    _, _, _, analytics_uc = _setup()
    result = analytics_uc.get_drawdown(scope="portfolio", range="1Y")
    assert result.max_drawdown_pct is None
    assert result.current_drawdown_pct is None


# --- check_import_prices -----------------------------------------------


def test_check_import_prices_flags_and_labels_a_bad_trade():
    asset_repo, portfolio_repo, market_data_repo, analytics_uc = _setup()
    asset = asset_repo.create("FUND", "Some Fund", AssetClass.FUND, "EUR")
    account = portfolio_repo.get_or_create_account("myinvestor", "acc1", "Test", "EUR", "manual")
    market_data_repo.upsert_bars(asset.id, [{"date": date(2026, 9, 1), "close": D(100)}], "yfinance")
    good = Transaction(
        id=1, account_id=account.id, asset_id=asset.id, type=TransactionType.BUY, quantity=D(1), price=D(101),
        fees=D(0), currency="EUR", executed_at=datetime.now(timezone.utc), trade_date=date(2026, 9, 1),
        external_id="ok", source=AccountSource.MANUAL,
    )
    bad = Transaction(
        id=2, account_id=account.id, asset_id=asset.id, type=TransactionType.BUY, quantity=D(1), price=D(1000),
        fees=D(0), currency="EUR", executed_at=datetime.now(timezone.utc), trade_date=date(2026, 9, 1),
        external_id="bad", source=AccountSource.MANUAL,
    )
    portfolio_repo.add_transactions([good, bad])

    flagged = analytics_uc.check_import_prices(account_id=account.id)

    assert len(flagged) == 1
    assert flagged[0].transaction_id == bad.id
    assert flagged[0].symbol == "FUND"
