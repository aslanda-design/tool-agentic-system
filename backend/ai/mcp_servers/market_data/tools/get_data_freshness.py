"""MCP tool: how stale is the app's market data right now. Thin wrapper over
QueryMarketDataUseCase.get_data_freshness — no logic of its own (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_query_market_data_use_case


def get_data_freshness() -> dict:
    """Check how up to date the app's persisted prices are.

    Returns:
        as_of, stale_positions (held assets with no price in the last few
        days), unmapped_assets (assets with no market-data ticker yet, so
        they have no price at all).
    """
    db = SessionLocal()
    try:
        return to_jsonable(build_query_market_data_use_case(db).get_data_freshness())
    finally:
        db.close()
