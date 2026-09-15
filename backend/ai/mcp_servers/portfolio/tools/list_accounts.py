"""MCP tool: connected accounts. Thin wrapper over PortfolioRepo.list_accounts
— no logic of its own (see backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import portfolio_repo


def list_accounts() -> list[dict]:
    """List every connected account (broker or manual).

    Returns:
        id, broker_key, name, currency, and source ("api" or "manual") for
        each account.
    """
    db = SessionLocal()
    try:
        return [to_jsonable(a) for a in portfolio_repo(db).list_accounts()]
    finally:
        db.close()
