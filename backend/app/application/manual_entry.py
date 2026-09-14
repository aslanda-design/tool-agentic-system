"""Manual data entry — the path for any broker with no usable API
(MyInvestor today). Manual holdings/transactions land in the exact same
tables as API-sourced ones (just `source='manual'`); nothing downstream
branches on where the data came from.

Also covers asset-mapping: confirming which market-data ticker (yfinance
symbol) a locally-tracked asset corresponds to, for assets flagged
`needs_mapping` during ingestion.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.domain.models import AccountSource, Asset, IdentifierScheme, Transaction, TransactionType
from app.ports.market_data import MarketDataPort
from app.ports.repositories import AssetRepo, PortfolioRepo

from .asset_resolution import resolve_asset
from .import_transactions import _fill_holdings_gaps_from_transactions


class ManualEntryUseCase:
    def __init__(self, asset_repo: AssetRepo, portfolio_repo: PortfolioRepo) -> None:
        self.asset_repo = asset_repo
        self.portfolio_repo = portfolio_repo

    def upsert_holding(
        self,
        account_id: int,
        symbol: str,
        name: str,
        currency: str,
        quantity: Decimal,
        avg_cost_price: Decimal,
        isin: str | None = None,
    ) -> None:
        asset = resolve_asset(self.asset_repo, symbol, name, currency, isin=isin)
        self.portfolio_repo.upsert_holding(
            account_id, asset.id, quantity, avg_cost_price, currency, datetime.now(timezone.utc), AccountSource.MANUAL
        )

    def add_transaction(
        self,
        account_id: int,
        symbol: str | None,
        type_: TransactionType,
        quantity: Decimal,
        price: Decimal,
        fees: Decimal,
        currency: str,
        executed_at: date,
        note: str = "",
    ) -> Transaction:
        asset_id = None
        if symbol:
            asset_id = resolve_asset(self.asset_repo, symbol, symbol, currency).id
        txn = Transaction(
            id=None,
            account_id=account_id,
            asset_id=asset_id,
            type=type_,
            quantity=quantity,
            price=price,
            fees=fees,
            currency=currency,
            executed_at=datetime.combine(executed_at, datetime.min.time(), tzinfo=timezone.utc),
            trade_date=executed_at,
            external_id=None,
            source=AccountSource.MANUAL,
            note=note,
        )
        result = self.portfolio_repo.add_manual_transaction(txn)
        _fill_holdings_gaps_from_transactions(self.portfolio_repo, account_id)
        return result

    def update_transaction(self, transaction_id: int, **fields) -> None:
        self.portfolio_repo.update_manual_transaction(transaction_id, **fields)

    def delete_transaction(self, transaction_id: int) -> None:
        self.portfolio_repo.delete_manual_transaction(transaction_id)

    def update_asset(self, asset_id: int, symbol: str, name: str, isin: str | None) -> Asset:
        """Correct an asset's identity (rename, fix a mis-mapped ISIN, etc).
        Deliberately a full overwrite of these three fields, not a partial
        patch — an edit form always knows and resubmits its current values,
        so there's no "leave unchanged" case that needs None to mean
        something other than "clear it" (relevant for `isin`)."""
        return self.asset_repo.update(asset_id, symbol, name, isin)

    def delete_asset(self, asset_id: int) -> dict:
        """Permanently remove an asset and everything recorded against it —
        e.g. to undo importing/mapping something to the wrong account or the
        wrong instrument entirely. Irreversible; the route layer is
        responsible for rebuilding snapshots afterwards since this can
        remove transactions."""
        transactions_deleted = self.portfolio_repo.delete_transactions_for_asset(asset_id)
        holdings_deleted = self.portfolio_repo.delete_holdings_for_asset(asset_id)
        self.asset_repo.delete(asset_id)
        return {"transactions_deleted": transactions_deleted, "holdings_deleted": holdings_deleted}


class MapAssetUseCase:
    """Confirms the yfinance ticker for an asset flagged `needs_mapping`."""

    def __init__(self, asset_repo: AssetRepo, market_data: MarketDataPort | None = None) -> None:
        self.asset_repo = asset_repo
        self.market_data = market_data

    def list_unmapped(self):
        return self.asset_repo.list_needing_mapping()

    def suggest_tickers(self, asset_id: int):
        """Candidate yfinance tickers for an unmapped asset, found by searching
        its name/ISIN — the same market-data search the Search page uses, but
        deliberately NOT persisted as a new Asset (unlike SearchAssetsUseCase):
        these are throwaway suggestions for a picker, not something to track."""
        if self.market_data is None:
            return []
        asset = self.asset_repo.get(asset_id)
        if asset is None:
            return []
        # `symbol` is the actual traded ticker; `name` is only reliable for
        # assets created by search (a real display name). For API-synced
        # assets it's often IBKR's contract.localSymbol — an internal code
        # (e.g. "PPFB" for a holding actually ticked "EGLN") that searches
        # well but finds the wrong listing. Prefer the ticker over it.
        query = asset.isin or asset.symbol or asset.name
        return self.market_data.search(query)

    def resolve(self, asset_id: int, yfinance_symbol: str) -> None:
        self.asset_repo.add_identifier(asset_id, IdentifierScheme.YFINANCE, yfinance_symbol)
        self.asset_repo.set_needs_mapping(asset_id, False)
