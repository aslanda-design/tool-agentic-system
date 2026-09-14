"""Tests for ResolveSecurityUseCase (Phases 3 and 4 of
plans/agentic_asset_mapping.md — candidate generation, then scoring/decision
/persistence). Uses fake ports/repos so these stay fast and network-free."""

from __future__ import annotations

from datetime import UTC, date
from decimal import Decimal

import pytest

from app.application.resolve_security import MAX_CANDIDATES, ResolveSecurityUseCase
from app.domain.errors import DomainError, ResolutionNotFoundError
from app.domain.listings import (
    FigiListing,
    ListingInfo,
    ResolutionContext,
    ResolutionStatus,
)
from app.domain.models import AssetClass
from app.ports.market_data import AssetSearchResult
from tests.fakes import FakeAssetRepo, FakePortfolioRepo, FakeResolutionRepo


class FakeSecurityMaster:
    def __init__(self, by_isin: dict[str, list[FigiListing] | None] | None = None) -> None:
        self.by_isin = by_isin or {}
        self.calls: list[str] = []

    def map_isin(self, isin: str):
        self.calls.append(isin)
        return self.by_isin.get(isin, [])


class FakeMarketData:
    """Only the two MarketDataPort methods generate_candidates calls."""

    def __init__(
        self, search_results: dict[str, list[AssetSearchResult]] | None = None, listings: dict[str, ListingInfo] | None = None
    ) -> None:
        self.search_results = search_results or {}
        self.listings = listings or {}
        self.search_calls: list[str] = []
        self.info_calls: list[str] = []

    def search(self, query: str) -> list[AssetSearchResult]:
        self.search_calls.append(query)
        return self.search_results.get(query, [])

    def get_listing_info(self, symbol: str):
        self.info_calls.append(symbol)
        return self.listings.get(symbol)


def _listing(symbol: str, currency: str = "EUR", days_old: int = 0) -> ListingInfo:
    return ListingInfo(
        symbol=symbol,
        name=f"{symbol} name",
        currency=currency,
        quote_type="ETF",
        last_close=Decimal(100),
        last_trade_date=date(2026, 9, 14 - days_old) if days_old < 14 else date(2026, 8, 1),
        avg_volume=Decimal(1000),
    )


def _ctx(**overrides) -> ResolutionContext:
    base = {
        "asset_id": 1,
        "isin": "IE00B4L5Y983",
        "broker_symbol": "EUNL",
        "broker_name": "iShares Core MSCI World",
        "broker_exchange": "IBIS2",
        "broker_mic": "XETR",
        "currency": "EUR",
        "broker_key": "interactive_brokers",
    }
    base.update(overrides)
    return ResolutionContext(**base)


def _use_case(
    security_master=None,
    market_data=None,
    asset_repo=None,
    portfolio_repo=None,
    resolution_repo=None,
    agent_enabled=False,
) -> ResolveSecurityUseCase:
    return ResolveSecurityUseCase(
        asset_repo or FakeAssetRepo(),
        portfolio_repo or FakePortfolioRepo(),
        resolution_repo or FakeResolutionRepo(),
        security_master or FakeSecurityMaster(),
        market_data or FakeMarketData(),
        agent_enabled,
    )


