"""MCP tool: fetch one resolution by id. Thin wrapper over ResolutionRepo.get
— no logic of its own (see backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import resolution_repo


def get_resolution(resolution_id: int) -> dict | None:
    """Get one resolution by id, with its context and every candidate.

    Args:
        resolution_id: The asset_resolutions row id.

    Returns:
        The resolution as a dict, or None if it doesn't exist.
    """
    db = SessionLocal()
    try:
        repo = resolution_repo(db)
        resolution = repo.get(resolution_id)
        return to_jsonable(resolution) if resolution is not None else None
    finally:
        db.close()
