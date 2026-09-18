"""tool RAG — pgvector-backed tool_index + agent_runs.tool_retrieval

See plans/tool_rag.md sections 3.4 and 5. Requires the `pgvector`
Postgres extension (the `db` service image is
`pgvector/pgvector:pg16` — see docker-compose.yml and
plans/tool_rag.md section 10 for why); `pg_trgm` is already enabled by
migration 0001.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        'tool_index',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('server_module', sa.String(length=100), nullable=False),
        sa.Column('tool_name', sa.String(length=100), nullable=False),
        sa.Column('doc_kind', sa.String(length=20), nullable=False),
        sa.Column('doc_text', sa.Text(), nullable=False),
        sa.Column('category', sa.String(length=40), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('embedding_model', sa.String(length=60), nullable=False),
        sa.Column('token_estimate', sa.Integer(), nullable=False),
        sa.Column('embedding', Vector(768), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('server_module', 'tool_name', 'doc_kind', 'doc_text', name='uq_tool_index_doc'),
    )
    op.create_index('ix_tool_index_server_tool', 'tool_index', ['server_module', 'tool_name'])
    # HNSW over cosine distance — the ANN index dense_search's
    # cosine_distance() ordering (app/adapters/persistence/repositories.py)
    # relies on. ivfflat would need a row-count-dependent `lists` tuning
    # parameter this table is far too small to bother with yet; hnsw has
    # no such requirement and is the current pgvector-recommended default.
    op.execute(
        "CREATE INDEX tool_index_embedding_idx ON tool_index "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX tool_index_doc_text_trgm_idx ON tool_index "
        "USING gin (doc_text gin_trgm_ops)"
    )

    op.add_column(
        'agent_runs',
        sa.Column('tool_retrieval', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('agent_runs', 'tool_retrieval')
    op.drop_index('tool_index_doc_text_trgm_idx', table_name='tool_index')
    op.execute("DROP INDEX IF EXISTS tool_index_embedding_idx")
    op.drop_index('ix_tool_index_server_tool', table_name='tool_index')
    op.drop_table('tool_index')
