"""Geometric Brownian Motion — the process Black-Scholes assumes for the
underlying — see plans/quant_lab_phase7_8.md section 2 for the full
derivation and worked examples. This simulates the REAL-WORLD (physical-
measure, historical-drift) process, not the Black-Scholes OPTION-PRICING
formula: no strike, no risk-free rate, no option anywhere in this file.

Unlike linear_regression.py/time_series_ar.py, this model does NOT go
through bootstrap.py's residual bootstrap — GBM has an exact closed-form
solution (Ito's lemma applied to log(S_t) turns the SDE into a plain
integral), so simulate() generates every path in one fully-vectorized
numpy call, no per-path or per-day Python loop at all. That same closed
form also gives an independent, exact check on the simulator's output —
see test_black_scholes_gbm_model.py's percentile test, which compares the
Monte Carlo output against `scipy.stats.norm.ppf` directly.

Parameters are ANNUALIZED (mu, sigma) with dt=1/252 per simulated trading
day — see plans/quant_lab_phase7_8.md section 1.1 for why this differs
from linear_regression.py/time_series_ar.py's raw-daily-unit convention.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from app.domain.quant.annualization import (
    TRADING_DAYS_PER_YEAR,
    annualized_mean_and_std,
    log_returns,
)
from app.domain.quant.confidence import (
    CONFIDENCE_LEVEL_PARAM_SPEC,
    confidence_band_percentiles,
    confidence_level_from_params,
)
from app.domain.quant.dates import next_trading_days
from app.domain.quant.registry import register
from app.domain.quant.types import CalibrationResult, ModelMetadata, ParamSpec, SimulationResult

MIN_HISTORY_DAYS = 60

METADATA = ModelMetadata(
    key="black_scholes_gbm",
    display_name="Black-Scholes (GBM)",
    description=(
        "Geometric Brownian Motion — constant annualized drift and volatility, estimated from "
        "this asset's own historical returns. The textbook 'random walk with a trend': no "
        "volatility clustering, no fat tails, no mean reversion. This simulates the real-world "
        "process Black-Scholes assumes for the underlying, not the Black-Scholes option-pricing "
        "formula itself."
    ),
    family="stochastic_process",
    min_history_days=MIN_HISTORY_DAYS,
    supports_calibration=True,
    param_specs=[
        ParamSpec(
            key="horizon_days",
            label="Horizon (trading days)",
            kind="int",
            default=20,
            min=1,
            max=252,
            help="How many trading days forward to simulate.",
        ),
        ParamSpec(
            key="n_paths",
            label="Monte Carlo paths",
            kind="int",
            default=200,
            min=10,
            max=5000,
            help="More paths = smoother percentile bands, slower to compute.",
        ),
        CONFIDENCE_LEVEL_PARAM_SPEC,
    ],
)


class BlackScholesGbmModel:
    metadata = METADATA

    def calibrate(self, closes: list[float], params: dict[str, float]) -> CalibrationResult:
        returns = log_returns(closes)
        if len(returns) < MIN_HISTORY_DAYS:
            raise ValueError(
                f"black_scholes_gbm needs at least {MIN_HISTORY_DAYS} daily returns to calibrate, got {len(returns)}"
            )
        mu, sigma = annualized_mean_and_std(returns)
        return CalibrationResult(
            params={"mu": mu, "sigma": sigma},
            diagnostics={"mu": mu, "sigma": sigma, "n_obs": float(len(returns))},
            residuals=[],  # unused — GBM has an exact solution, no residual bootstrap
            last_window=[],  # unused — simulate() needs only last_price + mu/sigma
        )

    def simulate(
        self,
        last_price: float,
        as_of: date,
        calibration: CalibrationResult,
        horizon_days: int,
        n_paths: int,
        seed: int,
        params: dict[str, float],
    ) -> SimulationResult:
        mu = calibration.params["mu"]
        sigma = calibration.params["sigma"]
        dt = 1.0 / TRADING_DAYS_PER_YEAR

        rng = np.random.default_rng(seed)
        z = rng.standard_normal(size=(n_paths, horizon_days))
        daily_log_returns = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z
        cumulative_log_returns = np.cumsum(daily_log_returns, axis=1)
        paths = last_price * np.exp(cumulative_log_returns)

        percentiles = confidence_band_percentiles(paths, confidence_level_from_params(params))
        return SimulationResult(
            dates=next_trading_days(as_of, horizon_days),
            paths=paths.tolist(),
            percentiles=percentiles,
        )


register(BlackScholesGbmModel())
