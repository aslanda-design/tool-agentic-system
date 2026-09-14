"""Tests for OpenFigiSecurityMaster — see plans/agentic_asset_mapping.md
Phase 3. Mocks the HTTP layer (`httpx.post`) rather than hitting the real
OpenFIGI API, so these stay fast and don't depend on network/rate limits."""

from __future__ import annotations

import httpx

from app.adapters.security_master.openfigi_adapter import OpenFigiSecurityMaster


def _responses(*responses):
    """Return a fake `httpx.post` that yields these responses in order on
    successive calls. Each response gets a dummy `.request` attached —
    `raise_for_status()` requires one, and a bare `httpx.Response(...)`
    built by hand (rather than returned by a real client) doesn't have it."""
    it = iter(responses)

    def fake_post(*args, **kwargs):
        response = next(it)
        response.request = httpx.Request("POST", "https://api.openfigi.com/v3/mapping")
        return response

    return fake_post


def test_map_isin_returns_listings_from_data(monkeypatch):
    body = [
        {
            "data": [
                {
                    "ticker": "EUNL",
                    "exchCode": "GY",
                    "name": "ISHARES CORE MSCI WORLD",
                    "securityType": "ETP",
                    "shareClassFIGI": "BBG000BLNNH6",
                },
                {"ticker": "IWDA", "exchCode": "LN", "name": "ISHARES CORE MSCI WORLD", "securityType": "ETP"},
            ]
        }
    ]
    monkeypatch.setattr(httpx, "post", _responses(httpx.Response(200, json=body)))

    result = OpenFigiSecurityMaster().map_isin("IE00B4L5Y983")

    assert result is not None
    assert len(result) == 2
    assert result[0].ticker == "EUNL"
    assert result[0].exch_code == "GY"
    assert result[0].share_class_figi == "BBG000BLNNH6"
    assert result[1].share_class_figi is None


def test_map_isin_warning_means_unknown_isin_returns_empty_list(monkeypatch):
    """A `warning` response is a real, final answer — distinct from None
    (provider unavailable, see SecurityMasterPort.map_isin's docstring)."""
    monkeypatch.setattr(
        httpx, "post", _responses(httpx.Response(200, json=[{"warning": "No identifier found."}]))
    )

    result = OpenFigiSecurityMaster().map_isin("XX0000000000")

    assert result == []


def test_map_isin_skips_listings_missing_ticker_or_exchange(monkeypatch):
    body = [{"data": [{"ticker": "EUNL"}, {"exchCode": "GY"}, {"ticker": "OK", "exchCode": "US"}]}]
    monkeypatch.setattr(httpx, "post", _responses(httpx.Response(200, json=body)))

    result = OpenFigiSecurityMaster().map_isin("IE00B4L5Y983")

    assert [listing.ticker for listing in result] == ["OK"]


def test_map_isin_retries_once_on_429_then_succeeds(monkeypatch):
    ok_body = [{"data": [{"ticker": "OK", "exchCode": "US"}]}]
    monkeypatch.setattr(
        httpx,
        "post",
        _responses(
            httpx.Response(429, headers={"retry-after": "0"}),
            httpx.Response(200, json=ok_body),
        ),
    )
    monkeypatch.setattr("app.adapters.security_master.openfigi_adapter.time.sleep", lambda _s: None)

    result = OpenFigiSecurityMaster().map_isin("IE00B4L5Y983")

    assert result is not None
    assert result[0].ticker == "OK"


def test_map_isin_returns_none_after_second_429(monkeypatch):
    monkeypatch.setattr(
        httpx, "post", _responses(httpx.Response(429, headers={"retry-after": "0"}), httpx.Response(429))
    )
    monkeypatch.setattr("app.adapters.security_master.openfigi_adapter.time.sleep", lambda _s: None)

    assert OpenFigiSecurityMaster().map_isin("IE00B4L5Y983") is None


def test_map_isin_returns_none_on_server_error(monkeypatch):
    monkeypatch.setattr(httpx, "post", _responses(httpx.Response(500)))

    assert OpenFigiSecurityMaster().map_isin("IE00B4L5Y983") is None


def test_map_isin_returns_none_on_network_error(monkeypatch):
    def raise_connect_error(*args, **kwargs):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "post", raise_connect_error)

    assert OpenFigiSecurityMaster().map_isin("IE00B4L5Y983") is None


def test_api_key_is_sent_only_when_configured(monkeypatch):
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["headers"] = headers
        response = httpx.Response(200, json=[{"data": []}])
        response.request = httpx.Request("POST", url)
        return response

    monkeypatch.setattr(httpx, "post", fake_post)

    OpenFigiSecurityMaster(api_key="").map_isin("IE00B4L5Y983")
    assert "X-OPENFIGI-APIKEY" not in captured["headers"]

    OpenFigiSecurityMaster(api_key="secret").map_isin("IE00B4L5Y983")
    assert captured["headers"]["X-OPENFIGI-APIKEY"] == "secret"
