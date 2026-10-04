"""SEC EDGAR-backed FilingsPort — the "accurate" leg of plans/market_intel.md
section 2: primary-source filings (8-K material events, Form 4 insider
transactions) rather than a news aggregator's summary of them. Free, no API
key, official (sec.gov) — but SEC's fair-access policy requires every
request to carry a descriptive `User-Agent` identifying the requester
(https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data);
`settings.sec_edgar_user_agent` is that string, left blank by default like
every other credential-shaped value in this app. Requests with no
User-Agent risk an IP block, so `get_filings` fails soft to None rather than
ever calling SEC without one.

Only US SEC filers have anything to return here — most non-US listings
(e.g. a Xetra-listed UCITS ETF) genuinely have no CIK, which is a normal
`[]` result, not an error.

Same fail-soft contract as every other adapter in this app: network error,
missing User-Agent, or an unexpected response shape returns None."""

from __future__ import annotations

import logging
import time
from datetime import date

import httpx

from app.ports.market_intel import FilingEvent, FilingsPort

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
TIMEOUT_SECONDS = 10.0

# 8-K = material events, 4 = insider (officer/director) transactions — the
# two forms most relevant to "what just happened with this holding," per
# plans/market_intel.md section 6. Full-text search across every form type
# is a possible future addition, not needed for this first pass.
RELEVANT_FORMS = frozenset({"8-K", "4"})
MAX_FILINGS = 10

# company_tickers.json is a multi-MB reference dataset (every US filer), not
# per-symbol data — refetching it on every call would be wasteful and slow.
# It's static enough that this app already tolerates similar TTLs elsewhere
# (market_data_refresh_minutes); a day is generous for a mapping that
# changes only when a company starts/stops filing.
_TICKER_CACHE_TTL_SECONDS = 24 * 60 * 60
_ticker_to_cik_cache: dict[str, int] = {}
_ticker_cache_fetched_at: float = 0.0


class SecEdgarAdapter(FilingsPort):
    def __init__(self, user_agent: str = "") -> None:
        self.user_agent = user_agent

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent}

    def _ticker_to_cik(self, symbol: str) -> int | None:
        global _ticker_cache_fetched_at
        now = time.monotonic()
        if not _ticker_to_cik_cache or (now - _ticker_cache_fetched_at) > _TICKER_CACHE_TTL_SECONDS:
            try:
                response = httpx.get(TICKERS_URL, headers=self._headers(), timeout=TIMEOUT_SECONDS)
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError):
                logger.warning("SEC EDGAR: failed to fetch company_tickers.json", exc_info=True)
                return _ticker_to_cik_cache.get(symbol.upper())
            _ticker_to_cik_cache.clear()
            for entry in data.values():
                ticker = entry.get("ticker")
                cik = entry.get("cik_str")
                if ticker and cik is not None:
                    _ticker_to_cik_cache[ticker.upper()] = int(cik)
            _ticker_cache_fetched_at = now
        return _ticker_to_cik_cache.get(symbol.upper())

    def get_filings(self, symbol: str) -> list[FilingEvent] | None:
        if not self.user_agent:
            logger.info("SEC EDGAR: SEC_EDGAR_USER_AGENT not configured — skipping filings lookup")
            return None
        try:
            cik = self._ticker_to_cik(symbol)
        except Exception:
            logger.warning("SEC EDGAR: ticker lookup failed for %s", symbol, exc_info=True)
            return None
        if cik is None:
            # Genuinely not a US SEC filer under this ticker — not an error.
            return []

        try:
            response = httpx.get(
                SUBMISSIONS_URL.format(cik=cik), headers=self._headers(), timeout=TIMEOUT_SECONDS
            )
            response.raise_for_status()
            recent = response.json().get("filings", {}).get("recent", {})
        except (httpx.HTTPError, ValueError):
            logger.warning(
                "SEC EDGAR: failed to fetch submissions for CIK %s (%s)", cik, symbol, exc_info=True
            )
            return None

        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])
        titles = recent.get("primaryDocDescription", [])

        events: list[FilingEvent] = []
        for form, filed, accession, doc, title in zip(
            forms, filing_dates, accession_numbers, primary_docs, titles, strict=False
        ):
            if form not in RELEVANT_FORMS:
                continue
            try:
                filed_at = date.fromisoformat(filed)
            except ValueError:
                continue
            accession_no_dashes = accession.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{doc}"
            events.append(
                FilingEvent(
                    form_type=form, filed_at=filed_at, title=title or f"Form {form}", url=url
                )
            )
            if len(events) >= MAX_FILINGS:
                break
        return events
