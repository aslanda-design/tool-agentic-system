import numpy as np

from app.domain.quant.recommendation import recommend_model


def test_too_short_series_returns_empty():
    assert recommend_model([100.0, 101.0, 99.0]) == []


def test_trending_series_favors_linear_regression():
    rng = np.random.default_rng(2)
    prices = [100.0 + 0.5 * i + rng.normal(0, 0.5) for i in range(200)]
    recs = recommend_model(prices)
    assert recs, "expected at least one recommendation"
    assert recs[0].model_key == "linear_regression"


def test_returns_are_sorted_descending_by_score():
    rng = np.random.default_rng(0)
    prices = (100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))).tolist()
    recs = recommend_model(prices)
    scores = [r.score for r in recs if r.model_key != "none"]
    assert scores == sorted(scores, reverse=True)


def test_heavy_tailed_series_flags_jump_limitation():
    rng = np.random.default_rng(1)
    normal_part = rng.normal(0, 0.005, 300)
    # Inject a few large jumps to create excess kurtosis.
    normal_part[::40] += rng.choice([-1, 1], size=len(normal_part[::40])) * 0.15
    prices = (100.0 * np.exp(np.cumsum(normal_part))).tolist()
    recs = recommend_model(prices)
    assert any(r.model_key == "hawkes_jump_diffusion" for r in recs)


def test_black_scholes_gbm_is_always_present_as_a_baseline():
    rng = np.random.default_rng(3)
    prices = (100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))).tolist()
    recs = recommend_model(prices)
    gbm = next(r for r in recs if r.model_key == "black_scholes_gbm")
    assert gbm.score == 0.2


def test_heston_outranks_time_series_ar_under_volatility_clustering():
    # A simple ARCH(1)-style process: variance depends on the previous
    # shock, producing real volatility clustering (unlike a plain iid
    # random walk) — exactly the pattern the Ljung-Box test should catch.
    rng = np.random.default_rng(4)
    n = 400
    returns = np.zeros(n)
    variance = 0.0001
    for i in range(1, n):
        variance = 0.00002 + 0.85 * returns[i - 1] ** 2
        returns[i] = rng.normal(0, np.sqrt(variance))
    prices = (100.0 * np.exp(np.cumsum(returns))).tolist()

    recs = recommend_model(prices)
    heston = next(r for r in recs if r.model_key == "heston")
    ar = next(r for r in recs if r.model_key == "time_series_ar")
    assert heston.score > 0, "expected the ARCH-style series to trigger real clustering evidence"
    assert heston.score >= ar.score
