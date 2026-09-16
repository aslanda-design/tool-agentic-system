"""Backfills an asset's persisted daily bars from a secondary source
(Twelve Data) when there isn't enough history to calibrate a quant model —
see plans/quant_lab.md sections 1.4 and 5. Called ONLY from
RunQuantSimulationUseCase, itself only reachable from the explicit,
user-initiated POST /api/quant/simulate — the same "explicit action"
exception to "never call a market-data port from a request handler" that
search_assets.py and POST /api/assets/{id}/resolve already establish (see
backend/AGENTS.md's "Adding a market-data source" section)."""

from __future__ import annotations

from datetime import date, timedelta

from app.ports.market_data import HistoricalBarSourcePort
from app.ports.repositories import AssetRepo, MarketDataRepo

# A generous calendar-day multiplier over the trading-day count needed —
# covers weekends/holidays without being clever about a market calendar.
CALENDAR_DAYS_PER_TRADING_DAY = 2.0


class BackfillQuantHistoryUseCase:
    def __init__(
        self,
        asset_repo: AssetRepo,
        market_data_repo: MarketDataRepo,
        bar_source: HistoricalBarSourcePort,
    ) -> None:
        self.asset_repo = asset_repo
        self.market_data_repo = market_data_repo
        self.bar_source = bar_source

    def ensure_history(self, asset_id: int, min_days: int) -> None:
        """No-op if enough history is already persisted. Otherwise attempts
        a one-shot backfill from the secondary source; if that also comes
        back empty (no API key configured, rate-limited, unknown symbol),
        leaves state untouched — RunQuantSimulationUseCase reports
        "insufficient history" to the caller rather than failing silently."""
        today = date.today()
        window_start = today - timedelta(days=int(min_days * CALENDAR_DAYS_PER_TRADING_DAY))
        existing = self.market_data_repo.get_bars(asset_id, window_start, today)
        if len(existing) >= min_days:
            return

        asset = self.asset_repo.get(asset_id)
        if asset is None:
            return

        bars = self.bar_source.get_daily_history(
            asset.symbol, asset.exchange, window_start, today, expected_currency=asset.currency
        )
        if not bars:
            return

        bar_rows = [
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
        ]
        self.market_data_repo.upsert_bars(asset_id, bar_rows, source="twelve_data")