def test_generates_candidates_from_openfigi_and_yahoo_isin_search():
    figi_listings = [
        FigiListing(ticker="EUNL", exch_code="GY", name="ISHARES CORE MSCI WORLD", security_type="ETP", share_class_figi="BBG000BLNNH6"),
        FigiListing(ticker="IWDA", exch_code="LN", name="ISHARES CORE MSCI WORLD", security_type="ETP", share_class_figi="BBG000BLNNH6"),
    ]
    security_master = FakeSecurityMaster({"IE00B4L5Y983": figi_listings})
    market_data = FakeMarketData(
        search_results={"IE00B4L5Y983": [AssetSearchResult(symbol="EUNL.DE", name="X", exchange=None, asset_class="ETF", currency="EUR")]},
        listings={"EUNL.DE": _listing("EUNL.DE", "EUR"), "IWDA.L": _listing("IWDA.L", "GBP")},
    )
    use_case = _use_case(security_master, market_data)

    candidates = use_case.generate_candidates(_ctx())

    by_symbol = {c.symbol: c for c in candidates}
    assert set(by_symbol) == {"EUNL.DE", "IWDA.L"}
    # EUNL.DE was found by BOTH openfigi and yahoo_isin search — sources merge
    assert by_symbol["EUNL.DE"].found_by == {"openfigi", "yahoo_isin"}
    assert by_symbol["IWDA.L"].found_by == {"openfigi"}
    assert by_symbol["EUNL.DE"].mic == "XETR"
    assert by_symbol["IWDA.L"].mic == "XLON"
    assert by_symbol["EUNL.DE"].info.currency == "EUR"
    assert by_symbol["EUNL.DE"].asset_class == "ETF"  # ETP -> ETF


def test_openfigi_unavailable_still_generates_candidates_from_yahoo():
    security_master = FakeSecurityMaster({"IE00B4L5Y983": None})  # provider unavailable
    market_data = FakeMarketData(
        search_results={"IE00B4L5Y983": [AssetSearchResult(symbol="EUNL.DE", name="X", exchange=None, asset_class="ETF", currency="EUR")]},
        listings={"EUNL.DE": _listing("EUNL.DE")},
    )
    use_case = _use_case(security_master, market_data)

    candidates = use_case.generate_candidates(_ctx())

    assert [c.symbol for c in candidates] == ["EUNL.DE"]
    assert candidates[0].found_by == {"yahoo_isin"}


def test_openfigi_warning_empty_list_yields_no_openfigi_candidates():
    security_master = FakeSecurityMaster({"IE00B4L5Y983": []})  # OpenFIGI: "no identifier found"
    market_data = FakeMarketData(search_results={}, listings={})
    use_case = _use_case(security_master, market_data)

    candidates = use_case.generate_candidates(_ctx())

    assert candidates == []


def test_no_isin_falls_back_to_broker_symbol_and_name_text_search():
    security_master = FakeSecurityMaster()  # must not be called without an ISIN
    market_data = FakeMarketData(
        search_results={
            "EUNL": [AssetSearchResult(symbol="EUNL.DE", name="X", exchange=None, asset_class="ETF", currency="EUR")],
            "iShares Core MSCI World": [AssetSearchResult(symbol="IWDA.L", name="Y", exchange=None, asset_class="ETF", currency="GBP")],
        },
        listings={"EUNL.DE": _listing("EUNL.DE"), "IWDA.L": _listing("IWDA.L", "GBP")},
    )
    use_case = _use_case(security_master, market_data)

    candidates = use_case.generate_candidates(_ctx(isin=None))

    assert security_master.calls == []
    assert set(market_data.search_calls) == {"EUNL", "iShares Core MSCI World"}
    assert {c.symbol for c in candidates} == {"EUNL.DE", "IWDA.L"}
    assert all(c.found_by == {"yahoo_text"} for c in candidates)


def test_candidate_missing_recent_prices_keeps_info_none_rather_than_dropping():
    security_master = FakeSecurityMaster({"IE00B4L5Y983": [FigiListing(ticker="EUNL", exch_code="GY", name="", security_type="ETP", share_class_figi=None)]})
    market_data = FakeMarketData(search_results={"IE00B4L5Y983": []}, listings={})  # EUNL.DE doesn't validate

    use_case = _use_case(security_master, market_data)
    candidates = use_case.generate_candidates(_ctx())

    assert len(candidates) == 1
    assert candidates[0].symbol == "EUNL.DE"
    assert candidates[0].info is None


