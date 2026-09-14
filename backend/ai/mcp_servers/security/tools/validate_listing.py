"""MCP tool: validate a market-data symbol and describe its current pricing.
Thin wrapper over MarketDataPort.get_listing_info — no logic of its own
(see backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_resolve_security_use_case


def validate_listing(symbol: str) -> dict | None:
    """Check whether a market-data symbol exists and has recent pricing.

    Args:
        symbol: A market-data symbol, e.g. a Yahoo Finance ticker like 'VWCE.DE'.

    Returns:
        The listing's currency, last close, last trade date, and average
        volume, or None if the symbol doesn't exist or has no recent trades.
    """
    db = SessionLocal()
    try:
        use_case = build_resolve_security_use_case(db)
        info = use_case.market_data.get_listing_info(symbol)
        return to_jsonable(info) if info is not None else None
    finally:
        db.close()
