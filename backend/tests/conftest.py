from __future__ import annotations

import pytest
from openpyxl import Workbook


@pytest.fixture
def make_xlsx(tmp_path):
    """Build minimal XLSX bytes from a list of rows (first row = headers).
    Keeps fixtures readable/diffable in the test file itself instead of a
    checked-in binary blob."""

    def _make(rows: list[list[object]], sheet_name: str = "Sheet1") -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = sheet_name
        for row in rows:
            ws.append(row)
        path = tmp_path / f"{sheet_name}.xlsx"
        wb.save(path)
        return path.read_bytes()

    return _make


# --- tenant identity for the whole suite (docs/saas/02) --------------------
# Tests run as a dedicated user, never the legacy dev user that owns the
# founder's real data in the dev database. Route tests get the same identity
# through a dependency override; direct SessionLocal() users get it through
# the identity ContextVar.

import uuid

from sqlalchemy import text

from app.adapters.persistence.session import SessionLocal, current_user_id
from app.api.deps import CurrentUser, get_current_user


def _ensure_user(subject: str) -> uuid.UUID:
    with SessionLocal() as db:
        uid = db.execute(
            text("SELECT auth_upsert_user(:s, :e, true)"), {"s": subject, "e": f"{subject}@test"}
        ).scalar_one()
        db.commit()
    return uid


@pytest.fixture(scope="session")
def tenant_user_id() -> uuid.UUID:
    return _ensure_user("pytest-user")


@pytest.fixture(autouse=True)
def _tenant_identity(tenant_user_id):
    from app.main import app

    token = current_user_id.set(str(tenant_user_id))
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=tenant_user_id, email="pytest-user@test"
    )
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        current_user_id.reset(token)
