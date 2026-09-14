"""Outbound port for pulling FULL historical transactions from a broker,
independent of whatever the live trading API session can see.

IBKR's `reqExecutions` only returns fills since midnight — it cannot be used
for real history. `StatementPort` is what makes the "amount invested" chart
correct: implementations pull complete statements (IBKR Flex Web Service) or
parse a user-provided export (CSV import).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from app.ports.broker import BrokerTransaction


class StatementPort(ABC):
    account_external_id: str

    @abstractmethod
    def fetch_transactions(self, since: date | None = None) -> list[BrokerTransaction]:
        """Full transaction history for the account, optionally since a date."""
