"""Tests for the `analytics` MCP server (Phase 8b of
plans/agentic_asset_mapping_phase7_8.md — backend/ai/mcp_servers/analytics).
Same shape as test_mcp_security_server.py — the underlying math is already
covered by test_analytics.py/test_query_analytics.py, so these focus on the
tools being correctly wired and JSON-safe."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from decimal import Decimal

from ai.mcp_servers.analytics import server
from ai.mcp_servers.analytics.tools import check_import_prices as check_import_prices_tool
from ai.mcp_servers.analytics.tools import get_concentration as get_concentration_tool
from ai.mcp_servers.analytics.tools import get_currency_exposure as get_currency_exposure_tool
from ai.mcp_servers.analytics.tools import get_drawdown as get_drawdown_tool
from ai.mcp_servers.analytics.tools import get_returns as get_returns_tool
from app.application.query_analytics import QueryAnalyticsUseCase
from app.application.query_portfolio import QueryPortfolioUseCase
from app.domain.models import AccountSource, AssetClass, Transaction, TransactionType
from tests.fakes import FakeAssetRepo, FakeMarketDataRepo, FakePortfolioRepo

D = Decimal


def _use_case(asset_repo=None, portfolio_repo=None, market_data_repo=None) -> QueryAnalyticsUseCase:
    asset_repo = asset_repo or FakeAssetRepo()
    portfolio_repo = portfolio_repo or FakePortfolioRepo(asset_repo)
    market_data_repo = market_data_repo or FakeMarketDataRepo()
    portfolio_uc = QueryPortfolioUseCase(portfolio_repo, market_data_repo, asset_repo, "EUR")
    return QueryAnalyticsUseCase(portfolio_uc, asset_repo, portfolio_repo, market_data_repo)


def test_get_returns_defaults_to_five_periods(monkeypatch):
    use_case = _use_case()
    monkeypatch.setattr(get_returns_tool, "build_query_analytics_use_case", lambda db: use_case)

    result = get_returns_tool.get_returns()

    assert set(result) == {"1d", "1w", "1m", "ytd", "1y"}
    assert all(v is None for v in result.values())  # no holdings


def test_get_concentration_returns_jsonable_result(monkeypatch):
    asset_repo = FakeAssetRepo()
    asset = asset_repo.create("AAPL", "Apple", AssetClass.EQUITY, "EUR")
    portfolio_repo = FakePortfolioRepo(asset_repo)
    account = portfolio_repo.get_or_create_account("ibkr", "U1", "Test", "EUR", "api")
    portfolio_repo.upsert_holding(account.id, asset.id, D(1), D(100), "EUR", datetime.now(timezone.utc), "api")
    market_data_repo = FakeMarketDataRepo()
    market_data_repo.upsert_quote(asset.id, D(100), None, "EUR", datetime.now(timezone.utc), "yfinance")
    use_case = _use_case(asset_repo, portfolio_repo, market_data_repo)
    monkeypatch.setattr(get_concentration_tool, "build_query_analytics_use_case", lambda db: use_case)

    result = get_concentration_tool.get_concentration(top_n=5)

    assert result == {"holdings": [{"symbol": "AAPL", "weight": 1.0}], "hhi": 1.0}


def test_get_currency_exposure_returns_a_list(monkeypatch):
    use_case = _use_case()
    monkeypatch.setattr(get_currency_exposure_tool, "build_query_analytics_use_case", lambda db: use_case)

    assert get_currency_exposure_tool.get_currency_exposure() == []


def test_get_drawdown_handles_no_history_gracefully(monkeypatch):
    use_case = _use_case()
    monkeypatch.setattr(get_drawdown_tool, "build_query_analytics_use_case", lambda db: use_case)

    result = get_drawdown_tool.get_drawdown()

    assert result["max_drawdown_pct"] is None
    assert result["current_drawdown_pct"] is None


def test_check_import_prices_flags_a_bad_trade(monkeypatch):
    asset_repo = FakeAssetRepo()
    asset = asset_repo.create("FUND", "Some Fund", AssetClass.FUND, "EUR")
    portfolio_repo = FakePortfolioRepo(asset_repo)
    account = portfolio_repo.get_or_create_account("myinvestor", "acc1", "Test", "EUR", "manual")
    market_data_repo = FakeMarketDataRepo()
    market_data_repo.upsert_bars(asset.id, [{"date": date(2026, 9, 1), "close": D(100)}], "yfinance")
    bad = Transaction(
        id=1, account_id=account.id, asset_id=asset.id, type=TransactionType.BUY, quantity=D(1), price=D(1000),
        fees=D(0), currency="EUR", executed_at=datetime.now(timezone.utc), trade_date=date(2026, 9, 1),
        external_id="bad", source=AccountSource.MANUAL,
    )
    portfolio_repo.add_transactions([bad])
    use_case = _use_case(asset_repo, portfolio_repo, market_data_repo)
    monkeypatch.setattr(check_import_prices_tool, "build_query_analytics_use_case", lambda db: use_case)

    result = check_import_prices_tool.check_import_prices()

    assert len(result) == 1
    assert result[0]["symbol"] == "FUND"
    assert result[0]["pct_diff"] == 9.0


def test_check_import_prices_empty_when_nothing_looks_wrong(monkeypatch):
    use_case = _use_case()
    monkeypatch.setattr(check_import_prices_tool, "build_query_analytics_use_case", lambda db: use_case)

    assert check_import_prices_tool.check_import_prices() == []


def test_server_registers_all_five_tools():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == {
        "get_returns",
        "get_concentration",
        "get_currency_exposure",
        "get_drawdown",
        "check_import_prices",
    }


def test_server_call_tool_round_trips_through_the_registered_function(monkeypatch):
    use_case = _use_case()
    monkeypatch.setattr(get_returns_tool, "build_query_analytics_use_case", lambda db: use_case)

    result = asyncio.run(server.mcp.call_tool("get_returns", {}))

    assert result.is_error is False
    assert set(json.loads(result.content[0].text)) == {"1d", "1w", "1m", "ytd", "1y"}
