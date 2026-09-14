from __future__ import annotations

from app.application.import_fingerprint import assign_external_ids
from app.application.import_transactions import ParsedRow


def _row(**overrides) -> ParsedRow:
    defaults = {
        "symbol": None,
        "name": None,
        "type": "BUY",
        "quantity": "10",
        "price": "16.5",
        "fees": "0",
        "currency": "EUR",
        "executed_at": "2025-02-05",
        "isin": "IE00BYX5MX67",
        "external_id": None,
        "warnings": [],
        "errors": [],
        "row_number": 1,
    }
    defaults.update(overrides)
    return ParsedRow(**defaults)


def test_fingerprint_deterministic_across_two_parses_of_same_bytes():
    a = [_row()]
    b = [_row()]
    assign_external_ids(a)
    assign_external_ids(b)
    assert a[0].external_id == b[0].external_id
    assert a[0].external_id.startswith("fp1:")


def test_fingerprint_stable_under_formatting_differences():
    a = [_row(quantity="12,5".replace(",", "."), price="16.5")]  # canonical "12.5"
    b = [_row(quantity="12.500000", price="16.5")]
    assign_external_ids(a)
    assign_external_ids(b)
    assert a[0].external_id == b[0].external_id


def test_fingerprint_stable_when_date_formatted_differently_upstream():
    # both already normalized to ISO by the parser layer by the time this
    # module sees them — but a stray time component must not matter for the
    # date portion used in the key.
    a = [_row(executed_at="2025-02-05")]
    b = [_row(executed_at="2025-02-05T00:00:00")]
    assign_external_ids(a)
    assign_external_ids(b)
    assert a[0].external_id == b[0].external_id


def test_fingerprint_survives_product_rename_when_isin_present():
    a = [_row(isin="IE00BYX5MX67", name="Old Fund Name")]
    b = [_row(isin="IE00BYX5MX67", name="New Fund Name After Rename")]
    assign_external_ids(a)
    assign_external_ids(b)
    assert a[0].external_id == b[0].external_id


def test_fingerprint_falls_back_to_name_and_warns_when_no_isin_or_symbol():
    row = _row(isin=None, symbol=None, name="Some Fund")
    assign_external_ids([row])
    assert row.external_id is not None
    assert any("product name" in w for w in row.warnings)


def test_fingerprint_changes_when_price_or_quantity_changes():
    base = _row()
    diff_qty = _row(quantity="11")
    diff_price = _row(price="17.0")
    rows = [base, diff_qty, diff_price]
    assign_external_ids(rows)
    ids = {r.external_id for r in rows}
    assert len(ids) == 3


def test_identical_rows_get_distinct_occurrence_indices():
    rows = [_row(), _row()]
    assign_external_ids(rows)
    ids = {r.external_id for r in rows}
    assert len(ids) == 2  # both survive — neither is silently dropped
    assert {rid.rsplit("#", 1)[1] for rid in ids} == {"0", "1"}


def test_occurrence_index_is_order_insensitive():
    forward = [_row(row_number=1), _row(row_number=2), _row(row_number=3)]
    backward = list(reversed([_row(row_number=1), _row(row_number=2), _row(row_number=3)]))
    assign_external_ids(forward)
    assign_external_ids(backward)
    assert {r.external_id for r in forward} == {r.external_id for r in backward}


def test_late_arriving_third_identical_row_inserts_only_once_more():
    narrow = [_row(), _row()]
    wide = [_row(), _row(), _row()]
    assign_external_ids(narrow)
    assign_external_ids(wide)
    narrow_ids = {r.external_id for r in narrow}
    wide_ids = {r.external_id for r in wide}
    assert narrow_ids.issubset(wide_ids)
    assert len(wide_ids - narrow_ids) == 1


def test_error_rows_are_never_assigned_an_id():
    row = _row(errors=["unparseable date"])
    assign_external_ids([row])
    assert row.external_id is None


def test_native_reference_is_left_untouched():
    row = _row(external_id="myinvestor:REF-123")
    assign_external_ids([row])
    assert row.external_id == "myinvestor:REF-123"
