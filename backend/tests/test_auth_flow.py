"""Login flow: PKCE, signed pre-login cookie, ID-token validation, server-side
sessions, CSRF and the callback — all without a live identity provider
(docs/saas/03). A locally generated RSA key stands in for the provider's JWKS."""

from __future__ import annotations

import base64
import hashlib
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.adapters.auth import sessions
from app.adapters.auth.oidc import (
    AuthError,
    new_pkce_pair,
    safe_next_path,
    sign_payload,
    validate_id_token,
    verify_payload,
)
from app.adapters.persistence.session import SessionLocal
from app.api.deps import get_current_user
from app.api.routes import auth as auth_routes
from app.config import settings
from app.main import app

ISS, AUD, SECRET = "https://idp.example/realms/x", "investment-tracker", "s" * 40


# --- PKCE / signed cookie ---------------------------------------------------


def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = new_pkce_pair()
    digest = hashlib.sha256(verifier.encode()).digest()
    assert challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert len(verifier) >= 43  # RFC 7636 minimum


def test_prelogin_cookie_round_trip_and_tamper_detection():
    token = sign_payload({"state": "abc", "nonce": "n"}, SECRET)
    assert verify_payload(token, SECRET)["state"] == "abc"
    raw, mac = token.split(".")
    with pytest.raises(AuthError):
        verify_payload(raw + "." + mac[:-2] + "AA", SECRET)  # tampered mac
    with pytest.raises(AuthError):
        verify_payload(token, "different-secret-" * 3)
    with pytest.raises(AuthError):
        verify_payload("garbage", SECRET)


def test_prelogin_cookie_expires():
    token = sign_payload({"state": "abc"}, SECRET, now=time.time() - 3600)
    with pytest.raises(AuthError, match="expired"):
        verify_payload(token, SECRET)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("/accounts", "/accounts"),
        ("/assistant/3?x=1", "/assistant/3?x=1"),
        (None, "/"),
        ("", "/"),
        ("https://evil.example", "/"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("javascript:alert(1)", "/"),
    ],
)
def test_next_path_is_never_an_open_redirect(value, expected):
    assert safe_next_path(value) == expected


# --- ID token validation -----------------------------------------------------


@pytest.fixture(scope="module")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


def _token(private, **over):
    now = int(time.time())
    claims = {"iss": ISS, "aud": AUD, "sub": "u1", "iat": now, "exp": now + 300, "nonce": "N1"}
    claims.update(over)
    return jwt.encode(claims, private, algorithm="RS256")


def test_valid_id_token_is_accepted(keypair):
    private, public = keypair
    claims = validate_id_token(_token(private), public, ISS, AUD, "N1")
    assert claims["sub"] == "u1"


@pytest.mark.parametrize(
    "over,nonce",
    [
        ({"nonce": "other"}, "N1"),  # replayed/mismatched nonce
        ({"aud": "someone-else"}, "N1"),
        ({"iss": "https://evil.example"}, "N1"),
        ({"exp": int(time.time()) - 3600}, "N1"),  # expired
    ],
)
def test_bad_id_tokens_are_rejected(keypair, over, nonce):
    private, public = keypair
    with pytest.raises(AuthError):
        validate_id_token(_token(private, **over), public, ISS, AUD, nonce)


def test_token_signed_by_another_key_is_rejected(keypair):
    _, public = keypair
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthError):
        validate_id_token(_token(other), public, ISS, AUD, "N1")


def test_alg_none_and_hmac_confusion_are_rejected(keypair):
    _private, public = keypair
    now = int(time.time())
    claims = {"iss": ISS, "aud": AUD, "sub": "u1", "iat": now, "exp": now + 300, "nonce": "N1"}
    unsigned = jwt.encode(claims, key=None, algorithm="none")
    with pytest.raises(AuthError):
        validate_id_token(unsigned, public, ISS, AUD, "N1")
    # HS256 "signed" with the public key bytes: the classic algorithm-confusion attack
    from cryptography.hazmat.primitives import serialization

    pem = public.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    # built by hand: PyJWT itself refuses to encode HS256 with an asymmetric key
    import hmac as _hmac
    import json as _json

    def _b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    signing_input = (
        _b64(_json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        + "."
        + _b64(_json.dumps(claims).encode())
    )
    sig = _hmac.new(pem, signing_input.encode(), hashlib.sha256).digest()
    forged = signing_input + "." + _b64(sig)
    with pytest.raises(AuthError):
        validate_id_token(forged, public, ISS, AUD, "N1")


# --- server-side sessions ------------------------------------------------------


@pytest.fixture
def raw_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_session_round_trip_and_logout(raw_session, tenant_user_id):
    raw = sessions.create_session(raw_session, tenant_user_id, "pytest")
    resolved = sessions.resolve_session(raw_session, raw)
    assert resolved is not None and resolved.user_id == tenant_user_id
    sessions.delete_session(raw_session, raw)
    assert sessions.resolve_session(raw_session, raw) is None


def test_unknown_or_forged_cookie_resolves_to_nothing(raw_session):
    assert sessions.resolve_session(raw_session, "not-a-real-session") is None


def test_idle_session_expires(raw_session, tenant_user_id, monkeypatch):
    raw = sessions.create_session(raw_session, tenant_user_id, "pytest")
    monkeypatch.setattr(settings, "session_idle_days", 0)
    assert sessions.resolve_session(raw_session, raw) is None
    sessions.delete_session(raw_session, raw)


def test_log_out_everywhere(raw_session, tenant_user_id):
    a = sessions.create_session(raw_session, tenant_user_id, "laptop")
    b = sessions.create_session(raw_session, tenant_user_id, "phone")
    sessions.delete_all_sessions(raw_session, tenant_user_id)
    assert sessions.resolve_session(raw_session, a) is None
    assert sessions.resolve_session(raw_session, b) is None


def test_only_the_hash_is_stored(raw_session, tenant_user_id):
    raw = sessions.create_session(raw_session, tenant_user_id, "pytest")
    try:
        # The app role cannot read the table at all; check via the owner connection.
        from sqlalchemy import create_engine, text

        owner = create_engine(settings.migration_database_url or settings.database_url)
        with owner.connect() as c:
            stored = c.execute(text("SELECT id_hash FROM sessions")).scalars().all()
        assert raw.encode() not in stored
        assert hashlib.sha256(raw.encode()).digest() in stored
    finally:
        sessions.delete_session(raw_session, raw)


# --- HTTP: cookie auth, CSRF, login redirect, callback -------------------------


@pytest.fixture
def oidc_mode(monkeypatch):
    app.dependency_overrides.pop(get_current_user, None)
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "session_secret", SECRET)
    monkeypatch.setattr(settings, "oidc_client_secret", "x")


