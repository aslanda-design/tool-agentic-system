# backend/AGENTS.md

Guidance for AI coding agents (and humans) working in the backend. See the
root `AGENTS.md` for project-wide context; `../database/AGENTS.md` for the
schema; `../frontend/AGENTS.md` for the UI.

## Architecture: hexagonal (ports & adapters)

```
app/
  main.py            FastAPI factory: runs migrations, gap-fills snapshots, starts the scheduler
  config.py          Settings (pydantic-settings, from backend/.env)
  container.py       COMPOSITION ROOT — the only module that wires adapters into use cases

  domain/            Pure Python. NO FastAPI, NO SQLAlchemy, NO I/O of any kind.
    money.py           Money value object — Decimal + currency, never float
    models.py          Asset, Account, Holding, Transaction, Quote, Bar, ...
    returns.py         Pure functions: avg-cost basis, P&L, price return, net-invested
    errors.py          DomainError and subclasses
    listings.py         Security-resolver types: ResolutionContext, Candidate, ... (see below)
    exchanges.py         Bloomberg/Yahoo/broker exchange-code <-> MIC lookup tables
    listing_scoring.py   Pure rule-based scoring/decision for the resolver — SCORER_VERSION
    analytics.py          Pure portfolio-analytics math (concentration, drawdown,
                           mispriced-trade detection) — see "Portfolio intelligence" below

  ports/              Interfaces the domain/application layer depends on
    broker.py            BrokerPort — live account state from a broker's API
    statements.py         StatementPort — FULL historical transactions
    market_data.py         MarketDataPort + FxRatePort — quotes/bars/FX
    security_master.py     SecurityMasterPort — ISIN -> exchange listings (OpenFIGI)
    repositories.py         AssetRepo, PortfolioRepo, MarketDataRepo, ResolutionRepo — persistence
    notes.py                 NoteRepo — agent-written notes (see "Portfolio intelligence" below)
    chat.py                   ChatRepo — portfolio_assistant's sessions/messages (see below)

  application/        Use cases: plain classes, ports injected via constructor
    sync_broker.py, import_transactions.py, manual_entry.py,
    refresh_market_data.py, build_snapshots.py,
    query_portfolio.py, query_asset.py, search_assets.py,
    query_market_data.py, query_analytics.py   read-only, agent/Dashboard-facing
                                                 (see "Portfolio intelligence" below)
    asset_resolution.py   shared identity-resolution logic (see below)
    resolve_security.py   the security resolver (see "Security resolver" below)
    import_fingerprint.py   deterministic dedup id for statement imports with no
                             native broker reference — see "Import idempotency" below
    dto.py                 plain dataclasses returned to the API layer

  adapters/           Implementations of the ports above
    brokers/ibkr.py           ib_async — live positions/NAV/cash (TWS/Gateway)
    brokers/ibkr_flex.py      Flex Web Service — full trade/cash history
    brokers/statement_files/  CSV/XLSX → transactions (any broker without an API)
      sniff.py                  detect real format from bytes (never filename) — CSV/XLSX,
                                 or an actionable error for legacy .xls/HTML-as-.xls/PDF
      tabular.py                broker-agnostic CSV/XLSX reading: encoding/delimiter
                                 sniffing, preamble/header-row detection, trailing-row trim
      numbers.py                Spanish-locale decimal/date parsing — returns None on
                                 anything unparseable, never a silent "0"
      headers.py                accent-insensitive column-alias matching
      generic.py                our own broker-agnostic import format
      myinvestor.py             MyInvestor's statement exports (see below)
    security_master/openfigi_adapter.py   OpenFIGI /v3/mapping — ISIN -> exchange listings
    persistence/orm.py        SQLAlchemy mapped tables (the ONLY module that knows the physical schema)
    persistence/repositories.py  SqlAssetRepo / SqlPortfolioRepo / SqlMarketDataRepo / SqlResolutionRepo / SqlNoteRepo / SqlChatRepo
    market_data/yfinance_adapter.py, fx_adapter.py
    scheduler.py               APScheduler jobs calling use cases (incl. run_resolver_job)

  api/                Inbound adapter: FastAPI
    routes/*.py          One router per resource; thin — build a use case via
                         `container.py`, call it, `db.commit()`, return the DTO
    routes/resolutions.py  everything about an existing security resolution
                            (POST /api/assets/{id}/resolve itself lives in assets.py)
    routes/notes.py, chat.py   agent-written notes; portfolio_assistant's
                                chat sessions (see "Portfolio intelligence" below)
    schemas.py            Pydantic REQUEST models only (see below)

ai/                  LLM/agent layer, sibling to app/, never mixed into it —
                     see backend/ai/AGENTS.md for its own rule set.
  mcp_servers/security/  MCP server exposing the resolver's use cases as 9
                         tools over stdio (Phase 5 of
                         plans/agentic_asset_mapping.md); tools/ has one
                         thin wrapper function per file, no logic.
  agents/security_resolver/  the local-LLM agent (Phase 6) that uses those
                             tools to finish a NEEDS_AGENT resolution;
                             evaluation/ is its offline eval harness.
  agents/import_reviewer/    reviews a fresh statement import, writes a
                             note via the `notes` server (Phase 8d)
  agents/portfolio_assistant/  conversational chat agent with session
                               memory/history (Phase 8f — see "Portfolio
                               intelligence" below)
  mcp_servers/portfolio/, market_data/, analytics/, notes/  read (+ notes:
                             write) MCP servers wrapping the query/analytics
                             use cases and NoteRepo (Phase 8a-c)
  common/                shared building blocks every agent uses:
    jsonable.py             dataclass/Decimal/date/set -> JSON-safe dict
    llm.py                  pluggable model backend: OllamaChatClient,
                             OpenAiCompatibleChatClient (Groq/OpenAI/...),
                             build_chat_client() picks one from settings
    mcp_client.py            stdio MCP client: open_mcp_server (one server)
                             and open_mcp_servers (several at once, Phase 8)
    agent_loop.py            the one tool-calling loop every agent uses —
                             terminal-tool mode or conversational
                             (terminal_tools=None) + optional session
                             memory (`history`), Phase 8f
```

