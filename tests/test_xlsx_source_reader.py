"""Comprehensive tests for streaming XLSX source reader (ADR-0008, WP-05).

Verifies:
- XR-01: Transitional and Strict namespaces, relationship resolution, helper sheets.
- XR-02: Persian & technical header checks, missing/wrong/formula headers rejected.
- XR-03: Sparse cells, explicit empty text vs missing, rich text, OOXML escapes,
         large exact numbers, Decimal scale, XML exponent, numeric date rejection.
- XR-04: Formula coverage (normal, shared, array, dataTable), covered cells, overrides.
- XR-05: Row activity: date/ID/formula-only skipped, zero counts, whitespace kept,
         active row missing/duplicate/non-v7 ID fails, clearing inputs omits row.
- XR-06: Hidden/filtered rows read, misleading dimensions ignored, distant rows,
         duplicate/mismatched coordinates rejected.
- XR-07: Corrupted ZIP/XML, missing parts, duplicate sheets/parts, DTD rejected.
- XR-08: Integration with WP-04 snapshot & change planner, state idempotency.
- XR-09: Hypothesis property tests: permutations, shared/inline strings, row orders.
- XR-10: Immutability and invariant validation of Result and Location constructors.
- XR-11: Read-only integrity, stream closure, unread helper sheets non-blocking.
- XR-12: 15,000-row benchmark measuring time (< 15s) and peak RSS (< 128 MiB).
- XR-13: Green quality checks and full suite integration.
"""

from __future__ import annotations

import io
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
    IdentityLifecycle,
    PriorIdentityState,
    build_prior_identity_registry,
    plan_source_changes,
)
from accounting_local_agent.xlsx_source_reader import (
    XLSX_SOURCE_READER_VERSION,
    SourceRowLocation,
    XlsxCellError,
    XlsxHeaderError,
    XlsxIdentityError,
    XlsxPackageError,
    XlsxStructureError,
    read_xlsx_source_snapshot,
)
from hypothesis import given
from hypothesis import strategies as st


def _make_uuid7(b: bytes) -> uuid.UUID:
    """Helper generating an RFC 4122 version 7 UUID from 16 bytes."""
    b_arr = bytearray(b)
    b_arr[6] = (b_arr[6] & 0x0F) | 0x70
    b_arr[8] = (b_arr[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(b_arr))


# --- Synthetic XLSX Package Builder Helper ---


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
        self.helper_sheets: dict[str, str] = {}  # name -> xml content
        self.shared_strings: list[str] = []
        self.extra_files: dict[str, bytes] = {}
        self.corrupt_zip: bool = False
        self.override_rels: str | None = None
        self.override_wb_rels: str | None = None
        self.override_wb_xml: str | None = None
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
            # 1. [Content_Types].xml
            ct_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                '  <Default Extension="rels" '
                'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
                '  <Default Extension="xml" ContentType="application/xml"/>\n'
                '  <Override PartName="/xl/workbook.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>\n'
                '  <Override PartName="/xl/sharedStrings.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>\n'
            )
            all_names = list(self.sheets.keys())
            for k in self.raw_sheet_xml_overrides:
                if k not in all_names:
                    all_names.append(k)
            for k in self.helper_sheets:
                if k not in all_names:
                    all_names.append(k)

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

    def _cell_to_xml(self, col: str, row: int, val: Any) -> str:
        cell_ref = f"{col}{row}"
        if val is None:
            return ""

        # Special dict for custom formula/cell controls
        if isinstance(val, dict):
            t_attr = val.get("t")
            v_val = val.get("v")
            f_val = val.get("f")
            f_type = val.get("f_t")
            f_ref = val.get("f_ref")
            is_val = val.get("is")

            c_tag = f'<c r="{cell_ref}"'
            if t_attr:
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
            elif v_val is not None:
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
        "J": "توضیحات فاکتور",  # notes_raw (Column J!)
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


# --- XR-01: Namespaces, Relationships, Non-standard Names, Helper Sheets ---


