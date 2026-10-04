"""GDELT-backed MacroNewsPort — the macro/sector leg of
plans/market_intel.md section 2: free, no API key, global news search
across 65+ languages, for questions that aren't tied to one ticker (e.g.
"European bank stocks", "semiconductor export controls").

**No per-article tone/sentiment score, despite this plan's original
assumption** — confirmed against GDELT's own documented fields for the
`artlist` mode (`url`, `url_mobile`, `title`, `seendate`, `socialimage`,
`domain`, `language`, `sourcecountry`). GDELT's tone is only available as
an aggregate (`timelinetone` mode: a time series of average tone across
all matching coverage, not a per-article number) — fetching it would need
a second call for a number this app can't attribute to any one headline,
so it's left out rather than faked. This tool returns headlines only.

GDELT's own fair-use limit is one request every 5 seconds per client — far
stricter than Finnhub/SEC EDGAR. The read-through cache
(MARKET_INTEL_CACHE_MINUTES) is what keeps this app well under that in
practice; this adapter itself makes no attempt to queue/retry, consistent
with every other adapter's fail-soft-on-429 behavior.

Same fail-soft contract as every other adapter: a network error, a 429, or
an unparseable response all return None. An empty (but 200) response body
is GDELT's own way of saying "no matches" and returns []."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from app.ports.market_intel import MacroNewsPort, NewsItem

logger = logging.getLogger(__name__)

DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
TIMEOUT_SECONDS = 15.0
MAX_RECORDS = 25


def _parse_seen_date(raw: str | None) -> datetime | None:
    # GDELT's own format: "20260918T121500Z".
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


class GdeltAdapter(MacroNewsPort):
    def get_headlines(self, topic: str) -> list[NewsItem] | None:
        try:
            response = httpx.get(
                DOC_API_URL,
                params={
                    "query": topic,
                    "mode": "artlist",
                    "format": "json",
                    "maxrecords": MAX_RECORDS,
                    "sort": "datedesc",
                },
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError:
            logger.warning("GDELT: request failed for topic %r", topic, exc_info=True)
            return None

        if response.status_code == 429:
            logger.info("GDELT: rate-limited (fair-use is 1 request/5s) for topic %r", topic)
            return None
        if response.status_code >= 400:
            logger.warning("GDELT: HTTP %s for topic %r", response.status_code, topic)
            return None

        if not response.text.strip():
            # GDELT's own "no matching articles" response is an empty body,
            # not `{"articles": []}`.
            return []
        try:
            data = response.json()
        except ValueError:
            logger.warning("GDELT: non-JSON response for topic %r", topic)
            return None

        items = []
        for article in data.get("articles") or []:
            title = article.get("title")
            url = article.get("url")
            published_at = _parse_seen_date(article.get("seendate"))
            if not title or not url or published_at is None:
                continue
            items.append(
                NewsItem(
                    title=title,
                    publisher=article.get("domain") or "",
                    url=url,
                    published_at=published_at,
                    source="gdelt",
                )
            )
        return items