**Dependency direction is one-way**: `domain` imports nothing from this app.
`application` imports `domain` and `ports` only — never `adapters`, never
FastAPI/SQLAlchemy. `adapters` implement `ports` and may import `domain`.
`container.py` is the only place that imports both a port and its concrete
adapter to wire them together. `api/routes` call `container.build_*()` and
nothing else — no adapter imports, no business logic in a route body.
`ai/` may import from `app/` (via `app.container`'s `build_*`/repo
factories) freely. The only import the other way is `container.py` itself
wiring in `SecurityResolverAgent` (so routes can call
`build_security_resolver_agent()`) — no other module under `app/` may
import `ai/` directly. See `backend/ai/AGENTS.md` for the full rule set
that package follows.

**Responses skip a schema layer.** `api/schemas.py` holds Pydantic *request*
models only. Responses are the application layer's plain dataclasses
(`application/dto.py`) returned directly — FastAPI's `jsonable_encoder`
already serializes dataclasses and `Decimal` correctly, so a parallel set of
response schemas would just duplicate the DTOs for no benefit. Don't add one.

## The money rule (non-negotiable)

**All monetary amounts, quantities, prices and FX rates are `Decimal`, never
`float`.** This is a finance app; float rounding error compounds silently
across history rebuilds. `domain/money.py::Money` wraps `Decimal` + currency.
Postgres columns are `Numeric` (see `database/AGENTS.md` for precision).
When a value crosses a boundary from an external library (`ib_async`,
`yfinance` — both give you floats), convert immediately with
`Decimal(str(value))` — never `Decimal(value)`, which inherits float's
binary imprecision.

## Adding a broker with a live API

1. Add a dataclass-returning adapter in `adapters/brokers/<broker>.py`
   implementing `BrokerPort.fetch_snapshot(since) -> BrokerSnapshot`. One
   call, one round trip — no `connect()`/lifecycle methods on the port;
   connection handling is the adapter's own business.
