"""MCP tool: recent global news for a free-text sector/macro topic — not
tied to one asset. Thin wrapper over GetMacroIntelUseCase.get_headlines
(see backend/ai/AGENTS.md rule 2), plus the one extra check every other
tool in this server doesn't need: `topic` is free text the model composes,
not a bare ticker, so it goes through domain.market_intel.validate_topic
first — the egress guard's defense-in-depth layer (backend/ai/AGENTS.md
rule 13, plans/market_intel.md section 4.3)."""

from __future__ import annotations

from app.adapters.persistence.session import SessionLocal
from app.config import settings
from app.container import build_get_macro_intel_use_case
from app.domain.market_intel import validate_topic


def get_macro_headlines(topic: str) -> dict:
    """Recent global news headlines for a short sector/macro search phrase
    — use this for questions that aren't about one specific holding, e.g.
    "European bank stocks" or "semiconductor export controls". For a
    single ticker, use get_asset_news instead.

    Args:
        topic: A short search phrase (a few words), never account/holding
            data and never more than ~80 characters.

    Returns:
        {"topic": ..., "headlines": [{"title", "publisher", "url",
        "published_at", "source"}, ...]} — or {"error": "..."} if market
        intel is disabled or the topic looks malformed.
    """
    if not settings.market_intel_enabled:
        return {"error": "market intel is disabled — set MARKET_INTEL_ENABLED=true"}
    error = validate_topic(topic)
    if error is not None:
        return {"error": error}
    db = SessionLocal()
    try:
        headlines = build_get_macro_intel_use_case(db).get_headlines(topic)
        db.commit()
        return {"topic": topic, "headlines": headlines}
    finally:
        db.close()