def test_ranking_prefers_two_source_confirmation_and_matching_exchange():
    # Three candidates: A confirmed by both sources AND matches the broker's
    # exchange; B confirmed by both sources but a different exchange; C only
    # from one source. A must rank before B, B before C.
    figi_listings = [
        FigiListing(ticker="A", exch_code="GY", name="", security_type="ETP", share_class_figi=None),
        FigiListing(ticker="B", exch_code="LN", name="", security_type="ETP", share_class_figi=None),
        FigiListing(ticker="C", exch_code="US", name="", security_type="ETP", share_class_figi=None),
    ]
    security_master = FakeSecurityMaster({"IE00B4L5Y983": figi_listings})
    market_data = FakeMarketData(
        search_results={
            "IE00B4L5Y983": [
                AssetSearchResult(symbol="A.DE", name="X", exchange=None, asset_class="ETF", currency="EUR"),
                AssetSearchResult(symbol="B.L", name="X", exchange=None, asset_class="ETF", currency="GBP"),
            ]
        },
        listings={"A.DE": _listing("A.DE"), "B.L": _listing("B.L", "GBP"), "C": _listing("C", "USD")},
    )
    use_case = _use_case(security_master, market_data)

    candidates = use_case.generate_candidates(_ctx(broker_mic="XETR"))

    assert [c.symbol for c in candidates] == ["A.DE", "B.L", "C"]


def test_truncates_to_max_candidates_before_validating():
    # 12 distinct exchange codes -> 12 candidate symbols from OpenFIGI alone;
    # only MAX_CANDIDATES should ever reach get_listing_info.
    exch_codes = ["US", "LN", "GY", "GF", "GS", "GM", "GD", "GB", "GH", "NA", "FP", "BB"]
    figi_listings = [
        FigiListing(ticker=f"T{i}", exch_code=code, name="", security_type="ETP", share_class_figi=None)
        for i, code in enumerate(exch_codes)
    ]
    security_master = FakeSecurityMaster({"IE00B4L5Y983": figi_listings})
    market_data = FakeMarketData(search_results={"IE00B4L5Y983": []}, listings={})
    use_case = _use_case(security_master, market_data)

    candidates = use_case.generate_candidates(_ctx())

    assert len(candidates) == MAX_CANDIDATES
    assert len(market_data.info_calls) == MAX_CANDIDATES


# --- Phase 4: scoring / decision / persistence (resolve_asset, add_candidate, accept, flag_for_review) ---


def _setup_asset(
    asset_repo: FakeAssetRepo,
    portfolio_repo: FakePortfolioRepo,
    *,
    symbol="EUNL",
    isin="IE00B4L5Y983",
    currency="EUR",
    exchange="IBIS2",
    name="",
    broker_key="interactive_brokers",
):
    from datetime import date as date_cls
    from datetime import datetime

    from app.domain.models import AccountSource, Transaction, TransactionType

    asset = asset_repo.create(
        symbol=symbol, name=name, asset_class=AssetClass.EQUITY, currency=currency, exchange=exchange,
        isin=isin, needs_mapping=True,
    )
    account = portfolio_repo.get_or_create_account(broker_key, "U123", "Test account", currency, AccountSource.API)
    portfolio_repo.add_transactions(
        [
            Transaction(
                id=None, account_id=account.id, asset_id=asset.id, type=TransactionType.BUY,
                quantity=Decimal(1), price=Decimal(1), fees=Decimal(0), currency=currency,
                executed_at=datetime(2026, 1, 1, tzinfo=UTC), trade_date=date_cls(2026, 1, 1),
                external_id="x1", source=AccountSource.API,
            )
        ]
    )
    return asset


