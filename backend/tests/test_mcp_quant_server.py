"""Tests for the `quant` MCP server (see plans/quant_lab.md section 8).
Same shape as test_mcp_analytics_server.py — the underlying math is
already covered by test_run_quant_simulation.py/test_quant_recommendation.py,
so these focus on the tools being correctly wired, read-only, and
JSON-safe."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal

import numpy as np

from ai.mcp_servers.quant import server
from ai.mcp_servers.quant.tools import explain_run as explain_run_tool
from ai.mcp_servers.quant.tools import list_models as list_models_tool
from ai.mcp_servers.quant.tools import recommend_model as recommend_model_tool
from app.application.backfill_quant_history import BackfillQuantHistoryUseCase
from app.application.run_quant_simulation import RunQuantSimulationUseCase
from app.domain.models import AssetClass
from tests.fakes import FakeAssetRepo, FakeMarketDataRepo, FakeQuantRunRepo


class _NullBarSource:
    def get_daily_history(self, symbol, mic, start, end, expected_currency=None):
        return None


def _trading_dates(start: date, n: int) -> list[date]:
    dates: list[date] = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def _seeded_use_case(n_days: int = 650, seed: int = 0):
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
    quant_run_repo = FakeQuantRunRepo()
    backfill = BackfillQuantHistoryUseCase(asset_repo, market_data_repo, _NullBarSource())
    use_case = RunQuantSimulationUseCase(asset_repo, market_data_repo, quant_run_repo, backfill)
    return use_case, asset.id, dates


def test_list_models_returns_all_models(monkeypatch):
    use_case, _asset_id, _dates = _seeded_use_case()
    monkeypatch.setattr(
        list_models_tool, "build_run_quant_simulation_use_case", lambda db: use_case
    )

    result = list_models_tool.list_models()

    assert {m["key"] for m in result} == {
        "linear_regression",
        "time_series_ar",
        "black_scholes_gbm",
        "heston",
        "hawkes_jump_diffusion",
        "rough_heston",
    }


def test_recommend_model_returns_a_ranked_list(monkeypatch):
    use_case, asset_id, _dates = _seeded_use_case()
    monkeypatch.setattr(
        recommend_model_tool, "build_run_quant_simulation_use_case", lambda db: use_case
    )

    result = recommend_model_tool.recommend_model(asset_id)

    assert isinstance(result, list)
    assert result  # some ranked recommendation for a 650-day series


def test_recommend_model_empty_for_unknown_asset(monkeypatch):
    use_case, _asset_id, _dates = _seeded_use_case()
    monkeypatch.setattr(
        recommend_model_tool, "build_run_quant_simulation_use_case", lambda db: use_case
    )

    assert recommend_model_tool.recommend_model(999999) == []


def test_explain_run_returns_summary_without_paths(monkeypatch):
    use_case, asset_id, dates = _seeded_use_case()
    monkeypatch.setattr(
        explain_run_tool, "build_run_quant_simulation_use_case", lambda db: use_case
    )
    created = use_case.run(asset_id, "linear_regression", dates[600], 10, 15, 1, {}, "user")

    result = explain_run_tool.explain_run(created.id)

    assert result["model_key"] == "linear_regression"
    assert "paths" not in result
    assert result["calibration_params"]


def test_explain_run_unknown_id_returns_error_dict(monkeypatch):
    use_case, _asset_id, _dates = _seeded_use_case()
    monkeypatch.setattr(
        explain_run_tool, "build_run_quant_simulation_use_case", lambda db: use_case
    )

    result = explain_run_tool.explain_run(999999)

    assert "error" in result


def test_server_registers_all_three_tools():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == {"list_models", "recommend_model", "explain_run"}


def test_server_call_tool_round_trips_through_the_registered_function(monkeypatch):
    use_case, _asset_id, _dates = _seeded_use_case()
    monkeypatch.setattr(
        list_models_tool, "build_run_quant_simulation_use_case", lambda db: use_case
    )

    result = asyncio.run(server.mcp.call_tool("list_models", {}))

    assert result.is_error is False
    # A bare list[dict] return comes back wrapped as {"result": [...]} in
    # structured_content — ai.common.mcp_client._unwrap undoes this for the
    # real agent loop; this test checks the same shape directly.
    models = result.structured_content["result"]
    assert {m["key"] for m in models} == {
        "linear_regression",
        "time_series_ar",
        "black_scholes_gbm",
        "heston",
        "hawkes_jump_diffusion",
        "rough_heston",
    }
