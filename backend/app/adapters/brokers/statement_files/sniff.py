"""Detect a statement file's real format from its bytes, never its filename
or declared content-type. Spanish bank exports routinely lie about both: a
"CSV" download can be windows-1252, and a "MyInvestor .xls" is, in practice,
either a legacy BIFF workbook or an HTML table with an .xls extension —
openpyxl can read neither. Every case that isn't CSV/XLSX raises
`UnsupportedStatementFileError` with one sentence naming the real format and
the fix, so no stack trace ever reaches the user.
"""

from __future__ import annotations

from enum import Enum, auto

from app.domain.errors import UnsupportedStatementFileError

_XLSX_MAGIC = b"PK\x03\x04"
_LEGACY_XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_PDF_MAGIC = b"%PDF"


class StatementFormat(Enum):
    CSV = auto()
    XLSX = auto()


def detect_format(file_bytes: bytes) -> StatementFormat:
    if file_bytes.startswith(_XLSX_MAGIC):
        return StatementFormat.XLSX

    if file_bytes.startswith(_LEGACY_XLS_MAGIC):
        raise UnsupportedStatementFileError(
            "This file is a legacy Excel 97-2003 (.xls) workbook, which this app cannot read. "
            "Open it in Excel or LibreOffice and use File > Save As > 'Excel Workbook (.xlsx)' "
            "or 'CSV UTF-8', then upload the new file."
        )

    if file_bytes.startswith(_PDF_MAGIC):
        raise UnsupportedStatementFileError(
            "This is a PDF, not a statement export. Use the Excel/CSV download from "
            "'Consulta de operaciones', not a printed/PDF statement."
        )

    head = file_bytes[:4096].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")) or b"<table" in head[:2048]:
        raise UnsupportedStatementFileError(
            "This file is named like a spreadsheet but is actually an HTML table (a common export "
            "quirk). Open it in Excel or LibreOffice and use File > Save As > 'Excel Workbook (.xlsx)' "
            "or 'CSV UTF-8', then upload the new file."
        )

    return StatementFormat.CSV