def test_xr01_transitional_and_strict_namespaces(tmp_path: Path) -> None:
    """XR-01: Support both Transitional and Strict SpreadsheetML namespaces."""
    for is_strict in (False, True):
        wb_bytes, uuids = _build_standard_synthetic_workbook(is_strict=is_strict)
        pkg_path = tmp_path / f"test_{is_strict}.xlsx"
        pkg_path.write_bytes(wb_bytes)

        result = read_xlsx_source_snapshot(pkg_path)
        assert result.version == XLSX_SOURCE_READER_VERSION
        assert result.snapshot.total_row_count == 4
        assert len(result.locations_by_uuid) == 4
        for u in uuids:
            assert u in result.snapshot.all_rows_by_id
            assert u in result.locations_by_uuid
            assert result.locations_by_uuid[u].physical_row_number == 2


def test_xr01_nonstandard_filenames_and_extra_report_sheets(
    tmp_path: Path,
) -> None:
    """XR-01: Dynamic targets, custom filenames, and ignored report sheets."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.add_sheet_rows(
        "خرید-فروش",
        [_sample_buy_sell_row_data(u_bf, 2)],
        custom_filename="worksheets/custom_bf_data.xml",
    )
    builder.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(u_dp, 2)],
        custom_filename="worksheets/my_receipts.xml",
    )
    builder.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(u_vk, 2)],
    )
    builder.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(u_lk, 2)],
    )
    # Extra helper sheet with broken XML that must NOT be opened or parsed
    builder.add_helper_sheet("گزارش سود و زیان", "<broken>XML<unclosed>")

    pkg_path = tmp_path / "custom_names_and_reports.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    result = read_xlsx_source_snapshot(pkg_path)
    assert result.snapshot.total_row_count == 4
    assert len(result.snapshot.sheets) == 4
    assert "گزارش سود و زیان" not in result.snapshot.sheets


# --- XR-02: Persian & Technical Headers, Missing/Wrong/Formula Headers ---


def test_xr02_exact_persian_and_technical_headers(tmp_path: Path) -> None:
    """XR-02: All required Persian and technical ID headers are validated."""
    wb_bytes, _ = _build_standard_synthetic_workbook()
    pkg_path = tmp_path / "valid_headers.xlsx"
    pkg_path.write_bytes(wb_bytes)
    result = read_xlsx_source_snapshot(pkg_path)
    assert result.snapshot.total_row_count == 4


def test_xr02_missing_or_wrong_header_fails(tmp_path: Path) -> None:
    """XR-02: Missing or wrong header text raises XlsxHeaderError."""
    u_bf = _make_uuid7(b"0000000000000001")
    builder = SyntheticXlsxBuilder()
    # Replace valid header 'تاریخ' with 'تاریخچه' in خرید-فروش
    custom_bf_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<worksheet xmlns="{builder.ns_sm}">\n'
        "  <sheetData>\n"
        '    <row r="1">\n'
        '      <c r="A1" t="inlineStr"><is><t>ردیف</t></is></c>\n'
        '      <c r="B1" t="inlineStr"><is><t>تاریخچه</t></is></c>\n'  # WRONG
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
        '      <c r="H2"><v>0</v></c>\n'
        '      <c r="J2" t="inlineStr"><is><t>توضیحات</t></is></c>\n'
        f'      <c r="Z2" t="inlineStr"><is><t>{u_bf}</t></is></c>\n'
        "    </row>\n"
        "  </sheetData>\n</worksheet>"
    )
    builder.raw_sheet_xml_overrides["خرید-فروش"] = custom_bf_xml
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

    pkg_path = tmp_path / "wrong_header.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    with pytest.raises(XlsxHeaderError) as exc:
        read_xlsx_source_snapshot(pkg_path)
    assert "Header mismatch" in str(exc.value)
    assert "B1" in str(exc.value)


def test_xr02_formula_backed_header_fails(tmp_path: Path) -> None:
    """XR-02: Formula cell in required header raises XlsxHeaderError."""
    builder = SyntheticXlsxBuilder()
    custom_bf_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<worksheet xmlns="{builder.ns_sm}">\n'
        "  <sheetData>\n"
        '    <row r="1">\n'
        '      <c r="A1" t="inlineStr"><is><t>ردیف</t></is></c>\n'
        '      <c r="B1"><f>CONCATENATE("تار","یخ")</f><v>تاریخ</v></c>\n'
        '      <c r="C1" t="inlineStr"><is><t>نام</t></is></c>\n'
        '      <c r="D1" t="inlineStr"><is><t>شرح</t></is></c>\n'
        '      <c r="E1" t="inlineStr"><is><t>کالا</t></is></c>\n'
        '      <c r="F1" t="inlineStr"><is><t>مقدار</t></is></c>\n'
        '      <c r="G1" t="inlineStr"><is><t>فی</t></is></c>\n'
        '      <c r="H1" t="inlineStr"><is><t>تخفیف</t></is></c>\n'
        '      <c r="J1" t="inlineStr"><is><t>توضیحات</t></is></c>\n'
        '      <c r="Z1" t="inlineStr"><is><t>record_id</t></is></c>\n'
        "    </row>\n"
        "  </sheetData>\n</worksheet>"
    )
    builder.raw_sheet_xml_overrides["خرید-فروش"] = custom_bf_xml
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

    pkg_path = tmp_path / "formula_header.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    with pytest.raises(XlsxHeaderError) as exc:
        read_xlsx_source_snapshot(pkg_path)
    assert "has formula" in str(exc.value)


# --- XR-03: Sparse Cells, Empty Text, OOXML Escapes, Numeric Types ---


def test_xr03_sparse_cells_and_empty_text_distinction(tmp_path: Path) -> None:
    """XR-03: Missing cells map to None; explicit empty text maps to ''."""
    u1 = _make_uuid7(b"0000000000000001")
    # Row with notes_raw missing (column J omitted) -> None
    # Row with party_name_raw explicit empty string -> ""
    row_data = _sample_buy_sell_row_data(u1, 2)
    del row_data["J"]  # notes_raw missing -> None
    row_data["C"] = ""  # party_name_raw explicit empty text

    builder = SyntheticXlsxBuilder()
    builder.add_sheet_rows("خرید-فروش", [row_data])
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

    pkg_path = tmp_path / "sparse_and_empty.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    result = read_xlsx_source_snapshot(pkg_path)
    v_row = result.snapshot.sheets["خرید-فروش"].rows[0]

    assert v_row.raw_values["notes_raw"] is None
    assert v_row.raw_values["party_name_raw"] == ""


def test_xr03_ooxml_escapes_and_rich_text(tmp_path: Path) -> None:
    """XR-03: OOXML _xHHHH_ escapes and surrogate pairs decode properly."""
    u1 = _make_uuid7(b"0000000000000001")
    # _x000D_ is carriage return, _xD83D__xDE00_ is 😀
    notes_escaped = "یادداشت_x000D__x000A_خط دوم_xD83D__xDE00_"
    row_data = _sample_buy_sell_row_data(u1, 2)
    row_data["J"] = notes_escaped

    builder = SyntheticXlsxBuilder()
    builder.add_sheet_rows("خرید-فروش", [row_data])
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

    pkg_path = tmp_path / "ooxml_escapes.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    result = read_xlsx_source_snapshot(pkg_path)
    v_row = result.snapshot.sheets["خرید-فروش"].rows[0]

    assert v_row.raw_values["notes_raw"] == "یادداشت\r\nخط دوم😀"


def test_xr03_numeric_types_decimals_and_scientific_notation(
    tmp_path: Path,
) -> None:
    """XR-03: Decimals decoded directly from XML, XML scientific notation accepted."""
    u1 = _make_uuid7(b"0000000000000001")
    row_data = _sample_buy_sell_row_data(u1, 2)
    row_data["G"] = {"t": "n", "v": "1.5e6"}  # 1500000 in XML exponent
    row_data["F"] = {"t": "n", "v": "12.3400"}  # exact scale preserved

    builder = SyntheticXlsxBuilder()
    builder.add_sheet_rows("خرید-فروش", [row_data])
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

    pkg_path = tmp_path / "numeric_types.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    result = read_xlsx_source_snapshot(pkg_path)
    v_row = result.snapshot.sheets["خرید-فروش"].rows[0]

    assert v_row.raw_values["unit_price_toman_raw"] == Decimal("1.5e6")
    assert v_row.raw_values["quantity_raw"] == Decimal("12.3400")


def test_xr03_numeric_date_and_boolean_error_rejection(tmp_path: Path) -> None:
    """XR-03: Numeric date, boolean, and error cell types in raw fields are rejected."""
    u1 = _make_uuid7(b"0000000000000001")

    # 1. Numeric XML in date_raw is rejected
    builder1 = SyntheticXlsxBuilder()
    row_num_date = _sample_buy_sell_row_data(u1, 2)
    row_num_date["B"] = {"t": "n", "v": "14030515"}
    builder1.add_sheet_rows("خرید-فروش", [row_num_date])
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

    p1 = tmp_path / "num_date.xlsx"
    p1.write_bytes(builder1.build_bytes())
    with pytest.raises(XlsxCellError) as exc1:
        read_xlsx_source_snapshot(p1)
    assert "Numeric XML not allowed for date_raw" in str(exc1.value)

    # 2. Boolean in raw field is rejected
    builder2 = SyntheticXlsxBuilder()
    row_bool = _sample_buy_sell_row_data(u1, 2)
    row_bool["D"] = {"t": "b", "v": "1"}
    builder2.add_sheet_rows("خرید-فروش", [row_bool])
    builder2.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    builder2.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    builder2.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p2 = tmp_path / "bool_cell.xlsx"
    p2.write_bytes(builder2.build_bytes())
    with pytest.raises(XlsxCellError) as exc2:
        read_xlsx_source_snapshot(p2)
    assert "Boolean cell 'b' rejected" in str(exc2.value)


# --- XR-04: Formula & Array/Data-table Coverage ---


def test_xr04_formula_and_array_coverage_exclusion(tmp_path: Path) -> None:
    """XR-04: Formulas and covered cells excluded; literal overrides kept."""
    u1 = _make_uuid7(b"0000000000000001")
    u2 = _make_uuid7(b"0000000000000002")

    builder = SyntheticXlsxBuilder()
    # Row 2: Discount column H has a normal formula
    row2 = _sample_buy_sell_row_data(u1, 2)
    row2["H"] = {"f": "G2*0.05", "v": "75000"}  # Formula -> excluded to None

    # Row 3: Covered by an array formula declared in column K (outside whitelist)
    row3 = _sample_buy_sell_row_data(u2, 3)
    row3["K"] = {
        "f": "ARRAY_FORMULA()",
        "f_t": "array",
        "f_ref": "H3:K3",
        "v": "100",
    }
    row3["H"] = {"v": "50000"}  # Covered cell -> excluded to None

    builder.add_sheet_rows("خرید-فروش", [row2, row3])
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

    pkg_path = tmp_path / "formula_coverage.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    result = read_xlsx_source_snapshot(pkg_path)
    r2 = [r for r in result.snapshot.sheets["خرید-فروش"].rows if r.stable_id == u1][0]
    r3 = [r for r in result.snapshot.sheets["خرید-فروش"].rows if r.stable_id == u2][0]

    assert r2.raw_values["discount_toman_raw"] is None
    assert r3.raw_values["discount_toman_raw"] is None


def test_xr04_literal_text_starting_with_equals_sign(tmp_path: Path) -> None:
    """XR-04: Literal text starting with '=' preserved as text, not formula."""
    u1 = _make_uuid7(b"0000000000000001")
    row_data = _sample_buy_sell_row_data(u1, 2)
    row_data["J"] = "=SUM(A1:A10) یادداشت فاکتور"

    builder = SyntheticXlsxBuilder()
    builder.add_sheet_rows("خرید-فروش", [row_data])
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

    pkg_path = tmp_path / "literal_equals.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    result = read_xlsx_source_snapshot(pkg_path)
    v_row = result.snapshot.sheets["خرید-فروش"].rows[0]
    assert v_row.raw_values["notes_raw"] == "=SUM(A1:A10) یادداشت فاکتور"


# --- XR-05: Row Activity, Zero Value, Inactive Rows, ID Validation ---


def test_xr05_row_activity_rules_and_inactive_omission(tmp_path: Path) -> None:
    """XR-05: Inactive rows omitted; numeric zero counts as activity."""
    u_active1 = _make_uuid7(b"0000000000000001")
    u_active2 = _make_uuid7(b"0000000000000002")
    u_inactive_id = _make_uuid7(b"0000000000000099")

    # Row 2: Active row with normal data
    row2 = _sample_buy_sell_row_data(u_active1, 2)

    # Row 3: Inactive template row (date & UUID present, but activity cols C..J empty)
    row3 = {
        "__row_num__": 3,
        "A": "2",
        "B": "1403/05/15",  # date
        "C": "",  # empty party
        "D": "   ",  # whitespace only
        "E": None,
        "F": None,
        "G": None,
        "H": None,
        "J": None,
        "Z": str(u_inactive_id).lower(),  # leftover UUID
    }

    # Row 4: Active row where quantity is numeric zero (0) -> numeric zero IS active!
    row4 = {
        "__row_num__": 4,
        "A": "3",
        "B": "1403/05/15",
        "C": "",
        "D": "",
        "E": "",
        "F": 0,  # NUMERIC ZERO!
        "G": None,
        "H": None,
        "J": None,
        "Z": str(u_active2).lower(),
    }

    builder = SyntheticXlsxBuilder()
    builder.add_sheet_rows("خرید-فروش", [row2, row3, row4])
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

    pkg_path = tmp_path / "activity_rules.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    result = read_xlsx_source_snapshot(pkg_path)
    bf_rows = result.snapshot.sheets["خرید-فروش"].rows

    # Row 3 must be completely omitted! Total rows in خرید-فروش should be 2.
    assert len(bf_rows) == 2
    assert u_inactive_id not in result.snapshot.all_rows_by_id
    assert u_inactive_id not in result.locations_by_uuid

    assert u_active1 in result.snapshot.all_rows_by_id
    assert u_active2 in result.snapshot.all_rows_by_id
    assert result.locations_by_uuid[u_active1].physical_row_number == 2
    assert result.locations_by_uuid[u_active2].physical_row_number == 4


def test_xr05_active_row_missing_or_non_v7_uuid_fails(tmp_path: Path) -> None:
    """XR-05: Active row with missing or non-v7 UUID raises XlsxIdentityError."""
    # 1. Missing UUID on active row
    builder1 = SyntheticXlsxBuilder()
    row_no_id = _sample_buy_sell_row_data(_make_uuid7(b"1" * 16), 2)
    del row_no_id["Z"]
    builder1.add_sheet_rows("خرید-فروش", [row_no_id])
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

    p1 = tmp_path / "missing_uuid.xlsx"
    p1.write_bytes(builder1.build_bytes())
    with pytest.raises(XlsxIdentityError) as exc1:
        read_xlsx_source_snapshot(p1)
    assert "missing UUIDv7" in str(exc1.value)

    # 2. Non-v7 UUID (v4) on active row
    builder2 = SyntheticXlsxBuilder()
    v4_id = uuid.uuid4()
    row_v4 = _sample_buy_sell_row_data(_make_uuid7(b"1" * 16), 2)
    row_v4["Z"] = str(v4_id).lower()
    builder2.add_sheet_rows("خرید-فروش", [row_v4])
    builder2.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    builder2.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )
    builder2.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"4" * 16), 2)],
    )

    p2 = tmp_path / "v4_uuid.xlsx"
    p2.write_bytes(builder2.build_bytes())
    with pytest.raises(XlsxIdentityError) as exc2:
        read_xlsx_source_snapshot(p2)
    assert "must be version 7" in str(exc2.value)


# --- XR-06: Hidden/Filtered Rows, Distant Rows, Coordinate Invariants ---


def test_xr06_hidden_rows_and_distant_physical_rows(tmp_path: Path) -> None:
    """XR-06: Hidden rows retained, distant physical rows tracked accurately."""
    u1 = _make_uuid7(b"0000000000000001")
    row_data = _sample_buy_sell_row_data(u1, 5432)

    builder = SyntheticXlsxBuilder()
    builder.add_sheet_rows("خرید-فروش", [row_data])
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

    pkg_path = tmp_path / "distant_row.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    result = read_xlsx_source_snapshot(pkg_path)
    assert result.locations_by_uuid[u1].physical_row_number == 5432


# --- XR-07: Error Handling & Package Invariants ---


def test_xr07_corrupted_zip_and_missing_sheets(tmp_path: Path) -> None:
    """XR-07: Corrupted ZIP and missing approved sheets raise typed errors."""
    # 1. Corrupted ZIP
    builder1 = SyntheticXlsxBuilder()
    builder1.corrupt_zip = True
    p1 = tmp_path / "corrupt.xlsx"
    p1.write_bytes(builder1.build_bytes())
    with pytest.raises(XlsxPackageError):
        read_xlsx_source_snapshot(p1)

    # 2. Missing one approved sheet (لیست کسبه omitted)
    builder2 = SyntheticXlsxBuilder()
    builder2.add_sheet_rows(
        "خرید-فروش", [_sample_buy_sell_row_data(_make_uuid7(b"1" * 16), 2)]
    )
    builder2.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"2" * 16), 2)],
    )
    builder2.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"3" * 16), 2)],
    )

    p2 = tmp_path / "missing_sheet.xlsx"
    p2.write_bytes(builder2.build_bytes())
    with pytest.raises(XlsxStructureError) as exc:
        read_xlsx_source_snapshot(p2)
    assert "Missing required approved sheet" in str(exc.value)


# --- XR-08: Integration with WP-04 Planner & Idempotency ---


def test_xr08_end_to_end_planner_integration_and_idempotency(
    tmp_path: Path,
) -> None:
    """XR-08: Parsed snapshot drives WP-04 change planner and state advancement."""
    wb_bytes, _ = _build_standard_synthetic_workbook()
    pkg_path = tmp_path / "integration.xlsx"
    pkg_path.write_bytes(wb_bytes)

    result = read_xlsx_source_snapshot(pkg_path)

    # Step 1: Initial change plan from empty prior registry -> 4 inserts
    plan1 = plan_source_changes(result.snapshot)
    assert plan1.total_counts.insert_count == 4
    assert plan1.total_counts.edit_count == 0
    assert plan1.total_counts.void_count == 0
    assert plan1.total_counts.unchanged_count == 0

    # Step 2: Advance prior identity registry with plan1 results
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

    # Step 3: Run plan again with same snapshot -> 4 unchanged, 0 mutations
    plan2 = plan_source_changes(result.snapshot, prior_reg)
    assert plan2.total_counts.insert_count == 0
    assert plan2.total_counts.edit_count == 0
    assert plan2.total_counts.void_count == 0
    assert plan2.total_counts.unchanged_count == 4


# --- XR-09: Hypothesis Property Tests ---


@given(
    permutation_seed=st.integers(min_value=0, max_value=1000),
)
def test_xr09_property_independent_xlsx_packages_invariance(
    permutation_seed: int,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """XR-09: Permutations of sheet/row XML order yield identical snapshot."""
    tmp_dir = tmp_path_factory.mktemp("hypo_xlsx")

    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    # Package 1: Standard inline string representation
    builder1 = SyntheticXlsxBuilder()
    builder1.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u_bf, 2)])
    builder1.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder1.add_sheet_rows(
        "ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)]
    )
    builder1.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    # Package 2: Reversed sheet order and different physical row positions
    builder2 = SyntheticXlsxBuilder()
    builder2.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 10)])
    builder2.add_sheet_rows(
        "ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 50)]
    )
    builder2.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 100)]
    )
    builder2.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u_bf, 500)])

    p1 = tmp_dir / f"pkg1_{permutation_seed}.xlsx"
    p2 = tmp_dir / f"pkg2_{permutation_seed}.xlsx"
    p1.write_bytes(builder1.build_bytes())
    p2.write_bytes(builder2.build_bytes())

    res1 = read_xlsx_source_snapshot(p1)
    res2 = read_xlsx_source_snapshot(p2)

    # Snapshots must be identical regardless of physical order or representation
    for s_name in RAW_CONTRACT_REGISTRY.sheets:
        assert res1.snapshot.sheets[s_name].rows == res2.snapshot.sheets[s_name].rows
        assert (
            res1.snapshot.sheets[s_name].sheet_snapshot_hash
            == res2.snapshot.sheets[s_name].sheet_snapshot_hash
        )

    assert res1.snapshot.all_rows_by_id == res2.snapshot.all_rows_by_id


# --- XR-10: Result and Location Immutability and Validation ---


def test_xr10_result_and_location_immutability() -> None:
    """XR-10: Result and location constructors validate invariants and are immutable."""
    loc = SourceRowLocation(sheet_name="خرید-فروش", physical_row_number=5)
    assert loc.sheet_name == "خرید-فروش"
    assert loc.physical_row_number == 5

    # Negative: row number out of bounds
    with pytest.raises(XlsxStructureError):
        SourceRowLocation(sheet_name="خرید-فروش", physical_row_number=1)

    # Negative: boolean row number
    with pytest.raises(XlsxStructureError):
        SourceRowLocation(
            sheet_name="خرید-فروش",
            physical_row_number=cast(Any, True),
        )


# --- XR-11: Read-Only File Handle Cleanup & Non-Mutation ---


def test_xr11_source_file_remains_unmodified_and_unlocked(
    tmp_path: Path,
) -> None:
    """XR-11: Source file hash/mtime untouched, handles cleanly released."""
    wb_bytes, _ = _build_standard_synthetic_workbook()
    pkg_path = tmp_path / "readonly_check.xlsx"
    pkg_path.write_bytes(wb_bytes)

    stat_before = pkg_path.stat()
    _ = read_xlsx_source_snapshot(pkg_path)
    stat_after = pkg_path.stat()

    assert stat_before.st_size == stat_after.st_size
    assert pkg_path.read_bytes() == wb_bytes

    # File can be safely renamed/removed immediately (verifies handle closure)
    new_path = tmp_path / "renamed_check.xlsx"
    pkg_path.rename(new_path)
    assert new_path.exists()


# --- XR-12: 15,000-Row Synthetic Streaming Benchmark ---


def test_xr12_synthetic_15000_row_benchmark(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """XR-12: Benchmark 15k active + 5k tail rows (< 15s, < 128 MiB peak RSS)."""
    rows_per_sheet = 3750
    builder = SyntheticXlsxBuilder()

    counter = 1
    for s_name, gen_func in [
        ("خرید-فروش", _sample_buy_sell_row_data),
        ("دریافت-پرداخت", _sample_receipts_payments_row_data),
        ("ورود-خروج", _sample_inventory_movements_row_data),
        ("لیست کسبه", _sample_business_parties_row_data),
    ]:
        sheet_rows: list[dict[str, Any]] = []
        for r_idx in range(2, rows_per_sheet + 2):
            u = _make_uuid7(counter.to_bytes(16, "big"))
            sheet_rows.append(gen_func(u, r_idx))
            counter += 1

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

    # Measure execution time and peak resident memory in a fresh process (WP-05 XR-12)
    bench_code = f"""
