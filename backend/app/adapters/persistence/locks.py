"""Postgres advisory locks: "exactly one of these processes does X".

Used to make an accidental second scheduler harmless (docs/saas/01 §4.1). The
lock lives as long as the dedicated connection holding it, so it is released
automatically if the process dies — no stale lock files.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

from app.config import settings

SCHEDULER_LOCK_KEY = 726_001  # arbitrary, app-wide unique

# NullPool: close() must really end the Postgres session, because a session-level
# advisory lock outlives a connection that merely goes back to a pool.
_lock_engine = create_engine(settings.database_url, poolclass=NullPool)


def try_acquire(key: int) -> Connection | None:
    """Return the connection holding the lock, or None if someone else has it.
    The caller must keep the connection open for as long as it wants the lock."""
    conn = _lock_engine.connect()
    got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar_one()
    conn.commit()
    if not got:
        conn.close()
        return None
    return conn
