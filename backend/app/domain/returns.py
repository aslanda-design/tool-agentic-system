"""Pure valuation/return math. No I/O, no ORM — easy to unit test in isolation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.models import Transaction, TransactionType

ZERO = Decimal("0")


@dataclass(slots=True)
class CostBasisState:
    """Running average-cost position for a single asset."""

    quantity: Decimal = ZERO
    total_cost: Decimal = ZERO  # in the transaction's own currency
    realized_pnl: Decimal = ZERO

    @property
    def avg_cost_price(self) -> Decimal:
        if self.quantity == ZERO:
            return ZERO
        return self.total_cost / self.quantity

    def apply(self, txn: Transaction) -> None:
        """Fold one BUY/SELL/SPLIT transaction into the running position.
        DIVIDEND/FEE/INTEREST/DEPOSIT/WITHDRAWAL don't change asset quantity/cost."""
        if txn.type is TransactionType.BUY:
            cost = txn.quantity * txn.price + txn.fees
            self.quantity += txn.quantity
            self.total_cost += cost
        elif txn.type is TransactionType.SELL:
            if self.quantity > ZERO:
                avg = self.avg_cost_price
                cost_removed = avg * txn.quantity
                proceeds = txn.quantity * txn.price - txn.fees
                self.realized_pnl += proceeds - cost_removed
                self.total_cost -= cost_removed
            self.quantity -= txn.quantity
        elif txn.type is TransactionType.SPLIT:
            # txn.quantity holds the split ratio's added share count; total_cost is unchanged.
            self.quantity += txn.quantity


def build_cost_basis(transactions: list[Transaction]) -> CostBasisState:
    """Replay transactions (must be pre-sorted by execution time) into a
    single average-cost position."""
    state = CostBasisState()
    for txn in sorted(transactions, key=lambda t: t.executed_at):
        state.apply(txn)
    return state


def unrealized_pnl(market_value: Decimal, cost_basis: Decimal) -> Decimal:
    return market_value - cost_basis


def unrealized_pnl_pct(market_value: Decimal, cost_basis: Decimal) -> Decimal | None:
    if cost_basis == ZERO:
        return None
    return (market_value - cost_basis) / cost_basis


def price_return(start_price: Decimal, end_price: Decimal) -> Decimal | None:
    """Simple price return between two points, as a fraction (0.05 = +5%)."""
    if start_price == ZERO:
        return None
    return (end_price - start_price) / start_price


def solve_opening_balance(
    recorded_transactions: list[Transaction], target_quantity: Decimal, target_avg_cost_price: Decimal
) -> tuple[Decimal, Decimal]:
    """Solve for the single opening BUY (quantity, avg_cost_price) that,
    prepended before all `recorded_transactions`, reconciles their replay to
    a known-correct current position — used when a broker's transaction feed
    doesn't reach as far back as the account's real inception (e.g. an IBKR
    Flex Query configured for a narrow date range), so `target_*` comes from
    the broker's own live-synced holding instead.

    Quantity is exact, ordinary arithmetic: BUY/SELL/SPLIT deltas don't
    depend on price, so `opening_quantity = target - sum(recorded deltas)`.

    Average cost is solved algebraically by replaying the recorded
    transactions with the unknown opening cost carried symbolically as
    `cost = a * C0 + b` (a running affine function of the unknown C0).  This
    is needed — not just `target_cost - sum(recorded buy costs)` — because a
    SELL removes cost at the *average* cost basis at that point in time,
    which itself depends on C0 once the sell happens before enough is
    recorded to know it outright; a affine coefficient handles that
    correctly instead of assuming the history is buy-only.

    Returns (0, 0) if the recorded transactions already account for the
    full target quantity (nothing missing to backfill).
    """
    ordered = sorted(recorded_transactions, key=lambda t: t.executed_at)

    net_recorded_qty = ZERO
    for txn in ordered:
        if txn.type in (TransactionType.BUY, TransactionType.SPLIT):
            net_recorded_qty += txn.quantity
        elif txn.type is TransactionType.SELL:
            net_recorded_qty -= txn.quantity
    opening_quantity = target_quantity - net_recorded_qty
    if opening_quantity <= ZERO:
        return ZERO, ZERO

    quantity = opening_quantity
    a = Decimal("1")
    b = ZERO
    for txn in ordered:
        if txn.type is TransactionType.BUY:
            quantity += txn.quantity
            b += txn.quantity * txn.price + txn.fees
        elif txn.type is TransactionType.SELL:
            if quantity > ZERO:
                factor = Decimal("1") - (txn.quantity / quantity)
                a *= factor
                b *= factor
            quantity -= txn.quantity
        elif txn.type is TransactionType.SPLIT:
            quantity += txn.quantity

    if a == ZERO:
        return opening_quantity, ZERO
    target_cost = target_quantity * target_avg_cost_price
    opening_cost = (target_cost - b) / a
    return opening_quantity, opening_cost / opening_quantity
