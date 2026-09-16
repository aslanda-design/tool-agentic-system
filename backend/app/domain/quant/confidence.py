"""Confidence-band utilities shared by every model, SDE-based or
residual-bootstrap-based alike — one `confidence_level` ParamSpec, one
conversion from the param form's integer percentage to a fraction, and one
percentile computation, so "what does the shaded band mean" is answered
identically everywhere instead of drifting per model."""

from __future__ import annotations

import numpy as np

from app.domain.quant.types import ParamSpec

DEFAULT_CONFIDENCE_LEVEL = 0.9

CONFIDENCE_LEVEL_PARAM_SPEC = ParamSpec(
    key="confidence_level",
    label="Confidence band (%)",
    kind="int",
    default=int(DEFAULT_CONFIDENCE_LEVEL * 100),
    min=50,
    max=99,
    help="Width of the shaded prediction band — e.g. 90 means 90% of simulated paths fall inside it.",
)


def confidence_level_from_params(params: dict[str, float]) -> float:
    """Every model's `confidence_level` ParamSpec is an integer percentage
    (50-99, e.g. 90) so the generic slider-driven param form
    (frontend/.../ModelParamForm) needs no special-casing for this one
    field — this is the one place that converts it to the 0-1 fraction
    `confidence_band_percentiles` expects."""
    pct = params.get("confidence_level", DEFAULT_CONFIDENCE_LEVEL * 100)
    return min(max(float(pct) / 100, 0.01), 0.99)


def confidence_band_percentiles(paths, confidence_level: float) -> dict[str, list[float]]:
    """{"lower": [...], "median": [...], "upper": [...]} from an n_paths x
    horizon_days array — e.g. confidence_level=0.9 -> the 5th/50th/95th
    percentiles. The one place every model computes its percentile output,
    so `SimulationResult.percentiles` has the same three keys regardless of
    which model produced it (see plans/quant_lab.md's original sketch,
    which fixed a p5/p25/p50/p75/p95 five-line fan — a single
    user-adjustable band turned out to be both simpler to reason about and
    what was actually wanted)."""
    lower_q = (1 - confidence_level) / 2 * 100
    upper_q = 100 - lower_q
    return {
        "lower": np.percentile(paths, lower_q, axis=0).tolist(),
        "median": np.percentile(paths, 50, axis=0).tolist(),
        "upper": np.percentile(paths, upper_q, axis=0).tolist(),
    }
