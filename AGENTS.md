# AGENTS.md

Guidance for AI coding agents (and humans) working in this repository.

## What this is

A local-first app for tracking personal investments across multiple brokers
(Interactive Brokers today; MyInvestor and anything else without a public
API via manual entry / CSV import) in one place: synced positions and full
transaction history, daily-refreshed market data, and a dashboard showing
portfolio value against amount invested over time.

This is a personal, single-user, local-only app. It is not designed for
multi-tenant hosting, and should not be deployed publicly without a serious
security review (see "Security rules" below). There is no LLM/agent layer in
the app yet — an earlier prototype of one was removed when the backend was
rebuilt hexagonally; if it comes back, it belongs as an inbound adapter
alongside `api/`, not woven into the domain. The **security resolver**
(deciding which market-data listing prices a broker holding — see
`backend/AGENTS.md`'s "Security resolver" section and
`plans/agentic_asset_mapping.md`) is a step toward that: today it's entirely
deterministic (OpenFIGI + rule-based scoring, no LLM involved), with an
agent as one possible escalation path for ambiguous cases in a later phase.

## Architecture

```
backend/    Python (FastAPI), hexagonal (ports & adapters) — see backend/AGENTS.md
frontend/   React + TypeScript (Vite), token-driven theming — see frontend/AGENTS.md
database/   Postgres schema rationale + pgAdmin — see database/AGENTS.md
docker-compose.yml   Runs db + pgadmin + backend + frontend together
```

The app runs as four containers via Docker Compose: `db` (Postgres),
`pgadmin` (pgAdmin 4, for inspecting the DB), `backend` (FastAPI), `frontend`
(built React app served by nginx). Postgres and pgAdmin data live in named
Docker volumes (`pgdata`, `pgadmin_data`), not bind mounts — never part of
the repo, never visible as files on disk. Schema is versioned with Alembic
and applied automatically on backend startup (see `database/AGENTS.md`).

**Each subdirectory's `AGENTS.md` is the source of truth for that layer** —
this file only covers what's shared across all three.

## Broker connectivity, in one paragraph

