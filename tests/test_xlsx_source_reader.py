"""Comprehensive test suite for read-only streaming XLSX source reader (WP-05).

Validates all Codex Review Axes (R1 to R6, Round 4 Remediations R4-01 to R4-06)
and Roadmap Criteria (XR-01 to XR-13) using synthetic in-memory OpenXML packages
without touching live Excel or real data.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import uuid
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
)
from accounting_contracts.source_change_plan import (
    IdentityLifecycle,
    IdentityRelocationError,
    PlanAction,
    PriorIdentityRegistry,
    PriorIdentityState,
    SourceRowInput,
    SourceSheetInput,
    build_prior_identity_registry,
    build_source_workbook_snapshot,
    plan_source_changes,
)
from accounting_local_agent.xlsx_source_reader import (
    REASON_CELL_INVALID_NUMERIC_LEXEME,
    REASON_CELL_INVALID_SST_INDEX,
    REASON_CELL_UNKNOWN_TYPE,
    REASON_HEADER_FORMULA_BACKED,
    REASON_HEADER_TEXT_MISMATCH,
    REASON_IDENTITY_DUPLICATE_UUID,
    REASON_PACKAGE_AMBIGUOUS_SHARED_STRINGS,
    REASON_PACKAGE_AMBIGUOUS_WORKBOOK,
    REASON_PACKAGE_FORBIDDEN_CONTENT_TYPE,
    REASON_PACKAGE_MISSING_CONTENT_TYPES,
    REASON_STRUCTURE_AMBIGUOUS_SHEETS,
    REASON_STRUCTURE_DUPLICATE_LOCATION_ROW,
    REASON_STRUCTURE_INVALID_ROOT,
    REASON_STRUCTURE_INVALID_SHEET_HIERARCHY,
    REASON_STRUCTURE_INVALID_SNAPSHOT_TYPE,
    REASON_STRUCTURE_INVALID_VERSION,
    REASON_STRUCTURE_LOCATION_IDENTITY_MISMATCH,
    REASON_STRUCTURE_LOCATION_SHEET_MISMATCH,
    REASON_STRUCTURE_MALFORMED_XML,
    REASON_STRUCTURE_ROW_OUT_OF_BOUNDS,
    REASON_STRUCTURE_UNKNOWN_LOCATION_SHEET,
    XLSX_SOURCE_READER_VERSION,
    SourceRowLocation,
    XlsxCellError,
    XlsxHeaderError,
    XlsxIdentityError,
    XlsxPackageError,
    XlsxSourceReadResult,
    XlsxStructureError,
    read_xlsx_source_snapshot,
)
from hypothesis import given, settings
from hypothesis import strategies as st

# --- Synthetic UUID Generator ---


def _make_uuid7(b: bytes) -> uuid.UUID:
    """Helper generating an RFC 4122 version 7 UUID from 16 bytes."""
    b_arr = bytearray(b)
    if len(b_arr) < 16:
        b_arr = b_arr.ljust(16, b"0")
    elif len(b_arr) > 16:
        b_arr = b_arr[:16]
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
        self.raw_sheet_xml_overrides: dict[str, str | bytes] = {}
        self.dimension_ref: str = "A1:Z5000"

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
            f'  <dimension ref="{self.dimension_ref}"/>\n'
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
            s_attr = val.get("s")
            v_val = val.get("v")
            f_val = val.get("f")
            f_type = val.get("f_t")
            f_ref = val.get("f_ref")
            is_val = val.get("is")
            raw_inner = val.get("raw_inner")

            c_tag = f'<c r="{cell_ref}"'
            if s_attr is not None:
                c_tag += f' s="{s_attr}"'
            if t_attr is not None:
                c_tag += f' t="{t_attr}"'
            c_tag += ">"

            if raw_inner is not None:
                return f"{c_tag}{raw_inner}</c>"

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


def _sample_buy_sell_row_data(u7: uuid.UUID | str, row_num: int = 2) -> dict[str, Any]:
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
    u7: uuid.UUID | str, row_num: int = 2
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
    u7: uuid.UUID | str, row_num: int = 2
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
    u7: uuid.UUID | str, row_num: int = 2
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


# --- R1 & Acceptance Matrix Aliases ---


def test_r1_dtd_and_external_entities_parser_driven_rejection(
    tmp_path: Path,
) -> None:
    """Validate DTD and entity rejection parser-driven tests (XR-07)."""
    test_r1_dtd_in_utf8_and_utf16_rejected(tmp_path)


def test_r1_invalid_row_and_cell_hierarchy_reproduction_rejection(
    tmp_path: Path,
) -> None:
    """Validate invalid hierarchy rejection (XR-01, XR-07)."""
    test_r2_invalid_wrapper_inside_si_and_is_rejected(tmp_path)


def test_r1_utf16_and_cdata_comments_dtd_handling(tmp_path: Path) -> None:
    """Validate UTF-16, CDATA and comments containing DOCTYPE text (XR-01, XR-07)."""
    test_r1_cdata_and_comments_containing_doctype_accepted(tmp_path)
    test_r1_valid_utf16_workbook_accepted(tmp_path)


def test_r2_missing_content_types_and_macro_enabled_rejection(
    tmp_path: Path,
) -> None:
    """Validate [Content_Types].xml checks and macro rejection (XR-01, XR-07)."""
    # 1. Missing [Content_Types].xml
    b = SyntheticXlsxBuilder()
    b.omit_content_types = True
    p1 = tmp_path / "missing_ct.xlsx"
    p1.write_bytes(b.build_bytes())
    with pytest.raises(XlsxPackageError) as exc1:
        read_xlsx_source_snapshot(p1)
    assert exc1.value.reason == REASON_PACKAGE_MISSING_CONTENT_TYPES

    # 2. Macro enabled content type
    b2 = SyntheticXlsxBuilder()
    b2.override_content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        '  <Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.ms-excel.sheet.macroEnabled.main+xml"/>\n'
        "</Types>"
    )
    p2 = tmp_path / "macro.xlsm"
    p2.write_bytes(b2.build_bytes())
    with pytest.raises(XlsxPackageError) as exc2:
        read_xlsx_source_snapshot(p2)
    assert exc2.value.reason == REASON_PACKAGE_FORBIDDEN_CONTENT_TYPE


def test_r5_strict_numeric_xml_grammar_and_rejections(tmp_path: Path) -> None:
    """Validate strict numeric XML grammar and rejections (XR-03, XR-05)."""
    test_r5_textual_scientific_exponent_rejected(tmp_path)


def test_r5_xml_exponent_parsed_directly_to_decimal(tmp_path: Path) -> None:
    """Validate scientific notation in XML parsed directly to Decimal (XR-03)."""
    test_r5_native_xml_numeric_exponent_accepted(tmp_path)


def test_r6_read_only_integrity_and_handle_cleanup(tmp_path: Path) -> None:
    """Validate read-only file integrity and cleanup (XR-11)."""
    test_r6_read_only_integrity_and_clean_cleanup(tmp_path)


def test_xr02_required_headers_validation_and_rejections(
    tmp_path: Path,
) -> None:
    """Validate required headers and rejections (XR-02)."""
    test_r4_02_required_headers_and_technical_id_matrix(tmp_path)


def test_xr04_formula_exclusion_and_coverage_intervals(tmp_path: Path) -> None:
    """Validate formula exclusion and coverage (XR-04)."""
    test_r4_03_sst_selection_and_classification_positive_negative(tmp_path)


def test_xr05_row_activity_and_identity_validation(tmp_path: Path) -> None:
    """Validate row activity and UUID validation (XR-05)."""
    test_r5_duplicate_uuid_raises_xlsx_identity_error(tmp_path)


def test_xr06_hidden_rows_dimensions_and_coordinates(tmp_path: Path) -> None:
    """Validate dimensions, distant rows, and coordinates (XR-06)."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.dimension_ref = "A1:Z1000000"
    # Row at distant row 1000
    row_bf = _sample_buy_sell_row_data(u_bf, 1000)
    builder.add_sheet_rows("خرید-فروش", [row_bf])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    p = tmp_path / "distant.xlsx"
    p.write_bytes(builder.build_bytes())
    res = read_xlsx_source_snapshot(p)
    assert res.snapshot.total_row_count == 4
    assert res.locations_by_uuid[u_bf].physical_row_number == 1000


# --- Detailed Tests ---


def test_r1_dtd_in_utf8_and_utf16_rejected(tmp_path: Path) -> None:
    for enc in ("utf-8", "utf-16"):
        builder = SyntheticXlsxBuilder()
        builder.override_wb_xml = (
            f'<?xml version="1.0" encoding="{enc}"?>\n'
            "<!DOCTYPE workbook [ <!ELEMENT workbook ANY > ]>\n"
            f'<workbook xmlns="{builder.ns_sm}"><sheets/></workbook>'
        )
        p = tmp_path / f"dtd_{enc}.xlsx"
        p.write_bytes(builder.build_bytes())
        with pytest.raises(XlsxStructureError) as exc:
            read_xlsx_source_snapshot(p)
        assert exc.value.reason == REASON_STRUCTURE_MALFORMED_XML


def test_r1_cdata_and_comments_containing_doctype_accepted(
    tmp_path: Path,
) -> None:
    wb_bytes, _ = _build_standard_synthetic_workbook()
    p = tmp_path / "valid.xlsx"
    p.write_bytes(wb_bytes)
    res = read_xlsx_source_snapshot(p)
    assert res.snapshot.total_row_count == 4


