"""Rough Heston — Heston's stochastic-volatility model with a fractional
kernel in place of ordinary exponential mean-reversion, so variance has
"roughness" (a Hurst parameter H < 0.5) baked in, matching the
well-established empirical finding that real market volatility is far
rougher than plain Heston's own variance process assumes — see
plans/quant_lab_phase9_rough_heston.md for the full derivation, worked
example, and the honest data-estimation limits (H is genuinely estimable
from this asset's own history, unlike kappa/xi/rho, which remain manual
sliders for exactly the same structural reason heston.py's own are).

    dS_t = S_t*sqrt(v_t) dW_t^S
    v_t  = v0 + int_0^t K(t-s)*kappa*(theta-v_s) ds + int_0^t K(t-s)*xi*sqrt(v_s) dW_s^v
    K(t) = t^(H-1/2) / Gamma(H+1/2)

Unlike heston.py, v_t has no simple Markovian SDE (section 2.3) — this
simulates it via a MULTI-FACTOR MARKOVIAN LIFTING (Abi Jaber 2019;
Abi Jaber & El Euch 2019; Bayer & Breneis 2021, chosen in section 4 over
the more accurate but much heavier "hybrid scheme"): the fractional
kernel is approximated by a finite sum of N ordinary exponentials
(rough_kernel.py), each of which admits a perfectly ordinary Markovian
SDE — so simulate() is heston.py's own full-truncation Euler loop,
generalized from one variance factor to N correlated ones (section 6).

At H=0.5, K(t) collapses to the constant kernel 1 and this model's SDE
reduces exactly to plain Heston's (section 2.4) — the strongest available
correctness check, chaining into Heston's own collapse to GBM at
xi=0,v0=theta (plans/quant_lab_phase7_8.md section 3.7). H is clamped
below 0.5 in this model's own ParamSpec, so that check is exercised near,
not exactly at, H=0.5 — see test_rough_heston_model.py.

Parameters are ANNUALIZED with dt=1/252 per simulated trading day, same
convention as black_scholes_gbm.py/heston.py.
"""

from __future__ import annotations

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
from app.domain.quant.rough_kernel import fit_kernel_quadrature
from app.domain.quant.roughness import DEFAULT_H, estimate_hurst
from app.domain.quant.types import CalibrationResult, ModelMetadata, ParamSpec, SimulationResult

MIN_HISTORY_DAYS = 252  # ~1 trading year — leaves >200 usable RV-proxy points for H (roughness.py)
V0_WINDOW = 20  # trading days of realized variance used as "today's" variance estimate

# Literature-typical equity/ETF defaults — same values heston.py uses and
# for the same structural reason (plans/quant_lab_phase9_rough_heston.md
# section 3.3): not fitted to any one asset, since these three still can't
# be reliably identified from price history alone even once roughness is
# accounted for.
DEFAULT_KAPPA = 2.0
DEFAULT_XI = 0.3
DEFAULT_RHO = -0.6

DEFAULT_N_FACTORS = 20  # the multi-factor lift's accuracy/speed dial — section 6.4
REFERENCE_HORIZON_YEARS = 1.0  # for calibrate()'s indicative kernel-fit diagnostic only — section 6.3

