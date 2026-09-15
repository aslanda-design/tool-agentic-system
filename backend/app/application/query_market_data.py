"""Read use cases behind the `market_data` MCP server
(ai/mcp_servers/market_data/ — see plans/agentic_asset_mapping_phase7_8.md
Phase 8a). Always reads MarketDataRepo (Postgres), never MarketDataPort
(live yfinance) — an agent's tool call must stay as cheap and rate-limit-safe
as any other request-time read, same rule GetAssetChartUseCase's docstring
explains for the one deliberate exception (the on-demand chart page)."""

from __future__ import annotations

from datetime import date

from app.domain.models import IdentifierScheme
from app.ports.repositories import AssetRepo, MarketDataRepo, PortfolioRepo

from .dto import (
    AssetSummaryDTO,
    DataFreshnessDTO,
    PricePointDTO,
    StalePositionDTO,
    UnmappedAssetDTO,
)

# A position's price data older than this counts as "stale" for
# get_data_freshness — a few days covers weekends/holidays without false
# positives; STALE_AFTER_DAYS in domain/listing_scoring.py (10 days, for a
# candidate LISTING'S own last trade) is a different, unrelated threshold.
STALE_AFTER_DAYS = 5


class QueryMarketDataUseCase:
    def __init__(self, asset_repo: AssetRepo, portfolio_repo: PortfolioRepo, market_data_repo: MarketDataRepo) -> None:
        self.asset_repo = asset_repo
        self.portfolio_repo = portfolio_repo
        self.market_data_repo = market_data_repo

    def get_asset(self, asset_id: int | None = None, symbol: str | None = None) -> AssetSummaryDTO | None:
        if (asset_id is None) == (symbol is None):
            raise ValueError("get_asset needs exactly one of asset_id or symbol")
        asset = (
            self.asset_repo.get(asset_id)
            if asset_id is not None
            else self.asset_repo.find_by_identifier(IdentifierScheme.YFINANCE, symbol)
            or self.asset_repo.get_by_symbol(symbol)
        )
        if asset is None:
            return None
        quote = self.market_data_repo.get_quote(asset.id)
        last_price = quote["price"] if quote else self.market_data_repo.get_price_on_or_before(asset.id, date.today())
        return AssetSummaryDTO(
            asset_id=asset.id,
            symbol=asset.symbol,
            name=asset.name,
            exchange=asset.exchange,
            currency=asset.currency,
            asset_class=asset.asset_class.value,
            isin=asset.isin,
            last_price=last_price,
            prev_close=quote.get("prev_close") if quote else None,
        )

    def get_price_history(
        self, asset_id: int, start: date, end: date, max_points: int = 120
    ) -> list[PricePointDTO]:
        """Persisted daily closes between start/end, downsampled to at most
        max_points with an even stride (no interpolation) — this feeds an
        LLM's context window, not a chart library, so a naive stride is
        enough and avoids a new dependency. Always keeps the most recent
        bar, so a caller can tell how fresh the data is even after
        downsampling."""
        bars = sorted(self.market_data_repo.get_bars(asset_id, start, end), key=lambda b: b["date"])
        if max_points > 0 and len(bars) > max_points:
            # Evenly spaced indices from 0 to len(bars)-1 inclusive (a
            # linspace, not a fixed stride) — guarantees both the first and
            # the last bar are kept without ever exceeding max_points, which
            # a plain `int(i * len(bars)/max_points)` stride can do by one.
            last = len(bars) - 1
            divisor = max(max_points - 1, 1)
            indices = sorted({round(i * last / divisor) for i in range(max_points)})
            bars = [bars[i] for i in indices]
        return [PricePointDTO(date=b["date"], close=b["close"]) for b in bars]

    def get_fx_rate(self, base: str, quote: str, on_date: date | None = None) -> dict | None:
        rate = self.market_data_repo.get_fx_rate(base, quote, on_date or date.today())
        if rate is None:
            return None
        return {"base": base.upper(), "quote": quote.upper(), "rate": rate, "on_date": on_date or date.today()}

    def get_data_freshness(self) -> DataFreshnessDTO:
        """Iterates currently-held assets (typically well under a hundred —
        see plans/agentic_asset_mapping_phase7_8.md §3.4) rather than adding
        a new aggregate repo method for a single caller."""
        today = date.today()
        held_asset_ids = sorted({row["asset_id"] for row in self.portfolio_repo.list_positions()})
        stale = []
        for asset_id in held_asset_ids:
            last_date = self.market_data_repo.latest_price_date(asset_id)
            if last_date is None or (today - last_date).days > STALE_AFTER_DAYS:
                asset = self.asset_repo.get(asset_id)
                if asset is not None:
                    stale.append(StalePositionDTO(asset_id=asset_id, symbol=asset.symbol, last_price_date=last_date))
        unmapped = [
            UnmappedAssetDTO(asset_id=a.id, symbol=a.symbol) for a in self.asset_repo.list_needing_mapping()
        ]
        return DataFreshnessDTO(as_of=today, stale_positions=stale, unmapped_assets=unmapped)
