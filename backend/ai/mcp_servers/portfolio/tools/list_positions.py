"""MCP tool: current positions. Thin wrapper over
QueryPortfolioUseCase.list_positions — no logic of its own (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_query_portfolio_use_case


def list_positions(account_id: int | None = None) -> list[dict]:
    """List currently held positions, each with quantity, cost basis,
    current market value, unrealized P&L, and returns over standard
    periods.

    Args:
        account_id: Restrict to one account's holdings. Omit for every
            account.

    Returns:
        One entry per held asset (per account it's held in).
    """
    db = SessionLocal()
    try:
        positions = build_query_portfolio_use_case(db).list_positions(account_id)
        return [to_jsonable(p) for p in positions]
    finally:
        db.close()
