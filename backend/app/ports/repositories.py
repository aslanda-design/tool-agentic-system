"""Persistence ports. Three coarse repositories — per-entity repos would be
layer explosion at this app's size. Implementations: adapters/persistence/repositories.py
(SQLAlchemy + Postgres, the only backing store this app targets)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from decimal import Decimal

from app.domain.models import (
    Account,
    Asset,
    AssetClass,
    IdentifierScheme,
    PortfolioSnapshotPoint,
    Transaction,
)


class AssetRepo(ABC):
    @abstractmethod
    def get(self, asset_id: int) -> Asset | None: ...

    @abstractmethod
    def get_by_symbol(self, symbol: str) -> Asset | None: ...

    @abstractmethod
    def find_by_identifier(self, scheme: IdentifierScheme, value: str) -> Asset | None: ...

    @abstractmethod
    def create(
        self,
        symbol: str,
        name: str,
        asset_class: AssetClass,
        currency: str,
        exchange: str | None = None,
        isin: str | None = None,
        needs_mapping: bool = False,
    ) -> Asset: ...

    @abstractmethod
    def add_identifier(self, asset_id: int, scheme: IdentifierScheme, value: str) -> None: ...

    @abstractmethod
    def set_needs_mapping(self, asset_id: int, needs_mapping: bool) -> None: ...

    @abstractmethod
    def set_isin(self, asset_id: int, isin: str) -> None:
        """Backfill assets.isin when it's learned later (e.g. from a Flex trade)
        and wasn't known at creation time. No-op if already set."""

    @abstractmethod
    def list_needing_mapping(self) -> list[Asset]: ...

    @abstractmethod
    def get_identifier_value(self, asset_id: int, scheme: IdentifierScheme) -> str | None: ...

    @abstractmethod
    def list_assets_with_scheme(self, scheme: IdentifierScheme) -> list[tuple[Asset, str]]:
        """Every asset that has an identifier of this scheme, paired with its value.
        Market data only ever resolves via IdentifierScheme.YFINANCE rows."""

    @abstractmethod
    def search_local(self, query: str, limit: int = 20) -> list[Asset]: ...

    @abstractmethod
    def list_all(self) -> list[Asset]: ...

    @abstractmethod
    def update(self, asset_id: int, symbol: str, name: str, isin: str | None) -> Asset:
        """Overwrite the asset's editable identity fields. Raises
        AssetNotFoundError if it doesn't exist, AssetConflictError if the
        new symbol/ISIN collides with a different asset's unique value."""

    @abstractmethod
    def delete(self, asset_id: int) -> None:
        """Permanently delete the asset row. Raises AssetNotFoundError if it
        doesn't exist. Caller must remove dependent holdings/transactions
        first (see PortfolioRepo.delete_holdings_for_asset /
        delete_transactions_for_asset) — those tables have no ON DELETE
        CASCADE, unlike asset_identifiers/prices/quotes/position_snapshots,
        so this would otherwise fail a foreign-key constraint."""


