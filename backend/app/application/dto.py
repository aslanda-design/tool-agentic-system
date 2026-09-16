"""Data transfer objects returned by use cases. The API layer maps these to
Pydantic response schemas (api/schemas.py) — kept separate so the application
layer has no FastAPI/Pydantic dependency."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(slots=True)
class PositionDTO:
    asset_id: int
    symbol: str
    name: str
    account_id: int
    broker_key: str
    quantity: Decimal
    avg_cost_price: Decimal
    last_price: Decimal | None  # None when no quote/price bar exists yet (asset needs mapping)
    currency: str
    market_value: Decimal | None
    cost_basis: Decimal  # always computable — doesn't depend on market price
    unrealized_pnl: Decimal | None
    unrealized_pnl_pct: Decimal | None
    returns: dict[str, Decimal | None]  # {"1d": .., "1w": .., "1m": .., "ytd": .., "1y": ..}


@dataclass(slots=True)
class PortfolioSummaryDTO:
    currency: str
    market_value: Decimal
    net_invested: Decimal
    cash: Decimal
    unrealized_pnl: Decimal | None
    unrealized_pnl_pct: Decimal | None
    day_change: Decimal
    day_change_pct: Decimal | None
    unpriced_count: int  # positions with no market price yet — market_value/pnl above exclude them


@dataclass(slots=True)
class HistoryPointDTO:
    date: date
    market_value: Decimal
    cost_basis: Decimal  # the "invested" line — see build_snapshots.py's docstring


@dataclass(slots=True)
class AllocationSliceDTO:
    label: str
    market_value: Decimal
    weight: Decimal


@dataclass(slots=True)
class AssetDetailDTO:
    asset_id: int
    symbol: str
    name: str
    exchange: str | None
    currency: str
    asset_class: str
    isin: str | None
    last_price: Decimal | None
    prev_close: Decimal | None
    position: PositionDTO | None


@dataclass(slots=True)
class AssetSummaryDTO:
    """A lighter version of AssetDetailDTO with no position — for the
    market_data MCP server's get_asset tool (application/query_market_data.py),
    which an agent calls to look up an asset it doesn't hold context for
    already, not to check a holding it's already looking at."""

    asset_id: int
    symbol: str
    name: str
    exchange: str | None
    currency: str
    asset_class: str
    isin: str | None
    last_price: Decimal | None
    prev_close: Decimal | None


@dataclass(slots=True)
class PricePointDTO:
    date: date
    close: Decimal


@dataclass(slots=True)
class StalePositionDTO:
    asset_id: int
    symbol: str
    last_price_date: date | None


@dataclass(slots=True)
class UnmappedAssetDTO:
    asset_id: int
    symbol: str


@dataclass(slots=True)
class DataFreshnessDTO:
    as_of: date
    stale_positions: list[StalePositionDTO]
    unmapped_assets: list[UnmappedAssetDTO]


@dataclass(slots=True)
class ConcentrationHoldingDTO:
    symbol: str
    weight: Decimal


@dataclass(slots=True)
class ConcentrationDTO:
    holdings: list[ConcentrationHoldingDTO]
    hhi: Decimal


@dataclass(slots=True)
class SyncResultDTO:
    broker_key: str
    accounts_synced: int
    holdings_synced: int
    transactions_added: int


@dataclass(slots=True)
class ParamSpecDTO:
    key: str
    label: str
    kind: str
    default: float | int | bool | str
    min: float | None
    max: float | None
    choices: list[str] | None
    help: str


@dataclass(slots=True)
class QuantModelDTO:
    key: str
    display_name: str
    description: str
    family: str
    min_history_days: int
    supports_calibration: bool
    param_specs: list[ParamSpecDTO]


@dataclass(slots=True)
class ModelRecommendationDTO:
    model_key: str
    score: float
    reason: str


@dataclass(slots=True)
class QuantBacktestDTO:
    covered_days: int
    within_band: int
    mean_abs_pct_error_median: float | None


@dataclass(slots=True)
class QuantRunDTO:
    """A fresh simulate/replay response — includes the full path ensemble.
    See QuantRunSummaryDTO for the list-endpoint shape, which omits `paths`
    (see plans/quant_lab.md section 0.2, decision 4: runs are never
    persisted as raw path arrays — only recomputed on demand)."""

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
    dates: list[date]
    percentiles: dict[str, list[Decimal]]
    paths: list[list[Decimal]]
    backtest: QuantBacktestDTO | None
    created_by: str
    note: str
    created_at: datetime


@dataclass(slots=True)
class QuantRunExplanationDTO:
    """A stored run's recipe + summary, no paths — a pure DB read (no
    recompute needed, unlike QuantRunDTO from replay()). What the `quant`
    MCP server's explain_run tool returns to an agent, and what a UI
    "run details" panel that doesn't need the fan chart could use too."""

    id: int
    asset_id: int
    model_key: str
    split_date: date
    horizon_days: int
    n_paths: int
    params: dict
    calibration_params: dict
    calibration_diagnostics: dict
    percentiles: dict[str, list[Decimal]]
    backtest: QuantBacktestDTO | None
    created_by: str
    note: str
    created_at: datetime


@dataclass(slots=True)
class QuantRunSummaryDTO:
    id: int
    asset_id: int
    model_key: str
    split_date: date
    horizon_days: int
    n_paths: int
    created_by: str
    created_at: datetime
    backtest: QuantBacktestDTO | None


@dataclass(slots=True)
class ImportPreviewDTO:
    total_rows: int
    new_transactions: int
    duplicate_transactions: int  # rows whose id already exists on this account (already imported)
    invalid_rows: int  # rows with errors — never written, shown so the user can fix the source file
    skipped_rows: int  # rows deliberately excluded (rejected orders, out-of-scope cash movements)
    unresolved_symbols: list[str]
    unmapped_columns: list[
        str
    ]  # required fields the parser couldn't map to a header — non-empty blocks commit
    column_mapping: dict[str, str]  # field -> the header that won, for user verification
    detected_headers: list[str]
    file_format: str
    notices: list[str]  # file-level notes, e.g. "no type column — every row treated as BUY"
    rows: list[dict]
