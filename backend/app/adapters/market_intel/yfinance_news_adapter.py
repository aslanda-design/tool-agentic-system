"""yfinance-backed NewsPort — Yahoo Finance's own aggregated headline feed
per ticker. Same unofficial-scraper caveat as yfinance_adapter.py (no SLA,
throttles, silently breaks when Yahoo changes their page) and the same
fail-soft contract: any error returns None rather than raising, so a bad
symbol never aborts a chat turn.

yfinance has shipped `Ticker.news` under two different item shapes across
versions — flat fields (older) and fields nested under a "content" key
(current, as of yfinance 1.7.x) — `_parse_item` below reads whichever is
present rather than pinning to one, since this app doesn't otherwise
constrain the yfinance version tightly."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import yfinance as yf

from app.ports.market_intel import NewsItem, NewsPort

logger = logging.getLogger(__name__)


def _parse_published_at(raw) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _parse_item(raw: dict) -> NewsItem | None:
    content = raw.get("content") if isinstance(raw.get("content"), dict) else raw

    title = content.get("title")
    url = (content.get("canonicalUrl") or {}).get("url") or content.get("link")
    provider = content.get("provider")
    publisher = (
        provider.get("displayName") if isinstance(provider, dict) else None
    ) or content.get("publisher")
    published_at = _parse_published_at(content.get("pubDate") or raw.get("providerPublishTime"))

    if not title or not url or published_at is None:
        return None
    return NewsItem(
        title=title,
        publisher=publisher or "Yahoo Finance",
        url=url,
        published_at=published_at,
        source="yfinance",
    )


class YFinanceNewsAdapter(NewsPort):
    def get_headlines(self, symbol: str) -> list[NewsItem] | None:
        try:
            raw_items = yf.Ticker(symbol).news
        except Exception:
            logger.warning("yfinance: failed to fetch news for %s", symbol, exc_info=True)
            return None
        if raw_items is None:
            return None
        items = []
        for raw in raw_items:
            try:
                item = _parse_item(raw)
            except Exception:
                item = None
            if item is not None:
                items.append(item)
        return items
