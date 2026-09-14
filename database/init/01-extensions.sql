-- Runs once, automatically, the FIRST time the `db` container starts on an
-- empty data volume (Postgres's docker-entrypoint-initdb.d convention).
-- Only what must exist before the app's own migrations run belongs here —
-- extensions and anything else that requires superuser privileges the
-- application's own DB role may not have. Table/column schema is Alembic's
-- job (backend/migrations), not this file's.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
