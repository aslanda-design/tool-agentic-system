"""Domain entities and value objects. Pure Python — no FastAPI, no SQLAlchemy,
no I/O. These are what the application layer and adapters exchange; the ORM
mapped classes in adapters/persistence/orm.py are a separate, private concern."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class AssetClass(StrEnum):
    EQUITY = "EQUITY"
    ETF = "ETF"
    FUND = "FUND"
    CRYPTO = "CRYPTO"
    BOND = "BOND"
    CASH = "CASH"
    OTHER = "OTHER"


class TransactionType(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    FEE = "FEE"
    INTEREST = "INTEREST"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    SPLIT = "SPLIT"


class AccountSource(StrEnum):
    API = "api"
    MANUAL = "manual"


class IdentifierScheme(StrEnum):
    IBKR_CONID = "IBKR_CONID"
    ISIN = "ISIN"
    YFINANCE = "YFINANCE"
    USER = "USER"


@dataclass(slots=True)
class Asset:
    id: int | None
    symbol: str
    name: str
    asset_class: AssetClass
    currency: str
    exchange: str | None = None
    isin: str | None = None
    needs_mapping: bool = False
    # OpenFIGI's shareClassFIGI — identifies the security across every
    # exchange it lists on, learned once the security resolver succeeds.
    share_class_figi: str | None = None


@dataclass(slots=True)
class Account:
    id: int | None
    broker_key: str
    external_id: str
    name: str
    currency: str
    source: AccountSource


@dataclass(slots=True)
class Holding:
    account_id: int
    asset_id: int
    quantity: Decimal
    avg_cost_price: Decimal
    cost_currency: str
    as_of: datetime
    source: AccountSource


@dataclass(slots=True)
class Transaction:
    id: int | None
    account_id: int
    asset_id: int | None
    type: TransactionType
    quantity: Decimal
    price: Decimal
    fees: Decimal
    currency: str
    executed_at: datetime
    trade_date: date
    external_id: str | None
    source: AccountSource
    note: str = ""


@dataclass(slots=True)
class Quote:
    asset_id: int
    price: Decimal
    prev_close: Decimal | None
    currency: str
    as_of: datetime
    source: str


@dataclass(slots=True)
class Bar:
    asset_id: int
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal
    volume: Decimal
    source: str


@dataclass(slots=True)
class PositionValue:
    """A holding priced and converted into the display currency, for the API."""

    asset_id: int
    symbol: str
    name: str
    account_id: int
    broker_key: str
    quantity: Decimal
    avg_cost_price: Decimal
    last_price: Decimal
    native_currency: str
    market_value: Decimal  # in display currency
    cost_basis: Decimal  # in display currency
    unrealized_pnl: Decimal  # in display currency
    unrealized_pnl_pct: Decimal | None


@dataclass(slots=True)
class PortfolioSnapshotPoint:
    date: date
    market_value: Decimal
    cost_basis: Decimal
    net_invested: Decimal
    cash: Decimal
