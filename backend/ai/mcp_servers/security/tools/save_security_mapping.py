"""MCP tool: apply a candidate as the asset's market-data mapping. WRITE,
TERMINAL — thin wrapper over ResolveSecurityUseCase.accept, commits on
success (see backend/ai/AGENTS.md). This is the resolver's one real
"decision" action available to an agent."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_resolve_security_use_case
from app.domain.errors import DomainError, ResolutionNotFoundError


def save_security_mapping(resolution_id: int, candidate_id: int, reason: str) -> dict:
    """Apply a candidate as the asset's market-data mapping. WRITE, TERMINAL.

    This ends the resolution: it moves to RESOLVED_BY_AGENT and its
    `asset.symbol`/currency/exchange are updated immediately. Only call this
    once you're confident in a specific candidate — use `resolve_isin`,
    `lookup_isin`, `search_listings`, and `validate_listing` to check first.

    Args:
        resolution_id: The asset_resolutions row id.
        candidate_id: The id of the candidate on this resolution to apply
            (from `get_resolution` or `add_candidate`).
        reason: A short explanation of why this candidate was chosen —
            stored on the resolution for audit.

    Returns:
        The resolved resolution on success, or {"error": "..."} if the
        resolution/candidate doesn't exist, is already decided, or the
        candidate has no recent price or currency.
    """
    db = SessionLocal()
    try:
        use_case = build_resolve_security_use_case(db)
        try:
            resolution = use_case.accept(resolution_id, candidate_id, decided_by="agent", note=reason)
        except (ResolutionNotFoundError, DomainError) as exc:
            db.rollback()
            return {"error": str(exc)}
        db.commit()
        return to_jsonable(resolution)
    finally:
        db.close()
