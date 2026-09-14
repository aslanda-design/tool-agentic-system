"""Builds the daily history that drives the dashboard's invested-vs-value
chart. A pure, idempotent replay: given a date range, it deletes and
rewrites every row in it by folding transactions day by day.

Convention for cash-flow-only transaction types (DIVIDEND, INTEREST, FEE,
DEPOSIT, WITHDRAWAL): `quantity` is 1 and `price` holds the cash amount, so
`quantity * price` is the transaction's cash value uniformly across every
type. This is documented in backend/AGENTS.md.

Cost basis is converted to the base currency at the FX rate of the
transaction's own date — never "today's rate applied to history" — so
snapshots never silently rewrite past currency effects.

"Invested" (the `net_invested` column) is the cost basis of currently-open
positions — the original buy cost, Sum(quantity * avg_cost_price) — NOT
cumulative cash deposited. An earlier version derived it from DEPOSIT/
WITHDRAWAL transactions instead, which silently broke the moment a broker
feed's recorded history didn't go back as far as its recorded deposits (or
vice versa): the two ledgers no longer agreed, and "invested" could show a
value with no relationship to what was actually paid for the positions on
screen. Cost basis is exact by construction (it's the same number the
positions table shows) and needs no separate cash-flow bookkeeping to stay
correct — see application/opening_balance.py for how to backfill it when a
broker's transaction feed itself doesn't reach far enough back.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from app.domain.models import Transaction, TransactionType
from app.domain.returns import CostBasisState
from app.ports.repositories import AssetRepo, MarketDataRepo, PortfolioRepo

ZERO = Decimal("0")


class BuildSnapshotsUseCase:
    def __init__(
        self,
        portfolio_repo: PortfolioRepo,
        market_data_repo: MarketDataRepo,
        asset_repo: AssetRepo,
        base_currency: str,
    ) -> None:
        self.portfolio_repo = portfolio_repo
        self.market_data_repo = market_data_repo
        self.asset_repo = asset_repo
        self.base_currency = base_currency.upper()

    def execute(self, from_date: date | None = None, to_date: date | None = None) -> int:
        to_date = to_date or date.today()
        from_date = from_date or self.portfolio_repo.earliest_transaction_date()
        if from_date is None or from_date > to_date:
            return 0

        all_txns = self.portfolio_repo.list_transactions()
        by_date: dict[date, list[Transaction]] = defaultdict(list)
        for t in all_txns:
            by_date[t.trade_date].append(t)

        asset_currency_cache: dict[int, str] = {}

        def currency_of(asset_id: int) -> str:
            if asset_id not in asset_currency_cache:
                asset = self.asset_repo.get(asset_id)
                asset_currency_cache[asset_id] = asset.currency if asset else self.base_currency
            return asset_currency_cache[asset_id]

        self.portfolio_repo.delete_snapshots_in_range(from_date, to_date)

        cost_states: dict[int, CostBasisState] = defaultdict(CostBasisState)
        cash_base = ZERO

        current = from_date
        days_written = 0
        while current <= to_date:
            for txn in by_date.get(current, []):
                rate = self._rate_to_base(txn.currency, current)
                if txn.asset_id is not None and txn.type in (
                    TransactionType.BUY,
                    TransactionType.SELL,
                    TransactionType.SPLIT,
                ):
                    cost_states[txn.asset_id].apply(txn)

                cash_base += self._cash_impact(txn) * rate

            market_value_base = ZERO
            cost_basis_base = ZERO
            for asset_id, state in cost_states.items():
                if state.quantity == ZERO:
                    continue
                currency = currency_of(asset_id)
                rate = self._rate_to_base(currency, current)
                price = self.market_data_repo.get_price_on_or_before(asset_id, current) or ZERO
                market_value = price * state.quantity * rate
                cost_basis = state.total_cost * rate
                self.portfolio_repo.upsert_position_snapshot(
                    current, asset_id, state.quantity, price, market_value, cost_basis
                )
                market_value_base += market_value
                cost_basis_base += cost_basis

            # "net_invested" here IS the cost basis of open positions — see
            # the module docstring for why this replaced a deposit-based calc.
            self.portfolio_repo.upsert_portfolio_snapshot(
                current, self.base_currency, market_value_base, cost_basis_base, cost_basis_base, cash_base
            )
            days_written += 1
            current += timedelta(days=1)

        return days_written

    def _rate_to_base(self, currency: str, on_date: date) -> Decimal:
        currency = currency.upper()
        if currency == self.base_currency:
            return Decimal("1")
        rate = self.market_data_repo.get_fx_rate(currency, self.base_currency, on_date)
        return rate if rate is not None else Decimal("1")

    @staticmethod
    def _cash_impact(txn: Transaction) -> Decimal:
        amount = txn.quantity * txn.price
        if txn.type is TransactionType.BUY:
            return -(amount + txn.fees)
        if txn.type is TransactionType.SELL:
            return amount - txn.fees
        if txn.type is TransactionType.FEE or txn.type is TransactionType.WITHDRAWAL:
            return -amount
        if txn.type in (TransactionType.DIVIDEND, TransactionType.INTEREST, TransactionType.DEPOSIT):
            return amount
        return ZERO  # SPLIT: no cash effect
