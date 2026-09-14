"""Tests for the `security` MCP server (Phase 5 of
plans/agentic_asset_mapping.md — backend/ai/mcp_servers/security). Two
layers:

- Each tool function is called directly against fakes (monkeypatching the
  `app.container` factory the tool module imported), matching how
  test_resolve_security.py tests the use case itself — fast, network-free.
- One protocol-level test calls the registered MCPServer directly
  (`server.mcp.list_tools()` / `.call_tool()`) to prove the tools are wired
  up correctly, not just importable as plain functions.

Tools that write also cover their error path ({"error": "..."} instead of
a raised exception — see backend/ai/AGENTS.md rule 2).
"""

from __future__ import annotations

import asyncio

from ai.mcp_servers.security import server
from ai.mcp_servers.security.tools import (
    add_candidate as add_candidate_tool,
)
from ai.mcp_servers.security.tools import (
    flag_for_review as flag_for_review_tool,
)
from ai.mcp_servers.security.tools import (
    get_resolution as get_resolution_tool,
)
from ai.mcp_servers.security.tools import (
    list_pending_resolutions as list_pending_resolutions_tool,
)
from ai.mcp_servers.security.tools import (
    lookup_isin as lookup_isin_tool,
)
from ai.mcp_servers.security.tools import (
    resolve_isin as resolve_isin_tool,
)
from ai.mcp_servers.security.tools import (
    save_security_mapping as save_security_mapping_tool,
)
from ai.mcp_servers.security.tools import (
    search_listings as search_listings_tool,
)
from ai.mcp_servers.security.tools import (
    validate_listing as validate_listing_tool,
)
from app.domain.listings import Candidate, FigiListing, ResolutionStatus
from app.ports.market_data import AssetSearchResult
from tests.fakes import FakeResolutionRepo
from tests.test_resolve_security import (
    FakeMarketData,
    FakeSecurityMaster,
    _ctx,
    _listing,
    _use_case,
)


def _seed_resolution(repo: FakeResolutionRepo, *, with_priceable_candidate: bool = True) -> int:
    candidate = Candidate(
        symbol="EUNL.DE",
        found_by={"openfigi"},
        info=_listing("EUNL.DE", "EUR") if with_priceable_candidate else None,
        mic="XETR",
        asset_class="ETF",
        features={"has_recent_price": with_priceable_candidate},
        score=85,
    )
    resolution_id = repo.create(_ctx(), ResolutionStatus.NEEDS_REVIEW, None, "", "rules-v1", [candidate])
    return resolution_id, candidate.id


# --- list_pending_resolutions / get_resolution (app.container.resolution_repo) ---


def test_list_pending_resolutions_defaults_to_review_and_agent_statuses(monkeypatch):
    repo = FakeResolutionRepo()
    repo.create(_ctx(asset_id=1), ResolutionStatus.NEEDS_REVIEW, None, "", "rules-v1", [])
    repo.create(_ctx(asset_id=2), ResolutionStatus.AUTO_ACCEPTED, "rules", "", "rules-v1", [])
    monkeypatch.setattr(list_pending_resolutions_tool, "resolution_repo", lambda db: repo)

    result = list_pending_resolutions_tool.list_pending_resolutions()

    assert len(result) == 1
    assert result[0]["status"] == "NEEDS_REVIEW"


def test_get_resolution_returns_none_for_missing_id(monkeypatch):
    monkeypatch.setattr(get_resolution_tool, "resolution_repo", lambda db: FakeResolutionRepo())
    assert get_resolution_tool.get_resolution(999) is None


def test_get_resolution_serializes_candidates_as_json_safe(monkeypatch):
    repo = FakeResolutionRepo()
    resolution_id, _ = _seed_resolution(repo)
    monkeypatch.setattr(get_resolution_tool, "resolution_repo", lambda db: repo)

    result = get_resolution_tool.get_resolution(resolution_id)

    assert result["id"] == resolution_id
    candidate = result["candidates"][0]
    assert candidate["found_by"] == ["openfigi"]  # set -> sorted list
    assert candidate["info"]["last_close"] == 100.0  # Decimal -> float
    assert candidate["info"]["last_trade_date"] == "2026-09-14"  # date -> ISO string