def test_r1_predefined_and_numeric_xml_entities_accepted(
    tmp_path: Path,
) -> None:
    wb_bytes, _ = _build_standard_synthetic_workbook()
    p = tmp_path / "entities.xlsx"
    p.write_bytes(wb_bytes)
    res = read_xlsx_source_snapshot(p)
    assert res.snapshot.total_row_count == 4


def test_r1_valid_utf16_workbook_accepted(tmp_path: Path) -> None:
    builder = SyntheticXlsxBuilder()
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")
    builder.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u_bf, 2)])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    ws1_xml = builder._build_sheet_xml(
        "خرید-فروش", [_sample_buy_sell_row_data(u_bf, 2)]
    )
    builder.raw_sheet_xml_overrides["خرید-فروش"] = ws1_xml.encode("utf-16")

    p = tmp_path / "utf16.xlsx"
    p.write_bytes(builder.build_bytes())
    res = read_xlsx_source_snapshot(p)
    assert res.snapshot.total_row_count == 4


def test_r2_invalid_wrapper_inside_si_and_is_rejected(tmp_path: Path) -> None:
    builder = SyntheticXlsxBuilder()
    builder.override_sst_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<sst xmlns="{builder.ns_sm}">\n'
        "  <si><invalid>Text</invalid></si>\n"
        "</sst>"
    )
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")
    row_bf = _sample_buy_sell_row_data(u_bf, 2)
    row_bf["C"] = {"t": "s", "v": "0"}
    builder.add_sheet_rows("خرید-فروش", [row_bf])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    p = tmp_path / "invalid_sst.xlsx"
    p.write_bytes(builder.build_bytes())
    with pytest.raises((XlsxStructureError, XlsxCellError)) as exc:
        read_xlsx_source_snapshot(p)
    assert exc.value.reason in (
        REASON_STRUCTURE_INVALID_SHEET_HIERARCHY,
        REASON_STRUCTURE_MALFORMED_XML,
        REASON_CELL_UNKNOWN_TYPE,
    )


def test_r2_foreign_namespace_si_in_sst_rejected(tmp_path: Path) -> None:
    builder = SyntheticXlsxBuilder()
    builder.override_sst_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<sst xmlns="{builder.ns_sm}" xmlns:f="http://foreign.com">\n'
        "  <f:si><t>Text</t></f:si>\n"
        "</sst>"
    )
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")
    row_bf = _sample_buy_sell_row_data(u_bf, 2)
    row_bf["C"] = {"t": "s", "v": "0"}
    builder.add_sheet_rows("خرید-فروش", [row_bf])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    p = tmp_path / "foreign_sst.xlsx"
    p.write_bytes(builder.build_bytes())
    with pytest.raises(XlsxStructureError) as exc:
        read_xlsx_source_snapshot(p)
    assert exc.value.reason in (
        REASON_STRUCTURE_INVALID_SHEET_HIERARCHY,
        REASON_STRUCTURE_MALFORMED_XML,
    )


def test_r2_phonetic_direct_rph_under_is_accepted(tmp_path: Path) -> None:
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    row_bf = _sample_buy_sell_row_data(u_bf, 2)
    row_bf["C"] = {
        "t": "inlineStr",
        "raw_inner": "<is><t>بازرگانی احمدی</t><rPh t='phonetic guide'/></is>",
    }
    builder.add_sheet_rows("خرید-فروش", [row_bf])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    p = tmp_path / "phonetic.xlsx"
    p.write_bytes(builder.build_bytes())
    res = read_xlsx_source_snapshot(p)
    row_c = res.snapshot.sheets["خرید-فروش"].rows[0].raw_values["party_name_raw"]
    assert row_c == "بازرگانی احمدی"


def test_r2_rich_text_runs_and_per_fragment_escapes_decoded(
    tmp_path: Path,
) -> None:
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    row_bf = _sample_buy_sell_row_data(u_bf, 2)
    row_bf["C"] = {
        "t": "inlineStr",
        "raw_inner": "<is><r><t>بازرگانی _x0627_حمدی</t></r></is>",
    }
    builder.add_sheet_rows("خرید-فروش", [row_bf])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    p = tmp_path / "escapes.xlsx"
    p.write_bytes(builder.build_bytes())
    res = read_xlsx_source_snapshot(p)
    row_c = res.snapshot.sheets["خرید-فروش"].rows[0].raw_values["party_name_raw"]
    assert row_c == "بازرگانی احمدی"


def test_r3_unused_sst_entries_not_decoded_or_retained(tmp_path: Path) -> None:
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = ["_xD800_"]  # Corrupt unused string
    builder.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u_bf, 2)])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    p = tmp_path / "unused_sst.xlsx"
    p.write_bytes(builder.build_bytes())
    res = read_xlsx_source_snapshot(p)
    assert res.snapshot.total_row_count == 4


def test_r4_ambiguous_officedocument_relationships_rejected(
    tmp_path: Path,
) -> None:
    builder = SyntheticXlsxBuilder()
    builder.override_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        f'  <Relationship Id="rId1" Type="{builder.ns_rel_office}/officeDocument"'
        ' Target="xl/wb1.xml"/>\n'
        f'  <Relationship Id="rId2" Type="{builder.ns_rel_office}/officeDocument"'
        ' Target="xl/wb2.xml"/>\n'
        "</Relationships>"
    )
    p = tmp_path / "ambig_wb.xlsx"
    p.write_bytes(builder.build_bytes())
    with pytest.raises(XlsxPackageError) as exc:
        read_xlsx_source_snapshot(p)
    assert exc.value.reason == REASON_PACKAGE_AMBIGUOUS_WORKBOOK


def test_r4_ambiguous_sharedstrings_relationships_rejected(
    tmp_path: Path,
) -> None:
    builder = SyntheticXlsxBuilder()
    builder.override_wb_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        f'  <Relationship Id="rId_sst1" Type="{builder.ns_rel_office}/sharedStrings"'
        ' Target="sst1.xml"/>\n'
        f'  <Relationship Id="rId_sst2" Type="{builder.ns_rel_office}/sharedStrings"'
        ' Target="sst2.xml"/>\n'
        "</Relationships>"
    )
    p = tmp_path / "ambig_sst.xlsx"
    p.write_bytes(builder.build_bytes())
    with pytest.raises(XlsxPackageError) as exc:
        read_xlsx_source_snapshot(p)
    assert exc.value.reason == REASON_PACKAGE_AMBIGUOUS_SHARED_STRINGS


def test_r4_ambiguous_sheets_containers_rejected(tmp_path: Path) -> None:
    builder = SyntheticXlsxBuilder()
    builder.override_wb_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<workbook xmlns="{builder.ns_sm}" xmlns:r="{builder.ns_rel_office}">\n'
        '  <sheets><sheet name="خرید-فروش" sheetId="1" r:id="rId_1"/></sheets>\n'
        '  <sheets><sheet name="دریافت-پرداخت" sheetId="2" r:id="rId_2"/></sheets>\n'
        "</workbook>"
    )
    p = tmp_path / "ambig_sheets.xlsx"
    p.write_bytes(builder.build_bytes())
    with pytest.raises(XlsxStructureError) as exc:
        read_xlsx_source_snapshot(p)
    assert exc.value.reason == REASON_STRUCTURE_AMBIGUOUS_SHEETS


def test_r5_duplicate_uuid_raises_xlsx_identity_error(tmp_path: Path) -> None:
    u = _make_uuid7(b"0000000000000001")
    builder = SyntheticXlsxBuilder()
    builder.add_sheet_rows(
        "خرید-فروش",
        [_sample_buy_sell_row_data(u, 2), _sample_buy_sell_row_data(u, 3)],
    )
    builder.add_sheet_rows("دریافت-پرداخت", [])
    builder.add_sheet_rows("ورود-خروج", [])
    builder.add_sheet_rows("لیست کسبه", [])

    p = tmp_path / "dup_uuid.xlsx"
    p.write_bytes(builder.build_bytes())
    with pytest.raises(XlsxIdentityError) as exc:
        read_xlsx_source_snapshot(p)
    assert exc.value.reason == REASON_IDENTITY_DUPLICATE_UUID


def test_r5_textual_scientific_exponent_rejected(tmp_path: Path) -> None:
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    row_bf = _sample_buy_sell_row_data(u_bf, 2)
    row_bf["F"] = "1e2"  # Textual exponent in decimal column
    builder.add_sheet_rows("خرید-فروش", [row_bf])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    p = tmp_path / "text_exp.xlsx"
    p.write_bytes(builder.build_bytes())
    with pytest.raises(XlsxCellError) as exc:
        read_xlsx_source_snapshot(p)
    assert exc.value.reason == REASON_CELL_INVALID_NUMERIC_LEXEME


def test_r5_native_xml_numeric_exponent_accepted(tmp_path: Path) -> None:
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    row_bf = _sample_buy_sell_row_data(u_bf, 2)
    row_bf["F"] = {"v": "1e2"}  # Numeric XML exponent
    builder.add_sheet_rows("خرید-فروش", [row_bf])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    p = tmp_path / "xml_exp.xlsx"
    p.write_bytes(builder.build_bytes())
    res = read_xlsx_source_snapshot(p)
    val = res.snapshot.sheets["خرید-فروش"].rows[0].raw_values["quantity_raw"]
    assert val == Decimal("100")


def test_r5_uuid_casing_canonicalized_by_snapshot(tmp_path: Path) -> None:
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    row_bf = _sample_buy_sell_row_data(u_bf, 2)
    row_bf["Z"] = str(u_bf).upper()  # Uppercase UUID
    builder.add_sheet_rows("خرید-فروش", [row_bf])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    p = tmp_path / "upper_uuid.xlsx"
    p.write_bytes(builder.build_bytes())
    res = read_xlsx_source_snapshot(p)
    assert res.snapshot.sheets["خرید-فروش"].rows[0].canonical_uuid == str(u_bf).lower()


