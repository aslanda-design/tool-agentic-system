"""yfinance-backed MarketDataPort. yfinance is an UNOFFICIAL scraper of
Yahoo Finance — no SLA, it throttles, and it silently breaks when Yahoo
changes their page. Every method below fails soft (skips the symbol/range
that errored) rather than raising, because a single bad ticker must never
abort a whole refresh cycle. Callers persist whatever comes back; see
application/refresh_market_data.py for the only place this is called from.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

import yfinance as yf

from app.ports.market_data import AssetSearchResult, BarData, IntradayBarData, MarketDataPort, QuoteData

logger = logging.getLogger(__name__)

# granularity -> (yfinance interval, lookback days). Yahoo rejects longer
# ranges for fine intervals: 1m tops out around 7 days, 60m around 730 —
# these are comfortably inside both limits.
_INTRADAY_INTERVALS: dict[str, tuple[str, int]] = {
    "1m": ("1m", 7),
    "1h": ("60m", 730),
}


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


class YFinanceMarketData(MarketDataPort):
    def get_quotes(self, symbols: list[str]) -> dict[str, QuoteData]:
        results: dict[str, QuoteData] = {}
        for symbol in symbols:
            try:
                info = yf.Ticker(symbol).fast_info
                price = _dec(info.get("lastPrice"))
                if price is None:
                    continue
                results[symbol] = QuoteData(
                    symbol=symbol,
                    price=price,
                    prev_close=_dec(info.get("previousClose")),
                    currency=(info.get("currency") or "USD").upper(),
                )
            except Exception:
                logger.warning("yfinance: failed to fetch quote for %s", symbol, exc_info=True)
        return results

    def get_history(self, symbol: str, start: date, end: date) -> list[BarData]:
        try:
            df = yf.Ticker(symbol).history(
                start=start, end=end + timedelta(days=1), interval="1d", auto_adjust=False
            )
        except Exception:
            logger.warning("yfinance: failed to fetch history for %s", symbol, exc_info=True)
            return []
        if df is None or df.empty:
            return []
        bars = []
        for idx, row in df.iterrows():
            try:
                bars.append(
                    BarData(
                        date=idx.date(),
                        open=_dec(row["Open"]),
                        high=_dec(row["High"]),
                        low=_dec(row["Low"]),
                        close=_dec(row["Close"]),
                        adj_close=_dec(row.get("Adj Close", row["Close"])),
                        volume=_dec(row.get("Volume", 0)) or Decimal("0"),
                    )
                )
            except Exception:
                continue
        return bars

    def get_intraday(self, symbol: str, granularity: str) -> list[IntradayBarData]:
        mapping = _INTRADAY_INTERVALS.get(granularity)
        if mapping is None:
            return []
        interval, lookback_days = mapping
        try:
            df = yf.Ticker(symbol).history(period=f"{lookback_days}d", interval=interval, auto_adjust=False)
        except Exception:
            logger.warning("yfinance: failed to fetch intraday %s history for %s", granularity, symbol, exc_info=True)
            return []
        if df is None or df.empty:
            return []
        bars = []
        for idx, row in df.iterrows():
            try:
                bars.append(
                    IntradayBarData(
                        timestamp=idx.to_pydatetime(),
                        open=_dec(row["Open"]),
                        high=_dec(row["High"]),
                        low=_dec(row["Low"]),
                        close=_dec(row["Close"]),
                        volume=_dec(row.get("Volume", 0)) or Decimal("0"),
                    )
                )
            except Exception:
                continue
        return bars

    def search(self, query: str) -> list[AssetSearchResult]:
        try:
            search = yf.Search(query, max_results=10)
            quotes = search.quotes
        except Exception:
            logger.warning("yfinance: search failed for %r", query, exc_info=True)
            return []
        results = []
        for q in quotes or []:
            symbol = q.get("symbol")
            if not symbol:
                continue
            quote_type = (q.get("quoteType") or "EQUITY").upper()
            asset_class = {
                "EQUITY": "EQUITY",
                "ETF": "ETF",
                "MUTUALFUND": "FUND",
                "CRYPTOCURRENCY": "CRYPTO",
            }.get(quote_type, "OTHER")
            results.append(
                AssetSearchResult(
                    symbol=symbol,
                    name=q.get("shortname") or q.get("longname") or symbol,
                    exchange=q.get("exchange"),
                    asset_class=asset_class,
                    currency=(q.get("currency") or "USD").upper(),
                )
            )
        return results
