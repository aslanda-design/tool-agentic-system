"""End to end: user B cannot see or touch user A's data through the real API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

SECRET = "A-secret-marker-9f31"


def test_accounts_are_isolated_over_http(user_a, user_b, as_user):
    with TestClient(app) as client:
        as_user(user_a)
        r = client.post(
            "/api/accounts", json={"broker_key": "ibkr", "name": SECRET, "currency": "EUR"}
        )
        assert r.status_code == 200
        assert SECRET in client.get("/api/accounts").text

        as_user(user_b)
        listing = client.get("/api/accounts")
        assert listing.status_code == 200
        assert SECRET not in listing.text


def test_chat_sessions_are_isolated_and_foreign_ids_are_404(user_a, user_b, as_user):
    with TestClient(app) as client:
        as_user(user_a)
        sid = client.post("/api/chat/sessions", json={"title": SECRET}).json()["id"]

        as_user(user_b)
        assert SECRET not in client.get("/api/chat/sessions").text
        # existence of A's session must not be revealed: 404, never 403
        assert client.get(f"/api/chat/sessions/{sid}").status_code == 404


def test_other_read_routes_never_mix_users(user_a, user_b, as_user):
    with TestClient(app) as client:
        as_user(user_a)
        client.post(
            "/api/accounts", json={"broker_key": "ibkr", "name": SECRET, "currency": "EUR"}
        )
        as_user(user_b)
        for path in ("/api/portfolio/summary", "/api/positions", "/api/notes"):
            assert SECRET not in client.get(path).text, path
