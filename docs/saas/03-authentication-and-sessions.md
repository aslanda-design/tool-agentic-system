# 03 — Authentication and sessions

## 1. The problem

Authentication code is where small mistakes become account takeovers:
password storage, reset flows, rate limiting, email verification, MFA,
session fixation, token theft. None of it differentiates your product, and all
of it has a long history of subtle bugs. Decision Q7 (ADR 0004): **don't
write it.**

You still own three things: **verifying who is calling**, **mapping that
identity to a `users` row**, and **how the browser holds the session**.

## 2. Options

### 2.1 Where identity comes from

| Option | Notes |
|--------|-------|
| Hand-rolled (argon2 + sessions) | Total control; you own every vulnerability. Rejected. |
| **Managed provider (OIDC)** | Passwords, MFA, social login, reset, brute-force protection handled for you. Pick EU residency. |
| Self-hosted (Keycloak, Zitadel, Authentik) | No per-user fees, but you operate a critical security service. |

Provider shortlist (pricing from secondary sources in Oct 2026 — ⚠ verify on
each vendor's page):

| Provider | EU residency | Free tier (approx.) | Notes |
|----------|--------------|--------------------|-------|
| Zitadel Cloud | Yes (EU region; Swiss company) | small / paid from ~€100/mo reported | OIDC-native, good fit for "serious" |
| Ory Network | Yes (Munich) | paid from ~€29/mo reported | Strong API-first design |
| Auth0 | EU tenant available | free tier, expensive at scale | Mature, many tutorials |
| Supabase Auth | EU region | 100k MAU on Pro | Cheap; couples you to Supabase's stack |
| Clerk | Mostly US-centric ⚠ | generous free tier | Excellent DX but check transfer terms |
| WorkOS AuthKit | Residency not clearly documented ⚠ | free to 1M MAU | Great for B2B SSO; less relevant for B2C |
| Keycloak (self-hosted) | You choose | free | You operate it |

**Recommendation:** default to **Zitadel Cloud (EU)** or **Ory Network**,
decided by a one-day spike in Phase 0: wire a FastAPI dependency that verifies
a token and check the signup/MFA/reset UX. Record the pick as ADR 0008. The
code below is provider-agnostic because it speaks plain OIDC/JWT.

### 2.2 How the browser holds the session

| Option | How | Pros | Cons |
|--------|-----|------|------|
| **A. SPA holds tokens** (Auth code + PKCE; access token in memory, refresh token rotation) | JS calls API with `Authorization: Bearer` | Simple, stateless API | Any XSS can steal tokens; refresh token handling in the browser |
| **B. Backend-for-Frontend (BFF) cookie session** | Backend does the OIDC code exchange; browser only has an opaque `HttpOnly` cookie | Tokens never reach JS; instant server-side revocation; CSRF is the only extra concern | Backend keeps a session table |

**Decision: B.** This is a finance app: XSS resilience and the ability to
"log out everywhere" are worth a small session table. The nginx setup already
puts frontend and API behind one origin, which is what makes cookie sessions
simple (no cross-site cookie gymnastics).

## 3. Decision and why — the flow

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser
  participant API as FastAPI (BFF)
  participant IDP as Auth provider
  participant DB as Postgres
  B->>API: GET /api/auth/login
  API-->>B: 302 → IDP /authorize (state, nonce, PKCE challenge)
  B->>IDP: user signs in (password/Google/MFA)
  IDP-->>B: 302 → /api/auth/callback?code=…&state=…
  B->>API: callback
  API->>IDP: exchange code + PKCE verifier (back-channel)
  IDP-->>API: id_token (+ access/refresh)
  API->>API: verify signature (JWKS), iss, aud, exp, nonce
  API->>DB: upsert users(idp_subject, email); create session (hash of random id)
  API-->>B: Set-Cookie __Host-session=<random>; HttpOnly; Secure; SameSite=Lax
  B->>API: GET /api/portfolio/summary (cookie)
  API->>DB: session → user_id; set RLS identity
  API-->>B: 200
```

Key properties:

- The cookie holds a **random opaque id**; the database stores only its
  **hash** (a DB leak doesn't yield live sessions).
- `__Host-` prefix forces `Secure`, `Path=/`, no `Domain` — blocks cookie
  injection from sibling subdomains.
- **CSRF:** `SameSite=Lax` already blocks cross-site POSTs; also require a
  custom header (e.g. `X-Requested-With`) or a double-submit token on
  state-changing requests, and keep GET side-effect-free.
- **Session lifetime:** idle timeout 30 days sliding for convenience vs
  shorter for sensitive actions; require **re-authentication** (recent login
  within N minutes) for account deletion, export, email change, adding
  credentials.
- **Revocation:** delete the session row. "Log out everywhere" = delete all
  rows for that user. Provider-side disable also blocks the next refresh.

## 4. In this repo

### 4.1 Tables

```sql
CREATE TABLE users (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  idp_subject  text UNIQUE NOT NULL,          -- the provider's stable `sub`
  email        text NOT NULL,                  -- display/contact; NOT the identity key
  email_verified boolean NOT NULL DEFAULT false,
  plan         text NOT NULL DEFAULT 'free',
  ai_consent_at timestamptz NULL,              -- see 05/07
  created_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz NULL
);

CREATE TABLE sessions (
  id_hash      bytea PRIMARY KEY,              -- sha256(random cookie value)
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  recent_auth_at timestamptz NOT NULL,         -- for step-up checks
  user_agent   text, ip inet
);
```

Key by `sub`, **not email** (emails change and can be recycled across
providers). Treat `email_verified` from the provider as required before
enabling anything sensitive.

### 4.2 The dependency every route uses

```python
# app/api/deps.py
def get_current_user(request: Request, db: Session = Depends(get_raw_db)) -> User:
    raw = request.cookies.get("__Host-session")
    if not raw:
        raise HTTPException(401)
    sess = sessions.find_valid(db, sha256(raw))        # not expired, user not deleted
    if not sess:
        raise HTTPException(401)
    sessions.touch(db, sess)                             # slide idle timeout (throttled)
    return sess.user

def require_recent_auth(minutes: int = 10):
    def dep(user: User = Depends(get_current_user), sess = Depends(get_session)):
        if now() - sess.recent_auth_at > timedelta(minutes=minutes):
            raise HTTPException(401, headers={"X-Reauth": "required"})
        return user
    return dep
```

Routers get a router-level dependency so nothing is accidentally public:

```python
app.include_router(portfolio.router, prefix="/api", dependencies=[Depends(get_current_user)])
```

Public routes (`/health`, `/api/auth/*`, pricing page assets) are an explicit
allow-list. A CI test enumerates `app.routes` and fails if a route outside the
allow-list lacks the dependency.

### 4.3 Roles

Only two matter at launch: **User** and **Operator** (reviews Security
resolutions, sees aggregate health). Operator is a claim/flag verified
server-side, requires MFA, and its actions go to the audit log (04). Operator
UI is a separate route group; ordinary Users never see resolver review
(`ResolutionsCard` in `AccountsPage.tsx` moves behind the Operator role).

### 4.4 Account lifecycle events

| Event | Behaviour |
|-------|-----------|
| First login | Upsert `users`; create empty state; show onboarding + terms acceptance (record version + timestamp) |
| Email change | Re-verify; notify the old address |
| Password reset / MFA change | Provider-side; also invalidate all sessions |
| Delete account | Step-up auth → mark `deleted_at`, revoke sessions → purge job (05) |
| Provider outage | Existing sessions keep working (they are in your DB); only new logins fail — a benefit of the BFF design |

## 5. What goes wrong in production

- **Trusting an unverified token.** Always verify signature **and** `iss`,
  `aud`, `exp`, `nonce`; pin accepted algorithms (`RS256`/`ES256`), never
  accept `alg: none` or algorithm confusion.
- **Keying by email.** Account takeover when an email is re-registered or
  unverified at the provider.
- **Open redirects** in the post-login `next=` parameter → phishing. Allow-list
  relative paths only.
- **Missing logout on the provider.** Deleting your cookie but not the
  provider session means "log in again" is instant. Provide RP-initiated
  logout where it matters.
- **Enumeration.** Login/reset responses that differ for existing vs
  non-existing emails (the provider usually handles this; verify).
- **No rate limit on `/api/auth/*`.** Apply per-IP limits at the edge and in-app.
- **Clock skew** breaking `exp`/`nbf`; allow a small leeway (≤60s).
- **Long-lived sessions without step-up** for dangerous actions.
- **Local dev auth hacks leaking to prod** (a "dev login" backdoor). Gate on
  `APP_ENV` and have the startup guard refuse it in production.

## 6. Checklist

- [ ] Provider chosen, EU residency written down (ADR 0008)
- [ ] OIDC code flow + PKCE + nonce; ID token fully validated
- [ ] Cookie: `__Host-`, `HttpOnly`, `Secure`, `SameSite=Lax`; session id hashed at rest
- [ ] CSRF defence on state-changing requests
- [ ] Router-level auth dependency; CI test for public-route allow-list
- [ ] Users keyed by `sub`; email verified before sensitive features
- [ ] Step-up auth for delete/export/email change
- [ ] Operator role requires MFA; actions audited
- [ ] "Log out everywhere" works and is tested
- [ ] No dev-only login path in production images
