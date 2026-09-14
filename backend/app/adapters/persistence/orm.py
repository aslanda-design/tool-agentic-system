"""SQLAlchemy 2.0 mapped tables. This is the ONLY module that knows about
the database's physical shape — the domain layer never imports from here.
Money: numeric(20,4). Prices: numeric(20,8). Quantities: numeric(28,10).
FX: numeric(20,10). All timestamps timestamptz. Schema is versioned via
Alembic (backend/migrations) — see database/AGENTS.md for the full model.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MONEY = Numeric(20, 4)
PRICE = Numeric(20, 8)
QUANTITY = Numeric(28, 10)
FX_RATE = Numeric(20, 10)


class Base(DeclarativeBase):
    pass


class AssetORM(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    asset_class: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    exchange: Mapped[str | None] = mapped_column(String(40), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(12), unique=True, nullable=True)
    needs_mapping: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    # passive_deletes=True: trust the DB's own ON DELETE CASCADE on
    # asset_identifiers.asset_id (see AssetIdentifierORM) instead of
    # SQLAlchemy's default behavior of loading this collection and issuing
    # an UPDATE to NULL the FK before deleting the parent — asset_id is
    # NOT NULL, so that default behavior fails deleting any asset that has
    # identifiers (i.e. any asset that isn't still needs_mapping).
    identifiers: Mapped[list["AssetIdentifierORM"]] = relationship(back_populates="asset", passive_deletes=True)


class AssetIdentifierORM(Base):
    __tablename__ = "asset_identifiers"

    scheme: Mapped[str] = mapped_column(String(20), primary_key=True)
    value: Mapped[str] = mapped_column(String(100), primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))

    asset: Mapped["AssetORM"] = relationship(back_populates="identifiers")

    __table_args__ = (Index("ix_asset_identifiers_asset_id", "asset_id"),)


class AccountORM(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_key: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3))
    source: Mapped[str] = mapped_column(String(10))  # api | manual

    __table_args__ = (UniqueConstraint("broker_key", "external_id"),)


class HoldingORM(Base):
    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    quantity: Mapped[float] = mapped_column(QUANTITY)
    avg_cost_price: Mapped[float] = mapped_column(PRICE)
    cost_currency: Mapped[str] = mapped_column(String(3))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(10))

    __table_args__ = (UniqueConstraint("account_id", "asset_id"),)


class TransactionORM(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[float] = mapped_column(QUANTITY)
    price: Mapped[float] = mapped_column(PRICE)
    fees: Mapped[float] = mapped_column(PRICE, default=0)
    currency: Mapped[str] = mapped_column(String(3))
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trade_date: Mapped[date] = mapped_column(Date)
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(String(10))
    note: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (
        UniqueConstraint("account_id", "external_id", name="uq_transactions_account_external_id"),
        Index("ix_transactions_account_trade_date", "account_id", "trade_date"),
        Index("ix_transactions_asset_trade_date", "asset_id", "trade_date"),
    )


class CashBalanceORM(Base):
    __tablename__ = "cash_balances"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    amount: Mapped[float] = mapped_column(MONEY)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PriceBarORM(Base):
    __tablename__ = "prices"

    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(PRICE)
    high: Mapped[float] = mapped_column(PRICE)
    low: Mapped[float] = mapped_column(PRICE)
    close: Mapped[float] = mapped_column(PRICE)
    adj_close: Mapped[float] = mapped_column(PRICE)
    volume: Mapped[float] = mapped_column(Numeric(28, 4), default=0)
    source: Mapped[str] = mapped_column(String(20))


class QuoteORM(Base):
    __tablename__ = "quotes"

    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    price: Mapped[float] = mapped_column(PRICE)
    prev_close: Mapped[float | None] = mapped_column(PRICE, nullable=True)
    currency: Mapped[str] = mapped_column(String(3))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(20))


class FxRateORM(Base):
    __tablename__ = "fx_rates"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    base: Mapped[str] = mapped_column(String(3), primary_key=True)
    quote: Mapped[str] = mapped_column(String(3), primary_key=True)
    rate: Mapped[float] = mapped_column(FX_RATE)


class PositionSnapshotORM(Base):
    __tablename__ = "position_snapshots"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    quantity: Mapped[float] = mapped_column(QUANTITY)
    price: Mapped[float] = mapped_column(PRICE)
    market_value_base: Mapped[float] = mapped_column(MONEY)
    cost_basis_base: Mapped[float] = mapped_column(MONEY)


class PortfolioSnapshotORM(Base):
    __tablename__ = "portfolio_snapshots"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    base_currency: Mapped[str] = mapped_column(String(3))
    market_value: Mapped[float] = mapped_column(MONEY)
    cost_basis: Mapped[float] = mapped_column(MONEY)
    net_invested: Mapped[float] = mapped_column(MONEY)
    cash: Mapped[float] = mapped_column(MONEY)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
