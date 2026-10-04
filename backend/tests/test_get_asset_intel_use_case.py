"""Tests for GetAssetIntelUseCase (app/application/get_asset_intel.py) — the
cache-read-through + fail-soft-fallback logic from plans/market_intel.md
sections 3.1/6, exercised against fakes only (no network, no DB)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.get_asset_intel import GetAssetIntelUseCase
from app.ports.market_intel import FilingEvent, NewsItem, SentimentReading
from tests.fakes import FakeAssetIntelCacheRepo, FakeFilingsPort, FakeNewsPort, FakeSentimentPort


def _use_case(
    news_by_symbol=None, filings_by_symbol=None, sentiment_ports=None, cache=None, cache_minutes=30
):
    return GetAssetIntelUseCase(
        FakeNewsPort(news_by_symbol),
        FakeFilingsPort(filings_by_symbol),
        sentiment_ports if sentiment_ports is not None else [],
        cache or FakeAssetIntelCacheRepo(),
        cache_minutes,
    )


def test_get_news_fetches_and_caches_on_first_call():
    item = NewsItem(
        title="T", publisher="P", url="u", published_at=datetime.now(UTC), source="yfinance"
    )
    cache = FakeAssetIntelCacheRepo()
    use_case = _use_case(news_by_symbol={"AAPL": [item]}, cache=cache)

    result = use_case.get_news("AAPL")

    assert result == [
        {
            "title": "T",
            "publisher": "P",
            "url": "u",
            "published_at": item.published_at.isoformat(),
            "source": "yfinance",
        }
    ]
    assert cache.get("AAPL", "news").payload == result


def test_get_news_serves_from_cache_when_fresh(monkeypatch):
    cache = FakeAssetIntelCacheRepo()
    cache.put("AAPL", "news", [{"title": "cached"}])
    news_port_calls = []

    class TrackingNewsPort:
        def get_headlines(self, symbol):
            news_port_calls.append(symbol)
            return []

    use_case = GetAssetIntelUseCase(
        TrackingNewsPort(), FakeFilingsPort(), [], cache, cache_minutes=30
    )

    result = use_case.get_news("AAPL")

    assert result == [{"title": "cached"}]
    assert news_port_calls == []  # never hit the live source


def test_get_news_refetches_when_cache_is_stale():
    cache = FakeAssetIntelCacheRepo()
    cache.put("AAPL", "news", [{"title": "stale"}])
    cache._rows[("AAPL", "news")].fetched_at = datetime.now(UTC) - timedelta(hours=2)
    item = NewsItem(
        title="fresh", publisher="P", url="u", published_at=datetime.now(UTC), source="yfinance"
    )

    use_case = _use_case(news_by_symbol={"AAPL": [item]}, cache=cache, cache_minutes=30)

    result = use_case.get_news("AAPL")

    assert result[0]["title"] == "fresh"


def test_get_news_falls_back_to_stale_cache_when_source_fails():
    cache = FakeAssetIntelCacheRepo()
    cache.put("AAPL", "news", [{"title": "stale but better than nothing"}])
    cache._rows[("AAPL", "news")].fetched_at = datetime.now(UTC) - timedelta(hours=2)

    use_case = _use_case(news_by_symbol={"AAPL": None}, cache=cache, cache_minutes=30)

    result = use_case.get_news("AAPL")

    assert result == [{"title": "stale but better than nothing"}]


def test_get_news_returns_empty_when_source_fails_and_nothing_cached():
    use_case = _use_case(news_by_symbol={"AAPL": None})

    assert use_case.get_news("AAPL") == []


def test_get_filings_fetches_and_caches():
    filing = FilingEvent(form_type="8-K", filed_at=datetime.now(UTC).date(), title="T", url="u")
    use_case = _use_case(filings_by_symbol={"AAPL": [filing]})

    result = use_case.get_filings("AAPL")

    assert result == [
        {
            "form_type": "8-K",
            "filed_at": filing.filed_at.isoformat(),
            "title": "T",
            "url": "u",
            "source": "sec_edgar",
        }
    ]


def test_get_filings_empty_list_is_not_treated_as_a_failure():
    """A [] from FilingsPort (e.g. a non-US listing with no CIK) is a real
    answer, not a fetch failure — it should still be cached, not silently
    dropped in favor of a nonexistent stale entry."""
    cache = FakeAssetIntelCacheRepo()
    use_case = _use_case(filings_by_symbol={"VWCE.DE": []}, cache=cache)

    result = use_case.get_filings("VWCE.DE")

    assert result == []


def test_get_sentiment_fuses_multiple_sources_and_caches():
    finnhub = SentimentReading(source="finnhub", label="bullish", score=0.4, sample_size=50)
    stocktwits = SentimentReading(source="stocktwits", label="bullish", score=0.6, sample_size=30)
    cache = FakeAssetIntelCacheRepo()
    use_case = _use_case(
        sentiment_ports=[
            FakeSentimentPort({"AAPL": finnhub}),
            FakeSentimentPort({"AAPL": stocktwits}),
        ],
        cache=cache,
    )

    result = use_case.get_sentiment("AAPL")

    assert result["agreement"] == "aligned"
    assert len(result["sources"]) == 2
    assert cache.get("AAPL", "sentiment").payload == result


def test_get_sentiment_skips_sources_that_return_none():
    finnhub = SentimentReading(source="finnhub", label="bullish", score=0.4, sample_size=50)
    use_case = _use_case(
        sentiment_ports=[
            FakeSentimentPort({"AAPL": finnhub}),
            FakeSentimentPort({}),
        ],  # stocktwits has nothing
    )

    result = use_case.get_sentiment("AAPL")

    assert result["agreement"] == "single_source"
    assert len(result["sources"]) == 1


def test_get_sentiment_falls_back_to_stale_cache_when_every_source_fails():
    cache = FakeAssetIntelCacheRepo()
    cache.put(
        "AAPL", "sentiment", {"agreement": "single_source", "sources": [{"source": "finnhub"}]}
    )
    cache._rows[("AAPL", "sentiment")].fetched_at = datetime.now(UTC) - timedelta(hours=2)
    use_case = _use_case(
        sentiment_ports=[FakeSentimentPort({}), FakeSentimentPort({})], cache=cache
    )

    result = use_case.get_sentiment("AAPL")

    assert result == {"agreement": "single_source", "sources": [{"source": "finnhub"}]}


def test_get_sentiment_returns_no_data_when_nothing_cached_and_every_source_fails():
    use_case = _use_case(sentiment_ports=[FakeSentimentPort({}), FakeSentimentPort({})])

    assert use_case.get_sentiment("AAPL") == {"agreement": "no_data", "sources": []}


def test_get_all_combines_news_filings_and_sentiment():
    news_item = NewsItem(
        title="N", publisher="P", url="u1", published_at=datetime.now(UTC), source="yfinance"
    )
    filing = FilingEvent(form_type="8-K", filed_at=datetime.now(UTC).date(), title="F", url="u2")
    reading = SentimentReading(source="finnhub", label="bullish", score=0.5, sample_size=10)
    use_case = _use_case(
        news_by_symbol={"AAPL": [news_item]},
        filings_by_symbol={"AAPL": [filing]},
        sentiment_ports=[FakeSentimentPort({"AAPL": reading})],
    )

    result = use_case.get_all("AAPL")

    assert result["news"][0]["title"] == "N"
    assert result["filings"][0]["title"] == "F"
    assert result["sentiment"]["agreement"] == "single_source"


def test_get_all_isolates_a_failure_in_one_piece_from_the_others():
    """SEC EDGAR being down (filings_port returns None, nothing cached)
    must not stop news/sentiment from coming back."""
    reading = SentimentReading(source="finnhub", label="bullish", score=0.5, sample_size=10)
    use_case = _use_case(
        news_by_symbol={"AAPL": []},
        filings_by_symbol={"AAPL": None},
        sentiment_ports=[FakeSentimentPort({"AAPL": reading})],
    )

    result = use_case.get_all("AAPL")

    assert result["filings"] == []
    assert result["sentiment"]["agreement"] == "single_source"
