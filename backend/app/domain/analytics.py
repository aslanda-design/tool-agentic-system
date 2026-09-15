"""Pure portfolio-analytics math — no I/O, no ORM (same discipline as
domain/returns.py). Backs both the `analytics` MCP server
(ai/mcp_servers/analytics/) and `GET /api/analytics/*` — see
plans/agentic_asset_mapping_phase7_8.md Phase 8b. Every function here
operates on data application/query_analytics.py already fetched from
Postgres; nothing below ever queries anything itself."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from app.domain.models import Transaction, TransactionType

ZERO = Decimal("0")


class _WeightedPosition(Protocol):
    """Structural shape this module needs from a position — deliberately
    NOT application/dto.py::PositionDTO itself: domain must never import
    from application (see backend/AGENTS.md's layering rule). PositionDTO
    already has exactly these two attributes, so passing one in just works."""

    market_value: Decimal | None
    returns: dict[str, Decimal | None]


class _HistoryPoint(Protocol):
    """Same reasoning as _WeightedPosition, for application/dto.py::HistoryPointDTO."""

    date: date
    market_value: Decimal


def weighted_return(positions: list[_WeightedPosition], period: str) -> Decimal | None:
    """Value-weighted average return across positions for one period label
    (e.g. "1d"/"1w"/"1m"/"ytd"/"1y" — the same keys PositionDTO.returns
    already uses). Positions with no market value yet (unpriced) or no
    return computed for this period are skipped rather than treated as 0 —
    an unknown return must never silently drag the average toward zero."""
    weighted_sum = ZERO
    total_weight = ZERO
    for position in positions:
        if position.market_value is None:
            continue
        period_return = position.returns.get(period)
        if period_return is None:
            continue
        weighted_sum += position.market_value * period_return
        total_weight += position.market_value
    if total_weight == ZERO:
        return None
    return weighted_sum / total_weight


def herfindahl_index(weights: list[Decimal]) -> Decimal:
    """Textbook Herfindahl-Hirschman Index: sum of squared weights, each in
    [0, 1]. 1/n for an equally-weighted n-holding portfolio; 1.0 for a
    single holding. `weights` need not sum to exactly 1 (rounding is the
    caller's problem), but should represent a full distribution — a
    top-N-only subset understates concentration."""
    return sum((weight * weight for weight in weights), ZERO)


@dataclass(slots=True)
class DrawdownResult:
    max_drawdown_pct: Decimal | None  # 0 or negative, e.g. Decimal("-0.15") for -15%
    max_drawdown_start: date | None  # the peak the worst drop fell from
    max_drawdown_end: date | None  # the trough of the worst drop
    current_drawdown_pct: Decimal | None  # vs the all-time-high seen in `history`


def max_drawdown(history: list[_HistoryPoint]) -> DrawdownResult:
    """Running peak-to-trough drawdown over a market_value history. `peak`
    only ever increases as the list is walked in date order, so its value
    at the end of the loop is also the all-time high needed for
    `current_drawdown_pct` — no second pass required."""
    if not history:
        return DrawdownResult(None, None, None, None)
    ordered = sorted(history, key=lambda point: point.date)
    peak = ordered[0].market_value
    peak_date = ordered[0].date
    worst_drawdown = ZERO
    worst_start: date | None = None
    worst_end: date | None = None
    for point in ordered:
        if point.market_value > peak:
            peak = point.market_value
            peak_date = point.date
        if peak > ZERO:
            drawdown = (point.market_value - peak) / peak
            if drawdown < worst_drawdown:
                worst_drawdown = drawdown
                worst_start, worst_end = peak_date, point.date
    last = ordered[-1]
    current_drawdown = (last.market_value - peak) / peak if peak > ZERO else None
    return DrawdownResult(
        max_drawdown_pct=worst_drawdown if worst_start is not None else ZERO,
        max_drawdown_start=worst_start,
        max_drawdown_end=worst_end,
        current_drawdown_pct=current_drawdown,
    )


@dataclass(slots=True)
class MispricedTradeDTO:
    transaction_id: int
    asset_id: int
    symbol: str
    executed_price: Decimal
    market_close: Decimal
    pct_diff: Decimal  # (executed_price - market_close) / market_close


def flag_mispriced_trades(
    transactions: list[Transaction],
    symbol_lookup: Callable[[int], str],
    price_lookup: Callable[[int, date], Decimal | None],
    threshold: Decimal = Decimal("0.10"),
) -> list[MispricedTradeDTO]:
    """Flag BUY/SELL transactions whose executed price differs from that
    day's close (`price_lookup`, expected to be a "most recent close on or
    before this date" lookup — same forward-fill semantics
    MarketDataRepo.get_price_on_or_before already uses everywhere else in
    this app, so a weekend/holiday trade date doesn't produce a false
    positive just because there's no bar for that exact date) by more than
    `threshold` (10% default). The most common real cause: a GBp/pence-style
    minor-unit mismatch (see yfinance_adapter.py's _MINOR_UNIT_CURRENCIES) or
    a manual-entry typo — either way, a >10% gap is never just noise.
    DIVIDEND/FEE/INTEREST/DEPOSIT/WITHDRAWAL/SPLIT rows have no meaningful
    "price vs market close" comparison and are skipped."""
    flagged = []
    for txn in transactions:
        if txn.type not in (TransactionType.BUY, TransactionType.SELL) or txn.asset_id is None:
            continue
        market_close = price_lookup(txn.asset_id, txn.trade_date)
        if market_close is None or market_close == ZERO:
            continue
        pct_diff = (txn.price - market_close) / market_close
        if abs(pct_diff) > threshold:
            flagged.append(
                MispricedTradeDTO(
                    transaction_id=txn.id,
                    asset_id=txn.asset_id,
                    symbol=symbol_lookup(txn.asset_id),
                    executed_price=txn.price,
                    market_close=market_close,
                    pct_diff=pct_diff,
                )
            )
    return flagged
