"""Twelve Data-backed HistoricalBarSourcePort — a second, keyed source of
daily bars used only to backfill Quant Lab calibration history when
yfinance's persisted history is too short/gappy (see
plans/quant_lab.md section 1). Free tier: 800 requests/day, 8/min as of
this writing — verify the current numbers at https://twelvedata.com/pricing
before relying on them, same caveat this app already states for OpenFIGI's
free tier. Get a key at https://twelvedata.com (no purchase needed for the
free tier) and set TWELVE_DATA_API_KEY.

Only ever sends a symbol, MIC, and date range — never account/holding/
position data, same privacy posture as OpenFigiSecurityMaster.

Fails soft (returns None) on any network error, rate limit, or unexpected
response shape — same contract as yfinance_adapter.py and
OpenFigiSecurityMaster.map_isin. Also returns None (rather than
mis-currencied bars) when the caller supplies `expected_currency` and
Twelve Data's own reported currency disagrees — the same class of bug
yfinance_adapter.py's GBp/minor-unit fix addressed (see
plans/agentic_asset_mapping.md bug B1); Twelve Data's currency handling for
minor-unit-quoted listings (e.g. London pence) hasn't been verified against
a real response yet, so this check is the safety net until it has been.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from decimal import Decimal

import httpx

from app.ports.market_data import BarData

logger = logging.getLogger(__name__)

TIME_SERIES_URL = "https://api.twelvedata.com/time_series"
TIMEOUT_SECONDS = 15.0
DEFAULT_RATE_LIMIT_RESET_SECONDS = 60.0


class TwelveDataAdapter:
    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    def _get(self, symbol: str, mic: str | None, start: date, end: date) -> httpx.Response:
        params = {
            "symbol": symbol,
            "interval": "1day",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "outputsize": 5000,
            "apikey": self.api_key,
        }
        if mic:
            params["mic_code"] = mic
        return httpx.get(TIME_SERIES_URL, params=params, timeout=TIMEOUT_SECONDS)

    def get_daily_history(
        self,
        symbol: str,
        mic: str | None,
        start: date,
        end: date,
        expected_currency: str | None = None,
    ) -> list[BarData] | None:
        if not self.api_key:
            logger.info("Twelve Data: no API key configured — skipping backfill for %s", symbol)
            return None
        try:
            response = self._get(symbol, mic, start, end)
            if response.status_code == 429:
                logger.info(
                    "Twelve Data rate-limited — waiting %.0fs before one retry",
                    DEFAULT_RATE_LIMIT_RESET_SECONDS,
                )
                time.sleep(DEFAULT_RATE_LIMIT_RESET_SECONDS)
                response = self._get(symbol, mic, start, end)
                if response.status_code == 429:
                    logger.warning("Twelve Data still rate-limited after one retry for %s", symbol)
                    return None
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning("Twelve Data request failed for %s", symbol, exc_info=True)
            return None

        try:
            payload = response.json()
        except ValueError:
            logger.warning("Twelve Data returned an unparseable response for %s", symbol)
            return None

        if payload.get("status") == "error":
            logger.info("Twelve Data: %s for %s", payload.get("message", "unknown error"), symbol)
            return None

        meta = payload.get("meta") or {}
        reported_currency = meta.get("currency")
        if (
            expected_currency
            and reported_currency
            and reported_currency.upper() != expected_currency.upper()
        ):
            logger.warning(
                "Twelve Data currency mismatch for %s: expected %s, got %s — discarding to avoid a silent mispricing",
                symbol,
                expected_currency,
                reported_currency,
            )
            return None

        values = payload.get("values") or []
        bars: list[BarData] = []
        for row in values:
            try:
                close = Decimal(str(row["close"]))
                bars.append(
                    BarData(
                        date=date.fromisoformat(row["datetime"]),
                        open=Decimal(str(row["open"])),
                        high=Decimal(str(row["high"])),
                        low=Decimal(str(row["low"])),
                        close=close,
                        adj_close=close,  # Twelve Data's free tier has no separate adjusted close
                        volume=Decimal(str(row.get("volume") or 0)),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        return bars
