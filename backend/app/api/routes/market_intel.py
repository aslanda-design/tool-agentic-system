"""Read-only external news/filings/sentiment for one asset — the frontend
counterpart to the `market_intel` MCP server's tools (see
plans/market_intel.md Phase 3). Wraps the exact same GetAssetIntelUseCase
the MCP tools use, so the `AssetDetailPage` card and the chat agent see
identical numbers. Symbol-keyed, not asset-id-keyed, same as the MCP
tools — the frontend already has the asset's symbol from `GET
/assets/{id}`, so no lookup happens here that could reach
positions/accounts (backend/ai/AGENTS.md rule 13's egress guard, applied
here too even though this route isn't an MCP tool)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import container
from app.adapters.persistence.session import get_db
from app.config import settings

router = APIRouter(prefix="/market-intel", tags=["market-intel"])


@router.get("/assets/{symbol}")
def get_asset_market_intel(symbol: str, db: Session = Depends(get_db)):
    """A GET that also writes: `get_all` reads through
    `AssetIntelCacheRepo` (backend/ai/AGENTS.md rule 6's third write path
    — a plain cache upsert with no state machine, safe from a GET the same
    way the MCP tools commit their own cache write). `get_db` never
    commits on its own, so this route must."""
    if not settings.market_intel_enabled:
        raise HTTPException(
            status_code=409, detail="Market intel is not enabled (MARKET_INTEL_ENABLED=false)."
        )
    result = container.build_get_asset_intel_use_case(db).get_all(symbol)
    db.commit()
    return result
