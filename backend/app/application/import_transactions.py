"""Two ways to bring full transaction history in for accounts the live
broker API can't (fully) provide: IBKR's Flex Web Service, and CSV import
for any broker (the fallback for MyInvestor and anything else with no API).

Both funnel into the same idempotent `PortfolioRepo.add_transactions`, keyed
on (account_id, external_id) — re-running an import is always safe.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from app.domain.errors import DomainError
from app.domain.models import AccountSource, Transaction, TransactionType
from app.domain.returns import build_cost_basis
from app.ports.repositories import AssetRepo, PortfolioRepo
from app.ports.statements import StatementPort

from .asset_resolution import resolve_asset
from .dto import ImportPreviewDTO, SyncResultDTO
from .import_fingerprint import assign_external_ids


def _fill_holdings_gaps_from_transactions(portfolio_repo: PortfolioRepo, account_id: int) -> None:
    """Import paths (Flex, CSV) only ever write to `transactions` — unlike a
    live broker sync, they have no separate "current positions" feed. Without
    this, an account that's only ever been imported (never live-synced) shows
    zero positions anywhere in the app, forever.

    Derive a holding from the transaction ledger for any (account, asset) that
    doesn't already have one. Deliberately never overwrites an existing
    holding: a live sync's avg cost comes straight from the broker and is
    authoritative, while a transaction replay is only as complete as the
    imported history (e.g. a Flex Query with a narrow date range) — replacing
    a good broker-derived number with a possibly-partial computed one would
    be a regression, not a fix.
    """
    txns = [t for t in portfolio_repo.list_transactions(account_id=account_id) if t.asset_id is not None]
    by_asset: dict[int, list[Transaction]] = defaultdict(list)
    for t in txns:
        by_asset[t.asset_id].append(t)

    now = datetime.now(timezone.utc)
    for asset_id, asset_txns in by_asset.items():
        if portfolio_repo.get_holding(account_id, asset_id) is not None:
            continue
        state = build_cost_basis(asset_txns)
        if state.quantity == 0:
            continue
        currency = asset_txns[-1].currency
        portfolio_repo.upsert_holding(
            account_id, asset_id, state.quantity, state.avg_cost_price, currency, now, AccountSource.MANUAL
        )


@dataclass(slots=True)
class ParsedRow:
    symbol: str | None
    name: str | None
    type: str
    quantity: str
    price: str
    fees: str
    currency: str
    executed_at: str  # ISO date/datetime string
    isin: str | None
    external_id: str | None
    warnings: list[str]
    errors: list[str] = field(default_factory=list)  # non-empty -> row is never written, only shown
    row_number: int = 0  # 1-based position in the source file, for user-facing messages


@dataclass(slots=True)
class ParsedStatement:
    """A parser's full result for one uploaded file: the rows it could
    extract, plus enough about *how* it read the file that a user can tell
    us what went wrong when something's missing — see the "detected headers
    / could not map" panel in the Accounts page."""

    rows: list[ParsedRow]
    file_format: str  # e.g. "CSV, cp1252, ';'-delimited" or "XLSX, sheet(s): Fondos"
    detected_headers: list[str]
    column_mapping: dict[str, str]  # field -> the header that won, for the fields we could map
    unmapped_columns: list[str]  # required fields we couldn't map — non-empty means rows is []
    notices: list[str]  # file-level information, e.g. "no type column — every row treated as BUY"
    skipped_count: int = 0  # rows deliberately excluded (rejected orders, out-of-scope cash movements)


class ImportStatementUseCase:
    """IBKR Flex Web Service: full history for one account."""

    def __init__(self, statement_port: StatementPort, account_id: int, asset_repo: AssetRepo, portfolio_repo: PortfolioRepo) -> None:
        self.statement_port = statement_port
        self.account_id = account_id
        self.asset_repo = asset_repo
        self.portfolio_repo = portfolio_repo

    def execute(self, since: date | None = None) -> SyncResultDTO:
        broker_transactions = self.statement_port.fetch_transactions(since)
        transactions: list[Transaction] = []
        for t in broker_transactions:
            asset_id = None
            if t.symbol:
                asset = resolve_asset(self.asset_repo, t.symbol, t.symbol, t.currency, t.ibkr_conid, t.isin)
                asset_id = asset.id
            transactions.append(
                Transaction(
                    id=None,
                    account_id=self.account_id,
                    asset_id=asset_id,
                    type=TransactionType(t.type),
                    quantity=t.quantity,
                    price=t.price,
                    fees=t.fees,
                    currency=t.currency,
                    executed_at=t.executed_at,
                    trade_date=t.executed_at.date(),
                    external_id=t.external_id,
                    source=AccountSource.API,
                )
            )
        added = self.portfolio_repo.add_transactions(transactions)
        _fill_holdings_gaps_from_transactions(self.portfolio_repo, self.account_id)
        return SyncResultDTO(
            broker_key="interactive_brokers_flex",
            accounts_synced=1,
            holdings_synced=0,
            transactions_added=added,
        )