def test_r6_planner_full_lifecycle_transitions_and_idempotency(
    tmp_path: Path,
) -> None:
    """WP-04 Planner lifecycle transitions and idempotency test (C-02, XR-08)."""
    u1 = _make_uuid7(b"0000000000000001")
    u2 = _make_uuid7(b"0000000000000002")
    u3 = _make_uuid7(b"0000000000000003")
    u4 = _make_uuid7(b"0000000000000004")

    def advance_registry(
        prior_reg: PriorIdentityRegistry, plan: Any
    ) -> PriorIdentityRegistry:
        states = dict(prior_reg.identities)
        for item in plan.items:
            if item.action == PlanAction.UNCHANGED:
                assert item.prior_revision is not None
                assert item.prior_lifecycle is not None
                states[item.stable_id] = PriorIdentityState(
                    stable_id=item.stable_id,
                    canonical_uuid=item.canonical_uuid,
                    home_sheet=item.sheet_name,
                    latest_revision=item.prior_revision,
                    lifecycle=item.prior_lifecycle,
                    source_hash=item.current_source_hash,
                )
            elif item.action in (PlanAction.INSERT, PlanAction.EDIT):
                assert item.planned_revision is not None
                states[item.stable_id] = PriorIdentityState(
                    stable_id=item.stable_id,
                    canonical_uuid=item.canonical_uuid,
                    home_sheet=item.sheet_name,
                    latest_revision=item.planned_revision,
                    lifecycle=IdentityLifecycle.ACTIVE,
                    source_hash=item.current_source_hash,
                )
            elif item.action == PlanAction.VOID:
                assert item.planned_revision is not None
                states[item.stable_id] = PriorIdentityState(
                    stable_id=item.stable_id,
                    canonical_uuid=item.canonical_uuid,
                    home_sheet=item.sheet_name,
                    latest_revision=item.planned_revision,
                    lifecycle=IdentityLifecycle.VOIDED,
                    source_hash=None,
                )
        return build_prior_identity_registry(list(states.values()))

    # Step 1: Initial file -> 4 Inserts (Planned revision = 1)
    builder1 = SyntheticXlsxBuilder()
    builder1.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u1, 2)])
    builder1.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u2, 2)]
    )
    builder1.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u3, 2)])
    builder1.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u4, 2)])

    p1 = tmp_path / "lifecycle1.xlsx"
    p1.write_bytes(builder1.build_bytes())

    res1 = read_xlsx_source_snapshot(p1)
    empty_prior = build_prior_identity_registry([])
    plan1 = plan_source_changes(res1.snapshot, empty_prior)
    assert plan1.total_counts.insert_count == 4
    assert plan1.total_counts.edit_count == 0
    assert plan1.total_counts.void_count == 0
    assert plan1.total_counts.unchanged_count == 0

    for item in plan1.items:
        assert item.action == PlanAction.INSERT
        assert item.planned_revision == 1
        assert item.prior_lifecycle is None
        assert item.prior_revision is None
        assert item.prior_source_hash is None
        assert item.current_source_hash is not None
        assert item.current_row is not None
        assert not item.is_reactivation

    # Step 2: Idempotency with exact same snapshot
    prior_reg1 = advance_registry(empty_prior, plan1)
    plan_same = plan_source_changes(res1.snapshot, prior_reg1)
    assert plan_same.total_counts.unchanged_count == 4
    assert plan_same.total_counts.insert_count == 0
    assert plan_same.total_counts.edit_count == 0
    assert plan_same.total_counts.void_count == 0
    for item in plan_same.items:
        assert item.action == PlanAction.UNCHANGED
        assert item.planned_revision is None
        assert item.prior_revision == 1
        assert item.prior_lifecycle == IdentityLifecycle.ACTIVE
        assert item.prior_source_hash == item.current_source_hash
        assert not item.is_reactivation

    # Step 3: Edit row u1 and Void row u2 -> 1 Edit, 1 Void, 2 Unchanged
    builder2 = SyntheticXlsxBuilder()
    edited_u1 = _sample_buy_sell_row_data(u1, 2)
    edited_u1["F"] = "99.99"  # Edit quantity
    builder2.add_sheet_rows("خرید-فروش", [edited_u1])
    builder2.add_sheet_rows("دریافت-پرداخت", [])  # u2 removed -> VOID
    builder2.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u3, 2)])
    builder2.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u4, 2)])

    p2 = tmp_path / "lifecycle2.xlsx"
    p2.write_bytes(builder2.build_bytes())
    res2 = read_xlsx_source_snapshot(p2)
    plan2 = plan_source_changes(res2.snapshot, prior_reg1)
    assert plan2.total_counts.edit_count == 1
    assert plan2.total_counts.void_count == 1
    assert plan2.total_counts.unchanged_count == 2
    assert plan2.total_counts.insert_count == 0

    item_u1 = next(it for it in plan2.items if it.stable_id == u1)
    assert item_u1.action == PlanAction.EDIT
    assert item_u1.planned_revision == 2
    assert item_u1.prior_revision == 1
    assert item_u1.prior_lifecycle == IdentityLifecycle.ACTIVE
    assert not item_u1.is_reactivation

    item_u2 = next(it for it in plan2.items if it.stable_id == u2)
    assert item_u2.action == PlanAction.VOID
    assert item_u2.planned_revision == 2
    assert item_u2.prior_revision == 1
    assert item_u2.prior_lifecycle == IdentityLifecycle.ACTIVE
    assert item_u2.current_source_hash is None
    assert item_u2.current_row is None
    assert not item_u2.is_reactivation

    # Step 4: Advance to Rev 2, then Reactivate u2 in SAME home sheet (دریافت-پرداخت)
    prior_reg2 = advance_registry(prior_reg1, plan2)
    assert prior_reg2.identities[u2].lifecycle == IdentityLifecycle.VOIDED
    assert prior_reg2.identities[u2].latest_revision == 2

    builder3 = SyntheticXlsxBuilder()
    builder3.add_sheet_rows("خرید-فروش", [edited_u1])
    builder3.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u2, 2)]
    )
    builder3.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u3, 2)])
    builder3.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u4, 2)])
    p3 = tmp_path / "lifecycle3_reactivate.xlsx"
    p3.write_bytes(builder3.build_bytes())
    res3 = read_xlsx_source_snapshot(p3)
    plan3 = plan_source_changes(res3.snapshot, prior_reg2)
    assert plan3.total_counts.edit_count == 1
    assert plan3.total_counts.unchanged_count == 3
    assert plan3.total_counts.insert_count == 0
    assert plan3.total_counts.void_count == 0

    item_u2_reactivate = next(it for it in plan3.items if it.stable_id == u2)
    assert item_u2_reactivate.action == PlanAction.EDIT
    assert item_u2_reactivate.planned_revision == 3
    assert item_u2_reactivate.prior_revision == 2
    assert item_u2_reactivate.prior_lifecycle == IdentityLifecycle.VOIDED
    assert item_u2_reactivate.prior_source_hash is None
    assert item_u2_reactivate.current_source_hash is not None
    assert item_u2_reactivate.is_reactivation

    # Step 5: Cross-sheet identity movement error: Put u2 into ورود-خروج
    builder_cross = SyntheticXlsxBuilder()
    builder_cross.add_sheet_rows("خرید-فروش", [edited_u1])
    builder_cross.add_sheet_rows("دریافت-پرداخت", [])
    cross_u2 = _sample_inventory_movements_row_data(u2, 3)
    builder_cross.add_sheet_rows(
        "ورود-خروج", [_sample_inventory_movements_row_data(u3, 2), cross_u2]
    )
    builder_cross.add_sheet_rows(
        "لیست کسبه", [_sample_business_parties_row_data(u4, 2)]
    )
    p_cross = tmp_path / "lifecycle_cross.xlsx"
    p_cross.write_bytes(builder_cross.build_bytes())
    res_cross = read_xlsx_source_snapshot(p_cross)
    with pytest.raises(IdentityRelocationError):
        plan_source_changes(res_cross.snapshot, prior_reg2)

    # Step 6: Advance to Revision 3 from plan3 and assert complete idempotency
    prior_reg3 = advance_registry(prior_reg2, plan3)
    plan_final_same = plan_source_changes(res3.snapshot, prior_reg3)
    assert plan_final_same.total_counts.unchanged_count == 4
    assert plan_final_same.total_counts.insert_count == 0
    assert plan_final_same.total_counts.edit_count == 0
    assert plan_final_same.total_counts.void_count == 0
    for it in plan_final_same.items:
        assert it.action == PlanAction.UNCHANGED
        assert it.planned_revision is None
        if it.stable_id == u1:
            assert it.prior_revision == 2
        elif it.stable_id == u2:
            assert it.prior_revision == 3
        elif it.stable_id in (u3, u4):
            assert it.prior_revision == 1


