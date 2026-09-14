"""Pull live state from a broker's API and persist it. Handles the identity
problem (broker contract -> local Asset) via IBKR_CONID/ISIN/symbol, in that
order of trust; anything it can't confidently resolve is flagged
`needs_mapping` for the user to fix in the UI."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from app.domain.models import AccountSource, Transaction, TransactionType
from app.ports.broker import BrokerHolding, BrokerPort
from app.ports.repositories import AssetRepo, PortfolioRepo

from .asset_resolution import resolve_asset
from .dto import SyncResultDTO


class SyncBrokerUseCase:
    def __init__(self, broker: BrokerPort, asset_repo: AssetRepo, portfolio_repo: PortfolioRepo) -> None:
        self.broker = broker
        self.asset_repo = asset_repo
        self.portfolio_repo = portfolio_repo

    def execute(self) -> SyncResultDTO:
        snapshot = self.broker.fetch_snapshot()
        now = datetime.now(timezone.utc)

        accounts_by_ext_id = {}
        for acc in snapshot.accounts:
            account = self.portfolio_repo.get_or_create_account(
                self.broker.broker_key, acc.external_id, acc.name, acc.currency, AccountSource.API
            )
            accounts_by_ext_id[acc.external_id] = account
        accounts_synced = len(accounts_by_ext_id)

        holdings_by_account: dict[str, list[BrokerHolding]] = defaultdict(list)
        for h in snapshot.holdings:
            holdings_by_account[h.account_external_id].append(h)

        holdings_synced = 0
        for ext_id, holdings in holdings_by_account.items():
            account = accounts_by_ext_id.get(ext_id) or self.portfolio_repo.get_or_create_account(
                self.broker.broker_key, ext_id, ext_id, holdings[0].currency, AccountSource.API
            )
            self.portfolio_repo.replace_holdings_for_account(account.id, AccountSource.API)
            for h in holdings:
                asset = resolve_asset(self.asset_repo, h.symbol, h.name, h.currency, h.ibkr_conid, h.isin)
                self.portfolio_repo.upsert_holding(
                    account.id, asset.id, h.quantity, h.avg_cost_price, h.currency, now, AccountSource.API
                )
                holdings_synced += 1

        for cash in snapshot.cash:
            account = accounts_by_ext_id.get(cash.account_external_id)
            if account is None:
                continue
            self.portfolio_repo.upsert_cash_balance(account.id, cash.currency, cash.amount, now)

        transactions: list[Transaction] = []
        for t in snapshot.transactions:
            account = accounts_by_ext_id.get(t.account_external_id)
            if account is None:
                continue
            asset_id = None
            if t.symbol:
                asset = resolve_asset(self.asset_repo, t.symbol, t.symbol, t.currency, t.ibkr_conid, t.isin)
                asset_id = asset.id
            transactions.append(
                Transaction(
                    id=None,
                    account_id=account.id,
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
        transactions_added = self.portfolio_repo.add_transactions(transactions)

        return SyncResultDTO(
            broker_key=self.broker.broker_key,
            accounts_synced=accounts_synced,
            holdings_synced=holdings_synced,
            transactions_added=transactions_added,
        )
