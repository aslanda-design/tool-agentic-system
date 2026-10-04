"""users + per-user ownership + row-level security

Phase 1 of plans/saas_launch.md; design in docs/saas/02-multi-tenancy-and-rls.md
and ADR 0005. Pre-launch there is exactly one real dataset (the founder's),
so unlike a live system this does expand + backfill + contract in one step:
every existing row is assigned to a "legacy" user created here.

What it does:
  * `users` table + `app_current_user_id()` (reads the per-transaction
    `app.user_id` setting; NULL when unset, so RLS fails closed).
  * `user_id` on every Private-data table, defaulting to the current identity
    so existing INSERT code needs no change; WITH CHECK still enforces it.
  * Composite foreign keys so a child row can never belong to a different user
    than its parent.
  * ENABLE + FORCE row-level security, one policy per table.
  * `app_user` runtime role (non-owner, NOBYPASSRLS). Table owners and
    superusers bypass RLS, so the app must NOT connect as the owner.
  * SECURITY DEFINER helpers for the few things that must happen *before* an
    identity exists (login upsert, listing users for the scheduler).
"""

import os

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

# (table, extra: how it joins to its parent for the composite FK)
PRIVATE_TABLES = [
    "accounts",
    "holdings",
    "transactions",
    "cash_balances",
    "position_snapshots",
    "portfolio_snapshots",
    "ai_notes",
    "chat_sessions",
    "chat_messages",
    "quant_runs",
]
# agent_runs is special: the security_resolver writes system runs with no user.
LEGACY_SUBJECT = "legacy-dev-user"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")  # gen_random_uuid() on PG<13 safety

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("idp_subject", sa.Text(), nullable=False, unique=True),
        sa.Column("email", sa.Text(), nullable=False, server_default=""),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("plan", sa.Text(), nullable=False, server_default="free"),
        sa.Column("ai_consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(
        """
        CREATE FUNCTION app_current_user_id() RETURNS uuid
        LANGUAGE sql STABLE AS
        $$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$
        """
    )

    # The founder's existing data becomes the legacy user's.
    op.execute(
        f"INSERT INTO users (idp_subject, email) VALUES ('{LEGACY_SUBJECT}', 'legacy@localhost')"
    )

    for table in PRIVATE_TABLES:
        op.execute(f"ALTER TABLE {table} ADD COLUMN user_id uuid")
        op.execute(
            f"UPDATE {table} SET user_id = (SELECT id FROM users WHERE idp_subject = '{LEGACY_SUBJECT}')"
        )
        op.execute(f"ALTER TABLE {table} ALTER COLUMN user_id SET NOT NULL")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN user_id SET DEFAULT app_current_user_id()")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_user_fk "
            f"FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        )
        op.execute(f"CREATE INDEX ix_{table}_user_id ON {table} (user_id)")

    # agent_runs: nullable user_id (NULL = system run by the worker/resolver).
    op.execute("ALTER TABLE agent_runs ADD COLUMN user_id uuid")
    op.execute("ALTER TABLE agent_runs ALTER COLUMN user_id SET DEFAULT app_current_user_id()")
    op.execute(
        "ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_user_fk "
        "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
    )
    op.execute("CREATE INDEX ix_agent_runs_user_id ON agent_runs (user_id)")
    # Existing agent runs: attribute to the legacy user except resolver runs (system).
    op.execute(
        f"UPDATE agent_runs SET user_id = (SELECT id FROM users WHERE idp_subject = '{LEGACY_SUBJECT}') "
        "WHERE agent <> 'security_resolver'"
    )

    # --- uniqueness that used to be global becomes per-user ---
    op.execute("ALTER TABLE accounts DROP CONSTRAINT accounts_broker_key_external_id_key")
    op.execute(
        "ALTER TABLE accounts ADD CONSTRAINT accounts_user_broker_external_key "
        "UNIQUE (user_id, broker_key, external_id)"
    )
    op.execute("ALTER TABLE position_snapshots DROP CONSTRAINT position_snapshots_pkey")
    op.execute(
        "ALTER TABLE position_snapshots ADD PRIMARY KEY (user_id, date, asset_id)"
    )
    op.execute("ALTER TABLE portfolio_snapshots DROP CONSTRAINT portfolio_snapshots_pkey")
    op.execute("ALTER TABLE portfolio_snapshots ADD PRIMARY KEY (user_id, date)")

    # --- children can never belong to a different user than their parent ---
    op.execute("ALTER TABLE accounts ADD CONSTRAINT accounts_id_user_key UNIQUE (id, user_id)")
    for child in ("holdings", "transactions", "cash_balances", "ai_notes"):
        op.execute(
            f"ALTER TABLE {child} ADD CONSTRAINT {child}_account_user_fk "
            f"FOREIGN KEY (account_id, user_id) REFERENCES accounts (id, user_id) ON DELETE CASCADE"
        )
    op.execute(
        "ALTER TABLE chat_sessions ADD CONSTRAINT chat_sessions_id_user_key UNIQUE (id, user_id)"
    )
    op.execute(
        "ALTER TABLE chat_messages ADD CONSTRAINT chat_messages_session_user_fk "
        "FOREIGN KEY (session_id, user_id) REFERENCES chat_sessions (id, user_id) ON DELETE CASCADE"
    )

    # --- runtime role: non-owner, subject to RLS ---
    password = os.environ.get("APP_DB_PASSWORD", "app_user_dev")
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
            CREATE ROLE app_user LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{password}';
          END IF;
        END $$
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO app_user")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO app_user"
    )
    # alembic_version is not the app's business.
    op.execute("REVOKE ALL ON alembic_version FROM app_user")

    # --- row-level security ---
    for table in PRIVATE_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_isolation ON {table} "
            f"USING (user_id = app_current_user_id()) "
            f"WITH CHECK (user_id = app_current_user_id())"
        )
    op.execute("ALTER TABLE agent_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY agent_runs_isolation ON agent_runs "
        "USING (user_id = app_current_user_id()) "
        "WITH CHECK (user_id IS NOT DISTINCT FROM app_current_user_id())"
    )

    # users: a user can see only their own row. Login/lookup go through the
    # SECURITY DEFINER functions below, which run before an identity exists.
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY users_self ON users USING (id = app_current_user_id())")
    op.execute("REVOKE INSERT, UPDATE, DELETE ON users FROM app_user")

    op.execute(
        """
        CREATE FUNCTION auth_upsert_user(p_subject text, p_email text, p_email_verified boolean)
        RETURNS uuid LANGUAGE sql SECURITY DEFINER SET search_path = public AS
        $$
          INSERT INTO users (idp_subject, email, email_verified)
          VALUES (p_subject, p_email, p_email_verified)
          ON CONFLICT (idp_subject) DO UPDATE
            SET email = EXCLUDED.email, email_verified = EXCLUDED.email_verified
          WHERE users.deleted_at IS NULL
          RETURNING id
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION auth_list_active_user_ids() RETURNS SETOF uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
        $$ SELECT id FROM users WHERE deleted_at IS NULL ORDER BY created_at $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION auth_upsert_user(text, text, boolean) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION auth_list_active_user_ids() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION auth_upsert_user(text, text, boolean) TO app_user")
    op.execute("GRANT EXECUTE ON FUNCTION auth_list_active_user_ids() TO app_user")
    op.execute("GRANT EXECUTE ON FUNCTION app_current_user_id() TO app_user")


def downgrade() -> None:
    raise NotImplementedError(
        "0009 is a one-way migration (pre-launch). Restore from backup instead."
    )
