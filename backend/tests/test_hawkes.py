import numpy as np
import pytest

from app.domain.quant.hawkes import (
    _excitation_recursion,
    fit_hawkes_mle,
    intensity_at,
    simulate_hawkes_events,
)


def test_round_trip_recovery_of_known_parameters():
    """Simulate-then-recover: the standard way to validate a point-process
    estimator when there's no closed form for a finite sample (section
    5's 'Round-trip recovery' test)."""
    mu, alpha, beta = 0.05, 0.4, 0.6
    rng = np.random.default_rng(123)
    event_times = simulate_hawkes_events(mu, alpha, beta, lambda0=mu, horizon=20000.0, rng=rng)
    assert len(event_times) > 200  # enough events for a tight recovery

    fit = fit_hawkes_mle(event_times, t_end=20000.0)

    assert fit.mu == pytest.approx(mu, rel=0.25)
    assert fit.alpha == pytest.approx(alpha, rel=0.3)
    assert fit.beta == pytest.approx(beta, rel=0.3)
    assert fit.branching_ratio == pytest.approx(alpha / beta, rel=0.3)
    assert fit.n_events == len(event_times)


def test_long_run_event_rate_matches_mu_over_one_minus_n():
    """Independent, closed-form fact about a stable Hawkes process
    (section 1.2): the long-run average event rate is mu/(1-n). This
    validates the simulator directly, with no MLE code path involved."""
    mu, alpha, beta = 0.02, 0.3, 0.5
    n = alpha / beta
    horizon = 200000.0
    rng = np.random.default_rng(7)
    event_times = simulate_hawkes_events(mu, alpha, beta, lambda0=mu, horizon=horizon, rng=rng)

    empirical_rate = len(event_times) / horizon
    expected_rate = mu / (1 - n)
    assert empirical_rate == pytest.approx(expected_rate, rel=0.1)


def test_excitation_recursion_matches_brute_force_double_sum():
    """Catches an off-by-one or indexing slip in the O(n) recursion that a
    purely statistical recovery test might not surface (section 5)."""
    event_times = [0.5, 1.2, 1.3, 4.0, 4.1, 9.0]
    beta = 0.7

    recursive = _excitation_recursion(event_times, beta)

    brute_force = np.zeros(len(event_times))
    for i in range(len(event_times)):
        for j in range(i):
            brute_force[i] += np.exp(-beta * (event_times[i] - event_times[j]))

    np.testing.assert_allclose(recursive, brute_force, rtol=1e-10)


def test_optimizer_never_fits_an_unstable_branching_ratio():
    """The n<1 stability condition (section 1.2) is enforced by the box
    constraint in the optimizer, not checked after the fact — feed it data
    that would 'want' n>=1 (many tightly clustered events) and confirm the
    fitted n still stays within [0, 0.95]."""
    # A short, extremely bursty synthetic sequence: lots of near-simultaneous
    # events, which pushes a naive MLE toward runaway self-excitation.
    rng = np.random.default_rng(3)
    event_times = sorted(rng.uniform(0, 5, size=50).tolist())

    fit = fit_hawkes_mle(event_times, t_end=5.0)

    assert 0.0 <= fit.branching_ratio <= 0.95


def test_intensity_at_matches_definition_directly():
    mu, alpha, beta = 0.1, 0.5, 1.0
    event_times = [1.0, 2.0]
    t = 3.0
    expected = mu + alpha * (np.exp(-beta * (t - 1.0)) + np.exp(-beta * (t - 2.0)))
    assert intensity_at(mu, alpha, beta, event_times, t) == pytest.approx(expected)


def test_intensity_at_before_any_event_is_just_mu():
    assert intensity_at(0.1, 0.5, 1.0, [], 3.0) == pytest.approx(0.1)
    assert intensity_at(0.1, 0.5, 1.0, [5.0], 3.0) == pytest.approx(0.1)


def test_simulate_hawkes_events_respects_horizon():
    rng = np.random.default_rng(11)
    event_times = simulate_hawkes_events(0.05, 0.2, 0.5, lambda0=0.05, horizon=100.0, rng=rng)
    assert all(0.0 <= t < 100.0 for t in event_times)
    assert event_times == sorted(event_times)
