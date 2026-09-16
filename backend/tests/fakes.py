"""Minimal in-memory stand-ins for AssetRepo/PortfolioRepo/ResolutionRepo,
covering only the methods the use cases under test actually call. Not full
ABC implementations — duck-typed on purpose so these tests stay fast and
DB-free (see backend/AGENTS.md: `backend/tests/` needs a running Postgres
only for test_health.py's TestClient and the *_sql.py tests; these must not)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from app.domain.errors import AssetConflictError, AssetNotFoundError, ResolutionNotFoundError
from app.domain.listings import (
    AgentRunRecord,
    Candidate,
    ResolutionContext,
    ResolutionDTO,
    ResolutionStatus,
)
from app.domain.models import (
    Account,
    AccountSource,
    Asset,
    AssetClass,
    ChatMessage,
    ChatSession,
    IdentifierScheme,
    Note,
    Transaction,
)
from app.domain.quant.types import QuantRunRecord


class FakeAssetRepo:
    def __init__(self) -> None:
        self._assets: dict[int, Asset] = {}
        self._by_identifier: dict[tuple[IdentifierScheme, str], int] = {}
        self._next_id = 1

    def get(self, asset_id: int) -> Asset | None:
        return self._assets.get(asset_id)

    def get_by_symbol(self, symbol: str) -> Asset | None:
        return next((a for a in self._assets.values() if a.symbol == symbol), None)

    def find_by_identifier(self, scheme: IdentifierScheme, value: str) -> Asset | None:
        asset_id = self._by_identifier.get((scheme, value))
        return self._assets.get(asset_id) if asset_id is not None else None

    def create(
        self,
        symbol: str,
        name: str,
        asset_class: AssetClass,
        currency: str,
        exchange: str | None = None,
        isin: str | None = None,
        needs_mapping: bool = False,
    ) -> Asset:
        asset = Asset(
            id=self._next_id,
            symbol=symbol,
            name=name,
            asset_class=asset_class,
            currency=currency,
            exchange=exchange,
            isin=isin,
            needs_mapping=needs_mapping,
        )
        self._assets[asset.id] = asset
        self._next_id += 1
        return asset

    def add_identifier(self, asset_id: int, scheme: IdentifierScheme, value: str) -> None:
        self._by_identifier[(scheme, value)] = asset_id

    def set_needs_mapping(self, asset_id: int, needs_mapping: bool) -> None:
        self._assets[asset_id].needs_mapping = needs_mapping

    def set_isin(self, asset_id: int, isin: str) -> None:
        if not self._assets[asset_id].isin:
            self._assets[asset_id].isin = isin

    def list_needing_mapping(self) -> list[Asset]:
        return [a for a in self._assets.values() if a.needs_mapping]

    def get_identifier_value(self, asset_id: int, scheme: IdentifierScheme) -> str | None:
        for (s, value), aid in self._by_identifier.items():
            if s == scheme and aid == asset_id:
                return value
        return None

    def list_assets_with_scheme(self, scheme: IdentifierScheme) -> list[tuple[Asset, str]]:
        return [
            (self._assets[aid], value) for (s, value), aid in self._by_identifier.items() if s == scheme
        ]

    def search_local(self, query: str, limit: int = 20) -> list[Asset]:
        return [a for a in self._assets.values() if query.lower() in a.symbol.lower()][:limit]

    def list_all(self) -> list[Asset]:
        return list(self._assets.values())

    def apply_listing(
        self,
        asset_id: int,
        yahoo_symbol: str,
        currency: str,
        mic: str | None,
        asset_class: AssetClass | None,
        share_class_figi: str | None,
        name: str | None = None,
    ) -> None:
        conflict_id = self._by_identifier.get((IdentifierScheme.YFINANCE, yahoo_symbol))
        if conflict_id is not None and conflict_id != asset_id:
            raise AssetConflictError(f"{yahoo_symbol!r} is already mapped to asset {conflict_id}")
        asset = self._assets.get(asset_id)
        if asset is None:
            raise AssetNotFoundError(f"Asset {asset_id} not found")

        # drop this asset's other YFINANCE identifiers — one pricing listing per asset
        for key in [k for k, v in self._by_identifier.items() if k[0] == IdentifierScheme.YFINANCE and v == asset_id]:
            del self._by_identifier[key]
        self._by_identifier[(IdentifierScheme.YFINANCE, yahoo_symbol)] = asset_id

        asset.currency = currency.upper()
        asset.exchange = mic
        asset.needs_mapping = False
        if asset_class is not None:
            asset.asset_class = asset_class
        if share_class_figi is not None:
            asset.share_class_figi = share_class_figi
        if name is not None:
            asset.name = name


@dataclass
class _HoldingRow:
    account_id: int
    asset_id: int
    quantity: Decimal
    avg_cost_price: Decimal
    cost_currency: str
    as_of: datetime
    source: str


class FakePortfolioRepo:
    def __init__(self, asset_repo: FakeAssetRepo | None = None) -> None:
        # asset_repo is optional and only needed by list_positions (which
        # joins holdings -> assets, same as SqlPortfolioRepo's real query,
        # to fill in symbol/name/currency) — most existing tests never call
        # list_positions and construct this with no arguments.
        self._asset_repo = asset_repo
        self._accounts: dict[int, Account] = {}
        self._next_account_id = 1
        self._transactions: list[Transaction] = []
        self._next_txn_id = 1
        self._holdings: dict[tuple[int, int], _HoldingRow] = {}
        self._cash_balances: dict[tuple[int, str], dict] = {}

    def get_or_create_account(
        self, broker_key: str, external_id: str, name: str, currency: str, source: str
    ) -> Account:
        existing = next(
            (a for a in self._accounts.values() if a.broker_key == broker_key and a.external_id == external_id),
            None,
        )
        if existing:
            return existing
        account = Account(
            id=self._next_account_id,
            broker_key=broker_key,
            external_id=external_id,
            name=name,
            currency=currency,
            source=AccountSource(source),
        )
        self._accounts[account.id] = account
        self._next_account_id += 1
        return account

    def list_accounts(self) -> list[Account]:
        return list(self._accounts.values())

    def get_account(self, account_id: int) -> Account | None:
        return self._accounts.get(account_id)

    def upsert_holding(
        self,
        account_id: int,
        asset_id: int,
        quantity: Decimal,
        avg_cost_price: Decimal,
        cost_currency: str,
        as_of: datetime,
        source: str,
    ) -> None:
        self._holdings[(account_id, asset_id)] = _HoldingRow(
            account_id, asset_id, quantity, avg_cost_price, cost_currency, as_of, source
        )

    def replace_holdings_for_account(self, account_id: int, source: str) -> None:
        self._holdings = {k: v for k, v in self._holdings.items() if k[0] != account_id}

    def delete_holding(self, account_id: int, asset_id: int) -> None:
        self._holdings.pop((account_id, asset_id), None)

    def get_holding(self, account_id: int, asset_id: int) -> dict | None:
        row = self._holdings.get((account_id, asset_id))
        return None if row is None else vars(row)

    def list_positions(self, account_id: int | None = None) -> list[dict]:
        """Mirrors SqlPortfolioRepo.list_positions' join + zero-quantity
        filter (see app/adapters/persistence/repositories.py) rather than
        the old vars(_HoldingRow) shape, which was missing symbol/name/
        currency/broker_key — fine when nothing exercised it, wrong once
        QueryPortfolioUseCase (which reads exactly those keys) is under
        test against this fake."""
        rows = []
        for h in self._holdings.values():
            if account_id is not None and h.account_id != account_id:
                continue
            if h.quantity == Decimal("0"):
                continue
            account = self._accounts.get(h.account_id)
            asset = self._asset_repo.get(h.asset_id) if self._asset_repo else None
            rows.append(
                {
                    "asset_id": h.asset_id,
                    "symbol": asset.symbol if asset else f"asset-{h.asset_id}",
                    "name": asset.name if asset else "",
                    "currency": asset.currency if asset else h.cost_currency,
                    "account_id": h.account_id,
                    "broker_key": account.broker_key if account else "unknown",
                    "quantity": h.quantity,
                    "avg_cost_price": h.avg_cost_price,
                }
            )
        return rows

    def upsert_cash_balance(self, account_id: int, currency: str, amount: Decimal, as_of: datetime) -> None:
        self._cash_balances[(account_id, currency.upper())] = {
            "account_id": account_id,
            "currency": currency.upper(),
            "amount": amount,
            "as_of": as_of,
        }

    def list_cash_balances(self, account_id: int | None = None) -> list[dict]:
        return [b for b in self._cash_balances.values() if account_id is None or b["account_id"] == account_id]

    def add_transactions(self, transactions: list[Transaction]) -> int:
        if not transactions:
            return 0
        existing = {(t.account_id, t.external_id) for t in self._transactions if t.external_id}
        inserted = 0
        for t in transactions:
            if t.external_id and (t.account_id, t.external_id) in existing:
                continue
            stored = Transaction(
                id=self._next_txn_id,
                account_id=t.account_id,
                asset_id=t.asset_id,
                type=t.type,
                quantity=t.quantity,
                price=t.price,
                fees=t.fees,
                currency=t.currency,
                executed_at=t.executed_at,
                trade_date=t.trade_date,
                external_id=t.external_id,
                source=t.source,
                note=t.note,
            )
            self._next_txn_id += 1
            self._transactions.append(stored)
            if t.external_id:
                existing.add((t.account_id, t.external_id))
            inserted += 1
        return inserted

    def list_transactions(
        self, account_id: int | None = None, asset_id: int | None = None, since: date | None = None
    ) -> list[Transaction]:
        rows = self._transactions
        if account_id is not None:
            rows = [t for t in rows if t.account_id == account_id]
        if asset_id is not None:
            rows = [t for t in rows if t.asset_id == asset_id]
        if since is not None:
            rows = [t for t in rows if t.trade_date >= since]
        return list(rows)

    def add_manual_transaction(self, transaction: Transaction) -> Transaction:
        self.add_transactions([transaction])
        return self._transactions[-1]

    def update_manual_transaction(self, transaction_id: int, **fields) -> None:
        pass

    def delete_manual_transaction(self, transaction_id: int) -> None:
        self._transactions = [t for t in self._transactions if t.id != transaction_id]

    def delete_snapshots_in_range(self, start: date, end: date) -> None:
        pass

    def upsert_position_snapshot(self, *args, **kwargs) -> None:
        pass

    def upsert_portfolio_snapshot(self, *args, **kwargs) -> None:
        pass

    def get_portfolio_snapshots(self, start: date, end: date) -> list:
        return []

    def get_position_snapshots_totals(self, start: date, end: date, asset_ids: list[int]) -> list[dict]:
        return []

    def get_latest_snapshot(self):
        return None

    def last_snapshot_date(self) -> date | None:
        return None

    def earliest_transaction_date(self, account_id: int | None = None) -> date | None:
        rows = self.list_transactions(account_id=account_id)
        return min((t.trade_date for t in rows), default=None)


@dataclass
class _ResolutionRow:
    id: int
    asset_id: int
    context: ResolutionContext
    status: ResolutionStatus
    decided_by: str | None
    note: str
    scorer_version: str
    candidates: list[Candidate]


class FakeResolutionRepo:
    """In-memory ResolutionRepo — see module docstring. Candidates are
    stored by reference (not copied), matching SqlResolutionRepo's contract
    of backfilling `.id` onto the caller's own Candidate objects."""

    def __init__(self) -> None:
        self._resolutions: dict[int, _ResolutionRow] = {}
        self._selected: dict[int, int] = {}  # resolution_id -> selected candidate_id
        self._next_resolution_id = 1
        self._next_candidate_id = 1
        self.agent_runs: list[AgentRunRecord] = []

    def create(
        self,
        ctx: ResolutionContext,
        status: ResolutionStatus,
        decided_by: str | None,
        note: str,
        scorer_version: str,
        candidates: list[Candidate],
    ) -> int:
        rid = self._next_resolution_id
        self._next_resolution_id += 1
        for candidate in candidates:
            candidate.id = self._next_candidate_id
            self._next_candidate_id += 1
        self._resolutions[rid] = _ResolutionRow(rid, ctx.asset_id, ctx, status, decided_by, note, scorer_version, list(candidates))
        return rid

    def get(self, resolution_id: int) -> ResolutionDTO | None:
        row = self._resolutions.get(resolution_id)
        if row is None:
            return None
        return ResolutionDTO(
            id=row.id,
            asset_id=row.asset_id,
            context=row.context,
            status=row.status,
            decided_by=row.decided_by,
            note=row.note,
            scorer_version=row.scorer_version,
            candidates=list(row.candidates),
            selected_candidate_id=self._selected.get(row.id),
        )

    def get_open_for_asset(self, asset_id: int) -> ResolutionDTO | None:
        for row in self._resolutions.values():
            if row.asset_id == asset_id and row.status is not ResolutionStatus.SUPERSEDED:
                return self.get(row.id)
        return None

    def list_by_status(self, statuses: list[ResolutionStatus], limit: int = 50) -> list[ResolutionDTO]:
        matches = [row for row in self._resolutions.values() if row.status in statuses]
        return [self.get(row.id) for row in matches[:limit]]

    def add_candidate(self, resolution_id: int, candidate: Candidate) -> int:
        row = self._resolutions[resolution_id]
        candidate.id = self._next_candidate_id
        self._next_candidate_id += 1
        row.candidates.append(candidate)
        return candidate.id

    def set_status(self, resolution_id: int, status: ResolutionStatus, decided_by: str | None, note: str) -> None:
        row = self._resolutions.get(resolution_id)
        if row is None:
            raise ResolutionNotFoundError(f"Resolution {resolution_id} not found")
        row.status = status
        row.decided_by = decided_by
        row.note = note

    def select_candidate(self, resolution_id: int, candidate_id: int) -> None:
        self._selected[resolution_id] = candidate_id

    def supersede_open(self, asset_id: int) -> None:
        for row in self._resolutions.values():
            if row.asset_id == asset_id and row.status is not ResolutionStatus.SUPERSEDED:
                row.status = ResolutionStatus.SUPERSEDED

    def add_agent_run(self, run: AgentRunRecord) -> int:
        self.agent_runs.append(run)
        return len(self.agent_runs)

    def list_assets_to_resolve(self, retry_empty_after_hours: int = 24) -> list[int]:
        raise NotImplementedError("not needed by tests using this fake yet")

    def list_recent(self, since, decided_by=None, limit: int = 100):
        rows = [r for r in self._resolutions.values() if r.status is not ResolutionStatus.SUPERSEDED]
        if decided_by:
            rows = [r for r in rows if r.decided_by in decided_by]
        return [self.get(r.id) for r in rows[:limit]]


