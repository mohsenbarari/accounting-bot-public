"""Synthetic package builders; marker literals are independent of product code."""

from __future__ import annotations

import io
import uuid
import zipfile
from collections.abc import Iterable
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from test_xlsx_source_reader import (
    SyntheticXlsxBuilder,
    _sample_business_parties_row_data,
    _sample_buy_sell_row_data,
    _sample_inventory_movements_row_data,
    _sample_receipts_payments_row_data,
)

SHEETS = ("خرید-فروش", "دریافت-پرداخت", "ورود-خروج", "لیست کسبه")
MARKER = "AccountingBot.SourceIdentity"
VALUE = "xlsx-source-identity.v1|00000000-0000-7000-8000-0000000003e7|1405"
FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"
PART = "docProps/custom.xml"
CT = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
TRANSITIONAL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties",
    "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties",
    "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
)
STRICT = (
    "http://purl.oclc.org/ooxml/officeDocument/relationships/customProperties",
    "http://purl.oclc.org/ooxml/officeDocument/customProperties",
    "http://purl.oclc.org/ooxml/officeDocument/docPropsVTypes",
)


def uid(index: int) -> uuid.UUID:
    return uuid.UUID(int=(7 << 76) | (2 << 62) | index)


def raw_parts(
    *,
    strict: bool = False,
    seed: int = 1,
    rows_per_sheet: int = 1,
    extra_buy: bool = True,
    edit: bool = False,
    reorder: bool = False,
    formula: int = 0,
    undated: bool = False,
    mixed: bool = False,
) -> dict[str, bytes]:
    builder = SyntheticXlsxBuilder(is_strict=strict)
    factories = (
        _sample_buy_sell_row_data,
        _sample_receipts_payments_row_data,
        _sample_inventory_movements_row_data,
        _sample_business_parties_row_data,
    )
    for index, (sheet, factory) in enumerate(zip(SHEETS, factories, strict=True)):
        count = rows_per_sheet + int(extra_buy and index == 0 and rows_per_sheet > 0)
        rows: list[dict[str, Any]] = []
        for number in range(count):
            row = factory(uid(seed + index * 100_000 + number), number + 2)
            if mixed and index == 2:
                row["B"] = "1404/12/29"
            if index == 0 and number == 0:
                row["G"] = "1500001" if edit else "1500000"
                if undated:
                    row["B"] = None
            rows.append(row)
        if reorder and index == 0 and len(rows) == 2:
            rows.reverse()
            for number, row in enumerate(rows, 2):
                row["__row_num__"] = number
        if formula and index == 0:
            rows.append(
                {
                    "__row_num__": 10,
                    "F": {"f": f"SUM(F2:F3)+{formula}", "v": str(99 + formula)},
                }
            )
        builder.add_sheet_rows(sheet, rows)
    with zipfile.ZipFile(io.BytesIO(builder.build_bytes())) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def property_xml(
    value: str = VALUE,
    *,
    strict: bool = False,
    prefix: str = "p",
    value_prefix: str = "v",
    name: str = MARKER,
    pid: str = "2",
    attributes: str = "",
    value_tag: str = "lpwstr",
    extra_properties: str = "",
    reverse: bool = False,
    encoding: str = "utf-8",
) -> bytes:
    _, ns_prop, ns_value = STRICT if strict else TRANSITIONAL
    prop = (
        f'<{prefix}:property fmtid="{FMTID}" pid={quoteattr(pid)} '
        f"name={quoteattr(name)} {attributes}>"
        f"<{value_prefix}:{value_tag}>{escape(value)}</{value_prefix}:{value_tag}>"
        f"</{prefix}:property>"
    )
    children = extra_properties + prop if reverse else prop + extra_properties
    return (
        f'<?xml version="1.0" encoding="{encoding}"?>'
        f'<{prefix}:Properties xmlns:{prefix}="{ns_prop}" '
        f'xmlns:{value_prefix}="{ns_value}">{children}</{prefix}:Properties>'
    ).encode(encoding)


def identified_parts(
    *,
    value: str = VALUE,
    strict: bool = False,
    part: str = PART,
    target: str | None = None,
    metadata: bytes | None = None,
    raw: dict[str, bytes] | None = None,
) -> dict[str, bytes]:
    result = dict(raw_parts(strict=strict) if raw is None else raw)
    rel_type = (STRICT if strict else TRANSITIONAL)[0]
    relation = (
        f'<Relationship Id="identity" Type="{rel_type}" '
        f"Target={quoteattr(part if target is None else target)}/>"
    )
    result["_rels/.rels"] = result["_rels/.rels"].replace(
        b"</Relationships>", relation.encode() + b"</Relationships>"
    )
    override = f'<Override PartName={quoteattr("/" + part)} ContentType="{CT}"/>'
    result["[Content_Types].xml"] = result["[Content_Types].xml"].replace(
        b"</Types>", override.encode() + b"</Types>"
    )
    result[part] = property_xml(value, strict=strict) if metadata is None else metadata
    return result


def zipped(parts: dict[str, bytes] | Iterable[tuple[str, bytes]]) -> bytes:
    entries = parts.items() if isinstance(parts, dict) else parts
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buf.getvalue()
