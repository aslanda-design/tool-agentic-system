"""The Quant Lab's orchestrating use case — see plans/quant_lab.md sections
5 and 0.2 (decision 4). Loads persisted bars, calibrates + simulates the
chosen model, compares the ensemble against real holdout data when it
exists, and persists a run's RECIPE + summary (never raw paths — `replay()`
recomputes them deterministically from the recipe, since calibration only
ever reads bars already persisted for a fixed date range and the RNG is
seeded)."""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime
from decimal import Decimal

from app.application.backfill_quant_history import BackfillQuantHistoryUseCase
from app.application.dto import (
    ModelRecommendationDTO,
    ParamSpecDTO,
    QuantBacktestDTO,
    QuantModelDTO,
    QuantRunDTO,
    QuantRunExplanationDTO,
    QuantRunSummaryDTO,
)
from app.domain.errors import (
    DomainError,
    InsufficientQuantHistoryError,
    QuantModelNotFoundError,
    QuantRunNotFoundError,
)
from app.domain.quant import (
    models as _quant_models,  # noqa: F401 - populates the registry on import
)
from app.domain.quant.recommendation import recommend_model
from app.domain.quant.registry import get as get_model
from app.domain.quant.registry import list_models
from app.domain.quant.types import BacktestResult, ModelMetadata, QuantModel, QuantRunRecord
from app.ports.repositories import AssetRepo, MarketDataRepo, QuantRunRepo

logger = logging.getLogger(__name__)

MAX_N_PATHS = 5000
MAX_HORIZON_DAYS = 252
# Well before any realistic listing start — get_bars is a plain range
# filter, so an "all persisted history" query still needs a lower bound.
EARLIEST_POSSIBLE_DATE = date(1970, 1, 1)


def _dec(value: float) -> Decimal:
    return Decimal(str(value))


def _backtest_dto(backtest: BacktestResult | None) -> QuantBacktestDTO | None:
    if backtest is None:
        return None
    return QuantBacktestDTO(
        covered_days=backtest.covered_days,
        within_band=backtest.within_band,
        mean_abs_pct_error_median=backtest.mean_abs_pct_error_median,
    )


def _model_to_dto(meta: ModelMetadata) -> QuantModelDTO:
    return QuantModelDTO(
        key=meta.key,
        display_name=meta.display_name,
        description=meta.description,
        family=meta.family,
        min_history_days=meta.min_history_days,
        supports_calibration=meta.supports_calibration,
        param_specs=[
            ParamSpecDTO(
                key=p.key,
                label=p.label,
                kind=p.kind,
                default=p.default,
                min=p.min,
                max=p.max,
                choices=p.choices,
                help=p.help,
            )
            for p in meta.param_specs
        ],
    )


def _compare_to_actual(
    dates: list[date], percentiles: dict[str, list[float]], after_bars: list[dict]
) -> BacktestResult | None:
    if not after_bars:
        return None
    actual_by_date = {b["date"]: float(b["close"]) for b in after_bars}
    covered = 0
    within_band = 0
    abs_pct_errors: list[float] = []
    for i, d in enumerate(dates):
        actual = actual_by_date.get(d)
        if actual is None:
            continue
        covered += 1
        if percentiles["lower"][i] <= actual <= percentiles["upper"][i]:
            within_band += 1
        if actual != 0:
            abs_pct_errors.append(abs(actual - percentiles["median"][i]) / abs(actual))
    if covered == 0:
        return None
    mean_abs = sum(abs_pct_errors) / len(abs_pct_errors) if abs_pct_errors else None
    return BacktestResult(covered_days=covered, within_band=within_band, mean_abs_pct_error_median=mean_abs)


