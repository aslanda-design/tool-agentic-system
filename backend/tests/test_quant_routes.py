"""Route-level tests for app/api/routes/quant.py — see plans/quant_lab.md.
Real Postgres + TestClient: seeds an asset with real persisted price
history directly through the repos (no live yfinance/Twelve Data calls —
TWELVE_DATA_API_KEY is unset in tests, so the backfill use case is a no-op
once enough history already exists)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
from fastapi.testclient import TestClient

from app.adapters.persistence.repositories import SqlAssetRepo, SqlMarketDataRepo
from app.adapters.persistence.session import SessionLocal
from app.application.run_quant_simulation import MAX_N_PATHS
from app.domain.models import AssetClass
from app.main import app


def _trading_dates(start: date, n: int) -> list[date]:
    dates: list[date] = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def _seed_asset_with_history(n_days: int = 650, seed: int = 0) -> tuple[int, list[date]]:
    rng = np.random.default_rng(seed)
    dates = _trading_dates(date(2023, 1, 2), n_days)
    log_returns = rng.normal(0.0003, 0.01, size=n_days - 1)
    prices = 100.0 * np.exp(np.cumsum(log_returns))
    closes = [100.0, *prices.tolist()]

    db = SessionLocal()
    try:
        asset_repo = SqlAssetRepo(db)
        market_data_repo = SqlMarketDataRepo(db)
        asset = asset_repo.create(f"QUANTTEST{seed}", "Quant Test Asset", AssetClass.ETF, "EUR")
        rows = [
            {
                "date": d,
                "open": Decimal(str(c)),
                "high": Decimal(str(c)),
                "low": Decimal(str(c)),
                "close": Decimal(str(c)),
                "adj_close": Decimal(str(c)),
                "volume": Decimal(1000),
            }
            for d, c in zip(dates, closes)
        ]
        market_data_repo.upsert_bars(asset.id, rows, source="test")
        db.commit()
        return asset.id, dates
    finally:
        db.close()


def _delete_asset(asset_id: int) -> None:
    db = SessionLocal()
    try:
        SqlAssetRepo(db).delete(asset_id)
        db.commit()
    finally:
        db.close()


def test_list_models_returns_all_built_models():
    with TestClient(app) as client:
        response = client.get("/api/quant/models")
        assert response.status_code == 200
        keys = {m["key"] for m in response.json()}
        assert keys == {
            "linear_regression",
            "time_series_ar",
            "black_scholes_gbm",
            "heston",
            "hawkes_jump_diffusion",
            "rough_heston",
        }


def test_simulate_then_replay_and_list_runs():
    asset_id, dates = _seed_asset_with_history(seed=10)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/quant/simulate",
                json={
                    "asset_id": asset_id,
                    "model_key": "linear_regression",
                    "split_date": dates[600].isoformat(),
                    "horizon_days": 10,
                    "n_paths": 15,
                    "seed": 1,
                    "params": {},
                },
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert len(body["paths"]) == 15
            assert all(len(p) == 10 for p in body["paths"])
            assert body["backtest"] is not None
            run_id = body["id"]

            replayed = client.get(f"/api/quant/runs/{run_id}")
            assert replayed.status_code == 200
            assert replayed.json()["paths"] == body["paths"]

            listed = client.get(f"/api/quant/runs?asset_id={asset_id}")
            assert listed.status_code == 200
            assert len(listed.json()) == 1
            assert "paths" not in listed.json()[0]
    finally:
        _delete_asset(asset_id)


def test_simulate_unknown_model_is_404():
    asset_id, dates = _seed_asset_with_history(seed=11)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/quant/simulate",
                json={
                    "asset_id": asset_id,
                    "model_key": "does_not_exist",
                    "split_date": dates[600].isoformat(),
                    "horizon_days": 10,
                    "n_paths": 15,
                    "params": {},
                },
            )
            assert response.status_code == 404
    finally:
        _delete_asset(asset_id)


def test_simulate_insufficient_history_is_422():
    asset_id, dates = _seed_asset_with_history(seed=12)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/quant/simulate",
                json={
                    "asset_id": asset_id,
                    "model_key": "linear_regression",
                    "split_date": dates[10].isoformat(),
                    "horizon_days": 10,
                    "n_paths": 15,
                    "params": {},
                },
            )
            assert response.status_code == 422
    finally:
        _delete_asset(asset_id)


def test_simulate_over_cap_is_422():
    asset_id, dates = _seed_asset_with_history(seed=13)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/quant/simulate",
                json={
                    "asset_id": asset_id,
                    "model_key": "linear_regression",
                    "split_date": dates[600].isoformat(),
                    "horizon_days": 10,
                    "n_paths": MAX_N_PATHS + 1,
                    "params": {},
                },
            )
            assert response.status_code == 422
    finally:
        _delete_asset(asset_id)


def test_get_recommendation_returns_a_ranked_list():
    asset_id, _dates = _seed_asset_with_history(seed=14)
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/quant/assets/{asset_id}/recommendation")
            assert response.status_code == 200
            assert isinstance(response.json(), list)
    finally:
        _delete_asset(asset_id)


def test_get_unknown_run_is_404():
    with TestClient(app) as client:
        assert client.get("/api/quant/runs/999999999").status_code == 404
