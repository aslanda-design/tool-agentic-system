"""MCP tool: recent transactions. Thin wrapper over
PortfolioRepo.list_transactions — the sort/limit below is presentation, not
business logic, so it stays here rather than earning a new repo method (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from datetime import date

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import portfolio_repo


def list_transactions(asset_id: int | None = None, since: str | None = None, limit: int = 50) -> list[dict]:
    """List recent transactions (buys, sells, dividends, fees, ...), most
    recent first.

    Args:
        asset_id: Restrict to one asset. Omit for every asset.
        since: Only transactions on or after this date (ISO format, e.g.
            "2026-01-01"). Omit for the full history.
        limit: Maximum number of transactions to return (default 50).

    Returns:
        Transactions ordered by execution time, most recent first.
    """
    db = SessionLocal()
    try:
        since_date = date.fromisoformat(since) if since else None
        transactions = portfolio_repo(db).list_transactions(asset_id=asset_id, since=since_date)
        transactions = sorted(transactions, key=lambda t: t.executed_at, reverse=True)[:limit]
        return [to_jsonable(t) for t in transactions]
    finally:
        db.close()
