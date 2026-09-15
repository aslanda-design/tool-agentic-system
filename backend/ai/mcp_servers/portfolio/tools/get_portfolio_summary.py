"""MCP tool: the portfolio's current top-line numbers. Thin wrapper over
QueryPortfolioUseCase.get_summary — no logic of its own (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_query_portfolio_use_case


def get_portfolio_summary() -> dict:
    """The portfolio's current total value, invested amount, cash, P&L, and
    today's change, in the app's base currency.

    Returns:
        market_value, net_invested, cash, unrealized_pnl(_pct), day_change(_pct),
        and unpriced_count (positions with no market price yet — excluded
        from the value/P&L figures above, not treated as zero).
    """
    db = SessionLocal()
    try:
        return to_jsonable(build_query_portfolio_use_case(db).get_summary())
    finally:
        db.close()
