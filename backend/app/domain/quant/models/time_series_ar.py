"""A classical time-series model — the second of the two models built to
prove the Quant Lab infrastructure (see plans/quant_lab.md section 4.5).

Implemented as a pure autoregression AR(p) via statsmodels' AutoReg,
**not** a full ARIMA(p,d,q)/MA model — a deliberate narrowing from the
plan's original sketch. Reason: simulate() needs a one-step-ahead forecast
function it can hand-roll forward path-by-path inside the residual
bootstrap (bootstrap.py), and a pure AR(p) recursion (`forecast = const +
sum(phi_i * lag_i)`) is straightforward and exact from AutoReg's own
fitted coefficients. Supporting differencing (d>0) or MA terms (q>0)
inside that same hand-rolled recursion is materially more work and more
ways to get subtly wrong — not worth it for what's explicitly meant to be
the "simple, textbook" model proving the infrastructure, not a
comprehensive ARIMA implementation. AutoReg is still a genuine, standard
time-series model, and the order (lag count) is user-adjustable on the
Quant Lab page's param form.
"""

from __future__ import annotations

from datetime import date

import numpy as np
from statsmodels.tsa.ar_model import AutoReg

from app.domain.quant.annualization import log_returns
from app.domain.quant.bootstrap import simulate_by_residual_bootstrap
from app.domain.quant.confidence import CONFIDENCE_LEVEL_PARAM_SPEC, confidence_level_from_params
from app.domain.quant.registry import register
from app.domain.quant.types import CalibrationResult, ModelMetadata, ParamSpec, SimulationResult

MIN_HISTORY_DAYS = 300
MAX_ORDER = 10

METADATA = ModelMetadata(
    key="time_series_ar",
    display_name="Time series (AR)",
    description=(
        "Autoregressive model AR(p) fit on daily log-returns via statsmodels. Captures "
        "short-term momentum/mean-reversion in the return series itself — still assumes "
        "constant volatility, so it won't capture volatility clustering any better than the "
        "linear regression model; see the recommendation tool for when that matters."
    ),
    family="time_series",
    min_history_days=MIN_HISTORY_DAYS,
    supports_calibration=True,
    param_specs=[
        ParamSpec(
            key="order_p",
            label="AR order (lags)",
            kind="int",
            default=1,
            min=1,
            max=MAX_ORDER,
            help="Number of past days' returns the model regresses on.",
        ),
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


class TimeSeriesArModel:
    metadata = METADATA

    def calibrate(self, closes: list[float], params: dict[str, float]) -> CalibrationResult:
        p = max(1, min(MAX_ORDER, round(params.get("order_p", 1))))
        returns = log_returns(closes)
        if len(returns) <= p + 30:
            raise ValueError(
                f"time_series_ar needs at least {p + 30} daily returns to calibrate, got {len(returns)}"
            )

        fit = AutoReg(returns, lags=p).fit()
        coef = np.asarray(fit.params, dtype=float)  # [const, lag_1, ..., lag_p]
        resid = np.asarray(fit.resid, dtype=float)
        resid = resid[~np.isnan(resid)]

        model_params = {"order_p": float(p), "const": float(coef[0])}
        for i in range(p):
            model_params[f"lag_{i + 1}"] = float(coef[i + 1])
        diagnostics = {
            "aic": float(fit.aic),
            "bic": float(fit.bic),
            "n_obs": float(fit.nobs),
            "residual_std": float(np.std(resid)) if len(resid) else 0.0,
        }
        return CalibrationResult(
            params=model_params,
            diagnostics=diagnostics,
            residuals=resid.tolist(),
            last_window=returns[-p:].tolist(),
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
        p = round(calibration.params["order_p"])
        const = calibration.params["const"]
        lag_coefs = [calibration.params[f"lag_{i + 1}"] for i in range(p)]

        def point_forecast_fn(history: list[float]) -> float:
            # history[-1] is the most recent return -> lag_1, history[-2] -> lag_2, ...
            return float(const + sum(lag_coefs[i] * history[-(i + 1)] for i in range(p)))

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


register(TimeSeriesArModel())
