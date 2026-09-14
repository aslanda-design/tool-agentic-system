"""MyInvestor has no public API (see root AGENTS.md) — this parses its own
statement exports instead. Verified against a real "Aportaciones" / buy-order
export (semicolon CSV): `Fecha de la orden;ISIN;Importe estimado;Nº de
participaciones;Estado`. That export has **no operation-type column at
all** — every row in it is a fund-subscription order, so the whole file is
treated as BUY when no `type` column is found (see `notices` on the result).
A different MyInvestor export (e.g. a general "Consulta de operaciones")
may have an explicit type column instead; `MYINVESTOR_HEADER_ALIASES` and
`MYINVESTOR_TYPE_MAP` cover both shapes, verified for the former and
best-effort for the latter — extend the alias lists here as real exports
turn up different headers (the import preview's "detected headers" panel
tells you exactly what to add).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.application.import_transactions import ParsedRow, ParsedStatement

from . import numbers
from .headers import alias_tokens, build_column_map, normalize_header
from .sniff import detect_format
from .tabular import read_sheet

MYINVESTOR_HEADER_ALIASES: dict[str, list[str]] = {
    "date": [
        "fecha de la orden",
        "fecha operacion",
        "fecha de la operacion",
        "f operacion",
        "fecha orden",
        "fecha",
    ],
    # "isin" deliberately also satisfies "symbol": MyInvestor's fund exports
    # are ISIN-centric with no separate ticker column, and asset resolution
    # needs *some* value in `symbol` (see resolve_asset in asset_resolution.py).
    "symbol": ["simbolo", "ticker", "isin"],
    "isin": ["isin"],
    "name": ["nombre", "producto", "activo", "descripcion", "denominacion"],
    "type": ["tipo", "tipo de operacion", "operacion"],
    "quantity": [
        "no de participaciones",
        "n de participaciones",
        "numero de participaciones",
        "num participaciones",
        "participaciones",
        "cantidad",
        "unidades",
        "titulos",
    ],
    "price": ["precio", "precio unitario", "valor liquidativo", "vl"],
    "amount": ["importe estimado", "importe", "importe bruto", "importe neto", "efectivo"],
    "fees": ["comision", "gastos", "corretaje", "canon"],
    "currency": ["divisa", "moneda"],
    "external_id": ["no operacion", "num operacion", "referencia", "id"],
    "status": ["estado"],
}

# Values of the "Estado" column, not headers — matched via normalize_header
# too, so accents don't matter here either.
COMPLETED_STATUSES = {"finalizada", "completada", "ejecutada", "confirmada", "liquidada"}
SKIPPED_STATUSES = {"rechazada", "cancelada", "anulada", "denegada", "fallida", "caducada"}

MYINVESTOR_TYPE_MAP = {
    "compra": "BUY",
    "suscripcion": "BUY",
    "traspaso de entrada": "BUY",
    "venta": "SELL",
    "reembolso": "SELL",
    "traspaso de salida": "SELL",
    "dividendo": "DIVIDEND",
    "reparto de dividendos": "DIVIDEND",
    "comision": "FEE",
    "canon": "FEE",
    "corretaje": "FEE",
}
# Cash movements are explicitly out of scope for this import (see AGENTS.md) —
# skip them with a visible count rather than guessing DEPOSIT/WITHDRAWAL and
# quietly changing the cash line the user didn't ask us to touch. Bare
# "traspaso" (no entrada/salida qualifier) is *not* in either map: its
# direction is unknowable from the word alone, and guessing DEPOSIT would
# fabricate cash (see build_snapshots.py's _cash_impact) while also dropping
# the position change — so it becomes a per-row error instead.
SKIPPED_TYPE_VALUES = {"aportacion", "reintegro"}


def _cell(raw: dict[str, str], mapped: dict[str, str], field: str) -> str:
    header = mapped.get(field)
    return raw.get(header, "") if header else ""


def parse_myinvestor_file(file_bytes: bytes) -> ParsedStatement:
    fmt = detect_format(file_bytes)
    sheet = read_sheet(file_bytes, fmt, alias_tokens(MYINVESTOR_HEADER_ALIASES))
    column_map = build_column_map(sheet.detected_headers, MYINVESTOR_HEADER_ALIASES)
    mapped = column_map.mapped

    missing: list[str] = []
    if "date" not in mapped:
        missing.append("date")
    if "quantity" not in mapped:
        missing.append("quantity")
    if "price" not in mapped and "amount" not in mapped:
        missing.append("price (or amount)")
    if "symbol" not in mapped and "isin" not in mapped:
        missing.append("symbol (or isin)")

    notices: list[str] = []
    infer_buy = "type" not in mapped
    if infer_buy:
        notices.append(
            "This file has no operation-type column, so every row is treated as a BUY — this matches "
            "MyInvestor's buy-order/'Aportaciones' export. If you also sell funds, those need a "
            "different MyInvestor export (one with an operation-type column)."
        )

    if missing:
        return ParsedStatement(
            rows=[],
            file_format=sheet.provenance,
            detected_headers=sheet.detected_headers,
            column_mapping=mapped,
            unmapped_columns=missing,
            notices=notices,
        )

    rows: list[ParsedRow] = []
    skipped_count = 0
    for i, raw in enumerate(sheet.rows, start=1):
        status_raw = normalize_header(_cell(raw, mapped, "status")) if "status" in mapped else ""
        if status_raw in SKIPPED_STATUSES:
            skipped_count += 1
            continue

        errors: list[str] = []
        warnings: list[str] = []

        if status_raw and status_raw not in COMPLETED_STATUSES:
            errors.append(f"unrecognized order status: {_cell(raw, mapped, 'status')!r}")

        date_raw = _cell(raw, mapped, "date")
        date_value = numbers.normalize_date(date_raw)
        if date_value is None:
            errors.append(f"unparseable date: {date_raw!r}")

        symbol = (_cell(raw, mapped, "symbol").strip() or None) if "symbol" in mapped else None
        isin = (_cell(raw, mapped, "isin").strip() or None) if "isin" in mapped else None
        name = (_cell(raw, mapped, "name").strip() or None) if "name" in mapped else None

        quantity_raw = _cell(raw, mapped, "quantity")
        quantity_value = numbers.normalize_decimal(quantity_raw)
        if quantity_value is None:
            errors.append(f"unparseable quantity: {quantity_raw!r}")

        amount_raw = _cell(raw, mapped, "amount") if "amount" in mapped else ""
        currency = numbers.extract_currency(amount_raw)
        if "currency" in mapped:
            currency_cell = _cell(raw, mapped, "currency").strip()
            if currency_cell:
                currency = currency_cell.upper()
        if currency is None:
            currency = "EUR"
            warnings.append("currency not found in file — defaulted to EUR")

        price_value = numbers.normalize_decimal(_cell(raw, mapped, "price")) if "price" in mapped else None
        if price_value is None and amount_raw and quantity_value not in (None, "0"):
            amount_value = numbers.normalize_decimal(amount_raw)
            if amount_value is not None:
                try:
                    price_value = str(
                        (Decimal(amount_value) / Decimal(quantity_value)).quantize(
                            Decimal("1E-8"), rounding=ROUND_HALF_UP
                        )
                    )
                    warnings.append("unit price derived from amount / quantity — not broker-reported")
                except (InvalidOperation, ZeroDivisionError):
                    price_value = None
        if price_value is None:
            errors.append("could not determine a unit price (no price column, and amount/quantity unavailable)")

        if infer_buy:
            txn_type = "BUY"
        else:
            raw_type_cell = _cell(raw, mapped, "type")
            raw_type = normalize_header(raw_type_cell)
            if raw_type in SKIPPED_TYPE_VALUES:
                skipped_count += 1
                continue
            txn_type = MYINVESTOR_TYPE_MAP.get(raw_type)
            if txn_type is None:
                errors.append(f"unrecognized transaction type: {raw_type_cell!r}")
                txn_type = ""

        fees_value = "0"
        if "fees" in mapped:
            fees_raw = _cell(raw, mapped, "fees")
            if fees_raw.strip():
                parsed_fees = numbers.normalize_decimal(fees_raw)
                if parsed_fees is not None:
                    fees_value = parsed_fees
                else:
                    warnings.append(f"unparseable fee amount {fees_raw!r} — treated as 0")

        external_id = None
        if "external_id" in mapped:
            ref = _cell(raw, mapped, "external_id").strip()
            if ref:
                candidate = f"myinvestor:{ref}"
                if len(candidate) <= 100:
                    external_id = candidate
                else:
                    warnings.append("operation reference too long — using a content fingerprint instead")

        rows.append(
            ParsedRow(
                symbol=symbol or isin,
                name=name,
                type=txn_type,
                quantity=quantity_value or "0",
                price=price_value or "0",
                fees=fees_value,
                currency=currency,
                executed_at=date_value or "",
                isin=isin,
                external_id=external_id,
                warnings=warnings,
                errors=errors,
                row_number=i,
            )
        )

    return ParsedStatement(
        rows=rows,
        file_format=sheet.provenance,
        detected_headers=sheet.detected_headers,
        column_mapping=mapped,
        unmapped_columns=[],
        notices=notices,
        skipped_count=skipped_count,
    )
