"""MCP tool: persisted daily closes for an asset. Thin wrapper over
QueryMarketDataUseCase.get_price_history — reads Postgres only, never live
Yahoo (see backend/ai/AGENTS.md and the use case's own docstring)."""

from __future__ import annotations

from datetime import date

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_query_market_data_use_case


def get_price_history(asset_id: int, start: str, end: str, max_points: int = 120) -> list[dict]:
    """Persisted daily closing prices for an asset between two dates.

    Args:
        asset_id: The asset's internal id.
        start: Start date, ISO format (e.g. "2026-01-01").
        end: End date, ISO format.
        max_points: Downsample to at most this many points (default 120) —
            keep this small for a model's context window.

    Returns:
        {date, close} points, oldest first, always including the most
        recent available bar.
    """
    db = SessionLocal()
    try:
        points = build_query_market_data_use_case(db).get_price_history(
            asset_id, date.fromisoformat(start), date.fromisoformat(end), max_points
        )
        return [to_jsonable(p) for p in points]
    finally:
        db.close()
