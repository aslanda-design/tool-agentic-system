"""Table-driven tests for domain/analytics.py — see
plans/agentic_asset_mapping_phase7_8.md Phase 8b. Pure functions, no DB —
callers (application/query_analytics.py) are tested separately."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from app.domain.analytics import (
    flag_mispriced_trades,
    herfindahl_index,
    max_drawdown,
    weighted_return,
)
from app.domain.models import AccountSource, Transaction, TransactionType

D = Decimal


@dataclass
class _Position:
    market_value: Decimal | None
    returns: dict[str, Decimal | None]


@dataclass
class _HistoryPoint:
    date: date
    market_value: Decimal


# --- weighted_return ---------------------------------------------------


def test_weighted_return_weights_by_market_value():
    positions = [_Position(D(100), {"1m": D("0.1")}), _Position(D(300), {"1m": D("0.2")})]
    assert weighted_return(positions, "1m") == D("0.175")


def test_weighted_return_skips_unpriced_positions():
    positions = [_Position(None, {"1m": D("0.5")}), _Position(D(100), {"1m": D("0.1")})]
    assert weighted_return(positions, "1m") == D("0.1")


def test_weighted_return_skips_positions_missing_the_period():
    positions = [_Position(D(100), {}), _Position(D(200), {"1m": D("0.05")})]
    assert weighted_return(positions, "1m") == D("0.05")


def test_weighted_return_is_none_when_nothing_qualifies():
    positions = [_Position(None, {"1m": D("0.1")}), _Position(D(100), {})]
    assert weighted_return(positions, "1m") is None


def test_weighted_return_empty_list_is_none():
    assert weighted_return([], "1m") is None


# --- herfindahl_index ----------------------------------------------------


def test_herfindahl_equal_weights():
    assert herfindahl_index([D("0.25")] * 4) == D("0.25")


def test_herfindahl_single_holding_is_one():
    assert herfindahl_index([D("1")]) == D("1")


def test_herfindahl_empty_is_zero():
    assert herfindahl_index([]) == D("0")


# --- max_drawdown ----------------------------------------------------------


def test_max_drawdown_empty_history_is_all_none():
    result = max_drawdown([])
    assert result.max_drawdown_pct is None
    assert result.max_drawdown_start is None
    assert result.max_drawdown_end is None
    assert result.current_drawdown_pct is None


def test_max_drawdown_monotonic_rise_has_zero_drawdown():
    history = [_HistoryPoint(date(2026, 1, i), D(100 + i)) for i in range(1, 5)]
    result = max_drawdown(history)
    assert result.max_drawdown_pct == D("0")
    assert result.current_drawdown_pct == D("0")


def test_max_drawdown_finds_the_worst_peak_to_trough_drop():
    history = [
        _HistoryPoint(date(2026, 1, 1), D(100)),
        _HistoryPoint(date(2026, 1, 2), D(120)),  # new peak
        _HistoryPoint(date(2026, 1, 3), D(90)),  # trough: (90-120)/120 = -0.25
        _HistoryPoint(date(2026, 1, 4), D(110)),  # partial recovery, still below peak
    ]
    result = max_drawdown(history)
    assert result.max_drawdown_pct == (D("90") - D("120")) / D("120")
    assert result.max_drawdown_start == date(2026, 1, 2)
    assert result.max_drawdown_end == date(2026, 1, 3)
    assert result.current_drawdown_pct == (D("110") - D("120")) / D("120")


def test_max_drawdown_sorts_out_of_order_history_by_date():
    history = [
        _HistoryPoint(date(2026, 1, 3), D(90)),
        _HistoryPoint(date(2026, 1, 1), D(100)),
        _HistoryPoint(date(2026, 1, 2), D(120)),
    ]
    result = max_drawdown(history)
    assert result.max_drawdown_start == date(2026, 1, 2)
    assert result.max_drawdown_end == date(2026, 1, 3)


# --- flag_mispriced_trades ---------------------------------------------


def _txn(id_, type_, price, trade_date=date(2026, 9, 1), asset_id=1) -> Transaction:
    return Transaction(
        id=id_, account_id=1, asset_id=asset_id, type=type_, quantity=D(1), price=price, fees=D(0),
        currency="EUR", executed_at=datetime(2026, 9, 1, tzinfo=timezone.utc), trade_date=trade_date,
        external_id=None, source=AccountSource.MANUAL,
    )


def _symbol_lookup(asset_id: int) -> str:
    return f"ASSET{asset_id}"


def test_flag_mispriced_trades_flags_a_large_gap():
    txns = [_txn(1, TransactionType.BUY, D(150))]
    flagged = flag_mispriced_trades(txns, _symbol_lookup, lambda aid, d: D(100))
    assert len(flagged) == 1
    assert flagged[0].transaction_id == 1
    assert flagged[0].symbol == "ASSET1"
    assert flagged[0].pct_diff == D("0.5")


def test_flag_mispriced_trades_does_not_flag_at_the_threshold_boundary():
    txns = [_txn(1, TransactionType.SELL, D(90))]  # exactly -10% vs a 100 close
    flagged = flag_mispriced_trades(txns, _symbol_lookup, lambda aid, d: D(100))
    assert flagged == []


def test_flag_mispriced_trades_ignores_non_trade_types():
    txns = [_txn(1, TransactionType.DIVIDEND, D(1000))]
    flagged = flag_mispriced_trades(txns, _symbol_lookup, lambda aid, d: D(1))
    assert flagged == []


def test_flag_mispriced_trades_skips_when_no_market_price_is_known():
    txns = [_txn(1, TransactionType.BUY, D(150))]
    flagged = flag_mispriced_trades(txns, _symbol_lookup, lambda aid, d: None)
    assert flagged == []


def test_flag_mispriced_trades_custom_threshold():
    txns = [_txn(1, TransactionType.BUY, D(105))]  # +5%
    assert flag_mispriced_trades(txns, _symbol_lookup, lambda aid, d: D(100), threshold=D("0.10")) == []
    flagged = flag_mispriced_trades(txns, _symbol_lookup, lambda aid, d: D(100), threshold=D("0.01"))
    assert len(flagged) == 1
