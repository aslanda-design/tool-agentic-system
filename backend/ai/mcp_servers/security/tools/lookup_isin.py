"""MCP tool: look up every exchange listing OpenFIGI knows for an ISIN.
Thin wrapper over SecurityMasterPort.map_isin — no logic of its own (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_resolve_security_use_case


def lookup_isin(isin: str) -> list[dict] | None:
    """Look up every exchange listing OpenFIGI knows for an ISIN.

    Args:
        isin: The security's ISIN.

    Returns:
        A list of listings (possibly empty, if OpenFIGI confirms there are
        none), or None if OpenFIGI itself is unavailable right now (rate
        limited or unreachable) — treat None as "try again later", never as
        "this ISIN has no listings".
    """
    db = SessionLocal()
    try:
        use_case = build_resolve_security_use_case(db)
        listings = use_case.security_master.map_isin(isin)
        return [to_jsonable(listing) for listing in listings] if listings is not None else None
    finally:
        db.close()
