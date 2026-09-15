"""MCP tool: currency exposure. Thin wrapper over
QueryAnalyticsUseCase.get_currency_exposure — no logic of its own (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_query_analytics_use_case


def get_currency_exposure() -> list[dict]:
    """Break the portfolio's market value down by currency.

    Returns:
        One entry per currency held, with market_value and weight (0..1 of
        the total), sorted largest first.
    """
    db = SessionLocal()
    try:
        slices = build_query_analytics_use_case(db).get_currency_exposure()
        return [to_jsonable(s) for s in slices]
    finally:
        db.close()
