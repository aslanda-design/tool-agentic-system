"""Tests for YFinanceMarketData.get_listing_info's name selection — real bug
found while verifying the security resolver against real MyInvestor mutual
funds: Yahoo sets `shortName` to the bare symbol itself for many of them
(e.g. "0P0001CLDM.F"), so a plain `shortName or longName` picks the useless
one even though `longName` has the actual readable name ("Fidelity S&P 500
Index EUR P Acc"). Monkeypatches yf.Ticker so no real network call is made."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.adapters.market_data import yfinance_adapter as module
from app.adapters.market_data.yfinance_adapter import YFinanceMarketData


class _FakeTicker:
    def __init__(self, fast_info: dict, info: dict, hist: pd.DataFrame) -> None:
        self.fast_info = fast_info
        self.info = info
        self._hist = hist

    def history(self, **kwargs):
        return self._hist


def _hist() -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": [10.0, 10.5], "Volume": [100, 200]},
        index=pd.to_datetime([date(2026, 9, 10), date(2026, 9, 11)]),
    )


@pytest.fixture
def market_data() -> YFinanceMarketData:
    return YFinanceMarketData()


def test_prefers_long_name_when_short_name_is_just_the_symbol(monkeypatch, market_data):
    fake = _FakeTicker(
        fast_info={"currency": "EUR"},
        info={"shortName": "0P0001CLDM.F", "longName": "Fidelity S&P 500 Index EUR P Acc", "quoteType": "MUTUALFUND"},
        hist=_hist(),
    )
    monkeypatch.setattr(module.yf, "Ticker", lambda symbol: fake)

    result = market_data.get_listing_info("0P0001CLDM.F")

    assert result is not None
    assert result.name == "Fidelity S&P 500 Index EUR P Acc"


def test_prefers_a_real_short_name_over_long_name(monkeypatch, market_data):
    fake = _FakeTicker(
        fast_info={"currency": "USD"},
        info={"shortName": "Apple Inc.", "longName": "Apple Inc", "quoteType": "EQUITY"},
        hist=_hist(),
    )
    monkeypatch.setattr(module.yf, "Ticker", lambda symbol: fake)

    result = market_data.get_listing_info("AAPL")

    assert result.name == "Apple Inc."


def test_falls_back_to_the_symbol_when_neither_name_is_useful(monkeypatch, market_data):
    fake = _FakeTicker(
        fast_info={"currency": "EUR"},
        info={"shortName": "WEIRD.F", "quoteType": "MUTUALFUND"},
        hist=_hist(),
    )
    monkeypatch.setattr(module.yf, "Ticker", lambda symbol: fake)

    result = market_data.get_listing_info("WEIRD.F")

    assert result.name == "WEIRD.F"


def test_falls_back_to_the_symbol_when_info_lookup_raises(monkeypatch, market_data):
    class _RaisingInfoTicker(_FakeTicker):
        @property
        def info(self):
            raise RuntimeError("Yahoo is flaky")

        @info.setter
        def info(self, value):
            pass

    fake = _RaisingInfoTicker(fast_info={"currency": "EUR"}, info={}, hist=_hist())
    monkeypatch.setattr(module.yf, "Ticker", lambda symbol: fake)

    result = market_data.get_listing_info("BROKEN.F")

    assert result is not None
    assert result.name == "BROKEN.F"