def _winning_setup(asset_repo=None, portfolio_repo=None, resolution_repo=None):
    """An asset + fakes wired so the resolver has exactly one clear winner
    (EUNL.DE) — used by several tests below as a known-auto-accept case."""
    asset_repo = asset_repo or FakeAssetRepo()
    portfolio_repo = portfolio_repo or FakePortfolioRepo()
    asset = _setup_asset(asset_repo, portfolio_repo)
    security_master = FakeSecurityMaster(
        {"IE00B4L5Y983": [FigiListing(ticker="EUNL", exch_code="GY", name="ISHARES CORE MSCI WORLD", security_type="ETP", share_class_figi="BBG000BLNNH6")]}
    )
    market_data = FakeMarketData(
        search_results={"IE00B4L5Y983": [AssetSearchResult(symbol="EUNL.DE", name="X", exchange=None, asset_class="ETF", currency="EUR")]},
        listings={"EUNL.DE": _listing("EUNL.DE", "EUR")},
    )
    use_case = _use_case(security_master, market_data, asset_repo, portfolio_repo, resolution_repo)
    return use_case, asset, asset_repo, portfolio_repo


def test_build_context_derives_currency_and_broker_key_from_transactions():
    asset_repo, portfolio_repo = FakeAssetRepo(), FakePortfolioRepo()
    asset = _setup_asset(asset_repo, portfolio_repo, currency="GBP", exchange="LSEETF", broker_key="interactive_brokers")
    use_case = _use_case(asset_repo=asset_repo, portfolio_repo=portfolio_repo)

    ctx = use_case._build_context(asset.id)

    assert ctx.isin == "IE00B4L5Y983"
    assert ctx.broker_symbol == "EUNL"
    assert ctx.broker_exchange == "LSEETF"
    assert ctx.broker_mic == "XLON"
    assert ctx.currency == "GBP"
    assert ctx.broker_key == "interactive_brokers"


def test_build_context_falls_back_to_asset_currency_without_transactions():
    asset_repo, portfolio_repo = FakeAssetRepo(), FakePortfolioRepo()
    asset = asset_repo.create(symbol="EUNL", name="", asset_class=AssetClass.EQUITY, currency="EUR", needs_mapping=True)
    use_case = _use_case(asset_repo=asset_repo, portfolio_repo=portfolio_repo)

    ctx = use_case._build_context(asset.id)

    assert ctx.currency == "EUR"
    assert ctx.broker_key is None


def test_resolve_asset_auto_accepts_and_applies_the_listing():
    use_case, asset, asset_repo, _ = _winning_setup()

    resolution = use_case.resolve_asset(asset.id)

    assert resolution.status is ResolutionStatus.AUTO_ACCEPTED
    assert resolution.decided_by == "rules"
    updated = asset_repo.get(asset.id)
    assert updated.needs_mapping is False
    assert updated.currency == "EUR"
    assert updated.share_class_figi == "BBG000BLNNH6"


def test_resolve_asset_conflict_falls_back_to_needs_review():
    asset_repo, portfolio_repo = FakeAssetRepo(), FakePortfolioRepo()
    other = asset_repo.create(symbol="OTHER", name="", asset_class=AssetClass.EQUITY, currency="EUR", needs_mapping=True)
    asset_repo.apply_listing(other.id, "EUNL.DE", "EUR", "XETR", None, None)  # steal the symbol first

    use_case, asset, _, _ = _winning_setup(asset_repo, portfolio_repo)
    resolution = use_case.resolve_asset(asset.id)

    assert resolution.status is ResolutionStatus.NEEDS_REVIEW
    assert resolution.decided_by is None
    assert "already mapped" in resolution.note
    assert asset_repo.get(asset.id).needs_mapping is True  # never partially applied


def test_resolve_asset_no_candidates_is_needs_review():
    asset_repo, portfolio_repo = FakeAssetRepo(), FakePortfolioRepo()
    asset = _setup_asset(asset_repo, portfolio_repo)
    use_case = _use_case(FakeSecurityMaster({"IE00B4L5Y983": []}), FakeMarketData(), asset_repo, portfolio_repo)

    resolution = use_case.resolve_asset(asset.id)

    assert resolution.status is ResolutionStatus.NEEDS_REVIEW
    assert resolution.candidates == []


