"""Request-scoped dependencies: who is calling, and a database session bound to them.

Every business route gets its session from `get_db` below, which depends on
`get_current_user` — so authentication is structural: a route cannot reach
Private data without an identity (tests/tenancy/test_route_auth.py enforces it
for the whole route table).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.adapters.persistence.session import raw_db, scoped_session
from app.config import settings

LEGACY_DEV_SUBJECT = "legacy-dev-user"


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    email: str = ""


def get_current_user(request: Request, db: Annotated[Session, Depends(raw_db)]) -> CurrentUser:
    """Resolve the caller or raise 401.

    auth_mode="dev" (refused outside app_env=dev at startup): every request is
    the legacy dev user — local convenience only, never a production path.
    """
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

