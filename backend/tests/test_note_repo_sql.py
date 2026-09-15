"""SqlNoteRepo against real Postgres — see plans/agentic_asset_mapping_phase7_8.md
Phase 8c. Needs a running Postgres (see backend/AGENTS.md: `docker compose
up -d db`) — same requirement as test_health.py. Uses a session rolled back
at the end of each test rather than committed, so nothing is left behind in
the dev DB.
"""

from __future__ import annotations

import pytest

from app.adapters.persistence.repositories import SqlNoteRepo, SqlPortfolioRepo
from app.adapters.persistence.session import SessionLocal


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_add_and_list_round_trip(db):
    repo = SqlNoteRepo(db)

    note_id = repo.add("weekly_report", "portfolio", "Weekly summary", "Everything looks normal.")

    notes = repo.list(scope="portfolio")
    assert any(n.id == note_id and n.title == "Weekly summary" for n in notes)


def test_list_filters_by_scope(db):
    repo = SqlNoteRepo(db)
    repo.add("weekly_report", "portfolio", "Portfolio note", "body")
    account = SqlPortfolioRepo(db).get_or_create_account("test-notes", "acc1", "Test", "EUR", "manual")
    repo.add("import_reviewer", "account", "Account note", "body", account_id=account.id)

    portfolio_notes = repo.list(scope="portfolio")
    account_notes = repo.list(scope="account")

    assert all(n.scope == "portfolio" for n in portfolio_notes)
    assert all(n.scope == "account" for n in account_notes)
    assert any(n.account_id == account.id for n in account_notes)


def test_list_orders_most_recent_first_and_respects_limit(db):
    repo = SqlNoteRepo(db)
    for i in range(3):
        repo.add("weekly_report", "portfolio", f"Note {i}", "body")

    notes = repo.list(scope="portfolio", limit=2)

    assert len(notes) == 2
    assert notes[0].created_at >= notes[1].created_at


def test_dismiss_sets_dismissed_at(db):
    repo = SqlNoteRepo(db)
    note_id = repo.add("weekly_report", "portfolio", "Note", "body")
    assert repo.list(scope="portfolio")[0].dismissed_at is None

    repo.dismiss(note_id)

    dismissed = next(n for n in repo.list(scope="portfolio") if n.id == note_id)
    assert dismissed.dismissed_at is not None


def test_dismiss_unknown_id_is_a_harmless_no_op(db):
    SqlNoteRepo(db).dismiss(999999)  # must not raise
