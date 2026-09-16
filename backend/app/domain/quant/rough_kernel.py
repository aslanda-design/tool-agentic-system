"""Fractional-kernel approximation machinery for rough Heston — see
plans/quant_lab_phase9_rough_heston.md sections 2.2 and 5 for the full
derivation and the worked example. Deliberately knows nothing about
prices, variance, or calibration: a Hurst parameter and a time grid in, a
finite sum-of-exponentials approximation out — the same separation of
concerns hawkes.py already established for its own point-process
primitives, kept apart from the price-model wrapper (models/rough_heston.py).

The fractional kernel K(t) = t^(H-1/2) / Gamma(H+1/2) is a completely
monotone function, exactly representable as a Laplace-transform mixture of
ordinary exponentials (section 5.1). fit_kernel_quadrature discretizes
that mixture into a finite sum via non-negative least squares over a
log-spaced collocation grid — a standard, published technique (Carmona,
Coutin & Montseny 2000, "Approximation of some Gaussian processes"; Abi
Jaber 2019, "Lifting the Heston model"; Abi Jaber & El Euch 2019,
"Multi-factor approximation of rough volatility models"; Bayer & Breneis
2021, "Markovian approximations of stochastic Volterra equations with the
fractional kernel"), not an ad hoc curve fit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import nnls

# Collocation points spanning [dt, horizon_years] the quadrature is fit
# against — dense enough to resolve the kernel's near-singular behavior at
# small t without needing to be horizon- or H-dependent itself.
N_COLLOCATION_POINTS = 100

# The slowest decay rate offered to the fit, as a fraction of 1/horizon_years
# rather than 1/horizon_years itself. The true kernel decays only as a slow
# power law (t^(H-1/2)) and never becomes negligible within any finite
# horizon — representing that residual "long memory" well (especially for
# H close to 0.5, where the kernel is nearly flat across the whole window)
# needs decay rates well below 1/horizon_years, not just at it. Verified
# empirically: at gamma_min = 1/horizon_years exactly, the fit plateaus at
# a rel_error that does NOT improve with more factors (the achievable
# subspace simply can't represent a near-constant target), and gets worse
# as H -> 0.5; at gamma_min = 0.01/horizon_years, error shrinks with more
# factors as expected across the whole H range and is small even at
# H=0.49 (see test_rough_kernel.py).
GAMMA_MIN_SCALE = 0.01


@dataclass(slots=True)
class KernelQuadrature:
    gammas: np.ndarray  # (n_factors,) decay rates, geometrically spaced
    weights: np.ndarray  # (n_factors,) non-negative weights c_i (nnls contract)
    rel_error: float  # relative L2 fit error over the collocation grid — section 5.2


def fractional_kernel(t: np.ndarray | float, h: float) -> np.ndarray:
    """K(t) = t^(H-1/2) / Gamma(H+1/2) — the Riemann-Liouville fractional
    kernel defining the Volterra variance process (El Euch & Rosenbaum
    2019). H in (0, 0.5) is what makes the model "rough" (section 2);
    H=0.5 collapses this to the constant kernel 1 (section 2.4)."""
    return np.asarray(t, dtype=float) ** (h - 0.5) / math.gamma(h + 0.5)


def fit_kernel_quadrature(
    h: float, dt: float, horizon_years: float, n_factors: int
) -> KernelQuadrature:
    """Approximate fractional_kernel(., h) by sum(c_i * exp(-gamma_i * t))
    via non-negative least squares over a log-spaced collocation grid
    spanning [dt, horizon_years] (section 5.2). Non-negative because
    section 5.1's Laplace-mixture representation guarantees the true
    weights form a genuine density, never signed — using plain
    unconstrained least squares here could produce a destabilizing
    (negative-weight) factor unfaithful to what's being approximated.

    Recomputed fresh on every simulate() call, never cached from
    calibrate() — the gamma grid depends on horizon_years, a
    simulate()-time input calibrate() never sees (section 6.3)."""
    if n_factors < 1:
        raise ValueError("n_factors must be at least 1")
    if dt <= 0 or horizon_years <= 0:
        raise ValueError("dt and horizon_years must be positive")
    if horizon_years <= dt:
        # A degenerate very-short horizon — widen the slow end so
        # geomspace still spans a valid (if narrow) range.
        horizon_years = dt * 2.0

    gamma_min = GAMMA_MIN_SCALE / horizon_years  # see GAMMA_MIN_SCALE's docstring above
    gamma_max = 1.0 / dt  # fastest timescale worth resolving: one simulated step
    gammas = np.geomspace(gamma_min, gamma_max, n_factors)

    collocation_t = np.geomspace(dt, horizon_years, N_COLLOCATION_POINTS)
    target = fractional_kernel(collocation_t, h)
    design = np.exp(-np.outer(collocation_t, gammas))  # (M, n_factors)

    weights, _residual_norm = nnls(design, target)
    approx = design @ weights
    target_norm = float(np.linalg.norm(target))
    rel_error = float(np.linalg.norm(approx - target) / target_norm) if target_norm > 0 else 0.0

    return KernelQuadrature(gammas=gammas, weights=weights, rel_error=rel_error)
