"""The shared Monte Carlo engine every non-SDE model reuses — see
plans/quant_lab.md section 4.4. A future stochastic-process model
(Black-Scholes/Heston/...) steps its own SDE instead and does NOT call
this; every model that only produces a point forecast + residuals
(linear_regression.py, time_series_ar.py) does.

The idea: roll `point_forecast_fn` forward `horizon_days` times per path,
each step adding a shock resampled (with replacement) from the model's own
in-sample residuals — a standard residual bootstrap. This is what makes a
plain regression/AR model produce a *path ensemble* instead of a single
number, so the frontend's "watch the Monte Carlo iterations build up to a
prediction" interaction works identically regardless of which model
produced the paths."""

from __future__ import annotations

import math
from datetime import date

import numpy as np

from app.domain.quant.confidence import DEFAULT_CONFIDENCE_LEVEL, confidence_band_percentiles
from app.domain.quant.dates import next_trading_days
from app.domain.quant.types import PointForecastFn, SimulationResult


def simulate_by_residual_bootstrap(
    last_price: float,
    recent_log_returns: list[float],
    point_forecast_fn: PointForecastFn,
    residuals: list[float],
    horizon_days: int,
    n_paths: int,
    seed: int,
    start_date: date,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
) -> SimulationResult:
    """confidence_level is a fraction (0.9 = 90%) — the returned
    `percentiles` dict has exactly three keys, "lower"/"median"/"upper",
    computed from it (e.g. 0.9 -> the 5th/50th/95th percentiles). This is
    user-configurable on the Quant Lab page's param form (see
    domain/quant/models/*.py's `confidence_level` ParamSpec), not
    hardcoded."""
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    if n_paths <= 0:
        raise ValueError("n_paths must be positive")
    if not (0 < confidence_level < 1):
        raise ValueError("confidence_level must be strictly between 0 and 1")

    rng = np.random.default_rng(seed)
    residual_pool = np.array(residuals, dtype=float) if residuals else np.array([0.0])

    paths = np.empty((n_paths, horizon_days), dtype=float)
    for p in range(n_paths):
        history = list(recent_log_returns)
        price = last_price
        shocks = rng.choice(residual_pool, size=horizon_days, replace=True)
        for t in range(horizon_days):
            forecast = point_forecast_fn(history)
            log_return = forecast + float(shocks[t])
            price = price * math.exp(log_return)
            paths[p, t] = price
            if history:
                history = history[1:] + [log_return]

    percentiles = confidence_band_percentiles(paths, confidence_level)
    return SimulationResult(
        dates=next_trading_days(start_date, horizon_days),
        paths=paths.tolist(),
        percentiles=percentiles,
    )
