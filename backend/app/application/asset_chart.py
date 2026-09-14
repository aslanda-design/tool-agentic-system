"""On-demand price bars for the asset detail page's chart — both the daily
range tabs and the 1h/1m granularities. Deliberately NOT persisted to
`prices`: unlike the Dashboard/positions (which need fast, reliable reads
backed by RefreshMarketDataUseCase — see refresh_market_data.py), a chart
you're actively looking at is an explicit, one-off request that can afford
to wait on yfinance directly, and always reflects live data instead of
whatever the last scheduled refresh happened to catch. `prices` stays the
source of truth for held-position valuation (query_portfolio.py,
query_asset.py's `position`) and the snapshot replay (build_snapshots.py),
which both need a persisted, queryable archive — this is the second
deliberate exception to "never call a market-data port from a request
handler" (see search_assets.py for the first, and its docstring for why).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.domain.models import IdentifierScheme
from app.ports.market_data import INTRADAY_GRANULARITIES, MarketDataPort
from app.ports.repositories import AssetRepo

RANGE_DAYS = {"1M": 30, "3M": 90, "1Y": 365, "5Y": 365 * 5}
EARLIEST_DATE = date(1990, 1, 1)  # matches refresh_market_data.EARLIEST_BACKFILL_DATE


@dataclass(slots=True)
class DailyBarDTO:
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(slots=True)
class IntradayBarDTO:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


class GetAssetChartUseCase:
    def __init__(self, asset_repo: AssetRepo, market_data: MarketDataPort) -> None:
        self.asset_repo = asset_repo
        self.market_data = market_data

    def _symbol(self, asset_id: int) -> str | None:
        return self.asset_repo.get_identifier_value(asset_id, IdentifierScheme.YFINANCE)

    def get_daily(self, asset_id: int, range_key: str = "1Y") -> list[DailyBarDTO]:
        symbol = self._symbol(asset_id)
        if symbol is None:
            return []  # not mapped to a market-data ticker yet
        today = date.today()
        range_key = range_key.upper()
        if range_key == "YTD":
            start = date(today.year, 1, 1)
        elif range_key == "ALL":
            start = EARLIEST_DATE
        else:
            start = today - timedelta(days=RANGE_DAYS.get(range_key, 365))
        bars = self.market_data.get_history(symbol, start, today)
        return [DailyBarDTO(date=b.date, open=b.open, high=b.high, low=b.low, close=b.close) for b in bars]

    def get_intraday(self, asset_id: int, granularity: str) -> list[IntradayBarDTO]:
        if granularity not in INTRADAY_GRANULARITIES:
            return []
        symbol = self._symbol(asset_id)
        if symbol is None:
            return []
        bars = self.market_data.get_intraday(symbol, granularity)
        return [
            IntradayBarDTO(timestamp=b.timestamp, open=b.open, high=b.high, low=b.low, close=b.close) for b in bars
        ]
