"""Comprehensive test suite for read-only streaming XLSX source reader (WP-05).

Validates all 9 Codex Review Axes (R1 to R9) and Roadmap Criteria (XR-01 to XR-13)
using synthetic in-memory OpenXML packages without touching live Excel or real data.
"""

from __future__ import annotations

import io
import json
import random
import subprocess
import sys
import uuid
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
)
from accounting_contracts.source_change_plan import (
    DuplicateIdentityError,
    IdentityLifecycle,
    PriorIdentityState,
    SourceRowInput,
    SourceSheetInput,
    build_prior_identity_registry,
    build_source_workbook_snapshot,
    plan_source_changes,
)
from accounting_local_agent.xlsx_source_reader import (
    REASON_CELL_INCOMPATIBLE_CHILDREN,
    REASON_CELL_INVALID_NUMERIC_LEXEME,
    REASON_CELL_INVALID_SST_INDEX,
    REASON_CELL_SST_INDEX_OUT_OF_RANGE,
    REASON_FORMULA_COVERAGE_REVERSED_RANGE,
    REASON_PACKAGE_DUPLICATE_REL_ID,
    REASON_PACKAGE_FORBIDDEN_CONTENT_TYPE,
    REASON_PACKAGE_MISSING_CONTENT_TYPES,
    REASON_STRUCTURE_INVALID_SHEET_HIERARCHY,
    REASON_STRUCTURE_INVALID_VERSION,
    XLSX_SOURCE_READER_VERSION,
    SourceRowLocation,
    XlsxCellError,
    XlsxFormulaCoverageError,
    XlsxPackageError,
    XlsxSourceReadResult,
    XlsxStructureError,
    read_xlsx_source_snapshot,
)
from hypothesis import given
from hypothesis import strategies as st

# --- Synthetic UUID Generator ---


