"""MCP tool: portfolio value over time. Thin wrapper over
QueryPortfolioUseCase.get_history — no logic of its own (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_query_portfolio_use_case


def get_value_history(range: str = "1Y") -> list[dict]:
    """Daily portfolio value vs. amount invested over a date range.

    Args:
        range: One of "1M", "YTD", "1Y", "ALL".

    Returns:
        One point per day with market_value and cost_basis ("invested").
    """
    db = SessionLocal()
    try:
        points = build_query_portfolio_use_case(db).get_history(range)
        return [to_jsonable(p) for p in points]
    finally:
        db.close()
