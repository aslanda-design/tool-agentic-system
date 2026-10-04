"""Tests for GdeltAdapter — monkeypatches httpx.get so this never makes a
real network call. Covers the quirks confirmed against GDELT's own
documented behavior (see gdelt_adapter.py's module docstring): no
per-article tone field, an empty (non-JSON) body means "no matches", and
its stricter (1 req/5s) rate limit surfaces as a 429."""

from __future__ import annotations

import httpx

from app.adapters.market_intel.gdelt_adapter import GdeltAdapter


class _FakeResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code

    def json(self):
        import json

        return json.loads(self.text)


def test_get_headlines_parses_articles(monkeypatch):
    response = _FakeResponse(
        '{"articles": [{"title": "Banks rally", "url": "https://example.com/a", '
        '"seendate": "20260918T121500Z", "domain": "example.com"}]}'
    )
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: response)

    result = GdeltAdapter().get_headlines("European bank stocks")

    assert len(result) == 1
    assert result[0].title == "Banks rally"
    assert result[0].publisher == "example.com"
    assert result[0].source == "gdelt"


def test_get_headlines_skips_articles_missing_required_fields(monkeypatch):
    response = _FakeResponse(
        '{"articles": [{"title": "No url"}, {"title": "Fine", "url": "u", "seendate": "20260918T000000Z"}]}'
    )
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: response)

    result = GdeltAdapter().get_headlines("topic")

    assert len(result) == 1
    assert result[0].title == "Fine"


def test_get_headlines_returns_empty_list_for_empty_body(monkeypatch):
    """GDELT's own "no matching articles" response is an empty body, not
    {"articles": []} — confirmed against the documented behavior."""
    response = _FakeResponse("")
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: response)

    assert GdeltAdapter().get_headlines("no results for this") == []


def test_get_headlines_returns_none_on_rate_limit(monkeypatch):
    response = _FakeResponse("Please limit requests to one every 5 seconds", status_code=429)
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: response)

    assert GdeltAdapter().get_headlines("topic") is None


def test_get_headlines_returns_none_on_network_error(monkeypatch):
    def _raise(url, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "get", _raise)

    assert GdeltAdapter().get_headlines("topic") is None
