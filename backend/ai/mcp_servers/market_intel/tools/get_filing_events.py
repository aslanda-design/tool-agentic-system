"""MCP tool: recent primary-source SEC filings for one asset. Thin wrapper
over GetAssetIntelUseCase.get_filings — no logic of its own (see
backend/ai/AGENTS.md rule 2). Same egress guard as get_asset_news.py: a
bare symbol string, nothing else, never called per-holding."""

from __future__ import annotations

from app.adapters.persistence.session import SessionLocal
from app.config import settings
from app.container import build_get_asset_intel_use_case


def get_filing_events(symbol: str) -> dict:
    """Recent primary-source SEC filings for one market-data symbol — 8-K
    material-event filings and Form 4 insider transactions. Only US SEC
    filers have anything to return; a non-US listing (e.g. most European
    ETFs) genuinely has none, which is not an error.

    Args:
        symbol: A market-data symbol, e.g. 'AAPL'. Never an account id or
            holding id.

    Returns:
        {"symbol": ..., "filings": [{"form_type", "filed_at", "title",
        "url", "source"}, ...]} — or {"error": "..."} if market intel is
        disabled or SEC_EDGAR_USER_AGENT isn't configured.
    """
    if not settings.market_intel_enabled:
        return {"error": "market intel is disabled — set MARKET_INTEL_ENABLED=true"}
    db = SessionLocal()
    try:
        filings = build_get_asset_intel_use_case(db).get_filings(symbol)
        db.commit()
        return {"symbol": symbol, "filings": filings}
    finally:
        db.close()
