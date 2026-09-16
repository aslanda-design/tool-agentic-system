"""add quant_runs

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-15 18:02:34.715565

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('quant_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.Column('model_key', sa.String(length=40), nullable=False),
    sa.Column('split_date', sa.Date(), nullable=False),
    sa.Column('horizon_days', sa.Integer(), nullable=False),
    sa.Column('n_paths', sa.Integer(), nullable=False),
    sa.Column('seed', sa.BigInteger(), nullable=False),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('calibration_params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('calibration_diagnostics', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('percentiles', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('backtest', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_by', sa.String(length=10), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_quant_runs_asset_created', 'quant_runs', ['asset_id', 'created_at'], unique=False)
    # NOTE: autogenerate also proposed dropping uq_asset_resolutions_open and
    # the two pg_trgm GIN indexes on `assets` — those are hand-written in
    # migration 0002/0001 (autogenerate can't see them from ORM metadata
    # alone, per database/AGENTS.md's migration workflow note) and are NOT
    # actually being removed. Deliberately omitted here.


def downgrade() -> None:
    op.drop_index('ix_quant_runs_asset_created', table_name='quant_runs')
    op.drop_table('quant_runs')
