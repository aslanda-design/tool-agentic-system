"""server-side login sessions (docs/saas/03)

The browser holds an opaque random cookie; only its SHA-256 hash is stored
here, so a database leak yields no live sessions. The table is reachable ONLY
through the SECURITY DEFINER functions below: `app_user` has no privileges on
it, because login/session lookup happens before any tenant identity exists.

Also corrects 0009: `users` must NOT be FORCE ROW LEVEL SECURITY. The
SECURITY DEFINER helpers run as the table owner, and with FORCE the owner is
subject to the policies too — invisible when the owner is a superuser (local
Docker), fatal in production where it is not. `app_user` is not the owner, so
plain ENABLE already binds it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users NO FORCE ROW LEVEL SECURITY")

    op.create_table(
        "sessions",
        sa.Column("id_hash", sa.LargeBinary(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        # When the user last proved their identity — for step-up checks
        # before deletion/export/email change.
        sa.Column(
            "recent_auth_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("user_agent", sa.Text(), nullable=True),
    )
    op.execute("CREATE INDEX ix_sessions_user_id ON sessions (user_id)")
    op.execute("ALTER TABLE sessions ENABLE ROW LEVEL SECURITY")  # no policy + no grant = no access
    op.execute("REVOKE ALL ON sessions FROM app_user")
    # Default privileges from 0009 auto-granted DML on the new table.

    op.execute(
        """
        CREATE FUNCTION auth_create_session(p_id_hash bytea, p_user_id uuid, p_user_agent text)
        RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = public AS
        $$
          INSERT INTO sessions (id_hash, user_id, user_agent) VALUES (p_id_hash, p_user_id, p_user_agent)
        $$
        """
    )
    # Returns the owner of a still-valid session and slides its idle timer.
    # Valid = seen within `p_idle`, created within `p_absolute`, user not deleted.
    op.execute(
        """
        CREATE FUNCTION auth_resolve_session(p_id_hash bytea, p_idle interval, p_absolute interval)
        RETURNS TABLE (user_id uuid, recent_auth_at timestamptz, email text)
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
          SELECT s.user_id, s.recent_auth_at, u.email FROM s JOIN users u ON u.id = s.user_id;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION auth_delete_session(p_id_hash bytea) RETURNS void
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS
        $$ DELETE FROM sessions WHERE id_hash = p_id_hash $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION auth_delete_user_sessions(p_user_id uuid) RETURNS void
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS
        $$ DELETE FROM sessions WHERE user_id = p_user_id $$
        """
    )
    for sig in (
        "auth_create_session(bytea, uuid, text)",
        "auth_resolve_session(bytea, interval, interval)",
        "auth_delete_session(bytea)",
        "auth_delete_user_sessions(uuid)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {sig} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {sig} TO app_user")


def downgrade() -> None:
    raise NotImplementedError("Pre-launch one-way migration; restore from backup instead.")