Interactive Brokers is the reference integration: `backend/app/adapters/brokers/ibkr.py`
(via `ib_async`) needs TWS or IB Gateway running on your machine with API
access enabled, and is authoritative for current positions/NAV but NOT full
history (IBKR's live API only sees trades since midnight); full history
comes from the Flex Web Service (`ibkr_flex.py`), a one-time setup in IBKR's
Account Management. Any broker without a usable API — MyInvestor today —
goes through manual entry or a statement import (CSV or XLSX) instead; both
land in the exact same database tables as an API sync, just tagged
`source='manual'`. MyInvestor's own PSD2/Open Banking API exists but is a
dead end for this app: it needs a licensed TPP + eIDAS certificate, and even
then only covers payment accounts, never fund/ETF positions — statement
export is the only route MyInvestor actually offers for portfolio data. See
`backend/AGENTS.md` for the full detail and the reasoning behind each choice.

## Security rules (non-negotiable)

- **Never commit credentials, API keys, tokens, or account numbers.** All of
  that lives in `backend/.env` (gitignored) — see `backend/.env.example` for
  the shape. This includes the IBKR Flex Web Service token, which grants
  read access to your full account statements — treat it like a password.
- **Never commit any exported financial data, database dumps, or anything
  from the Postgres/pgAdmin Docker volumes.** Keep it that way.
- Broker credentials are read from environment variables only, never
  hardcoded, never logged (including in error messages/stack traces).
- This app is local-only by design. Don't add remote hosting, multi-user
  auth, or expose the API beyond localhost without discussing it first —
  that changes the entire threat model for credential storage.
- When adding a new dependency for a broker or data source, check whether it
  phones home / sends data anywhere unexpected before adding it. The
  security resolver's OpenFIGI adapter (`backend/AGENTS.md`) sends only an
  ISIN — never account/holding/position data — to look up exchange listings.

## Running locally

Primary path — Docker Compose (db, pgAdmin, backend, frontend together):
```
cp backend/.env.example backend/.env   # fill in IBKR_FLEX_TOKEN etc. once you have them
docker compose up --build
```
- Frontend: http://localhost
- Backend API: http://localhost:8000
- pgAdmin: http://localhost:5050 (default `admin@investment-tracker.app` / `admin`)
- Postgres: localhost:5432 (user/db `investment_tracker` by default — override
  via root-level `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` env vars)

`DATABASE_URL` and `IBKR_HOST` are overridden automatically inside
`docker-compose.yml` for the backend container (it talks to the `db` service
and to `host.docker.internal` for TWS/IB Gateway running on your host).

Manual path (no Docker) — see `backend/AGENTS.md` and `frontend/AGENTS.md`
for the two halves; you'll still want `docker compose up -d db pgadmin` for
Postgres unless you run your own.

Interactive Brokers: launch TWS or IB Gateway yourself, log in, and enable
API access (Configure > API > Settings > Enable ActiveX and Socket Clients).
Paper trading port is 7497, live is 7496.

## Status / roadmap

- [x] Database: versioned Alembic schema, pgAdmin, `pg_trgm` search index
- [x] Backend: hexagonal restructure (domain/ports/application/adapters/api)
- [x] IBKR adapter (live positions/NAV via `ib_async`) + Flex Web Service
      adapter (full history) — both need a real IBKR paper account to
      exercise end-to-end; verified so far against manual entry + yfinance only
- [x] Manual entry + CSV/XLSX import (generic format verified; MyInvestor
      import verified against one real export shape — a buy-order/
      "Aportaciones" export with no operation-type column — extend
      `backend/app/adapters/brokers/statement_files/myinvestor.py`'s alias
      lists if a sell/dividend export turns out to use different headers).
      Re-importing an overlapping export is idempotent — see
      `backend/AGENTS.md`'s "Import idempotency" section.
- [x] Market data (yfinance) + FX + daily snapshot history, verified
      end-to-end with real quotes/price history
- [x] Frontend: Tailwind v4 token theming (dark/bright), sidebar+header
      layout, Dashboard/Search/Accounts/Asset-detail pages, all verified
      in-browser against live data
- [ ] IBKR sync tested against a real paper account (adapter is written and
      unit-tested for error handling, but not yet run against a live TWS session)
- [ ] MyInvestor import verified against a sell/dividend export (only the
      buy-order/"Aportaciones" export shape has been checked against real data)
- [x] Security resolver, Phases 1-4 of `plans/agentic_asset_mapping.md`:
      deterministic asset-mapping (OpenFIGI + rule-based scoring), scheduled
      job, `POST /api/assets/{id}/resolve`, `/api/resolutions/*` — no LLM
      involved yet. Verified end-to-end against real data (see the plan's
      own notes on what that surfaced). Frontend review panel (`AccountsPage`'s
      "Security resolver" card) built and verified in-browser against live data.
- [x] Security resolver, Phase 5: `security` MCP server (`backend/ai/mcp_servers/security`)
      exposing the resolver's 9 read/write tools over stdio. Verified with a
      real stdio subprocess client (tool listing + real yfinance/DB round-trips).
- [x] Security resolver, Phase 6: `security_resolver` agent
      (`backend/ai/agents/security_resolver`) — tool-calling loop,
      deterministic flag-for-review fallback, `agent_runs` audit trail,
      `POST /api/resolutions/{id}/agent`. Model backend is pluggable
      (`ai/common/llm.py`: `AGENT_PROVIDER=ollama|openai` — Ollama locally,
      or any OpenAI-compatible hosted API like Groq — swap models/providers
      via `.env`, never code). Verified end-to-end against real Postgres, a
      real MCP subprocess, and real Ollama: first with `mistral:latest`
      (not tool-tuned — scored 0/6, proving the harness/fallback work
      correctly), which surfaced and led to fixing a real bug in
      `OllamaChatClient` (its SDK pydantic-validates outbound tool_calls
      against its own nested `{"function": {...}}` shape — this project's
      internal flat shape needed one more translation step, now covered by
      regression tests in `tests/test_llm_clients.py`). Then with
      `qwen3:8b` (pulled and evaluated for real): **5/6 final_accuracy, 0
      safety_violations, 100% terminated_ok** on the offline harness, plus
      one full live run (real ambiguous ISIN, 8 real candidates) correctly
      resolved in a single tool call. `AGENT_MODEL` defaults to `qwen3:8b`;
      `AGENT_ENABLED` stays `false` by default regardless.
- [ ] Security resolver, Phases 7-8: ML ranker, additional MCP servers/agents
- [ ] AI/agent layer beyond the (non-LLM) security resolver above
      (deliberately out of scope for this pass)
