"""Suggests the "opening balance" transaction needed to reconcile an
account's recorded transaction history with its true current position —
useful when a broker's transaction feed doesn't go back far enough (e.g. an
IBKR Flex Query configured for a narrow date range, so most of the real buy
history was never imported).

The suggestion is never applied automatically. It's surfaced as a prefilled
manual BUY transaction (dated before the recorded history begins) for the
user to review and confirm via the existing manual-transaction endpoint —
no new write path, no new domain concept: an "opening balance" is just a
BUY transaction whose price is the historical average cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.domain.returns import solve_opening_balance
from app.ports.repositories import PortfolioRepo

ZERO = Decimal("0")


@dataclass(slots=True)
class OpeningBalanceSuggestionDTO:
    quantity: Decimal
    avg_cost_price: Decimal
    currency: str
    suggested_date: date


class SuggestOpeningBalanceUseCase:
    def __init__(self, portfolio_repo: PortfolioRepo) -> None:
        self.portfolio_repo = portfolio_repo

    def execute(self, account_id: int, asset_id: int) -> OpeningBalanceSuggestionDTO | None:
        holding = self.portfolio_repo.get_holding(account_id, asset_id)
        if holding is None:
            return None  # no broker-derived current position to reconcile against

        recorded = self.portfolio_repo.list_transactions(account_id=account_id, asset_id=asset_id)
        opening_qty, opening_avg_cost = solve_opening_balance(
            recorded, holding["quantity"], holding["avg_cost_price"]
        )
        if opening_qty <= ZERO:
            return None  # recorded history already accounts for the full position

        earliest = self.portfolio_repo.earliest_transaction_date(account_id=account_id)
        suggested_date = (earliest - timedelta(days=1)) if earliest else date.today()
        return OpeningBalanceSuggestionDTO(
            quantity=opening_qty,
            avg_cost_price=opening_avg_cost,
            currency=holding["cost_currency"],
            suggested_date=suggested_date,
        )
