from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest

from app.application.backfill_quant_history import BackfillQuantHistoryUseCase
from app.application.run_quant_simulation import MAX_N_PATHS, RunQuantSimulationUseCase
from app.domain.errors import (
    DomainError,
    InsufficientQuantHistoryError,
    QuantModelNotFoundError,
    QuantRunNotFoundError,
)
from app.domain.models import AssetClass
from tests.fakes import FakeAssetRepo, FakeMarketDataRepo, FakeQuantRunRepo


class _FakeBarSource:
    def get_daily_history(self, symbol, mic, start, end, expected_currency=None):
        return None  # never reached in these tests — enough history is pre-seeded


def _trading_dates(start: date, n: int) -> list[date]:
    dates: list[date] = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def _seed_asset_with_history(n_days: int = 650, seed: int = 0):
    rng = np.random.default_rng(seed)
    dates = _trading_dates(date(2023, 1, 2), n_days)
    log_returns = rng.normal(0.0003, 0.01, size=n_days - 1)
    prices = 100.0 * np.exp(np.cumsum(log_returns))
    closes = [100.0, *prices.tolist()]

    asset_repo = FakeAssetRepo()
    asset = asset_repo.create("TEST", "Test Asset", AssetClass.ETF, "EUR")
    market_data_repo = FakeMarketDataRepo()
    rows = [
        {
            "date": d,
            "open": Decimal(str(c)),
            "high": Decimal(str(c)),
            "low": Decimal(str(c)),
            "close": Decimal(str(c)),
            "adj_close": Decimal(str(c)),
            "volume": Decimal(1000),
        }
        for d, c in zip(dates, closes)
    ]
    market_data_repo.upsert_bars(asset.id, rows, source="test")
    return asset_repo, asset, market_data_repo, dates


def _use_case(asset_repo, market_data_repo, quant_run_repo=None) -> RunQuantSimulationUseCase:
    backfill = BackfillQuantHistoryUseCase(asset_repo, market_data_repo, _FakeBarSource())
    return RunQuantSimulationUseCase(
        asset_repo, market_data_repo, quant_run_repo or FakeQuantRunRepo(), backfill
    )


def test_run_produces_a_dto_with_expected_shape_and_backtest():
    asset_repo, asset, market_data_repo, dates = _seed_asset_with_history()
    split_date = dates[600]
    use_case = _use_case(asset_repo, market_data_repo)

    dto = use_case.run(
        asset.id,
        "linear_regression",
        split_date,
        horizon_days=10,
        n_paths=15,
        seed=1,
        params={},
        created_by="user",
    )

    assert dto.id == 1
    assert dto.model_key == "linear_regression"
    assert len(dto.paths) == 15
    assert all(len(p) == 10 for p in dto.paths)
    assert all(isinstance(v, Decimal) for path in dto.paths for v in path)
    assert dto.backtest is not None
    assert dto.backtest.covered_days > 0


def test_replay_reproduces_the_same_paths():
    asset_repo, asset, market_data_repo, dates = _seed_asset_with_history(seed=1)
    split_date = dates[600]
    quant_run_repo = FakeQuantRunRepo()
    use_case = _use_case(asset_repo, market_data_repo, quant_run_repo)

    original = use_case.run(
        asset.id,
        "time_series_ar",
        split_date,
        horizon_days=8,
        n_paths=12,
        seed=42,
        params={"order_p": 2},
        created_by="user",
    )
    replayed = use_case.replay(original.id)

    assert replayed.paths == original.paths
    assert replayed.percentiles == original.percentiles


def test_list_for_asset_returns_summaries_without_paths():
    asset_repo, asset, market_data_repo, dates = _seed_asset_with_history(seed=2)
    quant_run_repo = FakeQuantRunRepo()
    use_case = _use_case(asset_repo, market_data_repo, quant_run_repo)
    use_case.run(asset.id, "linear_regression", dates[600], 5, 10, 1, {}, "user")

    summaries = use_case.list_for_asset(asset.id)

    assert len(summaries) == 1
    assert summaries[0].model_key == "linear_regression"


def test_unknown_model_raises():
    asset_repo, asset, market_data_repo, dates = _seed_asset_with_history(seed=3)
    use_case = _use_case(asset_repo, market_data_repo)
    with pytest.raises(QuantModelNotFoundError):
        use_case.run(asset.id, "nonexistent_model", dates[600], 10, 15, 1, {}, "user")


def test_insufficient_history_raises():
    asset_repo, asset, market_data_repo, dates = _seed_asset_with_history(seed=4)
    use_case = _use_case(asset_repo, market_data_repo)
    with pytest.raises(InsufficientQuantHistoryError):
        use_case.run(asset.id, "linear_regression", dates[10], 10, 15, 1, {}, "user")


def test_caps_are_enforced():
    asset_repo, asset, market_data_repo, dates = _seed_asset_with_history(seed=5)
    use_case = _use_case(asset_repo, market_data_repo)
    with pytest.raises(DomainError):
        use_case.run(asset.id, "linear_regression", dates[600], 10, MAX_N_PATHS + 1, 1, {}, "user")
    with pytest.raises(DomainError):
        use_case.run(asset.id, "linear_regression", dates[600], 253, 15, 1, {}, "user")


def test_explain_returns_recipe_and_summary_without_recompute():
    asset_repo, asset, market_data_repo, dates = _seed_asset_with_history(seed=8)
    quant_run_repo = FakeQuantRunRepo()
    use_case = _use_case(asset_repo, market_data_repo, quant_run_repo)
    created = use_case.run(asset.id, "linear_regression", dates[600], 10, 15, 1, {}, "user")

    explanation = use_case.explain(created.id)

    assert explanation is not None
    assert explanation.model_key == "linear_regression"
    assert explanation.calibration_params
    assert explanation.percentiles["median"]


def test_explain_unknown_run_returns_none():
    asset_repo, _asset, market_data_repo, _dates = _seed_asset_with_history(seed=9)
    use_case = _use_case(asset_repo, market_data_repo)
    assert use_case.explain(999) is None


def test_replay_unknown_run_raises():
    asset_repo, _asset, market_data_repo, _dates = _seed_asset_with_history(seed=6)
    use_case = _use_case(asset_repo, market_data_repo)
    with pytest.raises(QuantRunNotFoundError):
        use_case.replay(999)


def test_list_available_models_and_recommend():
    asset_repo, asset, market_data_repo, _dates = _seed_asset_with_history(seed=7)
    use_case = _use_case(asset_repo, market_data_repo)

    models = use_case.list_available_models()
    assert {m.key for m in models} == {
        "linear_regression",
        "time_series_ar",
        "black_scholes_gbm",
        "heston",
        "hawkes_jump_diffusion",
        "rough_heston",
    }

    recommendations = use_case.recommend(asset.id)
    assert recommendations  # some ranked recommendation, doesn't matter which for this asset
