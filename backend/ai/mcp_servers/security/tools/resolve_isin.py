"""MCP tool: dry-run the resolver pipeline for an ISIN, without touching the
database. Thin wrapper over ResolveSecurityUseCase.preview — no logic of its
own (see backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_resolve_security_use_case
from app.domain.exchanges import broker_code_to_mic
from app.domain.listings import ResolutionContext


def resolve_isin(
    isin: str,
    currency: str,
    broker_symbol: str = "",
    broker_name: str = "",
    broker_exchange: str | None = None,
) -> list[dict]:
    """Explore candidate listings for an ISIN without persisting anything.

    Generates and scores candidates exactly like the real resolver would
    for an asset, but never creates a resolution or applies a listing — use
    this to look before committing via `save_security_mapping`.

    Args:
        isin: The security's ISIN.
        currency: The currency the broker reports this holding in.
        broker_symbol: The ticker the broker uses for this holding, if known.
        broker_name: The name the broker uses for this holding, if known.
        broker_exchange: The broker's own exchange code, if known (e.g. IBKR's 'IBIS2').

    Returns:
        Candidate listings, scored and sorted best-first.
    """
    db = SessionLocal()
    try:
        use_case = build_resolve_security_use_case(db)
        ctx = ResolutionContext(
            asset_id=0,
            isin=isin,
            broker_symbol=broker_symbol,
            broker_name=broker_name,
            broker_exchange=broker_exchange,
            broker_mic=broker_code_to_mic(broker_exchange),
            currency=currency,
            broker_key=None,
        )
        candidates = use_case.preview(ctx)
        return [to_jsonable(c) for c in candidates]
    finally:
        db.close()
