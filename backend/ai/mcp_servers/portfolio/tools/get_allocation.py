"""MCP tool: portfolio allocation breakdown. Thin wrapper over
QueryPortfolioUseCase.get_allocation — no logic of its own (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_query_portfolio_use_case


def get_allocation(by: str = "asset_class") -> list[dict]:
    """Break the portfolio's market value down by a dimension.

    Args:
        by: One of "asset_class", "currency", or "account".

    Returns:
        One slice per distinct value of `by`, each with its market value
        and weight (0..1) of the total, sorted largest first.
    """
    db = SessionLocal()
    try:
        slices = build_query_portfolio_use_case(db).get_allocation(by)
        return [to_jsonable(s) for s in slices]
    finally:
        db.close()
