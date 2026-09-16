"""MCP tool: explain a past Quant Lab run. Thin wrapper over
RunQuantSimulationUseCase.explain — a pure DB read of a run's stored
recipe/summary, never the raw path ensemble (kept out of an LLM's context,
same reasoning get_price_history's downsampling already established). See
backend/ai/AGENTS.md."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_run_quant_simulation_use_case


def explain_run(run_id: int) -> dict:
    """Summarize a Quant Lab run the user already made on the page: which
    model, what it was calibrated on, and how its forecast compared to
    what actually happened (if enough time has passed to check).

    Args:
        run_id: The quant_runs id, shown on the Quant Lab page's run history.

    Returns:
        {id, asset_id, model_key, split_date, horizon_days, n_paths, params,
         calibration_params, calibration_diagnostics, percentiles, backtest,
         created_by, note, created_at} — NOT the raw simulated paths, which
        stay UI-only. {"error": "..."} if the run doesn't exist.
    """
    db = SessionLocal()
    try:
        explanation = build_run_quant_simulation_use_case(db).explain(run_id)
        if explanation is None:
            return {"error": f"Quant run {run_id} not found"}
        return to_jsonable(explanation)
    finally:
        db.close()
