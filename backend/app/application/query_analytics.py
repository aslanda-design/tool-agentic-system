"""Read use cases behind `GET /api/analytics/*` and the `analytics` MCP
server (ai/mcp_servers/analytics/) — see
plans/agentic_asset_mapping_phase7_8.md Phase 8b. Composes
QueryPortfolioUseCase (for positions/history/allocation, already correct
and tested) with the pure functions in domain/analytics.py; adds no new
Postgres queries beyond PortfolioRepo.list_transactions for
check_import_prices."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.application.dto import AllocationSliceDTO, ConcentrationDTO, ConcentrationHoldingDTO
from app.application.query_portfolio import QueryPortfolioUseCase
from app.domain.analytics import (
    DrawdownResult,
    MispricedTradeDTO,
    flag_mispriced_trades,
    herfindahl_index,
    max_drawdown,
    weighted_return,
)
from app.ports.repositories import AssetRepo, MarketDataRepo, PortfolioRepo

ZERO = Decimal("0")
DEFAULT_RETURN_PERIODS = ["1d", "1w", "1m", "ytd", "1y"]


class QueryAnalyticsUseCase:
    def __init__(
        self,
        portfolio_use_case: QueryPortfolioUseCase,
        asset_repo: AssetRepo,
        portfolio_repo: PortfolioRepo,
        market_data_repo: MarketDataRepo,
    ) -> None:
        self.portfolio_use_case = portfolio_use_case
        self.asset_repo = asset_repo
        self.portfolio_repo = portfolio_repo
        self.market_data_repo = market_data_repo

    def get_returns(self, scope: str = "portfolio", periods: list[str] | None = None) -> dict[str, Decimal | None]:
        """scope is "portfolio" or an asset_id as a string (MCP tool args
        are JSON — an int-or-literal-string union is awkward there, so this
        accepts the string form and parses it; the REST route below takes a
        plain int query param instead)."""
        periods = periods or DEFAULT_RETURN_PERIODS
        positions = self.portfolio_use_case.list_positions()
        if scope != "portfolio":
            asset_id = int(scope)
            positions = [p for p in positions if p.asset_id == asset_id]
        return {period: weighted_return(positions, period) for period in periods}

    def get_concentration(self, top_n: int = 10) -> ConcentrationDTO:
        """HHI is computed over every held asset (not just the top N) —
        see herfindahl_index's docstring on why a truncated subset would
        understate concentration. Same asset held across multiple accounts
        is combined into one weight before scoring, since concentration
        risk doesn't care which account holds it."""
        positions = [p for p in self.portfolio_use_case.list_positions() if p.market_value is not None]
        total = sum((p.market_value for p in positions), ZERO)
        if total == ZERO:
            return ConcentrationDTO(holdings=[], hhi=ZERO)
        value_by_asset: dict[int, Decimal] = {}
        symbol_by_asset: dict[int, str] = {}
        for p in positions:
            value_by_asset[p.asset_id] = value_by_asset.get(p.asset_id, ZERO) + p.market_value
            symbol_by_asset[p.asset_id] = p.symbol
        hhi = herfindahl_index([value / total for value in value_by_asset.values()])
        ranked = sorted(value_by_asset.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        holdings = [
            ConcentrationHoldingDTO(symbol=symbol_by_asset[asset_id], weight=value / total)
            for asset_id, value in ranked
        ]
        return ConcentrationDTO(holdings=holdings, hhi=hhi)

    def get_currency_exposure(self) -> list[AllocationSliceDTO]:
        return self.portfolio_use_case.get_allocation(by="currency")

    def get_drawdown(self, scope: str = "portfolio", range: str = "1Y") -> DrawdownResult:
        asset_ids = None if scope == "portfolio" else [int(scope)]
        history = self.portfolio_use_case.get_history(range, asset_ids)
        return max_drawdown(history)

    def check_import_prices(
        self, account_id: int | None = None, since: date | None = None
    ) -> list[MispricedTradeDTO]:
        transactions = self.portfolio_repo.list_transactions(account_id=account_id, since=since)
        symbol_cache: dict[int, str] = {}

        def symbol_lookup(asset_id: int) -> str:
            if asset_id not in symbol_cache:
                asset = self.asset_repo.get(asset_id)
                symbol_cache[asset_id] = asset.symbol if asset else "?"
            return symbol_cache[asset_id]

        return flag_mispriced_trades(
            transactions, symbol_lookup, self.market_data_repo.get_price_on_or_before
        )
