"""widen asset_intel_cache.symbol to hold a macro topic, not just a ticker

Phase 3 of plans/market_intel.md: get_macro_headlines caches under kind
"macro_news", reusing the `symbol` column to hold a normalized topic
phrase (up to MAX_TOPIC_LENGTH=80 chars, domain/market_intel.py) rather
than a short ticker — the original String(20) is too narrow for that.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-18 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "asset_intel_cache",
        "symbol",
        type_=sa.String(length=100),
        existing_type=sa.String(length=20),
    )


def downgrade() -> None:
    op.alter_column(
        "asset_intel_cache",
        "symbol",
        type_=sa.String(length=20),
        existing_type=sa.String(length=100),
    )