class PortfolioRepo(ABC):
    # --- accounts ---
    @abstractmethod
    def get_or_create_account(
        self, broker_key: str, external_id: str, name: str, currency: str, source: str
    ) -> Account: ...

    @abstractmethod
    def list_accounts(self) -> list[Account]: ...

    @abstractmethod
    def get_account(self, account_id: int) -> Account | None: ...

    # --- holdings (current snapshot per account+asset) ---
    @abstractmethod
    def upsert_holding(
        self,
        account_id: int,
        asset_id: int,
        quantity: Decimal,
        avg_cost_price: Decimal,
        cost_currency: str,
        as_of: datetime,
        source: str,
    ) -> None: ...

    @abstractmethod
    def replace_holdings_for_account(self, account_id: int, source: str) -> None:
        """Clear stale holdings for an account before writing a fresh API sync."""

    @abstractmethod
    def delete_holding(self, account_id: int, asset_id: int) -> None: ...

    @abstractmethod
    def get_holding(self, account_id: int, asset_id: int) -> dict | None:
        """A single holding row (even if quantity is 0), or None if it doesn't exist yet."""

    @abstractmethod
    def list_positions(self, account_id: int | None = None) -> list[dict]:
        """Raw rows joining holdings+assets+accounts for the query layer to shape."""

    @abstractmethod
    def upsert_cash_balance(
        self, account_id: int, currency: str, amount: Decimal, as_of: datetime
    ) -> None: ...

    @abstractmethod
    def list_cash_balances(self, account_id: int | None = None) -> list[dict]:
        """Rows of {account_id, currency, amount, as_of}."""

    # --- transactions ---
    @abstractmethod
    def add_transactions(self, transactions: list[Transaction]) -> int:
        """Idempotent insert keyed on (account_id, external_id) where set.
        Returns the number of NEW rows actually inserted."""

    @abstractmethod
    def delete_transactions_for_asset(self, asset_id: int) -> int:
        """Permanently delete every transaction referencing this asset,
        across every account. Returns the number of rows deleted. Caller
        must rebuild snapshots afterwards — this bypasses the normal
        add/delete-one-transaction path deliberately (asset deletion is a
        bulk cleanup operation, not a ledger correction)."""

    @abstractmethod
    def delete_holdings_for_asset(self, asset_id: int) -> int:
        """Permanently delete every holding row for this asset, across every
        account. Returns the number of rows deleted."""

    @abstractmethod
    def list_transactions(
        self,
        account_id: int | None = None,
        asset_id: int | None = None,
        since: date | None = None,
    ) -> list[Transaction]: ...

    @abstractmethod
    def add_manual_transaction(self, transaction: Transaction) -> Transaction: ...

    @abstractmethod
    def update_manual_transaction(self, transaction_id: int, **fields) -> None: ...

    @abstractmethod
    def delete_manual_transaction(self, transaction_id: int) -> None: ...

    # --- snapshots ---
    @abstractmethod
    def delete_snapshots_in_range(self, start: date, end: date) -> None: ...

    @abstractmethod
    def upsert_position_snapshot(
        self,
        snap_date: date,
        asset_id: int,
        quantity: Decimal,
        price: Decimal,
        market_value_base: Decimal,
        cost_basis_base: Decimal,
    ) -> None: ...

    @abstractmethod
    def upsert_portfolio_snapshot(
        self,
        snap_date: date,
        base_currency: str,
        market_value: Decimal,
        cost_basis: Decimal,
        net_invested: Decimal,
        cash: Decimal,
    ) -> None: ...

    @abstractmethod
    def get_portfolio_snapshots(self, start: date, end: date) -> list[PortfolioSnapshotPoint]: ...

    @abstractmethod
    def get_position_snapshots_totals(
        self, start: date, end: date, asset_ids: list[int]
    ) -> list[dict]:
        """Daily totals of `position_snapshots` summed across just these
        assets — {date, market_value, cost_basis} — powers the dashboard
        chart's asset-subset selector."""

    @abstractmethod
    def get_latest_snapshot(self) -> PortfolioSnapshotPoint | None: ...

    @abstractmethod
    def last_snapshot_date(self) -> date | None: ...

    @abstractmethod
    def earliest_transaction_date(self, account_id: int | None = None) -> date | None: ...


class MarketDataRepo(ABC):
    @abstractmethod
    def upsert_quote(
        self,
        asset_id: int,
        price: Decimal,
        prev_close: Decimal | None,
        currency: str,
        as_of: datetime,
        source: str,
    ) -> None: ...

    @abstractmethod
    def get_quote(self, asset_id: int) -> dict | None: ...

    @abstractmethod
    def upsert_bars(self, asset_id: int, bars: list[dict], source: str) -> None: ...

    @abstractmethod
    def get_bars(self, asset_id: int, start: date, end: date) -> list[dict]: ...

    @abstractmethod
    def get_price_on_or_before(self, asset_id: int, on_date: date) -> Decimal | None:
        """Forward-fill: the most recent close at or before `on_date`."""

    @abstractmethod
    def latest_price_date(self, asset_id: int) -> date | None: ...

    @abstractmethod
    def upsert_fx_rates(self, base: str, quote: str, rates: dict[date, Decimal]) -> None: ...

    @abstractmethod
    def get_fx_rate(self, base: str, quote: str, on_date: date) -> Decimal | None:
        """Most recent daily FX close at or before `on_date`."""

    @abstractmethod
    def currency_pairs_in_use(self, base_currency: str) -> list[str]:
        """Distinct non-base currencies currently held, needing FX vs base_currency."""
