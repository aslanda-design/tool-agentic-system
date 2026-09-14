"""Asset search: local DB first (instant, from confirmed assets), then the
market-data source for anything not tracked yet. A remote hit is resolved
into a local Asset with its YFINANCE identifier immediately (so the frontend
always has an asset_id to link to) — the one deliberate exception to "never
call the market-data port from a request handler", justified because it's
an explicit, infrequent, user-initiated action rather than a background
refresh loop."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.models import AssetClass, IdentifierScheme
from app.ports.market_data import MarketDataPort
from app.ports.repositories import AssetRepo


@dataclass(slots=True)
class SearchResultDTO:
    asset_id: int
    symbol: str
    name: str
    exchange: str | None
    currency: str
    asset_class: str


class SearchAssetsUseCase:
    def __init__(self, asset_repo: AssetRepo, market_data: MarketDataPort) -> None:
        self.asset_repo = asset_repo
        self.market_data = market_data

    def execute(self, query: str, limit: int = 20) -> list[SearchResultDTO]:
        query = query.strip()
        if not query:
            return []

        results: list[SearchResultDTO] = []
        seen_symbols: set[str] = set()

        for asset in self.asset_repo.search_local(query, limit):
            results.append(
                SearchResultDTO(
                    asset_id=asset.id,
                    symbol=asset.symbol,
                    name=asset.name,
                    exchange=asset.exchange,
                    currency=asset.currency,
                    asset_class=asset.asset_class.value,
                )
            )
            seen_symbols.add(asset.symbol.upper())

        if len(results) >= limit:
            return results[:limit]

        for hit in self.market_data.search(query):
            if hit.symbol.upper() in seen_symbols:
                continue
            asset = self.asset_repo.get_by_symbol(hit.symbol)
            if asset is None:
                try:
                    asset_class = AssetClass(hit.asset_class)
                except ValueError:
                    asset_class = AssetClass.OTHER
                asset = self.asset_repo.create(
                    symbol=hit.symbol,
                    name=hit.name,
                    asset_class=asset_class,
                    currency=hit.currency,
                    exchange=hit.exchange,
                )
                self.asset_repo.add_identifier(asset.id, IdentifierScheme.YFINANCE, hit.symbol)
            results.append(
                SearchResultDTO(
                    asset_id=asset.id,
                    symbol=asset.symbol,
                    name=asset.name,
                    exchange=asset.exchange,
                    currency=asset.currency,
                    asset_class=asset.asset_class.value,
                )
            )
            seen_symbols.add(hit.symbol.upper())
            if len(results) >= limit:
                break

        return results
