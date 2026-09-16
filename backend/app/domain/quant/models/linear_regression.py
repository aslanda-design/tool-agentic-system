"""Multi-parameter linear regression on engineered return features — one of
the two models built to prove the Quant Lab infrastructure (see
plans/quant_lab.md section 4.5). Deliberately "textbook": plain OLS via
numpy.linalg.lstsq, no scikit-learn dependency needed for this.

Features per day: log-returns at a fixed set of lags, rolling realized
volatility, and rolling mean return — all computed from a trailing
HISTORY_WINDOW-day slice of log-returns, which is also exactly what
CalibrationResult.last_window carries forward into simulate() so a
bootstrap path's first forecast step has the same inputs a human trading
"today" would have.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from app.domain.quant.annualization import log_returns
from app.domain.quant.bootstrap import simulate_by_residual_bootstrap
from app.domain.quant.confidence import CONFIDENCE_LEVEL_PARAM_SPEC, confidence_level_from_params
from app.domain.quant.registry import register
from app.domain.quant.types import CalibrationResult, ModelMetadata, ParamSpec, SimulationResult

LAGS = (1, 2, 3, 5, 10)
VOL_WINDOW = 10
MEAN_WINDOW = 20
HISTORY_WINDOW = max(max(LAGS), VOL_WINDOW, MEAN_WINDOW)
FEATURE_NAMES = [f"lag_{lag}" for lag in LAGS] + ["vol", "mean"]

METADATA = ModelMetadata(
    key="linear_regression",
    display_name="Linear regression",
    description=(
        "OLS on lagged log-returns (1/2/3/5/10 day), rolling realized volatility, and "
        "rolling mean return. A textbook baseline, not a market model — it will not capture "
        "volatility clustering or fat tails; see the recommendation tool for when a "
        "time-series model fits the data better."
    ),
    family="regression",
    min_history_days=HISTORY_WINDOW + 60,  # enough rows to fit 8 coefficients meaningfully
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


def _features(window: list[float]) -> np.ndarray:
    """window: the trailing HISTORY_WINDOW log-returns, oldest first."""
    lag_feats = [window[-lag] for lag in LAGS]
    vol = float(np.std(window[-VOL_WINDOW:]))
    mean = float(np.mean(window[-MEAN_WINDOW:]))
    return np.array([*lag_feats, vol, mean], dtype=float)


class LinearRegressionModel:
    metadata = METADATA

    def calibrate(self, closes: list[float], params: dict[str, float]) -> CalibrationResult:
        returns = log_returns(closes)
        if len(returns) <= HISTORY_WINDOW + 5:
            raise ValueError(
                f"linear_regression needs at least {HISTORY_WINDOW + 5} daily returns to calibrate, got {len(returns)}"
            )

        rows, targets = [], []
        for t in range(HISTORY_WINDOW, len(returns)):
            window = returns[t - HISTORY_WINDOW : t]
            rows.append(_features(window))
            targets.append(returns[t])
        X = np.array(rows)
        y = np.array(targets)
        design = np.column_stack([np.ones(len(X)), X])
        coef, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
        fitted = design @ coef
        residuals = y - fitted

        ss_res = float(np.sum(residuals**2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

        model_params = {
            "intercept": float(coef[0]),
            **{name: float(c) for name, c in zip(FEATURE_NAMES, coef[1:])},
        }
        diagnostics = {
            "r_squared": r_squared,
            "n_obs": float(len(y)),
            "residual_std": float(np.std(residuals)),
        }
        return CalibrationResult(
            params=model_params,
            diagnostics=diagnostics,
            residuals=residuals.tolist(),
            last_window=returns[-HISTORY_WINDOW:].tolist(),
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
        coef = np.array([calibration.params[name] for name in FEATURE_NAMES])
        intercept = calibration.params["intercept"]

        def point_forecast_fn(history: list[float]) -> float:
            return float(intercept + coef @ _features(history))

        return simulate_by_residual_bootstrap(
            last_price=last_price,
            recent_log_returns=calibration.last_window,
            point_forecast_fn=point_forecast_fn,
            residuals=calibration.residuals,
            horizon_days=horizon_days,
            n_paths=n_paths,
            seed=seed,
            start_date=as_of,
            confidence_level=confidence_level_from_params(params),
        )


register(LinearRegressionModel())
