"""Read use cases behind the dashboard. Always reads from Postgres
(PortfolioRepo / MarketDataRepo) — never touches a broker or market-data
port directly, so a page load never waits on an external API."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.domain.returns import price_return, unrealized_pnl, unrealized_pnl_pct
from app.ports.repositories import AssetRepo, MarketDataRepo, PortfolioRepo

from .dto import AllocationSliceDTO, HistoryPointDTO, PortfolioSummaryDTO, PositionDTO

ZERO = Decimal("0")
RETURN_PERIODS = {"1d": 1, "1w": 7, "1m": 30, "1y": 365}


class QueryPortfolioUseCase:
    def __init__(
        self,
        portfolio_repo: PortfolioRepo,
        market_data_repo: MarketDataRepo,
        asset_repo: AssetRepo,
        base_currency: str,
    ) -> None:
        self.portfolio_repo = portfolio_repo
        self.market_data_repo = market_data_repo
        self.asset_repo = asset_repo
        self.base_currency = base_currency.upper()

    def _rate_to_base(self, currency: str, on_date: date | None = None) -> Decimal:
        currency = currency.upper()
        if currency == self.base_currency:
            return Decimal("1")
        rate = self.market_data_repo.get_fx_rate(currency, self.base_currency, on_date or date.today())
        return rate if rate is not None else Decimal("1")

    def list_positions(self, account_id: int | None = None) -> list[PositionDTO]:
        rows = self.portfolio_repo.list_positions(account_id)
        today = date.today()
        results = []
        for row in rows:
            asset_id = row["asset_id"]
            currency = row["currency"]
            quote = self.market_data_repo.get_quote(asset_id)
            last_price = quote["price"] if quote else self.market_data_repo.get_price_on_or_before(asset_id, today)
            rate = self._rate_to_base(currency)
            quantity = row["quantity"]
            cost_basis = quantity * row["avg_cost_price"] * rate

            if last_price is None:
                # No quote and no price bar yet — usually means the asset still
                # needs a market-data ticker mapped. Never fabricate a -100% loss
                # by treating an unknown price as zero.
                market_value = None
                pnl = None
                pnl_pct = None
                returns: dict[str, Decimal | None] = {label: None for label in (*RETURN_PERIODS, "ytd")}
            else:
                market_value = quantity * last_price * rate
                pnl = unrealized_pnl(market_value, cost_basis)
                pnl_pct = unrealized_pnl_pct(market_value, cost_basis)
                returns = {}
                for label, days in RETURN_PERIODS.items():
                    past_price = self.market_data_repo.get_price_on_or_before(asset_id, today - timedelta(days=days))
                    returns[label] = price_return(past_price, last_price) if past_price else None
                jan1 = date(today.year, 1, 1)
                ytd_price = self.market_data_repo.get_price_on_or_before(asset_id, jan1)
                returns["ytd"] = price_return(ytd_price, last_price) if ytd_price else None

            results.append(
                PositionDTO(
                    asset_id=asset_id,
                    symbol=row["symbol"],
                    name=row["name"],
                    account_id=row["account_id"],
                    broker_key=row["broker_key"],
                    quantity=quantity,
                    avg_cost_price=row["avg_cost_price"],
                    last_price=last_price,
                    currency=currency,
                    market_value=market_value,
                    cost_basis=cost_basis,
                    unrealized_pnl=pnl,
                    unrealized_pnl_pct=pnl_pct,
                    returns=returns,
                )
            )
        return results

    def get_summary(self) -> PortfolioSummaryDTO:
        positions = self.list_positions()
        priced = [p for p in positions if p.market_value is not None]
        unpriced_count = len(positions) - len(priced)
        market_value = sum((p.market_value for p in priced), ZERO)
        priced_cost_basis = sum((p.cost_basis for p in priced), ZERO)
        all_cost_basis = sum((p.cost_basis for p in positions), ZERO)
        cash = sum(
            (
                bal["amount"] * self._rate_to_base(bal["currency"])
                for bal in self.portfolio_repo.list_cash_balances()
            ),
            ZERO,
        )
        # "Invested" is the cost basis of everything you currently hold — the
        # original buy cost — computed live rather than off the latest
        # snapshot so it never lags a mutation that hasn't been rebuilt yet.
        net_invested = all_cost_basis

        day_change = ZERO
        for dto in priced:
            quote = self.market_data_repo.get_quote(dto.asset_id)
            if not quote or quote.get("prev_close") is None:
                continue
            rate = self._rate_to_base(dto.currency)
            day_change += dto.quantity * (dto.last_price - quote["prev_close"]) * rate

        total_value = market_value + cash
        prev_value = total_value - day_change
        return PortfolioSummaryDTO(
            currency=self.base_currency,
            market_value=total_value,
            net_invested=net_invested,
            cash=cash,
            unrealized_pnl=unrealized_pnl(market_value, priced_cost_basis) if priced else None,
            unrealized_pnl_pct=unrealized_pnl_pct(market_value, priced_cost_basis) if priced else None,
            day_change=day_change,
            day_change_pct=(day_change / prev_value) if prev_value != ZERO else None,
            unpriced_count=unpriced_count,
        )

    def get_history(self, range_key: str = "1Y", asset_ids: list[int] | None = None) -> list[HistoryPointDTO]:
        today = date.today()
        start_by_range = {
            "1M": today - timedelta(days=30),
            "YTD": date(today.year, 1, 1),
            "1Y": today - timedelta(days=365),
            "ALL": date(2000, 1, 1),
        }
        start = start_by_range.get(range_key.upper(), start_by_range["1Y"])
        if asset_ids:
            rows = self.portfolio_repo.get_position_snapshots_totals(start, today, asset_ids)
            return [
                HistoryPointDTO(date=r["date"], market_value=r["market_value"], cost_basis=r["cost_basis"])
                for r in rows
            ]
        points = self.portfolio_repo.get_portfolio_snapshots(start, today)
        return [
            HistoryPointDTO(date=p.date, market_value=p.market_value, cost_basis=p.net_invested) for p in points
        ]

    def get_allocation(self, by: str = "asset_class") -> list[AllocationSliceDTO]:
        positions = [p for p in self.list_positions() if p.market_value is not None]
        total = sum((p.market_value for p in positions), ZERO)
        buckets: dict[str, Decimal] = {}
        for p in positions:
            if by == "account":
                label = p.broker_key
            elif by == "currency":
                label = p.currency
            else:
                asset = self.asset_repo.get(p.asset_id)
                label = asset.asset_class.value if asset else "OTHER"
            buckets[label] = buckets.get(label, ZERO) + p.market_value
        if total == ZERO:
            return []
        return [
            AllocationSliceDTO(label=label, market_value=value, weight=value / total)
            for label, value in sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)
        ]
