from datetime import date

import numpy as np
import pytest

from app.domain.quant.annualization import (
    TRADING_DAYS_PER_YEAR,
    annualized_mean_and_std,
    log_returns,
)
from app.domain.quant.hawkes import MIN_EVENTS_FOR_MLE, simulate_hawkes_events
from app.domain.quant.models.hawkes_jump_diffusion import (
    DEFAULT_BETA,
    DEFAULT_BRANCHING_RATIO,
    DEFAULT_MU_H,
    MIN_HISTORY_DAYS,
    HawkesJumpDiffusionModel,
)


def _gbm_closes(
    n: int, mu_annual: float, sigma_annual: float, seed: int = 0, s0: float = 100.0
) -> list[float]:
    rng = np.random.default_rng(seed)
    dt = 1.0 / TRADING_DAYS_PER_YEAR
    z = rng.standard_normal(n - 1)
    daily_log_returns = (mu_annual - 0.5 * sigma_annual**2) * dt + sigma_annual * np.sqrt(dt) * z
    prices = s0 * np.exp(np.cumsum(daily_log_returns))
    return [s0, *prices.tolist()]


def _closes_from_returns(s0: float, returns: np.ndarray) -> list[float]:
    return [s0, *(s0 * np.exp(np.cumsum(returns))).tolist()]


def test_calibrate_raises_on_insufficient_history():
    closes = _gbm_closes(MIN_HISTORY_DAYS, mu_annual=0.0, sigma_annual=0.2, seed=1)
    with pytest.raises(ValueError):
        HawkesJumpDiffusionModel().calibrate(closes[:100], {})


def test_jump_day_detection_matches_hand_computed_threshold():
    closes = _gbm_closes(300, mu_annual=0.0, sigma_annual=0.15, seed=9)
    returns = log_returns(closes)
    sigma_full = float(np.std(returns))
    threshold_k = 3.0

    injected_indices = [10, 100, 250]
    jump_returns = returns.copy()
    for idx in injected_indices:
        jump_returns[idx] = 5 * sigma_full
    jump_closes = _closes_from_returns(closes[0], jump_returns)

    calibration = HawkesJumpDiffusionModel().calibrate(jump_closes, {"threshold_k": threshold_k})

    # Recompute the threshold check independently, the same way calibrate()
    # itself does (section 2.1), and confirm an exact match — plus that
    # every deliberately injected index was actually flagged.
    new_returns = log_returns(jump_closes)
    new_sigma_full = float(np.std(new_returns))
    expected_mask = np.abs(new_returns) > threshold_k * new_sigma_full

    assert calibration.diagnostics["n_jump_days_detected"] == float(np.sum(expected_mask))
    for idx in injected_indices:
        assert expected_mask[idx]


def test_fallback_path_used_when_too_few_jump_days_detected():
    closes = _gbm_closes(
        400, mu_annual=0.05, sigma_annual=0.15, seed=2
    )  # plain GBM, no injected jumps
    calibration = HawkesJumpDiffusionModel().calibrate(closes, {"threshold_k": 3.0})

    assert calibration.diagnostics["n_jump_days_detected"] < MIN_EVENTS_FOR_MLE
    assert calibration.diagnostics["source"] == "fallback_default"
    assert calibration.diagnostics["log_likelihood"] is None
    assert calibration.params["mu_h"] == DEFAULT_MU_H
    assert calibration.params["beta"] == DEFAULT_BETA
    assert calibration.diagnostics["branching_ratio"] == DEFAULT_BRANCHING_RATIO


def test_mle_path_fits_and_diffusion_excludes_jump_days():
    n = 600
    closes = _gbm_closes(n, mu_annual=0.03, sigma_annual=0.15, seed=5)
    returns = log_returns(closes)
    sigma_daily = float(np.std(returns))

    rng = np.random.default_rng(42)
    event_times = simulate_hawkes_events(
        mu=0.02, alpha=0.3, beta=0.4, lambda0=0.02, horizon=float(len(returns)), rng=rng
    )
    day_indices = sorted({int(t) for t in event_times})
    assert len(day_indices) >= MIN_EVENTS_FOR_MLE, "seed produced too few events — adjust the seed"

    jump_returns = returns.copy()
    for idx in day_indices:
        jump_returns[idx] += rng.choice([-1.0, 1.0]) * 6 * sigma_daily
    jump_closes = _closes_from_returns(closes[0], jump_returns)

    calibration = HawkesJumpDiffusionModel().calibrate(jump_closes, {"threshold_k": 3.0})

    assert calibration.diagnostics["source"] == "mle"
    assert calibration.diagnostics["log_likelihood"] is not None

    # The diffusive part must come from the non-jump days only — lower
    # variance than GBM alone would estimate on the full, jump-contaminated
    # series (section 2.2's double-counting concern).
    full_returns = log_returns(jump_closes)
    _, sigma_full_series = annualized_mean_and_std(full_returns)
    assert calibration.params["sigma_diffusion"] < sigma_full_series


def test_alpha_zero_collapses_jump_timing_to_a_poisson_process():
    """This model's analogue of Heston's xi=0 collapse test (section 5):
    with no self-excitation, jump timing is a plain homogeneous Poisson
    process at rate mu — checkable against the Poisson mean/variance."""
    closes = _gbm_closes(400, mu_annual=0.05, sigma_annual=0.2, seed=1)
    calibration = HawkesJumpDiffusionModel().calibrate(closes, {})
    mu_h = calibration.params["mu_h"]
    beta = calibration.params["beta"]
    horizon = 60.0

    rng = np.random.default_rng(99)
    counts = np.array(
        [
            len(simulate_hawkes_events(mu_h, 0.0, beta, lambda0=mu_h, horizon=horizon, rng=rng))
            for _ in range(3000)
        ]
    )

    expected_mean = mu_h * horizon
    assert counts.mean() == pytest.approx(expected_mean, rel=0.1)
    assert counts.var() == pytest.approx(expected_mean, rel=0.35)  # Poisson: variance == mean


def test_simulate_produces_expected_shape_and_finite_positive_prices():
    model = HawkesJumpDiffusionModel()
    closes = _gbm_closes(400, mu_annual=0.04, sigma_annual=0.2, seed=3)
    calibration = model.calibrate(closes, {"threshold_k": 3.0})
    result = model.simulate(
        last_price=closes[-1],
        as_of=date(2026, 1, 1),
        calibration=calibration,
        horizon_days=20,
        n_paths=50,
        seed=1,
        params={"threshold_k": 3.0},
    )
    assert len(result.paths) == 50
    assert all(len(p) == 20 for p in result.paths)
    flat = [v for path in result.paths for v in path]
    assert all(np.isfinite(v) and v > 0 for v in flat)


def test_simulate_deterministic_under_fixed_seed():
    model = HawkesJumpDiffusionModel()
    closes = _gbm_closes(400, mu_annual=0.05, sigma_annual=0.2, seed=4)
    calibration = model.calibrate(closes, {})
    kwargs = {
        "last_price": closes[-1],
        "as_of": date(2026, 1, 1),
        "calibration": calibration,
        "horizon_days": 15,
        "n_paths": 25,
        "params": {},
    }
    result_a = model.simulate(seed=13, **kwargs)
    result_b = model.simulate(seed=13, **kwargs)
    assert result_a.paths == result_b.paths
