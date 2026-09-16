"""GBM diffusion + a Hawkes-timed jump component — see
plans/quant_lab_phase10_hawkes.md for the full derivation, worked example,
and the honest limitation (daily closes, not tick data, so "jump day"
detection is itself an approximation — section 0).

Jump-day detection (section 2.1) splits the return series into "ordinary"
days (calibrate black_scholes_gbm's own mu/sigma formulas, reused
directly) and "jump" days (calibrate a Hawkes process's timing via MLE,
plus a plain empirical Normal jump-size distribution) — see section 2.2
for why excluding jump days from the diffusive calibration matters (double-
counting the same extreme moves as both "high ordinary volatility" and "a
jump" otherwise).

hawkes.py deliberately knows nothing about prices/closes/returns; this
file is the composition layer, same separation bootstrap.py (the Monte
Carlo engine) has from the models that call it.
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
from app.domain.quant.hawkes import (
    MIN_EVENTS_FOR_MLE,
    fit_hawkes_mle,
    intensity_at,
    simulate_hawkes_events,
)
from app.domain.quant.registry import register
from app.domain.quant.types import CalibrationResult, ModelMetadata, ParamSpec, SimulationResult

MIN_HISTORY_DAYS = (
    252  # ~1 trading year — shorter windows make a real MLE fit unlikely (section 4.2)
)

DEFAULT_THRESHOLD_K = 3.0

# Literature-typical defaults used only below MIN_EVENTS_FOR_MLE detected
# jump days (section 2.4) — "a jump every couple of months at baseline,
# moderate clustering, aftershock effects fading over about a trading week."
DEFAULT_MU_H = 0.02
DEFAULT_BRANCHING_RATIO = 0.3
DEFAULT_BETA = 0.3

METADATA = ModelMetadata(
    key="hawkes_jump_diffusion",
    display_name="Hawkes jump-diffusion",
    description=(
        "GBM for ordinary days, plus occasional jumps whose TIMING follows a Hawkes process — "
        "jumps make further jumps more likely for a while afterward, then that effect fades "
        "('the market just had a shock, more turbulence likely soon'). Jump days are detected "
        "from this asset's own history (a return beyond a few standard deviations); with enough "
        "of them, the clustering strength itself is fitted by maximum likelihood — with too few, "
        "literature-typical defaults are used instead (see the diagnostics' 'source' field)."
    ),
    family="stochastic_process",
    min_history_days=MIN_HISTORY_DAYS,
    supports_calibration=True,
    param_specs=[
        ParamSpec(
            key="threshold_k",
            label="Jump threshold (k std devs)",
            kind="float",
            default=DEFAULT_THRESHOLD_K,
            min=1.5,
            max=6.0,
            help="A day's return beyond this many standard deviations counts as a 'jump day' for "
            "calibration. Lower = more days counted (more data, but some may just be normal "
            "volatility); higher = stricter.",
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
            max=500,
            help="More paths = smoother percentile bands, slower to compute.",
        ),
        CONFIDENCE_LEVEL_PARAM_SPEC,
    ],
)


class HawkesJumpDiffusionModel:
    metadata = METADATA

    def calibrate(self, closes: list[float], params: dict[str, float]) -> CalibrationResult:
        returns = log_returns(closes)
        if len(returns) < MIN_HISTORY_DAYS:
            raise ValueError(
                f"hawkes_jump_diffusion needs at least {MIN_HISTORY_DAYS} daily returns to "
                f"calibrate, got {len(returns)}"
            )

        threshold_k = float(params.get("threshold_k", DEFAULT_THRESHOLD_K))
        sigma_full = float(np.std(returns))  # daily, not annualized — section 2.1
        jump_mask = np.abs(returns) > threshold_k * sigma_full
        n_jump_days = int(np.sum(jump_mask))

        non_jump_returns = returns[~jump_mask]
        jump_returns = returns[jump_mask]
        mu_diffusion, sigma_diffusion = annualized_mean_and_std(non_jump_returns)

        # Event times in raw trading-day-index units, t_end one day past the
        # last observed return — section 2.3/4.3 (this model's timing params
        # are the one place in the Quant Lab NOT annualized).
        event_times = np.nonzero(jump_mask)[0].astype(float).tolist()
        t_end = float(len(returns))

        if n_jump_days >= MIN_EVENTS_FOR_MLE:
            source = "mle"
            fit = fit_hawkes_mle(event_times, t_end)
            mu_h, alpha, beta = fit.mu, fit.alpha, fit.beta
            branching_ratio = fit.branching_ratio
            log_likelihood: float | None = fit.log_likelihood
        else:
            source = "fallback_default"
            mu_h, branching_ratio, beta = DEFAULT_MU_H, DEFAULT_BRANCHING_RATIO, DEFAULT_BETA
            alpha = branching_ratio * beta
            log_likelihood = None

        if n_jump_days > 0:
            jump_mean = float(np.mean(jump_returns))
            jump_std = float(np.std(jump_returns))
        else:
            jump_mean, jump_std = 0.0, 0.0

        # The process is mid-aftershock (or not) when the simulated future
        # begins — pick up simulation from the real starting intensity
        # rather than a cold lambda=mu (section 2.5).
        lambda_0 = intensity_at(mu_h, alpha, beta, event_times, t_end)

        return CalibrationResult(
            params={
                "mu_diffusion": mu_diffusion,
                "sigma_diffusion": sigma_diffusion,
                "mu_h": mu_h,
                "alpha": alpha,
                "beta": beta,
                "jump_mean": jump_mean,
                "jump_std": jump_std,
                "lambda_0": lambda_0,
            },
            diagnostics={
                "n_jump_days_detected": float(n_jump_days),
                "threshold_k": threshold_k,
                "source": source,
                "mu_h": mu_h,
                "alpha": alpha,
                "beta": beta,
                "branching_ratio": branching_ratio,
                "jump_mean": jump_mean,
                "jump_std": jump_std,
                "mu_diffusion": mu_diffusion,
                "sigma_diffusion": sigma_diffusion,
                "log_likelihood": log_likelihood,
                "n_obs": float(len(returns)),
            },
            residuals=[],  # unused — this model is simulated via SDE + Hawkes event draws, not a bootstrap
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
        mu_diffusion = calibration.params["mu_diffusion"]
        sigma_diffusion = calibration.params["sigma_diffusion"]
        mu_h = calibration.params["mu_h"]
        alpha = calibration.params["alpha"]
        beta = calibration.params["beta"]
        jump_mean = calibration.params["jump_mean"]
        jump_std = calibration.params["jump_std"]
        lambda_0 = calibration.params["lambda_0"]
        dt = 1.0 / TRADING_DAYS_PER_YEAR

        rng = np.random.default_rng(seed)

        # The diffusive part: identical in form to black_scholes_gbm.py's
        # own simulate() — one vectorized draw for every path at once
        # (section 3.1).
        z = rng.standard_normal(size=(n_paths, horizon_days))
        diffusive_log_returns = (
            mu_diffusion - 0.5 * sigma_diffusion**2
        ) * dt + sigma_diffusion * np.sqrt(dt) * z
        cumulative_diffusive = np.cumsum(diffusive_log_returns, axis=1)

        # The jump part: inherently sequential and path-specific (each path
        # has its own random number of jumps, at its own random times) —
        # a Python loop over paths, same shape bootstrap.py already uses
        # for a different reason, not a new architectural pattern
        # (section 3.2).
        cumulative_jump = np.zeros((n_paths, horizon_days), dtype=float)
        for p in range(n_paths):
            jump_times = simulate_hawkes_events(
                mu_h, alpha, beta, lambda_0, float(horizon_days), rng
            )
            if not jump_times:
                continue
            day_indices = np.clip(np.floor(jump_times).astype(int), 0, horizon_days - 1)
            jump_sizes = rng.normal(jump_mean, jump_std, size=len(jump_times))
            log_jump_by_day = np.zeros(horizon_days, dtype=float)
            np.add.at(log_jump_by_day, day_indices, jump_sizes)
            cumulative_jump[p, :] = np.cumsum(log_jump_by_day)

        paths = last_price * np.exp(cumulative_diffusive + cumulative_jump)

        percentiles = confidence_band_percentiles(paths, confidence_level_from_params(params))
        return SimulationResult(
            dates=next_trading_days(as_of, horizon_days),
            paths=paths.tolist(),
            percentiles=percentiles,
        )


register(HawkesJumpDiffusionModel())