class FakeMarketDataRepo:
    """In-memory MarketDataRepo — see module docstring. Backs
    application/query_market_data.py and query_analytics.py's tests (Phase
    8a/8b of plans/agentic_asset_mapping_phase7_8.md)."""

    def __init__(self) -> None:
        self._quotes: dict[int, dict] = {}
        self._bars: dict[int, dict[date, dict]] = {}
        self._fx_rates: dict[tuple[str, str], dict[date, Decimal]] = {}

    def upsert_quote(
        self, asset_id: int, price: Decimal, prev_close: Decimal | None, currency: str, as_of: datetime, source: str
    ) -> None:
        self._quotes[asset_id] = {"price": price, "prev_close": prev_close, "currency": currency.upper(), "as_of": as_of}

    def get_quote(self, asset_id: int) -> dict | None:
        return self._quotes.get(asset_id)

    def upsert_bars(self, asset_id: int, bars: list[dict], source: str) -> None:
        by_date = self._bars.setdefault(asset_id, {})
        for bar in bars:
            by_date[bar["date"]] = dict(bar)

    def get_bars(self, asset_id: int, start: date, end: date) -> list[dict]:
        by_date = self._bars.get(asset_id, {})
        return [bar for d, bar in sorted(by_date.items()) if start <= d <= end]

    def get_price_on_or_before(self, asset_id: int, on_date: date) -> Decimal | None:
        by_date = self._bars.get(asset_id, {})
        candidates = [d for d in by_date if d <= on_date]
        if not candidates:
            return None
        return by_date[max(candidates)]["close"]

    def latest_price_date(self, asset_id: int) -> date | None:
        by_date = self._bars.get(asset_id, {})
        return max(by_date) if by_date else None

    def upsert_fx_rates(self, base: str, quote: str, rates: dict[date, Decimal]) -> None:
        self._fx_rates.setdefault((base.upper(), quote.upper()), {}).update(rates)

    def get_fx_rate(self, base: str, quote: str, on_date: date) -> Decimal | None:
        rates = self._fx_rates.get((base.upper(), quote.upper()), {})
        candidates = [d for d in rates if d <= on_date]
        return rates[max(candidates)] if candidates else None

    def currency_pairs_in_use(self, base_currency: str) -> list[str]:
        return sorted({base for base, quote in self._fx_rates if quote == base_currency.upper() and base != base_currency.upper()})


