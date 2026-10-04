"""Request-scoped dependencies: who is calling, and a database session bound to them.

Every business route gets its session from `get_db` below, which depends on
`get_current_user` — so authentication is structural: a route cannot reach
Private data without an identity (tests/tenancy/test_route_auth.py enforces it
for the whole route table).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.adapters.auth import sessions
from app.adapters.persistence.session import raw_db, scoped_session
from app.config import settings

LEGACY_DEV_SUBJECT = "legacy-dev-user"

# CSRF defence for cookie sessions: state-changing requests must carry a custom
# header. A cross-site form/image/navigation cannot set one, and a cross-site
# fetch that tries needs a CORS preflight we do not grant. SameSite=Lax on the
# cookie is the first layer; this is the second (docs/saas/03 §3).
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "fetch"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    email: str = ""
    recent_auth_at: datetime | None = None


def get_current_user(request: Request, db: Annotated[Session, Depends(raw_db)]) -> CurrentUser:
    """Resolve the caller or raise 401.

    auth_mode="oidc": the opaque session cookie is looked up server-side.
    auth_mode="dev" (refused outside app_env=dev at startup): every request is
    the legacy dev user — local convenience only, never a production path.
    """
    if settings.auth_mode == "oidc":
        raw = request.cookies.get(sessions.cookie_name())
        if not raw:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            raise HTTPException(status_code=403, detail="Missing CSRF header")
        resolved = sessions.resolve_session(db, raw)
        if resolved is None:
            raise HTTPException(status_code=401, detail="Session expired")
        return CurrentUser(resolved.user_id, resolved.email, resolved.recent_auth_at)

    if settings.auth_mode == "dev" and settings.app_env == "dev":
        uid = db.execute(
            text("SELECT auth_upsert_user(:s, :e, true)"),
            {"s": LEGACY_DEV_SUBJECT, "e": "legacy@localhost"},
        ).scalar_one()
        db.commit()
        return CurrentUser(id=uid, email="legacy@localhost")
    raise HTTPException(status_code=401, detail="Not authenticated")


def get_db(user: Annotated[CurrentUser, Depends(get_current_user)]):
    """A session bound to the authenticated user (RLS applies)."""
    db = scoped_session(user.id)
    try:
        yield db
    finally:
        db.close()

