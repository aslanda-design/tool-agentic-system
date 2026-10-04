# 09 — Billing and business model

## 1. The problem

Charging EU consumers online brings: VAT at the *customer's* country rate,
invoicing rules, consumer withdrawal rights, payment-failure handling,
chargebacks, and security of payment data. Doing this directly as a solo
founder means registering for VAT schemes, building invoice logic, and storing
(or at least touching) payment data. The decision (Q3) was a **merchant of
record (MoR)** and a **freemium** model.

## 2. Options

| Option | Who is the seller of record? | You handle |
|--------|------------------------------|------------|
| **A. Payment processor only** (e.g. Stripe Billing + Stripe Tax) | You | VAT registration/filing (OSS), invoices, disputes, refunds, compliance |
| **B. Merchant of record** (Paddle, Lemon Squeezy, Polar, etc.) | They sell to the customer; you sell to them | Almost nothing tax-related; you get a payout |
| C. Free product / donations | n/a | Nothing — but no revenue |

MoR fees reported in 2026 are roughly **5% + $0.50 per transaction** for the
main players, sometimes with surcharges (⚠ verify; the landscape shifted —
Lemon Squeezy is being folded into Stripe's offering, Polar raised rates).
That's the price of not doing EU VAT yourself; at small scale it is worth it,
and you can migrate to option A when volume justifies a finance back-office.
Confirm the MoR **accepts your product category** (investment-tracking software
is generally fine; investment *advice* or brokerage services often aren't —
another reason for ADR 0001).

## 3. Decision and why

- **MoR**, chosen in Phase 0 (ADR 0010) after confirming: EU consumer
  VAT handling, invoices in the customer's language, withdrawal-right flow,
  payout to a Spanish autónomo/SL, webhooks quality, subscription management
  portal, and exit/export options.
- **Freemium** shaped by the real cost drivers:

| | Free | Plus (paid) |
|---|------|-------------|
| Brokerage Accounts | 1–2 | more/unlimited |
| Imports, dashboard, allocation, history | ✔ | ✔ |
| Quant Lab | limited runs/month | higher quota |
| Market Intel loads | limited | higher quota |
| AI assistant & import reviewer | ✘ | ✔ with monthly token quota (opt-in) |
| Export (JSON/CSV) | ✔ always (GDPR portability — never paywalled) | ✔ |

Rule: **never put a GDPR right behind a paywall** (export, deletion).

- **Price discovery:** don't guess; compute break-even from fixed costs
  (market-data licence dominates, see 06/08) and measured per-user marginal cost
  (AI tokens, quant CPU) from the beta:

```
break_even_users = fixed_monthly_costs / (price_net_of_MoR_fee - marginal_cost_per_paying_user)
```

## 4. In this repo

### 4.1 Entitlements: the single source of truth

```mermaid
flowchart LR
  MOR[Merchant of record] -- signed webhook --> WH[/api/billing/webhook/]
  WH --> V{verify signature<br/>+ idempotency key}
  V -- ok --> SUB[(subscriptions)]
  SUB --> ENT[entitlements(user)]
  ENT --> GATES[Feature gates:<br/>account limit · quota · AI]
```

```sql
CREATE TABLE subscriptions (
  user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  provider text NOT NULL, provider_customer_id text, provider_subscription_id text,
  plan text NOT NULL, status text NOT NULL,        -- active | trialing | past_due | canceled
  current_period_end timestamptz, updated_at timestamptz DEFAULT now()
);
CREATE TABLE billing_events (                       -- idempotency + audit; NOT Private financial data
  provider_event_id text PRIMARY KEY, received_at timestamptz DEFAULT now(), type text, processed_at timestamptz
);
```

```python
def entitlements(user) -> Entitlements:
    plan = user.plan if sub_is_active(user) else "free"
    return PLAN_TABLE[plan]            # limits live in code/config, not scattered in routes

@router.post("/billing/webhook")
async def webhook(request: Request, db = Depends(get_raw_db)):
    body = await request.body()
    event = provider.verify(body, request.headers)      # raises on bad signature
    if billing_events.seen(db, event.id): return {"ok": True}   # idempotent
    apply_event(db, event); billing_events.mark(db, event.id)
    return {"ok": True}
```

Gates read `entitlements(user)`; the UI shows what the plan allows and a clear
upgrade path.

### 4.2 What you never store

Card numbers, bank details, CVV — the MoR's hosted checkout handles them, so
your PCI scope is minimal (SAQ A-style: redirect/overlay to the provider's
page). Keep it that way: no custom card forms.

### 4.3 Lifecycle behaviours to define up front

| Event | Behaviour |
|-------|-----------|
| Trial | Optional 7–14 days; card required or not? (affects conversion vs abuse) |
| Upgrade | Immediate; prorated by the MoR |
| Downgrade/cancel | Keep paid features until period end, then limit gracefully: **never delete user data** because they downgraded; read-only over the limit |
| Payment failure (`past_due`) | Grace period (e.g. 7 days), email, then downgrade |
| Refund / withdrawal | Per MoR + consumer law; entitlement revoked on event |
| Account deletion | Cancel subscription at the MoR first; keep tax records the MoR/you must retain |
| Price change | Grandfather existing subscribers or give notice per consumer law |

## 5. What goes wrong in production

- **Unverified webhooks** — anyone can POST "subscription active". Verify
  signatures, check timestamps, make handlers idempotent and order-tolerant
  (events can arrive out of order or twice).
- **Entitlement drift** — plan stored in two places. One table, derived checks.
- **Gating by hiding the button only.** Enforce limits server-side.
- **Deleting data on downgrade** → angry users and possible GDPR/consumer problems.
- **Free-tier abuse** (many accounts for free AI) → AI is paid-only; verified
  email; Turnstile.
- **Chargebacks/fraud** — the MoR absorbs most of this; still monitor.
- **Tax accounting surprises** — payouts from a MoR are your revenue; talk to
  your gestor about how it is booked (autónomo/SL, IRPF/IVA/IS implications).
- **Refund policy mismatch** between your ToS and the MoR's checkout terms.

## 6. Checklist

- [ ] MoR selected (ADR 0010); category approval confirmed
- [ ] Plans table and entitlements function; server-side enforcement
- [ ] Webhook: signature, idempotency, out-of-order tolerance; replay test
- [ ] Withdrawal/immediate-start consent flow verified in MoR checkout
- [ ] Downgrade never deletes data; read-only over limits
- [ ] Exports/deletion free for everyone
- [ ] Break-even computed with real costs from the beta
- [ ] ToS refund/cancellation text matches checkout behaviour
