"""Outbound ports for external news/filings, and the port for this app's own
read-through cache of them — see plans/market_intel.md. Two things make this
different from every other port in this app:

1. **Every method takes a bare `symbol: str` (or, for filings, a `symbol`
   resolved internally to a CIK) and nothing else** — never an `asset_id`,
   never a DB session that could reach `positions`/`accounts`/`holdings`.
   This is the egress guard plans/market_intel.md section 4 describes:
   what leaves this app is bounded by these methods' signatures, not by
   trusting the caller. backend/ai/AGENTS.md rule 13 enforces this at the
   MCP tool layer too.
2. **Fail-soft returns `None`, exactly like MarketDataPort.get_listing_info**
   — a rate-limited or unreachable source is not an error, it's "nothing new
   to report," and the use case (application/get_asset_intel.py) falls back
   to a stale cache entry rather than raising into a chat turn.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime

# A cache row's payload is list[dict] for "news"/"filings" (each item is one
# story/filing) or a single dict for "sentiment" (one fused summary) — JSONB
# stores either without a schema change; see CachedIntel/AssetIntelCacheRepo
# below.


@dataclass(slots=True)
class NewsItem:
    title: str
    publisher: str
    url: str
    published_at: datetime  # tz-aware
    source: str  # e.g. "yfinance"


@dataclass(slots=True)
class FilingEvent:
    form_type: str  # e.g. "8-K", "4"
    filed_at: date
    title: str
    url: str
    source: str = "sec_edgar"


@dataclass(slots=True)
class SentimentReading:
    source: str  # "finnhub" | "stocktwits"
    label: str  # "bullish" | "bearish" | "neutral"
    score: float  # -1.0 (fully bearish) .. 1.0 (fully bullish)
    sample_size: int | None  # articles/posts behind this reading, when the source reports one


@dataclass(slots=True)
class CachedIntel:
    payload: dict | list[dict]
    fetched_at: datetime  # tz-aware


class NewsPort(ABC):
    @abstractmethod
    def get_headlines(self, symbol: str) -> list[NewsItem] | None:
        """Recent headlines for one symbol, newest first. None means the
        source failed/is unreachable — never raises. [] is a genuine
        "nothing found", distinct from None the same way
        MarketDataPort.get_listing_info distinguishes them."""


class FilingsPort(ABC):
    @abstractmethod
    def get_filings(self, symbol: str) -> list[FilingEvent] | None:
        """Recent primary-source filings (8-Ks, Form 4s) for one symbol,
        newest first. None means the source failed, the symbol has no US
        SEC filer (e.g. most non-US listings), or SEC_EDGAR_USER_AGENT isn't
        configured — never raises."""


class MacroNewsPort(ABC):
    @abstractmethod
    def get_headlines(self, topic: str) -> list[NewsItem] | None:
        """Recent headlines for a free-text sector/macro topic (e.g.
        "European bank stocks"), newest first — not tied to one ticker,
        unlike NewsPort. None means the source failed/is rate-limited —
        never raises. [] is a genuine "nothing found"."""


class SentimentPort(ABC):
    @abstractmethod
    def get_sentiment(self, symbol: str) -> SentimentReading | None:
        """One source's sentiment reading for one symbol. None means the
        source failed, isn't configured (e.g. no API key), or has nothing
        tagged for this symbol right now — never raises. Several of these
        (one per source) are fused by domain.market_intel.fuse_sentiment
        into a single agreement/divergence summary — see
        plans/market_intel.md section 6."""


class AssetIntelCacheRepo(ABC):
    @abstractmethod
    def get(self, symbol: str, kind: str) -> CachedIntel | None:
        """The most recently cached payload for (symbol, kind) — e.g.
        kind="news", "filings", or "sentiment" — or None if nothing's been
        cached yet. Freshness is the caller's decision
        (domain.market_intel.is_fresh), not this repo's — a stale row is
        still returned so a use case can fall back to it when the live
        source just failed."""

    @abstractmethod
    def put(self, symbol: str, kind: str, payload: dict | list[dict]) -> None:
        """Upsert — one row per (symbol, kind), overwriting whatever was
        cached before with a fresh fetched_at."""
