"""Operator-only surface: resolver review and manual mapping are shared
Reference-data administration, not something a User may touch."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.adapters.persistence.repositories import SqlAssetRepo
from app.adapters.persistence.session import scoped_session
from app.api.deps import CurrentUser, get_current_user
from app.domain.models import AssetClass
from app.main import app

OPERATOR_ROUTES = [
    ("GET", "/api/resolutions"),
    ("GET", "/api/resolutions/recent"),
    ("GET", "/api/resolutions/1"),
    ("POST", "/api/resolutions/1/flag-for-review"),
    ("POST", "/api/assets/1/map"),
    ("GET", "/api/assets/1/map-suggestions"),
]


@pytest.mark.parametrize("method,path", OPERATOR_ROUTES)
def test_regular_users_get_404_on_operator_routes(user_a, as_user, method, path):
    as_user(user_a)
    with TestClient(app) as client:
        r = client.request(method, path, json={"note": "x", "yfinance_symbol": "X"})
    assert r.status_code == 404, f"{method} {path} should not be reachable by a normal user"


def test_operators_can_reach_the_review_queue(user_a):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=user_a, is_operator=True)
    with TestClient(app) as client:
        assert client.get("/api/resolutions").status_code == 200


def test_user_cannot_resolve_someone_elses_or_a_shared_asset(user_a, user_b, as_user):
    db = scoped_session(user_a)
    try:
        a_asset = SqlAssetRepo(db).create("ZZOWN-ROUTE", "Stub", AssetClass.ETF, "EUR", needs_mapping=True)
        db.commit()
    finally:
        db.close()
    try:
        with TestClient(app) as client:
            as_user(user_b)  # not the owner
            assert client.post(f"/api/assets/{a_asset.id}/resolve").status_code == 404
            as_user(user_a)
            # an existing shared asset (owner NULL) is not the user's to re-resolve either
            sysdb = scoped_session(system=True)
            try:
                shared_id = SqlAssetRepo(sysdb).create(
                    "ZZOWN-ROUTE-SH", "Shared", AssetClass.ETF, "EUR", shared=True
                ).id
                sysdb.commit()
            finally:
                sysdb.close()
            assert client.post(f"/api/assets/{shared_id}/resolve").status_code == 404
    finally:
        sysdb = scoped_session(system=True)
        try:
            sysdb.execute(
                __import__("sqlalchemy").text("DELETE FROM assets WHERE symbol LIKE 'ZZOWN%'")
            )
            sysdb.commit()
        finally:
            sysdb.close()
