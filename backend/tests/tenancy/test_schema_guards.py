"""Structural guarantees: adding a table or weakening a role fails CI."""

from __future__ import annotations

from sqlalchemy import text

from app.adapters.persistence.session import SessionLocal

PRIVATE_TABLES = {
    "accounts",
    "holdings",
    "transactions",
    "cash_balances",
    "position_snapshots",
    "portfolio_snapshots",
    "ai_notes",
    "chat_sessions",
    "chat_messages",
    "quant_runs",
    "agent_runs",
}


def test_runtime_role_is_subject_to_rls():
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert not row.rolsuper and not row.rolbypassrls, (
        "The test/runtime DB role bypasses RLS; connect as app_user (docs/saas/02 §4.1)"
    )


def test_every_table_with_user_id_has_forced_rls_and_a_policy():
    with SessionLocal() as db:
        offenders = (
            db.execute(
                text(
                    """
                    SELECT c.relname
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
                    JOIN pg_attribute a
                      ON a.attrelid = c.oid AND a.attname = 'user_id' AND NOT a.attisdropped
                    WHERE c.relkind = 'r'
                      AND (NOT c.relrowsecurity
                           OR NOT c.relforcerowsecurity
                           OR NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid))
                    """
                )
            )
            .scalars()
            .all()
        )
    assert offenders == [], f"Tables with user_id but no enforced RLS policy: {offenders}"


def test_private_tables_match_the_documented_classification():
    """Keeps CONTEXT.md's Private/Reference split and the database in step."""
    with SessionLocal() as db:
        have = set(
            db.execute(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND column_name='user_id'"
                )
            ).scalars()
        )
    assert have == PRIVATE_TABLES, f"user_id tables changed: {have ^ PRIVATE_TABLES}"


def test_user_id_is_not_nullable_except_for_system_agent_runs():
    with SessionLocal() as db:
        nullable = set(
            db.execute(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND column_name='user_id' AND is_nullable='YES'"
                )
            ).scalars()
        )
    assert nullable == {"agent_runs"}

