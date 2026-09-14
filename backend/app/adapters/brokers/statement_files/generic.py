"""Broker-agnostic import format: a CSV/XLSX with our own column names, for
any broker without a dedicated parser, or as an export target from another
tool. Expected headers: date,symbol,name,type,quantity,price,fees,currency,
isin,external_id. `type` must be one of BUY/SELL/DIVIDEND/FEE/INTEREST/
DEPOSIT/WITHDRAWAL/SPLIT.
"""

from __future__ import annotations

from app.application.import_transactions import ParsedRow, ParsedStatement

from . import numbers
from .headers import alias_tokens, build_column_map
from .sniff import detect_format
from .tabular import read_sheet

GENERIC_FIELDS = ["date", "symbol", "name", "type", "quantity", "price", "fees", "currency", "isin", "external_id"]
GENERIC_ALIASES: dict[str, list[str]] = {field: [field] for field in GENERIC_FIELDS}
GENERIC_REQUIRED = ["date", "type", "quantity", "price", "currency"]


def parse_generic_csv(file_bytes: bytes) -> ParsedStatement:
    fmt = detect_format(file_bytes)
    sheet = read_sheet(file_bytes, fmt, alias_tokens(GENERIC_ALIASES))
    column_map = build_column_map(sheet.detected_headers, GENERIC_ALIASES)
    mapped = column_map.mapped

    missing = [f for f in GENERIC_REQUIRED if f not in mapped]
    if missing:
        return ParsedStatement(
            rows=[],
            file_format=sheet.provenance,
            detected_headers=sheet.detected_headers,
            column_mapping=mapped,
            unmapped_columns=missing,
            notices=[],
        )

    rows: list[ParsedRow] = []
    for i, raw in enumerate(sheet.rows, start=1):
        errors: list[str] = []
        warnings: list[str] = []

        date_raw = raw.get(mapped["date"], "")
        date_value = numbers.normalize_date(date_raw)
        if date_value is None:
            errors.append(f"unparseable date: {date_raw!r}")

        type_raw = raw.get(mapped["type"], "").strip().upper()
        if not type_raw:
            errors.append("missing transaction type")

        quantity_raw = raw.get(mapped["quantity"], "")
        quantity_value = numbers.normalize_decimal(quantity_raw)
        if quantity_value is None:
            errors.append(f"unparseable quantity: {quantity_raw!r}")

        price_raw = raw.get(mapped["price"], "")
        price_value = numbers.normalize_decimal(price_raw)
        if price_value is None:
            errors.append(f"unparseable price: {price_raw!r}")

        fees_raw = raw.get(mapped["fees"], "") if "fees" in mapped else ""
        fees_value = numbers.normalize_decimal(fees_raw) if fees_raw.strip() else "0"
        if fees_value is None:
            fees_value = "0"
            warnings.append(f"unparseable fee amount {fees_raw!r} — treated as 0")

        currency_raw = raw.get(mapped["currency"], "").strip().upper()
        if not currency_raw:
            errors.append("missing currency")

        symbol = (raw.get(mapped["symbol"], "").strip() or None) if "symbol" in mapped else None
        name = (raw.get(mapped["name"], "").strip() or None) if "name" in mapped else None
        isin = (raw.get(mapped["isin"], "").strip() or None) if "isin" in mapped else None

        external_id = None
        if "external_id" in mapped:
            ref = raw.get(mapped["external_id"], "").strip()
            if ref:
                candidate = f"generic:{ref}"
                if len(candidate) <= 100:
                    external_id = candidate
                else:
                    warnings.append("external id too long — using a content fingerprint instead")

        rows.append(
            ParsedRow(
                symbol=symbol,
                name=name,
                type=type_raw,
                quantity=quantity_value or "0",
                price=price_value or "0",
                fees=fees_value,
                currency=currency_raw or "EUR",
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
        notices=[],
    )
