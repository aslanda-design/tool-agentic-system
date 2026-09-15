"""SqlChatRepo against real Postgres — see plans/agentic_asset_mapping_phase7_8.md
Phase 8f. Needs a running Postgres (see backend/AGENTS.md: `docker compose
up -d db`) — same requirement as test_health.py. Uses a session rolled back
at the end of each test rather than committed, so nothing is left behind in
the dev DB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.adapters.persistence.repositories import SqlChatRepo
from app.adapters.persistence.session import SessionLocal


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_create_and_get_session_round_trip(db):
    repo = SqlChatRepo(db)

    session_id = repo.create_session("My first chat")

    session = repo.get_session(session_id)
    assert session is not None
    assert session.title == "My first chat"
    assert session.created_at is not None
    assert session.updated_at is not None


def test_get_session_returns_none_for_unknown_id(db):
    assert SqlChatRepo(db).get_session(999999) is None


def test_list_sessions_orders_by_updated_at_most_recent_first(db):
    repo = SqlChatRepo(db)
    old = repo.create_session("Older")
    new = repo.create_session("Newer")
    repo.touch_session(old, as_of=datetime.now(timezone.utc) - timedelta(days=1))
    repo.touch_session(new, as_of=datetime.now(timezone.utc))

    sessions = repo.list_sessions()

    ids_in_order = [s.id for s in sessions if s.id in (old, new)]
    assert ids_in_order == [new, old]


def test_rename_session(db):
    repo = SqlChatRepo(db)
    session_id = repo.create_session("Untitled")

    repo.rename_session(session_id, "What's my P&L?")

    assert repo.get_session(session_id).title == "What's my P&L?"


def test_rename_unknown_session_is_a_harmless_no_op(db):
    SqlChatRepo(db).rename_session(999999, "x")  # must not raise


def test_add_and_list_messages_in_transcript_order(db):
    repo = SqlChatRepo(db)
    session_id = repo.create_session()

    repo.add_message(session_id, "user", "what's my total value?")
    repo.add_message(session_id, "assistant", "€15,744.", tool_calls=[{"name": "get_portfolio_summary"}])

    messages = repo.list_messages(session_id)

    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "what's my total value?"
    assert messages[1].tool_calls == [{"name": "get_portfolio_summary"}]


def test_list_messages_limit_keeps_the_most_recent_in_oldest_first_order(db):
    repo = SqlChatRepo(db)
    session_id = repo.create_session()
    for i in range(5):
        repo.add_message(session_id, "user", f"message {i}")

    messages = repo.list_messages(session_id, limit=2)

    assert [m.content for m in messages] == ["message 3", "message 4"]


def test_delete_session_cascades_to_its_messages(db):
    repo = SqlChatRepo(db)
    session_id = repo.create_session()
    repo.add_message(session_id, "user", "hi")

    repo.delete_session(session_id)

    assert repo.get_session(session_id) is None
    assert repo.list_messages(session_id) == []


def test_delete_unknown_session_is_a_harmless_no_op(db):
    SqlChatRepo(db).delete_session(999999)  # must not raise


def test_touch_session_bumps_updated_at(db):
    repo = SqlChatRepo(db)
    session_id = repo.create_session()
    original = repo.get_session(session_id).updated_at

    repo.touch_session(session_id, as_of=original + timedelta(minutes=5))

    assert repo.get_session(session_id).updated_at > original