def test_cookie_session_authenticates_and_csrf_protects_writes(
    oidc_mode, raw_session, tenant_user_id
):
    raw = sessions.create_session(raw_session, tenant_user_id, "pytest")
    try:
        with TestClient(app) as client:
            client.cookies.set(sessions.cookie_name(), raw)
            assert client.get("/api/auth/me").json()["id"] == str(tenant_user_id)
            body = {"broker_key": "ibkr", "name": "csrf-test", "currency": "EUR"}
            assert client.post("/api/accounts", json=body).status_code == 403  # no header
            ok = client.post("/api/accounts", json=body, headers={"X-Requested-With": "fetch"})
            assert ok.status_code == 200
            out = client.post("/api/auth/logout", headers={"X-Requested-With": "fetch"})
            assert out.status_code == 200
            assert client.get("/api/auth/me").status_code == 401  # session really gone
    finally:
        sessions.delete_session(raw_session, raw)
        from app.adapters.persistence.session import scoped_session

        d = scoped_session(tenant_user_id)
        d.execute(__import__("sqlalchemy").text("DELETE FROM accounts WHERE user_id = :u"), {"u": tenant_user_id})
        d.commit()
        d.close()


class FakeOidc:
    def authorization_url(self, state, nonce, challenge, prompt):
        self.nonce = nonce
        return f"https://idp.example/auth?state={state}&challenge={challenge}"

    def exchange_code(self, code, verifier):
        assert code == "good-code" and verifier
        return {"id_token": "stub"}

    def verify_id_token(self, id_token, nonce):
        assert nonce == self.nonce  # the callback must pass back OUR nonce
        return {"sub": "kc-user-1", "email": "new@example.com", "email_verified": True}


def test_login_then_callback_creates_a_session(oidc_mode, monkeypatch):
    fake = FakeOidc()
    monkeypatch.setattr(auth_routes, "get_oidc_client", lambda: fake)
    with TestClient(app, follow_redirects=False) as client:
        r = client.get("/api/auth/login", params={"next": "/accounts"})
        assert r.status_code == 302 and r.headers["location"].startswith("https://idp.example/auth")
        state = r.headers["location"].split("state=")[1].split("&")[0]

        # wrong state (CSRF / login-fixation attempt) is rejected
        bad = client.get("/api/auth/callback", params={"code": "good-code", "state": "wrong"})
        assert bad.status_code == 400

        good = client.get("/api/auth/callback", params={"code": "good-code", "state": state})
        assert good.status_code == 302
        assert good.headers["location"].endswith("/accounts")
        cookie = good.headers.get_list("set-cookie")
        session_cookie = next(c for c in cookie if c.startswith(sessions.cookie_name() + "="))
        assert "httponly" in session_cookie.lower() and "samesite=lax" in session_cookie.lower()

        me = client.get("/api/auth/me")
        assert me.status_code == 200 and me.json()["email"] == "new@example.com"
        client.post("/api/auth/logout", headers={"X-Requested-With": "fetch"})


def test_callback_without_prelogin_cookie_is_rejected(oidc_mode, monkeypatch):
    monkeypatch.setattr(auth_routes, "get_oidc_client", lambda: FakeOidc())
    with TestClient(app, follow_redirects=False) as client:
        r = client.get("/api/auth/callback", params={"code": "good-code", "state": "x"})
        assert r.status_code == 400


def test_login_route_is_disabled_in_dev_mode():
    with TestClient(app, follow_redirects=False) as client:
        assert client.get("/api/auth/login").status_code == 404
