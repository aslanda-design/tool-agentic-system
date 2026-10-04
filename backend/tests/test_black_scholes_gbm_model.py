import math
from datetime import date

import numpy as np
import pytest
from scipy import stats

from app.domain.quant.annualization import TRADING_DAYS_PER_YEAR
from app.domain.quant.models.black_scholes_gbm import MIN_HISTORY_DAYS, BlackScholesGbmModel


def _gbm_closes(
    n: int, mu_annual: float, sigma_annual: float, seed: int = 0, s0: float = 100.0
) -> list[float]:
    """Synthetic GBM series generated the same way the model itself would
    simulate one — used both to check calibrate() recovers the true
    parameters and as a source of realistic-looking price data."""
    rng = np.random.default_rng(seed)
    dt = 1.0 / TRADING_DAYS_PER_YEAR
    z = rng.standard_normal(n - 1)
    daily_log_returns = (mu_annual - 0.5 * sigma_annual**2) * dt + sigma_annual * np.sqrt(dt) * z
    prices = s0 * np.exp(np.cumsum(daily_log_returns))
    return [s0, *prices.tolist()]


def test_calibrate_recovers_true_parameters_approximately():
    closes = _gbm_closes(1000, mu_annual=0.08, sigma_annual=0.20, seed=1)
    result = BlackScholesGbmModel().calibrate(closes, {})
    # Drift is famously hard to pin down from historical prices — its
    # standard error here is sigma/sqrt(years) ~ 0.20/2 = 0.10, so even a
    # few years of daily data leaves a wide plausible range. Volatility's
    # estimate is much better-behaved (its relative standard error shrinks
    # with sample size, not just years), so that's the tight check.
    assert math.isfinite(result.params["mu"])
    assert result.params["sigma"] == pytest.approx(0.20, rel=0.15)
    assert result.diagnostics["n_obs"] == 999
    assert result.residuals == []
    assert result.last_window == []


def test_calibrate_raises_on_insufficient_history():
    closes = _gbm_closes(MIN_HISTORY_DAYS, mu_annual=0.05, sigma_annual=0.2, seed=2)
    with pytest.raises(ValueError):
        BlackScholesGbmModel().calibrate(closes[:30], {})


def test_simulate_matches_the_closed_form_lognormal_distribution():
    """The one independent-of-this-codebase correctness check available for
    GBM (see plans/quant_lab_phase7_8.md section 2.2): log(S_T) is exactly
    Normal(log(S0) + (mu - sigma^2/2)*T, sigma^2*T), so the simulated
    percentiles at every horizon day must converge to
    exp(that Normal's own quantiles) as n_paths grows."""
    model = BlackScholesGbmModel()
    closes = _gbm_closes(1000, mu_annual=0.06, sigma_annual=0.25, seed=3)
    calibration = model.calibrate(closes, {})
    mu, sigma = calibration.params["mu"], calibration.params["sigma"]
    last_price = closes[-1]
    horizon_days = 60

    result = model.simulate(
        last_price=last_price,
        as_of=date(2026, 1, 1),
        calibration=calibration,
        horizon_days=horizon_days,
        n_paths=5000,
        seed=42,
        params={},
    )

    dt = 1.0 / TRADING_DAYS_PER_YEAR
    for day_idx in (0, 9, 29, 59):  # spot-check a few horizon days, not all 60
        T = (day_idx + 1) * dt
        mean_log = np.log(last_price) + (mu - 0.5 * sigma**2) * T
        std_log = sigma * np.sqrt(T)
        for q, key in ((5, "lower"), (50, "median"), (95, "upper")):
            expected = np.exp(stats.norm.ppf(q / 100, loc=mean_log, scale=std_log))
            actual = result.percentiles[key][day_idx]
            assert actual == pytest.approx(expected, rel=0.08), f"day {day_idx}, {key}"


def test_simulate_deterministic_under_fixed_seed():
    model = BlackScholesGbmModel()
    closes = _gbm_closes(400, mu_annual=0.05, sigma_annual=0.18, seed=4)
    calibration = model.calibrate(closes, {})
    kwargs = {
        "last_price": closes[-1],
        "as_of": date(2026, 1, 1),
        "calibration": calibration,
        "horizon_days": 10,
        "n_paths": 20,
        "params": {},
    }
    result_a = model.simulate(seed=7, **kwargs)
    result_b = model.simulate(seed=7, **kwargs)
    assert result_a.paths == result_b.paths


def test_simulate_produces_expected_shape_and_positive_prices():
    model = BlackScholesGbmModel()
    closes = _gbm_closes(400, mu_annual=0.03, sigma_annual=0.30, seed=5)
    calibration = model.calibrate(closes, {})
    result = model.simulate(
        last_price=closes[-1],
        as_of=date(2026, 1, 1),
        calibration=calibration,
        horizon_days=15,
        n_paths=30,
        seed=1,
        params={},
    )
    assert len(result.paths) == 30
    assert all(len(p) == 15 for p in result.paths)
    assert all(v > 0 for path in result.paths for v in path)
    assert len(result.dates) == 15


def test_percentiles_are_monotonic_at_every_horizon_day():
    model = BlackScholesGbmModel()
    closes = _gbm_closes(400, mu_annual=0.0, sigma_annual=0.4, seed=6)
    calibration = model.calibrate(closes, {})
    result = model.simulate(
        last_price=closes[-1],
        as_of=date(2026, 1, 1),
        calibration=calibration,
        horizon_days=20,
        n_paths=500,
        seed=1,
        params={},
    )
    for day_idx in range(20):
        row = [result.percentiles[key][day_idx] for key in ("lower", "median", "upper")]
        assert row == sorted(row)