class RunQuantSimulationUseCase:
    def __init__(
        self,
        asset_repo: AssetRepo,
        market_data_repo: MarketDataRepo,
        quant_run_repo: QuantRunRepo,
        backfill_use_case: BackfillQuantHistoryUseCase,
    ) -> None:
        self.asset_repo = asset_repo
        self.market_data_repo = market_data_repo
        self.quant_run_repo = quant_run_repo
        self.backfill_use_case = backfill_use_case

    def list_available_models(self) -> list[QuantModelDTO]:
        return [_model_to_dto(m) for m in list_models()]

    def recommend(self, asset_id: int) -> list[ModelRecommendationDTO]:
        bars = self.market_data_repo.get_bars(asset_id, EARLIEST_POSSIBLE_DATE, date.today())
        closes = [float(b["close"]) for b in sorted(bars, key=lambda b: b["date"])]
        return [
            ModelRecommendationDTO(model_key=r.model_key, score=r.score, reason=r.reason)
            for r in recommend_model(closes)
        ]

    def run(
        self,
        asset_id: int,
        model_key: str,
        split_date: date,
        horizon_days: int,
        n_paths: int,
        seed: int,
        params: dict,
        created_by: str,
    ) -> QuantRunDTO:
        logger.info(
            "quant run: request — asset_id=%s model=%s split_date=%s horizon_days=%s "
            "n_paths=%s seed=%s created_by=%s",
            asset_id, model_key, split_date, horizon_days, n_paths, seed, created_by,
        )
        model = self._get_model(model_key)
        self._validate_caps(horizon_days, n_paths)

        self.backfill_use_case.ensure_history(asset_id, model.metadata.min_history_days)
        logger.debug("quant run: history backfill check done — asset_id=%s", asset_id)

        before, after = self._load_and_split(asset_id, split_date)
        logger.info(
            "quant run: loaded %d bar(s) before %s, %d after — asset_id=%s",
            len(before), split_date, len(after), asset_id,
        )
        if len(before) < model.metadata.min_history_days:
            logger.warning(
                "quant run: insufficient history — model=%s needs %d daily bars, has %d "
                "(asset_id=%s, split_date=%s)",
                model_key, model.metadata.min_history_days, len(before), asset_id, split_date,
            )
            raise InsufficientQuantHistoryError(
                f"Not enough history before {split_date} to calibrate {model_key!r} "
                f"(need {model.metadata.min_history_days} daily bars, have {len(before)})"
            )

        closes = [float(b["close"]) for b in before]
        t0 = time.monotonic()
        calibration = model.calibrate(closes, params)
        logger.info(
            "quant run: %s calibrated in %.3fs — diagnostics=%s",
            model_key, time.monotonic() - t0, calibration.diagnostics,
        )

        t0 = time.monotonic()
        sim = model.simulate(
            closes[-1], split_date, calibration, horizon_days, n_paths, seed, params
        )
        logger.info(
            "quant run: %s simulated %d path(s) x %d day(s) in %.3fs",
            model_key, n_paths, horizon_days, time.monotonic() - t0,
        )
        backtest = _compare_to_actual(sim.dates, sim.percentiles, after)
        if backtest is not None:
            logger.info(
                "quant run: backtest — %d/%d day(s) within band, median abs error=%s",
                backtest.within_band, backtest.covered_days, backtest.mean_abs_pct_error_median,
            )
        else:
            logger.info("quant run: no holdout data after %s to backtest against", split_date)

        record = QuantRunRecord(
            id=0,
            asset_id=asset_id,
            model_key=model_key,
            split_date=split_date,
            horizon_days=horizon_days,
            n_paths=n_paths,
            seed=seed,
            params=dict(params),
            calibration_params=dict(calibration.params),
            calibration_diagnostics=dict(calibration.diagnostics),
            percentiles=dict(sim.percentiles),
            backtest=backtest,
            created_by=created_by,
            note="",
            created_at=datetime.now(UTC),
        )
        run_id = self.quant_run_repo.create(record)
        logger.info("quant run: persisted as run_id=%s", run_id)
        return self._to_run_dto(run_id, record, sim.dates, sim.paths)

    def replay(self, run_id: int) -> QuantRunDTO:
        """Recompute the exact same paths from a stored run's recipe —
        exact because calibration only reads bars already persisted for a
        fixed date range and the RNG is seeded."""
        logger.info("quant replay: request — run_id=%s", run_id)
        record = self.quant_run_repo.get(run_id)
        if record is None:
            logger.warning("quant replay: run_id=%s not found", run_id)
            raise QuantRunNotFoundError(f"Quant run {run_id} not found")
        model = self._get_model(record.model_key)

        before, _after = self._load_and_split(record.asset_id, record.split_date)
        if len(before) < model.metadata.min_history_days:
            logger.warning(
                "quant replay: insufficient history to replay run_id=%s (%s needs %d daily "
                "bars, has %d)",
                run_id, record.model_key, model.metadata.min_history_days, len(before),
            )
            raise InsufficientQuantHistoryError(
                f"Not enough persisted history left before {record.split_date} to replay run {run_id} "
                "(bars may have been deleted/remapped since)"
            )
        closes = [float(b["close"]) for b in before]
        t0 = time.monotonic()
        calibration = model.calibrate(closes, record.params)
        sim = model.simulate(
            closes[-1],
            record.split_date,
            calibration,
            record.horizon_days,
            record.n_paths,
            record.seed,
            record.params,
        )
        logger.info(
            "quant replay: run_id=%s (%s) recomputed in %.3fs",
            run_id, record.model_key, time.monotonic() - t0,
        )
        return self._to_run_dto(run_id, record, sim.dates, sim.paths)

    def list_for_asset(self, asset_id: int, limit: int = 20) -> list[QuantRunSummaryDTO]:
        return [
            self._to_summary_dto(r) for r in self.quant_run_repo.list_for_asset(asset_id, limit)
        ]

    def explain(self, run_id: int) -> QuantRunExplanationDTO | None:
        """A stored run's recipe + summary, straight from the DB — no
        recompute (unlike replay()), since calibration_params/diagnostics/
        percentiles/backtest are already fully persisted. Used by the
        `quant` MCP server's explain_run tool (read-only, per
        plans/quant_lab.md section 0.2)."""
        record = self.quant_run_repo.get(run_id)
        if record is None:
            return None
        return QuantRunExplanationDTO(
            id=record.id,
            asset_id=record.asset_id,
            model_key=record.model_key,
            split_date=record.split_date,
            horizon_days=record.horizon_days,
            n_paths=record.n_paths,
            params=record.params,
            calibration_params=record.calibration_params,
            calibration_diagnostics=record.calibration_diagnostics,
            percentiles={k: [_dec(v) for v in vals] for k, vals in record.percentiles.items()},
            backtest=_backtest_dto(record.backtest),
            created_by=record.created_by,
            note=record.note,
            created_at=record.created_at,
        )

    def _get_model(self, model_key: str) -> QuantModel:
        model = get_model(model_key)
        if model is None:
            logger.warning("quant: unknown model_key=%r", model_key)
            raise QuantModelNotFoundError(f"Unknown quant model: {model_key!r}")
        return model

    def _validate_caps(self, horizon_days: int, n_paths: int) -> None:
        if not (1 <= horizon_days <= MAX_HORIZON_DAYS):
            logger.warning(
                "quant: horizon_days=%s outside allowed range [1, %d]", horizon_days, MAX_HORIZON_DAYS
            )
            raise DomainError(f"horizon_days must be between 1 and {MAX_HORIZON_DAYS}")
        if not (1 <= n_paths <= MAX_N_PATHS):
            logger.warning("quant: n_paths=%s outside allowed range [1, %d]", n_paths, MAX_N_PATHS)
            raise DomainError(f"n_paths must be between 1 and {MAX_N_PATHS}")

    def _load_and_split(self, asset_id: int, split_date: date) -> tuple[list[dict], list[dict]]:
        bars = sorted(
            self.market_data_repo.get_bars(asset_id, EARLIEST_POSSIBLE_DATE, date.today()),
            key=lambda b: b["date"],
        )
        before = [b for b in bars if b["date"] <= split_date]
        after = [b for b in bars if b["date"] > split_date]
        return before, after

    def _to_run_dto(
        self, run_id: int, record: QuantRunRecord, dates: list[date], paths: list[list[float]]
    ) -> QuantRunDTO:
        return QuantRunDTO(
            id=run_id,
            asset_id=record.asset_id,
            model_key=record.model_key,
            split_date=record.split_date,
            horizon_days=record.horizon_days,
            n_paths=record.n_paths,
            seed=record.seed,
            params=record.params,
            calibration_params=record.calibration_params,
            calibration_diagnostics=record.calibration_diagnostics,
            dates=dates,
            percentiles={k: [_dec(v) for v in vals] for k, vals in record.percentiles.items()},
            paths=[[_dec(v) for v in path] for path in paths],
            backtest=_backtest_dto(record.backtest),
            created_by=record.created_by,
            note=record.note,
            created_at=record.created_at,
        )

    def _to_summary_dto(self, record: QuantRunRecord) -> QuantRunSummaryDTO:
        return QuantRunSummaryDTO(
            id=record.id,
            asset_id=record.asset_id,
            model_key=record.model_key,
            split_date=record.split_date,
            horizon_days=record.horizon_days,
            n_paths=record.n_paths,
            created_by=record.created_by,
            created_at=record.created_at,
            backtest=_backtest_dto(record.backtest),
        )
