"""MCP tool: list registered quant models. Thin wrapper over
RunQuantSimulationUseCase.list_available_models — no logic of its own (see
backend/ai/AGENTS.md)."""

from __future__ import annotations

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import build_run_quant_simulation_use_case


def list_models() -> list[dict]:
    """List every quantitative model registered in the Quant Lab.

    Returns:
        One entry per model: key, display_name, description, family,
        min_history_days, supports_calibration, and param_specs (the knobs
        a human sets on the Quant Lab page — key/label/kind/default/min/
        max/choices/help).
    """
    db = SessionLocal()
    try:
        models = build_run_quant_simulation_use_case(db).list_available_models()
        return [to_jsonable(m) for m in models]
    finally:
        db.close()
