"""MCP tool: free-text search for market-data listings. Thin wrapper over
MarketDataPort.search — no logic of its own (see backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_resolve_security_use_case


def search_listings(query: str) -> list[dict]:
    """Free-text search for market-data listings by symbol or name.

    Args:
        query: Text to search for, e.g. a company/fund name or ticker fragment.

    Returns:
        Matching listings from the market data source.
    """
    db = SessionLocal()
    try:
        use_case = build_resolve_security_use_case(db)
        results = use_case.market_data.search(query)
        return [to_jsonable(result) for result in results]
    finally:
        db.close()
