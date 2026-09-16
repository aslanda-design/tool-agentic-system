"""MCP tool: deterministic model recommendation for an asset. Thin wrapper
over RunQuantSimulationUseCase.recommend — no LLM involved, same "rules
first" posture the security resolver already established (see
domain/quant/recommendation.py and backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_run_quant_simulation_use_case


def recommend_model(asset_id: int) -> list[dict]:
    """Deterministic, non-LLM model recommendation for an asset, based on
    cheap statistical checks (volatility clustering via Ljung-Box, linear
    trend strength, excess kurtosis) over its persisted daily closes.

    Args:
        asset_id: The asset to analyze.

    Returns:
        Ranked [{model_key, score, reason}], highest score first — one
        entry per model built so far, plus a "none" entry with a reason
        when the data looks heavier-tailed than either model assumes.
        Empty if the asset has under 30 days of persisted price history.
    """
    db = SessionLocal()
    try:
        recommendations = build_run_quant_simulation_use_case(db).recommend(asset_id)
        return [to_jsonable(r) for r in recommendations]
    finally:
        db.close()
