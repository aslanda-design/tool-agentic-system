"""MCP tool: fused sentiment for one asset. Thin wrapper over
GetAssetIntelUseCase.get_sentiment — no logic of its own (see
backend/ai/AGENTS.md rule 2). Same egress guard as get_asset_news.py/
get_filing_events.py: a bare symbol string, nothing else, never called
per-holding."""

from __future__ import annotations

from app.adapters.persistence.session import SessionLocal
from app.config import settings
from app.container import build_get_asset_intel_use_case


def get_asset_sentiment(symbol: str) -> dict:
    """Fused news/social sentiment for one market-data symbol, combining
    Finnhub's news-derived sentiment score with StockTwits' self-tagged
    bullish/bearish posts. The point of this over either source alone is
    the "agreement" field: whether the two independent sources actually
    point the same direction, or disagree — worth calling out explicitly
    rather than averaging away.

    Args:
        symbol: A market-data symbol, e.g. 'AAPL'. Never an account id or
            holding id.

    Returns:
        {"symbol": ..., "sentiment": {"agreement": "aligned" | "diverging"
        | "single_source" | "no_data", "sources": [{"source", "label",
        "score", "sample_size"}, ...]}} — or {"error": "..."} if market
        intel is disabled.
    """
    if not settings.market_intel_enabled:
        return {"error": "market intel is disabled — set MARKET_INTEL_ENABLED=true"}
    db = SessionLocal()
    try:
        sentiment = build_get_asset_intel_use_case(db).get_sentiment(symbol)
        db.commit()
        return {"symbol": symbol, "sentiment": sentiment}
    finally:
        db.close()
