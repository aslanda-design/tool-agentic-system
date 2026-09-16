"""SQLAlchemy 2.0 mapped tables. This is the ONLY module that knows about
the database's physical shape — the domain layer never imports from here.
Money: numeric(20,4). Prices: numeric(20,8). Quantities: numeric(28,10).
FX: numeric(20,10). All timestamps timestamptz. Schema is versioned via
Alembic (backend/migrations) — see database/AGENTS.md for the full model.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    # OpenFIGI's shareClassFIGI — identifies the security across every exchange
    # it lists on, independent of which listing we chose to price it with.
    share_class_figi: Mapped[str | None] = mapped_column(String(12), nullable=True)
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


class AssetResolutionORM(Base):
    """One row per attempt to decide which market-data listing prices an
    asset. See backend/ai/AGENTS.md and plans/agentic_asset_mapping.md for
    the full design. `uq_asset_resolutions_open` (a partial unique index,
    added by hand in the migration — autogenerate can't express it) keeps at
    most one non-SUPERSEDED resolution per asset."""

    __tablename__ = "asset_resolutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    # --- context snapshot: what the broker told us at resolution time ---
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True)
    broker_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    broker_symbol: Mapped[str] = mapped_column(String(40))
    broker_name: Mapped[str] = mapped_column(String(200), default="")
    broker_exchange: Mapped[str | None] = mapped_column(String(40), nullable=True)
    broker_mic: Mapped[str | None] = mapped_column(String(4), nullable=True)
    currency: Mapped[str] = mapped_column(String(3))
    # --- decision ---
    status: Mapped[str] = mapped_column(String(20))
    decided_by: Mapped[str | None] = mapped_column(String(10), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    scorer_version: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    candidates: Mapped[list["ResolutionCandidateORM"]] = relationship(
        back_populates="resolution", passive_deletes=True
    )

    __table_args__ = (Index("ix_asset_resolutions_status", "status"),)


class ResolutionCandidateORM(Base):
    __tablename__ = "resolution_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    resolution_id: Mapped[int] = mapped_column(ForeignKey("asset_resolutions.id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200), default="")
    mic: Mapped[str | None] = mapped_column(String(4), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    asset_class: Mapped[str | None] = mapped_column(String(20), nullable=True)
    found_by: Mapped[str] = mapped_column(String(60))  # comma list: 'openfigi', 'yahoo_isin', 'yahoo_text', 'agent', 'user'
    last_close: Mapped[float | None] = mapped_column(PRICE, nullable=True)
    last_trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    avg_volume: Mapped[float | None] = mapped_column(Numeric(28, 4), nullable=True)
    features: Mapped[dict] = mapped_column(JSONB)
    score: Mapped[int] = mapped_column(Integer)
    ml_probability: Mapped[float | None] = mapped_column(Numeric(6, 5), nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)

    resolution: Mapped["AssetResolutionORM"] = relationship(back_populates="candidates")

    __table_args__ = (UniqueConstraint("resolution_id", "symbol"),)


class AgentRunORM(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    resolution_id: Mapped[int | None] = mapped_column(
        ForeignKey("asset_resolutions.id", ondelete="SET NULL"), nullable=True
    )
    agent: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20))
    steps: Mapped[int] = mapped_column(Integer)
    tool_calls: Mapped[list] = mapped_column(JSONB)
    final_message: Mapped[str] = mapped_column(Text, default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class AiNoteORM(Base):
    """A short Markdown note an agent wrote — see
    plans/agentic_asset_mapping_phase7_8.md Phase 8c. Append-only: the only
    mutation an existing row ever gets is `dismissed_at` being set."""

    __tablename__ = "ai_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent: Mapped[str] = mapped_column(String(40))  # 'import_reviewer' | 'weekly_report'
    scope: Mapped[str] = mapped_column(String(20))  # 'account' | 'portfolio'
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_ai_notes_created_at", "created_at"),)


class ChatSessionORM(Base):
    """One conversation thread with portfolio_assistant — see
    plans/agentic_asset_mapping_phase7_8.md Phase 8f. `updated_at` (not
    `created_at`) is what the history sidebar sorts by."""

    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    messages: Mapped[list["ChatMessageORM"]] = relationship(back_populates="session", passive_deletes=True)

    __table_args__ = (Index("ix_chat_sessions_updated_at", "updated_at"),)


class ChatMessageORM(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))  # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    session: Mapped[ChatSessionORM] = relationship(back_populates="messages")

    __table_args__ = (Index("ix_chat_messages_session_id", "session_id"),)


class QuantRunORM(Base):
    """One Monte Carlo run from the Quant Lab (see plans/quant_lab.md).
    Deliberately stores only the RUN'S RECIPE (asset/model/split/params/
    seed) plus a percentile summary — never the raw n_paths x horizon_days
    path array. RunQuantSimulationUseCase.replay() recomputes paths
    on demand from this row, exactly, because calibration only reads bars
    already persisted for a fixed date range and the RNG is seeded."""

    __tablename__ = "quant_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    model_key: Mapped[str] = mapped_column(String(40))
    split_date: Mapped[date] = mapped_column(Date)
    horizon_days: Mapped[int] = mapped_column(Integer)
    n_paths: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(BigInteger)
    params: Mapped[dict] = mapped_column(JSONB)
    calibration_params: Mapped[dict] = mapped_column(JSONB)
    calibration_diagnostics: Mapped[dict] = mapped_column(JSONB)
    percentiles: Mapped[dict] = mapped_column(JSONB)
    backtest: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str] = mapped_column(String(10))  # 'user' | 'agent'
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    __table_args__ = (Index("ix_quant_runs_asset_created", "asset_id", "created_at"),)
