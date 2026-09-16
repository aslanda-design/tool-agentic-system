"""Unit tests for the currency-normalization fix in yfinance_adapter.py
(bugs B1/B2 in plans/agentic_asset_mapping.md): Yahoo reports some listings'
prices in minor units (pence, cents), and previously we just uppercased the
raw currency code without dividing — silently overvaluing the position by
the divisor (100x for a pence-quoted London listing)."""

from __future__ import annotations

import math
from decimal import Decimal

from app.adapters.market_data.yfinance_adapter import _dec, _normalize_currency


def test_gbp_pence_normalizes_to_gbp_with_divisor():
    currency, divisor = _normalize_currency("GBp")
    assert currency == "GBP"
    assert divisor == Decimal(100)


def test_gbx_pence_normalizes_to_gbp_with_divisor():
    currency, divisor = _normalize_currency("GBX")
    assert currency == "GBP"
    assert divisor == Decimal(100)


def test_zac_and_ila_normalize_with_divisor():
    assert _normalize_currency("ZAc") == ("ZAR", Decimal(100))
    assert _normalize_currency("ILA") == ("ILS", Decimal(100))


def test_ordinary_currency_is_uppercased_with_no_divisor():
    currency, divisor = _normalize_currency("eur")
    assert currency == "EUR"
    assert divisor == Decimal(1)


def test_missing_currency_returns_none_not_a_fallback():
    currency, divisor = _normalize_currency(None)
    assert currency is None
    assert divisor == Decimal(1)


def test_dec_divides_by_divisor():
    assert _dec("9241", Decimal(100)) == Decimal("92.41")
    assert _dec("107.02") == Decimal("107.02")
    assert _dec(None, Decimal(100)) is None


def test_dec_treats_nan_as_missing():
    # yfinance/pandas represent a still-open trading day's incomplete bar as
    # NaN rather than omitting the row. Decimal(str(nan)) would otherwise
    # produce Decimal('NaN'), which later breaks JSON encoding of the whole
    # /assets/{id}/history response (json.dumps rejects NaN) instead of just
    # that one bar being missing.
    assert _dec(math.nan) is None
    assert _dec(math.nan, Decimal(100)) is None
