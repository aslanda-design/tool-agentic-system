"""Interactive Brokers adapter via `ib_async` (the maintained fork of the
now-unmaintained `ib_insync`). Requires TWS or IB Gateway running locally
with API access enabled (Configure > API > Settings > Enable ActiveX and
Socket Clients). Paper trading port is 7497, live is 7496.

This is the live-session view of the account: current positions, NAV, and
cash. It is NOT a source of full transaction history — `ib.fills()` /
`reqExecutions` only return executions since midnight, and IB Gateway
cannot widen that window (only TWS's Trade Log setting can, up to 7 days).
Full history comes from `ibkr_flex.py` instead.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from ib_async import IB

from app.domain.errors import BrokerConnectionError
from app.ports.broker import BrokerAccount, BrokerCashBalance, BrokerHolding, BrokerPort, BrokerSnapshot, BrokerTransaction

logger = logging.getLogger(__name__)


class IBKRAdapter(BrokerPort):
    broker_key = "interactive_brokers"

    def __init__(self, host: str, port: int, client_id: int) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id

    def fetch_snapshot(self, since: date | None = None) -> BrokerSnapshot:
        logger.info("Connecting to TWS/IB Gateway at %s:%s (clientId=%s)", self.host, self.port, self.client_id)
        ib = IB()
        try:
            ib.connect(self.host, self.port, clientId=self.client_id, timeout=10)
        except Exception as exc:
            logger.warning("IBKR connection to %s:%s failed: %s", self.host, self.port, exc)
            raise BrokerConnectionError(
                "Couldn't reach Interactive Brokers. Make sure TWS or IB Gateway is running, "
                "you're logged in, and API access is enabled (Configure > API > Settings)."
            ) from exc

        logger.info("Connected to IBKR at %s:%s", self.host, self.port)
        try:
            snapshot = self._read_snapshot(ib)
            logger.info(
                "IBKR snapshot: %d account(s), %d holding(s), %d cash balance(s), %d transaction(s)",
                len(snapshot.accounts),
                len(snapshot.holdings),
                len(snapshot.cash),
                len(snapshot.transactions),
            )
            return snapshot
        finally:
            ib.disconnect()

    def _read_snapshot(self, ib: IB) -> BrokerSnapshot:
        snapshot = BrokerSnapshot()
        for account_id in ib.managedAccounts():
            snapshot.accounts.append(BrokerAccount(external_id=account_id, name=account_id, currency="USD"))

            for item in ib.portfolio(account=account_id):
                contract = item.contract
                snapshot.holdings.append(
                    BrokerHolding(
                        account_external_id=account_id,
                        symbol=contract.symbol,
                        name=contract.localSymbol or contract.symbol,
                        quantity=Decimal(str(item.position)),
                        avg_cost_price=Decimal(str(item.averageCost)),
                        last_price=Decimal(str(item.marketPrice)),
                        currency=contract.currency or "USD",
                        ibkr_conid=str(contract.conId) if contract.conId else None,
                    )
                )

            for value in ib.accountValues(account_id):
                if value.tag == "CashBalance" and value.currency != "BASE":
                    snapshot.cash.append(
                        BrokerCashBalance(
                            account_external_id=account_id,
                            currency=value.currency,
                            amount=Decimal(str(value.value)),
                        )
                    )

            for fill in ib.fills():
                if fill.execution.acctNumber != account_id:
                    continue
                commission = fill.commissionReport.commission if fill.commissionReport else 0
                snapshot.transactions.append(
                    BrokerTransaction(
                        account_external_id=account_id,
                        external_id=str(fill.execution.execId),
                        symbol=fill.contract.symbol,
                        type="BUY" if fill.execution.side == "BOT" else "SELL",
                        quantity=Decimal(str(fill.execution.shares)),
                        price=Decimal(str(fill.execution.price)),
                        fees=Decimal(str(abs(commission))),
                        currency=fill.contract.currency or "USD",
                        executed_at=fill.execution.time,
                        ibkr_conid=str(fill.contract.conId) if fill.contract.conId else None,
                    )
                )

        return snapshot
