"""Minimal in-memory stand-ins for AssetRepo/PortfolioRepo, covering only the
methods CsvImportUseCase actually calls. Not full ABC implementations —
duck-typed on purpose so the import tests stay fast and DB-free (see
backend/AGENTS.md: `backend/tests/` needs a running Postgres only for
test_health.py's TestClient; these must not)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.domain.models import (
    Account,
    AccountSource,
    Asset,
    AssetClass,
    IdentifierScheme,
    Transaction,
)


class FakeAssetRepo:
    def __init__(self) -> None:
        self._assets: dict[int, Asset] = {}
        self._by_identifier: dict[tuple[IdentifierScheme, str], int] = {}
        self._next_id = 1

    def get(self, asset_id: int) -> Asset | None:
        return self._assets.get(asset_id)

    def get_by_symbol(self, symbol: str) -> Asset | None:
        return next((a for a in self._assets.values() if a.symbol == symbol), None)

    def find_by_identifier(self, scheme: IdentifierScheme, value: str) -> Asset | None:
        asset_id = self._by_identifier.get((scheme, value))
        return self._assets.get(asset_id) if asset_id is not None else None

    def create(
        self,
        symbol: str,
        name: str,
        asset_class: AssetClass,
        currency: str,
        exchange: str | None = None,
        isin: str | None = None,
        needs_mapping: bool = False,
    ) -> Asset:
        asset = Asset(
            id=self._next_id,
            symbol=symbol,
            name=name,
            asset_class=asset_class,
            currency=currency,
            exchange=exchange,
            isin=isin,
            needs_mapping=needs_mapping,
        )
        self._assets[asset.id] = asset
        self._next_id += 1
        return asset

    def add_identifier(self, asset_id: int, scheme: IdentifierScheme, value: str) -> None:
        self._by_identifier[(scheme, value)] = asset_id

    def set_needs_mapping(self, asset_id: int, needs_mapping: bool) -> None:
        self._assets[asset_id].needs_mapping = needs_mapping

    def set_isin(self, asset_id: int, isin: str) -> None:
        if not self._assets[asset_id].isin:
            self._assets[asset_id].isin = isin

    def list_needing_mapping(self) -> list[Asset]:
        return [a for a in self._assets.values() if a.needs_mapping]

    def get_identifier_value(self, asset_id: int, scheme: IdentifierScheme) -> str | None:
        for (s, value), aid in self._by_identifier.items():
            if s == scheme and aid == asset_id:
                return value
        return None

    def list_assets_with_scheme(self, scheme: IdentifierScheme) -> list[tuple[Asset, str]]:
        return [
            (self._assets[aid], value) for (s, value), aid in self._by_identifier.items() if s == scheme
        ]

    def search_local(self, query: str, limit: int = 20) -> list[Asset]:
        return [a for a in self._assets.values() if query.lower() in a.symbol.lower()][:limit]

    def list_all(self) -> list[Asset]:
        return list(self._assets.values())


@dataclass
class _HoldingRow:
    account_id: int
    asset_id: int
    quantity: Decimal
    avg_cost_price: Decimal
    cost_currency: str
    as_of: datetime
    source: str


class FakePortfolioRepo:
    def __init__(self) -> None:
        self._accounts: dict[int, Account] = {}
        self._next_account_id = 1
        self._transactions: list[Transaction] = []
        self._next_txn_id = 1
        self._holdings: dict[tuple[int, int], _HoldingRow] = {}

    def get_or_create_account(
        self, broker_key: str, external_id: str, name: str, currency: str, source: str
    ) -> Account:
        existing = next(
            (a for a in self._accounts.values() if a.broker_key == broker_key and a.external_id == external_id),
            None,
        )
        if existing:
            return existing
        account = Account(
            id=self._next_account_id,
            broker_key=broker_key,
            external_id=external_id,
            name=name,
            currency=currency,
            source=AccountSource(source),
        )
        self._accounts[account.id] = account
        self._next_account_id += 1
        return account

    def list_accounts(self) -> list[Account]:
        return list(self._accounts.values())

    def get_account(self, account_id: int) -> Account | None:
        return self._accounts.get(account_id)

    def upsert_holding(
        self,
        account_id: int,
        asset_id: int,
        quantity: Decimal,
        avg_cost_price: Decimal,
        cost_currency: str,
        as_of: datetime,
        source: str,
    ) -> None:
        self._holdings[(account_id, asset_id)] = _HoldingRow(
            account_id, asset_id, quantity, avg_cost_price, cost_currency, as_of, source
        )

    def replace_holdings_for_account(self, account_id: int, source: str) -> None:
        self._holdings = {k: v for k, v in self._holdings.items() if k[0] != account_id}

    def delete_holding(self, account_id: int, asset_id: int) -> None:
        self._holdings.pop((account_id, asset_id), None)

    def get_holding(self, account_id: int, asset_id: int) -> dict | None:
        row = self._holdings.get((account_id, asset_id))
        return None if row is None else vars(row)

    def list_positions(self, account_id: int | None = None) -> list[dict]:
        return [vars(h) for h in self._holdings.values() if account_id is None or h.account_id == account_id]

    def upsert_cash_balance(self, account_id: int, currency: str, amount: Decimal, as_of: datetime) -> None:
        pass

    def list_cash_balances(self, account_id: int | None = None) -> list[dict]:
        return []

    def add_transactions(self, transactions: list[Transaction]) -> int:
        if not transactions:
            return 0
        existing = {(t.account_id, t.external_id) for t in self._transactions if t.external_id}
        inserted = 0
        for t in transactions:
            if t.external_id and (t.account_id, t.external_id) in existing:
                continue
            stored = Transaction(
                id=self._next_txn_id,
                account_id=t.account_id,
                asset_id=t.asset_id,
                type=t.type,
                quantity=t.quantity,
                price=t.price,
                fees=t.fees,
                currency=t.currency,
                executed_at=t.executed_at,
                trade_date=t.trade_date,
                external_id=t.external_id,
                source=t.source,
                note=t.note,
            )
            self._next_txn_id += 1
            self._transactions.append(stored)
            if t.external_id:
                existing.add((t.account_id, t.external_id))
            inserted += 1
        return inserted

    def list_transactions(
        self, account_id: int | None = None, asset_id: int | None = None, since: date | None = None
    ) -> list[Transaction]:
        rows = self._transactions
        if account_id is not None:
            rows = [t for t in rows if t.account_id == account_id]
        if asset_id is not None:
            rows = [t for t in rows if t.asset_id == asset_id]
        if since is not None:
            rows = [t for t in rows if t.trade_date >= since]
        return list(rows)

    def add_manual_transaction(self, transaction: Transaction) -> Transaction:
        self.add_transactions([transaction])
        return self._transactions[-1]

    def update_manual_transaction(self, transaction_id: int, **fields) -> None:
        pass

    def delete_manual_transaction(self, transaction_id: int) -> None:
        self._transactions = [t for t in self._transactions if t.id != transaction_id]

    def delete_snapshots_in_range(self, start: date, end: date) -> None:
        pass

    def upsert_position_snapshot(self, *args, **kwargs) -> None:
        pass

    def upsert_portfolio_snapshot(self, *args, **kwargs) -> None:
        pass

    def get_portfolio_snapshots(self, start: date, end: date) -> list:
        return []

    def get_position_snapshots_totals(self, start: date, end: date, asset_ids: list[int]) -> list[dict]:
        return []

    def get_latest_snapshot(self):
        return None

    def last_snapshot_date(self) -> date | None:
        return None

    def earliest_transaction_date(self, account_id: int | None = None) -> date | None:
        rows = self.list_transactions(account_id=account_id)
        return min((t.trade_date for t in rows), default=None)
