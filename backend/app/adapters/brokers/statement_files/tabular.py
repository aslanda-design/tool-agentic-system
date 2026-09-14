"""Turn CSV or XLSX bytes into a broker-agnostic `Sheet` (rows as
normalized-header -> string-value dicts). Handles what real bank exports
throw at us: unknown encoding, unknown delimiter, a title/preamble before
the real header row, and trailing totals rows — none of it broker-specific,
which is why this lives apart from `myinvestor.py`.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from openpyxl import load_workbook

from app.domain.errors import UnsupportedStatementFileError

from .headers import normalize_header
from .sniff import StatementFormat

_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
_DELIMITER_CANDIDATES = (";", ",", "\t", "|")
_MAX_PREAMBLE_SCAN = 15
_MIN_HEADER_MATCHES = 2


@dataclass(slots=True)
class Sheet:
    rows: list[dict[str, str]]
    detected_headers: list[str]
    provenance: str
    skipped_rows: int = 0


def read_sheet(file_bytes: bytes, fmt: StatementFormat, header_hints: set[str]) -> Sheet:
    if fmt is StatementFormat.XLSX:
        return _read_xlsx(file_bytes, header_hints)
    return _read_csv(file_bytes, header_hints)


def _decode(file_bytes: bytes) -> tuple[str, str]:
    for encoding in _ENCODINGS[:-1]:
        try:
            return file_bytes.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("latin-1", errors="replace"), "latin-1"  # never fails


def _sniff_delimiter(sample: str) -> str:
    if not sample:
        return ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="".join(_DELIMITER_CANDIDATES))
        return dialect.delimiter
    except csv.Error:
        pass
    counts = {d: sample.count(d) for d in _DELIMITER_CANDIDATES}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def _find_header_row(rows: list[list[str]], header_hints: set[str]) -> int:
    for i, row in enumerate(rows[:_MAX_PREAMBLE_SCAN]):
        normalized = {normalize_header(cell) for cell in row if cell and cell.strip()}
        if len(normalized & header_hints) >= _MIN_HEADER_MATCHES:
            return i
    return -1


def _rows_to_records(non_empty_rows: list[list[str]], header_idx: int) -> tuple[list[dict[str, str]], list[str], int]:
    headers = [normalize_header(c) for c in non_empty_rows[header_idx]]
    rows: list[dict[str, str]] = []
    skipped = 0
    for row in non_empty_rows[header_idx + 1 :]:
        if not any(row):
            continue
        cells = list(row) + [""] * (len(headers) - len(row))
        record = {h: cells[i] for i, h in enumerate(headers) if h}
        if not any(v.strip() for v in record.values()):
            skipped += 1
            continue
        rows.append(record)
    return rows, [h for h in headers if h], skipped


def _read_csv(file_bytes: bytes, header_hints: set[str]) -> Sheet:
    text, encoding = _decode(file_bytes)
    non_empty_lines = [ln for ln in text.splitlines() if ln.strip()]
    sample = "\n".join(non_empty_lines[:20])
    delimiter = _sniff_delimiter(sample)

    raw_rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    stripped_rows = [[(cell or "").strip() for cell in row] for row in raw_rows]
    non_empty_rows = [row for row in stripped_rows if any(row)]

    header_idx = _find_header_row(non_empty_rows, header_hints)
    if header_idx == -1:
        preview = "\n".join(delimiter.join(r) for r in non_empty_rows[:5])
        raise UnsupportedStatementFileError(
            "Could not find a recognizable header row in this CSV. First rows seen:\n" + (preview or "(empty file)")
        )

    rows, headers, skipped = _rows_to_records(non_empty_rows, header_idx)
    return Sheet(
        rows=rows,
        detected_headers=headers,
        provenance=f"CSV, {encoding}, '{delimiter}'-delimited",
        skipped_rows=skipped,
    )


def _cell_to_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        return str(Decimal(str(value)))
    if isinstance(value, Decimal):
        return str(value)
    return str(value).strip()


def _read_xlsx(file_bytes: bytes, header_hints: set[str]) -> Sheet:
    try:
        workbook = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    except Exception as exc:
        raise UnsupportedStatementFileError(
            "Could not read this file as an Excel (.xlsx) workbook. If it's a legacy .xls file, "
            "re-save it as 'Excel Workbook (.xlsx)' or 'CSV UTF-8' and upload again."
        ) from exc

    all_rows: list[dict[str, str]] = []
    detected_headers: list[str] = []
    sheet_names: list[str] = []
    skipped = 0

    try:
        for worksheet in workbook.worksheets:
            raw_rows = [[_cell_to_str(c) for c in row] for row in worksheet.iter_rows(values_only=True)]
            non_empty_rows = [row for row in raw_rows if any(v.strip() for v in row)]
            if not non_empty_rows:
                continue
            header_idx = _find_header_row(non_empty_rows, header_hints)
            if header_idx == -1:
                continue
            rows, headers, sheet_skipped = _rows_to_records(non_empty_rows, header_idx)
            all_rows.extend(rows)
            skipped += sheet_skipped
            if not detected_headers:
                detected_headers = headers
            sheet_names.append(worksheet.title)
    finally:
        workbook.close()

    if not sheet_names:
        raise UnsupportedStatementFileError(
            "This Excel file doesn't contain a recognizable statement table on any sheet."
        )

    return Sheet(
        rows=all_rows,
        detected_headers=detected_headers,
        provenance=f"XLSX, sheet(s): {', '.join(sheet_names)}",
        skipped_rows=skipped,
    )
