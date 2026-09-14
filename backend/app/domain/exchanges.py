"""Exchange-code lookup tables — the only place in the app that knows how
Bloomberg exchange codes (from OpenFIGI), Yahoo Finance ticker suffixes,
broker exchange codes (from IBKR today) and MIC (ISO 10383) codes relate to
each other. Pure data + pure functions, no I/O — used by the security
resolver (see plans/agentic_asset_mapping.md, Phase 3) to compare listings
found via different providers on a common footing (MIC).

US venues are deliberately collapsed into one pseudo-MIC ("XNYS") — Yahoo
symbols traded in the US carry no suffix at all, so there is no way to tell
NYSE from Nasdaq from a symbol alone, and for pricing/currency purposes it
makes no difference which one a listing is actually on.
"""

from __future__ import annotations

# Bloomberg exchange code (OpenFIGI's `exchCode`) -> Yahoo Finance ticker
# suffix. Empty string means "no suffix" (the US venues).
BLOOMBERG_TO_YAHOO_SUFFIX: dict[str, str] = {
    "US": "",
    "UN": "",
    "UW": "",
    "UQ": "",
    "UA": "",
    "UP": "",
    "LN": ".L",
    "GY": ".DE",
    "GF": ".F",
    "GS": ".SG",
    "GM": ".MU",
    "GD": ".DU",
    "GB": ".BE",
    "GH": ".HM",
    "NA": ".AS",
    "FP": ".PA",
    "BB": ".BR",
    "PL": ".LS",
    "ID": ".IR",
    "IM": ".MI",
    "SM": ".MC",
    "SW": ".SW",
    "SE": ".SW",
    "AV": ".VI",
    "DC": ".CO",
    "SS": ".ST",
    "NO": ".OL",
    "FH": ".HE",
    "CN": ".TO",
    "CT": ".TO",
    "HK": ".HK",
    "JT": ".T",
    "AT": ".AX",
}

# Yahoo Finance ticker suffix -> MIC. This is the pivot used to compare a
# candidate listing's venue against the broker's own exchange, regardless of
# which provider (OpenFIGI/Yahoo) found the listing.
YAHOO_SUFFIX_TO_MIC: dict[str, str] = {
    "": "XNYS",  # US venues collapsed — see module docstring
    ".L": "XLON",
    ".DE": "XETR",
    ".F": "XFRA",
    ".SG": "XSTU",
    ".MU": "XMUN",
    ".DU": "XDUS",
    ".BE": "XBER",
    ".HM": "XHAM",
    ".AS": "XAMS",
    ".PA": "XPAR",
    ".BR": "XBRU",
    ".LS": "XLIS",
    ".IR": "XDUB",
    ".MI": "XMIL",
    ".MC": "XMAD",
    ".SW": "XSWX",
    ".VI": "XWBO",
    ".CO": "XCSE",
    ".ST": "XSTO",
    ".OL": "XOSL",
    ".HE": "XHEL",
    ".TO": "XTSE",
    ".HK": "XHKG",
    ".T": "XTKS",
    ".AX": "XASX",
}

# Broker exchange codes -> MIC. IBKR today (its live API's `primaryExchange`
# and Flex's `listingExchange`); extend per broker as more are added — see
# backend/AGENTS.md "Adding a broker with a live API".
BROKER_CODE_TO_MIC: dict[str, str] = {
    "IBIS": "XETR",
    "IBIS2": "XETR",
    "FWB": "XFRA",
    "FWB2": "XFRA",
    "SWB": "XSTU",
    "LSE": "XLON",
    "LSEETF": "XLON",
    "AEB": "XAMS",
    "SBF": "XPAR",
    "ENEXT.BE": "XBRU",
    "BVME": "XMIL",
    "BVME.ETF": "XMIL",
    "BM": "XMAD",
    "EBS": "XSWX",
    "VSE": "XWBO",
    "NYSE": "XNYS",
    "NASDAQ": "XNYS",
    "ARCA": "XNYS",
    "BATS": "XNYS",
    "ISLAND": "XNYS",
    "TSE": "XTSE",
    "SEHK": "XHKG",
}

# MIC -> ISO country code, for the "same country, different exchange" partial
# match in the rule scorer (domain/listing_scoring.py).
MIC_COUNTRY: dict[str, str] = {
    "XETR": "DE",
    "XFRA": "DE",
    "XSTU": "DE",
    "XMUN": "DE",
    "XDUS": "DE",
    "XBER": "DE",
    "XHAM": "DE",
    "XLON": "GB",
    "XAMS": "NL",
    "XPAR": "FR",
    "XBRU": "BE",
    "XLIS": "PT",
    "XDUB": "IE",
    "XMIL": "IT",
    "XMAD": "ES",
    "XSWX": "CH",
    "XWBO": "AT",
    "XCSE": "DK",
    "XSTO": "SE",
    "XOSL": "NO",
    "XHEL": "FI",
    "XNYS": "US",
    "XTSE": "CA",
    "XHKG": "HK",
    "XTKS": "JP",
    "XASX": "AU",
}


def yahoo_suffix(symbol: str) -> str:
    """The ticker suffix of a Yahoo Finance symbol, e.g. 'VUSA.L' -> '.L',
    'AAPL' -> ''. Only the LAST dot-separated segment is treated as a
    suffix, and only if it's a known one — Yahoo symbols occasionally
    contain dots that aren't exchange suffixes (e.g. share classes), and an
    unrecognized trailing segment is safer treated as "no suffix" than
    silently misread as a venue we don't know."""
    if "." not in symbol:
        return ""
    candidate = "." + symbol.rsplit(".", 1)[1]
    return candidate if candidate in YAHOO_SUFFIX_TO_MIC else ""


def mic_for_yahoo_symbol(symbol: str) -> str | None:
    """The MIC of a Yahoo Finance symbol's venue, or None if the suffix
    isn't one we recognize (mic_for_yahoo_symbol('AAPL') -> 'XNYS')."""
    return YAHOO_SUFFIX_TO_MIC.get(yahoo_suffix(symbol))


def broker_code_to_mic(code: str | None) -> str | None:
    """The MIC for a broker's own exchange code, or None if unknown."""
    if not code:
        return None
    return BROKER_CODE_TO_MIC.get(code.upper())


def yahoo_symbol_from_figi(ticker: str, exch_code: str) -> str | None:
    """Build the Yahoo Finance symbol we'd expect for an OpenFIGI listing
    (ticker + Bloomberg exchange code), or None if the exchange code isn't
    one we can translate. Yahoo replaces '/' in tickers with '-' (e.g.
    Bloomberg 'BRK/B' -> Yahoo 'BRK-B')."""
    suffix = BLOOMBERG_TO_YAHOO_SUFFIX.get(exch_code.upper())
    if suffix is None:
        return None
    return ticker.replace("/", "-") + suffix
