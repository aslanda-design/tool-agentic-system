"""Route-level tests for app/api/routes/market_intel.py (Phase 3 of
plans/market_intel.md) — same shape as test_chat_routes.py's disabled/
enabled pair: TestClient + monkeypatched settings flag, and the use case
itself monkeypatched so this never makes a real network call."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.routes import market_intel as market_intel_route
from app.main import app


class _FakeUseCase:
    def get_all(self, symbol):
        return {
            "news": [{"title": "T"}],
            "filings": [],
            "sentiment": {"agreement": "no_data", "sources": []},
        }


def test_get_asset_market_intel_is_409_when_disabled(monkeypatch):
    monkeypatch.setattr(market_intel_route.settings, "market_intel_enabled", False)
    with TestClient(app) as client:
        response = client.get("/api/market-intel/assets/AAPL")
        assert response.status_code == 409


def test_get_asset_market_intel_returns_combined_result_when_enabled(monkeypatch):
    monkeypatch.setattr(market_intel_route.settings, "market_intel_enabled", True)
    monkeypatch.setattr(
        market_intel_route.container, "build_get_asset_intel_use_case", lambda db: _FakeUseCase()
    )
    with TestClient(app) as client:
        response = client.get("/api/market-intel/assets/AAPL")
        assert response.status_code == 200
        assert response.json() == {
            "news": [{"title": "T"}],
            "filings": [],
            "sentiment": {"agreement": "no_data", "sources": []},
        }
