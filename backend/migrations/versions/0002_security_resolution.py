"""security resolution

Adds the tables that back the deterministic asset-mapping resolver (see
plans/agentic_asset_mapping.md, Phase 1): `asset_resolutions` records each
attempt to decide which market-data listing prices an asset,
`resolution_candidates` records every listing considered (with the exact
feature values used to score it — this doubles as the future ML training
set), and `agent_runs` records every local-LLM agent invocation for a
resolution. `assets.share_class_figi` is OpenFIGI's cross-exchange security
identifier, learned once a resolution succeeds via OpenFIGI.

Autogenerate also proposed dropping and recreating `ix_assets_name_trgm` /
`ix_assets_symbol_trgm` — a false positive (it doesn't understand the
`gin_trgm_ops` index created by raw SQL in 0001) — reviewed by hand and
removed per backend/AGENTS.md's migration workflow.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-14

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("share_class_figi", sa.String(length=12), nullable=True))

    op.create_table(
        "asset_resolutions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        # --- context snapshot: what the broker told us at resolution time ---
        sa.Column("isin", sa.String(length=12), nullable=True),
        sa.Column("broker_key", sa.String(length=40), nullable=True),
        sa.Column("broker_symbol", sa.String(length=40), nullable=False),
        sa.Column("broker_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("broker_exchange", sa.String(length=40), nullable=True),
        sa.Column("broker_mic", sa.String(length=4), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        # --- decision ---
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("decided_by", sa.String(length=10), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("scorer_version", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_asset_resolutions_status", "asset_resolutions", ["status"])
    # At most one live (non-SUPERSEDED) resolution per asset. Autogenerate
    # can't express a partial index — added by hand.
    op.execute(
        "CREATE UNIQUE INDEX uq_asset_resolutions_open ON asset_resolutions (asset_id) "
        "WHERE status <> 'SUPERSEDED'"
    )

    op.create_table(
        "resolution_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "resolution_id", sa.Integer(), sa.ForeignKey("asset_resolutions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("mic", sa.String(length=4), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("asset_class", sa.String(length=20), nullable=True),
        # comma list: 'openfigi', 'yahoo_isin', 'yahoo_text', 'agent', 'user'
        sa.Column("found_by", sa.String(length=60), nullable=False),
        sa.Column("last_close", sa.Numeric(20, 8), nullable=True),
        sa.Column("last_trade_date", sa.Date(), nullable=True),
        sa.Column("avg_volume", sa.Numeric(28, 4), nullable=True),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("ml_probability", sa.Numeric(6, 5), nullable=True),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resolution_id", "symbol"),
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "resolution_id",
            sa.Integer(),
            sa.ForeignKey("asset_resolutions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("agent", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("final_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("agent_runs")
    op.drop_table("resolution_candidates")
    op.execute("DROP INDEX IF EXISTS uq_asset_resolutions_open")
    op.drop_index("ix_asset_resolutions_status", table_name="asset_resolutions")
    op.drop_table("asset_resolutions")
    op.drop_column("assets", "share_class_figi")
