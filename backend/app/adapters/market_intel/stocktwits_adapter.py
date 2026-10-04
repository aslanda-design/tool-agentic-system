"""StockTwits-backed SentimentPort — the social/retail cross-check on
Finnhub's news-derived sentiment (plans/market_intel.md section 2). Public,
unauthenticated symbol stream — no signup, no key. Only a subset of posts
carry a self-tagged Bullish/Bearish label; this reading is built only from
those, ignoring untagged posts rather than guessing at their sentiment.

Same fail-soft contract as every other adapter: an unknown symbol, network
error, or unexpected response shape returns None. A symbol with messages but
none of them tagged also returns None — there's genuinely nothing to report,
not a source failure, but the caller (GetAssetIntelUseCase) treats both the
same way (skip this source for this reading).

**Needs a descriptive `User-Agent`, confirmed by hitting the real endpoint**
— StockTwits 403s httpx's default UA (basic anti-scraping heuristic, not an
auth requirement: the endpoint itself is still public/unauthenticated).
Sending a plain, honest identifier for this app — not spoofing a browser —
is enough."""

from __future__ import annotations

import logging

import httpx

from app.domain.market_intel import sentiment_label
from app.ports.market_intel import SentimentPort, SentimentReading

logger = logging.getLogger(__name__)

STREAM_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
TIMEOUT_SECONDS = 10.0
# See module docstring — httpx's default User-Agent gets a 403 here.
HEADERS = {"User-Agent": "investment-tracker-market-intel/1.0"}


class StockTwitsSentimentAdapter(SentimentPort):
    def get_sentiment(self, symbol: str) -> SentimentReading | None:
        try:
            response = httpx.get(
                STREAM_URL.format(symbol=symbol), headers=HEADERS, timeout=TIMEOUT_SECONDS
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            logger.warning(
                "StockTwits: failed to fetch symbol stream for %s", symbol, exc_info=True
            )
            return None

        bullish = bearish = 0
        for message in data.get("messages") or []:
            basic = ((message.get("entities") or {}).get("sentiment") or {}).get("basic")
            if basic == "Bullish":
                bullish += 1
            elif basic == "Bearish":
                bearish += 1

        tagged = bullish + bearish
        if tagged == 0:
            return None

        score = (bullish - bearish) / tagged
        return SentimentReading(
            source="stocktwits", label=sentiment_label(score), score=score, sample_size=tagged
        )
