"""MCP tool: list resolutions waiting on a decision. Thin wrapper over
ResolutionRepo.list_by_status — no logic of its own (see backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import resolution_repo
from app.domain.listings import ResolutionStatus

_DEFAULT_STATUSES = ["NEEDS_REVIEW", "NEEDS_AGENT"]


def list_pending_resolutions(statuses: list[str] | None = None, limit: int = 50) -> list[dict]:
    """List resolutions waiting on a decision.

    Args:
        statuses: Resolution statuses to include, e.g. ["NEEDS_REVIEW", "NEEDS_AGENT"].
            Defaults to both of those — the two non-terminal statuses an agent can act on.
        limit: Maximum number of resolutions to return (default 50).

    Returns:
        A list of resolutions, each with its context and every candidate.
    """
    db = SessionLocal()
    try:
        repo = resolution_repo(db)
        parsed = [ResolutionStatus(s) for s in (statuses or _DEFAULT_STATUSES)]
        resolutions = repo.list_by_status(parsed, limit=limit)
        return [to_jsonable(r) for r in resolutions]
    finally:
        db.close()
