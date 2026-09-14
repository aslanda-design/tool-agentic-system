"""Tests for the exchange-code lookup functions in domain/exchanges.py."""

from __future__ import annotations

from app.domain.exchanges import (
    broker_code_to_mic,
    mic_for_yahoo_symbol,
    yahoo_suffix,
    yahoo_symbol_from_figi,
)


def test_yahoo_suffix_known_suffix():
    assert yahoo_suffix("VUSA.L") == ".L"
    assert yahoo_suffix("EUNL.DE") == ".DE"


def test_yahoo_suffix_us_symbol_has_no_suffix():
    assert yahoo_suffix("AAPL") == ""


def test_yahoo_suffix_unrecognized_trailing_segment_is_not_a_suffix():
    # A dot that isn't a known exchange suffix (e.g. a share-class dot) must
    # not be misread as a venue we don't actually know.
    assert yahoo_suffix("BRK.B") == ""


def test_mic_for_yahoo_symbol():
    assert mic_for_yahoo_symbol("VUSA.L") == "XLON"
    assert mic_for_yahoo_symbol("EUNL.DE") == "XETR"
    assert mic_for_yahoo_symbol("AAPL") == "XNYS"
    # An unrecognized trailing segment (e.g. BRK.B's share-class dot) is
    # treated as "no suffix" by yahoo_suffix — which resolves to the US
    # pseudo-MIC, the correct read for the overwhelmingly common case of an
    # unadorned US symbol.
    assert mic_for_yahoo_symbol("BRK.B") == "XNYS"


def test_broker_code_to_mic():
    assert broker_code_to_mic("IBIS2") == "XETR"
    assert broker_code_to_mic("lseetf") == "XLON"  # case-insensitive
    assert broker_code_to_mic(None) is None
    assert broker_code_to_mic("") is None
    assert broker_code_to_mic("SOME_UNKNOWN_CODE") is None


def test_yahoo_symbol_from_figi_known_exchange():
    assert yahoo_symbol_from_figi("EUNL", "GY") == "EUNL.DE"
    assert yahoo_symbol_from_figi("VUSA", "LN") == "VUSA.L"
    assert yahoo_symbol_from_figi("AAPL", "US") == "AAPL"


def test_yahoo_symbol_from_figi_unknown_exchange_returns_none():
    assert yahoo_symbol_from_figi("XYZ", "ZZ") is None


def test_yahoo_symbol_from_figi_replaces_slash_with_dash():
    assert yahoo_symbol_from_figi("BRK/B", "US") == "BRK-B"
