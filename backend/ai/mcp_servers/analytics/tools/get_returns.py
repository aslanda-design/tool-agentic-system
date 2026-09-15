"""MCP tool: value-weighted returns. Thin wrapper over
QueryAnalyticsUseCase.get_returns — no logic of its own (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_query_analytics_use_case


def get_returns(scope: str = "portfolio", periods: list[str] | None = None) -> dict:
    """Value-weighted return over one or more periods.

    Args:
        scope: "portfolio" for the whole portfolio, or an asset id as a
            string (e.g. "42") for one asset.
        periods: Period labels to compute, e.g. ["1d", "1w", "1m", "ytd", "1y"].
            Defaults to all five.

    Returns:
        {period: return_fraction} — e.g. {"1m": 0.032} means +3.2%. A
        period is omitted (mapped to null) if no priced position covers it.
    """
    db = SessionLocal()
    try:
        return to_jsonable(build_query_analytics_use_case(db).get_returns(scope, periods))
    finally:
        db.close()
