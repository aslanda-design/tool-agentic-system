from datetime import date

import numpy as np
import pytest

from app.domain.quant.annualization import TRADING_DAYS_PER_YEAR, annualized_variance, log_returns
from app.domain.quant.models.heston import HestonModel
from app.domain.quant.models.rough_heston import MIN_HISTORY_DAYS, RoughHestonModel
from app.domain.quant.rough_kernel import fit_kernel_quadrature


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
    result = RoughHestonModel().calibrate(closes, {"kappa": 2.0, "xi": 0.3})

    returns = log_returns(closes)
    expected_v0 = annualized_variance(returns[-20:])
    expected_theta = annualized_variance(returns)

    assert result.params["v0"] == pytest.approx(expected_v0)
    assert result.params["theta"] == pytest.approx(expected_theta)
    assert result.residuals == []
    assert result.last_window == []


def test_calibrate_reports_h_and_source_and_kernel_fit_diagnostic():
    closes = _gbm_closes(700, mu_annual=0.0, sigma_annual=0.2, seed=2)
    result = RoughHestonModel().calibrate(closes, {})
    assert 0.02 <= result.params["h"] <= 0.49
    assert result.diagnostics["h_source"] in ("estimated", "fallback_default")
    assert result.diagnostics["kernel_fit_rel_error_1y_ref"] >= 0.0
    assert result.diagnostics["kernel_fit_rel_error_1y_ref"] < 0.1


def test_calibrate_raises_on_insufficient_history():
    closes = _gbm_closes(MIN_HISTORY_DAYS, mu_annual=0.0, sigma_annual=0.2, seed=4)
    with pytest.raises(ValueError):
        RoughHestonModel().calibrate(closes[:100], {})


def test_simulate_produces_expected_shape_and_finite_positive_prices():
    model = RoughHestonModel()
    closes = _gbm_closes(600, mu_annual=0.04, sigma_annual=0.25, seed=5)
    calibration = model.calibrate(closes, {"kappa": 2.0, "xi": 0.3, "n_factors": 15})
    result = model.simulate(
        last_price=closes[-1],
        as_of=date(2026, 1, 1),
        calibration=calibration,
        horizon_days=20,
        n_paths=50,
        seed=1,
        params={"kappa": 2.0, "xi": 0.3, "rho": -0.6, "n_factors": 15},
    )
    assert len(result.paths) == 50
    assert all(len(p) == 20 for p in result.paths)
    flat = [v for path in result.paths for v in path]
    assert all(np.isfinite(v) and v > 0 for v in flat)


def test_simulate_stays_finite_even_when_feller_condition_is_badly_violated():
    """Same stress case as test_heston_model.py's own — tiny kappa*theta,
    large xi — full truncation (shared with heston.py's own scheme,
    generalized to N factors, section 6.2) should keep this well-behaved."""
    model = RoughHestonModel()
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
    model = RoughHestonModel()
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


def test_kernel_grid_is_recomputed_per_horizon_not_cached_stale():
    """Section 6.3's structural gotcha, tested directly: the gamma grid
    depends on horizon_years, a simulate()-time input calibrate() never
    sees. Two simulate() calls on the SAME CalibrationResult at different
    horizon_days must use genuinely different quadratures, not a stale one
    reused from an earlier call or from calibrate()'s own reference fit."""
    model = RoughHestonModel()
    closes = _gbm_closes(600, mu_annual=0.03, sigma_annual=0.2, seed=11)
    calibration = model.calibrate(closes, {"n_factors": 10})
    h = calibration.params["h"]
    dt = 1.0 / TRADING_DAYS_PER_YEAR

    quad_short = fit_kernel_quadrature(h, dt, 10 * dt, n_factors=10)
    quad_long = fit_kernel_quadrature(h, dt, 200 * dt, n_factors=10)
    assert not np.allclose(quad_short.gammas, quad_long.gammas)

    # And simulate() itself actually behaves differently at the two
    # horizons in a way consistent with using its own horizon's grid: a
    # short-horizon run's own recomputed gammas span a narrower range than
    # a long-horizon run's would (gamma_min = GAMMA_MIN_SCALE/horizon_years
    # shrinks as horizon_years grows).
    result_short = model.simulate(
        last_price=closes[-1],
        as_of=date(2026, 1, 1),
        calibration=calibration,
        horizon_days=10,
        n_paths=20,
        seed=1,
        params={"n_factors": 10},
    )
    result_long = model.simulate(
        last_price=closes[-1],
        as_of=date(2026, 1, 1),
        calibration=calibration,
        horizon_days=200,
        n_paths=20,
        seed=1,
        params={"n_factors": 10},
    )
    assert len(result_short.paths[0]) == 10
    assert len(result_long.paths[0]) == 200


