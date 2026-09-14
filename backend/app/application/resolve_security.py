"""The security resolver — deciding which Yahoo Finance listing prices an
asset that came in flagged `needs_mapping`. Pipeline: build a
`ResolutionContext` from DB state -> generate candidate listings (OpenFIGI +
Yahoo) -> score them with plain rules -> either apply the winner
automatically, or persist an open resolution for an agent (a later phase) or
a human to finish via `accept()`/`add_candidate()`/`flag_for_review()`. See
plans/agentic_asset_mapping.md Phase 3 (generation) and Phase 4 (scoring/
decision/persistence) for the full design.

Deliberately makes real network calls (OpenFIGI, Yahoo) — unlike most of the
app, this is meant to be invoked from a background job (see
adapters/scheduler.py) or an explicit "resolve now" user action, never a
request handler serving a page load (see backend/AGENTS.md's market-data rule).
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from app.domain.errors import (
    AssetConflictError,
    AssetNotFoundError,
    DomainError,
    ResolutionNotFoundError,
)
from app.domain.exchanges import broker_code_to_mic, mic_for_yahoo_symbol, yahoo_symbol_from_figi
from app.domain.listing_scoring import SCORER_VERSION, decide, score_candidates
from app.domain.listings import (
    Candidate,
    FigiListing,
    ResolutionContext,
    ResolutionDTO,
    ResolutionStatus,
    resolved_display_name,
)
from app.domain.models import AssetClass
from app.ports.market_data import MarketDataPort
from app.ports.repositories import AssetRepo, PortfolioRepo, ResolutionRepo
from app.ports.security_master import SecurityMasterPort

# How many candidates go on to the (expensive) get_listing_info validation
# call. Generation can turn up more than this from OpenFIGI alone for a
# security listed on many exchanges — keep only the most promising ones,
# ranked by _rank_key below, before spending an HTTP round trip on each.
MAX_CANDIDATES = 8

# OpenFIGI securityType -> our AssetClass value. Anything not listed here
# falls through to a Yahoo quote_type guess (see _infer_asset_class) or
# stays None (leaves the asset's current asset_class alone).
_FIGI_SECURITY_TYPE_TO_ASSET_CLASS: dict[str, str] = {
    "ETP": "ETF",
    "Mutual Fund": "FUND",
    "Open-End Fund": "FUND",
    "Common Stock": "EQUITY",
    "REIT": "EQUITY",
}
_YAHOO_QUOTE_TYPE_TO_ASSET_CLASS: dict[str, str] = {
    "ETF": "ETF",
    "EQUITY": "EQUITY",
    "MUTUALFUND": "FUND",
}


def _infer_asset_class(candidate: Candidate) -> str | None:
    if candidate.figi is not None:
        by_figi = _FIGI_SECURITY_TYPE_TO_ASSET_CLASS.get(candidate.figi.security_type)
        if by_figi is not None:
            return by_figi
    if candidate.info is not None and candidate.info.quote_type is not None:
        return _YAHOO_QUOTE_TYPE_TO_ASSET_CLASS.get(candidate.info.quote_type.upper())
    return None


def _rank_key(ctx: ResolutionContext, candidate: Candidate) -> tuple[int, int]:
    """Sort key for picking which MAX_CANDIDATES survive to validation —
    most-promising first (used with sorted(..., key=...), ascending, so
    "better" must sort smaller: negate both)."""
    confirmed_by_two_sources = {"openfigi", "yahoo_isin"} <= candidate.found_by
    same_exchange = bool(ctx.broker_mic) and candidate.mic == ctx.broker_mic
    return (-int(confirmed_by_two_sources), -int(same_exchange))


class ResolveSecurityUseCase:
    def __init__(
        self,
        asset_repo: AssetRepo,
        portfolio_repo: PortfolioRepo,
        resolution_repo: ResolutionRepo,
        security_master: SecurityMasterPort,
        market_data: MarketDataPort,
        agent_enabled: bool = False,
    ) -> None:
        self.asset_repo = asset_repo
        self.portfolio_repo = portfolio_repo
        self.resolution_repo = resolution_repo
        self.security_master = security_master
        self.market_data = market_data
        self.agent_enabled = agent_enabled

    # --- context -----------------------------------------------------

    def _build_context(self, asset_id: int) -> ResolutionContext:
        asset = self.asset_repo.get(asset_id)
        if asset is None:
            raise AssetNotFoundError(f"Asset {asset_id} not found")

        # Transactions (not a separate holdings lookup — PortfolioRepo has
        # no "holdings for this asset across every account" query, and
        # holdings are themselves derived from transactions in this app —
        # see import_transactions.py's _fill_holdings_gaps_from_transactions)
        # are what carry the broker's own currency and which account (and
        # so which broker_key) actually holds this asset.
        transactions = self.portfolio_repo.list_transactions(asset_id=asset_id)
        currency = asset.currency
        broker_key = None
        if transactions:
            currency = Counter(t.currency for t in transactions).most_common(1)[0][0]
            account = self.portfolio_repo.get_account(transactions[0].account_id)
            broker_key = account.broker_key if account else None

        return ResolutionContext(
            asset_id=asset_id,
            isin=asset.isin,
            broker_symbol=asset.symbol,
            broker_name=asset.name,
            broker_exchange=asset.exchange,
            broker_mic=broker_code_to_mic(asset.exchange),
            currency=currency,
            broker_key=broker_key,
        )

    # --- generation ----------------------------------------------------

    def generate_candidates(self, ctx: ResolutionContext) -> list[Candidate]:
        """Find and validate candidate listings for `ctx`. Makes real
        OpenFIGI/Yahoo calls — see module docstring. Returns candidates with
        `.info` populated where the symbol validated, and left None where it
        didn't (e.g. no recent trades) — score_candidates decides what that
        means, not this method."""
        by_symbol: dict[str, Candidate] = {}

        def add(symbol: str | None, source: str, figi: FigiListing | None = None) -> None:
            if not symbol:
                return
            existing = by_symbol.get(symbol)
            if existing is not None:
                existing.found_by.add(source)
                if figi is not None and existing.figi is None:
                    existing.figi = figi
                return
            by_symbol[symbol] = Candidate(
                symbol=symbol, found_by={source}, figi=figi, mic=mic_for_yahoo_symbol(symbol)
            )

        if ctx.isin:
            figi_listings = self.security_master.map_isin(ctx.isin)
            for listing in figi_listings or []:  # None (provider unavailable) is fine — just fewer candidates
                add(yahoo_symbol_from_figi(listing.ticker, listing.exch_code), "openfigi", listing)

            for hit in self.market_data.search(ctx.isin):
                add(hit.symbol, "yahoo_isin")
        else:
            # No ISIN (e.g. an IBKR live-sync-only asset) — OpenFIGI needs
            # one, so fall back to free-text search on whatever the broker
            # gave us.
            for query in {q for q in (ctx.broker_symbol, ctx.broker_name) if q}:
                for hit in self.market_data.search(query):
                    add(hit.symbol, "yahoo_text")

        ranked = sorted(by_symbol.values(), key=lambda c: _rank_key(ctx, c))
        survivors = ranked[:MAX_CANDIDATES]

        for candidate in survivors:
            candidate.info = self.market_data.get_listing_info(candidate.symbol)
            candidate.asset_class = _infer_asset_class(candidate)

        return survivors

    # --- scoring / decision / persistence -------------------------------

    def preview(self, ctx: ResolutionContext) -> list[Candidate]:
        """Dry run: generate + score, no DB writes. Used by the
        `resolve_isin` MCP tool (a later phase) so an agent can explore
        without committing to anything."""
        candidates = self.generate_candidates(ctx)
        score_candidates(ctx, candidates, today=date.today())
        return sorted(candidates, key=lambda c: c.score, reverse=True)

    def resolve_asset(self, asset_id: int) -> ResolutionDTO:
        """Run the full pipeline for one asset: generate, score, decide,
        and — if the rules are confident enough — apply the winning listing
        immediately. Always persists a resolution row, even when nothing
        could be auto-decided, so the asset shows up for review/an agent."""
        ctx = self._build_context(asset_id)
        candidates = self.generate_candidates(ctx)
        score_candidates(ctx, candidates, today=date.today())
        status, chosen, note = decide(candidates, self.agent_enabled)

        decided_by = None
        if chosen is not None:
            try:
                self.asset_repo.apply_listing(
                    asset_id,
                    chosen.symbol,
                    chosen.info.currency,
                    chosen.mic,
                    AssetClass(chosen.asset_class) if chosen.asset_class else None,
                    chosen.figi.share_class_figi if chosen.figi else None,
                    name=resolved_display_name(chosen.info, chosen.symbol),
                )
                decided_by = "rules"
            except AssetConflictError as exc:
                status, chosen, note = ResolutionStatus.NEEDS_REVIEW, None, str(exc)

        self.resolution_repo.supersede_open(asset_id)
        resolution_id = self.resolution_repo.create(ctx, status, decided_by, note, SCORER_VERSION, candidates)
        if chosen is not None:
            self.resolution_repo.select_candidate(resolution_id, chosen.id)
        return self.resolution_repo.get(resolution_id)

    def add_candidate(self, resolution_id: int, symbol: str, source: str = "user") -> Candidate:
        """Validate and score a symbol an agent or user found (e.g. via the
        `search_listings`/`lookup_isin` MCP tools, or typed into the review
        UI) and add it to an existing resolution — does NOT apply it as the
        asset's listing; call `accept()` for that. Rescores every candidate
        on the resolution against the enlarged set (a newly-added listing
        can change who's "most_liquid"), but only the NEW candidate's score
        is persisted — an older candidate's persisted `most_liquid` going
        very slightly stale is harmless (resolve_asset() rescores everyone
        from scratch on any fresh attempt) and avoids a bulk-update repo
        method for a cosmetic feature."""
        resolution = self.resolution_repo.get(resolution_id)
        if resolution is None:
            raise ResolutionNotFoundError(f"Resolution {resolution_id} not found")
        if any(c.symbol == symbol for c in resolution.candidates):
            raise DomainError(f"{symbol!r} is already a candidate on this resolution")

        candidate = Candidate(symbol=symbol, found_by={source}, mic=mic_for_yahoo_symbol(symbol))
        candidate.info = self.market_data.get_listing_info(symbol)
        candidate.asset_class = _infer_asset_class(candidate)

        score_candidates(resolution.context, [*resolution.candidates, candidate], today=date.today())
        candidate_id = self.resolution_repo.add_candidate(resolution_id, candidate)
        candidate.id = candidate_id
        return candidate

    def accept(self, resolution_id: int, candidate_id: int, decided_by: str, note: str = "") -> ResolutionDTO:
        """Apply one of a resolution's candidates as the asset's pricing
        listing — the single write path used by both the user-facing REST
        endpoint and the (later) `save_security_mapping` MCP tool.
        `decided_by` is 'user' or 'agent'; anything else is a programming
        error, not a user-facing one, so it isn't specially handled."""
        resolution = self.resolution_repo.get(resolution_id)
        if resolution is None:
            raise ResolutionNotFoundError(f"Resolution {resolution_id} not found")
        if resolution.status.is_terminal:
            raise DomainError(f"Resolution {resolution_id} is already {resolution.status.value}")
        candidate = next((c for c in resolution.candidates if c.id == candidate_id), None)
        if candidate is None:
            raise DomainError(f"Candidate {candidate_id} does not belong to resolution {resolution_id}")
        if not candidate.features.get("has_recent_price"):
            raise DomainError(f"{candidate.symbol!r} has no recent price — can't be accepted")
        if candidate.info is None or candidate.info.currency is None:
            raise DomainError(f"{candidate.symbol!r} has no currency — can't be accepted")

        asset_class = AssetClass(candidate.asset_class) if candidate.asset_class else None
        self.asset_repo.apply_listing(
            resolution.asset_id,
            candidate.symbol,
            candidate.info.currency,
            candidate.mic,
            asset_class,
            None,
            name=resolved_display_name(candidate.info, candidate.symbol),
        )
        self.resolution_repo.select_candidate(resolution_id, candidate_id)
        status = ResolutionStatus.RESOLVED_BY_AGENT if decided_by == "agent" else ResolutionStatus.RESOLVED_BY_USER
        self.resolution_repo.set_status(resolution_id, status, decided_by, note)
        return self.resolution_repo.get(resolution_id)

    def flag_for_review(self, resolution_id: int, note: str, flagged_by: str | None = None) -> ResolutionDTO:
        """Hand a resolution to a human — the escape hatch for an agent (or
        a user) that can't confidently pick a candidate. NEEDS_REVIEW isn't
        a terminal status (see ResolutionStatus.is_terminal), so this can
        run on any still-open resolution, including one already flagged."""
        resolution = self.resolution_repo.get(resolution_id)
        if resolution is None:
            raise ResolutionNotFoundError(f"Resolution {resolution_id} not found")
        if resolution.status.is_terminal:
            raise DomainError(f"Resolution {resolution_id} is already {resolution.status.value}")
        self.resolution_repo.set_status(resolution_id, ResolutionStatus.NEEDS_REVIEW, flagged_by, note)
        return self.resolution_repo.get(resolution_id)
