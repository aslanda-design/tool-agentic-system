"""Deterministic dedup identity for imported statement rows that have no
native broker reference number, so re-importing an overlapping export
inserts only genuinely new transactions.

`add_transactions` (adapters/persistence/repositories.py) already dedups on
the unique `(account_id, external_id)` constraint — but only when
`external_id` is set. This module fills that gap for CSV/XLSX imports by
computing a stable synthetic id from the row's own content, so no schema
change is needed.

Frozen contract: this is the "fp1" fingerprint recipe. Changing which fields
feed the hash, or how they're normalized, requires bumping the prefix to
"fp2" — a v1-fingerprinted row and a v2-fingerprinted row of the same
transaction will look distinct and the overlap will re-insert once. Treat
that as a rare, explicit migration, not a casual refactor.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING

from app.adapters.brokers.statement_files.headers import normalize_header

if TYPE_CHECKING:
    # Deferred to avoid a runtime import cycle: import_transactions imports
    # assign_external_ids from this module. Safe because `from __future__
    # import annotations` (below) means these type hints are never evaluated.
    from app.application.import_transactions import ParsedRow

FINGERPRINT_VERSION = "fp1"
_QUANTITY_EXP = Decimal("1E-10")  # matches orm.py QUANTITY = Numeric(28, 10)
_PRICE_EXP = Decimal("1E-8")  # matches orm.py PRICE = Numeric(20, 8)


def _quantize(raw: str, exponent: Decimal) -> str:
    try:
        return str(Decimal(raw).quantize(exponent, rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return raw


def _instrument_key(row: ParsedRow) -> tuple[str, bool]:
    """(key, used_name_fallback). ISIN first, then symbol, then product name
    — name is the least stable identity (renames, truncation), so callers
    warn when it's the only thing they had."""
    if row.isin:
        return normalize_header(row.isin).upper(), False
    if row.symbol:
        return normalize_header(row.symbol).upper(), False
    return normalize_header(row.name or "").upper(), True


def _fingerprint_key(row: ParsedRow) -> tuple[str, bool]:
    instrument, used_name = _instrument_key(row)
    trade_date = (row.executed_at or "")[:10]
    canonical_type = (row.type or "").strip().upper()
    quantity = _quantize(row.quantity, _QUANTITY_EXP)
    price = _quantize(row.price, _PRICE_EXP)
    joined = f"{trade_date}\x1f{instrument}\x1f{canonical_type}\x1f{quantity}\x1f{price}"
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]
    return digest, used_name


def assign_external_ids(rows: list[ParsedRow]) -> None:
    """Mutates rows in place: fills `external_id` for every error-free row
    that doesn't already have a native broker reference.

    Occurrence index (`#n`) is assigned by counting membership within each
    content-fingerprint group, not by file position — so it stays stable
    across re-imports even if rows are reordered or a later-settling
    transaction appears in a subsequent export. Two truly identical
    operations (same fund/day/units/price) get distinct ids (`#0`, `#1`, ...)
    so neither is lost; a genuinely new instance of that same combination in
    a later, wider export becomes the group's `#2` — the first two match
    what's already stored, and only the new one inserts.
    """
    groups: dict[str, list[ParsedRow]] = defaultdict(list)
    for row in rows:
        if row.errors or row.external_id:
            continue
        key, used_name_fallback = _fingerprint_key(row)
        if used_name_fallback:
            row.warnings.append(
                "identity based on product name (no ISIN/symbol) — a product rename in a future "
                "export would cause this row to re-import as new"
            )
        groups[key].append(row)

    for key, group_rows in groups.items():
        for index, row in enumerate(group_rows):
            row.external_id = f"{FINGERPRINT_VERSION}:{key}#{index}"
