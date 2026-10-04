"""Tests for the `market_intel` MCP server (plans/market_intel.md). Same
shape as test_mcp_notes_server.py: tool-level tests monkeypatch the
container factory the tool calls, plus a couple of server-level round
trips through the real stdio-free `MCPServer.call_tool`."""

from __future__ import annotations

import asyncio
import json

from ai.mcp_servers.market_intel import server
from ai.mcp_servers.market_intel.tools import get_asset_news as get_asset_news_tool
from ai.mcp_servers.market_intel.tools import get_asset_sentiment as get_asset_sentiment_tool
from ai.mcp_servers.market_intel.tools import get_filing_events as get_filing_events_tool
from ai.mcp_servers.market_intel.tools import get_macro_headlines as get_macro_headlines_tool
from app.config import settings


class _FakeUseCase:
    def __init__(self, news=None, filings=None, sentiment=None, macro_headlines=None):
        self._news = news if news is not None else []
        self._filings = filings if filings is not None else []
        self._sentiment = (
            sentiment if sentiment is not None else {"agreement": "no_data", "sources": []}
        )
        self._macro_headlines = macro_headlines if macro_headlines is not None else []

    def get_news(self, symbol):
        return self._news

    def get_filings(self, symbol):
        return self._filings

    def get_sentiment(self, symbol):
        return self._sentiment

    def get_headlines(self, topic):
        return self._macro_headlines


def test_get_asset_news_returns_error_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "market_intel_enabled", False)

    result = get_asset_news_tool.get_asset_news("AAPL")

    assert "error" in result


def test_get_asset_news_returns_headlines_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "market_intel_enabled", True)
    monkeypatch.setattr(
        get_asset_news_tool,
        "build_get_asset_intel_use_case",
        lambda db: _FakeUseCase(news=[{"title": "T"}]),
    )

    result = get_asset_news_tool.get_asset_news("AAPL")

    assert result == {"symbol": "AAPL", "headlines": [{"title": "T"}]}


def test_get_filing_events_returns_error_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "market_intel_enabled", False)

    result = get_filing_events_tool.get_filing_events("AAPL")

    assert "error" in result


def test_get_filing_events_returns_filings_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "market_intel_enabled", True)
    monkeypatch.setattr(
        get_filing_events_tool,
        "build_get_asset_intel_use_case",
        lambda db: _FakeUseCase(filings=[{"form_type": "8-K"}]),
    )

    result = get_filing_events_tool.get_filing_events("AAPL")

    assert result == {"symbol": "AAPL", "filings": [{"form_type": "8-K"}]}


def test_get_asset_sentiment_returns_error_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "market_intel_enabled", False)

    result = get_asset_sentiment_tool.get_asset_sentiment("AAPL")

    assert "error" in result


def test_get_asset_sentiment_returns_fused_sentiment_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "market_intel_enabled", True)
    fused = {
        "agreement": "aligned",
        "sources": [{"source": "finnhub", "label": "bullish", "score": 0.4, "sample_size": 50}],
    }
    monkeypatch.setattr(
        get_asset_sentiment_tool,
        "build_get_asset_intel_use_case",
        lambda db: _FakeUseCase(sentiment=fused),
    )

    result = get_asset_sentiment_tool.get_asset_sentiment("AAPL")

    assert result == {"symbol": "AAPL", "sentiment": fused}


def test_get_macro_headlines_returns_error_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "market_intel_enabled", False)

    result = get_macro_headlines_tool.get_macro_headlines("European bank stocks")

    assert "error" in result


def test_get_macro_headlines_returns_error_for_a_malformed_topic(monkeypatch):
    monkeypatch.setattr(settings, "market_intel_enabled", True)

    result = get_macro_headlines_tool.get_macro_headlines("x" * 200)

    assert "error" in result


def test_get_macro_headlines_returns_headlines_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "market_intel_enabled", True)
    monkeypatch.setattr(
        get_macro_headlines_tool,
        "build_get_macro_intel_use_case",
        lambda db: _FakeUseCase(macro_headlines=[{"title": "T"}]),
    )

    result = get_macro_headlines_tool.get_macro_headlines("European bank stocks")

    assert result == {"topic": "European bank stocks", "headlines": [{"title": "T"}]}


def test_server_registers_all_four_tools():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == {
        "get_asset_news",
        "get_filing_events",
        "get_asset_sentiment",
        "get_macro_headlines",
    }


def test_server_call_tool_round_trips_through_the_registered_function(monkeypatch):
    monkeypatch.setattr(settings, "market_intel_enabled", True)
    monkeypatch.setattr(
        get_asset_news_tool,
        "build_get_asset_intel_use_case",
        lambda db: _FakeUseCase(news=[{"title": "T"}]),
    )

    result = asyncio.run(server.mcp.call_tool("get_asset_news", {"symbol": "AAPL"}))

    assert result.is_error is False
    assert json.loads(result.content[0].text)["headlines"] == [{"title": "T"}]
