import numpy as np
import pytest

from app.domain.quant.rough_kernel import fit_kernel_quadrature, fractional_kernel

DT = 1.0 / 252.0
HORIZON_YEARS = 1.0


@pytest.mark.parametrize("h", [0.05, 0.1, 0.3, 0.45])
def test_kernel_fit_accuracy_is_small(h: float):
    quad = fit_kernel_quadrature(h, DT, HORIZON_YEARS, n_factors=20)
    assert quad.rel_error < 0.05


@pytest.mark.parametrize("h", [0.05, 0.1, 0.3, 0.45])
def test_kernel_fit_accuracy_improves_with_more_factors(h: float):
    errors = [
        fit_kernel_quadrature(h, DT, HORIZON_YEARS, n_factors=n).rel_error for n in (5, 10, 20, 40)
    ]
    # Not strictly monotone at every step (nnls, discrete collocation grid),
    # but more factors should meaningfully shrink the error overall —
    # section 4.3's convergence claim, checked empirically.
    assert errors[-1] < errors[0]
    assert errors[-1] < 0.02


def test_weights_are_non_negative():
    quad = fit_kernel_quadrature(0.1, DT, HORIZON_YEARS, n_factors=20)
    assert np.all(quad.weights >= 0.0)


def test_h_near_one_half_approaches_constant_kernel():
    """As H -> 0.5, the true kernel K(t) = t^(H-1/2)/Gamma(H+1/2) approaches
    the constant function 1 (section 2.4 — the built-in consistency check
    that this model collapses to plain Heston). The AGGREGATE fit
    (design @ weights), not individual c_i/gamma_i (section 5.3's
    conditioning caveat), should track that constant closely."""
    quad = fit_kernel_quadrature(0.499, DT, HORIZON_YEARS, n_factors=20)
    collocation_t = np.geomspace(DT, HORIZON_YEARS, 100)
    design = np.exp(-np.outer(collocation_t, quad.gammas))
    approx = design @ quad.weights
    assert approx == pytest.approx(np.ones_like(approx), abs=0.05)


def test_fractional_kernel_matches_formula_directly():
    t = np.array([0.01, 0.1, 1.0])
    h = 0.2
    import math

    expected = t ** (h - 0.5) / math.gamma(h + 0.5)
    assert fractional_kernel(t, h) == pytest.approx(expected)


def test_fractional_kernel_at_one_half_is_constant_one():
    t = np.array([0.01, 0.1, 1.0, 5.0])
    assert fractional_kernel(t, 0.5) == pytest.approx(np.ones_like(t))


def test_rejects_non_positive_inputs():
    with pytest.raises(ValueError):
        fit_kernel_quadrature(0.1, dt=0.0, horizon_years=1.0, n_factors=10)
    with pytest.raises(ValueError):
        fit_kernel_quadrature(0.1, dt=DT, horizon_years=1.0, n_factors=0)