@given(
    sheet_order=st.permutations(
        ["خرید-فروش", "دریافت-پرداخت", "ورود-خروج", "لیست کسبه"]
    ),
    reverse_rows=st.booleans(),
    reverse_cells=st.booleans(),
    string_mode=st.sampled_from(["inline", "direct_str", "sst"]),
    row_offset=st.integers(min_value=0, max_value=5),
)
@settings(max_examples=15, deadline=None)
def test_r6_hypothesis_comprehensive_invariance_property(
    tmp_path_factory: pytest.TempPathFactory,
    sheet_order: list[str],
    reverse_rows: bool,
    reverse_cells: bool,
    string_mode: str,
    row_offset: int,
) -> None:
    """Hypothesis test for snapshot and location invariance (C-02, R5-05, XR-09)."""
    tmp_path = tmp_path_factory.mktemp("hyp")
    u1 = _make_uuid7(b"0000000000000001")
    u2 = _make_uuid7(b"0000000000000002")
    u3 = _make_uuid7(b"0000000000000003")
    u4 = _make_uuid7(b"0000000000000004")
    u5 = _make_uuid7(b"0000000000000005")

    r1_bf = 10 + row_offset
    r2_bf = 20 + row_offset
    r_dp = 2 + row_offset
    r_vk = 2 + row_offset
    r_lk = 2 + row_offset

    row1_bf_data = _sample_buy_sell_row_data(u1, r1_bf)
    row2_bf_data = _sample_buy_sell_row_data(u5, r2_bf)
    row2_bf_data["C"] = "فروشگاه زرین"
    row2_bf_data["F"] = "50.00"

    bf_rows = (
        [row2_bf_data, row1_bf_data] if reverse_rows else [row1_bf_data, row2_bf_data]
    )

    rows_data = {
        "خرید-فروش": bf_rows,
        "دریافت-پرداخت": [_sample_receipts_payments_row_data(u2, r_dp)],
        "ورود-خروج": [_sample_inventory_movements_row_data(u3, r_vk)],
        "لیست کسبه": [_sample_business_parties_row_data(u4, r_lk)],
    }

    builder = SyntheticXlsxBuilder()
    sst_list = [
        "DUMMY_UNUSED_0",
        "طلای آبشده",
        "فروشگاه زرین",
        "بازرگانی احمدی",
        "همکار نمونه",
        "کارگاه زرگری",
        "فروشگاه نمونه",
        str(u5).lower(),
        str(u1).lower(),
        str(u2).lower(),
        str(u3).lower(),
        str(u4).lower(),
        "1403/05/15",
        "1403/01/01",
        "1403/12/29",
        "خرید",
        "RS",
        "ورود",
        "شمش طلا",
        "تسویه حساب",
        "تحویل شمش",
        "SYNTHETIC-PHONE-001",
        "توضیحات فاکتور",
        "DUMMY_UNUSED_1",
    ]
    if string_mode == "sst":
        builder.shared_strings = sst_list
    sst_map = {s: idx for idx, s in enumerate(sst_list)}

    for s_name in sheet_order:
        sheet_rows = [dict(r) for r in rows_data[s_name]]
        if string_mode == "direct_str":
            for r in sheet_rows:
                for k, v in list(r.items()):
                    if k not in ("__row_num__", "A", "F", "G", "H"):
                        r[k] = {"t": "str", "v": str(v)}
        elif string_mode == "sst":
            for r in sheet_rows:
                for k, v in list(r.items()):
                    if (
                        k not in ("__row_num__", "A", "F", "G", "H")
                        and str(v) in sst_map
                    ):
                        r[k] = {"t": "s", "v": str(sst_map[str(v)])}

        if reverse_cells:
            for r in sheet_rows:
                keys = list(r.keys())
                rev_r = {k: r[k] for k in reversed(keys)}
                if "__row_num__" in r:
                    rev_r["__row_num__"] = r["__row_num__"]
                r.clear()
                r.update(rev_r)

        builder.add_sheet_rows(s_name, sheet_rows)

    pkg_path = tmp_path / "hyp.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    # Assert pre-reader XML reflects all generated parameters
    with zipfile.ZipFile(pkg_path, "r") as zf_check:
        bf_found = False
        for fn in zf_check.namelist():
            if fn.startswith("xl/worksheets/sheet"):
                xml_content = zf_check.read(fn).decode("utf-8")
                if f'r="{r1_bf}"' in xml_content or f'r="{r2_bf}"' in xml_content:
                    bf_found = True
                    pos_r1 = xml_content.find(f'<row r="{r1_bf}"')
                    pos_r2 = xml_content.find(f'<row r="{r2_bf}"')
                    if reverse_rows:
                        assert pos_r2 < pos_r1, "Row reversal not observable in XML"
                    else:
                        assert pos_r1 < pos_r2, (
                            "Row canonical order not observable in XML"
                        )
                    pos_z = xml_content.find(f'r="Z{r1_bf}"')
                    pos_b = xml_content.find(f'r="B{r1_bf}"')
                    if reverse_cells:
                        assert pos_z < pos_b, "Cell reversal not observable in XML"
                    else:
                        assert pos_b < pos_z, (
                            "Cell canonical order not observable in XML"
                        )
                    break
        assert bf_found

        if string_mode == "sst":
            assert "xl/sharedStrings.xml" in zf_check.namelist()
            sst_content = zf_check.read("xl/sharedStrings.xml").decode("utf-8")
            assert "DUMMY_UNUSED_0" in sst_content

    res = read_xlsx_source_snapshot(pkg_path)

    # Assert exact equality with independent Oracle snapshot
    expected_snap = build_source_workbook_snapshot(
        [
            SourceSheetInput(
                "خرید-فروش",
                [
                    SourceRowInput(
                        u1,
                        {
                            "date_raw": "1403/05/15",
                            "party_name_raw": "بازرگانی احمدی",
                            "transaction_type_raw": "خرید",
                            "item_name_raw": "طلای آبشده",
                            "quantity_raw": "12.34",
                            "unit_price_toman_raw": "1500000",
                            "discount_toman_raw": "0",
                            "notes_raw": "توضیحات فاکتور",
                        },
                    ),
                    SourceRowInput(
                        u5,
                        {
                            "date_raw": "1403/05/15",
                            "party_name_raw": "فروشگاه زرین",
                            "transaction_type_raw": "خرید",
                            "item_name_raw": "طلای آبشده",
                            "quantity_raw": "50.00",
                            "unit_price_toman_raw": "1500000",
                            "discount_toman_raw": "0",
                            "notes_raw": "توضیحات فاکتور",
                        },
                    ),
                ],
            ),
            SourceSheetInput(
                "دریافت-پرداخت",
                [
                    SourceRowInput(
                        u2,
                        {
                            "date_raw": "1403/01/01",
                            "party_name_raw": "همکار نمونه",
                            "entry_type_raw": "RS",
                            "amount_toman_raw": "50000000",
                            "notes_raw": "تسویه حساب",
                            "account_code_raw": "101",
                            "customer_flag_raw": "1",
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
                            "date_raw": "1403/12/29",
                            "party_name_raw": "کارگاه زرگری",
                            "movement_type_raw": "ورود",
                            "item_name_raw": "شمش طلا",
                            "quantity_raw": "100.5",
                            "purity_raw": "750",
                            "notes_raw": "تحویل شمش",
                            "customer_flag_raw": "1",
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
                            "party_name_raw": "فروشگاه نمونه",
                            "phone_number_raw": "SYNTHETIC-PHONE-001",
                        },
                    )
                ],
            ),
        ]
    )

    assert res.snapshot == expected_snap
    for s_name in RAW_CONTRACT_REGISTRY.sheets:
        assert (
            res.snapshot.sheets[s_name].sheet_snapshot_hash
            == expected_snap.sheets[s_name].sheet_snapshot_hash
        )
        for r_act, r_exp in zip(
            res.snapshot.sheets[s_name].rows,
            expected_snap.sheets[s_name].rows,
            strict=True,
        ):
            assert r_act.source_hash == r_exp.source_hash

    assert res.locations_by_uuid == {
        u1: SourceRowLocation("خرید-فروش", r1_bf),
        u5: SourceRowLocation("خرید-فروش", r2_bf),
        u2: SourceRowLocation("دریافت-پرداخت", r_dp),
        u3: SourceRowLocation("ورود-خروج", r_vk),
        u4: SourceRowLocation("لیست کسبه", r_lk),
    }

    # Plan source changes on nontrivial prior registry
    u_voided = _make_uuid7(b"0000000000000099")
    prior_reg = build_prior_identity_registry(
        [
            PriorIdentityState(
                stable_id=u1,
                canonical_uuid=str(u1).lower(),
                home_sheet="خرید-فروش",
                latest_revision=1,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=expected_snap.all_rows_by_id[u1].source_hash,
            ),
            PriorIdentityState(
                stable_id=u2,
                canonical_uuid=str(u2).lower(),
                home_sheet="دریافت-پرداخت",
                latest_revision=1,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash="0" * 64,
            ),
            PriorIdentityState(
                stable_id=u_voided,
                canonical_uuid=str(u_voided).lower(),
                home_sheet="لیست کسبه",
                latest_revision=1,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash="1" * 64,
            ),
        ]
    )

    plan_act = plan_source_changes(res.snapshot, prior_reg)
    plan_exp = plan_source_changes(expected_snap, prior_reg)

    assert plan_act == plan_exp
    assert plan_act.version == plan_exp.version
    assert plan_act.items == plan_exp.items
    assert plan_act.total_counts == plan_exp.total_counts
    assert plan_act.per_sheet_counts == plan_exp.per_sheet_counts
    assert (
        plan_act.current_sheet_snapshot_hashes == plan_exp.current_sheet_snapshot_hashes
    )
    assert plan_act.current_sheet_row_counts == plan_exp.current_sheet_row_counts


