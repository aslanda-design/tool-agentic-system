"""Tests for FinnhubSentimentAdapter — monkeypatches httpx.get so this
never makes a real network call."""

from __future__ import annotations

import httpx

from app.adapters.market_intel.finnhub_adapter import FinnhubSentimentAdapter


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json


def test_get_sentiment_returns_none_without_api_key():
    assert FinnhubSentimentAdapter(api_key="").get_sentiment("AAPL") is None


def test_get_sentiment_parses_a_bullish_reading(monkeypatch):
    response = _FakeResponse(
        {
            "sentiment": {"bullishPercent": 0.7, "bearishPercent": 0.3},
            "buzz": {"articlesInLastWeek": 105},
        }
    )
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: response)

    result = FinnhubSentimentAdapter(api_key="key").get_sentiment("AAPL")

    assert result.source == "finnhub"
    assert result.label == "bullish"
    assert round(result.score, 4) == 0.4
    assert result.sample_size == 105


def test_get_sentiment_returns_none_when_symbol_not_covered(monkeypatch):
    response = _FakeResponse({"sentiment": {}, "buzz": {}})
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: response)

    assert FinnhubSentimentAdapter(api_key="key").get_sentiment("UNKNOWN") is None


def test_get_sentiment_returns_none_on_http_error(monkeypatch):
    response = _FakeResponse({}, status_code=500)
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: response)

    assert FinnhubSentimentAdapter(api_key="key").get_sentiment("AAPL") is None
