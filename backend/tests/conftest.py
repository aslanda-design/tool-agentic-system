from __future__ import annotations

import pytest
from openpyxl import Workbook


@pytest.fixture
def make_xlsx(tmp_path):
    """Build minimal XLSX bytes from a list of rows (first row = headers).
    Keeps fixtures readable/diffable in the test file itself instead of a
    checked-in binary blob."""

    def _make(rows: list[list[object]], sheet_name: str = "Sheet1") -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = sheet_name
        for row in rows:
            ws.append(row)
        path = tmp_path / f"{sheet_name}.xlsx"
        wb.save(path)
        return path.read_bytes()

    return _make
