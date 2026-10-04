import numpy as np
import pytest

from app.domain.quant.annualization import (
    TRADING_DAYS_PER_YEAR,
    annualized_mean_and_std,
    annualized_variance,
    log_returns,
)


def test_log_returns_matches_manual_calculation():
    closes = [100.0, 110.0, 99.0]
    result = log_returns(closes)
    expected = [np.log(110.0 / 100.0), np.log(99.0 / 110.0)]
    assert result.tolist() == pytest.approx(expected)


def test_log_returns_rejects_non_positive_prices():
    try:
        log_returns([100.0, 0.0, 90.0])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_annualized_mean_and_std_on_a_deterministic_series():
    # A constant daily return of 0.01 has zero std, so annualization is exact.
    returns = np.array([0.01] * 10)
    mu, sigma = annualized_mean_and_std(returns)
    assert mu == pytest.approx(0.01 * TRADING_DAYS_PER_YEAR)
    assert sigma == pytest.approx(0.0, abs=1e-12)


def test_annualized_mean_and_std_scales_correctly():
    returns = np.array([0.01, -0.02, 0.015, 0.0, -0.005])
    mu, sigma = annualized_mean_and_std(returns)
    assert mu == float(np.mean(returns)) * TRADING_DAYS_PER_YEAR
    assert sigma == float(np.std(returns)) * np.sqrt(TRADING_DAYS_PER_YEAR)


def test_annualized_variance_matches_std_squared():
    returns = np.array([0.01, -0.02, 0.015, 0.0, -0.005])
    _mu, sigma = annualized_mean_and_std(returns)
    assert annualized_variance(returns) == sigma**2
