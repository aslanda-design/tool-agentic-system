"""Quant Lab: pluggable quantitative models + Monte Carlo playground — see
plans/quant_lab.md. Thin routes over RunQuantSimulationUseCase, same
pattern as resolutions.py."""

from __future__ import annotations

import logging
import random

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import container
from app.adapters.persistence.session import get_db
from app.api.schemas import SimulateRequest
from app.domain.errors import (
    DomainError,
    InsufficientQuantHistoryError,
    QuantModelNotFoundError,
    QuantRunNotFoundError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quant", tags=["quant"])


@router.get("/models")
def list_models(db: Session = Depends(get_db)):
    return container.build_run_quant_simulation_use_case(db).list_available_models()


@router.get("/assets/{asset_id}/recommendation")
def get_recommendation(asset_id: int, db: Session = Depends(get_db)):
    return container.build_run_quant_simulation_use_case(db).recommend(asset_id)


@router.post("/simulate")
def simulate(request: SimulateRequest, db: Session = Depends(get_db)):
    """Explicit, user-initiated action — allowed to backfill history from
    Twelve Data (application/backfill_quant_history.py), same "explicit
    action" exception search_assets.py/POST /api/assets/{id}/resolve
    already establish. Can take a few seconds for the larger caps; this is
    a local single-user app, so synchronous is fine at this scale (see
    plans/quant_lab.md section 5)."""
    seed = request.seed if request.seed is not None else random.randint(0, 2**31 - 1)
    logger.info(
        "POST /quant/simulate: asset_id=%s model_key=%s split_date=%s horizon_days=%s "
        "n_paths=%s seed=%s",
        request.asset_id, request.model_key, request.split_date, request.horizon_days,
        request.n_paths, seed,
    )
    try:
        result = container.build_run_quant_simulation_use_case(db).run(
            asset_id=request.asset_id,
            model_key=request.model_key,
            split_date=request.split_date,
            horizon_days=request.horizon_days,
            n_paths=request.n_paths,
            seed=seed,
            params=request.params,
            created_by="user",
        )
    except QuantModelNotFoundError as exc:
        logger.warning("POST /quant/simulate: 404 — %s", exc)
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InsufficientQuantHistoryError as exc:
        logger.warning("POST /quant/simulate: 422 — %s", exc)
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DomainError as exc:
        logger.warning("POST /quant/simulate: 422 — %s", exc)
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        logger.exception("POST /quant/simulate: unhandled error")
        db.rollback()
        raise
    db.commit()
    logger.info("POST /quant/simulate: 200 — run_id=%s", result.id)
    return result


@router.get("/runs")
def list_runs(asset_id: int, limit: int = 20, db: Session = Depends(get_db)):
    return container.build_run_quant_simulation_use_case(db).list_for_asset(asset_id, limit)


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
    """Recomputes paths from the run's stored recipe (see
    plans/quant_lab.md section 0.2, decision 4) rather than replaying a
    stored path array — there isn't one."""
    logger.info("GET /quant/runs/%s", run_id)
    try:
        result = container.build_run_quant_simulation_use_case(db).replay(run_id)
    except QuantRunNotFoundError as exc:
        logger.warning("GET /quant/runs/%s: 404 — %s", run_id, exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InsufficientQuantHistoryError as exc:
        logger.warning("GET /quant/runs/%s: 422 — %s", run_id, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info("GET /quant/runs/%s: 200", run_id)
    return result
