"""Fixtures for the tenant-isolation suite (docs/saas/02 §4.6).

Two real users, real Postgres, connecting as the runtime `app_user` role (so
RLS is actually enforced — test_schema_guards checks the role really is
subject to it). Data created here is removed per user afterwards.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.adapters.persistence.session import SessionLocal, scoped_session
from app.api.deps import CurrentUser, get_current_user
from tests.conftest import _ensure_user


@pytest.fixture(scope="session")
def user_a() -> uuid.UUID:
    return _ensure_user("tenant-a")


@pytest.fixture(scope="session")
def user_b() -> uuid.UUID:
    return _ensure_user("tenant-b")


@pytest.fixture(autouse=True)
def _clean_tenants(user_a, user_b):
    yield
    for uid in (user_a, user_b):
        db = scoped_session(uid)
        try:
            # Explicit user filter on top of RLS: cleanup must be unable to touch
            # anyone else's rows EVEN IF RLS were off. (An earlier version relied
            # on RLS alone; with RLS deliberately disabled for a mutation check,
            # it deleted every account in the dev database. Never again.)
            # accounts / chat_sessions cascade to their children.
            for table in ("accounts", "chat_sessions", "portfolio_snapshots"):
                db.execute(text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": uid})
            db.commit()
        finally:
            db.close()


@pytest.fixture
def as_user():
    """Call routes as a given user (overrides the suite-wide default user)."""
    from app.main import app

    def _set(uid: uuid.UUID) -> None:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=uid)

    return _set


@pytest.fixture
def anon_db():
    """A session with NO tenant identity at all."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