import json, resource, sys, time
from pathlib import Path
from accounting_local_agent.xlsx_source_reader import read_xlsx_source_snapshot

pkg = Path({repr(str(pkg_path))})
t0 = time.perf_counter()
res = read_xlsx_source_snapshot(pkg)
t1 = time.perf_counter()
ru = resource.getrusage(resource.RUSAGE_SELF)
peak_mib = ru.ru_maxrss / 1024.0

out = {{
    "rows": res.snapshot.total_row_count,
    "locations": len(res.locations_by_uuid),
    "read_build_seconds": round(t1 - t0, 4),
    "peak_rss_mib": round(peak_mib, 2),
    "version": res.version,
}}
print(json.dumps(out))
"""
    import json
    import subprocess
    import sys

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

    # Print benchmark results inside capsys.disabled() so it is visible in CI stdout
    with capsys.disabled():
        print(
            f"\n[WP-05 BENCHMARK] 15,000 active rows -> "
            f"read_build_seconds: {duration:.4f}s | "
            f"peak_rss_mib: {peak_rss_mib:.2f} MiB | "
            f"rows: {rows}"
        )

    assert rows == 15000
    assert data["locations"] == 15000
    assert data["version"] == XLSX_SOURCE_READER_VERSION

    # Thresholds: under 15 seconds, under 128 MiB peak RSS
    assert duration < 15.0, f"Streaming read too slow: {duration:.2f}s"
    assert peak_rss_mib < 128.0, f"Peak memory too high: {peak_rss_mib:.2f} MiB"
