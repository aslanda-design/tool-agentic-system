"""Domain types for the security resolver — deciding which market-data
listing (a Yahoo Finance symbol) prices a given asset. Pure Python, no I/O.
See plans/agentic_asset_mapping.md for the full design; domain/exchanges.py
for the exchange-code lookups these build on; domain/listing_scoring.py for
the pure scoring functions that operate on `Candidate`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum


class ResolutionStatus(StrEnum):
    AUTO_ACCEPTED = "AUTO_ACCEPTED"  # rules picked a listing and applied it
    NEEDS_AGENT = "NEEDS_AGENT"  # rules found plausible candidates but can't decide
    NEEDS_REVIEW = "NEEDS_REVIEW"  # needs a human: no candidates, conflict, or the agent flagged/failed
    RESOLVED_BY_AGENT = "RESOLVED_BY_AGENT"  # agent saved a candidate
    RESOLVED_BY_USER = "RESOLVED_BY_USER"  # user picked or typed a symbol
    SUPERSEDED = "SUPERSEDED"  # replaced by a newer resolution for the same asset

    @property
    def is_terminal(self) -> bool:
        return self in (
            ResolutionStatus.AUTO_ACCEPTED,
            ResolutionStatus.RESOLVED_BY_AGENT,
            ResolutionStatus.RESOLVED_BY_USER,
            ResolutionStatus.SUPERSEDED,
        )


@dataclass(slots=True)
class ResolutionContext:
    """Everything known about an asset at the moment we try to resolve it —
    built fresh from DB state each time (see application/resolve_security.py
    `_build_context`), never persisted as-is (it's flattened onto
    `asset_resolutions` by the repo)."""

    asset_id: int
    isin: str | None
    broker_symbol: str
    broker_name: str
    broker_exchange: str | None  # raw broker code, e.g. IBKR 'IBIS2'
    broker_mic: str | None  # broker_exchange translated via domain/exchanges.py
    currency: str
    broker_key: str | None


@dataclass(slots=True)
class FigiListing:
    """One `data` element from an OpenFIGI /v3/mapping response."""

    ticker: str
    exch_code: str  # Bloomberg exchange code, e.g. 'LN', 'GY'
    name: str
    security_type: str  # 'ETP', 'Common Stock', 'Mutual Fund', ...
    share_class_figi: str | None


@dataclass(slots=True)
class ListingInfo:
    """What Yahoo Finance says about one symbol right now (application/
    ports/market_data.py::MarketDataPort.get_listing_info)."""

    symbol: str
    name: str
    currency: str | None  # ISO, already normalized (e.g. GBp -> GBP)
    quote_type: str | None  # 'ETF', 'EQUITY', 'MUTUALFUND'
    last_close: Decimal | None  # in `currency` major units
    last_trade_date: date | None
    avg_volume: Decimal | None


def resolved_display_name(info: ListingInfo | None, symbol: str) -> str | None:
    """The asset's new display name once `symbol` is applied as its
    listing — Yahoo's own name for it, preferred over whatever the broker
    called it (often an ISIN or an internal code). yfinance_adapter's
    get_listing_info falls back to the bare symbol when Yahoo's `.info`
    lookup fails, so `info.name == symbol` means "no real name available";
    callers pass None through to AssetRepo.apply_listing in that case,
    which leaves the asset's existing name untouched rather than
    replacing it with something less readable than what it already had."""
    if info is None or not info.name or info.name == symbol:
        return None
    return info.name


@dataclass(slots=True)
class Candidate:
    """One candidate listing for a resolution, from generation through
    scoring through persistence. `id` is set once the candidate has been
    written to `resolution_candidates` (None until then)."""

    symbol: str
    found_by: set[str] = field(default_factory=set)  # 'openfigi' | 'yahoo_isin' | 'yahoo_text' | 'agent' | 'user'
    info: ListingInfo | None = None
    mic: str | None = None
    figi: FigiListing | None = None
    asset_class: str | None = None  # AssetClass.value, inferred from the FIGI/Yahoo security type
    features: dict[str, float | bool | int] = field(default_factory=dict)
    score: int = 0
    id: int | None = None


@dataclass(slots=True)
class AgentRunRecord:
    """One local-LLM agent invocation, persisted to `agent_runs` for audit
    and for the evaluation harness (Phase 6)."""

    resolution_id: int | None
    agent: str
    model: str
    status: str  # 'SAVED' | 'FLAGGED' | 'MAX_STEPS' | 'TIMEOUT' | 'ERROR'
    steps: int
    tool_calls: list[dict]
    final_message: str
    prompt_tokens: int
    completion_tokens: int
    duration_ms: int
    error: str | None = None


@dataclass(slots=True)
class ResolutionDTO:
    """A resolution plus its candidates, as returned by ResolutionRepo.get()
    / create() and by the REST/MCP layers."""

    id: int
    asset_id: int
    context: ResolutionContext
    status: ResolutionStatus
    decided_by: str | None
    note: str
    scorer_version: str
    candidates: list[Candidate]
    selected_candidate_id: int | None
