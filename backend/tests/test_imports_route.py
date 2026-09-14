"""One DB-backed integration test for the import endpoints, exercising the
real `SqlPortfolioRepo` and the real `uq_transactions_account_external_id`
constraint (the fakes-based tests in test_csv_import_use_case.py prove the
same dedup *logic*, but against an in-memory dict, not Postgres).

Needs a running Postgres (see backend/AGENTS.md: `docker compose up -d db`)
— same requirement as test_health.py. Unlike test_health.py, this test
writes data: it creates a manual account via the real API. There is
currently no DELETE endpoint for accounts anywhere in this app, so running
it leaves that account behind permanently. Prefer running this against a
disposable database, not one holding real portfolio data; if you do run it
against your dev DB, the account is named clearly enough (`broker_key=
"test-myinvestor-import"`) to find and drop by hand later.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

REAL_HEADER = "Fecha de la orden;ISIN;Importe estimado;Nº de participaciones;Estado\n"
EXPORT = (
    REAL_HEADER + "04/09/2026;IE00BYX5MX67;500 EUR;30,444;Finalizada\n"
    "01/06/2026;IE00BYX5MX67;250 EUR;15,492;Finalizada\n"
).encode("utf-8")


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
