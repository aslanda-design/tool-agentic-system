"""One DB-backed integration test for the import endpoints, exercising the
real `SqlPortfolioRepo` and the real `uq_transactions_account_external_id`
constraint (the fakes-based tests in test_csv_import_use_case.py prove the
same dedup *logic*, but against an in-memory dict, not Postgres).

Needs a running Postgres (see backend/AGENTS.md: `docker compose up -d db`)
— same requirement as test_health.py. This test writes data via the real
API: a manual account (`broker_key="test-myinvestor-import"`) plus its
imported transactions/holdings. There's no DELETE endpoint for accounts, so
the `_cleanup_test_account` fixture below removes them directly via SQL
after each test — without it, a second run of this file (or the whole
suite) sees pre-existing rows and its "first import" assertions fail, since
the import is then actually a re-import. Prefer running against a
disposable database regardless; this cleanup is a safety net, not a
substitute for one holding real portfolio data.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.adapters.persistence.session import SessionLocal
from app.main import app

REAL_HEADER = "Fecha de la orden;ISIN;Importe estimado;Nº de participaciones;Estado\n"
EXPORT = (
    REAL_HEADER + "04/09/2026;IE00BYX5MX67;500 EUR;30,444;Finalizada\n"
    "01/06/2026;IE00BYX5MX67;250 EUR;15,492;Finalizada\n"
).encode("utf-8")


@pytest.fixture(autouse=True)
def _cleanup_test_account():
    yield
    session = SessionLocal()
    try:
        ids = session.execute(
            text("SELECT id FROM accounts WHERE broker_key = 'test-myinvestor-import'")
        ).scalars().all()
        for account_id in ids:
            session.execute(text("DELETE FROM transactions WHERE account_id = :id"), {"id": account_id})
            session.execute(text("DELETE FROM holdings WHERE account_id = :id"), {"id": account_id})
            session.execute(text("DELETE FROM accounts WHERE id = :id"), {"id": account_id})
        session.commit()
    finally:
        session.close()


def test_reimport_via_api_is_idempotent_against_real_postgres():
    with TestClient(app) as client:
        account = client.post(
            "/api/accounts",
            json={"broker_key": "test-myinvestor-import", "name": "Test MyInvestor Import", "currency": "EUR"},
        ).json()
        account_id = account["id"]

        first = client.post(
            f"/api/imports/commit?account_id={account_id}&format=myinvestor",
            files={"file": ("export.csv", EXPORT, "text/csv")},
        )
        assert first.status_code == 200
        assert first.json()["transactions_added"] == 2

        second = client.post(
            f"/api/imports/commit?account_id={account_id}&format=myinvestor",
            files={"file": ("export.csv", EXPORT, "text/csv")},
        )
        assert second.status_code == 200
        assert second.json()["transactions_added"] == 0

        positions = client.get(f"/api/positions?account_id={account_id}").json()
        # two BUYs of the same asset, not four — the second commit inserted nothing.
        matching = [p for p in positions if p["symbol"] == "IE00BYX5MX67"]
        assert len(matching) == 1


def test_commit_with_unmapped_required_columns_returns_422_with_string_detail():
    with TestClient(app) as client:
        account = client.post(
            "/api/accounts",
            json={"broker_key": "test-myinvestor-import", "name": "Test MyInvestor Import", "currency": "EUR"},
        ).json()
        account_id = account["id"]

        response = client.post(
            f"/api/imports/commit?account_id={account_id}&format=myinvestor",
            files={"file": ("bad.csv", b"Producto;Estado\nBGF World;Finalizada\n", "text/csv")},
        )
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], str)
