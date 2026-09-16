# database/AGENTS.md

Guidance for AI coding agents (and humans) working on the database. See the
root `AGENTS.md` for project-wide context; `../backend/AGENTS.md` for how
the app talks to this schema.

## What lives where

```
database/
  init/         SQL run ONCE by Postgres's docker-entrypoint-initdb.d, only on a
               brand-new (empty) data volume. Extensions and anything else that
               needs superuser privileges the app's own DB role may not have.
  pgadmin/servers.json   Pre-registers the "Investment Tracker" connection in pgAdmin
backend/migrations/     Alembic — ALL table/column schema, versioned, re-runnable
```

**The boundary**: if it's a `CREATE TABLE`/`ALTER TABLE`, it's an Alembic
migration in `backend/migrations/versions/`, generated from
`backend/app/adapters/persistence/orm.py`. If it's something that must exist
*before* the app's migrations can even run (an extension), it's an `init/`
script. Don't add table DDL to `init/` — a fresh volume plus an already-
running one with `alembic upgrade head` must always converge to the same
schema, and only Alembic guarantees that.

## Why the schema looks like this

Money is `numeric(20,4)`, prices `numeric(20,8)`, quantities
`numeric(28,10)`, FX rates `numeric(20,10)` — all `Decimal` on the Python
side (see `backend/AGENTS.md`'s money rule). All timestamps are
`timestamptz`. No tax lots, no separate `brokers` lookup table (`broker_key`
is just a string column) — both are complexity this app doesn't need yet.

| Table | Why it exists |
|---|---|
| `assets` | One row per real-world instrument, regardless of which broker or data source knows about it. `share_class_figi` (OpenFIGI's cross-exchange security identifier) is learned once the security resolver succeeds — see `asset_resolutions` below. |
| `asset_identifiers` | **Solves the identity problem**: an IBKR contract (`conid`), an ISIN, and a Yahoo Finance ticker are three different names for the same asset. `(scheme, value) -> asset_id`, schemes `IBKR_CONID` / `ISIN` / `YFINANCE` / `USER`. Market data refresh ONLY ever resolves a symbol via the `YFINANCE` row — never guesses from `assets.symbol` directly — so a wrong guess never silently misprices a position. Assets without a confirmed `YFINANCE` identifier are flagged `assets.needs_mapping` and surfaced in the Accounts page for a human to resolve. |
| `accounts` | `source = 'api' \| 'manual'` is the ONLY thing that distinguishes an Interactive Brokers account from a hand-entered MyInvestor one — everything downstream (holdings, transactions, valuation) is identical either way. Don't add a broker-specific table; add a `source` value instead. |
| `holdings` | Current position snapshot per account+asset — overwritten wholesale on every API sync (`replace_holdings_for_account`), upserted individually for manual entry. |
| `transactions` | The append-only ledger everything else derives from. `UNIQUE(account_id, external_id)` (partial, `WHERE external_id IS NOT NULL`) is what makes every broker sync, Flex import, and CSV/XLSX import **idempotent** — run any of them twice, get zero duplicates. Manual entries have `external_id = NULL` and are addressed by `id` instead. For a statement import with no native broker reference number (MyInvestor's exports), `external_id` instead holds a deterministic content fingerprint (`fp1:<hash>#<n>` — see `backend/AGENTS.md`'s "Import idempotency" section); no schema change was needed to support it. Convention: for the cash-flow-only types (`DIVIDEND`, `INTEREST`, `FEE`, `DEPOSIT`, `WITHDRAWAL`), `quantity` is `1` and `price` holds the cash amount — this lets snapshot-building treat `quantity * price` as "the transaction's cash value" uniformly across every type, `BUY`/`SELL` included. |
| `cash_balances` | Latest known cash per account+currency. Not historized — history of cash comes from replaying `transactions`, not from this table (see `build_snapshots.py`). |
| `prices` / `quotes` | Daily bars and latest tick, per asset. **Never populated inside a request** — only `RefreshMarketDataUseCase` writes here; the read API only ever selects from these tables. This is what makes an unreliable upstream (yfinance) tolerable: a slow/broken refresh degrades to stale data, never a hung page load. |
| `fx_rates` | Daily historical closes, `(date, base, quote)`. Deliberately never "today's rate" applied retroactively — see `backend/AGENTS.md`'s FX note. |
| `position_snapshots` / `portfolio_snapshots` | The dashboard's history. Fully derived, fully rebuildable from `transactions` + `prices` + `fx_rates` at any time via `POST /api/snapshots/rebuild` — never hand-edit these tables, just rebuild. |
| `asset_resolutions` | One row per attempt to decide which market-data listing prices a `needs_mapping` asset (see `backend/AGENTS.md`'s security resolver section and `plans/agentic_asset_mapping.md`). `uq_asset_resolutions_open` (a partial unique index, hand-written in migration `0002` — autogenerate can't express it) keeps at most one non-`SUPERSEDED` resolution per asset; a retry supersedes the old one rather than updating it in place, so the history of attempts survives. |
| `resolution_candidates` | Every listing considered for a resolution, with the exact feature values (`features` jsonb) and `score` used to rank it at decision time — this is also the dataset a future ML ranker (Phase 7 of the plan) would train on, so nothing here gets recomputed or overwritten after the fact. |
| `agent_runs` | One row per local-LLM agent invocation for a resolution (Phase 6 of `plans/agentic_asset_mapping.md`), or for a note-writing/chat agent's own run (Phase 8d/8f of `plans/agentic_asset_mapping_phase7_8.md` — `import_reviewer`, `portfolio_assistant`). `tool_calls` jsonb is the full transcript, for audit and for the offline evaluation harness. `resolution_id` is nullable (`ON DELETE SET NULL`), which is exactly what let `import_reviewer`/`portfolio_assistant` reuse this table for their own runs with no schema change, since neither's work is tied to any one resolution. |
| `ai_notes` | A short Markdown note an agent wrote (Phase 8c of `plans/agentic_asset_mapping_phase7_8.md`) — `scope = 'account' \| 'portfolio'`, `account_id` set only for the former. Append-only: `dismissed_at` is the only field an existing row ever gets updated. Written exclusively through `NoteRepo.add()` (`ports/notes.py`) — the `notes` MCP server's `save_note` tool is the only agent-facing write path, alongside `security`'s (see `backend/ai/AGENTS.md` rule 6). |
| `chat_sessions` / `chat_messages` | `portfolio_assistant`'s conversation threads and their messages (Phase 8f of `plans/agentic_asset_mapping_phase7_8.md`) — the frontend's chat history sidebar lists `chat_sessions` (sorted by `updated_at`, not `created_at`, so a session with a new reply floats back to the top); `chat_messages.tool_calls` jsonb records which tool(s) an assistant reply used, shown in the UI but **never replayed back into the model** as session memory (`ai.common.agent_loop.run_agent`'s `history` param only replays each past turn's final text — a stale tool *result* could describe out-of-date prices). Written exclusively through `ChatRepo` (`ports/chat.py`) by the agent itself, not through an MCP tool — session memory/history is plumbing the agent class owns around the loop, invisible to the model. |
| `quant_runs` | One row per Quant Lab Monte Carlo run (`plans/quant_lab.md`) — the run's RECIPE (`asset_id`, `model_key`, `split_date`, `horizon_days`, `n_paths`, `seed`, `params`) plus a percentile summary (`calibration_params`, `calibration_diagnostics`, `percentiles`, `backtest`). Deliberately has **no `paths` column** — `RunQuantSimulationUseCase.replay()` recomputes the full path ensemble on demand from the recipe, exact because calibration only ever reads bars already persisted for a fixed date range and the RNG is seeded. Written exclusively through `QuantRunRepo` (`ports/repositories.py`), never directly. |

`assets.symbol` and `assets.name` have a `pg_trgm` GIN index (from
`database/init/01-extensions.sql` + the initial migration) backing the
`ILIKE`-based local search in `AssetRepo.search_local` — that's why the
extension has to exist before the migration runs.

## Migration workflow

```
cd backend
# after editing app/adapters/persistence/orm.py:
alembic revision --autogenerate -m "add foo column"
# ALWAYS review the generated file by hand — autogenerate won't catch things
# like the pg_trgm GIN indexes, CHECK constraints expressed as plain strings, etc.
alembic upgrade head   # or just restart the app — main.py runs this on startup
```
Never edit an already-applied migration in place; add a new one. Never
call `Base.metadata.create_all()` anywhere — that's how you get schema drift
between environments.

## pgAdmin

`docker compose up` starts a `pgadmin` service at `http://localhost:5050`
(default login `admin@investment-tracker.app` / `admin`, override via
`PGADMIN_DEFAULT_EMAIL` / `PGADMIN_DEFAULT_PASSWORD`). The "Investment
Tracker" server connection is pre-registered via `pgadmin/servers.json`
(host `db`, database `investment_tracker`) — you still enter the DB
password once per pgAdmin session; it's not stored in a plaintext pgpass
file on purpose.

## Non-negotiable

- **Never commit exported financial data, `pg_dump` output, or anything from
  the `pgdata`/`pgadmin_data` Docker volumes.** They're named volumes, not
  bind mounts, precisely so nothing here ever shows up as files in the repo.
- **Never commit real credentials** in `docker-compose.yml` defaults — the
  `POSTGRES_PASSWORD`/`PGADMIN_DEFAULT_PASSWORD` fallbacks in there are
  throwaway local-dev values; override them via a root `.env` for anything
  that isn't a disposable local instance.
