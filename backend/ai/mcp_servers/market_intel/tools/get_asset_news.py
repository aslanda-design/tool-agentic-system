"""MCP tool: recent headlines for one asset. Thin wrapper over
GetAssetIntelUseCase.get_news — no logic of its own (see backend/ai/AGENTS.md
rule 2). Egress guard (rule 13, plans/market_intel.md section 4): the only
argument is a bare symbol string — no asset_id, no DB session that could
reach positions/accounts, and never called in a loop over the user's own
holdings."""

from __future__ import annotations

from app.adapters.persistence.session import SessionLocal
from app.config import settings
from app.container import build_get_asset_intel_use_case


def get_asset_news(symbol: str) -> dict:
    """Recent news headlines for one market-data symbol (e.g. a Yahoo
    Finance ticker like 'AAPL' or 'VWCE.DE'), newest first, deduped across
    sources. Ask this for one ticker you're currently looking at — never
    call it once per holding in your portfolio.

    Args:
        symbol: A market-data symbol, exactly as used elsewhere in this
            app (e.g. get_asset). Never an account id or holding id.

    Returns:
        {"symbol": ..., "headlines": [{"title", "publisher", "url",
        "published_at", "source"}, ...]} — or {"error": "..."} if market
        intel is disabled.
    """
    if not settings.market_intel_enabled:
        return {"error": "market intel is disabled — set MARKET_INTEL_ENABLED=true"}
    db = SessionLocal()
    try:
        headlines = build_get_asset_intel_use_case(db).get_news(symbol)
        # Read-through cache write (app/ports/market_intel.py::AssetIntelCacheRepo.put)
        # is the only "write" this does — still needs an explicit commit,
        # same as every other MCP tool that mutates something, even a cache.
        db.commit()
        return {"symbol": symbol, "headlines": headlines}
    finally:
        db.close()
