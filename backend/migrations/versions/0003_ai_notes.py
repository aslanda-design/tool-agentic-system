"""ai notes

Adds `ai_notes` — the write path for the `notes` MCP server (see
plans/agentic_asset_mapping_phase7_8.md, Phase 8c) and, later, the
`import_reviewer`/`weekly_report` agents (Phase 8d-e). Deliberately the
only table an agent writes to besides the security resolver's own tables:
append-only, no state machine, `dismissed_at` is the only mutation a human
(or later, the agent itself) ever makes to an existing row.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-14

"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent", sa.String(length=40), nullable=False),  # 'import_reviewer' | 'weekly_report'
        sa.Column("scope", sa.String(length=20), nullable=False),  # 'account' | 'portfolio'
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),  # Markdown
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_notes_created_at", "ai_notes", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_notes_created_at", table_name="ai_notes")
    op.drop_table("ai_notes")