def test_resolve_asset_ambiguous_needs_agent_when_agent_enabled():
    asset_repo, portfolio_repo = FakeAssetRepo(), FakePortfolioRepo()
    asset = _setup_asset(asset_repo, portfolio_repo)
    security_master = FakeSecurityMaster(
        {
            "IE00B4L5Y983": [
                FigiListing(ticker="A", exch_code="GY", name="", security_type="ETP", share_class_figi=None),
                FigiListing(ticker="B", exch_code="GF", name="", security_type="ETP", share_class_figi=None),
            ]
        }
    )
    market_data = FakeMarketData(
        search_results={"IE00B4L5Y983": []},
        listings={"A.DE": _listing("A.DE", "EUR"), "B.F": _listing("B.F", "EUR")},
    )
    use_case = _use_case(security_master, market_data, asset_repo, portfolio_repo, agent_enabled=True)

    resolution = use_case.resolve_asset(asset.id)

    assert resolution.status is ResolutionStatus.NEEDS_AGENT
    assert asset_repo.get(asset.id).needs_mapping is True


def test_resolve_asset_supersedes_a_previous_open_resolution():
    resolution_repo = FakeResolutionRepo()
    use_case, asset, _, _ = _winning_setup(resolution_repo=resolution_repo)

    first = use_case.resolve_asset(asset.id)
    second = use_case.resolve_asset(asset.id)

    assert first.id != second.id
    assert resolution_repo.get(first.id).status is ResolutionStatus.SUPERSEDED
    assert resolution_repo.get_open_for_asset(asset.id).id == second.id


# --- add_candidate ---------------------------------------------------------


def test_add_candidate_scores_and_returns_it_with_an_id():
    resolution_repo = FakeResolutionRepo()
    use_case, asset, _, _ = _winning_setup(resolution_repo=resolution_repo)
    resolution = use_case.resolve_asset(asset.id)  # AUTO_ACCEPTED already, but the resolution row still exists

    use_case.market_data.listings["EXTRA.MI"] = _listing("EXTRA.MI", "EUR")
    candidate = use_case.add_candidate(resolution.id, "EXTRA.MI", source="user")

    assert candidate.id is not None
    assert candidate.found_by == {"user"}
    assert candidate.score > 0
    fetched = resolution_repo.get(resolution.id)
    assert "EXTRA.MI" in {c.symbol for c in fetched.candidates}


def test_add_candidate_duplicate_symbol_raises():
    resolution_repo = FakeResolutionRepo()
    use_case, asset, _, _ = _winning_setup(resolution_repo=resolution_repo)
    resolution = use_case.resolve_asset(asset.id)
    existing_symbol = resolution.candidates[0].symbol

    with pytest.raises(DomainError):
        use_case.add_candidate(resolution.id, existing_symbol)


def test_add_candidate_unknown_resolution_raises():
    use_case = _use_case()
    with pytest.raises(ResolutionNotFoundError):
        use_case.add_candidate(999, "EUNL.DE")


# --- accept ------------------------------------------------------------


def _ambiguous_resolution(agent_enabled=False):
    asset_repo, portfolio_repo, resolution_repo = FakeAssetRepo(), FakePortfolioRepo(), FakeResolutionRepo()
    asset = _setup_asset(asset_repo, portfolio_repo)
    security_master = FakeSecurityMaster(
        {
            "IE00B4L5Y983": [
                FigiListing(ticker="A", exch_code="GY", name="", security_type="ETP", share_class_figi=None),
                FigiListing(ticker="B", exch_code="GF", name="", security_type="ETP", share_class_figi=None),
            ]
        }
    )
    market_data = FakeMarketData(
        search_results={"IE00B4L5Y983": []},
        listings={"A.DE": _listing("A.DE", "EUR"), "B.F": _listing("B.F", "EUR")},
    )
    use_case = _use_case(security_master, market_data, asset_repo, portfolio_repo, resolution_repo, agent_enabled)
    resolution = use_case.resolve_asset(asset.id)
    return use_case, asset, asset_repo, resolution