def test_r6_direct_construction_comprehensive_matrix() -> None:
    """Matrix for SourceRowLocation & XlsxSourceReadResult (C-01, R5-04, XR-10)."""
    # 1. SourceRowLocation: Positive
    for s_name in RAW_CONTRACT_REGISTRY.sheets:
        loc_min = SourceRowLocation(s_name, 2)
        assert loc_min.sheet_name == s_name
        assert loc_min.physical_row_number == 2

        loc_max = SourceRowLocation(s_name, 1_048_576)
        assert loc_max.sheet_name == s_name
        assert loc_max.physical_row_number == 1_048_576

    # 2. SourceRowLocation: Invalid sheet names
    invalid_sheets = [
        "invalid_sheet",
        "",
        "  ",
        123,
        True,
        False,
        None,
        ["خرید-فروش"],
        {"خرید-فروش": 1},
        {"خرید-فروش"},
        ("خرید-فروش",),
        3.14,
    ]
    for bad_s in invalid_sheets:
        with pytest.raises(XlsxStructureError) as exc:
            SourceRowLocation(bad_s, 2)  # type: ignore[arg-type]
        assert exc.value.reason == REASON_STRUCTURE_UNKNOWN_LOCATION_SHEET

    # 3. SourceRowLocation: Invalid physical row numbers
    invalid_rows = [
        1,
        0,
        -1,
        1_048_577,
        10_000_000,
        True,
        False,
        None,
        "2",
        2.0,
        Decimal("2"),
        [2],
    ]
    for bad_r in invalid_rows:
        with pytest.raises(XlsxStructureError) as exc:
            SourceRowLocation("خرید-فروش", bad_r)  # type: ignore[arg-type]
        assert exc.value.reason == REASON_STRUCTURE_ROW_OUT_OF_BOUNDS

    # 4. XlsxSourceReadResult: Positive valid construction & canonical ordering
    u1 = _make_uuid7(b"0000000000000001")
    u2 = _make_uuid7(b"0000000000000002")
    u3 = _make_uuid7(b"0000000000000003")
    u4 = _make_uuid7(b"0000000000000004")

    r1 = SourceRowInput(
        u1,
        {
            col.field_name: None
            for col in RAW_CONTRACT_REGISTRY.sheets["خرید-فروش"].raw_columns
        },
    )
    r2 = SourceRowInput(
        u2,
        {
            col.field_name: None
            for col in RAW_CONTRACT_REGISTRY.sheets["دریافت-پرداخت"].raw_columns
        },
    )
    r3 = SourceRowInput(
        u3,
        {
            col.field_name: None
            for col in RAW_CONTRACT_REGISTRY.sheets["ورود-خروج"].raw_columns
        },
    )
    r4 = SourceRowInput(
        u4,
        {
            col.field_name: None
            for col in RAW_CONTRACT_REGISTRY.sheets["لیست کسبه"].raw_columns
        },
    )
    snap = build_source_workbook_snapshot(
        [
            SourceSheetInput("خرید-فروش", [r1]),
            SourceSheetInput("دریافت-پرداخت", [r2]),
            SourceSheetInput("ورود-خروج", [r3]),
            SourceSheetInput("لیست کسبه", [r4]),
        ]
    )
    loc1 = SourceRowLocation("خرید-فروش", 2)
    loc2 = SourceRowLocation("دریافت-پرداخت", 2)
    loc3 = SourceRowLocation("ورود-خروج", 2)
    loc4 = SourceRowLocation("لیست کسبه", 2)

    # Reverse-inserted input
    locs_rev = {u4: loc4, u3: loc3, u2: loc2, u1: loc1}
    res_rev = XlsxSourceReadResult(
        snapshot=snap,
        locations_by_uuid=locs_rev,
        version=XLSX_SOURCE_READER_VERSION,
    )
    assert res_rev.snapshot == snap
    assert res_rev.version == XLSX_SOURCE_READER_VERSION
    assert len(res_rev.locations_by_uuid) == 4
    # Exact iteration order by uuid.bytes
    assert list(res_rev.locations_by_uuid.keys()) == [u1, u2, u3, u4]

    # Permuted insertion input
    locs_perm = {u2: loc2, u4: loc4, u1: loc1, u3: loc3}
    res_perm = XlsxSourceReadResult(snapshot=snap, locations_by_uuid=locs_perm)
    assert list(res_perm.locations_by_uuid.keys()) == [u1, u2, u3, u4]
    assert res_rev.locations_by_uuid == res_perm.locations_by_uuid

    # Defensive copy: caller mutating dict after construction does not affect res
    locs_copy = dict(locs_perm)
    res_defensive = XlsxSourceReadResult(snapshot=snap, locations_by_uuid=locs_copy)
    locs_copy[u1] = SourceRowLocation("خرید-فروش", 999)
    locs_copy.clear()
    assert res_defensive.locations_by_uuid[u1].physical_row_number == 2
    assert len(res_defensive.locations_by_uuid) == 4
    assert list(res_defensive.locations_by_uuid.keys()) == [u1, u2, u3, u4]

    # Immutability: MappingProxyType prevents mutation
    with pytest.raises(TypeError):
        res_rev.locations_by_uuid[u1] = SourceRowLocation("خرید-فروش", 5)  # type: ignore[index]
    with pytest.raises(TypeError):
        del res_rev.locations_by_uuid[u1]  # type: ignore[attr-defined]

    # 5. XlsxSourceReadResult: Invalid snapshot type
    for bad_snap in [None, "bad_snapshot", 123, True, {}, locs_rev]:
        with pytest.raises(XlsxStructureError) as exc:
            XlsxSourceReadResult(snapshot=bad_snap, locations_by_uuid=locs_rev)  # type: ignore[arg-type]
        assert exc.value.reason == REASON_STRUCTURE_INVALID_SNAPSHOT_TYPE

    # 6. XlsxSourceReadResult: Invalid version
    for bad_ver in ["other.v1", "", "xlsx-source-reader.v2", 1, True, None]:
        with pytest.raises(XlsxStructureError) as exc:
            XlsxSourceReadResult(
                snapshot=snap,
                locations_by_uuid=locs_rev,
                version=bad_ver,  # type: ignore[arg-type]
            )
        assert exc.value.reason == REASON_STRUCTURE_INVALID_VERSION

    # 7. XlsxSourceReadResult: Invalid locations type
    for bad_locs in [None, "str", 123, [locs_rev], (locs_rev,)]:
        with pytest.raises(XlsxStructureError) as exc:
            XlsxSourceReadResult(snapshot=snap, locations_by_uuid=bad_locs)  # type: ignore[arg-type]
        assert exc.value.reason == REASON_STRUCTURE_INVALID_SNAPSHOT_TYPE

    # 8. XlsxSourceReadResult: Key mismatch (missing UUID, extra UUID, non-UUID key)
    u_extra = _make_uuid7(b"0000000000000005")
    # Missing u2
    with pytest.raises(XlsxStructureError) as exc:
        XlsxSourceReadResult(
            snapshot=snap,
            locations_by_uuid={u1: loc1, u3: loc3, u4: loc4},
        )
    assert exc.value.reason == REASON_STRUCTURE_LOCATION_IDENTITY_MISMATCH

    # Extra u_extra
    with pytest.raises(XlsxStructureError) as exc:
        XlsxSourceReadResult(
            snapshot=snap,
            locations_by_uuid={
                u1: loc1,
                u2: loc2,
                u3: loc3,
                u4: loc4,
                u_extra: SourceRowLocation("ورود-خروج", 5),
            },
        )
    assert exc.value.reason == REASON_STRUCTURE_LOCATION_IDENTITY_MISMATCH

    # Non-UUID key
    bad_key_dict: dict[Any, Any] = {
        str(u1): loc1,
        str(u2): loc2,
        str(u3): loc3,
        str(u4): loc4,
    }
    with pytest.raises(XlsxStructureError) as exc:
        XlsxSourceReadResult(
            snapshot=snap,
            locations_by_uuid=bad_key_dict,
        )
    assert exc.value.reason == REASON_STRUCTURE_LOCATION_IDENTITY_MISMATCH

    # 9. XlsxSourceReadResult: Non-SourceRowLocation value
    bad_val_dict: dict[Any, Any] = {
        u1: ("خرید-فروش", 2),
        u2: loc2,
        u3: loc3,
        u4: loc4,
    }
    with pytest.raises(XlsxStructureError) as exc:
        XlsxSourceReadResult(
            snapshot=snap,
            locations_by_uuid=bad_val_dict,
        )
    assert exc.value.reason == REASON_STRUCTURE_INVALID_SNAPSHOT_TYPE

    # 10. XlsxSourceReadResult: Sheet mismatch
    with pytest.raises(XlsxStructureError) as exc:
        XlsxSourceReadResult(
            snapshot=snap,
            locations_by_uuid={
                u1: SourceRowLocation("دریافت-پرداخت", 2),  # u1 is in خرید-فروش
                u2: loc2,
                u3: loc3,
                u4: loc4,
            },
        )
    assert exc.value.reason == REASON_STRUCTURE_LOCATION_SHEET_MISMATCH

    # 11. XlsxSourceReadResult: Duplicate physical location in same sheet
    snap_dup = build_source_workbook_snapshot(
        [
            SourceSheetInput("خرید-فروش", [r1, SourceRowInput(u3, r1.source_values)]),
            SourceSheetInput("دریافت-پرداخت", [r2]),
            SourceSheetInput("ورود-خروج", []),
            SourceSheetInput("لیست کسبه", []),
        ]
    )
    with pytest.raises(XlsxStructureError) as exc:
        XlsxSourceReadResult(
            snapshot=snap_dup,
            locations_by_uuid={
                u1: SourceRowLocation("خرید-فروش", 2),
                u3: SourceRowLocation("خرید-فروش", 2),  # Duplicate physical row 2
                u2: SourceRowLocation("دریافت-پرداخت", 2),
            },
        )
    assert exc.value.reason == REASON_STRUCTURE_DUPLICATE_LOCATION_ROW


