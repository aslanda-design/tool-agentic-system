"""Outbound port for a security master lookup — mapping an ISIN to every
exchange listing a provider knows about, independent of any one market-data
source's own search. Implementation: adapters/security_master/openfigi_adapter.py
(OpenFIGI's free /v3/mapping endpoint). Used by the security resolver
(application/resolve_security.py) to generate Yahoo Finance candidates that
yfinance's own search wouldn't find on its own — see
plans/agentic_asset_mapping.md Phase 3 for why (yfinance has poor coverage
of European ETF listings, but a Yahoo *symbol* built from OpenFIGI's own
exchange code + ticker usually still resolves)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.listings import FigiListing


class SecurityMasterPort(ABC):
    @abstractmethod
    def map_isin(self, isin: str) -> list[FigiListing] | None:
        """Every exchange listing known for this ISIN. Returns `[]` when the
        provider explicitly says the ISIN is unknown, and `None` when the
        provider itself is unavailable (network error, rate limit) — the
        caller must treat `None` as "try again later, continue with other
        sources for now", never as "this ISIN has no listings". Never raises."""
