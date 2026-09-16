"""Hurst-parameter (roughness) estimation for rough Heston — see
plans/quant_lab_phase9_rough_heston.md section 3.2 for the full derivation
and honesty caveats. Deliberately kept separate from the price model
(models/rough_heston.py), same reasoning hawkes.py/rough_kernel.py already
established for their own pure-math primitives.

Unlike heston.py's kappa/xi/rho, H is a genuinely estimable, structural
property of the volatility path itself (Gatheral, Jaisson & Rosenbaum
2018, "Volatility is rough") — but this app has no intraday history to
build the tick-level realized-vol series the original estimator uses
(plans/quant_lab.md section 11's daily-bar-only limitation), so this
module applies the same structure-function log-log-regression technique
to a coarser, honestly-labeled proxy: a rolling realized-vol series built
from daily log-returns. Treat the result as directionally informative,
not a precise recovery of "the" true H for a given asset — noisier than
the literature's own tick-level estimate by construction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.domain.quant.annualization import log_returns

RV_WINDOW = 2  # trading days — the rolling realized-vol proxy's window

# A real, empirically-verified bias/noise trade-off, not an arbitrary
# choice: roughness is a SHORT-LAG property, and averaging window daily
# squared returns to build the proxy acts as a low-pass filter that
# actively destroys roughness signal at lags below (and comparable to)
# `window` — a plan-sketch window of 20 (chosen, initially, by loose
# analogy to heston.py's v0 window) was verified during implementation to
# make the regression's slope converge to ~0.7-0.8 regardless of the true
# H baked into a synthetic series, i.e. useless. A window this short still
# doesn't eliminate the opposite failure mode (a single day's squared
# return is itself a very noisy point estimate of "volatility," which is
# exactly why realized-vol estimators use a window at all) — `window=2`
# is a deliberately modest compromise that keeps most of the short-lag
# signal in a clean synthetic test (see test_roughness.py's round-trip
# test) while damping the single-day-noise case slightly; on real, noisier
# market data this estimate should be expected to carry real uncertainty
# beyond what a clean synthetic test can show — see
# plans/quant_lab_phase9_rough_heston.md section 3.2's own honesty caveat,
# now confirmed concretely rather than just anticipated in the abstract.
DEFAULT_LAGS = (1, 2, 5, 10, 20)  # trading days — structure-function lags

MIN_OBS_FOR_H_ESTIMATE = 100  # below this many proxy points, don't trust the regression
H_MIN, H_MAX = 0.02, 0.49  # strictly below 0.5 ("not rough" is heston.py's job) and above 0
DEFAULT_H = 0.10  # literature-typical (Gatheral-Jaisson-Rosenbaum), used on fallback

_LOG_FLOOR = 1e-12  # guards log(0) on a (practically impossible) all-zero-return window


@dataclass(slots=True)
class HurstEstimate:
    h: float
    source: str  # "estimated" | "fallback_default"
    n_obs: int  # realized-vol proxy points the estimate (or fallback decision) was based on


def realized_vol_proxy(closes: list[float], window: int = RV_WINDOW) -> np.ndarray:
    """Rolling-window realized volatility from daily log-returns — a
    coarse, honestly-labeled proxy for the tick-level realized vol the
    original rough-volatility literature estimates H from (see this
    module's own docstring). sigma_t = sqrt(mean(r_i^2 for the last
    `window` returns ending at t)); length = len(log_returns) - window + 1."""
    returns = log_returns(closes)
    squared = returns**2
    if len(squared) < window:
        return np.array([], dtype=float)
    cumsum = np.cumsum(np.insert(squared, 0, 0.0))
    rolling_mean = (cumsum[window:] - cumsum[:-window]) / window
    return np.sqrt(rolling_mean)


def estimate_hurst(
    closes: list[float], window: int = RV_WINDOW, lags: tuple[int, ...] = DEFAULT_LAGS
) -> HurstEstimate:
    """The Gatheral-Jaisson-Rosenbaum structure-function estimator: for a
    process with Hurst parameter H, absolute increments of log-volatility
    over a lag Delta scale like Delta^H, so regressing
    log(mean|log(sigma_{t+lag}) - log(sigma_t)|) against log(lag) recovers
    H as the slope (section 3.2) — the same np.polyfit log-log-regression
    tool domain/quant/recommendation.py already uses for its own trend-R^2
    check, no new statistical machinery.

    Falls back to DEFAULT_H (source="fallback_default") below
    MIN_OBS_FOR_H_ESTIMATE proxy points or when too few lags yield a
    usable (positive) structure-function value to regress — same shape of
    "too little data to trust a real fit" fallback hawkes.py's
    MIN_EVENTS_FOR_MLE already established, applied to a different
    estimator. Either way, the returned h is clamped to [H_MIN, H_MAX]."""
    sigma = realized_vol_proxy(closes, window)
    n_obs = len(sigma)
    if n_obs < MIN_OBS_FOR_H_ESTIMATE:
        return HurstEstimate(h=DEFAULT_H, source="fallback_default", n_obs=n_obs)

    log_sigma = np.log(np.maximum(sigma, _LOG_FLOOR))
    log_lags: list[float] = []
    log_m: list[float] = []
    for lag in lags:
        if lag >= n_obs:
            continue
        diffs = np.abs(log_sigma[lag:] - log_sigma[:-lag])
        m = float(np.mean(diffs))
        if m <= 0:
            continue
        log_lags.append(math.log(lag))
        log_m.append(math.log(m))

    if len(log_lags) < 3:
        return HurstEstimate(h=DEFAULT_H, source="fallback_default", n_obs=n_obs)

    slope, _intercept = np.polyfit(log_lags, log_m, 1)
    h = float(min(max(slope, H_MIN), H_MAX))
    return HurstEstimate(h=h, source="estimated", n_obs=n_obs)