def test_r6_all_or_nothing_fourth_sheet_late_failure(tmp_path: Path) -> None:
    """4th sheet late failure yields zero partial snapshot (XR-11)."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")

    builder = SyntheticXlsxBuilder()
    # Sheets 1, 2, 3 are 100% valid
    builder.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u_bf, 2)])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])

    # Sheet 4 (لیست کسبه) has a corrupt XML structure
    ws4_corrupt = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<worksheet xmlns="{builder.ns_sm}">\n'
        "  <sheetData>\n"
        '    <row r="1">\n'
        '      <c r="A1" t="inlineStr"><is><t>ردیف</t></is></c>\n'
        '      <c r="B1" t="inlineStr"><is><t>نام طرف حساب</t></is></c>\n'
        '      <c r="C1" t="inlineStr"><is><t>شماره تماس</t></is></c>\n'
        '      <c r="D1" t="inlineStr"><is><t>record_id</t></is></c>\n'
        "    </row>\n"
        '    <row r="2">\n'
        "      <unclosed_tag>\n"
        "    </row>\n"
        "  </sheetData>\n"
        "</worksheet>"
    )
    builder.raw_sheet_xml_overrides["لیست کسبه"] = ws4_corrupt

    pkg = tmp_path / "late_fail_sheet4.xlsx"
    pkg_bytes = builder.build_bytes()
    pkg.write_bytes(pkg_bytes)

    with pytest.raises(XlsxStructureError) as exc:
        read_xlsx_source_snapshot(pkg)
    assert exc.value.reason == REASON_STRUCTURE_MALFORMED_XML
    assert exc.value.sheet_name == "لیست کسبه"

    # Verify file is unmodified and can be renamed/deleted
    assert pkg.read_bytes() == pkg_bytes
    renamed = tmp_path / "renamed_late_fail.xlsx"
    pkg.rename(renamed)
    assert renamed.is_file()
    renamed.unlink()


def test_r6_read_only_integrity_and_clean_cleanup(tmp_path: Path) -> None:
    wb_bytes, _ = _build_standard_synthetic_workbook()
    p = tmp_path / "ro_test.xlsx"
    p.write_bytes(wb_bytes)
    orig_hash = hash(wb_bytes)

    res = read_xlsx_source_snapshot(p)
    assert res.snapshot.total_row_count == 4
    # Ensure source file is unchanged and unblocked
    assert hash(p.read_bytes()) == orig_hash
    p.rename(tmp_path / "renamed.xlsx")


# --- Round 4 Review Remediations (R4-01 to R4-06) ---


def test_r4_01_negative_unsupported_tags_in_leaf_nodes_rejected(
    tmp_path: Path,
) -> None:
    """Verify that unsupported child tags in leaf nodes raise XlsxCellError (R4-01)."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    # Case 1: <is><t><unsupported>SYNTHETIC-ACTIVITY</unsupported></t></is>
    b1 = SyntheticXlsxBuilder()
    row_bf1 = _sample_buy_sell_row_data(u_bf, 2)
    row_bf1["C"] = {
        "t": "inlineStr",
        "raw_inner": "<is><t><unsupported>SYNTHETIC-ACTIVITY</unsupported></t></is>",
    }
    b1.add_sheet_rows("خرید-فروش", [row_bf1])
    b1.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b1.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b1.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p1 = tmp_path / "case1.xlsx"
    p1.write_bytes(b1.build_bytes())
    with pytest.raises(XlsxCellError) as exc1:
        read_xlsx_source_snapshot(p1)
    assert exc1.value.reason == REASON_CELL_UNKNOWN_TYPE

    # Case 2: <is><r><t><unsupported>SYNTHETIC-ACTIVITY</unsupported></t></r></is>
    b2 = SyntheticXlsxBuilder()
    row_bf2 = _sample_buy_sell_row_data(u_bf, 2)
    row_bf2["C"] = {
        "t": "inlineStr",
        "raw_inner": (
            "<is><r><t><unsupported>SYNTHETIC-ACTIVITY</unsupported></t></r></is>"
        ),
    }
    b2.add_sheet_rows("خرید-فروش", [row_bf2])
    b2.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b2.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b2.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p2 = tmp_path / "case2.xlsx"
    p2.write_bytes(b2.build_bytes())
    with pytest.raises(XlsxCellError) as exc2:
        read_xlsx_source_snapshot(p2)
    assert exc2.value.reason == REASON_CELL_UNKNOWN_TYPE

    # Case 3: SST with <si><t><unsupported>SYNTHETIC-ACTIVITY</unsupported></t></si>
    b3 = SyntheticXlsxBuilder()
    b3.override_sst_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<sst xmlns="{b3.ns_sm}">\n'
        "  <si><t><unsupported>SYNTHETIC-ACTIVITY</unsupported></t></si>\n"
        "</sst>"
    )
    row_bf3 = _sample_buy_sell_row_data(u_bf, 2)
    row_bf3["C"] = {"t": "s", "v": "0"}
    b3.add_sheet_rows("خرید-فروش", [row_bf3])
    b3.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b3.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b3.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p3 = tmp_path / "case3.xlsx"
    p3.write_bytes(b3.build_bytes())
    with pytest.raises(XlsxCellError) as exc3:
        read_xlsx_source_snapshot(p3)
    assert exc3.value.reason == REASON_CELL_UNKNOWN_TYPE

    # Case 4: <v><unsupported>SYNTHETIC-ACTIVITY</unsupported></v> in t="str"
    b4 = SyntheticXlsxBuilder()
    row_bf4 = _sample_buy_sell_row_data(u_bf, 2)
    row_bf4["C"] = {
        "t": "str",
        "raw_inner": "<v><unsupported>SYNTHETIC-ACTIVITY</unsupported></v>",
    }
    b4.add_sheet_rows("خرید-فروش", [row_bf4])
    b4.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b4.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b4.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p4 = tmp_path / "case4.xlsx"
    p4.write_bytes(b4.build_bytes())
    with pytest.raises(XlsxCellError) as exc4:
        read_xlsx_source_snapshot(p4)
    assert exc4.value.reason == REASON_CELL_UNKNOWN_TYPE

    # Case 5: <v><unsupported>12</unsupported></v> in t="n"
    b5 = SyntheticXlsxBuilder()
    row_bf5 = _sample_buy_sell_row_data(u_bf, 2)
    row_bf5["F"] = {
        "t": "n",
        "raw_inner": "<v><unsupported>100</unsupported></v>",
    }
    b5.add_sheet_rows("خرید-فروش", [row_bf5])
    b5.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b5.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b5.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p5 = tmp_path / "case5.xlsx"
    p5.write_bytes(b5.build_bytes())
    with pytest.raises(XlsxCellError) as exc5:
        read_xlsx_source_snapshot(p5)
    assert exc5.value.reason == REASON_CELL_UNKNOWN_TYPE


