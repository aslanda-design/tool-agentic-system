from datetime import date

import numpy as np

from app.domain.quant.models.linear_regression import HISTORY_WINDOW, LinearRegressionModel


def _random_walk_closes(
    n: int, seed: int = 0, drift: float = 0.0, vol: float = 0.01
) -> list[float]:
    rng = np.random.default_rng(seed)
    log_returns = rng.normal(drift, vol, size=n - 1)
    prices = 100.0 * np.exp(np.cumsum(log_returns))
    return [100.0, *prices.tolist()]


def test_calibrate_on_pure_random_walk_finds_little_structure():
    closes = _random_walk_closes(400, seed=1)
    result = LinearRegressionModel().calibrate(closes, {})
    # A pure random walk has no real return structure — R^2 should stay low,
    # not near 1 (which would mean the model hallucinated a signal).
    assert result.diagnostics["r_squared"] < 0.1
    assert set(result.params) == {
        "intercept",
        "lag_1",
        "lag_2",
        "lag_3",
        "lag_5",
        "lag_10",
        "vol",
        "mean",
    }
    assert len(result.last_window) == HISTORY_WINDOW
    assert len(result.residuals) > 0


def test_calibrate_raises_on_insufficient_history():
    closes = _random_walk_closes(20, seed=2)
    try:
        LinearRegressionModel().calibrate(closes, {})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_simulate_produces_expected_shape_and_positive_prices():
    closes = _random_walk_closes(300, seed=3)
    model = LinearRegressionModel()
    calibration = model.calibrate(closes, {})
    result = model.simulate(
        last_price=closes[-1],
        as_of=date(2026, 1, 1),
        calibration=calibration,
        horizon_days=15,
        n_paths=30,
        seed=42,
        params={},
    )
    assert len(result.paths) == 30
    assert all(len(p) == 15 for p in result.paths)
    assert all(v > 0 for path in result.paths for v in path)
    assert len(result.dates) == 15


def test_simulate_deterministic_under_fixed_seed():
    closes = _random_walk_closes(300, seed=4)
    model = LinearRegressionModel()
    calibration = model.calibrate(closes, {})
    kwargs = {
        "last_price": closes[-1],
        "as_of": date(2026, 1, 1),
        "calibration": calibration,
        "horizon_days": 10,
        "n_paths": 20,
        "params": {},
    }
    result_a = model.simulate(seed=99, **kwargs)
    result_b = model.simulate(seed=99, **kwargs)
    assert result_a.paths == result_b.paths
