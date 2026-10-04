"""Tests for TwelveDataAdapter — see plans/quant_lab.md section 1. Mocks
the HTTP layer (`httpx.get`) rather than hitting the real Twelve Data API,
same pattern as test_openfigi_adapter.py."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from app.adapters.market_data.twelve_data_adapter import TwelveDataAdapter


def _responses(*responses):
    it = iter(responses)

    def fake_get(*args, **kwargs):
        response = next(it)
        response.request = httpx.Request("GET", "https://api.twelvedata.com/time_series")
        return response

    return fake_get


def test_no_api_key_returns_none_without_a_request():
    adapter = TwelveDataAdapter(api_key="")
    result = adapter.get_daily_history("AAPL", "XNGS", date(2026, 1, 1), date(2026, 1, 10))
    assert result is None


def test_successful_response_parses_bars(monkeypatch):
    body = {
        "meta": {"symbol": "EUNL", "currency": "EUR", "mic_code": "XETR"},
        "values": [
            {
                "datetime": "2026-01-02",
                "open": "80.0",
                "high": "81.0",
                "low": "79.5",
                "close": "80.5",
                "volume": "1000",
            },
            {
                "datetime": "2026-01-01",
                "open": "79.0",
                "high": "80.0",
                "low": "78.5",
                "close": "79.5",
                "volume": "900",
            },
        ],
        "status": "ok",
    }
    monkeypatch.setattr(httpx, "get", _responses(httpx.Response(200, json=body)))

    bars = TwelveDataAdapter("test-key").get_daily_history(
        "EUNL", "XETR", date(2026, 1, 1), date(2026, 1, 2), expected_currency="EUR"
    )

    assert bars is not None
    assert len(bars) == 2
    assert bars[0].date == date(2026, 1, 2)
    assert bars[0].close == pytest.approx(80.5)


def test_currency_mismatch_discards_bars(monkeypatch):
    body = {
        "meta": {"currency": "GBP"},
        "values": [
            {
                "datetime": "2026-01-02",
                "open": "1",
                "high": "1",
                "low": "1",
                "close": "1",
                "volume": "1",
            }
        ],
        "status": "ok",
    }
    monkeypatch.setattr(httpx, "get", _responses(httpx.Response(200, json=body)))

    bars = TwelveDataAdapter("test-key").get_daily_history(
        "SWDA", "XLON", date(2026, 1, 1), date(2026, 1, 2), expected_currency="EUR"
    )

    assert bars is None


def test_error_status_returns_none(monkeypatch):
    body = {"code": 400, "message": "symbol not found", "status": "error"}
    monkeypatch.setattr(httpx, "get", _responses(httpx.Response(200, json=body)))

    bars = TwelveDataAdapter("test-key").get_daily_history(
        "BOGUS", None, date(2026, 1, 1), date(2026, 1, 2)
    )

    assert bars is None


def test_malformed_response_returns_none(monkeypatch):
    monkeypatch.setattr(httpx, "get", _responses(httpx.Response(200, text="not json")))

    bars = TwelveDataAdapter("test-key").get_daily_history(
        "AAPL", None, date(2026, 1, 1), date(2026, 1, 2)
    )

    assert bars is None


def test_500_error_returns_none(monkeypatch):
    monkeypatch.setattr(httpx, "get", _responses(httpx.Response(500, text="server error")))

    bars = TwelveDataAdapter("test-key").get_daily_history(
        "AAPL", None, date(2026, 1, 1), date(2026, 1, 2)
    )

    assert bars is None


def test_429_retries_once_then_succeeds(monkeypatch):
    ok_body = {
        "meta": {"currency": "USD"},
        "values": [
            {
                "datetime": "2026-01-02",
                "open": "1",
                "high": "1",
                "low": "1",
                "close": "1",
                "volume": "1",
            }
        ],
        "status": "ok",
    }
    monkeypatch.setattr(
        httpx, "get", _responses(httpx.Response(429), httpx.Response(200, json=ok_body))
    )
    monkeypatch.setattr(
        "app.adapters.market_data.twelve_data_adapter.time.sleep", lambda _seconds: None
    )

    bars = TwelveDataAdapter("test-key").get_daily_history(
        "AAPL", None, date(2026, 1, 1), date(2026, 1, 2)
    )

    assert bars is not None
    assert len(bars) == 1


def test_429_twice_returns_none(monkeypatch):
    monkeypatch.setattr(httpx, "get", _responses(httpx.Response(429), httpx.Response(429)))
    monkeypatch.setattr(
        "app.adapters.market_data.twelve_data_adapter.time.sleep", lambda _seconds: None
    )

    bars = TwelveDataAdapter("test-key").get_daily_history(
        "AAPL", None, date(2026, 1, 1), date(2026, 1, 2)
    )

    assert bars is None


def test_malformed_row_is_skipped_not_fatal(monkeypatch):
    body = {
        "meta": {"currency": "USD"},
        "values": [
            {
                "datetime": "2026-01-02",
                "open": "1",
                "high": "1",
                "low": "1",
                "close": "1",
                "volume": "1",
            },
            {"datetime": "bad-date", "open": "x"},
        ],
        "status": "ok",
    }
    monkeypatch.setattr(httpx, "get", _responses(httpx.Response(200, json=body)))

    bars = TwelveDataAdapter("test-key").get_daily_history(
        "AAPL", None, date(2026, 1, 1), date(2026, 1, 2)
    )

    assert bars is not None
    assert len(bars) == 1