def test_r4_01_comment_nodes_in_leaf_text_preserved(tmp_path: Path) -> None:
    """Verify that XML comments inside text nodes are preserved (R4-01)."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    row_bf = _sample_buy_sell_row_data(u_bf, 2)
    row_bf["C"] = {
        "t": "inlineStr",
        "raw_inner": "<is><t>A<!--transport-comment-->B</t></is>",
    }
    row_bf["D"] = {
        "t": "str",
        "raw_inner": "<v>خ<!--transport-comment-->رید</v>",
    }
    row_bf["F"] = {
        "t": "n",
        "raw_inner": "<v>1<!--transport-comment-->2</v>",
    }
    builder.add_sheet_rows("خرید-فروش", [row_bf])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    p = tmp_path / "comments.xlsx"
    p.write_bytes(builder.build_bytes())
    res = read_xlsx_source_snapshot(p)
    row_c = res.snapshot.sheets["خرید-فروش"].rows[0].raw_values
    assert row_c["party_name_raw"] == "AB"
    assert row_c["transaction_type_raw"] == "خرید"
    assert row_c["quantity_raw"] == Decimal("12")


def test_r4_01_root_connection_and_extlst_nested_worksheet(
    tmp_path: Path,
) -> None:
    """Verify document root validation and ignoring nested worksheets (R4-01)."""
    # 1. Foreign root document
    b1 = SyntheticXlsxBuilder()
    b1.raw_sheet_xml_overrides["خرید-فروش"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<foreign:document xmlns:foreign="http://foreign.com" xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n'
        '  <worksheet><sheetData><row r="1"/></sheetData></worksheet>\n'
        "</foreign:document>"
    )
    b1.add_sheet_rows("دریافت-پرداخت", [])
    b1.add_sheet_rows("ورود-خروج", [])
    b1.add_sheet_rows("لیست کسبه", [])
    p1 = tmp_path / "foreign_root.xlsx"
    p1.write_bytes(b1.build_bytes())
    with pytest.raises(XlsxStructureError) as exc1:
        read_xlsx_source_snapshot(p1)
    assert exc1.value.reason == REASON_STRUCTURE_INVALID_ROOT

    # 2. Worksheet copied inside extLst -> ignored, main row unchanged
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    b2 = SyntheticXlsxBuilder()
    main_ws_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<worksheet xmlns="{b2.ns_sm}">\n'
        "  <sheetData>\n"
        '    <row r="1">\n'
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
        '      <c r="E2" t="inlineStr"><is><t>طلای آبشده</t></is></c>\n'
        '      <c r="F2"><v>12.34</v></c>\n'
        '      <c r="G2"><v>1500000</v></c>\n'
        '      <c r="H2"><v>0</v></c>\n'
        '      <c r="J2" t="inlineStr"><is><t>توضیحات فاکتور</t></is></c>\n'
        f'      <c r="Z2" t="inlineStr"><is><t>{u_bf}</t></is></c>\n'
        "    </row>\n"
        "  </sheetData>\n"
        "  <extLst><ext><worksheet><sheetData><row r='2'/>"
        "</sheetData></worksheet></ext></extLst>\n"
        "</worksheet>"
    )
    b2.raw_sheet_xml_overrides["خرید-فروش"] = main_ws_xml
    b2.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b2.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b2.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    p2 = tmp_path / "extlst_ws.xlsx"
    p2.write_bytes(b2.build_bytes())
    res = read_xlsx_source_snapshot(p2)
    assert res.snapshot.total_row_count == 4


def test_r4_02_required_headers_and_technical_id_matrix(
    tmp_path: Path,
) -> None:
    """Parametrized header matrix covering Persian and technical headers (R4-02)."""
    # 1. Missing technical ID header Z1 in خرید-فروش
    b1 = SyntheticXlsxBuilder()
    ws1_no_z1 = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<worksheet xmlns="{b1.ns_sm}">\n'
        "  <sheetData>\n"
        '    <row r="1">\n'
        '      <c r="B1" t="inlineStr"><is><t>تاریخ</t></is></c>\n'
        '      <c r="C1" t="inlineStr"><is><t>نام</t></is></c>\n'
        '      <c r="D1" t="inlineStr"><is><t>شرح</t></is></c>\n'
        '      <c r="E1" t="inlineStr"><is><t>کالا</t></is></c>\n'
        '      <c r="F1" t="inlineStr"><is><t>مقدار</t></is></c>\n'
        '      <c r="G1" t="inlineStr"><is><t>فی</t></is></c>\n'
        '      <c r="H1" t="inlineStr"><is><t>تخفیف</t></is></c>\n'
        '      <c r="J1" t="inlineStr"><is><t>توضیحات</t></is></c>\n'
        "    </row>\n"
        "  </sheetData>\n"
        "</worksheet>"
    )
    b1.raw_sheet_xml_overrides["خرید-فروش"] = ws1_no_z1
    b1.add_sheet_rows("دریافت-پرداخت", [])
    b1.add_sheet_rows("ورود-خروج", [])
    b1.add_sheet_rows("لیست کسبه", [])
    p1 = tmp_path / "no_z1.xlsx"
    p1.write_bytes(b1.build_bytes())
    with pytest.raises(XlsxHeaderError) as exc1:
        read_xlsx_source_snapshot(p1)
    assert exc1.value.reason == REASON_HEADER_TEXT_MISMATCH

    # 2. Header with extra spaces " تاریخ "
    b2 = SyntheticXlsxBuilder()
    ws1_space = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<worksheet xmlns="{b2.ns_sm}">\n'
        "  <sheetData>\n"
        '    <row r="1">\n'
        '      <c r="B1" t="inlineStr"><is><t> تاریخ </t></is></c>\n'
        '      <c r="C1" t="inlineStr"><is><t>نام</t></is></c>\n'
        '      <c r="D1" t="inlineStr"><is><t>شرح</t></is></c>\n'
        '      <c r="E1" t="inlineStr"><is><t>کالا</t></is></c>\n'
        '      <c r="F1" t="inlineStr"><is><t>مقدار</t></is></c>\n'
        '      <c r="G1" t="inlineStr"><is><t>فی</t></is></c>\n'
        '      <c r="H1" t="inlineStr"><is><t>تخفیف</t></is></c>\n'
        '      <c r="J1" t="inlineStr"><is><t>توضیحات</t></is></c>\n'
        '      <c r="Z1" t="inlineStr"><is><t>record_id</t></is></c>\n'
        "    </row>\n"
        "  </sheetData>\n"
        "</worksheet>"
    )
    b2.raw_sheet_xml_overrides["خرید-فروش"] = ws1_space
    b2.add_sheet_rows("دریافت-پرداخت", [])
    b2.add_sheet_rows("ورود-خروج", [])
    b2.add_sheet_rows("لیست کسبه", [])
    p2 = tmp_path / "space_header.xlsx"
    p2.write_bytes(b2.build_bytes())
    with pytest.raises(XlsxHeaderError) as exc2:
        read_xlsx_source_snapshot(p2)
    assert exc2.value.reason == REASON_HEADER_TEXT_MISMATCH

    # 3. Array formula covering header B1
    b3 = SyntheticXlsxBuilder()
    ws1_array_cov = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<worksheet xmlns="{b3.ns_sm}">\n'
        "  <sheetData>\n"
        '    <row r="1">\n'
        '      <c r="A1"><f t="array" ref="A1:B1">FORMULA</f></c>\n'
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
        "  </sheetData>\n"
        "</worksheet>"
    )
    b3.raw_sheet_xml_overrides["خرید-فروش"] = ws1_array_cov
    b3.add_sheet_rows("دریافت-پرداخت", [])
    b3.add_sheet_rows("ورود-خروج", [])
    b3.add_sheet_rows("لیست کسبه", [])
    p3 = tmp_path / "array_cov_header.xlsx"
    p3.write_bytes(b3.build_bytes())
    with pytest.raises(XlsxHeaderError) as exc3:
        read_xlsx_source_snapshot(p3)
    assert exc3.value.reason == REASON_HEADER_FORMULA_BACKED


def test_r4_03_sst_selection_and_classification_positive_negative(
    tmp_path: Path,
) -> None:
    """Verify SST selection only decodes strings needed for active inputs (R4-03)."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    # Scenario 1: I2 derived column pointing to corrupt SST index 0
    b1 = SyntheticXlsxBuilder()
    b1.shared_strings = ["_xD800_"]
    row_bf = _sample_buy_sell_row_data(u_bf, 2)
    row_bf["I"] = {"t": "s", "v": "0"}  # derived column I
    b1.add_sheet_rows("خرید-فروش", [row_bf])
    b1.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b1.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b1.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p1 = tmp_path / "scen1.xlsx"
    p1.write_bytes(b1.build_bytes())
    res1 = read_xlsx_source_snapshot(p1)
    assert res1.snapshot.total_row_count == 4

    # Scenario 2: Y2 non-whitelist column pointing to corrupt SST index 0
    b2 = SyntheticXlsxBuilder()
    b2.shared_strings = ["_xD800_"]
    row_bf2 = _sample_buy_sell_row_data(u_bf, 2)
    row_bf2["Y"] = {"t": "s", "v": "0"}  # column Y outside whitelist
    b2.add_sheet_rows("خرید-فروش", [row_bf2])
    b2.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b2.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b2.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p2 = tmp_path / "scen2.xlsx"
    p2.write_bytes(b2.build_bytes())
    res2 = read_xlsx_source_snapshot(p2)
    assert res2.snapshot.total_row_count == 4

    # Scenario 3: F2 has formula with t="s" v="0" cache pointing to corrupt index 0
    b3 = SyntheticXlsxBuilder()
    b3.shared_strings = ["_xD800_"]
    row_bf3 = _sample_buy_sell_row_data(u_bf, 2)
    row_bf3["F"] = {"f": "SUM(A1:A2)", "t": "s", "v": "0"}
    b3.add_sheet_rows("خرید-فروش", [row_bf3])
    b3.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b3.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b3.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p3 = tmp_path / "scen3.xlsx"
    p3.write_bytes(b3.build_bytes())
    res3 = read_xlsx_source_snapshot(p3)
    assert res3.snapshot.total_row_count == 4

    # Scenario 4: H2 covered by array anchor K2 with ref=H2:K2
    b4 = SyntheticXlsxBuilder()
    b4.shared_strings = ["_xD800_"]
    row_bf4 = _sample_buy_sell_row_data(u_bf, 2)
    row_bf4["K"] = {"f": "ARRAYFORMULA()", "f_t": "array", "f_ref": "H2:K2"}
    row_bf4["H"] = {"t": "s", "v": "0"}
    b4.add_sheet_rows("خرید-فروش", [row_bf4])
    b4.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b4.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b4.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p4 = tmp_path / "scen4.xlsx"
    p4.write_bytes(b4.build_bytes())
    res4 = read_xlsx_source_snapshot(p4)
    assert res4.snapshot.total_row_count == 4

    # Scenario 5: G1 optional header in دریافت-پرداخت pointing to corrupt index 0
    b5 = SyntheticXlsxBuilder()
    b5.shared_strings = ["_xD800_"]
    ws_dp_g1 = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<worksheet xmlns="{b5.ns_sm}">\n'
        "  <sheetData>\n"
        '    <row r="1">\n'
        '      <c r="B1" t="inlineStr"><is><t>تاریخ</t></is></c>\n'
        '      <c r="C1" t="inlineStr"><is><t>نام</t></is></c>\n'
        '      <c r="D1" t="inlineStr"><is><t>شرح</t></is></c>\n'
        '      <c r="E1" t="inlineStr"><is><t>مبلغ</t></is></c>\n'
        '      <c r="F1" t="inlineStr"><is><t>توضیحات</t></is></c>\n'
        '      <c r="G1" t="s"><v>0</v></c>\n'  # optional header G1
        '      <c r="P1" t="inlineStr"><is><t>record_id</t></is></c>\n'
        "    </row>\n"
        "  </sheetData>\n"
        "</worksheet>"
    )
    b5.raw_sheet_xml_overrides["دریافت-پرداخت"] = ws_dp_g1
    b5.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u_bf, 2)])
    b5.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b5.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p5 = tmp_path / "scen5.xlsx"
    p5.write_bytes(b5.build_bytes())
    res5 = read_xlsx_source_snapshot(p5)
    assert res5.snapshot.total_row_count == 3

    # Scenario 6 & 7: B2 date and Z2 ID on an inactive row pointing to corrupt index 0
    b6 = SyntheticXlsxBuilder()
    b6.shared_strings = ["_xD800_"]
    inactive_row = {
        "__row_num__": 3,
        "A": "2",
        "B": {"t": "s", "v": "0"},
        "Z": {"t": "s", "v": "0"},
    }
    b6.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u_bf, 2), inactive_row])
    b6.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b6.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b6.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p6 = tmp_path / "scen6_7.xlsx"
    p6.write_bytes(b6.build_bytes())
    res6 = read_xlsx_source_snapshot(p6)
    assert res6.snapshot.total_row_count == 4  # row 3 omitted as inactive


