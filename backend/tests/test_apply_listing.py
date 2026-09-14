"""Tests for AssetRepo.apply_listing, the single write path for a YFINANCE
mapping (see plans/agentic_asset_mapping.md Phase 1, bugs B3/B4). Runs
against FakeAssetRepo — the same duck-typed in-memory repo the import tests
use, so these stay fast and DB-free (see tests/fakes.py's docstring)."""

from __future__ import annotations

import pytest

from app.domain.errors import AssetConflictError
from app.domain.models import AssetClass, IdentifierScheme
from tests.fakes import FakeAssetRepo


def _make_unmapped_asset(repo: FakeAssetRepo, symbol="VUSA", currency="EUR"):
    return repo.create(
        symbol=symbol, name=symbol, asset_class=AssetClass.EQUITY, currency=currency, needs_mapping=True
    )


def test_apply_listing_sets_currency_and_clears_needs_mapping():
    """Bug B3: mapping used to leave assets.currency untouched, even though
    build_snapshots.py prices the holding using that field — mapping a
    EUR-tracked asset to a GBP listing silently mispriced it."""
    repo = FakeAssetRepo()
    asset = _make_unmapped_asset(repo, currency="EUR")

    repo.apply_listing(asset.id, "VUSA.L", "GBP", "XLON", AssetClass.ETF, "BBG000XYZ123")

    updated = repo.get(asset.id)
    assert updated.currency == "GBP"
    assert updated.exchange == "XLON"
    assert updated.needs_mapping is False
    assert updated.asset_class == AssetClass.ETF
    assert updated.share_class_figi == "BBG000XYZ123"
    assert repo.find_by_identifier(IdentifierScheme.YFINANCE, "VUSA.L").id == asset.id


def test_remap_replaces_the_listing_not_adds_a_second_one():
    """Bug B4: add_identifier only ever added — remapping (agent retry, user
    correction) left the asset with two YFINANCE identifiers, so
    list_assets_with_scheme returned it twice and it got double-refreshed."""
    repo = FakeAssetRepo()
    asset = _make_unmapped_asset(repo)
    repo.apply_listing(asset.id, "VUSA.DE", "EUR", "XETR", None, None)
    repo.apply_listing(asset.id, "VUSA.L", "GBP", "XLON", None, None)

    mappings = [(a.id, value) for a, value in repo.list_assets_with_scheme(IdentifierScheme.YFINANCE) if a.id == asset.id]
    assert mappings == [(asset.id, "VUSA.L")]
    assert repo.find_by_identifier(IdentifierScheme.YFINANCE, "VUSA.DE") is None
    assert repo.get(asset.id).currency == "GBP"


def test_apply_listing_to_symbol_already_owned_by_another_asset_raises_conflict():
    repo = FakeAssetRepo()
    first = _make_unmapped_asset(repo, symbol="AAA")
    second = _make_unmapped_asset(repo, symbol="BBB")
    repo.apply_listing(first.id, "SHARED.L", "GBP", "XLON", None, None)

    with pytest.raises(AssetConflictError):
        repo.apply_listing(second.id, "SHARED.L", "GBP", "XLON", None, None)

    # the conflict must not have partially applied to `second`
    assert repo.get(second.id).needs_mapping is True


def test_reapplying_the_same_symbol_is_a_harmless_no_op_on_the_identifier():
    repo = FakeAssetRepo()
    asset = _make_unmapped_asset(repo)
    repo.apply_listing(asset.id, "VUSA.L", "GBP", "XLON", None, None)
    repo.apply_listing(asset.id, "VUSA.L", "GBP", "XLON", None, None)

    mappings = [v for a, v in repo.list_assets_with_scheme(IdentifierScheme.YFINANCE) if a.id == asset.id]
    assert mappings == ["VUSA.L"]


def test_apply_listing_with_a_name_renames_the_asset():
    repo = FakeAssetRepo()
    asset = _make_unmapped_asset(repo)

    repo.apply_listing(asset.id, "VUSA.L", "GBP", "XLON", None, None, name="Vanguard S&P 500 UCITS ETF")

    assert repo.get(asset.id).name == "Vanguard S&P 500 UCITS ETF"


def test_apply_listing_without_a_name_leaves_the_existing_one_alone():
    repo = FakeAssetRepo()
    asset = _make_unmapped_asset(repo)  # name == symbol == "VUSA"

    repo.apply_listing(asset.id, "VUSA.L", "GBP", "XLON", None, None)

    assert repo.get(asset.id).name == "VUSA"
