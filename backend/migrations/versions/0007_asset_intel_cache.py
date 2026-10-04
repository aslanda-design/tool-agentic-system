"""asset_intel_cache — read-through cache for the market_intel MCP server

See plans/market_intel.md section 3.1.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-18 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_intel_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "kind", name="uq_asset_intel_cache_symbol_kind"),
    )


def downgrade() -> None:
    op.drop_table("asset_intel_cache")
