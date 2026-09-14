"""Outbound ports for prices/quotes/FX. Implementations are unofficial,
rate-limited scrapers (yfinance) — nothing above this layer may call them
directly from a request path. Refresh use cases write results to Postgres;
the read API only ever queries the DB (see MarketDataRepo)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(slots=True)
class QuoteData:
    symbol: str
    price: Decimal
    prev_close: Decimal | None
    currency: str


@dataclass(slots=True)
class BarData:
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal
    volume: Decimal


@dataclass(slots=True)
class IntradayBarData:
    timestamp: datetime  # tz-aware
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


# The intraday granularities the app exposes in the UI. Each adapter decides
# its own lookback window per granularity (upstream data sources cap how far
# back fine-grained bars go) — see yfinance_adapter.py for yfinance's limits.
INTRADAY_GRANULARITIES = frozenset({"1m", "1h"})


@dataclass(slots=True)
class AssetSearchResult:
    symbol: str
    name: str
    exchange: str | None
    asset_class: str
    currency: str


class MarketDataPort(ABC):
    @abstractmethod
    def get_quotes(self, symbols: list[str]) -> dict[str, QuoteData]:
        """Batched latest-quote lookup. Missing symbols are simply absent
        from the result rather than raising."""

    @abstractmethod
    def get_history(self, symbol: str, start: date, end: date) -> list[BarData]:
        """Daily bars for a symbol between two dates (inclusive)."""

    @abstractmethod
    def get_intraday(self, symbol: str, granularity: str) -> list[IntradayBarData]:
        """Bars for one of INTRADAY_GRANULARITIES, covering whatever lookback
        window the adapter's upstream source actually supports for it —
        there's no `start`/`end` because fine-grained history is inherently
        short-lived, not a caller-chosen range."""

    @abstractmethod
    def search(self, query: str) -> list[AssetSearchResult]:
        """Free-text symbol/name search against the market data source."""


class FxRatePort(ABC):
    @abstractmethod
    def get_daily_rates(self, base: str, quote: str, start: date, end: date) -> dict[date, Decimal]:
        """Daily historical FX closes, base->quote, for the given range.
        Never a single 'current' rate — history must use the rate of the day."""
