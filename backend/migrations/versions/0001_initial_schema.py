"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-13

"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

MONEY = sa.Numeric(20, 4)
PRICE = sa.Numeric(20, 8)
QUANTITY = sa.Numeric(28, 10)
FX_RATE = sa.Numeric(20, 10)


def upgrade() -> None:
    # pg_trgm is created by database/init/01-extensions.sql on first container
    # boot (superuser-only on some managed Postgres setups) — not here.
    op.create_table(
        "assets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("symbol", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("asset_class", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("exchange", sa.String(40), nullable=True),
        sa.Column("isin", sa.String(12), nullable=True, unique=True),
        sa.Column("needs_mapping", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE INDEX ix_assets_symbol_trgm ON assets USING gin (symbol gin_trgm_ops)")
    op.execute("CREATE INDEX ix_assets_name_trgm ON assets USING gin (name gin_trgm_ops)")

    op.create_table(
        "asset_identifiers",
        sa.Column("scheme", sa.String(20), primary_key=True),
        sa.Column("value", sa.String(100), primary_key=True),
        sa.Column("asset_id", sa.Integer, sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
    )
    op.create_index("ix_asset_identifiers_asset_id", "asset_identifiers", ["asset_id"])

    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("broker_key", sa.String(40), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.UniqueConstraint("broker_key", "external_id"),
    )

    op.create_table(
        "holdings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", sa.Integer, sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("avg_cost_price", PRICE, nullable=False),
        sa.Column("cost_currency", sa.String(3), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.UniqueConstraint("account_id", "asset_id"),
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", sa.Integer, sa.ForeignKey("assets.id"), nullable=True),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("price", PRICE, nullable=False),
        sa.Column("fees", PRICE, nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("external_id", sa.String(100), nullable=True),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.UniqueConstraint("account_id", "external_id", name="uq_transactions_account_external_id"),
    )
    op.create_index("ix_transactions_account_trade_date", "transactions", ["account_id", "trade_date"])
    op.create_index("ix_transactions_asset_trade_date", "transactions", ["asset_id", "trade_date"])

    op.create_table(
        "cash_balances",
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("currency", sa.String(3), primary_key=True),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "prices",
        sa.Column("asset_id", sa.Integer, sa.ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("open", PRICE, nullable=False),
        sa.Column("high", PRICE, nullable=False),
        sa.Column("low", PRICE, nullable=False),
        sa.Column("close", PRICE, nullable=False),
        sa.Column("adj_close", PRICE, nullable=False),
        sa.Column("volume", sa.Numeric(28, 4), nullable=False, server_default="0"),
        sa.Column("source", sa.String(20), nullable=False),
    )

    op.create_table(
        "quotes",
        sa.Column("asset_id", sa.Integer, sa.ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("price", PRICE, nullable=False),
        sa.Column("prev_close", PRICE, nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
    )

    op.create_table(
        "fx_rates",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("base", sa.String(3), primary_key=True),
        sa.Column("quote", sa.String(3), primary_key=True),
        sa.Column("rate", FX_RATE, nullable=False),
    )

    op.create_table(
        "position_snapshots",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("asset_id", sa.Integer, sa.ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("price", PRICE, nullable=False),
        sa.Column("market_value_base", MONEY, nullable=False),
        sa.Column("cost_basis_base", MONEY, nullable=False),
    )

    op.create_table(
        "portfolio_snapshots",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("market_value", MONEY, nullable=False),
        sa.Column("cost_basis", MONEY, nullable=False),
        sa.Column("net_invested", MONEY, nullable=False),
        sa.Column("cash", MONEY, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_assets_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_assets_symbol_trgm")
    op.drop_table("portfolio_snapshots")
    op.drop_table("position_snapshots")
    op.drop_table("fx_rates")
    op.drop_table("quotes")
    op.drop_table("prices")
    op.drop_table("cash_balances")
    op.drop_index("ix_transactions_asset_trade_date", table_name="transactions")
    op.drop_index("ix_transactions_account_trade_date", table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("holdings")
    op.drop_table("accounts")
    op.drop_index("ix_asset_identifiers_asset_id", table_name="asset_identifiers")
    op.drop_table("asset_identifiers")
    op.drop_table("assets")
