"""Outbound port for pulling live account state from a broker's API.

Deliberately has no `connect()` / lifecycle methods — connection handling is
an adapter detail. One call, one round trip, one clear result.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass(slots=True)
class BrokerAccount:
    external_id: str
    name: str
    currency: str


@dataclass(slots=True)
class BrokerHolding:
    account_external_id: str
    symbol: str
    name: str
    quantity: Decimal
    avg_cost_price: Decimal
    last_price: Decimal
    currency: str
    ibkr_conid: str | None = None
    isin: str | None = None
    # The broker's own exchange code (e.g. IBKR's contract.primaryExchange —
    # 'IBIS2', 'LSEETF') — feeds the security resolver's context (see
    # domain/exchanges.py, application/asset_resolution.py). Not necessarily
    # a listing/market-data venue by itself.
    exchange: str | None = None


@dataclass(slots=True)
class BrokerCashBalance:
    account_external_id: str
    currency: str
    amount: Decimal


@dataclass(slots=True)
class BrokerTransaction:
    account_external_id: str
    external_id: str
    symbol: str | None
    type: str  # matches domain.models.TransactionType values
    quantity: Decimal
    price: Decimal
    fees: Decimal
    currency: str
    executed_at: datetime
    ibkr_conid: str | None = None
    isin: str | None = None
    # See BrokerHolding.exchange. `name` is the broker's own description of
    # the instrument (e.g. Flex's `description` attribute) — better than the
    # bare symbol when a fresh needs_mapping asset is created from a
    # transaction rather than a holding.
    exchange: str | None = None
    name: str | None = None


@dataclass(slots=True)
class BrokerSnapshot:
    accounts: list[BrokerAccount] = field(default_factory=list)
    holdings: list[BrokerHolding] = field(default_factory=list)
    cash: list[BrokerCashBalance] = field(default_factory=list)
    transactions: list[BrokerTransaction] = field(default_factory=list)


class BrokerPort(ABC):
    """One implementation per broker that exposes a live API (today: IBKR)."""

    broker_key: str

    @abstractmethod
    def fetch_snapshot(self, since: date | None = None) -> BrokerSnapshot:
        """Pull accounts, current holdings, cash balances, and any
        transactions the broker's live API can see (may be partial — see
        StatementPort for full history)."""