2. Register it in `container.BROKER_ADAPTERS`.
3. Add credential fields to `.env.example` (never real values) and `config.py`.
4. Route ingestion through `application/asset_resolution.py::resolve_asset`
   so identity resolution (IBKR conid > ISIN > symbol) stays consistent
   across every ingestion path. Don't hand-roll asset lookup in a new adapter.

**Never add a fake adapter for a broker with no API** (e.g. don't write a
`ManualBrokerAdapter` that reads our own DB — that's fake dependency
inversion, since a port models pulling from an *external* system). Instead:
manual entries and CSV imports write to the exact same `holdings` /
`transactions` tables with `source='manual'` — see `application/manual_entry.py`
and `adapters/brokers/csv_import.py`. Every read use case is blind to origin.

## Import idempotency (CSV/XLSX statement imports)

`ports/repositories.py::add_transactions` dedups on the unique
`(account_id, external_id)` constraint, but only inserts unconditionally
when `external_id` is `NULL` (reserved for manual entry — see
`database/AGENTS.md`). Every row that comes through `CsvImportUseCase` must
therefore have a **non-null** `external_id` before it reaches
`add_transactions`, or a re-imported overlapping export will duplicate the
overlap. Two sources for that id, both namespaced so they can never collide:

1. **A native operation-reference column**, if the export has one — the
   adapter (`myinvestor.py`/`generic.py`) sets `myinvestor:<raw>` /
   `generic:<raw>` directly.
