from datetime import date

from app.domain.quant.bootstrap import simulate_by_residual_bootstrap


def test_simulate_is_deterministic_under_a_fixed_seed():
    def flat_forecast(_history: list[float]) -> float:
        return 0.0

    kwargs = {
        "last_price": 100.0,
        "recent_log_returns": [0.001] * 10,
        "point_forecast_fn": flat_forecast,
        "residuals": [0.01, -0.01, 0.02, -0.02, 0.0],
        "horizon_days": 15,
        "n_paths": 25,
        "start_date": date(2026, 1, 1),
    }
    result_a = simulate_by_residual_bootstrap(seed=42, **kwargs)
    result_b = simulate_by_residual_bootstrap(seed=42, **kwargs)
    assert result_a.paths == result_b.paths
    assert result_a.percentiles == result_b.percentiles


def test_simulate_different_seeds_diverge():
    def flat_forecast(_history: list[float]) -> float:
        return 0.0

    kwargs = {
        "last_price": 100.0,
        "recent_log_returns": [0.0] * 10,
        "point_forecast_fn": flat_forecast,
        "residuals": [0.05, -0.05, 0.1, -0.1],
        "horizon_days": 10,
        "n_paths": 25,
        "start_date": date(2026, 1, 1),
    }
    result_a = simulate_by_residual_bootstrap(seed=1, **kwargs)
    result_b = simulate_by_residual_bootstrap(seed=2, **kwargs)
    assert result_a.paths != result_b.paths


def test_percentiles_are_monotonic_at_every_horizon_day():
    def flat_forecast(_history: list[float]) -> float:
        return 0.0

    result = simulate_by_residual_bootstrap(
        last_price=50.0,
        recent_log_returns=[0.0] * 10,
        point_forecast_fn=flat_forecast,
        residuals=[0.03, -0.03, 0.06, -0.06, 0.01, -0.01],
        horizon_days=20,
        n_paths=200,
        seed=7,
        start_date=date(2026, 1, 1),
    )
    for day_idx in range(20):
        row = [result.percentiles[key][day_idx] for key in ("lower", "median", "upper")]
        assert row == sorted(row)


def test_confidence_level_widens_the_band():
    def flat_forecast(_history: list[float]) -> float:
        return 0.0

    kwargs = {
        "last_price": 100.0,
        "recent_log_returns": [0.0] * 10,
        "point_forecast_fn": flat_forecast,
        "residuals": [0.03, -0.03, 0.06, -0.06, 0.01, -0.01, 0.02, -0.02],
        "horizon_days": 10,
        "n_paths": 500,
        "seed": 7,
        "start_date": date(2026, 1, 1),
    }
    narrow = simulate_by_residual_bootstrap(confidence_level=0.5, **kwargs)
    wide = simulate_by_residual_bootstrap(confidence_level=0.95, **kwargs)
    day_idx = 9
    assert wide.percentiles["upper"][day_idx] - wide.percentiles["lower"][day_idx] > (
        narrow.percentiles["upper"][day_idx] - narrow.percentiles["lower"][day_idx]
    )
    # The median doesn't depend on how wide the surrounding band is.
    assert narrow.percentiles["median"] == wide.percentiles["median"]


def test_paths_shape_and_dates_length():
    def flat_forecast(_history: list[float]) -> float:
        return 0.0

    result = simulate_by_residual_bootstrap(
        last_price=10.0,
        recent_log_returns=[0.0] * 5,
        point_forecast_fn=flat_forecast,
        residuals=[0.0],
        horizon_days=8,
        n_paths=13,
        seed=1,
        start_date=date(2026, 1, 1),
    )
    assert len(result.paths) == 13
    assert all(len(p) == 8 for p in result.paths)
    assert len(result.dates) == 8


def test_all_prices_positive_from_positive_start():
    def small_forecast(_history: list[float]) -> float:
        return -0.5  # a large negative drift, to make sure prices never go non-positive

    result = simulate_by_residual_bootstrap(
        last_price=100.0,
        recent_log_returns=[0.0] * 5,
        point_forecast_fn=small_forecast,
        residuals=[0.0],
        horizon_days=30,
        n_paths=10,
        seed=3,
        start_date=date(2026, 1, 1),
    )
    assert all(v > 0 for path in result.paths for v in path)
