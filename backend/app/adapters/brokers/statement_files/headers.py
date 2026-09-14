"""Accent-/case-insensitive column-header matching, shared by every statement
parser (`generic.py`, `myinvestor.py`) and by `tabular.py` (which uses the
flattened alias vocabulary to find the header row among preamble/title rows
Spanish bank exports commonly prepend).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass


def normalize_header(raw: str) -> str:
    text = unicodedata.normalize("NFKD", raw or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.strip().lower().rstrip(".:*")
    return " ".join(text.split())


@dataclass(slots=True)
class ColumnMap:
    mapped: dict[str, str]  # field -> winning (normalized) header
    unmapped: list[str]  # required fields with no match — caller decides which fields are required
    detected_headers: list[str]  # every normalized header seen in the file, in file order


def build_column_map(headers: list[str], aliases: dict[str, list[str]]) -> ColumnMap:
    """`aliases`: field -> candidate header names in priority order (first
    match wins) — priority matters, e.g. preferring "fecha operacion" over
    "fecha valor" so cost-basis dating isn't silently shifted to a
    settlement date."""
    normalized_headers = {normalize_header(h) for h in headers if h}
    mapped: dict[str, str] = {}
    unmapped: list[str] = []
    for field, candidate_aliases in aliases.items():
        winner = next((a for a in candidate_aliases if a in normalized_headers), None)
        if winner is not None:
            mapped[field] = winner
        else:
            unmapped.append(field)
    return ColumnMap(mapped=mapped, unmapped=unmapped, detected_headers=[normalize_header(h) for h in headers if h])


def alias_tokens(aliases: dict[str, list[str]]) -> set[str]:
    """Every alias across every field, flattened — used by `tabular.py` to
    recognize which row in a file is the header row."""
    tokens: set[str] = set()
    for candidate_aliases in aliases.values():
        tokens.update(candidate_aliases)
    return tokens
