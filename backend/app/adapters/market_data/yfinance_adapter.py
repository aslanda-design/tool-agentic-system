"""yfinance-backed MarketDataPort. yfinance is an UNOFFICIAL scraper of
Yahoo Finance — no SLA, it throttles, and it silently breaks when Yahoo
changes their page. Every method below fails soft (skips the symbol/range
that errored) rather than raising, because a single bad ticker must never
abort a whole refresh cycle. Callers persist whatever comes back; see
application/refresh_market_data.py for the only place this is called from.
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from decimal import Decimal

import yfinance as yf

from app.domain.listings import ListingInfo
from app.ports.market_data import AssetSearchResult, BarData, IntradayBarData, MarketDataPort, QuoteData

logger = logging.getLogger(__name__)

# granularity -> (yfinance interval, lookback days). Yahoo rejects longer
# ranges for fine intervals: 1m tops out around 7 days, 60m around 730 —
# these are comfortably inside both limits.
_INTRADAY_INTERVALS: dict[str, tuple[str, int]] = {
    "1m": ("1m", 7),
    "1h": ("60m", 730),
}

# Currencies Yahoo reports quotes/bars in MINOR units (pence, cents) rather
# than the ISO currency's major unit -> (ISO code, divisor). London-listed
# lines are the common case ('GBp'/'GBX' = pence; divide by 100 to get GBP).
# Getting this wrong doesn't just mis-tag a currency — it overvalues the
# position by the divisor (100x for pence), since the raw number is used
# as-is as the price. See plans/agentic_asset_mapping.md bug B1.
_MINOR_UNIT_CURRENCIES: dict[str, tuple[str, Decimal]] = {
    "GBp": ("GBP", Decimal(100)),
    "GBX": ("GBP", Decimal(100)),
    "ZAc": ("ZAR", Decimal(100)),
    "ILA": ("ILS", Decimal(100)),
}


def _normalize_currency(raw: str | None) -> tuple[str | None, Decimal]:
    """(ISO currency code or None, divisor to apply to any price/amount
    yfinance reported alongside `raw`). Deliberately returns None rather
    than a fallback like 'USD' when yfinance gave us nothing — callers
    decide their own fallback (see AssetSearchResult.currency's docstring)
    rather than have a wrong guess baked in here and propagated silently."""
    if not raw:
        return None, Decimal(1)
    if raw in _MINOR_UNIT_CURRENCIES:
        return _MINOR_UNIT_CURRENCIES[raw]
    return raw.upper(), Decimal(1)


def _dec(value, divisor: Decimal = Decimal(1)) -> Decimal | None:
    if value is None:
        return None
    # yfinance/pandas represent a missing bar (e.g. the still-open trading
    # day's row, requested before that exchange's data is finalized) as NaN
    # rather than None. Decimal(str(nan)) happily produces Decimal('NaN')
    # instead of raising, which then blows up JSON encoding several layers
    # up (json.dumps rejects NaN) — treat it as missing here so callers skip
    # the bar the same way they already do for a genuinely absent value.
    if isinstance(value, float) and math.isnan(value):
        return None
    result = Decimal(str(value))
    return result / divisor if divisor != 1 else result


class YFinanceMarketData(MarketDataPort):
    def get_quotes(self, symbols: list[str]) -> dict[str, QuoteData]:
        results: dict[str, QuoteData] = {}
        for symbol in symbols:
            try:
                info = yf.Ticker(symbol).fast_info
                currency, divisor = _normalize_currency(info.get("currency"))
                price = _dec(info.get("lastPrice"), divisor)
                if price is None:
                    continue
                results[symbol] = QuoteData(
                    symbol=symbol,
                    price=price,
                    prev_close=_dec(info.get("previousClose"), divisor),
                    # Quotes always need a currency to be usable downstream
                    # (build_snapshots.py prices a holding in it) — USD is
                    # the least-wrong fallback when yfinance gave us none.
                    currency=currency or "USD",
                )
            except Exception:
                logger.warning("yfinance: failed to fetch quote for %s", symbol, exc_info=True)
        return results

    def get_history(self, symbol: str, start: date, end: date) -> list[BarData]:
        try:
            ticker = yf.Ticker(symbol)
            _, divisor = _normalize_currency(ticker.fast_info.get("currency"))
            df = ticker.history(start=start, end=end + timedelta(days=1), interval="1d", auto_adjust=False)
        except Exception:
            logger.warning("yfinance: failed to fetch history for %s", symbol, exc_info=True)
            return []
        if df is None or df.empty:
            return []
        bars = []
        for idx, row in df.iterrows():
            try:
                open_, high, low, close = (
                    _dec(row["Open"], divisor),
                    _dec(row["High"], divisor),
                    _dec(row["Low"], divisor),
                    _dec(row["Close"], divisor),
                )
                if None in (open_, high, low, close):
                    # Typically today's still-open bar — the exchange hasn't
                    # finalized it yet, so yfinance reports it as NaN rather
                    # than simply not including the row.
                    continue
                bars.append(
                    BarData(
                        date=idx.date(),
                        open=open_,
                        high=high,
                        low=low,
                        close=close,
                        adj_close=_dec(row.get("Adj Close", row["Close"]), divisor) or close,
                        volume=_dec(row.get("Volume", 0)) or Decimal(0),
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
            ticker = yf.Ticker(symbol)
            _, divisor = _normalize_currency(ticker.fast_info.get("currency"))
            df = ticker.history(period=f"{lookback_days}d", interval=interval, auto_adjust=False)
        except Exception:
            logger.warning("yfinance: failed to fetch intraday %s history for %s", granularity, symbol, exc_info=True)
            return []
        if df is None or df.empty:
            return []
        bars = []
        for idx, row in df.iterrows():
            try:
                open_, high, low, close = (
                    _dec(row["Open"], divisor),
                    _dec(row["High"], divisor),
                    _dec(row["Low"], divisor),
                    _dec(row["Close"], divisor),
                )
                if None in (open_, high, low, close):
                    continue
                bars.append(
                    IntradayBarData(
                        timestamp=idx.to_pydatetime(),
                        open=open_,
                        high=high,
                        low=low,
                        close=close,
                        volume=_dec(row.get("Volume", 0)) or Decimal(0),
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
            currency, _divisor = _normalize_currency(q.get("currency"))
            results.append(
                AssetSearchResult(
                    symbol=symbol,
                    name=q.get("shortname") or q.get("longname") or symbol,
                    exchange=q.get("exchange"),
                    asset_class=asset_class,
                    currency=currency,
                )
            )
        return results

    def get_listing_info(self, symbol: str) -> ListingInfo | None:
        try:
            ticker = yf.Ticker(symbol)
            fast_info = ticker.fast_info
            currency, divisor = _normalize_currency(fast_info.get("currency"))
            hist = ticker.history(period="1mo", interval="1d", auto_adjust=False)
        except Exception:
            logger.warning("yfinance: failed to fetch listing info for %s", symbol, exc_info=True)
            return None
        if hist is None or hist.empty:
            return None
        try:
            last_close = _dec(hist["Close"].iloc[-1], divisor)
            last_trade_date = hist.index[-1].date()
            avg_volume = _dec(hist["Volume"].tail(10).mean())
        except Exception:
            logger.warning("yfinance: unparseable history for %s", symbol, exc_info=True)
            return None

        name = symbol
        quote_type = None
        try:
            info = ticker.info  # slow, occasionally flaky — last resort, name/quote_type only
            short_name = info.get("shortName")
            long_name = info.get("longName")
            # For many mutual funds Yahoo sets shortName to the bare symbol
            # itself (no real short name) — longName still has the readable
            # one (e.g. "Fidelity S&P 500 Index EUR P Acc" for 0P0001CLDM.F),
            # so don't let a symbol-shaped shortName win via `or`.
            if short_name and short_name.strip().upper() != symbol.upper():
                name = short_name
            elif long_name:
                name = long_name
            else:
                name = short_name or symbol
            quote_type = info.get("quoteType")
        except Exception:
            logger.info("yfinance: .info unavailable for %s — using symbol as name", symbol)

        return ListingInfo(
            symbol=symbol,
            name=name,
            currency=currency,
            quote_type=quote_type,
            last_close=last_close,
            last_trade_date=last_trade_date,
            avg_volume=avg_volume,
        )
