"""MCP tool: look up one asset. Thin wrapper over
QueryMarketDataUseCase.get_asset — no logic of its own (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_query_market_data_use_case


def get_asset(asset_id: int | None = None, symbol: str | None = None) -> dict:
    """Look up an asset by id or by its market-data symbol.

    Args:
        asset_id: The asset's internal id.
        symbol: A market-data symbol, e.g. a Yahoo Finance ticker like
            'VWCE.DE'. Give exactly one of asset_id/symbol.

    Returns:
        symbol, name, exchange, currency, asset_class, isin, last_price,
        prev_close — or {"error": "..."} if not found or the arguments are
        invalid.
    """
    if (asset_id is None) == (symbol is None):
        return {"error": "give exactly one of asset_id or symbol"}
    db = SessionLocal()
    try:
        result = build_query_market_data_use_case(db).get_asset(asset_id, symbol)
        return to_jsonable(result) if result is not None else {"error": "asset not found"}
    finally:
        db.close()
