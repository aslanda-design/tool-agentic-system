"""Tests for domain/listings.py::resolved_display_name — the rule that
decides when a resolved listing's Yahoo name is worth writing over an
asset's existing name (e.g. an ISIN or a broker's internal code). Exercised
end-to-end (asset actually renamed) in test_resolve_security.py and
test_apply_listing.py; this file covers the decision function in isolation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.listings import ListingInfo, resolved_display_name


def _info(name: str, symbol: str = "VUSA.L") -> ListingInfo:
    return ListingInfo(
        symbol=symbol, name=name, currency="GBP", quote_type="ETF",
        last_close=Decimal(100), last_trade_date=date(2026, 9, 1), avg_volume=Decimal(1000),
    )


def test_returns_the_name_when_it_differs_from_the_symbol():
    assert resolved_display_name(_info("Vanguard S&P 500 UCITS ETF"), "VUSA.L") == "Vanguard S&P 500 UCITS ETF"


def test_returns_none_when_yahoo_only_gave_back_the_symbol_itself():
    """yfinance_adapter.get_listing_info falls back to `name=symbol` when
    Yahoo's `.info` lookup fails — that's not a real name."""
    assert resolved_display_name(_info("VUSA.L"), "VUSA.L") is None


def test_returns_none_when_info_is_missing():
    assert resolved_display_name(None, "VUSA.L") is None


def test_returns_none_for_an_empty_name():
    assert resolved_display_name(_info(""), "VUSA.L") is None
