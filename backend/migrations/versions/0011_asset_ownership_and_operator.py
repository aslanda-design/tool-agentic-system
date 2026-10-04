"""asset ownership (private vs shared) + operator role

Decision Q15 / ADR 0005: assets are Reference data once VERIFIED, but a user's
free-text import creates an unverified stub that must not pollute everyone.

  * `assets.owner_user_id`: NULL = shared (verified), set = private to that user.
    Defaults to the caller's identity, so user-created assets are private.
  * Users can read shared assets and read/write their own private ones. Writing
    a shared asset requires the `app.system` flag, which only server-side jobs
    and Operator routes set (see app/adapters/persistence/session.py).
  * The security resolver later PROMOTES a verified private stub to shared (or
    MERGES it into an existing shared twin) — see ResolveSecurityUseCase.
  * Symbol/ISIN uniqueness is per scope (shared namespace, and per owner).
  * `users.is_operator`: who may review resolutions / administer.

Existing assets stay shared (owner NULL): they were created before multi-user
and were already resolved against real listings.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("is_operator", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.execute("UPDATE users SET is_operator = true WHERE idp_subject = 'legacy-dev-user'")

    op.execute(
        """
        CREATE FUNCTION app_is_system() RETURNS boolean
        LANGUAGE sql STABLE AS
        $$ SELECT coalesce(current_setting('app.system', true), '') = '1' $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION app_is_system() TO app_user")

    op.execute(
        "ALTER TABLE assets ADD COLUMN owner_user_id uuid REFERENCES users(id) ON DELETE CASCADE"
    )
    op.execute("ALTER TABLE assets ALTER COLUMN owner_user_id SET DEFAULT app_current_user_id()")
    op.execute("CREATE INDEX ix_assets_owner_user_id ON assets (owner_user_id)")

    # uniqueness per scope instead of global
    op.execute("ALTER TABLE assets DROP CONSTRAINT assets_symbol_key")
    op.execute("ALTER TABLE assets DROP CONSTRAINT assets_isin_key")
    op.execute("CREATE UNIQUE INDEX uq_assets_symbol_shared ON assets (symbol) WHERE owner_user_id IS NULL")
    op.execute(
        "CREATE UNIQUE INDEX uq_assets_isin_shared ON assets (isin) "
        "WHERE owner_user_id IS NULL AND isin IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_assets_symbol_private ON assets (owner_user_id, symbol) "
        "WHERE owner_user_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_assets_isin_private ON assets (owner_user_id, isin) "
        "WHERE owner_user_id IS NOT NULL AND isin IS NOT NULL"
    )

    # ENABLE (not FORCE): there are no SECURITY DEFINER helpers on assets, and
    # app_user is not the owner, so plain ENABLE already binds the runtime role.
    op.execute("ALTER TABLE assets ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY assets_select ON assets FOR SELECT USING "
        "(owner_user_id IS NULL OR owner_user_id = app_current_user_id() OR app_is_system())"
    )
    # NULL = NULL is not true: with no identity and no system flag, INSERT is denied,
    # so a shared asset can only ever be created in system mode.
    op.execute(
        "CREATE POLICY assets_insert ON assets FOR INSERT WITH CHECK "
        "(owner_user_id = app_current_user_id() OR app_is_system())"
    )
    op.execute(
        "CREATE POLICY assets_update ON assets FOR UPDATE "
        "USING (owner_user_id = app_current_user_id() OR app_is_system()) "
        "WITH CHECK (owner_user_id = app_current_user_id() OR app_is_system())"
    )
    op.execute(
        "CREATE POLICY assets_delete ON assets FOR DELETE "
        "USING (owner_user_id = app_current_user_id() OR app_is_system())"
    )

    # the session resolver now also reports is_operator
    op.execute("DROP FUNCTION auth_resolve_session(bytea, interval, interval)")
    op.execute(
        """
        CREATE FUNCTION auth_resolve_session(p_id_hash bytea, p_idle interval, p_absolute interval)
        RETURNS TABLE (user_id uuid, recent_auth_at timestamptz, email text, is_operator boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS
        $$
        BEGIN
          RETURN QUERY
          WITH s AS (
            UPDATE sessions SET last_seen_at = now()
            WHERE id_hash = p_id_hash
              AND last_seen_at > now() - p_idle
              AND created_at  > now() - p_absolute
              AND EXISTS (SELECT 1 FROM users u WHERE u.id = sessions.user_id AND u.deleted_at IS NULL)
            RETURNING sessions.user_id, sessions.recent_auth_at
          )
          SELECT s.user_id, s.recent_auth_at, u.email, u.is_operator FROM s JOIN users u ON u.id = s.user_id;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION auth_resolve_session(bytea, interval, interval) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION auth_resolve_session(bytea, interval, interval) TO app_user")


def downgrade() -> None:
    raise NotImplementedError("Pre-launch one-way migration; restore from backup instead.")
