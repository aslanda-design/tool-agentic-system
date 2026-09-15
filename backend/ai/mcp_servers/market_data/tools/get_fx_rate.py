"""MCP tool: a persisted FX rate. Thin wrapper over
QueryMarketDataUseCase.get_fx_rate — no logic of its own (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from datetime import date

from app.adapters.persistence.session import SessionLocal
from app.container import build_query_market_data_use_case


def get_fx_rate(base: str, quote: str, on_date: str | None = None) -> dict:
    """Look up a persisted daily FX rate.

    Args:
        base: Base currency, ISO code (e.g. "USD").
        quote: Quote currency, ISO code (e.g. "EUR").
        on_date: Date, ISO format. Defaults to today (the most recent
            persisted rate at or before it).

    Returns:
        {base, quote, rate, on_date}, or {"error": "..."} if no rate is
        known for that pair yet.
    """
    db = SessionLocal()
    try:
        parsed_date = date.fromisoformat(on_date) if on_date else None
        result = build_query_market_data_use_case(db).get_fx_rate(base, quote, parsed_date)
        if result is None:
            return {"error": f"no rate known for {base.upper()}/{quote.upper()}"}
        return {**result, "rate": float(result["rate"]), "on_date": result["on_date"].isoformat()}
    finally:
        db.close()
