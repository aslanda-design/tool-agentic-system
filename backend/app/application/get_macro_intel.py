"""GetMacroIntelUseCase — same shape as GetAssetIntelUseCase but for a
free-text sector/macro topic instead of a symbol (plans/market_intel.md
section 3, Phase 3). Kept as its own use case rather than a third method on
GetAssetIntelUseCase because its cache key is a normalized topic phrase,
not a ticker — mixing the two would blur what "symbol" means in the cache
table.

Same egress guarantee as GetAssetIntelUseCase: the only input is the
`topic` string, never anything from `positions`/`accounts`/`holdings`."""

from __future__ import annotations

from app.domain.market_intel import dedupe_headlines, is_fresh, news_item_to_dict
from app.ports.market_intel import AssetIntelCacheRepo, MacroNewsPort


def _cache_key(topic: str) -> str:
    return topic.strip().lower()


class GetMacroIntelUseCase:
    def __init__(
        self, macro_news_port: MacroNewsPort, cache_repo: AssetIntelCacheRepo, cache_minutes: int
    ) -> None:
        self.macro_news_port = macro_news_port
        self.cache_repo = cache_repo
        self.cache_minutes = cache_minutes

    def get_headlines(self, topic: str) -> list[dict]:
        key = _cache_key(topic)
        cached = self.cache_repo.get(key, "macro_news")
        if cached is not None and is_fresh(cached.fetched_at, self.cache_minutes):
            return cached.payload

        items = self.macro_news_port.get_headlines(topic)
        if items is None:
            return cached.payload if cached is not None else []

        payload = [news_item_to_dict(item) for item in dedupe_headlines(items)]
        self.cache_repo.put(key, "macro_news", payload)
        return payload
