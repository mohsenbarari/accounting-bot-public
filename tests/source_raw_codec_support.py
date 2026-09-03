"""Synthetic literal Raw fixtures and independent scalar/field-order oracles."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from accounting_contracts import (
    SourceSheetInput,
    ValidatedSourceRow,
    ValidatedSourceWorkbookSnapshot,
    build_source_workbook_snapshot,
    compute_source_hash,
)

SHEETS = ("خرید-فروش", "دریافت-پرداخت", "ورود-خروج", "لیست کسبه")
FIELDS = (
    (
        "date_raw",
        "party_name_raw",
        "transaction_type_raw",
        "item_name_raw",
        "quantity_raw",
        "unit_price_toman_raw",
        "discount_toman_raw",
        "notes_raw",
    ),
    (
        "date_raw",
        "party_name_raw",
        "entry_type_raw",
        "amount_toman_raw",
        "notes_raw",
        "account_code_raw",
        "customer_flag_raw",
    ),
    (
        "date_raw",
        "party_name_raw",
        "movement_type_raw",
        "item_name_raw",
        "quantity_raw",
        "purity_raw",
        "notes_raw",
        "customer_flag_raw",
    ),
    ("party_name_raw", "phone_number_raw"),
)
RAW: tuple[dict[str, Any], ...] = (
    dict(
        zip(
            FIELDS[0],
            [
                " ۱۴۰۵/۱/۱ ",
                "RAW-GOLDEN-A",
                "خرید",
                "SYNTHETIC",
                Decimal("1.00"),
                100,
                None,
                "",
            ],
            strict=True,
        )
    ),
    dict(
        zip(
            FIELDS[1],
            ["1405-01-01", "RAW-GOLDEN-B", "C", Decimal("-0.00"), None, " 001 ", ""],
            strict=True,
        )
    ),
    dict(
        zip(
            FIELDS[2],
            [
                None,
                "RAW-GOLDEN-C",
                "ورود",
                "SYNTHETIC",
                Decimal("1E+3"),
                "0.7500",
                " ",
                None,
            ],
            strict=True,
        )
    ),
    dict(zip(FIELDS[3], ["RAW-GOLDEN-D", "SYNTHETIC-PHONE"], strict=True)),
)


def uid(number: int) -> uuid.UUID:
    return uuid.UUID(int=(7 << 76) | (2 << 62) | number)


def make_row(
    sheet: int = 0,
    changes: Mapping[str, Any] | None = None,
    number: int | None = None,
) -> ValidatedSourceRow:
    values = dict(RAW[sheet])
    values.update(changes or {})
    identity = uid(sheet + 1 if number is None else number)
    return ValidatedSourceRow(
        identity,
        str(identity),
        SHEETS[sheet],
        MappingProxyType(values),
        compute_source_hash(SHEETS[sheet], values).source_hash,
    )


def snapshot(rows: Iterable[ValidatedSourceRow]) -> ValidatedSourceWorkbookSnapshot:
    grouped: dict[str, list[tuple[uuid.UUID, Mapping[str, Any]]]] = {
        sheet: [] for sheet in SHEETS
    }
    for row in rows:
        grouped[row.sheet_name].append((row.stable_id, row.raw_values))
    return build_source_workbook_snapshot(
        SourceSheetInput(sheet, grouped[sheet]) for sheet in SHEETS
    )


def scalar_view(value: Any) -> tuple[type[Any], Any]:
    # Decimal numeric equality would hide lost negative zero and scale.
    return type(value), value.as_tuple() if isinstance(value, Decimal) else value


def row_view(row: ValidatedSourceRow) -> tuple[Any, ...]:
    return (
        row.stable_id.bytes,
        row.canonical_uuid,
        row.sheet_name,
        row.source_hash,
        tuple((name, scalar_view(value)) for name, value in row.raw_values.items()),
    )


def wire(tree: Any) -> bytes:
    """Encode a test-authored tree; never invoke the product codec."""
    return json.dumps(
        tree, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def wire_tree(row: ValidatedSourceRow) -> list[Any]:
    """Independent grammar oracle used in generated cases and malformed payloads."""
    fields = []
    for name in FIELDS[SHEETS.index(row.sheet_name)]:
        value = row.raw_values[name]
        tag = {type(None): "null", str: "text", int: "int", Decimal: "decimal"}[
            type(value)
        ]
        encoded: Any = value
        if tag == "int":
            encoded = str(value)
        elif tag == "decimal":
            sign, digits, exponent = value.as_tuple()
            encoded = [
                str(sign),
                "".join(str(digit) for digit in digits),
                str(exponent),
            ]
        fields.append([name, [tag, encoded]])
    return [
        "source-raw-codec.v1",
        "raw-source-contract.v1",
        "source-hash.v1",
        row.sheet_name,
        row.canonical_uuid,
        row.source_hash,
        fields,
    ]
