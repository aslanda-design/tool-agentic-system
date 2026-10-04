"""Tests for YFinanceNewsAdapter — monkeypatches yfinance.Ticker so this
never makes a real network call (see backend/AGENTS.md's "never let a test
exercise a real external call" principle, same reasoning as
test_query_market_data.py's yfinance monkeypatching)."""

from __future__ import annotations

import yfinance as yf

from app.adapters.market_intel.yfinance_news_adapter import YFinanceNewsAdapter


class _FakeTicker:
    def __init__(self, news):
        self.news = news


def test_get_headlines_parses_nested_content_shape(monkeypatch):
    raw = [
        {
            "content": {
                "title": "Company beats earnings",
                "canonicalUrl": {"url": "https://example.com/a"},
                "provider": {"displayName": "Yahoo Finance"},
                "pubDate": "2026-09-17T12:00:00Z",
            }
        }
    ]
    monkeypatch.setattr(yf, "Ticker", lambda symbol: _FakeTicker(raw))

    result = YFinanceNewsAdapter().get_headlines("AAPL")

    assert len(result) == 1
    assert result[0].title == "Company beats earnings"
    assert result[0].url == "https://example.com/a"
    assert result[0].publisher == "Yahoo Finance"
    assert result[0].source == "yfinance"


def test_get_headlines_parses_flat_legacy_shape(monkeypatch):
    raw = [
        {
            "title": "Legacy shape headline",
            "link": "https://example.com/b",
            "publisher": "Reuters",
            "providerPublishTime": 1757937600,
        }
    ]
    monkeypatch.setattr(yf, "Ticker", lambda symbol: _FakeTicker(raw))

    result = YFinanceNewsAdapter().get_headlines("AAPL")

    assert len(result) == 1
    assert result[0].title == "Legacy shape headline"
    assert result[0].publisher == "Reuters"


def test_get_headlines_skips_unparseable_items(monkeypatch):
    raw = [
        {"content": {"title": None}},
        {"content": {"title": "Fine", "link": "u", "pubDate": "2026-09-17T00:00:00Z"}},
    ]
    monkeypatch.setattr(yf, "Ticker", lambda symbol: _FakeTicker(raw))

    result = YFinanceNewsAdapter().get_headlines("AAPL")

    assert len(result) == 1
    assert result[0].title == "Fine"


def test_get_headlines_returns_none_on_ticker_error(monkeypatch):
    def _raise(symbol):
        raise RuntimeError("boom")

    monkeypatch.setattr(yf, "Ticker", _raise)

    assert YFinanceNewsAdapter().get_headlines("AAPL") is None


def test_get_headlines_returns_none_when_news_is_none(monkeypatch):
    monkeypatch.setattr(yf, "Ticker", lambda symbol: _FakeTicker(None))

    assert YFinanceNewsAdapter().get_headlines("AAPL") is None
