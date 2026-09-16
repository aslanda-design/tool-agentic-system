"""Domain types for the Quant Lab (see plans/quant_lab.md). Pure Python, no
I/O — the one thing worth stating here is the numpy/float boundary: this
package (and only this package) works in plain `float`, not `Decimal`. It
converts at its own edges (see registry.py's callers in
application/run_quant_simulation.py) — nothing outside `domain/quant/`
should ever see a bare float price."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Protocol


@dataclass(slots=True)
class ParamSpec:
    """One knob on a model's calibration/simulation form — drives both the
    frontend's dynamically-rendered param form and an MCP tool's schema
    (via ai.common.jsonable), so a new model needs no UI/MCP change."""

    key: str
    label: str
    kind: Literal["float", "int", "bool", "enum"]
    default: float | int | bool | str
    min: float | None = None
    max: float | None = None
    choices: list[str] | None = None
    help: str = ""


@dataclass(slots=True)
class ModelMetadata:
    key: str
    display_name: str
    description: str
    family: Literal["regression", "time_series", "stochastic_process"]
    min_history_days: int
    supports_calibration: bool
    param_specs: list[ParamSpec] = field(default_factory=list)


@dataclass(slots=True)
class CalibrationResult:
    params: dict[str, float]
    diagnostics: dict[str, float]
    # In-sample residuals (log-return space) — the raw material
    # bootstrap.py's Monte Carlo engine resamples from.
    residuals: list[float]
    # The trailing window of log-returns a bootstrap-based model needs to
    # seed simulate()'s first forecast step (e.g. the last 20 returns for
    # linear_regression's lag/rolling features) — empty for a model that
    # doesn't need one (e.g. a future closed-form GBM model only needs
    # mu/sigma, already in `params`).
    last_window: list[float] = field(default_factory=list)


@dataclass(slots=True)
class SimulationResult:
    dates: list[date]
    paths: list[list[float]]  # n_paths x horizon_days, price LEVELS
    # {"lower": [...], "median": [...], "upper": [...]} — the confidence
    # band width behind "lower"/"upper" is user-configurable per model (see
    # each model's `confidence_level` ParamSpec), not fixed.
    percentiles: dict[str, list[float]]


@dataclass(slots=True)
class BacktestResult:
    covered_days: int
    within_band: int
    mean_abs_pct_error_median: float | None


@dataclass(slots=True)
class ModelRecommendation:
    """One entry from domain.quant.recommendation.recommend_model — a
    deterministic, non-LLM heuristic (see plans/quant_lab.md section 6.2),
    same "rules first" posture the security resolver already established."""

    model_key: str
    score: float
    reason: str


class QuantModel(Protocol):
    metadata: ModelMetadata

    def calibrate(self, closes: list[float], params: dict[str, float]) -> CalibrationResult: ...

    def simulate(
        self,
        last_price: float,
        as_of: date,
        calibration: CalibrationResult,
        horizon_days: int,
        n_paths: int,
        seed: int,
        params: dict[str, float],
    ) -> SimulationResult: ...


@dataclass(slots=True)
class QuantRunRecord:
    """The persisted shape of a quant_runs row — a recipe (asset, model,
    split date, params, seed) plus a small percentile summary, deliberately
    WITHOUT raw paths (see plans/quant_lab.md section 0.2, decision 4).
    RunQuantSimulationUseCase.replay() recomputes paths from this record's
    recipe rather than ever storing them."""

    id: int
    asset_id: int
    model_key: str
    split_date: date
    horizon_days: int
    n_paths: int
    seed: int
    params: dict
    calibration_params: dict
    calibration_diagnostics: dict
    percentiles: dict[str, list[float]]
    backtest: BacktestResult | None
    created_by: str
    note: str
    created_at: datetime


PointForecastFn = Callable[[list[float]], float]
"""A one-step-ahead log-return forecast given a rolling window of recent
log-returns — what simulate_by_residual_bootstrap steps forward."""