METADATA = ModelMetadata(
    key="rough_heston",
    display_name="Rough Heston (rough volatility)",
    description=(
        "Heston's stochastic-volatility model with a fractional kernel in place of ordinary "
        "exponential mean-reversion — variance now has 'roughness' (a Hurst parameter H < 0.5) "
        "baked in, matching the well-established empirical finding that real market volatility "
        "is far rougher than Heston's own variance process assumes. H is estimated from this "
        "asset's own history (a noisier daily-bar proxy of the tick-level estimate the research "
        "literature uses); v0 and the long-run variance are estimated exactly as in the plain "
        "Heston model; mean-reversion speed, vol-of-vol, and the leverage correlation are, as in "
        "Heston, literature-typical defaults you can adjust — they can't be reliably identified "
        "from price history alone. Simulated via a Markovian multi-factor approximation of the "
        "true fractional-kernel process, not an exact simulation — see the diagnostics' fit-"
        "quality field."
    ),
    family="stochastic_process",
    min_history_days=MIN_HISTORY_DAYS,
    supports_calibration=True,
    param_specs=[
        ParamSpec(
            key="h",
            label="Roughness (Hurst H)",
            kind="float",
            default=DEFAULT_H,
            min=0.02,
            max=0.49,
            help="How jagged/bursty volatility is at every timescale. Lower = rougher (real "
            "markets typically estimate around 0.1). 0.5 would recover plain Heston.",
        ),
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
            key="n_factors",
            label="Approximation factors (N)",
            kind="int",
            default=DEFAULT_N_FACTORS,
            min=5,
            max=40,
            help="How many terms approximate the fractional kernel — more is a more faithful "
            "simulation of the true model (see the fit-quality diagnostic), slower to compute.",
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


class RoughHestonModel:
    metadata = METADATA

    def calibrate(self, closes: list[float], params: dict[str, float]) -> CalibrationResult:
        returns = log_returns(closes)
        if len(returns) < MIN_HISTORY_DAYS:
            raise ValueError(
                f"rough_heston needs at least {MIN_HISTORY_DAYS} daily returns to calibrate, "
                f"got {len(returns)}"
            )
        mu, _sigma = annualized_mean_and_std(returns)
        v0 = annualized_variance(returns[-V0_WINDOW:])
        theta = annualized_variance(returns)
        hurst = estimate_hurst(closes)

        n_factors = int(params.get("n_factors", DEFAULT_N_FACTORS))
        # Indicative only, at a fixed reference horizon — simulate() always
        # recomputes the real quadrature against that call's actual
        # horizon_days (section 6.3); this exists so the Calibration card
        # has something concrete to show before a horizon is even chosen.
        reference_quad = fit_kernel_quadrature(
            hurst.h, dt=1.0 / TRADING_DAYS_PER_YEAR, horizon_years=REFERENCE_HORIZON_YEARS,
            n_factors=n_factors,
        )

        return CalibrationResult(
            params={"mu": mu, "v0": v0, "theta": theta, "h": hurst.h},
            diagnostics={
                "v0": v0,
                "theta": theta,
                "h": hurst.h,
                "h_source": hurst.source,
                "n_obs": float(len(returns)),
                "kernel_fit_rel_error_1y_ref": reference_quad.rel_error,
            },
            residuals=[],  # unused — simulated via SDE stepping, not a residual bootstrap
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
        h = calibration.params["h"]
        kappa = params.get("kappa", DEFAULT_KAPPA)
        xi = params.get("xi", DEFAULT_XI)
        rho = params.get("rho", DEFAULT_RHO)
        n_factors = int(params.get("n_factors", DEFAULT_N_FACTORS))
        dt = 1.0 / TRADING_DAYS_PER_YEAR
        horizon_years = horizon_days * dt
        sqrt_one_minus_rho2 = float(np.sqrt(max(1.0 - rho**2, 0.0)))

        # Recomputed fresh every call — the gamma grid depends on
        # horizon_years, a simulate()-time input calibrate() never sees
        # (section 6.3). NOT cached/reused from calibrate()'s own
        # reference-horizon fit above.
        quad = fit_kernel_quadrature(h, dt, horizon_years, n_factors)
        gammas, weights = quad.gammas, quad.weights

        rng = np.random.default_rng(seed)
        u = np.zeros((n_paths, n_factors), dtype=float)
        v = np.full(n_paths, v0, dtype=float)
        log_price = np.full(n_paths, np.log(last_price), dtype=float)
        paths = np.empty((n_paths, horizon_days), dtype=float)

        for t in range(horizon_days):
            v_pos = np.maximum(v, 0.0)
            z1 = rng.standard_normal(n_paths)
            z2 = rng.standard_normal(n_paths)
            zv = rho * z1 + sqrt_one_minus_rho2 * z2

            # Shared across every factor (section 6.1) — every factor's SDE
            # is driven by the same kappa*(theta-v_t)dt + xi*sqrt(v_t)dW_t^v
            # term, differing only in its own decay rate gamma_i (applied
            # via the broadcast below) and its own readout weight c_i
            # (applied only at the final sum, not per step).
            drift_common = kappa * (theta - v_pos) * dt
            diffusion_common = xi * np.sqrt(v_pos * dt) * zv

            u = u + (-gammas[None, :] * u) * dt + (drift_common + diffusion_common)[:, None]
            v = v0 + u @ weights

            log_price += (mu - 0.5 * v_pos) * dt + np.sqrt(v_pos * dt) * z1
            paths[:, t] = np.exp(log_price)

        percentiles = confidence_band_percentiles(paths, confidence_level_from_params(params))
        return SimulationResult(
            dates=next_trading_days(as_of, horizon_days),
            paths=paths.tolist(),
            percentiles=percentiles,
        )


register(RoughHestonModel())