def test_accept_applies_listing_and_marks_resolved_by_user():
    use_case, asset, asset_repo, resolution = _ambiguous_resolution()
    candidate_id = resolution.candidates[0].id

    result = use_case.accept(resolution.id, candidate_id, decided_by="user")

    assert result.status is ResolutionStatus.RESOLVED_BY_USER
    assert result.selected_candidate_id == candidate_id
    assert asset_repo.get(asset.id).needs_mapping is False


def test_accept_by_agent_marks_resolved_by_agent():
    use_case, _asset, _asset_repo, resolution = _ambiguous_resolution(agent_enabled=True)
    candidate_id = resolution.candidates[0].id

    result = use_case.accept(resolution.id, candidate_id, decided_by="agent", note="best match")

    assert result.status is ResolutionStatus.RESOLVED_BY_AGENT
    assert result.note == "best match"


def test_accept_unknown_candidate_raises():
    use_case, _asset, _asset_repo, resolution = _ambiguous_resolution()
    with pytest.raises(DomainError):
        use_case.accept(resolution.id, 999999, decided_by="user")


def test_accept_stale_candidate_raises():
    asset_repo, portfolio_repo, resolution_repo = FakeAssetRepo(), FakePortfolioRepo(), FakeResolutionRepo()
    asset = _setup_asset(asset_repo, portfolio_repo)
    security_master = FakeSecurityMaster(
        {"IE00B4L5Y983": [FigiListing(ticker="OLD", exch_code="GY", name="", security_type="ETP", share_class_figi=None)]}
    )
    market_data = FakeMarketData(
        search_results={"IE00B4L5Y983": []}, listings={"OLD.DE": _listing("OLD.DE", "EUR", days_old=30)}
    )
    use_case = _use_case(security_master, market_data, asset_repo, portfolio_repo, resolution_repo)
    resolution = use_case.resolve_asset(asset.id)
    assert resolution.status is ResolutionStatus.NEEDS_REVIEW  # gated out — nothing to auto-accept

    with pytest.raises(DomainError):
        use_case.accept(resolution.id, resolution.candidates[0].id, decided_by="user")


def test_accept_on_terminal_resolution_raises():
    use_case, asset, _, _ = _winning_setup()
    resolution = use_case.resolve_asset(asset.id)  # already AUTO_ACCEPTED — terminal
    candidate_id = resolution.candidates[0].id

    with pytest.raises(DomainError):
        use_case.accept(resolution.id, candidate_id, decided_by="user")


def test_accept_unknown_resolution_raises():
    use_case = _use_case()
    with pytest.raises(ResolutionNotFoundError):
        use_case.accept(999, 1, decided_by="user")


# --- flag_for_review --------------------------------------------------


def test_flag_for_review_sets_status_and_note():
    use_case, _asset, _asset_repo, resolution = _ambiguous_resolution(agent_enabled=True)

    result = use_case.flag_for_review(resolution.id, "couldn't disambiguate", flagged_by="agent")

    assert result.status is ResolutionStatus.NEEDS_REVIEW
    assert result.note == "couldn't disambiguate"


def test_flag_for_review_on_terminal_resolution_raises():
    use_case, asset, _, _ = _winning_setup()
    resolution = use_case.resolve_asset(asset.id)

    with pytest.raises(DomainError):
        use_case.flag_for_review(resolution.id, "too late")


def test_flag_for_review_unknown_resolution_raises():
    use_case = _use_case()
    with pytest.raises(ResolutionNotFoundError):
        use_case.flag_for_review(999, "note")