class FakeNoteRepo:
    """In-memory NoteRepo — see module docstring. Backs
    ai/mcp_servers/notes/'s tool tests (Phase 8c of
    plans/agentic_asset_mapping_phase7_8.md)."""

    def __init__(self) -> None:
        self._notes: dict[int, Note] = {}
        self._next_id = 1

    def add(self, agent: str, scope: str, title: str, body: str, account_id: int | None = None) -> int:
        note_id = self._next_id
        self._next_id += 1
        self._notes[note_id] = Note(
            id=note_id, agent=agent, scope=scope, account_id=account_id, title=title, body=body,
            created_at=datetime.now(timezone.utc), dismissed_at=None,
        )
        return note_id

    def list(self, scope: str | None = None, since: datetime | None = None, limit: int = 20) -> list[Note]:
        rows = sorted(self._notes.values(), key=lambda n: n.created_at, reverse=True)
        if scope is not None:
            rows = [n for n in rows if n.scope == scope]
        if since is not None:
            rows = [n for n in rows if n.created_at >= since]
        return rows[:limit]

    def dismiss(self, note_id: int) -> None:
        note = self._notes.get(note_id)
        if note is not None:
            note.dismissed_at = datetime.now(timezone.utc)


class FakeChatRepo:
    """In-memory ChatRepo — see module docstring. Backs
    ai/agents/portfolio_assistant/'s tests (Phase 8f of
    plans/agentic_asset_mapping_phase7_8.md)."""

    def __init__(self) -> None:
        self._sessions: dict[int, ChatSession] = {}
        self._messages: dict[int, list[ChatMessage]] = {}
        self._next_session_id = 1
        self._next_message_id = 1

    def create_session(self, title: str = "") -> int:
        session_id = self._next_session_id
        self._next_session_id += 1
        now = datetime.now(timezone.utc)
        self._sessions[session_id] = ChatSession(id=session_id, title=title, created_at=now, updated_at=now)
        self._messages[session_id] = []
        return session_id

    def list_sessions(self, limit: int = 50) -> list[ChatSession]:
        return sorted(self._sessions.values(), key=lambda s: s.updated_at, reverse=True)[:limit]

    def get_session(self, session_id: int) -> ChatSession | None:
        return self._sessions.get(session_id)

    def rename_session(self, session_id: int, title: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.title = title

    def delete_session(self, session_id: int) -> None:
        self._sessions.pop(session_id, None)
        self._messages.pop(session_id, None)

    def touch_session(self, session_id: int, as_of: datetime | None = None) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.updated_at = as_of or datetime.now(timezone.utc)

    def add_message(self, session_id: int, role: str, content: str, tool_calls: list[dict] | None = None) -> int:
        message_id = self._next_message_id
        self._next_message_id += 1
        message = ChatMessage(
            id=message_id, session_id=session_id, role=role, content=content,
            tool_calls=tool_calls, created_at=datetime.now(timezone.utc),
        )
        self._messages.setdefault(session_id, []).append(message)
        return message_id

    def list_messages(self, session_id: int, limit: int | None = None) -> list[ChatMessage]:
        messages = self._messages.get(session_id, [])
        return messages[-limit:] if limit is not None else list(messages)


class FakeQuantRunRepo:
    """In-memory QuantRunRepo — see module docstring. Backs
    application/run_quant_simulation.py's tests (plans/quant_lab.md)."""

    def __init__(self) -> None:
        self._runs: dict[int, QuantRunRecord] = {}
        self._next_id = 1

    def create(self, record: QuantRunRecord) -> int:
        run_id = self._next_id
        self._next_id += 1
        stored = QuantRunRecord(
            id=run_id, asset_id=record.asset_id, model_key=record.model_key, split_date=record.split_date,
            horizon_days=record.horizon_days, n_paths=record.n_paths, seed=record.seed, params=record.params,
            calibration_params=record.calibration_params, calibration_diagnostics=record.calibration_diagnostics,
            percentiles=record.percentiles, backtest=record.backtest, created_by=record.created_by,
            note=record.note, created_at=record.created_at,
        )
        self._runs[run_id] = stored
        return run_id

    def get(self, run_id: int) -> QuantRunRecord | None:
        return self._runs.get(run_id)

    def list_for_asset(self, asset_id: int, limit: int = 20) -> list[QuantRunRecord]:
        rows = sorted(
            (r for r in self._runs.values() if r.asset_id == asset_id), key=lambda r: r.created_at, reverse=True
        )
        return rows[:limit]
