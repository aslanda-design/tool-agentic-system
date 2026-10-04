"""Pure functions over NewsItem/FilingEvent/SentimentReading
(app/ports/market_intel.py) — no I/O, no DB, fully unit-testable without a
network call. See plans/market_intel.md section 3/6: this is where "combine
several sources" turns into an actual judgment call (dedupe across two news
feeds, agreement/divergence across two sentiment sources) rather than a
passthrough of whichever adapter answered."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.ports.market_intel import FilingEvent, NewsItem, SentimentReading

# Cap on how many headlines a single get_asset_news call returns, regardless
# of how many sources contributed — keeps a chat tool's result small and
# readable rather than dumping a raw feed.
MAX_HEADLINES = 15

# get_macro_headlines' egress guard (plans/market_intel.md section 4.3) — a
# short search phrase, not a dump. A currency symbol followed by a digit, or
# any run of 5+ digits (longer than a ticker, year, or ISIN needs), reads as
# "this looks like an amount/account number, not a topic" and is rejected
# before it ever reaches GdeltAdapter. Defense in depth, not the real
# guarantee — see MacroNewsPort's docstring for that.
MAX_TOPIC_LENGTH = 80
_SUSPICIOUS_TOPIC_PATTERN = re.compile(r"[$€£]\s?\d|\d{5,}")

# A score within +-NEUTRAL_BAND of zero reads as "neutral" rather than a
# weak lean either way — avoids two sources both hovering near zero (e.g.
# 0.02 and -0.03) being reported as "diverging" over noise.
NEUTRAL_BAND = 0.1


def dedupe_headlines(items: list[NewsItem]) -> list[NewsItem]:
    """Newest first, deduped by URL (falling back to a normalized title for
    the rare case two sources report the same story under different URLs),
    capped at MAX_HEADLINES."""
    seen: set[str] = set()
    deduped: list[NewsItem] = []
    for item in sorted(items, key=lambda i: i.published_at, reverse=True):
        key = item.url or item.title.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:MAX_HEADLINES]


def news_item_to_dict(item: NewsItem) -> dict:
    return {
        "title": item.title,
        "publisher": item.publisher,
        "url": item.url,
        "published_at": item.published_at.isoformat(),
        "source": item.source,
    }


def filing_event_to_dict(item: FilingEvent) -> dict:
    return {
        "form_type": item.form_type,
        "filed_at": item.filed_at.isoformat(),
        "title": item.title,
        "url": item.url,
        "source": item.source,
    }


def validate_topic(topic: str) -> str | None:
    """None if `topic` is safe to hand to a macro-news search — a short
    error string otherwise. This is the tool-level sanity check
    plans/market_intel.md section 4.3 describes: it catches an obviously
    wrong argument (way too long, or shaped like a currency amount) before
    it reaches GdeltAdapter. It is deliberately not a content-moderation
    system — the real egress guarantee is structural (section 4.2: no
    tool ever reads positions/accounts to begin with), this just adds a
    cheap second check on the one free-text argument in this whole
    server."""
    stripped = topic.strip()
    if not stripped:
        return "topic must not be empty"
    if len(stripped) > MAX_TOPIC_LENGTH:
        return f"topic is too long ({len(stripped)} chars, max {MAX_TOPIC_LENGTH}) — use a short search phrase, not a passage"
    if _SUSPICIOUS_TOPIC_PATTERN.search(stripped):
        return "topic looks like it contains a currency amount or account-shaped number, not a search phrase"
    return None


def is_fresh(fetched_at: datetime, ttl_minutes: int) -> bool:
    age = datetime.now(UTC) - fetched_at
    return age.total_seconds() < ttl_minutes * 60


def sentiment_label(score: float) -> str:
    """score in [-1, 1] -> "bullish"/"bearish"/"neutral". Shared by every
    SentimentPort adapter (so a reading's label always matches this app's
    own thresholds, not each source's own wording) and by fuse_sentiment
    below."""
    if score > NEUTRAL_BAND:
        return "bullish"
    if score < -NEUTRAL_BAND:
        return "bearish"
    return "neutral"


def sentiment_reading_to_dict(reading: SentimentReading) -> dict:
    return {
        "source": reading.source,
        "label": reading.label,
        "score": reading.score,
        "sample_size": reading.sample_size,
    }


def fuse_sentiment(readings: list[SentimentReading]) -> dict:
    """The judgment call plans/market_intel.md section 6 describes as what
    makes get_asset_sentiment more than a passthrough: whether independent
    sources actually agree, not just their individual scores.

    "agreement" is:
    - "no_data" — no source had anything to report
    - "single_source" — only one source answered; nothing to cross-check
      against, so "agreement" isn't a meaningful concept yet
    - "aligned" — every source's label (via sentiment_label) points the
      same direction
    - "diverging" — sources disagree, e.g. Finnhub reads bullish while
      StockTwits reads bearish — this is the case worth surfacing to the
      user rather than averaging away.
    """
    if not readings:
        return {"agreement": "no_data", "sources": []}
    if len(readings) == 1:
        agreement = "single_source"
    else:
        labels = {sentiment_label(r.score) for r in readings}
        agreement = "aligned" if len(labels) == 1 else "diverging"
    return {"agreement": agreement, "sources": [sentiment_reading_to_dict(r) for r in readings]}
