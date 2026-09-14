from __future__ import annotations

import pytest

from app.adapters.brokers.statement_files import numbers
from app.adapters.brokers.statement_files.generic import parse_generic_csv
from app.adapters.brokers.statement_files.myinvestor import parse_myinvestor_file
from app.adapters.brokers.statement_files.sniff import StatementFormat, detect_format
from app.domain.errors import UnsupportedStatementFileError

# --- sniff.py -----------------------------------------------------------

BIFF_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100
HTML_AS_XLS = b"<html><body><table><tr><td>Fecha</td></tr></table></body></html>"
PDF_BYTES = b"%PDF-1.4\n%...rest of a pdf..."


def test_detect_format_csv_plain_text():
    assert detect_format(b"date,type,quantity\n2025-01-01,BUY,1\n") is StatementFormat.CSV


def test_detect_format_xlsx_magic():
    assert detect_format(b"PK\x03\x04" + b"\x00" * 20) is StatementFormat.XLSX


def test_legacy_biff_xls_raises_actionable_error():
    with pytest.raises(UnsupportedStatementFileError) as exc:
        detect_format(BIFF_MAGIC)
    message = str(exc.value)
    assert "Save As" in message
    assert ".xlsx" in message


def test_html_masquerading_as_xls_raises_actionable_error():
    with pytest.raises(UnsupportedStatementFileError) as exc:
        detect_format(HTML_AS_XLS)
    assert "HTML" in str(exc.value)


def test_pdf_raises_actionable_error():
    with pytest.raises(UnsupportedStatementFileError) as exc:
        detect_format(PDF_BYTES)
    assert "PDF" in str(exc.value)


# --- numbers.py -----------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1.234,56", "1234.56"),
        ("12,5", "12.5"),
        ("49,3", "49.3"),
        ("-1.234,56", "-1234.56"),
        ("(1.234,56)", "-1234.56"),
        ("12,50 €", "12.50"),
        ("1000.2", "1000.2"),
        ("30,444", "30.444"),
        ("1 234,56", "1234.56"),  # NBSP thousands separator
        ("", None),
        (None, None),
        ("abc", None),
    ],
)
def test_normalize_decimal(raw, expected):
    assert numbers.normalize_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("04/09/2026", "2026-09-04"),
        ("2026-09-04", "2026-09-04"),
        ("04-09-2026", "2026-09-04"),
        ("04/09/26", "2026-09-04"),
        ("", None),
        (None, None),
        ("not a date", None),
    ],
)
def test_normalize_date(raw, expected):
    assert numbers.normalize_date(raw) == expected


def test_extract_currency():
    assert numbers.extract_currency("500 EUR") == "EUR"
    assert numbers.extract_currency("1.234,56") is None
    assert numbers.extract_currency(None) is None


# --- myinvestor.py: the real "Aportaciones"/buy-order export shape --------

REAL_HEADER = "Fecha de la orden;ISIN;Importe estimado;Nº de participaciones;Estado\n"


def _real_export(*data_rows: str) -> bytes:
    return (REAL_HEADER + "\n".join(data_rows) + "\n").encode("utf-8")


def test_real_myinvestor_export_parses_with_inferred_buy_type():
    data = _real_export(
        "04/09/2026;IE00BYX5MX67;500 EUR;30,444;Finalizada",
        "03/12/2025;IE000QAZP7L2;500 EUR;;Rechazada",
    )
    stmt = parse_myinvestor_file(data)
    assert stmt.unmapped_columns == []
    assert any("no operation-type column" in n for n in stmt.notices)
    assert stmt.skipped_count == 1  # the Rechazada row
    assert len(stmt.rows) == 1

    row = stmt.rows[0]
    assert row.errors == []
    assert row.type == "BUY"
    assert row.isin == "IE00BYX5MX67"
    assert row.symbol == "IE00BYX5MX67"
    assert row.quantity == "30.444"
    assert row.currency == "EUR"
    assert row.executed_at == "2026-09-04"
    # price derived from amount/quantity: 500 / 30.444
    assert row.price.startswith("16.42")
    assert any("derived from amount" in w for w in row.warnings)


def test_myinvestor_cp1252_semicolon_csv_with_accented_header():
    text = REAL_HEADER + "04/09/2026;IE00BYX5MX67;500 EUR;30,444;Finalizada\n"
    data = text.encode("cp1252")
    stmt = parse_myinvestor_file(data)
    assert stmt.file_format.startswith("CSV, cp1252")
    assert len(stmt.rows) == 1
    assert stmt.rows[0].errors == []


def test_myinvestor_xlsx_same_data_as_csv(make_xlsx):
    xlsx_bytes = make_xlsx(
        [
            ["Fecha de la orden", "ISIN", "Importe estimado", "Nº de participaciones", "Estado"],
            ["04/09/2026", "IE00BYX5MX67", "500 EUR", "30,444", "Finalizada"],
        ]
    )
    stmt = parse_myinvestor_file(xlsx_bytes)
    assert stmt.file_format.startswith("XLSX")
    assert len(stmt.rows) == 1
    row = stmt.rows[0]
    assert row.quantity == "30.444"
    assert row.currency == "EUR"
    assert row.executed_at == "2026-09-04"


