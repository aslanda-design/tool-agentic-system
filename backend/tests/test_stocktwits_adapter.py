"""Tests for StockTwitsSentimentAdapter — monkeypatches httpx.get so this
never makes a real network call."""

from __future__ import annotations

import httpx

from app.adapters.market_intel.stocktwits_adapter import StockTwitsSentimentAdapter


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json


def _message(basic_sentiment):
    return {"entities": {"sentiment": {"basic": basic_sentiment} if basic_sentiment else None}}


def test_get_sentiment_computes_ratio_from_tagged_messages(monkeypatch):
    response = _FakeResponse(
        {
            "messages": [
                _message("Bullish"),
                _message("Bullish"),
                _message("Bearish"),
                _message(None),
            ]
        }
    )
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: response)

    result = StockTwitsSentimentAdapter().get_sentiment("AAPL")

    assert result.source == "stocktwits"
    assert result.sample_size == 3  # untagged message excluded
    assert round(result.score, 4) == round(1 / 3, 4)
    assert result.label == "bullish"


def test_get_sentiment_returns_none_when_nothing_tagged(monkeypatch):
    response = _FakeResponse({"messages": [_message(None), _message(None)]})
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: response)

    assert StockTwitsSentimentAdapter().get_sentiment("AAPL") is None


def test_get_sentiment_returns_none_on_unknown_symbol(monkeypatch):
    response = _FakeResponse({}, status_code=404)
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: response)

    assert StockTwitsSentimentAdapter().get_sentiment("NOTASYMBOL") is None


def test_get_sentiment_returns_none_on_network_error(monkeypatch):
    def _raise(url, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "get", _raise)

    assert StockTwitsSentimentAdapter().get_sentiment("AAPL") is None
