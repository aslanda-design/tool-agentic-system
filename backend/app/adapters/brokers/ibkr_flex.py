"""IBKR Flex Web Service — full historical trades/cash transactions,
independent of any TWS/Gateway session. Setup (one-time, in Account
Management > Reports > Flex Queries): create an "Activity Flex Query"
including at least Trades (Symbol, ISIN, ConID, Trade Date/Time, Quantity,
Trade Price, Commission, Currency, Buy/Sell, Transaction ID) and Cash
Transactions (Type, Amount, Currency, Date/Time, Transaction ID), then
generate a Flex Web Service token under Settings > API > Flex Web Service.
Also include Description and Listing Exchange on both sections if available
— optional (missing ones are simply read as None), but they feed the
security resolver's context (see plans/agentic_asset_mapping.md Phase 2)
and make an unmapped asset much easier to resolve automatically.

The service is a two-step async HTTP flow: SendRequest returns a reference
code, then GetStatement polls for the generated XML (it isn't instant).

CAVEAT: Flex Query column names are user-configurable in IBKR's UI. The
attribute names read below match IBKR's default Activity Flex XML, but if
your query is configured with different columns, adjust `_parse_trade` /
`_parse_cash_transaction` accordingly — verify against one real export
before relying on this for your invested-capital numbers.
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

from app.domain.errors import BrokerConnectionError
from app.ports.broker import BrokerTransaction
from app.ports.statements import StatementPort

logger = logging.getLogger(__name__)

SEND_REQUEST_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
STATEMENT_NOT_READY_CODE = "1019"
MAX_POLL_ATTEMPTS = 6
POLL_DELAY_SECONDS = 5


class IBKRFlexAdapter(StatementPort):
    def __init__(self, token: str, query_id: str, account_external_id: str) -> None:
        self.token = token
        self.query_id = query_id
        self.account_external_id = account_external_id

    def fetch_transactions(self, since: date | None = None) -> list[BrokerTransaction]:
        xml_bytes = self._fetch_statement_xml()
        root = ET.fromstring(xml_bytes)

        trade_elements = root.findall(".//Trade")
        cash_elements = root.findall(".//CashTransaction")
        logger.info(
            "Flex statement parsed: %d <Trade> element(s), %d <CashTransaction> element(s) found",
            len(trade_elements),
            len(cash_elements),
        )

        transactions: list[BrokerTransaction] = []
        skipped = 0
        for trade in trade_elements:
            txn = self._parse_trade(trade)
            if txn is None:
                skipped += 1
                continue
            if since is None or txn.executed_at.date() >= since:
                transactions.append(txn)
        for cash in cash_elements:
            txn = self._parse_cash_transaction(cash)
            if txn is None:
                skipped += 1
                continue
            if since is None or txn.executed_at.date() >= since:
                transactions.append(txn)

        logger.info(
            "Flex statement yielded %d transaction(s) (%d skipped — parse failure or unrecognized type)",
            len(transactions),
            skipped,
        )
        return transactions

    def _fetch_statement_xml(self) -> bytes:
        with httpx.Client(timeout=30) as client:
            logger.info("Requesting Flex statement (query_id=%s)", self.query_id)
            send_resp = client.get(SEND_REQUEST_URL, params={"t": self.token, "q": self.query_id, "v": "3"})
            send_resp.raise_for_status()
            send_root = ET.fromstring(send_resp.content)
            status = send_root.findtext("Status")
            if status != "Success":
                error = send_root.findtext("ErrorMessage") or "Unknown error requesting Flex statement"
                logger.warning("Flex SendRequest failed (query_id=%s): %s", self.query_id, error)
                raise BrokerConnectionError(f"IBKR Flex request failed: {error}")
            reference_code = send_root.findtext("ReferenceCode")
            statement_url = send_root.findtext("Url")
            logger.info("Flex SendRequest accepted, reference_code=%s — polling for statement", reference_code)

            for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
                time.sleep(POLL_DELAY_SECONDS)
                stmt_resp = client.get(
                    statement_url, params={"t": self.token, "q": reference_code, "v": "3"}
                )
                stmt_resp.raise_for_status()
                if b"<FlexStatementResponse>" in stmt_resp.content and STATEMENT_NOT_READY_CODE.encode() in stmt_resp.content:
                    logger.info("Flex statement not ready yet (poll attempt %d/%d)", attempt, MAX_POLL_ATTEMPTS)
                    continue
                logger.info("Flex statement received (%d bytes) after %d poll attempt(s)", len(stmt_resp.content), attempt)
                return stmt_resp.content

        logger.warning("Flex statement (query_id=%s) wasn't ready after %d attempts", self.query_id, MAX_POLL_ATTEMPTS)
        raise BrokerConnectionError("IBKR Flex statement wasn't ready in time — try again shortly.")

    def _parse_trade(self, el: ET.Element) -> BrokerTransaction | None:
        try:
            quantity = Decimal(el.get("quantity", "0"))
            side = (el.get("buySell") or "").upper()
            executed_at = _parse_datetime(el.get("tradeDate", ""), el.get("tradeTime", "000000"))
            return BrokerTransaction(
                account_external_id=self.account_external_id,
                external_id=el.get("transactionID") or el.get("tradeID") or "",
                symbol=el.get("symbol"),
                type="BUY" if side == "BUY" else "SELL",
                quantity=abs(quantity),
                price=Decimal(el.get("tradePrice", "0")),
                fees=abs(Decimal(el.get("ibCommission", "0"))),
                currency=(el.get("currency") or "USD").upper(),
                executed_at=executed_at,
                ibkr_conid=el.get("conid"),
                isin=el.get("isin"),
                exchange=el.get("listingExchange") or None,
                name=el.get("description") or None,
            )
        except (InvalidOperation, ValueError, TypeError) as exc:
            logger.warning("Skipping unparseable <Trade> element (%s): %s", el.attrib, exc)
            return None

    def _parse_cash_transaction(self, el: ET.Element) -> BrokerTransaction | None:
        raw_type = (el.get("type") or "").lower()
        try:
            amount = Decimal(el.get("amount", "0"))
        except InvalidOperation as exc:
            logger.warning("Skipping <CashTransaction> with unparseable amount (%s): %s", el.attrib, exc)
            return None

        if "dividend" in raw_type:
            txn_type = "DIVIDEND"
        elif "interest" in raw_type:
            txn_type = "INTEREST"
        elif "deposit" in raw_type or "withdrawal" in raw_type:
            txn_type = "DEPOSIT" if amount >= 0 else "WITHDRAWAL"
        elif "fee" in raw_type or "withholding" in raw_type or "tax" in raw_type:
            txn_type = "FEE"
        else:
            logger.info("Skipping <CashTransaction> with unrecognized type %r", raw_type)
            return None  # unrecognized cash transaction type — skip rather than guess

        try:
            executed_at = _parse_datetime(el.get("dateTime", el.get("settleDate", "")), "000000")
        except (ValueError, TypeError) as exc:
            logger.warning("Skipping <CashTransaction> with unparseable date (%s): %s", el.attrib, exc)
            return None
        return BrokerTransaction(
            account_external_id=self.account_external_id,
            external_id=el.get("transactionID") or "",
            symbol=el.get("symbol") or None,
            type=txn_type,
            quantity=Decimal("1"),
            price=abs(amount),
            fees=Decimal("0"),
            currency=(el.get("currency") or "USD").upper(),
            executed_at=executed_at,
            ibkr_conid=el.get("conid"),
            isin=el.get("isin"),
            exchange=el.get("listingExchange") or None,
            name=el.get("description") or None,
        )


def _parse_datetime(date_part: str, time_part: str) -> datetime:
    # IBKR's date/time format depends on the Flex Query's configured Date/Time
    # Format setting — either compact (YYYYMMDD, HHMMSS) or with separators
    # (YYYY-MM-DD, HH:MM:SS). Strip non-digits so either works.
    digits = "".join(ch for ch in date_part if ch.isdigit())[:8]
    time_digits = "".join(ch for ch in (time_part or "") if ch.isdigit())[:6].ljust(6, "0")
    return datetime.strptime(digits + time_digits, "%Y%m%d%H%M%S")