def _make_uuid7(b: bytes) -> uuid.UUID:
    """Helper generating an RFC 4122 version 7 UUID from 16 bytes."""
    b_arr = bytearray(b)
    b_arr[6] = (b_arr[6] & 0x0F) | 0x70
    b_arr[8] = (b_arr[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(b_arr))


# --- Synthetic OpenXML Package Builder ---


class SyntheticXlsxBuilder:
    """Helper constructing in-memory valid OpenXML OPC .xlsx packages for testing."""

    def __init__(self, *, is_strict: bool = False) -> None:
        self.is_strict = is_strict
        self.ns_sm = (
            "http://purl.oclc.org/ooxml/spreadsheetml/main"
            if is_strict
            else "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        )
        self.ns_rel_office = (
            "http://purl.oclc.org/ooxml/officeDocument/relationships"
            if is_strict
            else "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        )
        self.sheets: dict[str, list[dict[str, Any]]] = {}
        self.custom_sheet_filenames: dict[str, str] = {}
        self.helper_sheets: dict[str, str] = {}
        self.shared_strings: list[str] = []
        self.extra_files: dict[str, bytes] = {}
        self.corrupt_zip: bool = False
        self.duplicate_zip_entries: list[tuple[str, bytes]] = []
        self.override_content_types: str | None = None
        self.omit_content_types: bool = False
        self.override_rels: str | None = None
        self.omit_root_rels: bool = False
        self.override_wb_rels: str | None = None
        self.override_wb_xml: str | None = None
        self.override_sst_xml: str | None = None
        self.raw_sheet_xml_overrides: dict[str, str] = {}

    def add_sheet_rows(
        self,
        sheet_name: str,
        rows: list[dict[str, Any]],
        *,
        custom_filename: str | None = None,
    ) -> SyntheticXlsxBuilder:
        self.sheets[sheet_name] = rows
        if custom_filename:
            self.custom_sheet_filenames[sheet_name] = custom_filename
        return self

    def add_helper_sheet(
        self, sheet_name: str, xml_content: str = ""
    ) -> SyntheticXlsxBuilder:
        self.helper_sheets[sheet_name] = xml_content
        return self

    def build_bytes(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            all_names = list(self.sheets.keys())
            for k in self.raw_sheet_xml_overrides:
                if k not in all_names:
                    all_names.append(k)
            for k in self.helper_sheets:
                if k not in all_names:
                    all_names.append(k)

            # 1. [Content_Types].xml
            if not self.omit_content_types:
                if self.override_content_types is not None:
                    zf.writestr("[Content_Types].xml", self.override_content_types)
                else:
                    ct_wb = (
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.main+xml"
                        if self.is_strict
                        else "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet.main+xml"
                    )
                    ct_xml = (
                        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                        '  <Default Extension="rels" '
                        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
                        '  <Default Extension="xml" ContentType="application/xml"/>\n'
                        f'  <Override PartName="/xl/workbook.xml" '
                        f'ContentType="{ct_wb}"/>\n'
                        '  <Override PartName="/xl/sharedStrings.xml" '
                        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>\n'
                    )
                    for idx, s_name in enumerate(all_names, 1):
                        fn = self.custom_sheet_filenames.get(
                            s_name, f"worksheets/sheet{idx}.xml"
                        )
                        ct_xml += (
                            f'  <Override PartName="/xl/{fn}" '
                            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\n'
                        )
                    ct_xml += "</Types>"
                    zf.writestr("[Content_Types].xml", ct_xml)

            # 2. _rels/.rels
            if not self.omit_root_rels:
                if self.override_rels is not None:
                    zf.writestr("_rels/.rels", self.override_rels)
                else:
                    rels_xml = (
                        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                        f'  <Relationship Id="rId1" '
                        f'Type="{self.ns_rel_office}/officeDocument" '
                        'Target="xl/workbook.xml"/>\n'
                        "</Relationships>"
                    )
                    zf.writestr("_rels/.rels", rels_xml)

            # 3. xl/_rels/workbook.xml.rels
            if self.override_wb_rels is not None:
                zf.writestr("xl/_rels/workbook.xml.rels", self.override_wb_rels)
            else:
                wb_rels_xml = (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                    f'  <Relationship Id="rId_sst" '
                    f'Type="{self.ns_rel_office}/sharedStrings" '
                    'Target="sharedStrings.xml"/>\n'
                )
                for idx, s_name in enumerate(all_names, 1):
                    fn = self.custom_sheet_filenames.get(
                        s_name, f"worksheets/sheet{idx}.xml"
                    )
                    wb_rels_xml += (
                        f'  <Relationship Id="rId_{idx}" '
                        f'Type="{self.ns_rel_office}/worksheet" Target="{fn}"/>\n'
                    )
                wb_rels_xml += "</Relationships>"
                zf.writestr("xl/_rels/workbook.xml.rels", wb_rels_xml)

            # 4. xl/workbook.xml
            if self.override_wb_xml is not None:
                zf.writestr("xl/workbook.xml", self.override_wb_xml)
            else:
                wb_xml = (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    f'<workbook xmlns="{self.ns_sm}" xmlns:r="{self.ns_rel_office}">\n'
                    "  <sheets>\n"
                )
                for idx, s_name in enumerate(all_names, 1):
                    wb_xml += (
                        f'    <sheet name="{s_name}" sheetId="{idx}" '
                        f'r:id="rId_{idx}"/>\n'
                    )
                wb_xml += "  </sheets>\n</workbook>"
                zf.writestr("xl/workbook.xml", wb_xml)

            # 5. Build sharedStrings.xml if needed
            if self.override_sst_xml is not None:
                zf.writestr("xl/sharedStrings.xml", self.override_sst_xml)
            elif "xl/sharedStrings.xml" not in self.extra_files:
                sst_xml = (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    f'<sst xmlns="{self.ns_sm}" count="{len(self.shared_strings)}" '
                    f'uniqueCount="{len(self.shared_strings)}">\n'
                )
                for s in self.shared_strings:
                    sst_xml += f"  <si><t>{_escape_xml_text(s)}</t></si>\n"
                sst_xml += "</sst>"
                zf.writestr("xl/sharedStrings.xml", sst_xml)

            # 6. Worksheets
            for idx, s_name in enumerate(all_names, 1):
                fn = self.custom_sheet_filenames.get(
                    s_name, f"worksheets/sheet{idx}.xml"
                )
                if s_name in self.raw_sheet_xml_overrides:
                    ws_xml = self.raw_sheet_xml_overrides[s_name]
                elif s_name in self.helper_sheets:
                    ws_xml = (
                        self.helper_sheets[s_name]
                        if self.helper_sheets[s_name]
                        else (
                            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                            f'<worksheet xmlns="{self.ns_sm}"><sheetData/></worksheet>'
                        )
                    )
                else:
                    rows_data = self.sheets.get(s_name, [])
                    ws_xml = self._build_sheet_xml(s_name, rows_data)
                zf.writestr(f"xl/{fn}", ws_xml)

            for extra_path, extra_data in self.extra_files.items():
                zf.writestr(extra_path, extra_data)

            # Duplicate ZIP entries (R2)
            for dup_name, dup_data in self.duplicate_zip_entries:
                zf.writestr(dup_name, dup_data)

        raw_bytes = buf.getvalue()
        if self.corrupt_zip:
            return raw_bytes[: len(raw_bytes) // 2]
        return raw_bytes

    def _build_sheet_xml(self, sheet_name: str, rows_data: list[dict[str, Any]]) -> str:
        """Build standard sheetData XML with valid headers at row 1."""
        contract = RAW_CONTRACT_REGISTRY.sheets.get(sheet_name)
        xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<worksheet xmlns="{self.ns_sm}">\n'
            '  <dimension ref="A1:Z5000"/>\n'
            "  <sheetData>\n"
        )

        # Row 1: Headers
        xml += '    <row r="1">\n'
        if contract:
            for (
                col_letter,
                header_text,
            ) in contract.required_headers_by_column.items():
                xml += (
                    f'      <c r="{col_letter}1" t="inlineStr"><is><t>'
                    f"{_escape_xml_text(header_text)}</t></is></c>\n"
                )
            id_col = contract.stable_id_column.column_letter
            if contract.stable_id_column.required_header:
                xml += (
                    f'      <c r="{id_col}1" t="inlineStr"><is><t>'
                    f"{_escape_xml_text(contract.stable_id_column.required_header)}</t></is></c>\n"
                )
        xml += "    </row>\n"

        # Data rows
        for row_dict in rows_data:
            r_num = row_dict.get("__row_num__", 2)
            xml += f'    <row r="{r_num}">\n'
            for col_letter, val in row_dict.items():
                if col_letter.startswith("__"):
                    continue
                c_xml = self._cell_to_xml(col_letter, r_num, val)
                if c_xml:
                    xml += f"      {c_xml}\n"
            xml += "    </row>\n"

        xml += "  </sheetData>\n</worksheet>"
        return xml

    def _cell_to_xml(self, col_letter: str, row_num: int, val: Any) -> str | None:
        """Serialize cell value to OpenXML <c> tag."""
        if val is None:
            return None

        cell_ref = f"{col_letter}{row_num}"

        # Special dict for custom formula/cell controls
        if isinstance(val, dict):
            t_attr = val.get("t")
            v_val = val.get("v")
            f_val = val.get("f")
            f_type = val.get("f_t")
            f_ref = val.get("f_ref")
            is_val = val.get("is")

            c_tag = f'<c r="{cell_ref}"'
            if t_attr is not None:
                c_tag += f' t="{t_attr}"'
            c_tag += ">"

            inner = ""
            if f_val is not None:
                f_tag = "<f"
                if f_type:
                    f_tag += f' t="{f_type}"'
                if f_ref:
                    f_tag += f' ref="{f_ref}"'
                f_tag += f">{_escape_xml_text(str(f_val))}</f>"
                inner += f_tag
            if is_val is not None:
                inner += f"<is><t>{_escape_xml_text(str(is_val))}</t></is>"
            if v_val is not None:
                inner += f"<v>{_escape_xml_text(str(v_val))}</v>"

            return f"{c_tag}{inner}</c>"

        if isinstance(val, (int, Decimal)):
            return f'<c r="{cell_ref}"><v>{val}</v></c>'

        if isinstance(val, str):
            return (
                f'<c r="{cell_ref}" t="inlineStr"><is><t>'
                f"{_escape_xml_text(val)}</t></is></c>"
            )

        return f'<c r="{cell_ref}"><v>{val}</v></c>'


def _escape_xml_text(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _sample_buy_sell_row_data(u7: uuid.UUID, row_num: int = 2) -> dict[str, Any]:
    return {
        "__row_num__": row_num,
        "A": "1",  # row_number (derived)
        "B": "1403/05/15",  # date_raw
        "C": "بازرگانی احمدی",  # party_name_raw
        "D": "خرید",  # transaction_type_raw
        "E": "طلای آبشده",  # item_name_raw
        "F": "12.34",  # quantity_raw
        "G": "1500000",  # unit_price_toman_raw
        "H": "0",  # discount_toman_raw
        "J": "توضیحات فاکتور",  # notes_raw
        "Z": str(u7).lower(),  # record_id
    }


def _sample_receipts_payments_row_data(
    u7: uuid.UUID, row_num: int = 2
) -> dict[str, Any]:
    return {
        "__row_num__": row_num,
        "A": "1",
        "B": "1403/01/01",
        "C": "همکار نمونه",
        "D": "RS",
        "E": "50000000",
        "F": "تسویه حساب",
        "G": "101",
        "H": "1",
        "P": str(u7).lower(),
    }


def _sample_inventory_movements_row_data(
    u7: uuid.UUID, row_num: int = 2
) -> dict[str, Any]:
    return {
        "__row_num__": row_num,
        "A": "1",
        "B": "1403/12/29",
        "C": "کارگاه زرگری",
        "D": "ورود",
        "E": "شمش طلا",
        "F": "100.5",
        "G": "750",
        "I": "تحویل شمش",
        "K": "1",
        "P": str(u7).lower(),
    }


def _sample_business_parties_row_data(
    u7: uuid.UUID, row_num: int = 2
) -> dict[str, Any]:
    return {
        "__row_num__": row_num,
        "A": "1",
        "B": "فروشگاه نمونه",
        "C": "SYNTHETIC-PHONE-001",
        "D": str(u7).lower(),
    }


def _build_standard_synthetic_workbook(
    *, is_strict: bool = False
) -> tuple[bytes, list[uuid.UUID]]:
    """Build a standard valid 4-sheet synthetic workbook."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder(is_strict=is_strict)
    builder.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u_bf, 2)])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    return builder.build_bytes(), [u_bf, u_dp, u_vk, u_lk]


# --- R1: XML Structure & False Deletions ---


def test_r1_invalid_row_and_cell_hierarchy_reproduction_rejection(
    tmp_path: Path,
) -> None:
    """R1: Invalid hierarchy on row or cells is rejected (no false VOID)."""
    u_bf = _make_uuid7(b"1" * 16)
    u_dp = _make_uuid7(b"2" * 16)
    u_vk = _make_uuid7(b"3" * 16)
    u_lk = _make_uuid7(b"4" * 16)

    # 4 invalid hierarchy scenarios that previously caused false row omission:
    scenarios = [
        # 1. Invalid namespace on row 2
        (
            '<row r="2" xmlns="http://invalid.org">'
            f'<c r="B2" t="inlineStr"><is><t>1403/01/01</t></is></c>'
            f'<c r="C2" t="inlineStr"><is><t>احمدی</t></is></c>'
            f'<c r="D2" t="inlineStr"><is><t>خرید</t></is></c>'
            f'<c r="Z2" t="inlineStr"><is><t>{u_bf}</t></is></c></row>'
        ),
        # 2. Cells inside unknown wrapper under row 2
        (
            '<row r="2"><customCellWrapper>'
            f'<c r="B2" t="inlineStr"><is><t>1403/01/01</t></is></c>'
            f'<c r="C2" t="inlineStr"><is><t>احمدی</t></is></c>'
            f'<c r="D2" t="inlineStr"><is><t>خرید</t></is></c>'
            f'<c r="Z2" t="inlineStr"><is><t>{u_bf}</t></is></c>'
            "</customCellWrapper></row>"
        ),
        # 3. Row inside unknown wrapper under sheetData
        (
            '<customRowWrapper><row r="2">'
            f'<c r="B2" t="inlineStr"><is><t>1403/01/01</t></is></c>'
            f'<c r="C2" t="inlineStr"><is><t>احمدی</t></is></c>'
            f'<c r="D2" t="inlineStr"><is><t>خرید</t></is></c>'
            f'<c r="Z2" t="inlineStr"><is><t>{u_bf}</t></is></c>'
            "</row></customRowWrapper>"
        ),
        # 4. Invalid namespace on cell C2
        (
            '<row r="2">'
            f'<c r="B2" t="inlineStr"><is><t>1403/01/01</t></is></c>'
            '<c r="C2" xmlns="http://invalid.org" t="inlineStr">'
            "<is><t>احمدی</t></is></c>"
            f'<c r="D2" t="inlineStr"><is><t>خرید</t></is></c>'
            f'<c r="Z2" t="inlineStr"><is><t>{u_bf}</t></is></c></row>'
        ),
    ]

    for idx, sc_row_xml in enumerate(scenarios, 1):
        builder = SyntheticXlsxBuilder()
        custom_ws = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<worksheet xmlns="{builder.ns_sm}">\n'
            "  <sheetData>\n"
            '    <row r="1">\n'
            '      <c r="A1" t="inlineStr"><is><t>ردیف</t></is></c>\n'
            '      <c r="B1" t="inlineStr"><is><t>تاریخ</t></is></c>\n'
            '      <c r="C1" t="inlineStr"><is><t>نام</t></is></c>\n'
            '      <c r="D1" t="inlineStr"><is><t>شرح</t></is></c>\n'
            '      <c r="E1" t="inlineStr"><is><t>کالا</t></is></c>\n'
            '      <c r="F1" t="inlineStr"><is><t>مقدار</t></is></c>\n'
            '      <c r="G1" t="inlineStr"><is><t>فی</t></is></c>\n'
            '      <c r="H1" t="inlineStr"><is><t>تخفیف</t></is></c>\n'
            '      <c r="J1" t="inlineStr"><is><t>توضیحات</t></is></c>\n'
            '      <c r="Z1" t="inlineStr"><is><t>record_id</t></is></c>\n'
            "    </row>\n"
            f"    {sc_row_xml}\n"
            "  </sheetData>\n</worksheet>"
        )
        builder.raw_sheet_xml_overrides["خرید-فروش"] = custom_ws
        builder.add_sheet_rows(
            "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
        )
        builder.add_sheet_rows(
            "ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)]
        )
        builder.add_sheet_rows(
            "لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)]
        )

        p = tmp_path / f"sc_{idx}.xlsx"
        p.write_bytes(builder.build_bytes())

        with pytest.raises(XlsxStructureError) as exc_info:
            read_xlsx_source_snapshot(p)
        assert exc_info.value.reason == REASON_STRUCTURE_INVALID_SHEET_HIERARCHY


def test_r1_conflicting_cell_children_rejected(tmp_path: Path) -> None:
    """R1: Incompatible multiple children in cell rejected with typed error."""
    builder1 = SyntheticXlsxBuilder()
    row_bad_c = _sample_buy_sell_row_data(_make_uuid7(b"1" * 16), 2)
    # Inject both is and v
    row_bad_c["C"] = {"is": "احمدی", "v": "123"}
    builder1.add_sheet_rows("خرید-فروش", [row_bad_c])
    builder1.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    builder1.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    builder1.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p1 = tmp_path / "bad_cell_children.xlsx"
    p1.write_bytes(builder1.build_bytes())

    with pytest.raises(XlsxCellError) as exc_info:
        read_xlsx_source_snapshot(p1)
    assert exc_info.value.reason == REASON_CELL_INCOMPATIBLE_CHILDREN


def test_r1_truly_empty_sheet_with_valid_headers_accepted(
    tmp_path: Path,
) -> None:
    """R1: Truly empty sheet (having row 1 headers, 0 data rows) is valid."""
    builder = SyntheticXlsxBuilder()
    builder.add_sheet_rows("خرید-فروش", [])
    builder.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    builder.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    builder.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    pkg_path = tmp_path / "empty_sheet.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg_path)
    assert len(res.snapshot.sheets["خرید-فروش"].rows) == 0
    assert res.snapshot.total_row_count == 3


# --- R2: OPC Package & XML Security ---


def test_r2_missing_content_types_and_macro_enabled_rejection(
    tmp_path: Path,
) -> None:
    """R2: Missing [Content_Types].xml or macroEnabled content type is rejected."""
    # 1. Missing [Content_Types].xml
    b1 = SyntheticXlsxBuilder()
    b1.omit_content_types = True
    b1.add_sheet_rows(
        "خرید-فروش", [_sample_buy_sell_row_data(_make_uuid7(b"1" * 16), 2)]
    )
    b1.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    b1.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    b1.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p1 = tmp_path / "missing_ct.xlsx"
    p1.write_bytes(b1.build_bytes())

    with pytest.raises(XlsxPackageError) as exc1:
        read_xlsx_source_snapshot(p1)
    assert exc1.value.reason == REASON_PACKAGE_MISSING_CONTENT_TYPES

    # 2. MacroEnabled workbook content type
    b2 = SyntheticXlsxBuilder()
    b2.override_content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        '  <Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        '  <Default Extension="xml" ContentType="application/xml"/>\n'
        '  <Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.ms-excel.sheet.macroEnabled.main+xml"/>\n'
        '  <Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\n'
        '  <Override PartName="/xl/worksheets/sheet2.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\n'
        '  <Override PartName="/xl/worksheets/sheet3.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\n'
        '  <Override PartName="/xl/worksheets/sheet4.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\n'
        "</Types>"
    )
    b2.add_sheet_rows(
        "خرید-فروش", [_sample_buy_sell_row_data(_make_uuid7(b"1" * 16), 2)]
    )
    b2.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    b2.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    b2.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p2 = tmp_path / "macro_enabled.xlsx"
    p2.write_bytes(b2.build_bytes())

    with pytest.raises(XlsxPackageError) as exc2:
        read_xlsx_source_snapshot(p2)
    assert exc2.value.reason == REASON_PACKAGE_FORBIDDEN_CONTENT_TYPE


def test_r2_duplicate_rel_ids_rejected(tmp_path: Path) -> None:
    """R2: Duplicate relationship Id across all rels entries is rejected."""
    b = SyntheticXlsxBuilder()
    b.override_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        '  <Relationship Id="rId1" TargetMode="External" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"\n'
        '    Target="https://example.com"/>\n'
        f'  <Relationship Id="rId1" '
        f'Type="{b.ns_rel_office}/officeDocument" '
        'Target="xl/workbook.xml"/>\n'
        "</Relationships>"
    )
    b.add_sheet_rows(
        "خرید-فروش", [_sample_buy_sell_row_data(_make_uuid7(b"1" * 16), 2)]
    )
    b.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    b.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    b.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p = tmp_path / "dup_rel_id.xlsx"
    p.write_bytes(b.build_bytes())

    with pytest.raises(XlsxPackageError) as exc:
        read_xlsx_source_snapshot(p)
    assert exc.value.reason == REASON_PACKAGE_DUPLICATE_REL_ID


def test_r2_sst_missing_internal_relationship_rejected(tmp_path: Path) -> None:
    """R2: Cell t='s' referencing SST with missing or external rel is rejected."""
    b = SyntheticXlsxBuilder()
    b.override_wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        '  <Relationship Id="rId_1" '
        f'Type="{b.ns_rel_office}/worksheet" Target="worksheets/sheet1.xml"/>\n'
        '  <Relationship Id="rId_2" '
        f'Type="{b.ns_rel_office}/worksheet" Target="worksheets/sheet2.xml"/>\n'
        '  <Relationship Id="rId_3" '
        f'Type="{b.ns_rel_office}/worksheet" Target="worksheets/sheet3.xml"/>\n'
        '  <Relationship Id="rId_4" '
        f'Type="{b.ns_rel_office}/worksheet" Target="worksheets/sheet4.xml"/>\n'
        "</Relationships>"
    )
    b.shared_strings = ["بازرگانی احمدی"]
    row_sst = _sample_buy_sell_row_data(_make_uuid7(b"1" * 16), 2)
    row_sst["C"] = {"t": "s", "v": "0"}
    b.add_sheet_rows("خرید-فروش", [row_sst])
    b.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    b.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    b.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p = tmp_path / "sst_no_rel.xlsx"
    p.write_bytes(b.build_bytes())

    with pytest.raises(XlsxCellError) as exc:
        read_xlsx_source_snapshot(p)
    assert exc.value.reason == REASON_CELL_SST_INDEX_OUT_OF_RANGE


def test_r2_dtd_and_entity_explicitly_rejected(tmp_path: Path) -> None:
    """R2: DOCTYPE and ENTITY declarations in package parts are rejected."""
    parts_to_test = [
        ("[Content_Types].xml", "<!DOCTYPE Types [<!ENTITY x 'test'>]>"),
        ("_rels/.rels", "<!DOCTYPE Relationships [<!ENTITY x 'test'>]>"),
        (
            "xl/_rels/workbook.xml.rels",
            "<!DOCTYPE Relationships [<!ENTITY x 'test'>]>",
        ),
        ("xl/workbook.xml", "<!DOCTYPE workbook [<!ENTITY x 'test'>]>"),
        ("xl/worksheets/sheet1.xml", "<!DOCTYPE worksheet [<!ENTITY x 'test'>]>"),
        ("xl/sharedStrings.xml", "<!DOCTYPE sst [<!ENTITY x 'test'>]>"),
    ]

    for part_name, doctype_decl in parts_to_test:
        b = SyntheticXlsxBuilder()
        b.add_sheet_rows(
            "خرید-فروش", [_sample_buy_sell_row_data(_make_uuid7(b"1" * 16), 2)]
        )
        b.add_sheet_rows(
            "دریافت-پرداخت",
            [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
        )
        b.add_sheet_rows(
            "ورود-خروج",
            [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
        )
        b.add_sheet_rows(
            "لیست کسبه",
            [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
        )

        if part_name == "[Content_Types].xml":
            b.override_content_types = (
                f'<?xml version="1.0" encoding="UTF-8"?>\n{doctype_decl}\n'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
            )
        elif part_name == "_rels/.rels":
            b.override_rels = (
                f'<?xml version="1.0" encoding="UTF-8"?>\n{doctype_decl}\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
            )
        elif part_name == "xl/_rels/workbook.xml.rels":
            b.override_wb_rels = (
                f'<?xml version="1.0" encoding="UTF-8"?>\n{doctype_decl}\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
            )
        elif part_name == "xl/workbook.xml":
            b.override_wb_xml = (
                f'<?xml version="1.0" encoding="UTF-8"?>\n{doctype_decl}\n'
                f'<workbook xmlns="{b.ns_sm}"><sheets/></workbook>'
            )
        elif part_name == "xl/worksheets/sheet1.xml":
            b.raw_sheet_xml_overrides["خرید-فروش"] = (
                f'<?xml version="1.0" encoding="UTF-8"?>\n{doctype_decl}\n'
                f'<worksheet xmlns="{b.ns_sm}"><sheetData/></worksheet>'
            )
        elif part_name == "xl/sharedStrings.xml":
            b.override_sst_xml = (
                f'<?xml version="1.0" encoding="UTF-8"?>\n{doctype_decl}\n'
                f'<sst xmlns="{b.ns_sm}"><si><t>test</t></si></sst>'
            )

        p = tmp_path / f"dtd_{part_name.replace('/', '_')}.xlsx"
        p.write_bytes(b.build_bytes())

        with pytest.raises((XlsxStructureError, XlsxPackageError)):
            read_xlsx_source_snapshot(p)


# --- R3 & R4: Activity & Text Boundary ---


def test_r3_sst_type_missing_v_tag_fails_read(tmp_path: Path) -> None:
    """R3: Cell t='s' without v tag is an invalid SST index error, not an empty row."""
    b = SyntheticXlsxBuilder()
    row_bad = _sample_buy_sell_row_data(_make_uuid7(b"1" * 16), 2)
    # C2 has t="s" but no <v>
    row_bad["C"] = {"t": "s"}
    b.add_sheet_rows("خرید-فروش", [row_bad])
    b.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    b.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    b.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p = tmp_path / "sst_no_v.xlsx"
    p.write_bytes(b.build_bytes())

    with pytest.raises(XlsxCellError) as exc:
        read_xlsx_source_snapshot(p)
    assert exc.value.reason == REASON_CELL_INVALID_SST_INDEX


def test_r3_inactive_row_with_invalid_date_or_uuid_is_omitted(
    tmp_path: Path,
) -> None:
    """R3: Inactive row (activity cols blank) with invalid date/UUID is omitted."""
    b = SyntheticXlsxBuilder()
    # Inactive row: only date and invalid UUID are present, all activity cols blank
    inactive_row = {
        "__row_num__": 2,
        "A": "1",
        "B": "1403/99/99",  # invalid date
        "C": "",  # party_name blank
        "D": "",  # transaction_type blank
        "E": "",  # item_name blank
        "F": None,  # quantity None
        "G": None,  # unit_price None
        "H": None,  # discount None
        "J": "",  # notes blank
        "Z": "NOT-A-UUID",  # invalid UUID
    }
    b.add_sheet_rows("خرید-فروش", [inactive_row])
    b.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    b.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    b.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p = tmp_path / "inactive_omitted.xlsx"
    p.write_bytes(b.build_bytes())

    res = read_xlsx_source_snapshot(p)
    assert len(res.snapshot.sheets["خرید-فروش"].rows) == 0
    assert res.snapshot.total_row_count == 3


def test_r4_direct_string_empty_v_is_empty_text_not_none(
    tmp_path: Path,
) -> None:
    """R4: Direct string t='str' with <v/> decodes to explicit empty text ''."""
    b = SyntheticXlsxBuilder()
    u1 = _make_uuid7(b"1" * 16)
    row = _sample_buy_sell_row_data(u1, 2)
    # J2 notes_raw set to empty str tag
    row["J"] = {"t": "str", "v": ""}
    b.add_sheet_rows("خرید-فروش", [row])
    b.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    b.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    b.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p = tmp_path / "empty_v_str.xlsx"
    p.write_bytes(b.build_bytes())

    res = read_xlsx_source_snapshot(p)
    assert res.snapshot.sheets["خرید-فروش"].rows[0].raw_values["notes_raw"] == ""


def test_r4_phonetic_rph_elements_excluded_from_rich_text(
    tmp_path: Path,
) -> None:
    """R4: Phonetic guide elements <rPh> in rich text <r> are excluded."""
    b = SyntheticXlsxBuilder()
    u1 = _make_uuid7(b"1" * 16)
    # J2 rich text with <rPh>
    custom_ws = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<worksheet xmlns="{b.ns_sm}">\n'
        "  <sheetData>\n"
        '    <row r="1">\n'
        '      <c r="A1" t="inlineStr"><is><t>ردیف</t></is></c>\n'
        '      <c r="B1" t="inlineStr"><is><t>تاریخ</t></is></c>\n'
        '      <c r="C1" t="inlineStr"><is><t>نام</t></is></c>\n'
        '      <c r="D1" t="inlineStr"><is><t>شرح</t></is></c>\n'
        '      <c r="E1" t="inlineStr"><is><t>کالا</t></is></c>\n'
        '      <c r="F1" t="inlineStr"><is><t>مقدار</t></is></c>\n'
        '      <c r="G1" t="inlineStr"><is><t>فی</t></is></c>\n'
        '      <c r="H1" t="inlineStr"><is><t>تخفیف</t></is></c>\n'
        '      <c r="J1" t="inlineStr"><is><t>توضیحات</t></is></c>\n'
        '      <c r="Z1" t="inlineStr"><is><t>record_id</t></is></c>\n'
        "    </row>\n"
        '    <row r="2">\n'
        '      <c r="B2" t="inlineStr"><is><t>1403/05/15</t></is></c>\n'
        '      <c r="C2" t="inlineStr"><is><t>احمدی</t></is></c>\n'
        '      <c r="D2" t="inlineStr"><is><t>خرید</t></is></c>\n'
        '      <c r="E2" t="inlineStr"><is><t>طلا</t></is></c>\n'
        '      <c r="F2"><v>1</v></c>\n'
        '      <c r="G2"><v>1000</v></c>\n'
        '      <c r="H2"><v>0</v></c>\n'
        '      <c r="J2" t="inlineStr">\n'
        "        <is><r><rPh><t>PHONETIC</t></rPh><t>توضیحات</t></r></is>\n"
        "      </c>\n"
        f'      <c r="Z2" t="inlineStr"><is><t>{u1}</t></is></c>\n'
        "    </row>\n"
        "  </sheetData>\n</worksheet>"
    )
    b.raw_sheet_xml_overrides["خرید-فروش"] = custom_ws
    b.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    b.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    b.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p = tmp_path / "rph_excluded.xlsx"
    p.write_bytes(b.build_bytes())

    res = read_xlsx_source_snapshot(p)
    assert res.snapshot.sheets["خرید-فروش"].rows[0].raw_values["notes_raw"] == "توضیحات"


def test_r4_per_fragment_escape_decoding(tmp_path: Path) -> None:
    """R4: OpenXML _xHHHH_ escapes decoded per fragment before joining runs."""
    b = SyntheticXlsxBuilder()
    u1 = _make_uuid7(b"1" * 16)
    row = _sample_buy_sell_row_data(u1, 2)
    # J2 has escaped characters e.g. _x0041_ for 'A'
    row["J"] = "_x0041__x0042__x0043_"
    b.add_sheet_rows("خرید-فروش", [row])
    b.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    b.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    b.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p = tmp_path / "escapes.xlsx"
    p.write_bytes(b.build_bytes())

    res = read_xlsx_source_snapshot(p)
    assert res.snapshot.sheets["خرید-فروش"].rows[0].raw_values["notes_raw"] == "ABC"


# --- R5: Strict Whole-String Regex & XML Numbers ---


def test_r5_strict_numeric_xml_grammar_and_rejections(tmp_path: Path) -> None:
    """R5: Strict ASCII numeric grammar; newline and underscore rejected."""
    bad_numbers = ["12\n", "1_000", "NaN", "INF", "۱۲۳"]
    for bad_n in bad_numbers:
        b = SyntheticXlsxBuilder()
        row = _sample_buy_sell_row_data(_make_uuid7(b"1" * 16), 2)
        row["F"] = {"v": bad_n}
        b.add_sheet_rows("خرید-فروش", [row])
        b.add_sheet_rows(
            "دریافت-پرداخت",
            [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
        )
        b.add_sheet_rows(
            "ورود-خروج",
            [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
        )
        b.add_sheet_rows(
            "لیست کسبه",
            [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
        )

        p = tmp_path / "bad_num.xlsx"
        p.write_bytes(b.build_bytes())

        with pytest.raises(XlsxCellError) as exc:
            read_xlsx_source_snapshot(p)
        assert exc.value.reason == REASON_CELL_INVALID_NUMERIC_LEXEME


def test_r5_xml_exponent_parsed_directly_to_decimal(tmp_path: Path) -> None:
    """R5: XML scientific notation 1.5e6 converts directly to Decimal."""
    b = SyntheticXlsxBuilder()
    u1 = _make_uuid7(b"1" * 16)
    row = _sample_buy_sell_row_data(u1, 2)
    row["F"] = {"v": "1.5e3"}  # 1500
    b.add_sheet_rows("خرید-فروش", [row])
    b.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    b.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    b.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p = tmp_path / "exponent.xlsx"
    p.write_bytes(b.build_bytes())

    res = read_xlsx_source_snapshot(p)
    qty = res.snapshot.sheets["خرید-فروش"].rows[0].raw_values["quantity_raw"]
    assert qty == Decimal("1500")


# --- R6: Formula Coverage & Anchor Handling ---


def test_r6_formula_anchor_anywhere_inside_range_accepted(
    tmp_path: Path,
) -> None:
    """R6: Formula anchor inside range (e.g. anchor=K2 with ref=H2:K2) is accepted."""
    b = SyntheticXlsxBuilder()
    u1 = _make_uuid7(b"1" * 16)

    # In sheet "خرید-فروش", put array formula anchor at K2 with ref=H2:K2
    # H2 is discount_toman_raw (a raw candidate); cache is excluded -> None.
    custom_ws = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<worksheet xmlns="{b.ns_sm}">\n'
        "  <sheetData>\n"
        '    <row r="1">\n'
        '      <c r="A1" t="inlineStr"><is><t>ردیف</t></is></c>\n'
        '      <c r="B1" t="inlineStr"><is><t>تاریخ</t></is></c>\n'
        '      <c r="C1" t="inlineStr"><is><t>نام</t></is></c>\n'
        '      <c r="D1" t="inlineStr"><is><t>شرح</t></is></c>\n'
        '      <c r="E1" t="inlineStr"><is><t>کالا</t></is></c>\n'
        '      <c r="F1" t="inlineStr"><is><t>مقدار</t></is></c>\n'
        '      <c r="G1" t="inlineStr"><is><t>فی</t></is></c>\n'
        '      <c r="H1" t="inlineStr"><is><t>تخفیف</t></is></c>\n'
        '      <c r="J1" t="inlineStr"><is><t>توضیحات</t></is></c>\n'
        '      <c r="Z1" t="inlineStr"><is><t>record_id</t></is></c>\n'
        "    </row>\n"
        '    <row r="2">\n'
        '      <c r="B2" t="inlineStr"><is><t>1403/05/15</t></is></c>\n'
        '      <c r="C2" t="inlineStr"><is><t>بازرگانی احمدی</t></is></c>\n'
        '      <c r="D2" t="inlineStr"><is><t>خرید</t></is></c>\n'
        '      <c r="E2" t="inlineStr"><is><t>طلا</t></is></c>\n'
        '      <c r="F2"><v>10</v></c>\n'
        '      <c r="G2"><v>1000</v></c>\n'
        '      <c r="H2"><v>500</v></c>\n'  # Covered by array formula -> None
        '      <c r="J2" t="inlineStr"><is><t>توضیحات</t></is></c>\n'
        '      <c r="K2"><f t="array" ref="H2:K2">SUM(A1:A10)</f><v>999</v></c>\n'
        f'      <c r="Z2" t="inlineStr"><is><t>{u1}</t></is></c>\n'
        "    </row>\n"
        "  </sheetData>\n</worksheet>"
    )
    b.raw_sheet_xml_overrides["خرید-فروش"] = custom_ws
    b.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    b.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    b.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p = tmp_path / "anchor_k2.xlsx"
    p.write_bytes(b.build_bytes())

    res = read_xlsx_source_snapshot(p)
    row = res.snapshot.sheets["خرید-فروش"].rows[0]
    # discount_toman_raw at H2 is covered by array formula -> None
    assert row.raw_values["discount_toman_raw"] is None


def test_r6_reversed_formula_coverage_range_rejected(tmp_path: Path) -> None:
    """R6: Reversed array range e.g. K2:H2 is rejected with XlsxFormulaCoverageError."""
    b = SyntheticXlsxBuilder()
    row = _sample_buy_sell_row_data(_make_uuid7(b"1" * 16), 2)
    row["F"] = {"f": "SUM(1,2)", "f_t": "array", "f_ref": "K2:H2", "v": "100"}
    b.add_sheet_rows("خرید-فروش", [row])
    b.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    b.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    b.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p = tmp_path / "reversed_ref.xlsx"
    p.write_bytes(b.build_bytes())

    with pytest.raises(XlsxFormulaCoverageError) as exc:
        read_xlsx_source_snapshot(p)
    assert exc.value.reason == REASON_FORMULA_COVERAGE_REVERSED_RANGE


def test_r6_extlst_formula_does_not_affect_raw_cell(tmp_path: Path) -> None:
    """R6: Formulas declared in <extLst> outside sheetData do not cover cells."""
    b = SyntheticXlsxBuilder()
    u1 = _make_uuid7(b"1" * 16)

    # Put a formula inside <extLst> outside <sheetData>
    custom_ws = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<worksheet xmlns="{b.ns_sm}">\n'
        "  <sheetData>\n"
        '    <row r="1">\n'
        '      <c r="A1" t="inlineStr"><is><t>ردیف</t></is></c>\n'
        '      <c r="B1" t="inlineStr"><is><t>تاریخ</t></is></c>\n'
        '      <c r="C1" t="inlineStr"><is><t>نام</t></is></c>\n'
        '      <c r="D1" t="inlineStr"><is><t>شرح</t></is></c>\n'
        '      <c r="E1" t="inlineStr"><is><t>کالا</t></is></c>\n'
        '      <c r="F1" t="inlineStr"><is><t>مقدار</t></is></c>\n'
        '      <c r="G1" t="inlineStr"><is><t>فی</t></is></c>\n'
        '      <c r="H1" t="inlineStr"><is><t>تخفیف</t></is></c>\n'
        '      <c r="J1" t="inlineStr"><is><t>توضیحات</t></is></c>\n'
        '      <c r="Z1" t="inlineStr"><is><t>record_id</t></is></c>\n'
        "    </row>\n"
        '    <row r="2">\n'
        '      <c r="B2" t="inlineStr"><is><t>1403/05/15</t></is></c>\n'
        '      <c r="C2" t="inlineStr"><is><t>احمدی</t></is></c>\n'
        '      <c r="D2" t="inlineStr"><is><t>خرید</t></is></c>\n'
        '      <c r="E2" t="inlineStr"><is><t>طلا</t></is></c>\n'
        '      <c r="F2"><v>10</v></c>\n'
        '      <c r="G2"><v>1000</v></c>\n'
        '      <c r="H2"><v>500</v></c>\n'
        '      <c r="J2" t="inlineStr"><is><t>توضیحات</t></is></c>\n'
        f'      <c r="Z2" t="inlineStr"><is><t>{u1}</t></is></c>\n'
        "    </row>\n"
        "  </sheetData>\n"
        "  <extLst>\n"
        '    <ext uri="{some-uri}">\n'
        '      <f t="array" ref="H2:K2">SUM(A1:A10)</f>\n'
        "    </ext>\n"
        "  </extLst>\n"
        "</worksheet>"
    )
    b.raw_sheet_xml_overrides["خرید-فروش"] = custom_ws
    b.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    b.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    b.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p = tmp_path / "extlst_formula.xlsx"
    p.write_bytes(b.build_bytes())

    res = read_xlsx_source_snapshot(p)
    # H2 is NOT covered because formula was outside sheetData
    assert res.snapshot.sheets["خرید-فروش"].rows[0].raw_values[
        "discount_toman_raw"
    ] == Decimal("500")


# --- R7: Safe Errors & WP-04 Identity Integration ---


def test_r7_uuid_casing_canonicalized_by_snapshot(tmp_path: Path) -> None:
    """R7: Non-canonical uppercase hex UUID in XLSX is canonicalized."""
    b = SyntheticXlsxBuilder()
    u1 = _make_uuid7(b"1" * 16)
    u1_upper = str(u1).upper()
    row = _sample_buy_sell_row_data(u1_upper, 2)  # type: ignore[arg-type]
    b.add_sheet_rows("خرید-فروش", [row])
    b.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    b.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    b.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p = tmp_path / "upper_uuid.xlsx"
    p.write_bytes(b.build_bytes())

    res = read_xlsx_source_snapshot(p)
    assert u1 in res.snapshot.all_rows_by_id
    assert res.snapshot.all_rows_by_id[u1].canonical_uuid == str(u1).lower()


def test_r7_error_reason_consistency_and_zero_data_leakage() -> None:
    """R7: Error messages contain only machine reasons and safe coordinates."""
    err_safe = XlsxCellError(
        REASON_CELL_INVALID_NUMERIC_LEXEME,
        sheet_name="خرید-فروش",
        cell_ref="F2",
        physical_row_number=2,
    )
    msg_safe = str(err_safe)
    assert "F2" in msg_safe
    assert "خرید-فروش" in msg_safe
    assert REASON_CELL_INVALID_NUMERIC_LEXEME in msg_safe

    # Invalid coordinate marker SYNTHETIC-PRIVATE-COORDINATE is omitted from str(error)
    err_unsafe = XlsxCellError(
        REASON_CELL_INVALID_NUMERIC_LEXEME,
        sheet_name="خرید-فروش",
        cell_ref="SYNTHETIC-PRIVATE-COORDINATE",
        physical_row_number=2,
    )
    msg_unsafe = str(err_unsafe)
    assert "SYNTHETIC-PRIVATE-COORDINATE" not in msg_unsafe


def test_r7_duplicate_uuid_raises_duplicate_identity_error(
    tmp_path: Path,
) -> None:
    """R7: Duplicate UUID across or within sheets raises DuplicateIdentityError."""
    u_dup = _make_uuid7(b"1" * 16)

    b = SyntheticXlsxBuilder()
    b.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u_dup, 2)])
    b.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dup, 2)])
    b.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    b.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p = tmp_path / "dup_uuid.xlsx"
    p.write_bytes(b.build_bytes())

    with pytest.raises(DuplicateIdentityError):
        read_xlsx_source_snapshot(p)


# --- R8: Cross-Platform Benchmark & Memory Helper ---


def test_xr12_synthetic_15000_row_benchmark(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """XR-12 / R8: Streaming benchmark on 15,000 active rows (<15s, <128MiB)."""
    builder = SyntheticXlsxBuilder()
    total_active_target = 15000
    rows_per_sheet = total_active_target // 4  # 3750 per sheet

    # Add 500 unused shared strings to test selective SST retention
    builder.shared_strings = [f"UNUSED_STRING_{i}" for i in range(500)]

    for sheet_idx, s_name in enumerate(RAW_CONTRACT_REGISTRY.sheets, 1):
        sheet_rows: list[dict[str, Any]] = []
        for r_idx in range(2, rows_per_sheet + 2):
            raw_uuid_bytes = f"{sheet_idx:04d}{r_idx:012d}".encode()  # 16 bytes
            u = _make_uuid7(raw_uuid_bytes)

            if s_name == "خرید-فروش":
                sheet_rows.append(_sample_buy_sell_row_data(u, r_idx))
            elif s_name == "دریافت-پرداخت":
                sheet_rows.append(_sample_receipts_payments_row_data(u, r_idx))
            elif s_name == "ورود-خروج":
                sheet_rows.append(_sample_inventory_movements_row_data(u, r_idx))
            else:
                sheet_rows.append(_sample_business_parties_row_data(u, r_idx))

        # Add 1,250 inactive tail rows per sheet (total 5,000 inactive rows)
        for r_idx in range(rows_per_sheet + 2, rows_per_sheet + 1252):
            sheet_rows.append(
                {
                    "__row_num__": r_idx,
                    "A": str(r_idx),
                    "B": "" if s_name == "لیست کسبه" else "1403/01/01",
                    "C": "",
                    "D": "",
                    "E": "",
                    "F": "",
                    "G": "",
                    "H": "",
                    "I": "",
                    "J": "",
                    "K": "",
                }
            )

        builder.add_sheet_rows(s_name, sheet_rows)

    pkg_path = tmp_path / "benchmark_15000.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    bench_code = f"""
import json, sys, time
from pathlib import Path
from accounting_local_agent.xlsx_source_reader import read_xlsx_source_snapshot

def get_peak_mem_mib():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        ):
            val = counters.PeakWorkingSetSize / (1024.0 * 1024.0)
            if val > 0.0:
                return val
        raise RuntimeError("Windows GetProcessMemoryInfo failed")
    else:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF)
        return ru.ru_maxrss / 1024.0

pkg = Path({repr(str(pkg_path))})
t0 = time.perf_counter()
res = read_xlsx_source_snapshot(pkg)
t1 = time.perf_counter()
peak_mib = get_peak_mem_mib()

out = {{
    "rows": res.snapshot.total_row_count,
    "locations": len(res.locations_by_uuid),
    "read_build_seconds": round(t1 - t0, 4),
    "peak_rss_mib": round(peak_mib, 2),
    "version": res.version,
    "platform": sys.platform,
}}
print(json.dumps(out))
"""
    proc = subprocess.run(
        [sys.executable, "-c", bench_code],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(proc.stdout.strip().splitlines()[-1])

    rows = data["rows"]
    duration = data["read_build_seconds"]
    peak_rss_mib = data["peak_rss_mib"]

    with capsys.disabled():
        print(
            f"\n[WP-05 BENCHMARK] 15,000 active rows -> "
            f"read_build_seconds: {duration:.4f}s | "
            f"peak_rss_mib: {peak_rss_mib:.2f} MiB | "
            f"rows: {rows} | platform: {data['platform']}"
        )

    assert rows == 15000
    assert data["locations"] == 15000
    assert data["version"] == XLSX_SOURCE_READER_VERSION

    assert duration < 15.0, f"Streaming read too slow: {duration:.2f}s"
    assert peak_rss_mib < 128.0, f"Peak memory too high: {peak_rss_mib:.2f} MiB"


# --- R9: Real Property Tests, Hidden Rows, Immutability & File Handles ---


@given(
    seed=st.integers(min_value=1, max_value=10000),
)
def test_r9_hypothesis_randomized_packages_invariance(
    seed: int,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """R9 / XR-09: Randomized permutations produce identical snapshot."""
    rng = random.Random(seed)
    tmp_dir = tmp_path_factory.mktemp("prop_perm")

    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    # Package 1 (Baseline)
    b1 = SyntheticXlsxBuilder()
    b1.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u_bf, 2)])
    b1.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b1.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b1.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    # Package 2 (Randomized sheet order, row positions)
    b2 = SyntheticXlsxBuilder()
    sheet_entries = [
        ("خرید-فروش", _sample_buy_sell_row_data(u_bf, rng.randint(2, 500))),
        (
            "دریافت-پرداخت",
            _sample_receipts_payments_row_data(u_dp, rng.randint(2, 500)),
        ),
        (
            "ورود-خروج",
            _sample_inventory_movements_row_data(u_vk, rng.randint(2, 500)),
        ),
        (
            "لیست کسبه",
            _sample_business_parties_row_data(u_lk, rng.randint(2, 500)),
        ),
    ]
    rng.shuffle(sheet_entries)

    for s_name, r_data in sheet_entries:
        b2.add_sheet_rows(s_name, [r_data])

    p1 = tmp_dir / f"pkg1_{seed}.xlsx"
    p2 = tmp_dir / f"pkg2_{seed}.xlsx"
    p1.write_bytes(b1.build_bytes())
    p2.write_bytes(b2.build_bytes())

    res1 = read_xlsx_source_snapshot(p1)
    res2 = read_xlsx_source_snapshot(p2)

    for s_name in RAW_CONTRACT_REGISTRY.sheets:
        assert (
            res1.snapshot.sheets[s_name].sheet_snapshot_hash
            == res2.snapshot.sheets[s_name].sheet_snapshot_hash
        )
        assert res1.snapshot.sheets[s_name].rows == res2.snapshot.sheets[s_name].rows

    assert res1.snapshot.all_rows_by_id == res2.snapshot.all_rows_by_id


def test_r9_hidden_rows_and_autofilter_retained(tmp_path: Path) -> None:
    """R9 / XR-06: Rows with hidden='1' or autoFilter ranges are retained."""
    u1 = _make_uuid7(b"0000000000000001")

    builder = SyntheticXlsxBuilder()
    custom_ws = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<worksheet xmlns="{builder.ns_sm}">\n'
        '  <autoFilter ref="A1:Z500"/>\n'
        "  <sheetData>\n"
        '    <row r="1">\n'
        '      <c r="A1" t="inlineStr"><is><t>ردیف</t></is></c>\n'
        '      <c r="B1" t="inlineStr"><is><t>تاریخ</t></is></c>\n'
        '      <c r="C1" t="inlineStr"><is><t>نام</t></is></c>\n'
        '      <c r="D1" t="inlineStr"><is><t>شرح</t></is></c>\n'
        '      <c r="E1" t="inlineStr"><is><t>کالا</t></is></c>\n'
        '      <c r="F1" t="inlineStr"><is><t>مقدار</t></is></c>\n'
        '      <c r="G1" t="inlineStr"><is><t>فی</t></is></c>\n'
        '      <c r="H1" t="inlineStr"><is><t>تخفیف</t></is></c>\n'
        '      <c r="J1" t="inlineStr"><is><t>توضیحات</t></is></c>\n'
        '      <c r="Z1" t="inlineStr"><is><t>record_id</t></is></c>\n'
        "    </row>\n"
        '    <row r="2" hidden="1">\n'
        '      <c r="B2" t="inlineStr"><is><t>1403/05/15</t></is></c>\n'
        '      <c r="C2" t="inlineStr"><is><t>احمدی</t></is></c>\n'
        '      <c r="D2" t="inlineStr"><is><t>خرید</t></is></c>\n'
        '      <c r="E2" t="inlineStr"><is><t>کالا</t></is></c>\n'
        '      <c r="F2"><v>1</v></c>\n'
        '      <c r="G2"><v>1000</v></c>\n'
        '      <c r="H2"><v>0</v></c>\n'
        '      <c r="J2" t="inlineStr"><is><t>توضیحات</t></is></c>\n'
        f'      <c r="Z2" t="inlineStr"><is><t>{u1}</t></is></c>\n'
        "    </row>\n"
        "  </sheetData>\n</worksheet>"
    )
    builder.raw_sheet_xml_overrides["خرید-فروش"] = custom_ws
    builder.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    builder.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    builder.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    pkg_path = tmp_path / "hidden_row.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    result = read_xlsx_source_snapshot(pkg_path)
    assert len(result.snapshot.sheets["خرید-فروش"].rows) == 1
    assert u1 in result.snapshot.all_rows_by_id


def test_r9_read_only_integrity_and_handle_cleanup_on_success_and_failure(
    tmp_path: Path,
) -> None:
    """R9 / XR-11: Handles cleanly closed on success and failure; file unchanged."""
    wb_bytes, _ = _build_standard_synthetic_workbook()
    pkg_path = tmp_path / "readonly_test.xlsx"
    pkg_path.write_bytes(wb_bytes)

    # Success case: file size/mtime unchanged, file can be read/renamed
    res = read_xlsx_source_snapshot(pkg_path)
    assert res.snapshot.total_row_count == 4
    assert pkg_path.read_bytes() == wb_bytes

    # Failure case: file handle closed, can be unlinked immediately
    bad_path = tmp_path / "bad_handle.xlsx"
    bad_path.write_bytes(b"NOT-A-ZIP-CORRUPT")
    with pytest.raises(XlsxPackageError):
        read_xlsx_source_snapshot(bad_path)

    bad_path.unlink()
    assert not bad_path.exists()


def test_r9_planner_transitions_and_state_advancement(tmp_path: Path) -> None:
    """R9 / XR-08: Full transition table and state advancement idempotency."""
    wb_bytes, _ = _build_standard_synthetic_workbook()
    pkg_path = tmp_path / "planner_test.xlsx"
    pkg_path.write_bytes(wb_bytes)

    result = read_xlsx_source_snapshot(pkg_path)

    # 1. Plan on empty prior registry -> 4 inserts
    plan1 = plan_source_changes(result.snapshot)
    assert plan1.total_counts.insert_count == 4

    # 2. Advance state with plan results
    prior_states = [
        PriorIdentityState(
            stable_id=it.stable_id,
            canonical_uuid=it.canonical_uuid,
            home_sheet=it.sheet_name,
            latest_revision=it.planned_revision or 1,
            lifecycle=IdentityLifecycle.ACTIVE,
            source_hash=it.current_source_hash or "0" * 64,
        )
        for it in plan1.items
    ]
    prior_reg = build_prior_identity_registry(prior_states)

    # 3. Re-run plan -> 0 mutations, 4 unchanged (Idempotency)
    plan2 = plan_source_changes(result.snapshot, prior_reg)
    assert plan2.total_counts.insert_count == 0
    assert plan2.total_counts.edit_count == 0
    assert plan2.total_counts.void_count == 0
    assert plan2.total_counts.unchanged_count == 4


def test_r9_result_immutability_and_defensive_copies() -> None:
    """R9 / XR-10: XlsxSourceReadResult validates invariants and is immutable."""
    u1 = _make_uuid7(b"0000000000000001")
    u2 = _make_uuid7(b"0000000000000002")
    u3 = _make_uuid7(b"0000000000000003")
    u4 = _make_uuid7(b"0000000000000004")

    snap = build_source_workbook_snapshot(
        [
            SourceSheetInput(
                "خرید-فروش",
                [
                    SourceRowInput(
                        u1,
                        {
                            "date_raw": "1403/01/01",
                            "party_name_raw": "احمدی",
                            "transaction_type_raw": "خرید",
                            "item_name_raw": "طلا",
                            "quantity_raw": Decimal(1),
                            "unit_price_toman_raw": Decimal(1000),
                            "discount_toman_raw": Decimal(0),
                            "notes_raw": None,
                        },
                    )
                ],
            ),
            SourceSheetInput(
                "دریافت-پرداخت",
                [
                    SourceRowInput(
                        u2,
                        {
                            "date_raw": "1403/01/01",
                            "party_name_raw": "احمدی",
                            "entry_type_raw": "RS",
                            "amount_toman_raw": Decimal(1000),
                            "notes_raw": None,
                            "account_code_raw": None,
                            "customer_flag_raw": None,
                        },
                    )
                ],
            ),
            SourceSheetInput(
                "ورود-خروج",
                [
                    SourceRowInput(
                        u3,
                        {
                            "date_raw": "1403/01/01",
                            "party_name_raw": "احمدی",
                            "movement_type_raw": "ورود",
                            "item_name_raw": "شمش",
                            "quantity_raw": Decimal(1),
                            "purity_raw": Decimal(750),
                            "notes_raw": None,
                            "customer_flag_raw": None,
                        },
                    )
                ],
            ),
            SourceSheetInput(
                "لیست کسبه",
                [
                    SourceRowInput(
                        u4,
                        {
                            "party_name_raw": "احمدی",
                            "phone_number_raw": "SYNTHETIC-PHONE-001",
                        },
                    )
                ],
            ),
        ]
    )

    locs = {
        u1: SourceRowLocation("خرید-فروش", 2),
        u2: SourceRowLocation("دریافت-پرداخت", 2),
        u3: SourceRowLocation("ورود-خروج", 2),
        u4: SourceRowLocation("لیست کسبه", 2),
    }

    res = XlsxSourceReadResult(snapshot=snap, locations_by_uuid=cast(Any, locs))
    assert res.version == XLSX_SOURCE_READER_VERSION

    # Modifying caller dictionary does not affect result
    locs[u1] = SourceRowLocation("خرید-فروش", 999)
    assert res.locations_by_uuid[u1].physical_row_number == 2

    # Negative: invalid version
    with pytest.raises(XlsxStructureError) as exc_v:
        XlsxSourceReadResult(
            snapshot=snap, locations_by_uuid=cast(Any, locs), version="v2"
        )
    assert exc_v.value.reason == REASON_STRUCTURE_INVALID_VERSION