2. **No native reference** (MyInvestor's export usually has none) —
   `application/import_fingerprint.py::assign_external_ids` computes
   `fp1:<sha256-of-content>#<n>` from the row's own trade date, instrument
   (ISIN > symbol > name), canonical type, and quantity/price quantized to
   the DB's own `Numeric` precision — so formatting differences between two
   exports of the same operation (`12,5` vs `12.500000`, `05/02/2025` vs
   `2025-02-05`) still hash identically. The `#n` suffix disambiguates
   genuinely duplicate operations (same fund/day/units/price) by their
   position within that content group, counted per-parse — not by file row
   position — so re-imports stay stable even if a later export reorders
   rows or a settlement adds a same-group row for an earlier date.

**`fp1` is a frozen recipe.** Changing which fields feed the hash, or how
they're normalized, requires bumping to `fp2` — mixing the two means a
previously-imported row and its freshly-computed id no longer match, so the
overlap re-inserts once. Treat that as a rare, deliberate migration (and
tell the user their next import of overlapping history will duplicate),
never a casual refactor of `import_fingerprint.py`.

Deliberately **excluded** from the fingerprint: `fees` (the field most
likely to be presented differently between two exports of the same
operation — including it would make a cosmetic fee change duplicate the
whole row) and anything about the file itself (name, row position, date
range). The accepted consequence: imports are insert-only — a corrected fee
in a later export won't update an already-imported row; delete it via
`DELETE /api/manual/transactions/{id}` and re-import if that matters.

`CsvImportUseCase.preview` and `.commit` both start from the same
`_prepare()` step that calls `assign_external_ids` — they must never
diverge on which rows count as "new," or the number the user previews stops
matching what actually gets written.

## Adding a market-data source

Implement `MarketDataPort` and/or `FxRatePort` in `adapters/market_data/`.
**Never call a market-data port from a request handler or a query use case.**
`application/refresh_market_data.py` is the only caller; it persists results
to `MarketDataRepo` (`prices`, `quotes`, `fx_rates`), and everything else
reads the DB. The one deliberate exception is `search_assets.py`, which hits
the market-data port directly on an explicit, infrequent user action and
immediately persists what it finds — see the docstring there before adding
another exception.

## Adding a use case

Put it in `application/`, inject ports via `__init__`, return a `dto.py`
dataclass. Wire it in `container.py` as a `build_<name>_use_case(db, ...)`
factory. Add a thin route in `api/routes/` that calls the factory, invokes
the use case, and `db.commit()`s on success (routes own the transaction
boundary — use cases and repos never commit, only `flush()`).

## Security resolver (deciding an asset's market-data listing)

`application/resolve_security.py` decides which Yahoo Finance listing prices
an asset flagged `needs_mapping` — see `plans/agentic_asset_mapping.md` for
the full design. Pipeline: `_build_context` (asset + its transactions ->
`ResolutionContext`) -> `generate_candidates` (OpenFIGI + Yahoo search, real
network calls) -> `domain/listing_scoring.py::score_candidates` + `decide`
(pure, no I/O) -> either `AssetRepo.apply_listing` immediately (rules
confident enough) or persist an open `asset_resolutions` row for
`accept()`/`add_candidate()`/`flag_for_review()` to finish later (a human,
or — a later phase — a local-LLM agent). `AssetRepo.apply_listing` is the
**only** place a `YFINANCE` identifier is written, whether the caller is
rules, a human via `POST /api/resolutions/{id}/accept`, or (later) an agent
— never call `add_identifier` directly for this. It also renames the asset
(`domain/listings.py::resolved_display_name`, applied by every
`apply_listing` call site) to Yahoo's own name for the chosen listing
whenever that's more readable than what's there — no ISIN or broker code is
left behind once an asset resolves, and no LLM involved: the name already
comes back from `MarketDataPort.get_listing_info`.

This runs from three places: the scheduled job
(`adapters/scheduler.py::run_resolver_job`, every `RESOLVER_INTERVAL_MINUTES`),
an explicit `POST /api/assets/{id}/resolve` (one asset, user-initiated), or
the existing manual `POST /api/assets/{id}/map` path (still works — it now
goes through `apply_listing` too, and records the pick as a
`RESOLVED_BY_USER` candidate on any open resolution for that asset, which is
exactly the label a future ML ranker needs — see the plan's Phase 7).

**NEVER wire `run_resolver_job` (or anything that calls
`generate_candidates`) into a FastAPI `BackgroundTasks` callback on a
request route, and never call it from `main.py`'s startup `lifespan`.**
Both were tried and reverted — see git history / the plan's Phase 4 notes.
The problem: `TestClient` (used by every route test) runs `BackgroundTasks`
**synchronously inside the request**, and runs the `lifespan` on every
`with TestClient(app) as client:` block. Either wiring means "run pytest"
= "make real OpenFIGI/Yahoo calls and permanently mutate whatever
`DATABASE_URL` points at" — this actually happened once during development
and silently remapped a real asset. The resolver only ever runs from the
scheduled job or an explicit single-asset action, both of which a plain
`pytest` run never triggers.

**Agent access** (Phase 5, implemented): `ai/mcp_servers/security/` exposes
this same use case as 9 MCP tools over stdio — `python -m
ai.mcp_servers.security`. The tools call `accept()`/`flag_for_review()`
directly, not through the scheduler, so the `BackgroundTasks`/`lifespan`
warning above doesn't apply to them; see `backend/ai/AGENTS.md` for the
rules that do.

**The agent itself** (Phase 6, implemented): `ai/agents/security_resolver/`
is a tool-calling agent that uses those MCP tools to finish a
`NEEDS_AGENT` resolution — see `ai/agents/security_resolver/README.md`.
The model backend is pluggable (`AGENT_PROVIDER=ollama|openai` — Ollama
locally, or any OpenAI-compatible hosted API like Groq; see
`ai/common/llm.py`), so swapping models is a `.env` change, never code.
Runs only via `POST /api/resolutions/{id}/agent`, gated on
`AGENT_ENABLED=true` (default `false`); never scheduled, never
`BackgroundTasks`. It always leaves the resolution in a settled state
(`RESOLVED_BY_AGENT` or `NEEDS_REVIEW`), never stuck.

## Portfolio intelligence (MCP servers + analytics + notes + chat)

Phase 8a-d and 8f of `plans/agentic_asset_mapping_phase7_8.md`,
implemented (8e, a `weekly_report` agent, was descoped by the user —
`notes`/`import_reviewer` already cover the "something worth flagging"
case for a single-user portfolio): four MCP servers under
`ai/mcp_servers/` — `portfolio` (6 tools: summary, positions, allocation,
value history, transactions, accounts), `market_data` (4 tools: asset
lookup, price history, FX rate, data freshness), `analytics` (5 tools:
returns, concentration, currency exposure, drawdown, mispriced-trade
detection), and `notes` (2 tools — see below) — plus two agents that use
them: `import_reviewer` (reviews a fresh import, writes a note) and
`portfolio_assistant` (a conversational chat agent — see its own
paragraph below).

`portfolio` and most of `market_data` are thin wrappers over the existing
`QueryPortfolioUseCase`/`QueryAssetUseCase` — no new business logic.
`market_data` reads `MarketDataRepo` (Postgres) exclusively, **never**
`MarketDataPort` (live yfinance) — an agent's tool call must stay as cheap
and rate-limit-safe as any other read; `application/query_market_data.py`
is the new use case this required (`GetAssetChartUseCase`, used by the
chart page, is the one place that's allowed to call live yfinance from a
request, and that reasoning doesn't extend here).

`analytics` has real new math, all pure functions in `domain/analytics.py`:
`weighted_return` (value-weighted average across positions),
`herfindahl_index` (concentration), `max_drawdown` (peak-to-trough over a
value history), `flag_mispriced_trades` (BUY/SELL price vs. that day's
persisted close, >10% default threshold — the most practically useful tool
here: verified live against the real dev DB, where it correctly caught two
real IBKR trades priced well off market). `QueryAnalyticsUseCase`
composes these with `QueryPortfolioUseCase`; also exposed at
`GET /api/analytics/*` (`api/routes/analytics.py`) so a human on the
Dashboard sees the exact same numbers an agent would.

`notes` (`ai_notes` table, migration `0003`; `ports/notes.py::NoteRepo`,
`SqlNoteRepo`) is the write path `import_reviewer` uses to record findings
— `save_note` (write) and `list_notes` (read). It's the second legitimate
MCP write path besides `security`'s (see `backend/ai/AGENTS.md` rule 6): a
note is append-only with no state machine, so `NoteRepo.add()` itself is
the whole guard, no use case needed. Also exposed at `GET /api/notes` /
`POST /api/notes/{id}/dismiss` for the Accounts-page/Dashboard UI.

**`import_reviewer`** (`ai/agents/import_reviewer/`): runs after a
statement import commits (`api/routes/imports.py::commit_import`, gated
`AGENT_ENABLED`), opens `analytics`/`market_data`/`security` purely to
pre-fetch context (`check_import_prices`, `get_data_freshness`,
`list_pending_resolutions` — none of these are exposed to the model as
callable tools; see `open_mcp_servers`' `tool_names=set()` pattern in
`ai/common/mcp_client.py`), and writes exactly one note via `notes`'
`save_note` — the model's only real tool. Single-turn in practice, same
"don't make a small model fetch its own context" reasoning as
`security_resolver`. Uses `arun()` (async), not `run()` (sync) —
`commit_import` is itself `async def`, so it awaits the agent directly
rather than going through a sync wrapper that would try to nest
`asyncio.run()` inside an already-running event loop (see
`backend/ai/AGENTS.md` rule 11 — a real bug this fixes, not a
hypothetical one).

**`portfolio_assistant`** (`ai/agents/portfolio_assistant/`): a
conversational chat agent — `ai/common/agent_loop.py::run_agent`'s
`terminal_tools=None` mode (ends on the model's first plain-text reply,
no forced "finish" tool) plus a `history` param that replays prior turns
from `chat_sessions`/`chat_messages` (migration `0004`;
`ports/chat.py::ChatRepo`, `SqlChatRepo`) as session memory — only each
past turn's final text, never the tool calls/results that produced it, so
the model never answers from a stale price it happened to memorize; it
re-calls a tool instead. Reachable at `POST
/api/chat/sessions/{id}/messages` (`api/routes/chat.py`; session CRUD —
`GET/POST /api/chat/sessions`, `GET/DELETE /api/chat/sessions/{id}` — are
thin routes straight over `ChatRepo`, same "plain repo write is its own
guard" reasoning as `notes`). Tool allowlist: `get_portfolio_summary`,
`list_positions`, `get_allocation`, `get_value_history` (`portfolio`);
`get_asset` (`market_data`); `get_returns`, `get_concentration`,
`get_currency_exposure`, `get_drawdown` (`analytics`) — spread across
three servers via `open_mcp_servers`, which is why this agent (like
`import_reviewer`) needs the multi-server form, not the single-server
`open_mcp_server` `security_resolver` uses. Fully read-only against
portfolio data, but does persist the conversation itself (`ChatRepo`,
called directly by the agent — not through an MCP tool, since session
memory is plumbing the agent owns, invisible to the model) and an
`agent_runs` audit row per turn. Frontend:
`frontend/src/features/assistant/AssistantPage.tsx` — a two-pane chat
page (session history sidebar + conversation), not a stub.

A real bug in `ai/common/mcp_client.py` was found and fixed verifying
these live — a tool annotated to return a bare `dict` could come back as
`None` from `McpToolSession.call_tool` even with real data returned (an
SDK quirk, not a model problem — see `backend/ai/AGENTS.md`'s "MCP SDK
gotcha" section for the full account, since it affects any future tool
with that annotation shape too, not just these two agents).

## Known limitations (don't rediscover these)

- **IBKR's `reqExecutions` / `ib.fills()` only return trades since midnight**,
  and IB Gateway cannot widen that window (only TWS's Trade Log setting can,
  up to 7 days). `adapters/brokers/ibkr.py` is authoritative for *current
  positions and NAV*, not history. Full history requires the **Flex Web
  Service** (`ibkr_flex.py`) — a one-time setup in IBKR Account Management
  (Reports > Flex Queries) plus a token (Settings > API > Flex Web Service).
- **Flex Query columns are user-configurable** in IBKR's UI. The attribute
  names `ibkr_flex.py` reads match IBKR's default Activity Flex XML — verify
  against a real export before trusting the numbers if your query differs.
- **yfinance is an unofficial scraper**, not a supported API. It throttles,
  breaks silently on Yahoo changes, and has poor coverage of European ETFs
  when searched by name/ticker alone — the security resolver works around
  this by building the Yahoo symbol directly from OpenFIGI's exchange code
  (see `domain/exchanges.py::yahoo_symbol_from_figi`) rather than relying on
  Yahoo's own search to find it. `fast_info` keys are camelCase (`lastPrice`,
  `previousClose`) — easy to get wrong, already bit us once. Some listings
  (notably London, `.L`) quote in MINOR units (pence, `GBp`/`GBX`) rather
  than the ISO currency's major unit — `yfinance_adapter.py::_normalize_currency`
  divides by 100 for the currencies we know about; a price 100x too high on
  a newly-mapped asset almost always means a new minor-unit currency turned
  up and needs adding there. Unmapped assets are flagged `needs_mapping=True`;
  resolve them via the security resolver (`POST /api/assets/{id}/resolve`,
  or the scheduled job) or manually via `POST /api/assets/{id}/map`. For many
  mutual funds `.info["shortName"]` is just the bare symbol again (no real
  short name) while `.info["longName"]` has the actual readable one — e.g.
  Yahoo's `0P0001CLDM.F` gives `shortName="0P0001CLDM.F"` but
  `longName="Fidelity S&P 500 Index EUR P Acc"`. A plain `shortName or
  longName` silently picks the useless one; `get_listing_info` only takes
  `shortName` when it differs from the symbol (see the real MyInvestor
  fund names this fixed, in git history).
- **OpenFIGI's free tier is rate-limited** (roughly 25 req/min without an API
  key — check https://www.openfigi.com/api for the current number). An
  optional `OPENFIGI_API_KEY` raises it. `OpenFigiSecurityMaster.map_isin`
  retries once on HTTP 429 (honoring `retry-after`) and returns `None`
  (not `[]`) on a second 429 or any other failure — `None` means "try again
  later," `[]` means "OpenFIGI confirms this ISIN has no listings." Only the
  ISIN is ever sent to OpenFIGI, never account/holding data.
- **`MyInvestor`'s import mapping is verified against one real export shape, not all of them.**
  `adapters/brokers/statement_files/myinvestor.py::MYINVESTOR_HEADER_ALIASES` /
  `MYINVESTOR_TYPE_MAP` are confirmed against a real "Aportaciones"/buy-order
  export (`Fecha de la orden;ISIN;Importe estimado;Nº de participaciones;Estado`,
  semicolon CSV, no operation-type column — every row in it is inferred as
  BUY). A different MyInvestor export (e.g. one that also covers sells) may
  use different headers/values; the import preview's "detected headers /
  could not map" panel names exactly what it saw, so extending the alias
  lists is a copy-paste job, not a guess.
- **FX must be historical.** `MarketDataRepo.get_fx_rate` always returns the
  most recent rate at or before a given date — applying "today's rate" to
  past dates would silently rewrite history and conflate FX gain with asset
  gain. `build_snapshots.py` converts cost basis at the transaction's own date.
- **The app is usually off.** `build_snapshots.py` is a pure, idempotent
  replay (delete + rewrite a date range), and `main.py` gap-fills every
  missing day on startup — the APScheduler jobs in `adapters/scheduler.py`
  are convenience on top of that, never the source of truth.
- No tax lots, no time-weighted returns in v1 — average cost only, and
  returns are simple price returns per period. Don't add either without
  discussing the added complexity first (mid-period cash flows make TWR
  genuinely ambiguous, not just more code).

## Credential handling (non-negotiable)

- Never commit `.env`, tokens, account numbers, or the Flex token — see root
  `AGENTS.md` security rules. `IBKR_FLEX_TOKEN` is a long-lived secret with
  read access to your full statements; treat it like a password.
- Never log a credential or account number, including in exception messages
  (`ibkr.py`'s `BrokerConnectionError` message is deliberately generic).

## Database & migrations

Schema is versioned with Alembic (`backend/migrations/`), generated from
`adapters/persistence/orm.py`. Never call `Base.metadata.create_all()` —
`main.py` runs `alembic upgrade head` on startup instead. To change the
schema: edit `orm.py`, then `alembic revision --autogenerate -m "..."` from
`backend/`, review the generated migration by hand (autogenerate misses
things like the `pg_trgm` GIN indexes), then commit both. See
`../database/AGENTS.md` for the full schema rationale.

## Conventions

- Python 3.11+, FastAPI, SQLAlchemy 2.0, Pydantic v2. `ruff` for format/lint.
  Type hints throughout.
- Functional/dataclass style in `domain` and `application` — no unnecessary
  classes-as-namespaces, no abstractions beyond what today's broker/data-
  source set needs.

## Running locally

```
cp .env.example .env   # fill in IBKR_FLEX_TOKEN etc. once you have them
docker compose up --build   # from the repo root — backend + frontend + db + pgadmin
```
Or outside Docker (needs your own local Postgres, or `docker compose up -d db`):
```
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -e .
uvicorn app.main:app --reload
```
Interactive Brokers: launch TWS or IB Gateway yourself, log in, enable API
access (Configure > API > Settings > Enable ActiveX and Socket Clients),
paper account port 7497. The backend connects to it via `host.docker.internal`
when running in Docker.

Tests: `pytest` (needs a running Postgres — `docker compose up -d db` first,
since `main.py`'s lifespan runs migrations against `DATABASE_URL` on app
startup, which `TestClient` triggers).

MCP server (Phase 5, optional — only needed to use/develop the agent-facing
tools): `pip install -e ".[ai]"` then `python -m ai.mcp_servers.security`,
or point an MCP client (`.mcp.json`, Claude Desktop, ...) at that same
command with `cwd` set to `backend/`. See `ai/mcp_servers/security/README.md`.

security_resolver agent (Phase 6, optional): also needs a reachable model
backend — either [Ollama](https://ollama.com) running locally with a
tool-capable model pulled (its Ollama library page must list **tools**),
or an API key for an OpenAI-compatible provider like Groq — plus
`AGENT_ENABLED=true`, `AGENT_PROVIDER`, `AGENT_BASE_URL`, `AGENT_MODEL` (and
`AGENT_API_KEY` for the `openai` provider) set in `.env`. Try it via
`POST /api/resolutions/{id}/agent` on a `NEEDS_AGENT` resolution, or
evaluate a model offline first — see
`ai/agents/security_resolver/evaluation/README.md`.