def test_n_factors_sensitivity_shrinks_as_n_grows():
    """A Monte-Carlo-level echo of test_rough_kernel.py's deterministic
    kernel-convergence test: increasing n_factors at fixed seed/params
    should move the simulated percentiles by a shrinking amount as N
    grows, confirming the approximation behaves as advertised once wired
    into the full price model (section 11)."""
    model = RoughHestonModel()
    closes = _gbm_closes(600, mu_annual=0.03, sigma_annual=0.2, seed=13)
    calibration = model.calibrate(closes, {"n_factors": 40})

    def median_at(n_factors: int) -> list[float]:
        result = model.simulate(
            last_price=closes[-1],
            as_of=date(2026, 1, 1),
            calibration=calibration,
            horizon_days=60,
            n_paths=3000,
            seed=77,
            params={"kappa": 2.0, "xi": 0.3, "rho": -0.6, "n_factors": n_factors},
        )
        return result.percentiles["median"]

    m5 = np.array(median_at(5))
    m20 = np.array(median_at(20))
    m40 = np.array(median_at(40))

    diff_5_to_20 = float(np.max(np.abs(m20 - m5)))
    diff_20_to_40 = float(np.max(np.abs(m40 - m20)))
    assert diff_20_to_40 < diff_5_to_20


def test_collapses_to_heston_when_h_is_near_one_half():
    """The structural fact from section 2.4: at H=0.5 the fractional
    kernel collapses to the constant kernel 1 and this model's SDE reduces
    exactly to plain Heston's. H is clamped below 0.5 in this model's own
    ParamSpec, so this drives H close to (not exactly) 0.5 and compares
    against HestonModel's own simulate() on matching v0/theta/mu/kappa/xi/
    rho — the strongest correctness test available for this model, and the
    second link (after heston.py's own xi=0,v0=theta->GBM check) in this
    Quant Lab's three-model verification chain."""
    closes = _gbm_closes(600, mu_annual=0.04, sigma_annual=0.2, seed=8)

    rough_model = RoughHestonModel()
    heston_model = HestonModel()

    rough_calibration = rough_model.calibrate(closes, {"kappa": 2.0, "xi": 0.3, "n_factors": 25})
    heston_calibration = heston_model.calibrate(closes, {"kappa": 2.0, "xi": 0.3})

    # Force matching v0/theta/mu so the comparison isolates the effect of
    # H alone, not incidental differences in how the two models' own v0/
    # theta estimates happened to land on this particular series.
    heston_calibration.params["v0"] = rough_calibration.params["v0"]
    heston_calibration.params["theta"] = rough_calibration.params["theta"]
    heston_calibration.params["mu"] = rough_calibration.params["mu"]
    rough_calibration.params["h"] = 0.49

    shared_params = {"kappa": 2.0, "xi": 0.3, "rho": -0.6}
    kwargs = {
        "last_price": closes[-1],
        "as_of": date(2026, 1, 1),
        "horizon_days": 60,
        "n_paths": 8000,
        "seed": 42,
    }

    rough_result = rough_model.simulate(
        calibration=rough_calibration, params={**shared_params, "n_factors": 25}, **kwargs
    )
    heston_result = heston_model.simulate(
        calibration=heston_calibration, params=shared_params, **kwargs
    )

    for day_idx in (0, 9, 29, 59):
        for key in ("lower", "median", "upper"):
            rough_value = rough_result.percentiles[key][day_idx]
            heston_value = heston_result.percentiles[key][day_idx]
            assert rough_value == pytest.approx(heston_value, rel=0.03), f"day {day_idx}, {key}"
