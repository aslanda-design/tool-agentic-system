"""GetAssetIntelUseCase — orchestrates one symbol's news/filings/sentiment
lookup across external ports, applies domain/market_intel.py's dedupe and
sentiment fusion, and reads through app/ports/market_intel.py's cache. See
plans/market_intel.md sections 3/6.

Every public method takes a bare `symbol: str` and returns an already-
jsonable plain `dict`/`list[dict]` — never an `asset_id`, and this use case
never opens a repo that could reach `positions`/`accounts`/`holdings`. That
is the whole egress guarantee from plans/market_intel.md section 4: nothing
above this layer can make it send more than the symbol string it was
called with."""

from __future__ import annotations

from app.domain.market_intel import (
    dedupe_headlines,
    filing_event_to_dict,
    fuse_sentiment,
    is_fresh,
    news_item_to_dict,
)
from app.ports.market_intel import AssetIntelCacheRepo, FilingsPort, NewsPort, SentimentPort


class GetAssetIntelUseCase:
    def __init__(
        self,
        news_port: NewsPort,
        filings_port: FilingsPort,
        sentiment_ports: list[SentimentPort],
        cache_repo: AssetIntelCacheRepo,
        cache_minutes: int,
    ) -> None:
        self.news_port = news_port
        self.filings_port = filings_port
        self.sentiment_ports = sentiment_ports
        self.cache_repo = cache_repo
        self.cache_minutes = cache_minutes

    def get_news(self, symbol: str) -> list[dict]:
        cached = self.cache_repo.get(symbol, "news")
        if cached is not None and is_fresh(cached.fetched_at, self.cache_minutes):
            return cached.payload

        items = self.news_port.get_headlines(symbol)
        if items is None:
            # Source unreachable/rate-limited — serve a stale cache entry
            # rather than an empty result, per NewsPort.get_headlines'
            # fail-soft contract.
            return cached.payload if cached is not None else []

        payload = [news_item_to_dict(item) for item in dedupe_headlines(items)]
        self.cache_repo.put(symbol, "news", payload)
        return payload

    def get_filings(self, symbol: str) -> list[dict]:
        cached = self.cache_repo.get(symbol, "filings")
        if cached is not None and is_fresh(cached.fetched_at, self.cache_minutes):
            return cached.payload

        items = self.filings_port.get_filings(symbol)
        if items is None:
            return cached.payload if cached is not None else []

        payload = [filing_event_to_dict(item) for item in items]
        self.cache_repo.put(symbol, "filings", payload)
        return payload

    def get_sentiment(self, symbol: str) -> dict:
        cached = self.cache_repo.get(symbol, "sentiment")
        if cached is not None and is_fresh(cached.fetched_at, self.cache_minutes):
            return cached.payload

        readings = [
            r
            for r in (port.get_sentiment(symbol) for port in self.sentiment_ports)
            if r is not None
        ]
        if not readings:
            # Every source failed/had nothing — fall back to a stale
            # reading rather than reporting "no_data" when we actually do
            # have a (older) answer, same fallback shape as get_news/
            # get_filings.
            return cached.payload if cached is not None else fuse_sentiment([])

        payload = fuse_sentiment(readings)
        self.cache_repo.put(symbol, "sentiment", payload)
        return payload

    def get_all(self, symbol: str) -> dict:
        """The three lookups above in one call — for a caller that wants
        the full picture on one asset without three round trips (the
        `AssetDetailPage` card, Phase 3). Each piece still goes through
        its own cache/fail-soft path independently; a failure in one
        (e.g. SEC EDGAR down) doesn't affect the other two."""
        return {
            "news": self.get_news(symbol),
            "filings": self.get_filings(symbol),
            "sentiment": self.get_sentiment(symbol),
        }
