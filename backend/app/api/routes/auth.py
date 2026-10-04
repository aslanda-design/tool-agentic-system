"""Login/logout routes (OIDC authorization-code + PKCE, backend-for-frontend).

Flow and rationale: docs/saas/03-authentication-and-sessions.md. Tokens never
reach the browser: the backend exchanges the code, validates the ID token,
creates a server-side session and hands the browser only an opaque HttpOnly
cookie.
"""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.adapters.auth import sessions
from app.adapters.auth.oidc import (
    AuthError,
    OidcClient,
    new_pkce_pair,
    safe_next_path,
    sign_payload,
    verify_payload,
)
from app.adapters.persistence.session import raw_db
from app.api.deps import CSRF_HEADER, CSRF_VALUE, CurrentUser, get_current_user
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

PRELOGIN_COOKIE = "prelogin"


@lru_cache
def get_oidc_client() -> OidcClient:
    return OidcClient(
        settings.oidc_issuer,
        settings.oidc_client_id,
        settings.oidc_client_secret,
        settings.oidc_redirect_uri,
    )


def _require_oidc_mode() -> None:
    if settings.auth_mode != "oidc":
        raise HTTPException(status_code=404, detail="Login is not enabled (AUTH_MODE != oidc)")


@router.get("/login")
def login(next: str = "/", prompt: str | None = None):
    _require_oidc_mode()
    verifier, challenge = new_pkce_pair()
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    url = get_oidc_client().authorization_url(state, nonce, challenge, prompt)
    resp = RedirectResponse(url, status_code=302)
    cookie = sign_payload(
        {"state": state, "nonce": nonce, "verifier": verifier, "next": safe_next_path(next)},
        settings.session_secret,
    )
    resp.set_cookie(
        PRELOGIN_COOKIE,
        cookie,
        max_age=600,
        httponly=True,
        secure=settings.app_env != "dev",
        samesite="lax",
        path="/api/auth",
    )
    return resp


@router.get("/callback")
def callback(
    request: Request,
    db: Annotated[Session, Depends(raw_db)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    _require_oidc_mode()
    if error or not code or not state:
        raise HTTPException(status_code=400, detail="Login was not completed")
    try:
        pre = verify_payload(request.cookies.get(PRELOGIN_COOKIE, ""), settings.session_secret)
        if not secrets.compare_digest(pre["state"], state):
            raise AuthError("state mismatch")
        client = get_oidc_client()
        tokens = client.exchange_code(code, pre["verifier"])
        claims = client.verify_id_token(tokens["id_token"], pre["nonce"])
    except AuthError as exc:
        logger.warning("Login rejected: %s", exc)
        raise HTTPException(status_code=400, detail="Login failed") from exc

    email_verified = bool(claims.get("email_verified", False))
    if settings.app_env != "dev" and not email_verified:
        raise HTTPException(status_code=403, detail="Please verify your email address first")

    user_id = db.execute(
        text("SELECT auth_upsert_user(:s, :e, :v)"),
        {"s": claims["sub"], "e": claims.get("email", ""), "v": email_verified},
    ).scalar_one_or_none()
    db.commit()
    if user_id is None:  # account was deleted
        raise HTTPException(status_code=403, detail="This account is no longer active")

    raw = sessions.create_session(db, user_id, request.headers.get("user-agent"))
    resp = RedirectResponse(settings.app_base_url.rstrip("/") + safe_next_path(pre.get("next")), 302)
    resp.set_cookie(sessions.cookie_name(), raw, **sessions.cookie_kwargs())
    resp.delete_cookie(PRELOGIN_COOKIE, path="/api/auth")
    return resp


@router.post("/logout")
def logout(request: Request, db: Annotated[Session, Depends(raw_db)]):
    # CSRF: a cross-site form post cannot set this header.
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status_code=403, detail="Missing CSRF header")
    raw = request.cookies.get(sessions.cookie_name())
    if raw:
        sessions.delete_session(db, raw)
    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie(sessions.cookie_name(), path="/")
    return resp


@router.get("/me")
def me(user: Annotated[CurrentUser, Depends(get_current_user)]):
    return {"id": str(user.id), "email": user.email, "auth_mode": settings.auth_mode}
