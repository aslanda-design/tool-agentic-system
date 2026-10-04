"""Database sessions and tenant identity.

Every Private-data table is protected by Postgres row-level security keyed on
the per-transaction setting `app.user_id` (migration 0009,
docs/saas/02-multi-tenancy-and-rls.md). This module is the ONE place that
setting is applied:

  * `current_user_id` is a ContextVar holding "who is this work for".
  * An `after_begin` hook re-applies it at the start of EVERY transaction.
    It must be per-transaction, not per-session: repositories call
    `db.commit()` mid-request, which ends the transaction and discards a
    transaction-local setting; the next statement would otherwise run with no
    identity and silently see zero rows.
  * `set_config(..., true)` is transaction-local (like SET LOCAL) so nothing
    leaks to the next request reusing a pooled connection.

With no identity set, RLS matches no rows: a forgotten identity fails closed.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# str(UUID) of the User the current work belongs to, or None (no identity).
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)

# MCP server subprocesses are spawned BY the API/agent runner with the user
# fixed in the environment (ADR 0007). It is read once at import and can never
# be changed by a tool argument — the model has no way to name another user.
_PROCESS_USER_ID: str | None = os.environ.get("APP_USER_ID") or None


@event.listens_for(SessionLocal, "after_begin")
def _apply_identity(session, transaction, connection) -> None:
    # session.info wins: FastAPI runs each sync dependency in its own thread
    # context, so a ContextVar set in a dependency is invisible to the endpoint.
    # The ContextVar covers jobs/agents/tests (`user_scope`).
    uid = session.info.get("user_id") or current_user_id.get() or _PROCESS_USER_ID
    if uid:
        connection.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": uid})


@contextmanager
def user_scope(user_id: uuid.UUID | str) -> Iterator[None]:
    """Run a block of work as `user_id` (jobs, agents, tests)."""
    token = current_user_id.set(str(user_id))
    try:
        yield
    finally:
        current_user_id.reset(token)


def raw_db() -> Generator[Session, None, None]:
    """A session with NO tenant identity. Only for the auth layer (login,
    session lookup) and the SECURITY DEFINER helpers — never for business
    data, where it would (correctly) see nothing."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def scoped_session(user_id: uuid.UUID | str) -> Session:
    """A session bound to `user_id` regardless of thread/context. The
    caller owns closing it. Used by `app.api.deps.get_db` and by agents."""
    db = SessionLocal()
    db.info["user_id"] = str(user_id)
    return db


def list_active_user_ids() -> list[uuid.UUID]:
    """Every non-deleted user (for the scheduler's per-user loops). Goes
    through a SECURITY DEFINER function because the `users` table itself is
    only visible per-user."""
    with SessionLocal() as db:
        rows = db.execute(text("SELECT auth_list_active_user_ids()")).scalars().all()
        db.rollback()
    return list(rows)


def for_each_user(job: Callable[[Session], None]) -> None:
    """Run `job(db)` once per active user, each in its own identity scope and
    transaction. One user's failure must not stop the others."""
    for uid in list_active_user_ids():
        with user_scope(uid):
            db = SessionLocal()
            try:
                job(db)
            except Exception:
                logger.exception("per-user job failed for user %s", uid)
                db.rollback()
            finally:
                db.close()


def check_rls_enforced() -> None:
    """Startup safety check: the runtime role must be subject to RLS.

    A superuser or BYPASSRLS role (e.g. connecting as the table owner) makes
    every policy a no-op — the single most common way RLS silently fails.
    Fatal outside dev; a loud warning in dev so local single-URL setups work.
    """
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT rolsuper, rolbypassrls, current_user FROM pg_roles WHERE rolname = current_user")
        ).one()
        db.rollback()
    if row.rolsuper or row.rolbypassrls:
        msg = (
            f"Database role {row.current_user!r} bypasses row-level security "
            "(superuser/BYPASSRLS). Tenant isolation is NOT enforced. Connect as `app_user`."
        )
        if settings.app_env == "dev":
            logger.warning(msg)
        else:
            raise RuntimeError(msg)
