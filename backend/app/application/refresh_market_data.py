"""Pulls quotes/bars/FX from the market data source and persists them.
This is the ONLY place yfinance is called from — request handlers always
read the DB (MarketDataRepo), never the port directly, so a slow or
rate-limited upstream never blocks a page load."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.domain.models import IdentifierScheme
from app.ports.market_data import FxRatePort, MarketDataPort
from app.ports.repositories import AssetRepo, MarketDataRepo

# First-time backfill reaches back this far; yfinance simply returns whatever
# it actually has (IPO date, ETF inception, etc.) — there's no "too early"
# error, so asking for the maximum plausible range gets "as old as possible"
# for free instead of hiding data behind an arbitrary lookback window.
EARLIEST_BACKFILL_DATE = date(1990, 1, 1)


class RefreshMarketDataUseCase:
    def __init__(
        self,
        market_data: MarketDataPort,
        fx: FxRatePort,
        market_data_repo: MarketDataRepo,
        asset_repo: AssetRepo,
        base_currency: str,
    ) -> None:
        self.market_data = market_data
        self.fx = fx
        self.market_data_repo = market_data_repo
        self.asset_repo = asset_repo
        self.base_currency = base_currency.upper()

    def _mapped_assets(self) -> list[tuple[int, str, str]]:
        """(asset_id, yfinance_symbol, native_currency) for every resolved asset."""
        pairs = self.asset_repo.list_assets_with_scheme(IdentifierScheme.YFINANCE)
        return [(asset.id, yf_symbol, asset.currency) for asset, yf_symbol in pairs]

    def refresh_quotes(self) -> int:
        mapped = self._mapped_assets()
        if not mapped:
            return 0
        symbol_to_asset_id = {yf_symbol: asset_id for asset_id, yf_symbol, _ in mapped}
        quotes = self.market_data.get_quotes(list(symbol_to_asset_id))
        now = datetime.now(timezone.utc)
        for symbol, quote in quotes.items():
            self.market_data_repo.upsert_quote(
                symbol_to_asset_id[symbol], quote.price, quote.prev_close, quote.currency, now, "yfinance"
            )
        return len(quotes)

    def refresh_history(self, today: date | None = None) -> int:
        """Backfills full history for newly-mapped assets, and tops up
        everyone else from their last known price to today."""
        today = today or date.today()
        updated = 0
        for asset_id, yf_symbol, _currency in self._mapped_assets():
            last = self.market_data_repo.latest_price_date(asset_id)
            start = EARLIEST_BACKFILL_DATE if last is None else last + timedelta(days=1)
            if start > today:
                continue
            bars = self.market_data.get_history(yf_symbol, start, today)
            if not bars:
                continue
            self.market_data_repo.upsert_bars(
                asset_id,
                [
                    {
                        "date": b.date,
                        "open": b.open,
                        "high": b.high,
                        "low": b.low,
                        "close": b.close,
                        "adj_close": b.adj_close,
                        "volume": b.volume,
                    }
                    for b in bars
                ],
                "yfinance",
            )
            updated += 1
        return updated

    def refresh_fx(self, today: date | None = None) -> int:
        today = today or date.today()
        pairs = self.market_data_repo.currency_pairs_in_use(self.base_currency)
        updated = 0
        for currency in pairs:
            if currency == self.base_currency:
                continue
            rates = self.fx.get_daily_rates(currency, self.base_currency, EARLIEST_BACKFILL_DATE, today)
            if not rates:
                continue
            self.market_data_repo.upsert_fx_rates(currency, self.base_currency, rates)
            updated += 1
        return updated

    def refresh_all(self) -> dict[str, int]:
        return {
            "quotes": self.refresh_quotes(),
            "history": self.refresh_history(),
            "fx_pairs": self.refresh_fx(),
        }
