"""Pure-function tests for app/domain/market_intel.py — no network, no DB
(see plans/market_intel.md section 8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.market_intel import (
    MAX_HEADLINES,
    dedupe_headlines,
    filing_event_to_dict,
    fuse_sentiment,
    is_fresh,
    news_item_to_dict,
    sentiment_label,
    sentiment_reading_to_dict,
    validate_topic,
)
from app.ports.market_intel import FilingEvent, NewsItem, SentimentReading

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _news(title: str, url: str, minutes_ago: int, source: str = "yfinance") -> NewsItem:
    return NewsItem(
        title=title,
        publisher="Publisher",
        url=url,
        published_at=NOW - timedelta(minutes=minutes_ago),
        source=source,
    )


def test_dedupe_headlines_sorts_newest_first():
    items = [_news("Old", "u1", minutes_ago=60), _news("New", "u2", minutes_ago=5)]
    result = dedupe_headlines(items)
    assert [i.title for i in result] == ["New", "Old"]


def test_dedupe_headlines_removes_same_url_from_different_sources():
    items = [
        _news("Same story", "https://example.com/a", minutes_ago=10, source="yfinance"),
        _news(
            "Same story, different headline",
            "https://example.com/a",
            minutes_ago=5,
            source="finnhub",
        ),
    ]
    result = dedupe_headlines(items)
    assert len(result) == 1
    # Newest-first sort keeps the more recent duplicate.
    assert result[0].source == "finnhub"


def test_dedupe_headlines_falls_back_to_title_when_no_url():
    items = [
        _news("Duplicate headline", "", minutes_ago=10),
        _news("Duplicate Headline", "", minutes_ago=5),
    ]
    result = dedupe_headlines(items)
    assert len(result) == 1


def test_dedupe_headlines_caps_at_max_headlines():
    items = [_news(f"Story {i}", f"u{i}", minutes_ago=i) for i in range(MAX_HEADLINES + 10)]
    assert len(dedupe_headlines(items)) == MAX_HEADLINES


def test_news_item_to_dict_is_jsonable():
    item = _news("Title", "https://example.com", minutes_ago=1)
    result = news_item_to_dict(item)
    assert result == {
        "title": "Title",
        "publisher": "Publisher",
        "url": "https://example.com",
        "published_at": item.published_at.isoformat(),
        "source": "yfinance",
    }


def test_filing_event_to_dict_is_jsonable():
    item = FilingEvent(
        form_type="8-K", filed_at=NOW.date(), title="Material event", url="https://sec.gov/x"
    )
    result = filing_event_to_dict(item)
    assert result == {
        "form_type": "8-K",
        "filed_at": NOW.date().isoformat(),
        "title": "Material event",
        "url": "https://sec.gov/x",
        "source": "sec_edgar",
    }


def test_is_fresh_true_within_ttl():
    fetched_at = datetime.now(UTC) - timedelta(minutes=5)
    assert is_fresh(fetched_at, ttl_minutes=30) is True


def test_is_fresh_false_past_ttl():
    fetched_at = datetime.now(UTC) - timedelta(minutes=45)
    assert is_fresh(fetched_at, ttl_minutes=30) is False


def test_sentiment_label_boundaries():
    assert sentiment_label(0.5) == "bullish"
    assert sentiment_label(-0.5) == "bearish"
    assert sentiment_label(0.0) == "neutral"
    assert sentiment_label(0.1) == "neutral"  # exactly at the band edge
    assert sentiment_label(0.11) == "bullish"


def test_sentiment_reading_to_dict_is_jsonable():
    reading = SentimentReading(source="finnhub", label="bullish", score=0.4, sample_size=120)
    assert sentiment_reading_to_dict(reading) == {
        "source": "finnhub",
        "label": "bullish",
        "score": 0.4,
        "sample_size": 120,
    }


def test_fuse_sentiment_no_data():
    assert fuse_sentiment([]) == {"agreement": "no_data", "sources": []}


def test_fuse_sentiment_single_source():
    reading = SentimentReading(source="finnhub", label="bullish", score=0.5, sample_size=10)
    result = fuse_sentiment([reading])
    assert result["agreement"] == "single_source"
    assert result["sources"] == [sentiment_reading_to_dict(reading)]


def test_fuse_sentiment_aligned_when_both_sources_lean_the_same_way():
    readings = [
        SentimentReading(source="finnhub", label="bullish", score=0.4, sample_size=10),
        SentimentReading(source="stocktwits", label="bullish", score=0.6, sample_size=20),
    ]
    assert fuse_sentiment(readings)["agreement"] == "aligned"


def test_fuse_sentiment_diverging_when_sources_disagree():
    readings = [
        SentimentReading(source="finnhub", label="bullish", score=0.5, sample_size=10),
        SentimentReading(source="stocktwits", label="bearish", score=-0.5, sample_size=20),
    ]
    assert fuse_sentiment(readings)["agreement"] == "diverging"


def test_fuse_sentiment_treats_near_zero_scores_as_neutral_not_diverging():
    readings = [
        SentimentReading(source="finnhub", label="neutral", score=0.02, sample_size=10),
        SentimentReading(source="stocktwits", label="neutral", score=-0.03, sample_size=20),
    ]
    assert fuse_sentiment(readings)["agreement"] == "aligned"


def test_validate_topic_accepts_a_short_phrase():
    assert validate_topic("European bank stocks") is None


def test_validate_topic_rejects_empty():
    assert validate_topic("   ") is not None


def test_validate_topic_rejects_too_long():
    assert validate_topic("x" * 81) is not None


def test_validate_topic_accepts_exactly_max_length():
    assert validate_topic("x" * 80) is None


def test_validate_topic_rejects_currency_amount():
    assert validate_topic("why did I lose $5000 today") is not None


def test_validate_topic_rejects_long_digit_run():
    assert validate_topic("account 123456789 activity") is not None


def test_validate_topic_allows_a_year_or_short_ticker_shaped_number():
    assert validate_topic("2026 inflation outlook") is None
