"""Authentication is structural: every route is authenticated unless it is on
an explicit allow-list (docs/saas/03 §4.2)."""

from __future__ import annotations

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.config import settings
from app.main import app

PUBLIC_PATHS = {
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/docs/oauth2-redirect",
}


def _depends_on(dependant, target) -> bool:
    if dependant.call is target:
        return True
    return any(_depends_on(d, target) for d in dependant.dependencies)


def test_every_route_requires_authentication_unless_allow_listed():
    unprotected = [
        f"{sorted(r.methods)} {r.path}"
        for r in app.routes
        if isinstance(r, APIRoute)
        and r.path not in PUBLIC_PATHS
        and not _depends_on(r.dependant, get_current_user)
    ]
    assert unprotected == [], f"Routes reachable without authentication: {unprotected}"


def test_unauthenticated_request_is_401(monkeypatch):
    app.dependency_overrides.pop(get_current_user, None)
    monkeypatch.setattr(settings, "auth_mode", "oidc")  # no dev bypass
    with TestClient(app) as client:
        for path in ("/api/portfolio/summary", "/api/accounts", "/api/chat/sessions"):
            assert client.get(path).status_code == 401, path


def test_health_is_public(monkeypatch):
    app.dependency_overrides.pop(get_current_user, None)
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
