"""Tests for Phase 2 of plans/agentic_asset_mapping.md: ingestion paths
carry through the broker's exchange code (and a better name) so a fresh
needs_mapping asset has enough context for the security resolver."""

from __future__ import annotations

from app.application.asset_resolution import resolve_asset
from tests.fakes import FakeAssetRepo


def test_exchange_is_stored_on_a_freshly_created_asset():
    repo = FakeAssetRepo()
    asset = resolve_asset(repo, "VUSA", "Vanguard S&P 500", "GBP", exchange="LSEETF")
    assert asset.exchange == "LSEETF"
    assert asset.needs_mapping is True


def test_exchange_does_not_overwrite_an_already_resolved_asset():
    """A confirmed listing's MIC (set via AssetRepo.apply_listing) must never
    be clobbered by a later broker sync's raw exchange code."""
    repo = FakeAssetRepo()
    asset = resolve_asset(repo, "VUSA", "Vanguard S&P 500", "GBP", exchange="LSEETF")
    repo.apply_listing(asset.id, "VUSA.L", "GBP", "XLON", None, None)

    resolve_asset(repo, "VUSA", "Vanguard S&P 500", "GBP", exchange="SOME_OTHER_CODE")

    assert repo.get(asset.id).exchange == "XLON"


def test_no_exchange_given_leaves_it_none():
    repo = FakeAssetRepo()
    asset = resolve_asset(repo, "VUSA", "Vanguard S&P 500", "GBP")
    assert asset.exchange is None