# --- resolve_isin / lookup_isin / search_listings / validate_listing (use case) ---


def test_resolve_isin_previews_without_persisting(monkeypatch):
    security_master = FakeSecurityMaster(
        {"IE00B4L5Y983": [FigiListing(ticker="EUNL", exch_code="GY", name="X", security_type="ETP", share_class_figi=None)]}
    )
    market_data = FakeMarketData(listings={"EUNL.DE": _listing("EUNL.DE", "EUR")})
    use_case = _use_case(security_master, market_data)
    monkeypatch.setattr(resolve_isin_tool, "build_resolve_security_use_case", lambda db: use_case)

    result = resolve_isin_tool.resolve_isin(isin="IE00B4L5Y983", currency="EUR")

    assert len(result) == 1
    assert result[0]["symbol"] == "EUNL.DE"
    assert result[0]["score"] > 0


def test_lookup_isin_returns_none_when_provider_unavailable(monkeypatch):
    use_case = _use_case(security_master=FakeSecurityMaster({"IE00XXX": None}))
    monkeypatch.setattr(lookup_isin_tool, "build_resolve_security_use_case", lambda db: use_case)

    assert lookup_isin_tool.lookup_isin("IE00XXX") is None


def test_lookup_isin_returns_empty_list_when_isin_confirmed_unknown(monkeypatch):
    use_case = _use_case(security_master=FakeSecurityMaster({"IE00XXX": []}))
    monkeypatch.setattr(lookup_isin_tool, "build_resolve_security_use_case", lambda db: use_case)

    assert lookup_isin_tool.lookup_isin("IE00XXX") == []


def test_search_listings_wraps_market_data_search(monkeypatch):
    use_case = _use_case(
        market_data=FakeMarketData(search_results={"apple": [AssetSearchResult(symbol="AAPL", name="Apple", exchange=None, asset_class="EQUITY", currency="USD")]})
    )
    monkeypatch.setattr(search_listings_tool, "build_resolve_security_use_case", lambda db: use_case)

    result = search_listings_tool.search_listings("apple")

    assert result == [{"symbol": "AAPL", "name": "Apple", "exchange": None, "asset_class": "EQUITY", "currency": "USD"}]


def test_validate_listing_returns_none_for_unknown_symbol(monkeypatch):
    use_case = _use_case(market_data=FakeMarketData())
    monkeypatch.setattr(validate_listing_tool, "build_resolve_security_use_case", lambda db: use_case)

    assert validate_listing_tool.validate_listing("NOPE") is None


def test_validate_listing_returns_jsonable_listing_info(monkeypatch):
    use_case = _use_case(market_data=FakeMarketData(listings={"AAPL": _listing("AAPL", "USD")}))
    monkeypatch.setattr(validate_listing_tool, "build_resolve_security_use_case", lambda db: use_case)

    result = validate_listing_tool.validate_listing("AAPL")

    assert result["symbol"] == "AAPL"
    assert result["currency"] == "USD"
    assert isinstance(result["last_close"], float)


# --- add_candidate / save_security_mapping / flag_for_review (writes) ---


def test_add_candidate_returns_error_dict_for_missing_resolution(monkeypatch):
    use_case = _use_case(market_data=FakeMarketData(listings={"AAPL": _listing("AAPL", "USD")}))
    monkeypatch.setattr(add_candidate_tool, "build_resolve_security_use_case", lambda db: use_case)

    result = add_candidate_tool.add_candidate(resolution_id=999, symbol="AAPL")

    assert "error" in result


def test_add_candidate_scores_and_persists_new_candidate(monkeypatch):
    repo = FakeResolutionRepo()
    resolution_id, _ = _seed_resolution(repo)
    use_case = _use_case(resolution_repo=repo, market_data=FakeMarketData(listings={"AAPL": _listing("AAPL", "USD")}))
    monkeypatch.setattr(add_candidate_tool, "build_resolve_security_use_case", lambda db: use_case)

    result = add_candidate_tool.add_candidate(resolution_id=resolution_id, symbol="AAPL")

    assert result["symbol"] == "AAPL"
    assert result["id"] is not None
    assert len(repo.get(resolution_id).candidates) == 2


