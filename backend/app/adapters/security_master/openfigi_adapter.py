"""OpenFIGI-backed SecurityMasterPort. OpenFIGI (https://www.openfigi.com/api)
is Bloomberg's free, no-signup-required identifier mapping service — sending
it an ISIN returns every exchange listing (Bloomberg exchange code + ticker)
it knows for that security. An API key raises the free-tier rate limit but
isn't required; only the ISIN is ever sent, never account/holding data (see
root AGENTS.md's privacy notes).

Fails soft like yfinance_adapter.py: any network error, unexpected response
shape, or a still-rate-limited retry returns None (not []) — see
SecurityMasterPort.map_isin's docstring for why that distinction matters to
the caller.
"""

from __future__ import annotations

import logging
import time

import httpx

from app.domain.listings import FigiListing
from app.ports.security_master import SecurityMasterPort

logger = logging.getLogger(__name__)

MAPPING_URL = "https://api.openfigi.com/v3/mapping"
TIMEOUT_SECONDS = 10.0
DEFAULT_RATE_LIMIT_RESET_SECONDS = 60.0


class OpenFigiSecurityMaster(SecurityMasterPort):
    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-OPENFIGI-APIKEY"] = self.api_key
        return headers

    def _post(self, isin: str) -> httpx.Response:
        return httpx.post(
            MAPPING_URL,
            json=[{"idType": "ID_ISIN", "idValue": isin}],
            headers=self._headers(),
            timeout=TIMEOUT_SECONDS,
        )

    def map_isin(self, isin: str) -> list[FigiListing] | None:
        try:
            response = self._post(isin)
            if response.status_code == 429:
                # Rate-limited — wait once for the window to reset (per
                # OpenFIGI's docs; the header isn't always present) and retry
                # exactly once. A second 429 means "try again later".
                reset_seconds = DEFAULT_RATE_LIMIT_RESET_SECONDS
                try:
                    reset_seconds = float(response.headers.get("retry-after", reset_seconds))
                except (TypeError, ValueError):
                    pass
                logger.info("OpenFIGI rate-limited — waiting %.0fs before one retry", reset_seconds)
                time.sleep(reset_seconds)
                response = self._post(isin)
                if response.status_code == 429:
                    logger.warning("OpenFIGI still rate-limited after one retry for ISIN %s", isin)
                    return None
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning("OpenFIGI request failed for ISIN %s", isin, exc_info=True)
            return None

        try:
            jobs = response.json()
            job = jobs[0]
        except (ValueError, IndexError, TypeError):
            logger.warning("OpenFIGI returned an unexpected response shape for ISIN %s", isin)
            return None

        if "warning" in job:
            # e.g. {"warning": "No identifier found."} — a real, final answer.
            return []
        if "error" in job:
            logger.warning("OpenFIGI returned an error for ISIN %s: %s", isin, job["error"])
            return None

        listings = []
        for item in job.get("data", []):
            ticker = item.get("ticker")
            exch_code = item.get("exchCode")
            if not ticker or not exch_code:
                continue
            listings.append(
                FigiListing(
                    ticker=ticker,
                    exch_code=exch_code,
                    name=item.get("name") or "",
                    security_type=item.get("securityType") or "",
                    share_class_figi=item.get("shareClassFIGI"),
                )
            )
        return listings
