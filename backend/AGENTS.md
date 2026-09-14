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

  ports/              Interfaces the domain/application layer depends on
    broker.py            BrokerPort — live account state from a broker's API
    statements.py         StatementPort — FULL historical transactions
    market_data.py         MarketDataPort + FxRatePort — quotes/bars/FX
    repositories.py         AssetRepo, PortfolioRepo, MarketDataRepo — persistence

  application/        Use cases: plain classes, ports injected via constructor
    sync_broker.py, import_transactions.py, manual_entry.py,
    refresh_market_data.py, build_snapshots.py,
    query_portfolio.py, query_asset.py, search_assets.py,
    asset_resolution.py   shared identity-resolution logic (see below)
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
    persistence/orm.py        SQLAlchemy mapped tables (the ONLY module that knows the physical schema)
    persistence/repositories.py  SqlAssetRepo / SqlPortfolioRepo / SqlMarketDataRepo
    market_data/yfinance_adapter.py, fx_adapter.py
    scheduler.py               APScheduler jobs calling use cases

  api/                Inbound adapter: FastAPI
    routes/*.py          One router per resource; thin — build a use case via
                         `container.py`, call it, `db.commit()`, return the DTO
    schemas.py            Pydantic REQUEST models only (see below)
```

**Dependency direction is one-way**: `domain` imports nothing from this app.
`application` imports `domain` and `ports` only — never `adapters`, never
FastAPI/SQLAlchemy. `adapters` implement `ports` and may import `domain`.
`container.py` is the only place that imports both a port and its concrete
adapter to wire them together. `api/routes` call `container.build_*()` and
nothing else — no adapter imports, no business logic in a route body.

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
  (`.DE`/`.MC`/`.AS` suffixes) and MyInvestor-style funds (often ISIN-only,
  not on Yahoo at all). `fast_info` keys are camelCase (`lastPrice`,
  `previousClose`) — easy to get wrong, already bit us once. Unmapped assets
  are flagged `needs_mapping=True`; resolve them via `POST /api/assets/{id}/map`.
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
