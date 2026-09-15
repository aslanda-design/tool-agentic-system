"""MCP tool: max/current drawdown. Thin wrapper over
QueryAnalyticsUseCase.get_drawdown — no logic of its own (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_query_analytics_use_case


def get_drawdown(scope: str = "portfolio", range: str = "1Y") -> dict:
    """Peak-to-trough drawdown over a date range.

    Args:
        scope: "portfolio" for the whole portfolio, or an asset id as a
            string for one asset.
        range: One of "1M", "YTD", "1Y", "ALL".

    Returns:
        max_drawdown_pct (0 or negative — e.g. -0.15 for -15%) and its
        start/end dates, plus current_drawdown_pct vs. the range's all-time
        high. Every field is null if there's no history yet.
    """
    db = SessionLocal()
    try:
        return to_jsonable(build_query_analytics_use_case(db).get_drawdown(scope, range))
    finally:
        db.close()
