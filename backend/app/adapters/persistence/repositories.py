"""SQLAlchemy implementations of the three persistence ports. Every method
that writes calls `session.flush()` (so generated ids are available to the
caller) but never `session.commit()` — the API layer owns the transaction
boundary and commits once per request after a use case completes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.errors import AssetConflictError, AssetNotFoundError
from app.domain.listings import (
    AgentRunRecord,
    Candidate,
    ListingInfo,
    ResolutionContext,
    ResolutionDTO,
    ResolutionStatus,
)
from app.domain.models import (
    Account,
    AccountSource,
    Asset,
    AssetClass,
    ChatMessage,
    ChatSession,
    IdentifierScheme,
    Note,
    PortfolioSnapshotPoint,
    Transaction,
    TransactionType,
)
from app.domain.quant.types import BacktestResult, QuantRunRecord
from app.ports.chat import ChatRepo
from app.ports.notes import NoteRepo
from app.ports.repositories import (
    AssetRepo,
    MarketDataRepo,
    PortfolioRepo,
    QuantRunRepo,
    ResolutionRepo,
)

from .orm import (
    AccountORM,
    AgentRunORM,
    AiNoteORM,
    AssetIdentifierORM,
    AssetORM,
    AssetResolutionORM,
    CashBalanceORM,
    ChatMessageORM,
    ChatSessionORM,
    FxRateORM,
    HoldingORM,
    PortfolioSnapshotORM,
    PositionSnapshotORM,
    PriceBarORM,
    QuantRunORM,
    QuoteORM,
    ResolutionCandidateORM,
    TransactionORM,
)


def _asset_from_orm(row: AssetORM) -> Asset:
    return Asset(
        id=row.id,
        symbol=row.symbol,
        name=row.name,
        asset_class=AssetClass(row.asset_class),
        currency=row.currency,
        exchange=row.exchange,
        isin=row.isin,
        needs_mapping=row.needs_mapping,
        share_class_figi=row.share_class_figi,
    )


def _account_from_orm(row: AccountORM) -> Account:
    return Account(
        id=row.id,
        broker_key=row.broker_key,
        external_id=row.external_id,
        name=row.name,
        currency=row.currency,
        source=AccountSource(row.source),
    )


def _candidate_from_orm(row: ResolutionCandidateORM) -> Candidate:
    info = None
    if row.currency is not None or row.last_close is not None:
        info = ListingInfo(
            symbol=row.symbol,
            name=row.name,
            currency=row.currency,
            quote_type=None,  # not persisted separately — see asset_class
            last_close=Decimal(row.last_close) if row.last_close is not None else None,
            last_trade_date=row.last_trade_date,
            avg_volume=Decimal(row.avg_volume) if row.avg_volume is not None else None,
        )
    return Candidate(
        symbol=row.symbol,
        found_by=set(row.found_by.split(",")) if row.found_by else set(),
        info=info,
        mic=row.mic,
        asset_class=row.asset_class,
        features=dict(row.features or {}),
        score=row.score,
        id=row.id,
    )


def _resolution_from_orm(row: AssetResolutionORM) -> ResolutionDTO:
    selected_id = next((c.id for c in row.candidates if c.selected), None)
    return ResolutionDTO(
        id=row.id,
        asset_id=row.asset_id,
        context=ResolutionContext(
            asset_id=row.asset_id,
            isin=row.isin,
            broker_symbol=row.broker_symbol,
            broker_name=row.broker_name,
            broker_exchange=row.broker_exchange,
            broker_mic=row.broker_mic,
            currency=row.currency,
            broker_key=row.broker_key,
        ),
        status=ResolutionStatus(row.status),
        decided_by=row.decided_by,
        note=row.note,
        scorer_version=row.scorer_version,
        candidates=[_candidate_from_orm(c) for c in row.candidates],
        selected_candidate_id=selected_id,
    )


def _transaction_from_orm(row: TransactionORM) -> Transaction:
    return Transaction(
        id=row.id,
        account_id=row.account_id,
        asset_id=row.asset_id,
        type=TransactionType(row.type),
        quantity=Decimal(row.quantity),
        price=Decimal(row.price),
        fees=Decimal(row.fees),
        currency=row.currency,
        executed_at=row.executed_at,
        trade_date=row.trade_date,
        external_id=row.external_id,
        source=AccountSource(row.source),
        note=row.note,
    )


class SqlAssetRepo(AssetRepo):
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, asset_id: int) -> Asset | None:
        row = self.session.get(AssetORM, asset_id)
        return _asset_from_orm(row) if row else None

    def get_by_symbol(self, symbol: str) -> Asset | None:
        row = self.session.execute(select(AssetORM).where(AssetORM.symbol == symbol)).scalar_one_or_none()
        return _asset_from_orm(row) if row else None

    def find_by_identifier(self, scheme: IdentifierScheme, value: str) -> Asset | None:
        ident = self.session.get(AssetIdentifierORM, {"scheme": scheme.value, "value": value})
        if ident is None:
            return None
        return _asset_from_orm(ident.asset)

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
        row = AssetORM(
            symbol=symbol,
            name=name,
            asset_class=asset_class.value,
            currency=currency.upper(),
            exchange=exchange,
            isin=isin,
            needs_mapping=needs_mapping,
        )
        self.session.add(row)
        self.session.flush()
        return _asset_from_orm(row)

    def add_identifier(self, asset_id: int, scheme: IdentifierScheme, value: str) -> None:
        existing = self.session.get(AssetIdentifierORM, {"scheme": scheme.value, "value": value})
        if existing is not None:
            return
        self.session.add(AssetIdentifierORM(scheme=scheme.value, value=value, asset_id=asset_id))
        self.session.flush()

    def set_needs_mapping(self, asset_id: int, needs_mapping: bool) -> None:
        row = self.session.get(AssetORM, asset_id)
        if row is not None:
            row.needs_mapping = needs_mapping
            self.session.flush()

    def set_isin(self, asset_id: int, isin: str) -> None:
        row = self.session.get(AssetORM, asset_id)
        if row is not None and not row.isin:
            existing = self.session.execute(select(AssetORM).where(AssetORM.isin == isin)).scalar_one_or_none()
            if existing is None:  # isin has a unique constraint — don't collide with another asset
                row.isin = isin
                self.session.flush()

    def list_needing_mapping(self) -> list[Asset]:
        rows = self.session.execute(select(AssetORM).where(AssetORM.needs_mapping.is_(True))).scalars()
        return [_asset_from_orm(r) for r in rows]

    def get_identifier_value(self, asset_id: int, scheme: IdentifierScheme) -> str | None:
        row = self.session.execute(
            select(AssetIdentifierORM).where(
                AssetIdentifierORM.asset_id == asset_id, AssetIdentifierORM.scheme == scheme.value
            )
        ).scalar_one_or_none()
        return row.value if row else None

    def list_assets_with_scheme(self, scheme: IdentifierScheme) -> list[tuple[Asset, str]]:
        rows = self.session.execute(
            select(AssetIdentifierORM).where(AssetIdentifierORM.scheme == scheme.value)
        ).scalars()
        return [(_asset_from_orm(r.asset), r.value) for r in rows]

    def search_local(self, query: str, limit: int = 20) -> list[Asset]:
        pattern = f"%{query}%"
        rows = self.session.execute(
            select(AssetORM)
            .where((AssetORM.symbol.ilike(pattern)) | (AssetORM.name.ilike(pattern)))
            .limit(limit)
        ).scalars()
        return [_asset_from_orm(r) for r in rows]

    def list_all(self) -> list[Asset]:
        rows = self.session.execute(select(AssetORM)).scalars()
        return [_asset_from_orm(r) for r in rows]

    def update(self, asset_id: int, symbol: str, name: str, isin: str | None) -> Asset:
        row = self.session.get(AssetORM, asset_id)
        if row is None:
            raise AssetNotFoundError(f"Asset {asset_id} not found")

        if symbol != row.symbol:
            conflict = self.session.execute(
                select(AssetORM).where(AssetORM.symbol == symbol, AssetORM.id != asset_id)
            ).scalar_one_or_none()
            if conflict is not None:
                raise AssetConflictError(f"Another asset already uses symbol {symbol!r}")

        if isin and isin != row.isin:
            conflict = self.session.execute(
                select(AssetORM).where(AssetORM.isin == isin, AssetORM.id != asset_id)
            ).scalar_one_or_none()
            if conflict is not None:
                raise AssetConflictError(f"Another asset already uses ISIN {isin!r}")

        row.symbol = symbol
        row.name = name
        row.isin = isin or None
        self.session.flush()
        return _asset_from_orm(row)

    def delete(self, asset_id: int) -> None:
        row = self.session.get(AssetORM, asset_id)
        if row is None:
            raise AssetNotFoundError(f"Asset {asset_id} not found")
        self.session.delete(row)
        self.session.flush()

    def apply_listing(
        self,
        asset_id: int,
        yahoo_symbol: str,
        currency: str,
        mic: str | None,
        asset_class: AssetClass | None,
        share_class_figi: str | None,
        name: str | None = None,
    ) -> None:
        row = self.session.get(AssetORM, asset_id)
        if row is None:
            raise AssetNotFoundError(f"Asset {asset_id} not found")

        conflict = self.session.get(AssetIdentifierORM, {"scheme": IdentifierScheme.YFINANCE.value, "value": yahoo_symbol})
        if conflict is not None and conflict.asset_id != asset_id:
            raise AssetConflictError(
                f"{yahoo_symbol!r} is already mapped to asset {conflict.asset_id} — merge needed"
            )

        existing_yfinance = self.session.execute(
            select(AssetIdentifierORM).where(
                AssetIdentifierORM.asset_id == asset_id,
                AssetIdentifierORM.scheme == IdentifierScheme.YFINANCE.value,
            )
        ).scalars().all()
        symbol_changed = not any(i.value == yahoo_symbol for i in existing_yfinance)
        for ident in existing_yfinance:
            if ident.value != yahoo_symbol:
                self.session.delete(ident)

        if symbol_changed:
            # Stale listing data — prices/quotes belong to whatever the OLD
            # symbol was, and are meaningless once the listing changes.
            self.session.query(PriceBarORM).filter(PriceBarORM.asset_id == asset_id).delete()
            self.session.query(QuoteORM).filter(QuoteORM.asset_id == asset_id).delete()
            if conflict is None:
                self.session.add(
                    AssetIdentifierORM(scheme=IdentifierScheme.YFINANCE.value, value=yahoo_symbol, asset_id=asset_id)
                )

        row.currency = currency.upper()
        row.exchange = mic
        row.needs_mapping = False
        if asset_class is not None:
            row.asset_class = asset_class.value
        if share_class_figi is not None:
            row.share_class_figi = share_class_figi
        if name is not None:
            row.name = name
        self.session.flush()


class SqlPortfolioRepo(PortfolioRepo):
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_or_create_account(
        self, broker_key: str, external_id: str, name: str, currency: str, source: AccountSource
    ) -> Account:
        row = self.session.execute(
            select(AccountORM).where(AccountORM.broker_key == broker_key, AccountORM.external_id == external_id)
        ).scalar_one_or_none()
        if row is None:
            row = AccountORM(
                broker_key=broker_key,
                external_id=external_id,
                name=name,
                currency=currency.upper(),
                source=AccountSource(source).value,
            )
            self.session.add(row)
            self.session.flush()
        return _account_from_orm(row)

    def list_accounts(self) -> list[Account]:
        rows = self.session.execute(select(AccountORM)).scalars()
        return [_account_from_orm(r) for r in rows]

    def get_account(self, account_id: int) -> Account | None:
        row = self.session.get(AccountORM, account_id)
        return _account_from_orm(row) if row else None

    def upsert_holding(
        self,
        account_id: int,
        asset_id: int,
        quantity: Decimal,
        avg_cost_price: Decimal,
        cost_currency: str,
        as_of: datetime,
        source: AccountSource,
    ) -> None:
        row = self.session.execute(
            select(HoldingORM).where(HoldingORM.account_id == account_id, HoldingORM.asset_id == asset_id)
        ).scalar_one_or_none()
        if row is None:
            row = HoldingORM(account_id=account_id, asset_id=asset_id)
            self.session.add(row)
        row.quantity = quantity
        row.avg_cost_price = avg_cost_price
        row.cost_currency = cost_currency.upper()
        row.as_of = as_of
        row.source = AccountSource(source).value
        self.session.flush()

    def replace_holdings_for_account(self, account_id: int, source: AccountSource) -> None:
        self.session.query(HoldingORM).filter(HoldingORM.account_id == account_id).delete()
        self.session.flush()

    def delete_holding(self, account_id: int, asset_id: int) -> None:
        self.session.query(HoldingORM).filter(
            HoldingORM.account_id == account_id, HoldingORM.asset_id == asset_id
        ).delete()
        self.session.flush()

    def get_holding(self, account_id: int, asset_id: int) -> dict | None:
        row = self.session.execute(
            select(HoldingORM).where(HoldingORM.account_id == account_id, HoldingORM.asset_id == asset_id)
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "account_id": row.account_id,
            "asset_id": row.asset_id,
            "quantity": Decimal(row.quantity),
            "avg_cost_price": Decimal(row.avg_cost_price),
            "cost_currency": row.cost_currency,
        }

    def list_positions(self, account_id: int | None = None) -> list[dict]:
        stmt = select(HoldingORM, AssetORM, AccountORM).join(AssetORM, HoldingORM.asset_id == AssetORM.id).join(
            AccountORM, HoldingORM.account_id == AccountORM.id
        )
        if account_id is not None:
            stmt = stmt.where(HoldingORM.account_id == account_id)
        rows = self.session.execute(stmt).all()
        return [
            {
                "asset_id": asset.id,
                "symbol": asset.symbol,
                "name": asset.name,
                "currency": asset.currency,
                "account_id": account.id,
                "broker_key": account.broker_key,
                "quantity": Decimal(holding.quantity),
                "avg_cost_price": Decimal(holding.avg_cost_price),
            }
            for holding, asset, account in rows
            if Decimal(holding.quantity) != 0
        ]

    def upsert_cash_balance(self, account_id: int, currency: str, amount: Decimal, as_of: datetime) -> None:
        row = self.session.get(CashBalanceORM, {"account_id": account_id, "currency": currency.upper()})
        if row is None:
            row = CashBalanceORM(account_id=account_id, currency=currency.upper())
            self.session.add(row)
        row.amount = amount
        row.as_of = as_of
        self.session.flush()

    def list_cash_balances(self, account_id: int | None = None) -> list[dict]:
        stmt = select(CashBalanceORM)
        if account_id is not None:
            stmt = stmt.where(CashBalanceORM.account_id == account_id)
        rows = self.session.execute(stmt).scalars()
        return [
            {"account_id": r.account_id, "currency": r.currency, "amount": Decimal(r.amount), "as_of": r.as_of}
            for r in rows
        ]

    def add_transactions(self, transactions: list[Transaction]) -> int:
        if not transactions:
            return 0
        account_ids = {t.account_id for t in transactions}
        existing = set(
            self.session.execute(
                select(TransactionORM.account_id, TransactionORM.external_id).where(
                    TransactionORM.account_id.in_(account_ids), TransactionORM.external_id.is_not(None)
                )
            ).all()
        )
        inserted = 0
        for t in transactions:
            if t.external_id and (t.account_id, t.external_id) in existing:
                continue
            self.session.add(
                TransactionORM(
                    account_id=t.account_id,
                    asset_id=t.asset_id,
                    type=t.type.value,
                    quantity=t.quantity,
                    price=t.price,
                    fees=t.fees,
                    currency=t.currency.upper(),
                    executed_at=t.executed_at,
                    trade_date=t.trade_date,
                    external_id=t.external_id,
                    source=t.source.value,
                    note=t.note,
                )
            )
            if t.external_id:
                existing.add((t.account_id, t.external_id))
            inserted += 1
        self.session.flush()
        return inserted

    def delete_transactions_for_asset(self, asset_id: int) -> int:
        count = self.session.query(TransactionORM).filter(TransactionORM.asset_id == asset_id).delete()
        self.session.flush()
        return count

    def delete_holdings_for_asset(self, asset_id: int) -> int:
        count = self.session.query(HoldingORM).filter(HoldingORM.asset_id == asset_id).delete()
        self.session.flush()
        return count

    def list_transactions(
        self, account_id: int | None = None, asset_id: int | None = None, since: date | None = None
    ) -> list[Transaction]:
        stmt = select(TransactionORM)
        if account_id is not None:
            stmt = stmt.where(TransactionORM.account_id == account_id)
        if asset_id is not None:
            stmt = stmt.where(TransactionORM.asset_id == asset_id)
        if since is not None:
            stmt = stmt.where(TransactionORM.trade_date >= since)
        rows = self.session.execute(stmt.order_by(TransactionORM.trade_date)).scalars()
        return [_transaction_from_orm(r) for r in rows]

    def add_manual_transaction(self, transaction: Transaction) -> Transaction:
        row = TransactionORM(
            account_id=transaction.account_id,
            asset_id=transaction.asset_id,
            type=transaction.type.value,
            quantity=transaction.quantity,
            price=transaction.price,
            fees=transaction.fees,
            currency=transaction.currency.upper(),
            executed_at=transaction.executed_at,
            trade_date=transaction.trade_date,
            external_id=transaction.external_id,
            source=transaction.source.value,
            note=transaction.note,
        )
        self.session.add(row)
        self.session.flush()
        return _transaction_from_orm(row)

    def update_manual_transaction(self, transaction_id: int, **fields) -> None:
        row = self.session.get(TransactionORM, transaction_id)
        if row is None:
            return
        for key, value in fields.items():
            if hasattr(row, key) and value is not None:
                setattr(row, key, value)
        self.session.flush()

    def delete_manual_transaction(self, transaction_id: int) -> None:
        row = self.session.get(TransactionORM, transaction_id)
        if row is not None:
            self.session.delete(row)
            self.session.flush()

    def delete_snapshots_in_range(self, start: date, end: date) -> None:
        self.session.query(PositionSnapshotORM).filter(
            PositionSnapshotORM.date >= start, PositionSnapshotORM.date <= end
        ).delete()
        self.session.query(PortfolioSnapshotORM).filter(
            PortfolioSnapshotORM.date >= start, PortfolioSnapshotORM.date <= end
        ).delete()
        self.session.flush()

    def upsert_position_snapshot(
        self,
        snap_date: date,
        asset_id: int,
        quantity: Decimal,
        price: Decimal,
        market_value_base: Decimal,
        cost_basis_base: Decimal,
    ) -> None:
        self.session.add(
            PositionSnapshotORM(
                date=snap_date,
                asset_id=asset_id,
                quantity=quantity,
                price=price,
                market_value_base=market_value_base,
                cost_basis_base=cost_basis_base,
            )
        )

    def upsert_portfolio_snapshot(
        self,
        snap_date: date,
        base_currency: str,
        market_value: Decimal,
        cost_basis: Decimal,
        net_invested: Decimal,
        cash: Decimal,
    ) -> None:
        self.session.add(
            PortfolioSnapshotORM(
                date=snap_date,
                base_currency=base_currency.upper(),
                market_value=market_value,
                cost_basis=cost_basis,
                net_invested=net_invested,
                cash=cash,
            )
        )

    def get_portfolio_snapshots(self, start: date, end: date) -> list[PortfolioSnapshotPoint]:
        rows = self.session.execute(
            select(PortfolioSnapshotORM)
            .where(PortfolioSnapshotORM.date >= start, PortfolioSnapshotORM.date <= end)
            .order_by(PortfolioSnapshotORM.date)
        ).scalars()
        return [
            PortfolioSnapshotPoint(
                date=r.date,
                market_value=Decimal(r.market_value),
                cost_basis=Decimal(r.cost_basis),
                net_invested=Decimal(r.net_invested),
                cash=Decimal(r.cash),
            )
            for r in rows
        ]

    def get_position_snapshots_totals(self, start: date, end: date, asset_ids: list[int]) -> list[dict]:
        if not asset_ids:
            return []
        rows = self.session.execute(
            select(
                PositionSnapshotORM.date,
                func.sum(PositionSnapshotORM.market_value_base),
                func.sum(PositionSnapshotORM.cost_basis_base),
            )
            .where(
                PositionSnapshotORM.date >= start,
                PositionSnapshotORM.date <= end,
                PositionSnapshotORM.asset_id.in_(asset_ids),
            )
            .group_by(PositionSnapshotORM.date)
            .order_by(PositionSnapshotORM.date)
        ).all()
        return [
            {"date": d, "market_value": Decimal(mv), "cost_basis": Decimal(cb)} for d, mv, cb in rows
        ]

    def get_latest_snapshot(self) -> PortfolioSnapshotPoint | None:
        row = self.session.execute(
            select(PortfolioSnapshotORM).order_by(PortfolioSnapshotORM.date.desc()).limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        return PortfolioSnapshotPoint(
            date=row.date,
            market_value=Decimal(row.market_value),
            cost_basis=Decimal(row.cost_basis),
            net_invested=Decimal(row.net_invested),
            cash=Decimal(row.cash),
        )

    def last_snapshot_date(self) -> date | None:
        return self.session.execute(select(PortfolioSnapshotORM.date).order_by(PortfolioSnapshotORM.date.desc()).limit(1)).scalar_one_or_none()

    def earliest_transaction_date(self, account_id: int | None = None) -> date | None:
        stmt = select(TransactionORM.trade_date).order_by(TransactionORM.trade_date).limit(1)
        if account_id is not None:
            stmt = stmt.where(TransactionORM.account_id == account_id)
        return self.session.execute(stmt).scalar_one_or_none()


class SqlMarketDataRepo(MarketDataRepo):
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_quote(
        self,
        asset_id: int,
        price: Decimal,
        prev_close: Decimal | None,
        currency: str,
        as_of: datetime,
        source: str,
    ) -> None:
        row = self.session.get(QuoteORM, asset_id)
        if row is None:
            row = QuoteORM(asset_id=asset_id)
            self.session.add(row)
        row.price = price
        row.prev_close = prev_close
        row.currency = currency.upper()
        row.as_of = as_of
        row.source = source
        self.session.flush()

    def get_quote(self, asset_id: int) -> dict | None:
        row = self.session.get(QuoteORM, asset_id)
        if row is None:
            return None
        return {
            "price": Decimal(row.price),
            "prev_close": Decimal(row.prev_close) if row.prev_close is not None else None,
            "currency": row.currency,
            "as_of": row.as_of,
        }

    def upsert_bars(self, asset_id: int, bars: list[dict], source: str) -> None:
        for bar in bars:
            row = self.session.get(PriceBarORM, {"asset_id": asset_id, "date": bar["date"]})
            if row is None:
                row = PriceBarORM(asset_id=asset_id, date=bar["date"])
                self.session.add(row)
            row.open = bar["open"]
            row.high = bar["high"]
            row.low = bar["low"]
            row.close = bar["close"]
            row.adj_close = bar["adj_close"]
            row.volume = bar["volume"]
            row.source = source
        self.session.flush()

    def get_bars(self, asset_id: int, start: date, end: date) -> list[dict]:
        rows = self.session.execute(
            select(PriceBarORM)
            .where(PriceBarORM.asset_id == asset_id, PriceBarORM.date >= start, PriceBarORM.date <= end)
            .order_by(PriceBarORM.date)
        ).scalars()
        return [
            {
                "date": r.date,
                "open": Decimal(r.open),
                "high": Decimal(r.high),
                "low": Decimal(r.low),
                "close": Decimal(r.close),
                "adj_close": Decimal(r.adj_close),
                "volume": Decimal(r.volume),
            }
            for r in rows
        ]

    def get_price_on_or_before(self, asset_id: int, on_date: date) -> Decimal | None:
        row = self.session.execute(
            select(PriceBarORM.close)
            .where(PriceBarORM.asset_id == asset_id, PriceBarORM.date <= on_date)
            .order_by(PriceBarORM.date.desc())
            .limit(1)
        ).scalar_one_or_none()
        return Decimal(row) if row is not None else None

    def latest_price_date(self, asset_id: int) -> date | None:
        return self.session.execute(
            select(PriceBarORM.date).where(PriceBarORM.asset_id == asset_id).order_by(PriceBarORM.date.desc()).limit(1)
        ).scalar_one_or_none()

    def upsert_fx_rates(self, base: str, quote: str, rates: dict[date, Decimal]) -> None:
        base, quote = base.upper(), quote.upper()
        for rate_date, rate in rates.items():
            row = self.session.get(FxRateORM, {"date": rate_date, "base": base, "quote": quote})
            if row is None:
                row = FxRateORM(date=rate_date, base=base, quote=quote)
                self.session.add(row)
            row.rate = rate
        self.session.flush()

    def get_fx_rate(self, base: str, quote: str, on_date: date) -> Decimal | None:
        row = self.session.execute(
            select(FxRateORM.rate)
            .where(FxRateORM.base == base.upper(), FxRateORM.quote == quote.upper(), FxRateORM.date <= on_date)
            .order_by(FxRateORM.date.desc())
            .limit(1)
        ).scalar_one_or_none()
        return Decimal(row) if row is not None else None

    def currency_pairs_in_use(self, base_currency: str) -> list[str]:
        rows = self.session.execute(
            select(AssetORM.currency)
            .join(HoldingORM, HoldingORM.asset_id == AssetORM.id)
            .where(AssetORM.currency != base_currency.upper())
            .distinct()
        ).scalars()
        return list(rows)


def _candidate_to_orm(resolution_id: int, candidate: Candidate) -> ResolutionCandidateORM:
    return ResolutionCandidateORM(
        resolution_id=resolution_id,
        symbol=candidate.symbol,
        name=candidate.info.name if candidate.info else "",
        mic=candidate.mic,
        currency=candidate.info.currency if candidate.info else None,
        asset_class=candidate.asset_class,
        found_by=",".join(sorted(candidate.found_by)),
        last_close=candidate.info.last_close if candidate.info else None,
        last_trade_date=candidate.info.last_trade_date if candidate.info else None,
        avg_volume=candidate.info.avg_volume if candidate.info else None,
        features=dict(candidate.features),
        score=candidate.score,
    )


class SqlResolutionRepo(ResolutionRepo):
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        ctx: ResolutionContext,
        status: ResolutionStatus,
        decided_by: str | None,
        note: str,
        scorer_version: str,
        candidates: list[Candidate],
    ) -> int:
        row = AssetResolutionORM(
            asset_id=ctx.asset_id,
            isin=ctx.isin,
            broker_key=ctx.broker_key,
            broker_symbol=ctx.broker_symbol,
            broker_name=ctx.broker_name,
            broker_exchange=ctx.broker_exchange,
            broker_mic=ctx.broker_mic,
            currency=ctx.currency.upper(),
            status=status.value,
            decided_by=decided_by,
            note=note,
            scorer_version=scorer_version,
            decided_at=datetime.now(timezone.utc) if status.is_terminal else None,
        )
        self.session.add(row)
        self.session.flush()  # need row.id for the candidates' FK
        for candidate in candidates:
            candidate_row = _candidate_to_orm(row.id, candidate)
            self.session.add(candidate_row)
            self.session.flush()
            candidate.id = candidate_row.id
        self.session.flush()
        return row.id

    def get(self, resolution_id: int) -> ResolutionDTO | None:
        row = self.session.get(AssetResolutionORM, resolution_id)
        return _resolution_from_orm(row) if row else None

    def get_open_for_asset(self, asset_id: int) -> ResolutionDTO | None:
        row = self.session.execute(
            select(AssetResolutionORM).where(
                AssetResolutionORM.asset_id == asset_id,
                AssetResolutionORM.status != ResolutionStatus.SUPERSEDED.value,
            )
        ).scalar_one_or_none()
        return _resolution_from_orm(row) if row else None

    def list_by_status(self, statuses: list[ResolutionStatus], limit: int = 50) -> list[ResolutionDTO]:
        values = [s.value for s in statuses]
        rows = self.session.execute(
            select(AssetResolutionORM)
            .where(AssetResolutionORM.status.in_(values))
            .order_by(AssetResolutionORM.created_at)
            .limit(limit)
        ).scalars()
        return [_resolution_from_orm(r) for r in rows]

    def add_candidate(self, resolution_id: int, candidate: Candidate) -> int:
        row = _candidate_to_orm(resolution_id, candidate)
        self.session.add(row)
        self.session.flush()
        candidate.id = row.id
        return row.id

    def set_status(self, resolution_id: int, status: ResolutionStatus, decided_by: str | None, note: str) -> None:
        row = self.session.get(AssetResolutionORM, resolution_id)
        if row is None:
            return
        row.status = status.value
        row.decided_by = decided_by
        row.note = note
        if status.is_terminal:
            row.decided_at = datetime.now(timezone.utc)
        self.session.flush()

    def select_candidate(self, resolution_id: int, candidate_id: int) -> None:
        rows = self.session.execute(
            select(ResolutionCandidateORM).where(ResolutionCandidateORM.resolution_id == resolution_id)
        ).scalars()
        for r in rows:
            r.selected = r.id == candidate_id
        self.session.flush()

    def supersede_open(self, asset_id: int) -> None:
        row = self.session.execute(
            select(AssetResolutionORM).where(
                AssetResolutionORM.asset_id == asset_id,
                AssetResolutionORM.status != ResolutionStatus.SUPERSEDED.value,
            )
        ).scalar_one_or_none()
        if row is not None:
            row.status = ResolutionStatus.SUPERSEDED.value
            row.decided_at = datetime.now(timezone.utc)
            self.session.flush()

    def add_agent_run(self, run: AgentRunRecord) -> int:
        row = AgentRunORM(
            resolution_id=run.resolution_id,
            agent=run.agent,
            model=run.model,
            status=run.status,
            steps=run.steps,
            tool_calls=run.tool_calls,
            final_message=run.final_message,
            prompt_tokens=run.prompt_tokens,
            completion_tokens=run.completion_tokens,
            duration_ms=run.duration_ms,
            error=run.error,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def list_assets_to_resolve(self, retry_empty_after_hours: int = 24) -> list[int]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=retry_empty_after_hours)

        needs_mapping_ids = set(
            self.session.execute(select(AssetORM.id).where(AssetORM.needs_mapping.is_(True))).scalars()
        )
        if not needs_mapping_ids:
            return []

        open_resolutions = self.session.execute(
            select(AssetResolutionORM).where(
                AssetResolutionORM.asset_id.in_(needs_mapping_ids),
                AssetResolutionORM.status != ResolutionStatus.SUPERSEDED.value,
            )
        ).scalars().all()
        open_by_asset = {r.asset_id: r for r in open_resolutions}

        result = []
        for asset_id in needs_mapping_ids:
            open_res = open_by_asset.get(asset_id)
            if open_res is None:
                result.append(asset_id)
                continue
            is_stale_empty = (
                open_res.status == ResolutionStatus.NEEDS_REVIEW.value
                and len(open_res.candidates) == 0
                and open_res.created_at < cutoff
            )
            if is_stale_empty:
                result.append(asset_id)
        return result

    def list_recent(
        self, since: datetime, decided_by: list[str] | None = None, limit: int = 100
    ) -> list[ResolutionDTO]:
        stmt = select(AssetResolutionORM).where(
            AssetResolutionORM.decided_at.is_not(None), AssetResolutionORM.decided_at >= since
        )
        if decided_by:
            stmt = stmt.where(AssetResolutionORM.decided_by.in_(decided_by))
        rows = self.session.execute(
            stmt.order_by(AssetResolutionORM.decided_at.desc()).limit(limit)
        ).scalars()
        return [_resolution_from_orm(r) for r in rows]


def _note_from_orm(row: AiNoteORM) -> Note:
    return Note(
        id=row.id,
        agent=row.agent,
        scope=row.scope,
        account_id=row.account_id,
        title=row.title,
        body=row.body,
        created_at=row.created_at,
        dismissed_at=row.dismissed_at,
    )


class SqlNoteRepo(NoteRepo):
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, agent: str, scope: str, title: str, body: str, account_id: int | None = None) -> int:
        row = AiNoteORM(agent=agent, scope=scope, account_id=account_id, title=title, body=body)
        self.session.add(row)
        self.session.flush()
        return row.id

    def list(self, scope: str | None = None, since: datetime | None = None, limit: int = 20) -> list[Note]:
        stmt = select(AiNoteORM)
        if scope is not None:
            stmt = stmt.where(AiNoteORM.scope == scope)
        if since is not None:
            stmt = stmt.where(AiNoteORM.created_at >= since)
        rows = self.session.execute(stmt.order_by(AiNoteORM.created_at.desc()).limit(limit)).scalars()
        return [_note_from_orm(r) for r in rows]

    def dismiss(self, note_id: int) -> None:
        row = self.session.get(AiNoteORM, note_id)
        if row is None:
            return
        row.dismissed_at = datetime.now(timezone.utc)
        self.session.flush()


def _session_from_orm(row: ChatSessionORM) -> ChatSession:
    return ChatSession(id=row.id, title=row.title, created_at=row.created_at, updated_at=row.updated_at)


def _message_from_orm(row: ChatMessageORM) -> ChatMessage:
    return ChatMessage(
        id=row.id, session_id=row.session_id, role=row.role, content=row.content,
        tool_calls=row.tool_calls, created_at=row.created_at,
    )


class SqlChatRepo(ChatRepo):
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_session(self, title: str = "") -> int:
        row = ChatSessionORM(title=title)
        self.session.add(row)
        self.session.flush()
        return row.id

    def list_sessions(self, limit: int = 50) -> list[ChatSession]:
        rows = self.session.execute(
            select(ChatSessionORM).order_by(ChatSessionORM.updated_at.desc()).limit(limit)
        ).scalars()
        return [_session_from_orm(r) for r in rows]

    def get_session(self, session_id: int) -> ChatSession | None:
        row = self.session.get(ChatSessionORM, session_id)
        return _session_from_orm(row) if row is not None else None

    def rename_session(self, session_id: int, title: str) -> None:
        row = self.session.get(ChatSessionORM, session_id)
        if row is None:
            return
        row.title = title
        self.session.flush()

    def delete_session(self, session_id: int) -> None:
        row = self.session.get(ChatSessionORM, session_id)
        if row is None:
            return
        self.session.delete(row)
        self.session.flush()

    def touch_session(self, session_id: int, as_of: datetime | None = None) -> None:
        row = self.session.get(ChatSessionORM, session_id)
        if row is None:
            return
        row.updated_at = as_of or datetime.now(timezone.utc)
        self.session.flush()

    def add_message(self, session_id: int, role: str, content: str, tool_calls: list[dict] | None = None) -> int:
        row = ChatMessageORM(session_id=session_id, role=role, content=content, tool_calls=tool_calls)
        self.session.add(row)
        self.session.flush()
        return row.id

    def list_messages(self, session_id: int, limit: int | None = None) -> list[ChatMessage]:
        stmt = select(ChatMessageORM).where(ChatMessageORM.session_id == session_id)
        if limit is None:
            rows = self.session.execute(stmt.order_by(ChatMessageORM.created_at)).scalars()
            return [_message_from_orm(r) for r in rows]
        # Most recent `limit`, but returned oldest-first — a plain
        # ORDER BY ... DESC LIMIT n then reverse in Python, since SQL has no
        # "last N, ascending" in one clause.
        rows = self.session.execute(
            stmt.order_by(ChatMessageORM.created_at.desc()).limit(limit)
        ).scalars()
        return [_message_from_orm(r) for r in reversed(list(rows))]


def _quant_run_from_orm(row: QuantRunORM) -> QuantRunRecord:
    backtest = None
    if row.backtest is not None:
        # Fall back to the pre-rename keys (within_90pct_band /
        # mean_abs_pct_error_p50, from when the confidence band was fixed
        # at 90%/median) so a run persisted before that rename still reads
        # back instead of KeyError-ing — this JSONB blob is frozen at
        # write time, same "old rows keep their old shape" reasoning as
        # the fp1 import fingerprint recipe (see backend/AGENTS.md).
        within = row.backtest.get("within_band", row.backtest.get("within_90pct_band"))
        mean_abs = row.backtest.get("mean_abs_pct_error_median", row.backtest.get("mean_abs_pct_error_p50"))
        backtest = BacktestResult(
            covered_days=row.backtest["covered_days"],
            within_band=within,
            mean_abs_pct_error_median=mean_abs,
        )
    return QuantRunRecord(
        id=row.id,
        asset_id=row.asset_id,
        model_key=row.model_key,
        split_date=row.split_date,
        horizon_days=row.horizon_days,
        n_paths=row.n_paths,
        seed=row.seed,
        params=dict(row.params or {}),
        calibration_params=dict(row.calibration_params or {}),
        calibration_diagnostics=dict(row.calibration_diagnostics or {}),
        percentiles=dict(row.percentiles or {}),
        backtest=backtest,
        created_by=row.created_by,
        note=row.note,
        created_at=row.created_at,
    )


class SqlQuantRunRepo(QuantRunRepo):
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, record: QuantRunRecord) -> int:
        backtest_dict = None
        if record.backtest is not None:
            backtest_dict = {
                "covered_days": record.backtest.covered_days,
                "within_band": record.backtest.within_band,
                "mean_abs_pct_error_median": record.backtest.mean_abs_pct_error_median,
            }
        row = QuantRunORM(
            asset_id=record.asset_id,
            model_key=record.model_key,
            split_date=record.split_date,
            horizon_days=record.horizon_days,
            n_paths=record.n_paths,
            seed=record.seed,
            params=dict(record.params),
            calibration_params=dict(record.calibration_params),
            calibration_diagnostics=dict(record.calibration_diagnostics),
            percentiles=dict(record.percentiles),
            backtest=backtest_dict,
            created_by=record.created_by,
            note=record.note,
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def get(self, run_id: int) -> QuantRunRecord | None:
        row = self.session.get(QuantRunORM, run_id)
        return _quant_run_from_orm(row) if row else None

    def list_for_asset(self, asset_id: int, limit: int = 20) -> list[QuantRunRecord]:
        rows = self.session.execute(
            select(QuantRunORM)
            .where(QuantRunORM.asset_id == asset_id)
            .order_by(QuantRunORM.created_at.desc())
            .limit(limit)
        ).scalars()
        return [_quant_run_from_orm(r) for r in rows]
