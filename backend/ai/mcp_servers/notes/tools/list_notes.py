"""MCP tool: list saved notes. Thin wrapper over NoteRepo.list — no logic
of its own (see backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import note_repo


def list_notes(scope: str | None = None, limit: int = 20) -> list[dict]:
    """List saved notes, most recent first.

    Args:
        scope: "account" or "portfolio" to filter. Omit for both.
        limit: Maximum number of notes to return (default 20).

    Returns:
        Notes including dismissed ones — filter on dismissed_at is None
        yourself if you only want active ones.
    """
    db = SessionLocal()
    try:
        return [to_jsonable(n) for n in note_repo(db).list(scope=scope, limit=limit)]
    finally:
        db.close()
