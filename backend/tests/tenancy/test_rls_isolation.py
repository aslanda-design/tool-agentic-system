"""Row-level security behaviour, straight against Postgres (no HTTP)."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.adapters.persistence.session import scoped_session


def _add_account(uid, name="Main", ext="X1"):
    db = scoped_session(uid)
    try:
        acc_id = db.execute(
            text(
                "INSERT INTO accounts (broker_key, external_id, name, currency, source) "
                "VALUES ('ibkr', :e, :n, 'EUR', 'manual') RETURNING id"
            ),
            {"e": ext, "n": name},
        ).scalar_one()
        db.commit()
        return acc_id
    finally:
        db.close()


def test_user_id_is_filled_from_identity_not_from_the_caller(user_a):
    _add_account(user_a)
    db = scoped_session(user_a)
    try:
        assert db.execute(text("SELECT user_id FROM accounts")).scalar_one() == user_a
    finally:
        db.close()


def test_other_user_sees_nothing(user_a, user_b):
    _add_account(user_a)
    db = scoped_session(user_b)
    try:
        assert db.execute(text("SELECT count(*) FROM accounts")).scalar_one() == 0
    finally:
        db.close()


def test_no_identity_fails_closed(user_a, anon_db):
    _add_account(user_a)
    assert anon_db.execute(text("SELECT count(*) FROM accounts")).scalar_one() == 0


def test_cannot_insert_a_row_owned_by_someone_else(user_a, user_b):
    db = scoped_session(user_b)
    try:
        with pytest.raises(DBAPIError):  # WITH CHECK violation
            db.execute(
                text(
                    "INSERT INTO accounts (user_id, broker_key, external_id, name, currency, source) "
                    "VALUES (:u, 'ibkr', 'evil', 'x', 'EUR', 'manual')"
                ),
                {"u": user_a},
            )
    finally:
        db.rollback()
        db.close()


def test_cannot_update_or_delete_another_users_row(user_a, user_b):
    acc = _add_account(user_a)
    db = scoped_session(user_b)
    try:
        upd = db.execute(text("UPDATE accounts SET name='pwned' WHERE id=:i"), {"i": acc})
        dele = db.execute(text("DELETE FROM accounts WHERE id=:i"), {"i": acc})
        assert upd.rowcount == 0 and dele.rowcount == 0
        db.commit()
    finally:
        db.close()
    check = scoped_session(user_a)
    try:
        assert check.execute(text("SELECT name FROM accounts")).scalar_one() == "Main"
    finally:
        check.close()


def test_cannot_reassign_own_row_to_another_user(user_a, user_b):
    _add_account(user_a)
    db = scoped_session(user_a)
    try:
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE accounts SET user_id = :b"), {"b": user_b})
    finally:
        db.rollback()
        db.close()


def test_same_external_account_id_allowed_for_different_users(user_a, user_b):
    # Uniqueness is per user now: two users may both hold broker account "U123".
    _add_account(user_a, ext="U123")
    _add_account(user_b, ext="U123")


def test_child_row_cannot_point_at_another_users_parent(user_a, user_b):
    acc_a = _add_account(user_a)
    db = scoped_session(user_b)
    try:
        with pytest.raises(DBAPIError):
            db.execute(
                text(
                    "INSERT INTO cash_balances (account_id, currency, amount, as_of) "
                    "VALUES (:a, 'EUR', 1, now())"
                ),
                {"a": acc_a},
            )
    finally:
        db.rollback()
        db.close()


def test_identity_survives_a_mid_request_commit(user_a):
    """The trap in docs/saas/02 §4.3: commit() ends the transaction, and the
    next transaction must be re-scoped rather than silently empty."""
    _add_account(user_a)
    db = scoped_session(user_a)
    try:
        assert db.execute(text("SELECT count(*) FROM accounts")).scalar_one() == 1
        db.commit()
        assert db.execute(text("SELECT count(*) FROM accounts")).scalar_one() == 1
    finally:
        db.close()


def test_users_table_shows_only_your_own_row(user_a, user_b):
    db = scoped_session(user_a)
    try:
        assert db.execute(text("SELECT id FROM users")).scalars().all() == [user_a]
    finally:
        db.close()