def test_myinvestor_missing_required_columns_reported_not_defaulted():
    # "Producto" + "Estado" gives the header-row detector two alias hits to
    # recognize this as a header row at all, without supplying any of the
    # fields the parser actually requires (date, quantity, price/amount).
    data = b"Producto;Estado\nBGF World;Finalizada\n"
    stmt = parse_myinvestor_file(data)
    assert stmt.rows == []
    assert "date" in stmt.unmapped_columns
    assert "quantity" in stmt.unmapped_columns


def test_myinvestor_rejected_status_is_skipped_not_errored():
    data = _real_export("03/12/2025;IE000QAZP7L2;500 EUR;;Rechazada")
    stmt = parse_myinvestor_file(data)
    assert stmt.rows == []
    assert stmt.skipped_count == 1


def test_myinvestor_unrecognized_status_is_an_error_row():
    data = _real_export("03/12/2025;IE000QAZP7L2;500 EUR;10;Pendiente")
    stmt = parse_myinvestor_file(data)
    assert len(stmt.rows) == 1
    assert stmt.rows[0].errors
    assert "Pendiente" in stmt.rows[0].errors[0]


def test_myinvestor_settlement_date_column_does_not_win_over_operation_date():
    text = (
        "Fecha operacion;Fecha valor;ISIN;Cantidad;Precio;Tipo\n"
        "05/02/2025;07/02/2025;IE00BYX5MX67;10;16.5;Compra\n"
    )
    stmt = parse_myinvestor_file(text.encode("utf-8"))
    assert stmt.unmapped_columns == []
    assert stmt.column_mapping["date"] == "fecha operacion"
    assert stmt.rows[0].executed_at == "2025-02-05"


def test_myinvestor_bare_traspaso_is_an_error_not_deposit():
    text = "Fecha operacion;ISIN;Cantidad;Precio;Tipo\n05/02/2025;IE00BYX5MX67;10;16.5;Traspaso\n"
    stmt = parse_myinvestor_file(text.encode("utf-8"))
    assert len(stmt.rows) == 1
    row = stmt.rows[0]
    assert row.errors
    assert row.type != "DEPOSIT"


def test_myinvestor_unknown_type_is_an_error_never_becomes_buy():
    text = "Fecha operacion;ISIN;Cantidad;Precio;Tipo\n05/02/2025;IE00BYX5MX67;10;16.5;Rareza\n"
    stmt = parse_myinvestor_file(text.encode("utf-8"))
    row = stmt.rows[0]
    assert row.errors
    assert "unrecognized transaction type" in row.errors[0]


def test_myinvestor_aportacion_cash_movement_is_skipped():
    text = "Fecha operacion;ISIN;Cantidad;Precio;Tipo\n05/02/2025;IE00BYX5MX67;10;16.5;Aportacion\n"
    stmt = parse_myinvestor_file(text.encode("utf-8"))
    assert stmt.rows == []
    assert stmt.skipped_count == 1


def test_myinvestor_explicit_type_column_maps_compra_and_venta():
    text = (
        "Fecha operacion;ISIN;Cantidad;Precio;Tipo\n"
        "05/02/2025;IE00BYX5MX67;10;16.5;Compra\n"
        "06/02/2025;IE00BYX5MX67;5;17.0;Venta\n"
    )
    stmt = parse_myinvestor_file(text.encode("utf-8"))
    assert [r.type for r in stmt.rows] == ["BUY", "SELL"]
    assert all(not r.errors for r in stmt.rows)


# --- generic.py: regression guard for the module move ---------------------


def test_generic_csv_path_unchanged():
    text = "date,symbol,name,type,quantity,price,fees,currency,isin,external_id\n2025-01-01,ACME,Acme Corp,BUY,10,5.5,0,USD,US1234567890,ref-1\n"
    stmt = parse_generic_csv(text.encode("utf-8"))
    assert stmt.unmapped_columns == []
    assert len(stmt.rows) == 1
    row = stmt.rows[0]
    assert row.errors == []
    assert row.symbol == "ACME"
    assert row.type == "BUY"
    assert row.quantity == "10"
    assert row.price == "5.5"
    assert row.external_id == "generic:ref-1"


def test_generic_csv_missing_required_field_is_an_error_row():
    text = "date,symbol,type,quantity,price,currency\n,ACME,BUY,10,5.5,USD\n"
    stmt = parse_generic_csv(text.encode("utf-8"))
    assert len(stmt.rows) == 1
    assert stmt.rows[0].errors


def test_generic_csv_optional_exchange_column_is_carried_through():
    text = (
        "date,symbol,type,quantity,price,currency,exchange\n"
        "2025-01-01,VUSA,BUY,10,90.0,GBP,LSEETF\n"
    )
    stmt = parse_generic_csv(text.encode("utf-8"))
    assert stmt.unmapped_columns == []
    assert stmt.rows[0].exchange == "LSEETF"


def test_generic_csv_without_exchange_column_leaves_it_none():
    text = "date,symbol,type,quantity,price,currency\n2025-01-01,ACME,BUY,10,5.5,USD\n"
    stmt = parse_generic_csv(text.encode("utf-8"))
    assert stmt.rows[0].exchange is None