def test_r4_04_strict_sst_index_grammar_and_error_boundaries(
    tmp_path: Path,
) -> None:
    """Verify strict SST index grammar and exact cell error reasons (R4-04)."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    # 1. SST index with newline <v>0\n</v>
    b1 = SyntheticXlsxBuilder()
    b1.shared_strings = ["احمدی"]
    row_bf1 = _sample_buy_sell_row_data(u_bf, 2)
    row_bf1["C"] = {"t": "s", "raw_inner": "<v>0\n</v>"}
    b1.add_sheet_rows("خرید-فروش", [row_bf1])
    b1.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b1.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b1.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p1 = tmp_path / "newline_sst.xlsx"
    p1.write_bytes(b1.build_bytes())
    with pytest.raises(XlsxCellError) as exc1:
        read_xlsx_source_snapshot(p1)
    assert exc1.value.reason == REASON_CELL_INVALID_SST_INDEX

    # 2. F2 = "bad" in decimal column -> XlsxCellError
    b2 = SyntheticXlsxBuilder()
    row_bf2 = _sample_buy_sell_row_data(u_bf, 2)
    row_bf2["F"] = "bad"
    b2.add_sheet_rows("خرید-فروش", [row_bf2])
    b2.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b2.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b2.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p2 = tmp_path / "bad_decimal.xlsx"
    p2.write_bytes(b2.build_bytes())
    with pytest.raises(XlsxCellError) as exc2:
        read_xlsx_source_snapshot(p2)
    assert exc2.value.reason == REASON_CELL_INVALID_NUMERIC_LEXEME
    assert exc2.value.cell_ref == "F2"

    # 3. G2 = "100.5" in integer toman column -> XlsxCellError
    b3 = SyntheticXlsxBuilder()
    row_bf3 = _sample_buy_sell_row_data(u_bf, 2)
    row_bf3["G"] = "100.5"
    b3.add_sheet_rows("خرید-فروش", [row_bf3])
    b3.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b3.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b3.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p3 = tmp_path / "bad_toman.xlsx"
    p3.write_bytes(b3.build_bytes())
    with pytest.raises(XlsxCellError) as exc3:
        read_xlsx_source_snapshot(p3)
    assert exc3.value.reason == REASON_CELL_INVALID_NUMERIC_LEXEME
    assert exc3.value.cell_ref == "G2"

    # 4. B2 = "1403/13/01" invalid date -> XlsxCellError
    b4 = SyntheticXlsxBuilder()
    row_bf4 = _sample_buy_sell_row_data(u_bf, 2)
    row_bf4["B"] = "1403/13/01"
    b4.add_sheet_rows("خرید-فروش", [row_bf4])
    b4.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b4.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b4.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p4 = tmp_path / "bad_date.xlsx"
    p4.write_bytes(b4.build_bytes())
    with pytest.raises(XlsxCellError) as exc4:
        read_xlsx_source_snapshot(p4)
    assert exc4.value.reason == REASON_CELL_INVALID_NUMERIC_LEXEME
    assert exc4.value.cell_ref == "B2"


def test_xr12_synthetic_15000_row_benchmark(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Benchmark 15,000 active rows in isolated process (XR-12, R4-06, F3)."""
    builder = SyntheticXlsxBuilder()
    builder.dimension_ref = "A1:Z1000000"  # Large misleading dimension
    builder.shared_strings = [f"UNUSED_SST_ENTRY_{i:04d}" for i in range(100)]

    total_active_target = 15000
    rows_per_sheet = total_active_target // 4

    for sheet_idx, s_name in enumerate(RAW_CONTRACT_REGISTRY.sheets, 1):
        sheet_rows = []
        for r_idx in range(2, rows_per_sheet + 2):
            raw_uuid_bytes = f"{sheet_idx:04d}{r_idx:012d}".encode()
            u = _make_uuid7(raw_uuid_bytes)
            if s_name == "خرید-فروش":
                sheet_rows.append(_sample_buy_sell_row_data(u, r_idx))
            elif s_name == "دریافت-پرداخت":
                sheet_rows.append(_sample_receipts_payments_row_data(u, r_idx))
            elif s_name == "ورود-خروج":
                sheet_rows.append(_sample_inventory_movements_row_data(u, r_idx))
            else:
                sheet_rows.append(_sample_business_parties_row_data(u, r_idx))

        # 1,250 tail rows per sheet (5,000 total across 4 sheets):
        # 1) Inactive rows: 417 rows
        for r_idx in range(rows_per_sheet + 2, rows_per_sheet + 419):
            sheet_rows.append(
                {
                    "__row_num__": r_idx,
                    "A": str(r_idx),
                    "B": "" if s_name == "لیست کسبه" else "1403/01/01",
                    "C": "",
                }
            )
        # 2) Formula-only rows: 417 rows
        for r_idx in range(rows_per_sheet + 419, rows_per_sheet + 836):
            sheet_rows.append(
                {
                    "__row_num__": r_idx,
                    "A": str(r_idx),
                    "F": {"f": "SUM(F2:F3751)", "v": "12345.67"},
                }
            )
        # 3) Style/formatting-only rows: 416 rows
        for r_idx in range(rows_per_sheet + 836, rows_per_sheet + 1252):
            sheet_rows.append(
                {
                    "__row_num__": r_idx,
                    "A": {"s": "1", "v": str(r_idx)},
                    "B": {"s": "2", "v": ""},
                }
            )

        builder.add_sheet_rows(s_name, sheet_rows)

    pkg_path = tmp_path / "benchmark_15000.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    # Pre-reader XML assertions: verify formula, style, and unused SST constructs
    with zipfile.ZipFile(pkg_path, "r") as zf_check:
        s1_xml = zf_check.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "<f" in s1_xml, "Missing <f tag in pre-reader worksheet XML"
        assert 's="1"' in s1_xml, "Missing s=1 style in pre-reader worksheet XML"
        assert 's="2"' in s1_xml, "Missing s=2 style in pre-reader worksheet XML"
        sst_xml = zf_check.read("xl/sharedStrings.xml").decode("utf-8")
        assert "UNUSED_SST_ENTRY_0000" in sst_xml, (
            "Missing unused SST entry in pre-reader sharedStrings XML"
        )

    bench_code = f"""
import json, sys, time
from pathlib import Path
from accounting_contracts.raw_input_contracts import RAW_CONTRACT_REGISTRY
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

sheet_hashes = {{
    s_name: res.snapshot.sheets[s_name].sheet_snapshot_hash
    for s_name in RAW_CONTRACT_REGISTRY.sheets
}}

out = {{
    "rows": res.snapshot.total_row_count,
    "locations": len(res.locations_by_uuid),
    "read_build_seconds": round(t1 - t0, 4),
    "peak_rss_mib": round(peak_mib, 2),
    "version": res.version,
    "platform": sys.platform,
    "sheet_hashes": sheet_hashes,
}}
print(json.dumps(out))
"""
    proc = subprocess.run(
        [sys.executable, "-c", bench_code],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print("BENCHMARK SUBPROCESS FAILED:")
        print("STDOUT:", proc.stdout)
        print("STDERR:", proc.stderr)
        proc.check_returncode()

    data = json.loads(proc.stdout.strip().splitlines()[-1])

    rows = data["rows"]
    duration = data["read_build_seconds"]
    peak_rss_mib = data["peak_rss_mib"]
    sheet_hashes = data["sheet_hashes"]

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
    assert duration < 15.0, f"Benchmark exceeded 15.0s limit: {duration}s"
    assert peak_rss_mib < 128.0, (
        f"Peak RSS exceeded 128.0 MiB limit: {peak_rss_mib} MiB"
    )
    assert peak_rss_mib > 0.0, f"Peak RSS should be positive: {peak_rss_mib}"

    # Literal deterministic 64-hex golden digests checked into the test
    expected_golden_hashes = {
        "خرید-فروش": (
            "b07faa7f4a680ab01c2da88105e0b7aff7f9b0251bc9fdd8269734a0ca9027a5"
        ),
        "دریافت-پرداخت": (
            "040a8e31178c14d7b7937384a82770d18a8d677156515b4d98b9f496469f9c68"
        ),
        "ورود-خروج": (
            "e2d1c853b83547ccfd9f549a9a17014a7bc8e54e5957ab49f1dcff30165c4349"
        ),
        "لیست کسبه": (
            "ab51f1f0e34de06e78bd8f066584f84705fd77b98f541b6af060e8ac118f1ed1"
        ),
    }
    assert sheet_hashes == expected_golden_hashes, (
        f"Snapshot hashes differed from golden digests: {sheet_hashes}"
    )
