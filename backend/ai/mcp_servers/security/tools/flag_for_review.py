"""MCP tool: hand a resolution to a human instead of deciding it. WRITE,
TERMINAL (for this agent turn) — thin wrapper over
ResolveSecurityUseCase.flag_for_review, commits on success (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_resolve_security_use_case
from app.domain.errors import DomainError, ResolutionNotFoundError


def flag_for_review(resolution_id: int, reason: str) -> dict:
    """Hand a resolution to a human instead of deciding it. WRITE, TERMINAL.

    Use this when no candidate is confidently correct — e.g. every
    candidate lacks a recent price, or several are equally plausible.

    Args:
        resolution_id: The asset_resolutions row id.
        reason: A short explanation of why the agent couldn't decide —
            stored on the resolution and shown to the human reviewer.

    Returns:
        The flagged resolution on success, or {"error": "..."} if the
        resolution doesn't exist or is already decided.
    """
    db = SessionLocal()
    try:
        use_case = build_resolve_security_use_case(db)
        try:
            resolution = use_case.flag_for_review(resolution_id, note=reason, flagged_by="agent")
        except (ResolutionNotFoundError, DomainError) as exc:
            db.rollback()
            return {"error": str(exc)}
        db.commit()
        return to_jsonable(resolution)
    finally:
        db.close()
