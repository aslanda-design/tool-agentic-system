"""Real bug found on the Interactive Brokers account's European-listed
holdings (CBUK.DE, EGLN.L, IB1T.DE, IUSQ.DE, NQSE.DE): yfinance reports the
still-open trading day's bar with NaN OHLC values rather than omitting the
row. That NaN flowed through `_dec` into a Decimal('NaN'), which crashed
GET /assets/{id}/history with a 500 the moment json.dumps hit it — breaking
the whole chart, not just that one day. get_history/get_intraday must drop
any bar with a NaN field instead of passing it through."""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from app.adapters.market_data import yfinance_adapter as module
from app.adapters.market_data.yfinance_adapter import YFinanceMarketData


class _FakeTicker:
    def __init__(self, fast_info: dict, hist: pd.DataFrame) -> None:
        self.fast_info = fast_info
        self._hist = hist

    def history(self, **kwargs):
        return self._hist


@pytest.fixture
def market_data() -> YFinanceMarketData:
    return YFinanceMarketData()


def test_get_history_drops_bar_with_nan_close(monkeypatch, market_data):
    hist = pd.DataFrame(
        {
            "Open": [4.06, 4.064],
            "High": [4.08, 4.072],
            "Low": [4.04, 4.048],
            "Close": [4.05, math.nan],  # today's still-open bar
            "Volume": [268043, 71950],
        },
        index=pd.to_datetime([date(2026, 9, 14), date(2026, 9, 15)]),
    )
    monkeypatch.setattr(module.yf, "Ticker", lambda symbol: _FakeTicker({"currency": "EUR"}, hist))

    bars = market_data.get_history("CBUK.DE", date(2026, 9, 1), date(2026, 9, 15))

    assert [b.date for b in bars] == [date(2026, 9, 14)]
    assert bars[0].close == Decimal("4.05")


def test_get_intraday_drops_bar_with_nan_field(monkeypatch, market_data):
    hist = pd.DataFrame(
        {
            "Open": [4.06, math.nan],
            "High": [4.08, 4.072],
            "Low": [4.04, 4.048],
            "Close": [4.05, 4.065],
            "Volume": [268043, 71950],
        },
        index=pd.to_datetime(["2026-09-15 10:00", "2026-09-15 11:00"]),
    )
    monkeypatch.setattr(module.yf, "Ticker", lambda symbol: _FakeTicker({"currency": "EUR"}, hist))

    bars = market_data.get_intraday("CBUK.DE", "1h")

    assert len(bars) == 1
    assert bars[0].close == Decimal("4.05")
