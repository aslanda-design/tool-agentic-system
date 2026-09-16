"""Hawkes self-exciting point-process primitives — see
plans/quant_lab_phase10_hawkes.md sections 1-3 for the full derivation and
worked examples. Deliberately knows nothing about prices, closes, or
returns: event times in, fitted parameters or simulated event times out —
the same separation of concerns bootstrap.py (the Monte Carlo engine) has
from the models that call it. Reusable in principle by any future jump
model, though only hawkes_jump_diffusion.py uses it today.

All times here are in raw units (trading days for the price model that
calls this, but this module doesn't know or care) — see
plans/quant_lab_phase10_hawkes.md section 4.3 for why the Hawkes timing
parameters specifically are NOT annualized, unlike every other
stochastic-process model in this package.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

# Below this many detected events, alpha/beta become poorly separated by
# MLE (see plans/quant_lab_phase10_hawkes.md section 2.4) — callers fall
# back to literature-typical defaults instead of trusting a fit this thin.
MIN_EVENTS_FOR_MLE = 8

# Box constraints for the (mu, n, beta) optimization — n<1 by construction
# enforces the stability condition from section 1.2 rather than checking it
# after the fact; mu/beta get small positive floors since the likelihood
# formula is undefined at exactly zero.
_MU_BOUNDS = (1e-6, None)
_N_BOUNDS = (0.0, 0.95)
_BETA_BOUNDS = (1e-6, None)


@dataclass(slots=True)
class HawkesFit:
    mu: float
    alpha: float
    beta: float
    branching_ratio: float
    log_likelihood: float
    n_events: int


def _excitation_recursion(event_times: list[float], beta: float) -> np.ndarray:
    """A_i for i=1..n via the O(n) Markov recursion (section 2.3):
    A_1 = 0, A_i = exp(-beta*(t_i - t_{i-1})) * (1 + A_{i-1}). A_i is "the
    sum of every earlier event's still-remaining excitation, evaluated at
    the moment of event i" — the inner sum in lambda(t) at t=t_i."""
    n = len(event_times)
    a = np.zeros(n, dtype=float)
    for i in range(1, n):
        gap = event_times[i] - event_times[i - 1]
        a[i] = math.exp(-beta * gap) * (1.0 + a[i - 1])
    return a


def _neg_log_likelihood(
    mu: float, alpha: float, beta: float, event_times: list[float], t_end: float
) -> float:
    """-log L(mu, alpha, beta) from plans/quant_lab_phase10_hawkes.md
    section 2.3, evaluated directly (used for the reported
    HawkesFit.log_likelihood, not the optimizer's own reparametrized
    objective)."""
    a = _excitation_recursion(event_times, beta)
    n = len(event_times)
    event_times_arr = np.array(event_times, dtype=float)

    compensator = mu * t_end + (alpha / beta) * float(
        np.sum(1.0 - np.exp(-beta * (t_end - event_times_arr)))
    )
    log_intensity_sum = float(np.sum(np.log(mu + alpha * a))) if n else 0.0
    return -(log_intensity_sum - compensator)


def intensity_at(mu: float, alpha: float, beta: float, event_times: list[float], t: float) -> float:
    """lambda(t) = mu + alpha * sum_{t_i < t} exp(-beta*(t - t_i))
    (section 1.1) — used both for lambda_0 (section 2.5, the starting
    intensity a forward simulation picks up from) and internally by the
    MLE's A_i recursion (via the excitation-at-a-point special case)."""
    past = [ti for ti in event_times if ti < t]
    if not past:
        return mu
    past_arr = np.array(past, dtype=float)
    return mu + alpha * float(np.sum(np.exp(-beta * (t - past_arr))))


def fit_hawkes_mle(event_times: list[float], t_end: float) -> HawkesFit:
    """Maximum-likelihood fit of (mu, alpha, beta) given detected event
    times over a window [0, t_end]. Reparametrized to the branching ratio
    n=alpha/beta (section 2.3) so bounding n in [0, 0.95] makes the
    stability condition n<1 impossible to violate by construction, rather
    than something checked and rejected after the fact. Callers below
    MIN_EVENTS_FOR_MLE should use literature-typical defaults instead of
    calling this — it will converge to *something* even on too little
    data, just not to anything trustworthy (section 2.4)."""
    if t_end <= 0:
        raise ValueError("t_end must be positive")
    n_events = len(event_times)
    if n_events == 0:
        raise ValueError("fit_hawkes_mle requires at least one event")

    def objective(x: np.ndarray) -> float:
        mu, n, beta = x
        alpha = n * beta
        return _neg_log_likelihood(mu, alpha, beta, event_times, t_end)

    # A reasonable, scale-free starting point: baseline rate from the
    # empirical average event rate, moderate clustering, decay over a
    # timescale comparable to the mean inter-event gap.
    mean_rate = n_events / t_end
    mean_gap = t_end / n_events if n_events > 0 else 1.0
    x0 = np.array([max(mean_rate * 0.7, 1e-3), 0.3, max(1.0 / mean_gap, 1e-3)])

    result = minimize(
        objective,
        x0,
        method="L-BFGS-B",
        bounds=[_MU_BOUNDS, _N_BOUNDS, _BETA_BOUNDS],
    )
    mu, n, beta = result.x
    alpha = n * beta
    log_likelihood = -_neg_log_likelihood(mu, alpha, beta, event_times, t_end)
    return HawkesFit(
        mu=float(mu),
        alpha=float(alpha),
        beta=float(beta),
        branching_ratio=float(n),
        log_likelihood=float(log_likelihood),
        n_events=n_events,
    )


def simulate_hawkes_events(
    mu: float,
    alpha: float,
    beta: float,
    lambda0: float,
    horizon: float,
    rng: np.random.Generator,
) -> list[float]:
    """Ogata's thinning algorithm (section 3.2) — the standard, exact
    method for simulating a point process from its intensity, specialized
    here to the exponential kernel's Markov structure so no per-instant
    scan is needed. `lambda0` is the intensity the process starts at
    (section 2.5's lambda_0 when continuing from real history mid-
    aftershock, or plain `mu` for a cold start / a synthetic test)."""
    if horizon <= 0:
        return []

    t = 0.0
    lambda_bar = max(lambda0, mu)
    jump_times: list[float] = []
    while t < horizon:
        u = rng.uniform()
        t_candidate = t - math.log(u) / lambda_bar
        if t_candidate >= horizon:
            break
        lambda_at_candidate = mu + (lambda_bar - mu) * math.exp(-beta * (t_candidate - t))
        if rng.uniform() <= lambda_at_candidate / lambda_bar:
            jump_times.append(t_candidate)
            lambda_bar = lambda_at_candidate + alpha
        else:
            lambda_bar = lambda_at_candidate
        t = t_candidate
    return jump_times
