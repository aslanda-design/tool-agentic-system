"""Tests for the `market_data` MCP server (Phase 8a of
plans/agentic_asset_mapping_phase7_8.md — backend/ai/mcp_servers/market_data).
Same shape as test_mcp_security_server.py."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from decimal import Decimal

from ai.mcp_servers.market_data import server
from ai.mcp_servers.market_data.tools import get_asset as get_asset_tool
from ai.mcp_servers.market_data.tools import get_data_freshness as get_data_freshness_tool
from ai.mcp_servers.market_data.tools import get_fx_rate as get_fx_rate_tool
from ai.mcp_servers.market_data.tools import get_price_history as get_price_history_tool
from app.application.query_market_data import QueryMarketDataUseCase
from app.domain.models import AssetClass
from tests.fakes import FakeAssetRepo, FakeMarketDataRepo, FakePortfolioRepo

D = Decimal


def _use_case(asset_repo=None, portfolio_repo=None, market_data_repo=None) -> QueryMarketDataUseCase:
    asset_repo = asset_repo or FakeAssetRepo()
    return QueryMarketDataUseCase(
        asset_repo, portfolio_repo or FakePortfolioRepo(asset_repo), market_data_repo or FakeMarketDataRepo()
    )


def test_get_asset_returns_error_dict_when_not_found(monkeypatch):
    use_case = _use_case()
    monkeypatch.setattr(get_asset_tool, "build_query_market_data_use_case", lambda db: use_case)

    result = get_asset_tool.get_asset(asset_id=999)

    assert "error" in result


def test_get_asset_returns_error_dict_for_invalid_arguments(monkeypatch):
    use_case = _use_case()
    monkeypatch.setattr(get_asset_tool, "build_query_market_data_use_case", lambda db: use_case)

    assert "error" in get_asset_tool.get_asset()
    assert "error" in get_asset_tool.get_asset(asset_id=1, symbol="AAPL")


def test_get_asset_returns_jsonable_summary(monkeypatch):
    asset_repo = FakeAssetRepo()
    asset = asset_repo.create("AAPL", "Apple Inc.", AssetClass.EQUITY, "USD")
    market_data_repo = FakeMarketDataRepo()
    market_data_repo.upsert_quote(asset.id, D(200), D(198), "USD", datetime.now(timezone.utc), "yfinance")
    use_case = _use_case(asset_repo, market_data_repo=market_data_repo)
    monkeypatch.setattr(get_asset_tool, "build_query_market_data_use_case", lambda db: use_case)

    result = get_asset_tool.get_asset(asset_id=asset.id)

    assert result["symbol"] == "AAPL"
    assert result["last_price"] == 200.0


def test_get_price_history_downsamples(monkeypatch):
    asset_repo = FakeAssetRepo()
    asset = asset_repo.create("AAPL", "Apple", AssetClass.EQUITY, "USD")
    market_data_repo = FakeMarketDataRepo()
    bars = [{"date": date(2026, 1, 1), "close": D(100)}, {"date": date(2026, 1, 2), "close": D(101)}]
    market_data_repo.upsert_bars(asset.id, bars, "yfinance")
    use_case = _use_case(asset_repo, market_data_repo=market_data_repo)
    monkeypatch.setattr(get_price_history_tool, "build_query_market_data_use_case", lambda db: use_case)

    result = get_price_history_tool.get_price_history(asset.id, "2026-01-01", "2026-01-02", max_points=120)

    assert result == [{"date": "2026-01-01", "close": 100.0}, {"date": "2026-01-02", "close": 101.0}]


def test_get_fx_rate_returns_error_dict_for_unknown_pair(monkeypatch):
    use_case = _use_case()
    monkeypatch.setattr(get_fx_rate_tool, "build_query_market_data_use_case", lambda db: use_case)

    result = get_fx_rate_tool.get_fx_rate("USD", "JPY")

    assert "error" in result


def test_get_fx_rate_returns_known_rate(monkeypatch):
    market_data_repo = FakeMarketDataRepo()
    market_data_repo.upsert_fx_rates("USD", "EUR", {date(2026, 9, 1): D("0.92")})
    use_case = _use_case(market_data_repo=market_data_repo)
    monkeypatch.setattr(get_fx_rate_tool, "build_query_market_data_use_case", lambda db: use_case)

    result = get_fx_rate_tool.get_fx_rate("USD", "EUR", "2026-09-01")

    assert result == {"base": "USD", "quote": "EUR", "rate": 0.92, "on_date": "2026-09-01"}


def test_get_data_freshness_returns_jsonable_result(monkeypatch):
    use_case = _use_case()
    monkeypatch.setattr(get_data_freshness_tool, "build_query_market_data_use_case", lambda db: use_case)

    result = get_data_freshness_tool.get_data_freshness()

    assert result["stale_positions"] == []
    assert result["unmapped_assets"] == []
    assert "as_of" in result


def test_server_registers_all_four_tools():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == {"get_asset", "get_price_history", "get_fx_rate", "get_data_freshness"}


def test_server_call_tool_round_trips_through_the_registered_function(monkeypatch):
    use_case = _use_case()
    monkeypatch.setattr(get_data_freshness_tool, "build_query_market_data_use_case", lambda db: use_case)

    result = asyncio.run(server.mcp.call_tool("get_data_freshness", {}))

    assert result.is_error is False
    assert json.loads(result.content[0].text)["stale_positions"] == []
