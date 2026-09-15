"""chat sessions

Adds `chat_sessions` + `chat_messages` — persisted session memory and
session history for the `portfolio_assistant` chat agent (see
plans/agentic_asset_mapping_phase7_8.md, Phase 8f). A session is a
conversation thread (what the frontend's history sidebar lists); its
messages are the full transcript, replayed (minus tool-call scaffolding —
see ai/common/agent_loop.py::run_agent's `history` param) as session memory
on the next turn.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-15

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # Bumped on every new message — what "most recent first" in the
        # history sidebar sorts by, not created_at (an old session with a
        # new reply should float back to the top).
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_sessions_updated_at", "chat_sessions", ["updated_at"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),  # 'user' | 'assistant'
        sa.Column("content", sa.Text(), nullable=False),
        # Only ever set on an 'assistant' row — which tools that reply used,
        # for transparency in the UI; never replayed back into the model as
        # session memory (see run_agent's `history` param docstring on why).
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_session_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_sessions_updated_at", table_name="chat_sessions")
    op.drop_table("chat_sessions")