def test_save_security_mapping_returns_error_dict_on_domain_error(monkeypatch):
    repo = FakeResolutionRepo()
    resolution_id, _ = _seed_resolution(repo, with_priceable_candidate=False)  # no recent price -> accept() rejects
    _, candidate_id = resolution_id, repo.get(resolution_id).candidates[0].id
    use_case = _use_case(resolution_repo=repo)
    monkeypatch.setattr(save_security_mapping_tool, "build_resolve_security_use_case", lambda db: use_case)

    result = save_security_mapping_tool.save_security_mapping(resolution_id, candidate_id, "test reason")

    assert "error" in result
    assert repo.get(resolution_id).status is ResolutionStatus.NEEDS_REVIEW  # unchanged


def test_save_security_mapping_applies_listing_and_resolves(monkeypatch):
    from app.domain.models import AssetClass
    from tests.fakes import FakeAssetRepo, FakePortfolioRepo

    repo = FakeResolutionRepo()
    resolution_id, candidate_id = _seed_resolution(repo)
    asset_repo = FakeAssetRepo()
    asset_repo.create("IE00B4L5Y983", "iShares Core MSCI World", AssetClass.ETF, "USD", isin="IE00B4L5Y983")  # id=1, matches _ctx()'s asset_id
    use_case = _use_case(resolution_repo=repo, asset_repo=asset_repo, portfolio_repo=FakePortfolioRepo())
    monkeypatch.setattr(save_security_mapping_tool, "build_resolve_security_use_case", lambda db: use_case)

    result = save_security_mapping_tool.save_security_mapping(resolution_id, candidate_id, "confident match")

    assert result["status"] == "RESOLVED_BY_AGENT"
    assert result["decided_by"] == "agent"
    assert result["note"] == "confident match"


def test_flag_for_review_returns_error_dict_for_missing_resolution(monkeypatch):
    use_case = _use_case()
    monkeypatch.setattr(flag_for_review_tool, "build_resolve_security_use_case", lambda db: use_case)

    result = flag_for_review_tool.flag_for_review(999, "ambiguous")

    assert "error" in result


def test_flag_for_review_sets_note_and_decided_by(monkeypatch):
    repo = FakeResolutionRepo()
    resolution_id, _ = _seed_resolution(repo)
    use_case = _use_case(resolution_repo=repo)
    monkeypatch.setattr(flag_for_review_tool, "build_resolve_security_use_case", lambda db: use_case)

    result = flag_for_review_tool.flag_for_review(resolution_id, "no clear winner")

    assert result["status"] == "NEEDS_REVIEW"
    assert result["decided_by"] == "agent"
    assert result["note"] == "no clear winner"


# --- protocol-level: the registered MCPServer itself ---


def test_server_registers_all_nine_tools():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == {
        "list_pending_resolutions",
        "get_resolution",
        "resolve_isin",
        "lookup_isin",
        "search_listings",
        "validate_listing",
        "add_candidate",
        "save_security_mapping",
        "flag_for_review",
    }
    # The SDK builds each tool's JSON Schema from the function's type hints —
    # confirm that actually happened rather than every tool getting an empty schema.
    by_name = {t.name: t for t in tools}
    assert "resolution_id" in by_name["get_resolution"].input_schema["properties"]


def test_server_call_tool_round_trips_through_the_registered_function(monkeypatch):
    use_case = _use_case(market_data=FakeMarketData(listings={"AAPL": _listing("AAPL", "USD")}))
    monkeypatch.setattr(validate_listing_tool, "build_resolve_security_use_case", lambda db: use_case)

    result = asyncio.run(server.mcp.call_tool("validate_listing", {"symbol": "AAPL"}))

    assert result.is_error is False
    # The SDK wraps a non-object return value's JSON under "result" in structured_content.
    assert result.structured_content["result"]["symbol"] == "AAPL"