class CsvImportUseCase:
    """Statement import (CSV or XLSX) — the manual-entry fallback for any
    broker without a usable API (MyInvestor today). `preview` and `commit`
    both start from `_prepare`, so a row is always counted/skipped the same
    way in both — the id assignment that makes re-imports safe must never
    diverge between what the user previews and what actually gets written.
    """

    def __init__(self, asset_repo: AssetRepo, portfolio_repo: PortfolioRepo) -> None:
        self.asset_repo = asset_repo
        self.portfolio_repo = portfolio_repo

    def _prepare(self, statement: ParsedStatement, account_id: int) -> tuple[list[ParsedRow], set[str]]:
        assign_external_ids(statement.rows)
        existing_external_ids = {
            t.external_id for t in self.portfolio_repo.list_transactions(account_id=account_id) if t.external_id
        }
        return statement.rows, existing_external_ids

    def preview(self, statement: ParsedStatement, account_id: int) -> ImportPreviewDTO:
        rows, existing_external_ids = self._prepare(statement, account_id)

        unresolved: set[str] = set()
        preview_rows = []
        already_imported = 0
        invalid = 0
        for row in rows:
            is_dup = bool(row.external_id and row.external_id in existing_external_ids)
            if row.errors:
                invalid += 1
            elif is_dup:
                already_imported += 1
            elif row.symbol and not self.asset_repo.get_by_symbol(row.symbol):
                unresolved.add(row.symbol)
            preview_rows.append(
                {
                    "row_number": row.row_number,
                    "symbol": row.symbol,
                    "name": row.name,
                    "isin": row.isin,
                    "type": row.type,
                    "quantity": row.quantity,
                    "price": row.price,
                    "currency": row.currency,
                    "executed_at": row.executed_at,
                    "duplicate": is_dup,
                    "warnings": row.warnings,
                    "errors": row.errors,
                }
            )
        return ImportPreviewDTO(
            total_rows=len(rows),
            new_transactions=len(rows) - invalid - already_imported,
            duplicate_transactions=already_imported,
            invalid_rows=invalid,
            skipped_rows=statement.skipped_count,
            unresolved_symbols=sorted(unresolved),
            unmapped_columns=statement.unmapped_columns,
            column_mapping=statement.column_mapping,
            detected_headers=statement.detected_headers,
            file_format=statement.file_format,
            notices=statement.notices,
            rows=preview_rows,
        )

    def commit(self, statement: ParsedStatement, account_id: int) -> SyncResultDTO:
        from decimal import Decimal

        rows, existing_external_ids = self._prepare(statement, account_id)

        transactions: list[Transaction] = []
        for row in rows:
            if row.errors:
                continue
            if row.external_id is None:
                raise DomainError(f"import row {row.row_number} has no external_id — dedup would be unsafe")
            if row.external_id in existing_external_ids:
                continue
            asset_id = None
            if row.symbol:
                asset = resolve_asset(
                    self.asset_repo, row.symbol, row.name or row.symbol, row.currency, None, row.isin
                )
                asset_id = asset.id
            executed_at = date.fromisoformat(row.executed_at[:10])
            transactions.append(
                Transaction(
                    id=None,
                    account_id=account_id,
                    asset_id=asset_id,
                    type=TransactionType(row.type),
                    quantity=Decimal(row.quantity),
                    price=Decimal(row.price),
                    fees=Decimal(row.fees or "0"),
                    currency=row.currency,
                    executed_at=executed_at,  # type: ignore[arg-type]
                    trade_date=executed_at,
                    external_id=row.external_id,
                    source=AccountSource.MANUAL,
                )
            )
        added = self.portfolio_repo.add_transactions(transactions)
        _fill_holdings_gaps_from_transactions(self.portfolio_repo, account_id)
        return SyncResultDTO(
            broker_key="csv_import", accounts_synced=1, holdings_synced=0, transactions_added=added
        )
