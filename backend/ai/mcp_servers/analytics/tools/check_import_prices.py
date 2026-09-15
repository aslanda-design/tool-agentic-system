"""MCP tool: flag mispriced trades. Thin wrapper over
QueryAnalyticsUseCase.check_import_prices — no logic of its own (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from datetime import date

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_query_analytics_use_case


def check_import_prices(account_id: int | None = None, since: str | None = None) -> list[dict]:
    """Flag BUY/SELL transactions whose executed price differs from that
    day's market close by more than 10% — usually a unit mismatch (e.g. a
    GBp-style minor-currency listing) or a manual-entry typo.

    Args:
        account_id: Restrict to one account. Omit for every account.
        since: Only check transactions on or after this date (ISO format).
            Omit for the full history.

    Returns:
        One entry per flagged transaction: symbol, executed_price,
        market_close, pct_diff. Empty if nothing looks wrong.
    """
    db = SessionLocal()
    try:
        since_date = date.fromisoformat(since) if since else None
        flagged = build_query_analytics_use_case(db).check_import_prices(account_id, since_date)
        return [to_jsonable(f) for f in flagged]
    finally:
        db.close()
