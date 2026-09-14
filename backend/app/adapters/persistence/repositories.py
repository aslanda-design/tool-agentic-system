"""SQLAlchemy implementations of the three persistence ports. Every method
that writes calls `session.flush()` (so generated ids are available to the
caller) but never `session.commit()` — the API layer owns the transaction
boundary and commits once per request after a use case completes.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.errors import AssetConflictError, AssetNotFoundError
from app.domain.models import (
    Account,
    AccountSource,
    Asset,
    AssetClass,
    IdentifierScheme,
    PortfolioSnapshotPoint,
    Transaction,
    TransactionType,
)
from app.ports.repositories import AssetRepo, MarketDataRepo, PortfolioRepo

from .orm import (
    AccountORM,
    AssetIdentifierORM,
    AssetORM,
    CashBalanceORM,
    FxRateORM,
    HoldingORM,
    PortfolioSnapshotORM,
    PositionSnapshotORM,
    PriceBarORM,
    QuoteORM,
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
