"""MCP tool: attach a candidate listing to an open resolution. WRITE — thin
wrapper over ResolveSecurityUseCase.add_candidate, commits on success (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_resolve_security_use_case
from app.domain.errors import DomainError, ResolutionNotFoundError


def add_candidate(resolution_id: int, symbol: str) -> dict:
    """Add a candidate listing to an open resolution. WRITE — inserts a row.

    Validates and scores `symbol` and attaches it to the resolution. Does
    NOT apply it as the asset's mapping — call `save_security_mapping` once
    you've decided which candidate to use.

    Args:
        resolution_id: The asset_resolutions row id.
        symbol: A market-data symbol to add, e.g. a Yahoo Finance ticker.

    Returns:
        The new candidate, scored, on success — or {"error": "..."} if the
        resolution doesn't exist or the symbol is already a candidate on it.
    """
    db = SessionLocal()
    try:
        use_case = build_resolve_security_use_case(db)
        try:
            candidate = use_case.add_candidate(resolution_id, symbol, source="agent")
        except (ResolutionNotFoundError, DomainError) as exc:
            db.rollback()
            return {"error": str(exc)}
        db.commit()
        return to_jsonable(candidate)
    finally:
        db.close()
