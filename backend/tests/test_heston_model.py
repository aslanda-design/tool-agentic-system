import math
from datetime import date

import numpy as np
import pytest
from scipy import stats

from app.domain.quant.annualization import TRADING_DAYS_PER_YEAR, annualized_variance, log_returns
from app.domain.quant.models.heston import MIN_HISTORY_DAYS, HestonModel


def _gbm_closes(
    n: int, mu_annual: float, sigma_annual: float, seed: int = 0, s0: float = 100.0
) -> list[float]:
    rng = np.random.default_rng(seed)
    dt = 1.0 / TRADING_DAYS_PER_YEAR
    z = rng.standard_normal(n - 1)
    daily_log_returns = (mu_annual - 0.5 * sigma_annual**2) * dt + sigma_annual * np.sqrt(dt) * z
    prices = s0 * np.exp(np.cumsum(daily_log_returns))
    return [s0, *prices.tolist()]


def test_calibrate_computes_v0_and_theta_from_the_formulas_directly():
    closes = _gbm_closes(600, mu_annual=0.05, sigma_annual=0.22, seed=1)
    result = HestonModel().calibrate(closes, {"kappa": 2.0, "xi": 0.3})

    returns = log_returns(closes)
    expected_v0 = annualized_variance(returns[-20:])
    expected_theta = annualized_variance(returns)

    assert result.params["v0"] == pytest.approx(expected_v0)
    assert result.params["theta"] == pytest.approx(expected_theta)
    assert result.residuals == []
    assert result.last_window == []


def test_feller_ratio_diagnostic_matches_the_formula():
    closes = _gbm_closes(600, mu_annual=0.0, sigma_annual=0.2, seed=2)
    kappa, xi = 3.0, 0.4
    result = HestonModel().calibrate(closes, {"kappa": kappa, "xi": xi})
    theta = result.params["theta"]
    assert result.diagnostics["feller_ratio"] == pytest.approx(2 * kappa * theta / xi**2)


def test_feller_ratio_is_infinite_when_xi_is_zero():
    closes = _gbm_closes(600, mu_annual=0.0, sigma_annual=0.2, seed=3)
    result = HestonModel().calibrate(closes, {"kappa": 2.0, "xi": 0.0})
    assert result.diagnostics["feller_ratio"] == math.inf


def test_calibrate_raises_on_insufficient_history():
    closes = _gbm_closes(MIN_HISTORY_DAYS, mu_annual=0.0, sigma_annual=0.2, seed=4)
    with pytest.raises(ValueError):
        HestonModel().calibrate(closes[:50], {})


def test_simulate_produces_expected_shape_and_finite_positive_prices():
    model = HestonModel()
    closes = _gbm_closes(600, mu_annual=0.04, sigma_annual=0.25, seed=5)
    calibration = model.calibrate(closes, {"kappa": 2.0, "xi": 0.3, "rho": -0.6})
    result = model.simulate(
        last_price=closes[-1],
        as_of=date(2026, 1, 1),
        calibration=calibration,
        horizon_days=20,
        n_paths=50,
        seed=1,
        params={"kappa": 2.0, "xi": 0.3, "rho": -0.6},
    )
    assert len(result.paths) == 50
    assert all(len(p) == 20 for p in result.paths)
    flat = [v for path in result.paths for v in path]
    assert all(np.isfinite(v) and v > 0 for v in flat)


def test_simulate_stays_finite_even_when_feller_condition_is_badly_violated():
    """A tiny kappa*theta and a large xi push the discretized variance
    toward zero often — exactly what full truncation (section 3.4) exists
    to handle gracefully. A NaN/inf here means the floor logic broke."""
    model = HestonModel()
    closes = _gbm_closes(600, mu_annual=0.0, sigma_annual=0.05, seed=6)
    calibration = model.calibrate(closes, {"kappa": 0.1, "xi": 2.0})
    result = model.simulate(
        last_price=closes[-1],
        as_of=date(2026, 1, 1),
        calibration=calibration,
        horizon_days=100,
        n_paths=200,
        seed=1,
        params={"kappa": 0.1, "xi": 2.0, "rho": -0.9},
    )
    flat = [v for path in result.paths for v in path]
    assert all(np.isfinite(v) and v > 0 for v in flat)


def test_simulate_deterministic_under_fixed_seed():
    model = HestonModel()
    closes = _gbm_closes(600, mu_annual=0.05, sigma_annual=0.2, seed=7)
    calibration = model.calibrate(closes, {})
    kwargs = {
        "last_price": closes[-1],
        "as_of": date(2026, 1, 1),
        "calibration": calibration,
        "horizon_days": 10,
        "n_paths": 20,
        "params": {},
    }
    result_a = model.simulate(seed=9, **kwargs)
    result_b = model.simulate(seed=9, **kwargs)
    assert result_a.paths == result_b.paths


def test_collapses_to_gbm_closed_form_when_xi_is_zero_and_v0_equals_theta():
    """The structural fact from plans/quant_lab_phase7_8.md section 3.7:
    with no vol-of-vol and variance starting exactly at its long-run
    level, Heston's variance process is deterministically constant at
    theta, and the price SDE reduces exactly to GBM with sigma^2=theta.
    This chains Heston's implementation against GBM's own closed-form
    formula (section 2.2) — the strongest correctness test available for
    either SDE-based model."""
    model = HestonModel()
    closes = _gbm_closes(600, mu_annual=0.05, sigma_annual=0.2, seed=8)
    calibration = model.calibrate(closes, {})
    mu = calibration.params["mu"]
    theta = calibration.params["theta"]
    # Force v0 == theta regardless of the calibrated (last-20-day) value —
    # this test is about the xi=0 structural collapse, not about how close
    # the recent realized variance happens to be to the long-run one.
    calibration.params["v0"] = theta
    sigma = math.sqrt(theta)
    last_price = closes[-1]
    horizon_days = 40

    result = model.simulate(
        last_price=last_price,
        as_of=date(2026, 1, 1),
        calibration=calibration,
        horizon_days=horizon_days,
        n_paths=5000,
        seed=42,
        params={"xi": 0.0, "kappa": 2.0, "rho": -0.6},
    )

    dt = 1.0 / TRADING_DAYS_PER_YEAR
    for day_idx in (0, 9, 19, 39):
        T = (day_idx + 1) * dt
        mean_log = math.log(last_price) + (mu - 0.5 * sigma**2) * T
        std_log = sigma * math.sqrt(T)
        for q, key in ((5, "lower"), (50, "median"), (95, "upper")):
            expected = math.exp(stats.norm.ppf(q / 100, loc=mean_log, scale=std_log))
            actual = result.percentiles[key][day_idx]
            assert actual == pytest.approx(expected, rel=0.08), f"day {day_idx}, {key}"
