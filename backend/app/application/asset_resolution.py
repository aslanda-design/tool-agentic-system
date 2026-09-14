"""Shared asset-identity resolution, used by every ingestion path (live
broker sync, Flex statement import, CSV import) so they agree on one Asset
per real-world instrument. Trust order: IBKR conid > ISIN > symbol. Anything
resolved only by symbol is flagged `needs_mapping` for the user to confirm
a market-data ticker in the UI."""

from __future__ import annotations

from app.domain.models import Asset, AssetClass, IdentifierScheme
from app.ports.repositories import AssetRepo


def resolve_asset(
    asset_repo: AssetRepo,
    symbol: str,
    name: str,
    currency: str,
    ibkr_conid: str | None = None,
    isin: str | None = None,
) -> Asset:
    if ibkr_conid:
        asset = asset_repo.find_by_identifier(IdentifierScheme.IBKR_CONID, ibkr_conid)
        if asset:
            return asset
    if isin:
        asset = asset_repo.find_by_identifier(IdentifierScheme.ISIN, isin)
        if asset:
            return asset

    asset = asset_repo.get_by_symbol(symbol)
    if asset is None:
        asset = asset_repo.create(
            symbol=symbol,
            name=name or symbol,
            asset_class=AssetClass.EQUITY,
            currency=currency,
            isin=isin,
            needs_mapping=True,
        )
    elif isin and not asset.isin:
        # Learned the ISIN later (e.g. a Flex trade carries it even though the
        # asset was first created by a live sync, which doesn't) — worth
        # keeping since it powers ticker-mapping suggestions.
        asset_repo.set_isin(asset.id, isin)

    if ibkr_conid:
        asset_repo.add_identifier(asset.id, IdentifierScheme.IBKR_CONID, ibkr_conid)
    if isin:
        asset_repo.add_identifier(asset.id, IdentifierScheme.ISIN, isin)
    return asset
