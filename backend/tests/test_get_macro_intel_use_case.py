"""Tests for GetMacroIntelUseCase (app/application/get_macro_intel.py) —
same cache/fallback shape as test_get_asset_intel_use_case.py, keyed by a
normalized topic instead of a symbol."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.get_macro_intel import GetMacroIntelUseCase
from app.ports.market_intel import NewsItem
from tests.fakes import FakeAssetIntelCacheRepo, FakeMacroNewsPort


def _use_case(by_topic=None, cache=None, cache_minutes=30):
    return GetMacroIntelUseCase(
        FakeMacroNewsPort(by_topic), cache or FakeAssetIntelCacheRepo(), cache_minutes
    )


def test_get_headlines_fetches_and_caches_under_a_normalized_key():
    item = NewsItem(
        title="T", publisher="P", url="u", published_at=datetime.now(UTC), source="gdelt"
    )
    cache = FakeAssetIntelCacheRepo()
    use_case = _use_case(by_topic={"European bank stocks": [item]}, cache=cache)

    result = use_case.get_headlines("European bank stocks")

    assert len(result) == 1
    assert result[0]["title"] == "T"
    # Cached under a normalized (stripped, lowercased) key, not the raw
    # topic string as typed.
    assert cache.get("european bank stocks", "macro_news").payload == result


def test_get_headlines_serves_from_cache_when_fresh():
    cache = FakeAssetIntelCacheRepo()
    cache.put("inflation", "macro_news", [{"title": "cached"}])

    class TrackingPort:
        called = False

        def get_headlines(self, topic):
            self.called = True
            return []

    port = TrackingPort()
    use_case = GetMacroIntelUseCase(port, cache, cache_minutes=30)

    result = use_case.get_headlines("Inflation")  # different case than the cached key

    assert result == [{"title": "cached"}]
    assert port.called is False


def test_get_headlines_falls_back_to_stale_cache_when_source_fails():
    cache = FakeAssetIntelCacheRepo()
    cache.put("inflation", "macro_news", [{"title": "stale but better than nothing"}])
    cache._rows[("inflation", "macro_news")].fetched_at = datetime.now(UTC) - timedelta(hours=2)
    use_case = _use_case(by_topic={"inflation": None}, cache=cache)

    result = use_case.get_headlines("inflation")

    assert result == [{"title": "stale but better than nothing"}]


def test_get_headlines_returns_empty_when_source_fails_and_nothing_cached():
    use_case = _use_case(by_topic={"inflation": None})

    assert use_case.get_headlines("inflation") == []
