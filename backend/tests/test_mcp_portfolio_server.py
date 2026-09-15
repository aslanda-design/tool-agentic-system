"""Tests for the `portfolio` MCP server (Phase 8a of
plans/agentic_asset_mapping_phase7_8.md — backend/ai/mcp_servers/portfolio).
Same two-layer shape as test_mcp_security_server.py: each tool called
directly against fakes (monkeypatching the app.container factory the tool
module imported), plus one protocol-level test against the registered
MCPServer."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from decimal import Decimal

from ai.mcp_servers.portfolio import server
from ai.mcp_servers.portfolio.tools import (
    get_allocation as get_allocation_tool,
)
from ai.mcp_servers.portfolio.tools import (
    get_portfolio_summary as get_portfolio_summary_tool,
)
from ai.mcp_servers.portfolio.tools import (
    get_value_history as get_value_history_tool,
)
from ai.mcp_servers.portfolio.tools import (
    list_accounts as list_accounts_tool,
)
from ai.mcp_servers.portfolio.tools import (
    list_positions as list_positions_tool,
)
from ai.mcp_servers.portfolio.tools import (
    list_transactions as list_transactions_tool,
)
from app.application.query_portfolio import QueryPortfolioUseCase
from app.domain.models import AccountSource, AssetClass, Transaction, TransactionType
from tests.fakes import FakeAssetRepo, FakeMarketDataRepo, FakePortfolioRepo

D = Decimal


def _use_case(asset_repo=None, portfolio_repo=None, market_data_repo=None) -> QueryPortfolioUseCase:
    asset_repo = asset_repo or FakeAssetRepo()
    return QueryPortfolioUseCase(
        portfolio_repo or FakePortfolioRepo(asset_repo), market_data_repo or FakeMarketDataRepo(), asset_repo, "EUR"
    )


def test_get_portfolio_summary_returns_jsonable_dto(monkeypatch):
    use_case = _use_case()
    monkeypatch.setattr(get_portfolio_summary_tool, "build_query_portfolio_use_case", lambda db: use_case)

    result = get_portfolio_summary_tool.get_portfolio_summary()

    assert result["currency"] == "EUR"
    assert isinstance(result["market_value"], float)


def test_list_positions_serializes_decimals(monkeypatch):
    asset_repo = FakeAssetRepo()
    asset = asset_repo.create("AAPL", "Apple", AssetClass.EQUITY, "EUR")
    portfolio_repo = FakePortfolioRepo(asset_repo)
    account = portfolio_repo.get_or_create_account("ibkr", "U1", "Test", "EUR", "api")
    portfolio_repo.upsert_holding(account.id, asset.id, D(1), D(100), "EUR", datetime.now(timezone.utc), "api")
    use_case = _use_case(asset_repo, portfolio_repo)
    monkeypatch.setattr(list_positions_tool, "build_query_portfolio_use_case", lambda db: use_case)

    result = list_positions_tool.list_positions()

    assert len(result) == 1
    assert result[0]["symbol"] == "AAPL"
    assert isinstance(result[0]["avg_cost_price"], float)


def test_get_allocation_groups_by_the_given_dimension(monkeypatch):
    asset_repo = FakeAssetRepo()
    asset = asset_repo.create("AAPL", "Apple", AssetClass.EQUITY, "USD")
    portfolio_repo = FakePortfolioRepo(asset_repo)
    account = portfolio_repo.get_or_create_account("ibkr", "U1", "Test", "USD", "api")
    portfolio_repo.upsert_holding(account.id, asset.id, D(1), D(100), "USD", datetime.now(timezone.utc), "api")
    market_data_repo = FakeMarketDataRepo()
    market_data_repo.upsert_quote(asset.id, D(150), None, "USD", datetime.now(timezone.utc), "yfinance")
    use_case = _use_case(asset_repo, portfolio_repo, market_data_repo)
    monkeypatch.setattr(get_allocation_tool, "build_query_portfolio_use_case", lambda db: use_case)

    result = get_allocation_tool.get_allocation(by="currency")

    assert result == [{"label": "USD", "market_value": 150.0, "weight": 1.0}]


def test_get_value_history_returns_a_list(monkeypatch):
    use_case = _use_case()
    monkeypatch.setattr(get_value_history_tool, "build_query_portfolio_use_case", lambda db: use_case)

    assert get_value_history_tool.get_value_history(range="1Y") == []


def test_list_transactions_orders_most_recent_first_and_respects_limit(monkeypatch):
    portfolio_repo = FakePortfolioRepo()
    account = portfolio_repo.get_or_create_account("ibkr", "U1", "Test", "EUR", "api")
    older = Transaction(
        id=None, account_id=account.id, asset_id=1, type=TransactionType.BUY, quantity=D(1), price=D(10),
        fees=D(0), currency="EUR", executed_at=datetime(2026, 1, 1, tzinfo=timezone.utc), trade_date=date(2026, 1, 1),
        external_id="a", source=AccountSource.API,
    )
    newer = Transaction(
        id=None, account_id=account.id, asset_id=1, type=TransactionType.BUY, quantity=D(1), price=D(10),
        fees=D(0), currency="EUR", executed_at=datetime(2026, 6, 1, tzinfo=timezone.utc), trade_date=date(2026, 6, 1),
        external_id="b", source=AccountSource.API,
    )
    portfolio_repo.add_transactions([older, newer])
    monkeypatch.setattr(list_transactions_tool, "portfolio_repo", lambda db: portfolio_repo)

    result = list_transactions_tool.list_transactions(limit=1)

    assert len(result) == 1
    assert result[0]["external_id"] == "b"  # the newer one


def test_list_accounts_returns_jsonable_accounts(monkeypatch):
    portfolio_repo = FakePortfolioRepo()
    portfolio_repo.get_or_create_account("myinvestor", "acc1", "MyInvestor", "EUR", "manual")
    monkeypatch.setattr(list_accounts_tool, "portfolio_repo", lambda db: portfolio_repo)

    result = list_accounts_tool.list_accounts()

    assert result == [{"id": 1, "broker_key": "myinvestor", "external_id": "acc1", "name": "MyInvestor", "currency": "EUR", "source": "manual"}]


def test_server_registers_all_six_tools():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == {
        "get_portfolio_summary",
        "list_positions",
        "get_allocation",
        "get_value_history",
        "list_transactions",
        "list_accounts",
    }


def test_server_call_tool_round_trips_through_the_registered_function(monkeypatch):
    use_case = _use_case()
    monkeypatch.setattr(get_portfolio_summary_tool, "build_query_portfolio_use_case", lambda db: use_case)

    result = asyncio.run(server.mcp.call_tool("get_portfolio_summary", {}))

    assert result.is_error is False
    # A bare `-> dict` return annotation doesn't get structured_content
    # populated by this SDK version (unlike `dict | None` — see
    # test_mcp_security_server.py's validate_listing case); the text
    # content is always present regardless, so assert against that.
    assert json.loads(result.content[0].text)["currency"] == "EUR"
