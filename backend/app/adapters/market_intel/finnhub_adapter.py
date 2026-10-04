"""Finnhub-backed SentimentPort — see plans/market_intel.md section 2. Free
tier (60 calls/min, personal/non-commercial use only — see the plan's
rejected-candidates table) needs a signup-issued API key
(`settings.finnhub_api_key`), unlike yfinance/SEC EDGAR. `news-sentiment`
folds Finnhub's own news-derived bullish/bearish split and a week's article
volume into one response — no raw articles are fetched here, just that
summary.

Same fail-soft contract as every other adapter in this app: no key, a
network error, a rate limit, or an unexpected response shape all return
None rather than raising."""

from __future__ import annotations

import logging

import httpx

from app.domain.market_intel import sentiment_label
from app.ports.market_intel import SentimentPort, SentimentReading

logger = logging.getLogger(__name__)

SENTIMENT_URL = "https://finnhub.io/api/v1/news-sentiment"
TIMEOUT_SECONDS = 10.0


class FinnhubSentimentAdapter(SentimentPort):
    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    def get_sentiment(self, symbol: str) -> SentimentReading | None:
        if not self.api_key:
            return None
        try:
            response = httpx.get(
                SENTIMENT_URL,
                params={"symbol": symbol, "token": self.api_key},
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            logger.warning("Finnhub: failed to fetch sentiment for %s", symbol, exc_info=True)
            return None

        sentiment = data.get("sentiment") or {}
        bullish = sentiment.get("bullishPercent")
        bearish = sentiment.get("bearishPercent")
        if not isinstance(bullish, (int, float)) or not isinstance(bearish, (int, float)):
            # A symbol Finnhub doesn't cover reports {} for "sentiment" —
            # not an error, just nothing to report.
            return None

        score = float(bullish) - float(bearish)
        buzz = data.get("buzz") or {}
        articles = buzz.get("articlesInLastWeek")
        return SentimentReading(
            source="finnhub",
            label=sentiment_label(score),
            score=score,
            sample_size=int(articles) if isinstance(articles, (int, float)) else None,
        )
