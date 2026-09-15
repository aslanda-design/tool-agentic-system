"""MCP tool: portfolio concentration. Thin wrapper over
QueryAnalyticsUseCase.get_concentration — no logic of its own (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_query_analytics_use_case


def get_concentration(top_n: int = 10) -> dict:
    """How concentrated the portfolio is in its largest holdings.

    Args:
        top_n: How many of the largest holdings to list individually
            (default 10). The HHI below always covers every holding,
            regardless of top_n.

    Returns:
        holdings: the top_n largest positions by weight (0..1 of total
            market value, same asset combined across accounts).
        hhi: Herfindahl-Hirschman Index over every holding, 0..1 — higher
            means more concentrated (1.0 = a single holding).
    """
    db = SessionLocal()
    try:
        return to_jsonable(build_query_analytics_use_case(db).get_concentration(top_n))
    finally:
        db.close()
