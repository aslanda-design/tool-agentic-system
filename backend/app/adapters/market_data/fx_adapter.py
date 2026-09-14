"""FX rates via yfinance currency pair tickers (e.g. EURUSD=X). Same
unofficial-source caveats as yfinance_adapter.py."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

import yfinance as yf

from app.ports.market_data import FxRatePort

logger = logging.getLogger(__name__)


class YFinanceFxRates(FxRatePort):
    def get_daily_rates(self, base: str, quote: str, start: date, end: date) -> dict[date, Decimal]:
        base, quote = base.upper(), quote.upper()
        symbol = f"{base}{quote}=X"
        try:
            df = yf.Ticker(symbol).history(
                start=start, end=end + timedelta(days=1), interval="1d", auto_adjust=False
            )
        except Exception:
            logger.warning("yfinance: failed to fetch FX history for %s", symbol, exc_info=True)
            return {}
        if df is None or df.empty:
            return {}
        return {idx.date(): Decimal(str(row["Close"])) for idx, row in df.iterrows()}
