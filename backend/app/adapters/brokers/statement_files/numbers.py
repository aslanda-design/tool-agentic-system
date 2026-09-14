"""Spanish-locale number/date normalization for bank statement exports.

Never returns a default on unparseable input — callers turn `None` into a
row error, never a silent "0" (a "0" price/quantity that came from a parse
failure, not the broker, has bitten this app before via `.replace(",", ".")`
mangling "1.234,56" into "1.234.56").
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_WHITESPACE = re.compile(r"[\s ]+")
_CURRENCY_JUNK = re.compile(r"[€$%]|EUR|USD|GBP", re.IGNORECASE)
_PARENS = re.compile(r"^\((.*)\)$")
_CURRENCY_CODE = re.compile(r"\b(EUR|USD|GBP|CHF|JPY)\b", re.IGNORECASE)


def extract_currency(raw: str | None) -> str | None:
    """Pull a 3-letter currency code out of a value like "500 EUR" or
    "1.234,56 USD" — used when a file has no dedicated currency column but
    embeds it in the amount cell (MyInvestor's buy-order export does this)."""
    if not raw:
        return None
    match = _CURRENCY_CODE.search(raw)
    return match.group(1).upper() if match else None


def normalize_decimal(raw: str | None) -> str | None:
    """Parse a Spanish- or English-formatted number into a canonical decimal
    string (dot separator, no thousands separator, no currency symbol).
    Returns None if `raw` isn't a recognizable number — never "0"."""
    if raw is None:
        return None
    text = _CURRENCY_JUNK.sub("", raw)
    text = _WHITESPACE.sub("", text).strip()
    if not text:
        return None

    negative = False
    paren_match = _PARENS.match(text)
    if paren_match:
        negative = True
        text = paren_match.group(1)
    if text.startswith("-"):
        negative = True
        text = text[1:]
    elif text.endswith("-"):
        negative = True
        text = text[:-1]

    if not text or not re.fullmatch(r"[0-9.,]+", text):
        return None

    has_dot = "." in text
    has_comma = "," in text
    if has_dot and has_comma:
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        text = text.replace(thousands_sep, "")
        text = text.replace(decimal_sep, ".")
    elif has_comma:
        # Spanish convention: comma is always the decimal separator when alone.
        text = text.replace(",", ".")
    # else: only dots (or none) — treat as already-canonical. A lone
    # "1.234" is genuinely ambiguous (thousands vs. decimal); we accept it
    # as a decimal value as-is rather than guess, since Decimal() will parse
    # it either way and this only matters for values that happen to look
    # like a thousands-grouped integer.

    try:
        value = Decimal(text)
    except InvalidOperation:
        return None

    if negative:
        value = -value
    return str(value)


_DATE_PATTERNS = (
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})"), lambda m: (m.group(1), m.group(2), m.group(3))),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), lambda m: (m.group(3), m.group(2), m.group(1))),
    (re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$"), lambda m: (m.group(3), m.group(2), m.group(1))),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2})$"), lambda m: (_pivot_year(m.group(3)), m.group(2), m.group(1))),
)


def _pivot_year(two_digit: str) -> str:
    year = int(two_digit)
    return str(2000 + year) if year < 70 else str(1900 + year)


def normalize_date(raw: str | None) -> str | None:
    """Parse DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD, or DD/MM/YY into an ISO
    YYYY-MM-DD string. Returns None if unparseable — never a stand-in date."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    for pattern, extract in _DATE_PATTERNS:
        match = pattern.match(text)
        if match:
            year, month, day = extract(match)
            try:
                y, mo, d = int(year), int(month), int(day)
                if not (1 <= mo <= 12 and 1 <= d <= 31):
                    return None
            except ValueError:
                return None
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None
