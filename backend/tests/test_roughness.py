import math

import numpy as np

from app.domain.quant.rough_kernel import fit_kernel_quadrature
from app.domain.quant.roughness import (
    DEFAULT_H,
    H_MAX,
    H_MIN,
    MIN_OBS_FOR_H_ESTIMATE,
    estimate_hurst,
    realized_vol_proxy,
)


def _flat_closes(n: int, daily_return: float = 0.0005) -> list[float]:
    price = 100.0
    closes = [price]
    for _ in range(n):
        price *= math.exp(daily_return)
        closes.append(price)
    return closes


def _rough_vol_closes(h_true: float, n_days: int, seed: int, n_factors: int = 20) -> list[float]:
    """Section 3.2's own "simulate-then-recover" round trip: drive a
    synthetic price series with a log-volatility process built from THIS
    plan's own multi-factor kernel machinery (rough_kernel.py) at a known
    target Hurst parameter, then feed the resulting closes through
    estimate_hurst and check it recovers something in the right
    direction/magnitude — the same idiom
    plans/quant_lab_phase10_hawkes.md's own test plan established for
    validating an estimator with no finite-sample closed form.

    Returns are generated with a DETERMINISTIC magnitude (r_t =
    sigma_t*sqrt(dt), no separate per-day idiosyncratic shock) so this
    isolates whether the regression recovers roughness that's genuinely
    present in the vol path — real market returns carry additional
    idiosyncratic noise on top of their own "true" volatility, which is a
    separate, already-documented limitation (see RV_WINDOW's own comment
    in roughness.py) this synthetic construction doesn't also need to
    stress-test."""
    dt = 1.0 / 252.0
    horizon_years = n_days * dt
    quad = fit_kernel_quadrature(h_true, dt, horizon_years, n_factors)
    rng = np.random.default_rng(seed)
    u = np.zeros(len(quad.gammas))
    price = 100.0
    closes = [price]
    for _ in range(n_days):
        z = rng.standard_normal()
        u = u + (-quad.gammas * u) * dt + math.sqrt(dt) * z
        log_vol_t = float(np.dot(quad.weights, u))
        sigma_t = 0.2 * math.exp(log_vol_t)
        r = sigma_t * math.sqrt(dt)
        price *= math.exp(r)
        closes.append(price)
    return closes


def test_realized_vol_proxy_length_and_positivity():
    closes = _rough_vol_closes(0.1, 300, seed=1)
    proxy = realized_vol_proxy(closes, window=2)
    assert len(proxy) == len(closes) - 1 - 2 + 1
    assert np.all(proxy >= 0.0)


def test_fallback_below_min_obs():
    closes = _flat_closes(MIN_OBS_FOR_H_ESTIMATE)  # too short once the RV window is consumed
    estimate = estimate_hurst(closes)
    assert estimate.source == "fallback_default"
    assert estimate.h == DEFAULT_H


def test_estimate_stays_within_bounds():
    for h_true in (0.05, 0.2, 0.45):
        closes = _rough_vol_closes(h_true, 1500, seed=3)
        estimate = estimate_hurst(closes)
        assert H_MIN <= estimate.h <= H_MAX


def test_round_trip_recovers_relative_roughness_ordering():
    """Not a tight-tolerance test (section 3.2 is explicit that this
    app's daily-bar proxy is noisier than the literature's own tick-level
    estimate) — checks direction, not precise recovery: a rougher target
    (lower H) should recover a lower (or equal, once clamped) estimated H
    than a smoother one, consistently across several seeds."""
    for seed in range(3):
        estimates = {
            h_true: estimate_hurst(_rough_vol_closes(h_true, 1500, seed)).h
            for h_true in (0.1, 0.2, 0.4)
        }
        assert estimates[0.1] < estimates[0.2] < estimates[0.4], (seed, estimates)


def test_estimate_source_is_estimated_with_enough_data():
    closes = _rough_vol_closes(0.15, 1500, seed=5)
    estimate = estimate_hurst(closes)
    assert estimate.source == "estimated"
    assert estimate.n_obs >= MIN_OBS_FOR_H_ESTIMATE
