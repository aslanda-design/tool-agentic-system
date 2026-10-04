"""Server-side login sessions (docs/saas/03 §3).

The cookie value is 256 random bits; the database stores only its SHA-256, so
a leaked database dump contains no usable session. All table access goes
through SECURITY DEFINER functions (migration 0010) because a session lookup
happens before any tenant identity exists.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings


def cookie_name() -> str:
    # `__Host-` forces Secure + Path=/ + no Domain (blocks cookie injection from
    # sibling subdomains). Browsers reject it without Secure, so dev (plain
    # http://localhost) uses a plain name.
    return "session" if settings.app_env == "dev" else "__Host-session"


def cookie_kwargs() -> dict:
    return {
        "httponly": True,
        "secure": settings.app_env != "dev",
        "samesite": "lax",
        "path": "/",
        "max_age": settings.session_absolute_days * 86400,
    }


def _hash(raw: str) -> bytes:
    return hashlib.sha256(raw.encode()).digest()


@dataclass(frozen=True)
class ResolvedSession:
    user_id: uuid.UUID
    recent_auth_at: datetime
    email: str
    is_operator: bool = False


def create_session(db: Session, user_id: uuid.UUID, user_agent: str | None) -> str:
    raw = secrets.token_urlsafe(32)
    db.execute(
        text("SELECT auth_create_session(:h, :u, :ua)"),
        {"h": _hash(raw), "u": user_id, "ua": (user_agent or "")[:300]},
    )
    db.commit()
    return raw


def resolve_session(db: Session, raw: str) -> ResolvedSession | None:
    row = db.execute(
        text("SELECT * FROM auth_resolve_session(:h, :idle, :abs)"),
        {
            "h": _hash(raw),
            "idle": timedelta(days=settings.session_idle_days),
            "abs": timedelta(days=settings.session_absolute_days),
        },
    ).first()
    db.commit()
    if row is None:
        return None
    return ResolvedSession(row.user_id, row.recent_auth_at, row.email, row.is_operator)


def delete_session(db: Session, raw: str) -> None:
    db.execute(text("SELECT auth_delete_session(:h)"), {"h": _hash(raw)})
    db.commit()


def delete_all_sessions(db: Session, user_id: uuid.UUID) -> None:
    db.execute(text("SELECT auth_delete_user_sessions(:u)"), {"u": user_id})
    db.commit()
