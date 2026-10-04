"""OpenID Connect (authorization-code + PKCE) building blocks.

Provider-agnostic: speaks plain OIDC discovery/JWKS, so Keycloak (local dev),
Zitadel, Ory, Auth0... are interchangeable by configuration (ADR 0004).
Everything security-relevant is here and unit-tested without a live provider:

  * PKCE verifier/challenge, state and nonce generation
  * an HMAC-signed, expiring pre-login cookie (carries state/nonce/verifier/next)
  * ID-token validation: signature via the provider's JWKS, pinned algorithms,
    `iss`, `aud`, `exp`, `nonce` — never trust an unverified token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

ALLOWED_ALGS = ["RS256", "ES256"]  # never "none", never HS* (algorithm confusion)
PRELOGIN_TTL_SECONDS = 600
CLOCK_SKEW_SECONDS = 60


class AuthError(Exception):
    """Login failed; the message is safe to log but not to show verbatim."""


# --- PKCE / state ----------------------------------------------------------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def new_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


# --- signed pre-login cookie ----------------------------------------------


def sign_payload(payload: dict, secret: str, now: float | None = None) -> str:
    body = dict(payload, exp=(now or time.time()) + PRELOGIN_TTL_SECONDS)
    raw = _b64url(json.dumps(body, separators=(",", ":")).encode())
    mac = _b64url(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest())
    return f"{raw}.{mac}"


def verify_payload(token: str, secret: str, now: float | None = None) -> dict:
    try:
        raw, mac = token.split(".", 1)
    except ValueError as exc:
        raise AuthError("malformed pre-login cookie") from exc
    expected = _b64url(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(mac, expected):
        raise AuthError("bad pre-login cookie signature")
    body = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    if body.get("exp", 0) < (now or time.time()):
        raise AuthError("pre-login cookie expired")
    return body


def safe_next_path(value: str | None) -> str:
    """Only same-site relative paths — an open redirect after login is phishing."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


# --- provider interaction --------------------------------------------------


@dataclass(frozen=True)
class ProviderMetadata:
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None
    issuer: str


class OidcClient:
    def __init__(self, issuer: str, client_id: str, client_secret: str, redirect_uri: str):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._meta: ProviderMetadata | None = None
        self._jwks: PyJWKClient | None = None

    def metadata(self) -> ProviderMetadata:
        if self._meta is None:
            r = httpx.get(f"{self.issuer}/.well-known/openid-configuration", timeout=10)
            r.raise_for_status()
            d = r.json()
            self._meta = ProviderMetadata(
                d["authorization_endpoint"],
                d["token_endpoint"],
                d["jwks_uri"],
                d.get("end_session_endpoint"),
                d["issuer"],
            )
        return self._meta

    def authorization_url(self, state: str, nonce: str, challenge: str, prompt: str | None) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if prompt:
            params["prompt"] = prompt
        return f"{self.metadata().authorization_endpoint}?{urlencode(params)}"

    def exchange_code(self, code: str, verifier: str) -> dict:
        r = httpx.post(
            self.metadata().token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code_verifier": verifier,
            },
            timeout=10,
        )
        if r.status_code != 200:
            raise AuthError(f"token endpoint returned {r.status_code}")
        return r.json()

    def verify_id_token(self, id_token: str, nonce: str, signing_key=None) -> dict:
        """`signing_key` is injectable so tests need no live JWKS endpoint."""
        meta = self.metadata() if signing_key is None else None
        if signing_key is None:
            if self._jwks is None:
                self._jwks = PyJWKClient(meta.jwks_uri)
            signing_key = self._jwks.get_signing_key_from_jwt(id_token).key
        return validate_id_token(
            id_token, signing_key, self.issuer, self.client_id, nonce
        )


def validate_id_token(id_token: str, key, issuer: str, audience: str, nonce: str) -> dict:
    try:
        claims = jwt.decode(
            id_token,
            key,
            algorithms=ALLOWED_ALGS,
            audience=audience,
            issuer=issuer,
            leeway=CLOCK_SKEW_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"invalid id_token: {exc}") from exc
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise AuthError("nonce mismatch")
    return claims
