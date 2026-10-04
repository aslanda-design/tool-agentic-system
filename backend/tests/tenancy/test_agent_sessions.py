"""Agents that handle a user's data must open sessions through `user_session()`
(which refuses to run without a tenant identity), never a bare SessionLocal()
(docs/saas/07 §3.1, ADR 0007). The security_resolver is the one exception: it
works on shared Reference data in system mode."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.persistence.session import current_user_id, user_session

AGENTS = Path(__file__).resolve().parents[2] / "ai" / "agents"


@pytest.mark.parametrize("agent", ["import_reviewer", "portfolio_assistant"])
def test_user_data_agents_never_open_a_bare_session(agent):
    source = (AGENTS / agent / "agent.py").read_text(encoding="utf-8")
    assert "SessionLocal" not in source, f"{agent} must use user_session()"


def test_user_session_refuses_to_run_without_an_identity():
    token = current_user_id.set(None)
    try:
        with pytest.raises(RuntimeError, match="No tenant identity"):
            user_session()
    finally:
        current_user_id.reset(token)


def test_user_session_is_bound_to_the_scope_identity(user_a):
    from app.adapters.persistence.session import user_scope

    with user_scope(user_a):
        db = user_session()
        try:
            assert db.info["user_id"] == str(user_a)
        finally:
            db.close()
