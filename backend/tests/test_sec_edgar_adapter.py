"""Tests for SecEdgarAdapter — monkeypatches httpx.get so this never makes
a real network call. Also covers the User-Agent gate: SEC's fair-access
policy requires one on every request, so an adapter with no
SEC_EDGAR_USER_AGENT configured must never call out at all (see
sec_edgar_adapter.py's module docstring)."""

from __future__ import annotations

import httpx

from app.adapters.market_intel import sec_edgar_adapter
from app.adapters.market_intel.sec_edgar_adapter import SecEdgarAdapter


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json


def _reset_module_cache(monkeypatch):
    monkeypatch.setattr(sec_edgar_adapter, "_ticker_to_cik_cache", {})
    monkeypatch.setattr(sec_edgar_adapter, "_ticker_cache_fetched_at", 0.0)


def test_get_filings_returns_none_without_user_agent():
    assert SecEdgarAdapter(user_agent="").get_filings("AAPL") is None


def test_get_filings_returns_empty_list_for_unknown_ticker(monkeypatch):
    _reset_module_cache(monkeypatch)
    tickers_response = _FakeResponse({"0": {"ticker": "AAPL", "cik_str": 320193}})
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: tickers_response)

    result = SecEdgarAdapter(user_agent="Test app test@example.com").get_filings("VWCE.DE")

    assert result == []


def test_get_filings_returns_relevant_forms_only(monkeypatch):
    _reset_module_cache(monkeypatch)
    tickers_response = _FakeResponse({"0": {"ticker": "AAPL", "cik_str": 320193}})
    submissions_response = _FakeResponse(
        {
            "filings": {
                "recent": {
                    "form": ["8-K", "10-K", "4"],
                    "filingDate": ["2026-09-15", "2026-09-01", "2026-09-10"],
                    "accessionNumber": ["0001-26-000001", "0001-26-000002", "0001-26-000003"],
                    "primaryDocument": ["a.htm", "b.htm", "c.htm"],
                    "primaryDocDescription": [
                        "Material event",
                        "Annual report",
                        "Insider transaction",
                    ],
                }
            }
        }
    )
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **kwargs: (
            tickers_response if url == sec_edgar_adapter.TICKERS_URL else submissions_response
        ),
    )

    result = SecEdgarAdapter(user_agent="Test app test@example.com").get_filings("AAPL")

    assert [f.form_type for f in result] == ["8-K", "4"]
    assert result[0].url == "https://www.sec.gov/Archives/edgar/data/320193/000126000001/a.htm"


def test_get_filings_returns_none_on_submissions_error(monkeypatch):
    _reset_module_cache(monkeypatch)
    tickers_response = _FakeResponse({"0": {"ticker": "AAPL", "cik_str": 320193}})

    def _get(url, **kwargs):
        if url == sec_edgar_adapter.TICKERS_URL:
            return tickers_response
        return _FakeResponse({}, status_code=500)

    monkeypatch.setattr(httpx, "get", _get)

    assert SecEdgarAdapter(user_agent="Test app test@example.com").get_filings("AAPL") is None
