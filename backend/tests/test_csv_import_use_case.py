from __future__ import annotations

from app.adapters.brokers.statement_files.myinvestor import parse_myinvestor_file
from app.application.import_transactions import CsvImportUseCase
from tests.fakes import FakeAssetRepo, FakePortfolioRepo

REAL_HEADER = "Fecha de la orden;ISIN;Importe estimado;Nº de participaciones;Estado\n"

NARROW_EXPORT = (
    REAL_HEADER
    + "04/09/2026;IE00BYX5MX67;500 EUR;30,444;Finalizada\n"
    + "04/09/2026;IE000QAZP7L2;700 EUR;49,3;Finalizada\n"
    + "01/06/2026;IE00BYX5MX67;250 EUR;15,492;Finalizada\n"
).encode("utf-8")

# A wider export covering the same date range as NARROW_EXPORT plus one
# genuinely new operation — this is the scenario the user asked for: "in
# case new excels arrive, the already inserted data must be ignored."
WIDE_EXPORT = (
    REAL_HEADER
    + "04/09/2026;IE00BYX5MX67;500 EUR;30,444;Finalizada\n"
    + "04/09/2026;IE000QAZP7L2;700 EUR;49,3;Finalizada\n"
    + "01/06/2026;IE00BYX5MX67;250 EUR;15,492;Finalizada\n"
    + "23/12/2025;IE00BYX5MX67;760 EUR;52,535;Finalizada\n"  # new
).encode("utf-8")


def _use_case() -> tuple[CsvImportUseCase, FakeAssetRepo, FakePortfolioRepo]:
    asset_repo = FakeAssetRepo()
    portfolio_repo = FakePortfolioRepo()
    return CsvImportUseCase(asset_repo, portfolio_repo), asset_repo, portfolio_repo


def test_commit_inserts_all_rows_on_first_import():
    use_case, _, portfolio_repo = _use_case()
    statement = parse_myinvestor_file(NARROW_EXPORT)
    result = use_case.commit(statement, account_id=1)
    assert result.transactions_added == 3
    assert len(portfolio_repo.list_transactions(account_id=1)) == 3


def test_reimport_overlapping_range_inserts_only_new_rows():
    """The acceptance test for the user's explicit requirement: re-importing
    a wider export that overlaps a previous one must insert only the rows
    that weren't already there."""
    use_case, _, portfolio_repo = _use_case()

    first = use_case.commit(parse_myinvestor_file(NARROW_EXPORT), account_id=1)
    assert first.transactions_added == 3

    second = use_case.commit(parse_myinvestor_file(WIDE_EXPORT), account_id=1)
    assert second.transactions_added == 1  # only the genuinely new row

    assert len(portfolio_repo.list_transactions(account_id=1)) == 4


def test_reimport_of_the_exact_same_file_inserts_nothing():
    use_case, _, portfolio_repo = _use_case()
    use_case.commit(parse_myinvestor_file(NARROW_EXPORT), account_id=1)
    second = use_case.commit(parse_myinvestor_file(NARROW_EXPORT), account_id=1)
    assert second.transactions_added == 0
    assert len(portfolio_repo.list_transactions(account_id=1)) == 3


def test_preview_reports_already_imported_vs_new():
    use_case, _, _ = _use_case()
    use_case.commit(parse_myinvestor_file(NARROW_EXPORT), account_id=1)

    preview = use_case.preview(parse_myinvestor_file(WIDE_EXPORT), account_id=1)
    assert preview.total_rows == 4
    assert preview.duplicate_transactions == 3
    assert preview.new_transactions == 1
    assert preview.invalid_rows == 0


def test_preview_and_commit_agree_on_which_rows_are_new():
    use_case, _, _ = _use_case()
    use_case.commit(parse_myinvestor_file(NARROW_EXPORT), account_id=1)

    preview = use_case.preview(parse_myinvestor_file(WIDE_EXPORT), account_id=1)
    commit_result = use_case.commit(parse_myinvestor_file(WIDE_EXPORT), account_id=1)
    assert preview.new_transactions == commit_result.transactions_added


def test_invalid_rows_are_skipped_on_commit_and_counted_in_preview():
    use_case, _, portfolio_repo = _use_case()
    bad_text = (
        b"Fecha operacion;ISIN;Cantidad;Precio;Tipo\n"
        b"05/02/2025;IE00BYX5MX67;10;16.5;Compra\n"
        b"05/02/2025;IE00BYX5MX67;10;16.5;Rareza\n"  # unrecognized type -> error
    )

    preview = use_case.preview(parse_myinvestor_file(bad_text), account_id=1)
    assert preview.invalid_rows == 1
    assert preview.new_transactions == 1

    result = use_case.commit(parse_myinvestor_file(bad_text), account_id=1)
    assert result.transactions_added == 1
    assert len(portfolio_repo.list_transactions(account_id=1)) == 1


def test_unmapped_required_columns_yield_no_rows_and_are_reported():
    use_case, _, _ = _use_case()
    statement = parse_myinvestor_file(b"Producto;Estado\nBGF World;Finalizada\n")
    preview = use_case.preview(statement, account_id=1)
    assert preview.total_rows == 0
    assert "date" in preview.unmapped_columns


def test_holdings_are_derived_from_imported_transactions():
    from decimal import Decimal

    use_case, asset_repo, portfolio_repo = _use_case()
    use_case.commit(parse_myinvestor_file(NARROW_EXPORT), account_id=1)

    # IE00BYX5MX67 appears twice in NARROW_EXPORT: 30.444 + 15.492 units.
    asset = asset_repo.get_by_symbol("IE00BYX5MX67")
    assert asset is not None
    holding = portfolio_repo.get_holding(1, asset.id)
    assert holding is not None
    assert holding["quantity"] == Decimal("45.936")
