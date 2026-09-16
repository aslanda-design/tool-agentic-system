"""Heston stochastic-volatility model — see plans/quant_lab_phase7_8.md
section 3 for the full derivation, worked examples, and — importantly —
why kappa/xi/rho are NOT fitted to this asset's history (v0 and theta are).

Two correlated SDEs (price and its own instantaneous variance):

    dS_t = mu*S_t dt + sqrt(v_t)*S_t dW_t^S
    dv_t = kappa*(theta - v_t) dt + xi*sqrt(v_t) dW_t^v
    corr(dW_t^S, dW_t^v) = rho

Unlike black_scholes_gbm.py, Heston has no closed-form path solution — v_t
appears inside the diffusion of both SDEs, so simulate() discretizes with
the FULL TRUNCATION Euler scheme (Lord, Koekkoek & van Dijk 2010): every
formula that consumes v_t uses its positive part v+ = max(v_t, 0) instead,
which keeps the simulation well-behaved even when a discretized step would
otherwise go negative (a well-known problem with a naive Euler scheme on
this SDE, not specific to this implementation). This is vectorized across
paths, looped only over days (horizon_days iterations, each a handful of
numpy array ops over all paths at once) — cheaper than a naive per-path
loop, though not as cheap as GBM's fully closed-form, loop-free simulate().

Parameters are ANNUALIZED with dt=1/252 per simulated trading day, same
convention as black_scholes_gbm.py — see plans/quant_lab_phase7_8.md
section 1.1.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np

from app.domain.quant.annualization import (
    TRADING_DAYS_PER_YEAR,
    annualized_mean_and_std,
    annualized_variance,
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

MIN_HISTORY_DAYS = 120
V0_WINDOW = 20  # trading days of realized variance used as "today's" variance estimate

# Literature-typical equity/ETF defaults — NOT fitted to any one asset (see
# plans/quant_lab_phase7_8.md section 3.3 for why these three specifically
# can't be reliably estimated from price history alone).
DEFAULT_KAPPA = 2.0
DEFAULT_XI = 0.3
DEFAULT_RHO = -0.6

METADATA = ModelMetadata(
    key="heston",
    display_name="Heston (stochastic volatility)",
    description=(
        "Volatility itself follows a random, mean-reverting process, correlated with price moves "
        "(the equity 'leverage effect'). Today's variance and its long-run average are estimated "
        "from this asset's own history; mean-reversion speed, vol-of-vol, and the leverage "
        "correlation can't be reliably identified from price history alone (that needs an options "
        "market, which this app doesn't have data for) — they're literature-typical defaults you "
        "can adjust to explore 'what if volatility behaved this way.'"
    ),
    family="stochastic_process",
    min_history_days=MIN_HISTORY_DAYS,
    supports_calibration=True,
    param_specs=[
        ParamSpec(
            key="kappa",
            label="Mean-reversion speed (κ)",
            kind="float",
            default=DEFAULT_KAPPA,
            min=0.1,
            max=10.0,
            help="How fast variance snaps back to its long-run average, per year. Higher = faster.",
        ),
        ParamSpec(
            key="xi",
            label="Vol-of-vol (ξ)",
            kind="float",
            default=DEFAULT_XI,
            min=0.01,
            max=2.0,
            help="How noisy the variance process itself is.",
        ),
        ParamSpec(
            key="rho",
            label="Leverage correlation (ρ)",
            kind="float",
            default=DEFAULT_RHO,
            min=-0.99,
            max=0.99,
            help="Correlation between return shocks and variance shocks. Negative = falling prices "
            "tend to come with rising volatility (the usual equity pattern).",
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


def _feller_ratio(kappa: float, theta: float, xi: float) -> float:
    """2*kappa*theta / xi^2 — >= 1 means the (continuous-time) Feller
    condition holds and variance is mathematically kept away from zero;
    below 1 the simulated variance will spend more time pinned near zero.
    Not an error either way (full truncation handles both), just a
    diagnostic. xi=0 makes variance deterministic (no barrier issue at
    all), reported as +inf rather than dividing by zero."""
    if xi <= 0:
        return math.inf
    return 2.0 * kappa * theta / xi**2


class HestonModel:
    metadata = METADATA

    def calibrate(self, closes: list[float], params: dict[str, float]) -> CalibrationResult:
        returns = log_returns(closes)
        if len(returns) < MIN_HISTORY_DAYS:
            raise ValueError(
                f"heston needs at least {MIN_HISTORY_DAYS} daily returns to calibrate, got {len(returns)}"
            )
        mu, _sigma = annualized_mean_and_std(returns)
        v0 = annualized_variance(returns[-V0_WINDOW:])
        theta = annualized_variance(returns)

        kappa = params.get("kappa", DEFAULT_KAPPA)
        xi = params.get("xi", DEFAULT_XI)
        feller_ratio = _feller_ratio(kappa, theta, xi)

        return CalibrationResult(
            params={"mu": mu, "v0": v0, "theta": theta},
            diagnostics={
                "v0": v0,
                "theta": theta,
                "feller_ratio": feller_ratio,
                "n_obs": float(len(returns)),
            },
            residuals=[],  # unused — Heston is simulated via SDE stepping, not a residual bootstrap
            last_window=[],
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
        theta = calibration.params["theta"]
        v0 = calibration.params["v0"]
        kappa = params.get("kappa", DEFAULT_KAPPA)
        xi = params.get("xi", DEFAULT_XI)
        rho = params.get("rho", DEFAULT_RHO)
        dt = 1.0 / TRADING_DAYS_PER_YEAR
        sqrt_one_minus_rho2 = math.sqrt(max(1.0 - rho**2, 0.0))

        rng = np.random.default_rng(seed)
        v = np.full(n_paths, v0, dtype=float)
        log_price = np.full(n_paths, math.log(last_price), dtype=float)
        paths = np.empty((n_paths, horizon_days), dtype=float)

        for t in range(horizon_days):
            v_pos = np.maximum(v, 0.0)
            z1 = rng.standard_normal(n_paths)
            z2 = rng.standard_normal(n_paths)
            zv = rho * z1 + sqrt_one_minus_rho2 * z2

            log_price += (mu - 0.5 * v_pos) * dt + np.sqrt(v_pos * dt) * z1
            v = v + kappa * (theta - v_pos) * dt + xi * np.sqrt(v_pos * dt) * zv

            paths[:, t] = np.exp(log_price)

        percentiles = confidence_band_percentiles(paths, confidence_level_from_params(params))
        return SimulationResult(
            dates=next_trading_days(as_of, horizon_days),
            paths=paths.tolist(),
            percentiles=percentiles,
        )


register(HestonModel())
