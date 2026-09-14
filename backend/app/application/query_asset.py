from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.returns import unrealized_pnl, unrealized_pnl_pct
from app.ports.repositories import AssetRepo, MarketDataRepo, PortfolioRepo

from .dto import AssetDetailDTO, PositionDTO

ZERO = Decimal("0")


class QueryAssetUseCase:
    def __init__(
        self,
        asset_repo: AssetRepo,
        portfolio_repo: PortfolioRepo,
        market_data_repo: MarketDataRepo,
        base_currency: str,
    ) -> None:
        self.asset_repo = asset_repo
        self.portfolio_repo = portfolio_repo
        self.market_data_repo = market_data_repo
        self.base_currency = base_currency.upper()

    def get_detail(self, asset_id: int) -> AssetDetailDTO | None:
        asset = self.asset_repo.get(asset_id)
        if asset is None:
            return None
        quote = self.market_data_repo.get_quote(asset_id)
        # Fall back to the latest price bar so a quote outage doesn't blank out
        # a page that still has real historical price data to show.
        last_price = quote["price"] if quote else self.market_data_repo.get_price_on_or_before(asset_id, date.today())

        position = None
        rows = [r for r in self.portfolio_repo.list_positions() if r["asset_id"] == asset_id]
        if rows:
            row = rows[0]  # aggregate the simple case: one row per account; combine if several
            total_qty = sum((r["quantity"] for r in rows), ZERO)
            total_cost = sum((r["quantity"] * r["avg_cost_price"] for r in rows), ZERO)
            avg_cost_price = total_cost / total_qty if total_qty else ZERO
            cost_basis = total_qty * avg_cost_price
            if last_price is None:
                market_value = None
                pnl = None
                pnl_pct = None
            else:
                market_value = total_qty * last_price
                pnl = unrealized_pnl(market_value, cost_basis)
                pnl_pct = unrealized_pnl_pct(market_value, cost_basis)
            position = PositionDTO(
                asset_id=asset_id,
                symbol=asset.symbol,
                name=asset.name,
                account_id=row["account_id"],
                broker_key=row["broker_key"],
                quantity=total_qty,
                avg_cost_price=avg_cost_price,
                last_price=last_price,
                currency=asset.currency,
                market_value=market_value,
                cost_basis=cost_basis,
                unrealized_pnl=pnl,
                unrealized_pnl_pct=pnl_pct,
                returns={},
            )

        return AssetDetailDTO(
            asset_id=asset.id,
            symbol=asset.symbol,
            name=asset.name,
            exchange=asset.exchange,
            currency=asset.currency,
            asset_class=asset.asset_class.value,
            isin=asset.isin,
            last_price=last_price,
            prev_close=quote.get("prev_close") if quote else None,
            position=position,
        )
