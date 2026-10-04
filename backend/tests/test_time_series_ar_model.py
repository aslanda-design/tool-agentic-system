from datetime import date

import numpy as np

from app.domain.quant.models.time_series_ar import TimeSeriesArModel


def _ar1_closes(n: int, phi: float = 0.3, seed: int = 0, vol: float = 0.01) -> list[float]:
    rng = np.random.default_rng(seed)
    shocks = rng.normal(0, vol, size=n - 1)
    returns = np.zeros(n - 1)
    for i in range(1, n - 1):
        returns[i] = phi * returns[i - 1] + shocks[i]
    prices = 100.0 * np.exp(np.cumsum(returns))
    return [100.0, *prices.tolist()]


def test_calibrate_recovers_ar1_structure_roughly():
    closes = _ar1_closes(500, phi=0.3, seed=1)
    result = TimeSeriesArModel().calibrate(closes, {"order_p": 1})
    assert result.params["order_p"] == 1.0
    # Not an exact recovery on finite noisy data, but the sign/rough
    # magnitude should be in the right neighborhood.
    assert 0.05 < result.params["lag_1"] < 0.6
    assert result.diagnostics["n_obs"] > 0
    assert len(result.last_window) == 1


def test_calibrate_respects_order_p_param():
    closes = _ar1_closes(500, phi=0.2, seed=2)
    result = TimeSeriesArModel().calibrate(closes, {"order_p": 3})
    assert result.params["order_p"] == 3.0
    assert {"lag_1", "lag_2", "lag_3"} <= set(result.params)
    assert len(result.last_window) == 3


def test_calibrate_raises_on_insufficient_history():
    closes = _ar1_closes(20, seed=3)
    try:
        TimeSeriesArModel().calibrate(closes, {"order_p": 1})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_simulate_produces_expected_shape_and_positive_prices():
    closes = _ar1_closes(500, phi=0.3, seed=4)
    model = TimeSeriesArModel()
    calibration = model.calibrate(closes, {"order_p": 2})
    result = model.simulate(
        last_price=closes[-1],
        as_of=date(2026, 1, 1),
        calibration=calibration,
        horizon_days=20,
        n_paths=30,
        seed=42,
        params={"order_p": 2},
    )
    assert len(result.paths) == 30
    assert all(len(p) == 20 for p in result.paths)
    assert all(v > 0 for path in result.paths for v in path)


def test_simulate_deterministic_under_fixed_seed():
    closes = _ar1_closes(500, phi=0.3, seed=5)
    model = TimeSeriesArModel()
    calibration = model.calibrate(closes, {"order_p": 1})
    kwargs = {
        "last_price": closes[-1],
        "as_of": date(2026, 1, 1),
        "calibration": calibration,
        "horizon_days": 10,
        "n_paths": 20,
        "params": {"order_p": 1},
    }
    result_a = model.simulate(seed=7, **kwargs)
    result_b = model.simulate(seed=7, **kwargs)
    assert result_a.paths == result_b.paths
