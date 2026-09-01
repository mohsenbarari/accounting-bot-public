"""Independent regression tests for WP-05 R5-A remediations.

Verifies:
1. E-01: Cell coordinate fullmatch regex and bounds checking on exact XML attributes
   (Unicode Ü2, Α2, K2, data LF C2&#10;, header LF B1&#10;, out-of-bounds column XFE2,
   out-of-bounds row 1048577) asserting XlsxStructureError without partial snapshots.
2. E-02: Type and scale-sensitive independent oracle across all 4 sheets asserting
   exact types (type is type), Decimal scale preservation (as_tuple), registry raw
   key ordering, and direct WP-03 hash consistency.
3. E-03: Dynamic registry-driven verification of all 16 auxiliary RAW_TEXT columns
   decoding numeric XML directly to exact str, date/UUID numeric XML rejection, and
   non-dialable synthetic lexemes.
4. E-04: Negative and blank activity matrix (isolated numeric 0, explicit blank text
   vs truly missing cells, and inactive rows).
"""

from __future__ import annotations

import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from accounting_contracts.canonical_hashing import compute_source_hash
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
    ValueKind,
)
from accounting_contracts.source_change_plan import (
    SourceRowInput,
    build_source_workbook_snapshot,
)
from accounting_local_agent.xlsx_source_reader import (
    REASON_CELL_INVALID_NUMERIC_LEXEME,
    REASON_CELL_NUMERIC_XML_IN_TEXT_FIELD,
    REASON_CELL_UNKNOWN_TYPE,
    REASON_HEADER_TEXT_MISMATCH,
    REASON_STRUCTURE_INVALID_CELL_REF,
    REASON_STRUCTURE_ROW_OUT_OF_BOUNDS,
    XlsxCellError,
    XlsxHeaderError,
    XlsxStructureError,
    read_xlsx_source_snapshot,
)
from test_xlsx_source_reader import (
    SyntheticXlsxBuilder,
    _make_uuid7,
    _sample_business_parties_row_data,
    _sample_buy_sell_row_data,
    _sample_inventory_movements_row_data,
    _sample_receipts_payments_row_data,
)


def test_r5_01_text_in_numeric_columns_raw_preservation(tmp_path: Path) -> None:
    """Verify raw str preservation with spaces, leading zeros, Persian/Arabic digits."""
    u1 = _make_uuid7(b"0000000000000001")
    u2 = _make_uuid7(b"0000000000000002")
    u3 = _make_uuid7(b"0000000000000003")
    u4 = _make_uuid7(b"0000000000000004")
    u_dp = _make_uuid7(b"0000000000000006")
    u_vk = _make_uuid7(b"0000000000000007")
    u_lk = _make_uuid7(b"0000000000000008")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = [" +002000 "]

    rows_bf = [
        # Row 2: F2 inlineStr " +0012.3400 ", G2 inlineStr " +001000 "
        {
            "__row_num__": 2,
            "A": "2",
            "B": "1403/05/15",
            "C": "بازرگانی اول",
            "D": "خرید",
            "E": "طلای ۱۸ عیار",
            "F": {"t": "inlineStr", "is": " +0012.3400 "},
            "G": {"t": "inlineStr", "is": " +001000 "},
            "H": "0",
            "J": "توضیحات ۱",
            "Z": str(u1),
        },
        # Row 3: F2 direct str " -000.5000 ", G2 shared string " +002000 " (index 0)
        {
            "__row_num__": 3,
            "A": "3",
            "B": "1403/05/16",
            "C": "بازرگانی دوم",
            "D": "فروش",
            "E": "طلای آبشده",
            "F": {"t": "str", "v": " -000.5000 "},
            "G": {"t": "s", "v": "0"},
            "H": "0",
            "J": "توضیحات ۲",
            "Z": str(u2),
        },
        # Row 4: F2 Persian digits "۱۲.۳۴", G2 Persian digits "۱۵۰۰۰۰۰"
        {
            "__row_num__": 4,
            "A": "4",
            "B": "1403/05/17",
            "C": "بازرگانی سوم",
            "D": "خرید",
            "E": "سکه تمام",
            "F": {"t": "inlineStr", "is": "۱۲.۳۴"},
            "G": {"t": "inlineStr", "is": "۱۵۰۰۰۰۰"},
            "H": "0",
            "J": "توضیحات ۳",
            "Z": str(u3),
        },
        # Row 5: F2 Arabic digits "١٢.٣٤", G2 Arabic digits "١٥٠٠٠٠٠"
        {
            "__row_num__": 5,
            "A": "5",
            "B": "1403/05/18",
            "C": "بازرگانی چهارم",
            "D": "فروش",
            "E": "طلای ۱۸ عیار",
            "F": {"t": "inlineStr", "is": "١٢.٣٤"},
            "G": {"t": "inlineStr", "is": "١٥٠٠٠٠٠"},
            "H": "0",
            "J": "توضیحات ۴",
            "Z": str(u4),
        },
    ]

    builder.add_sheet_rows("خرید-فروش", rows_bf)
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg_path = tmp_path / "raw_text_numeric.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg_path)
    bf_rows = {r.stable_id: r for r in res.snapshot.sheets["خرید-فروش"].rows}

    # Row 2 asserts: exactly preserves raw string
    r2 = bf_rows[u1]
    assert r2.raw_values["quantity_raw"] == " +0012.3400 "
    assert type(r2.raw_values["quantity_raw"]) is str
    assert r2.raw_values["unit_price_toman_raw"] == " +001000 "
    assert type(r2.raw_values["unit_price_toman_raw"]) is str

    # Row 3 asserts: exactly preserves direct string and shared string
    r3 = bf_rows[u2]
    assert r3.raw_values["quantity_raw"] == " -000.5000 "
    assert type(r3.raw_values["quantity_raw"]) is str
    assert r3.raw_values["unit_price_toman_raw"] == " +002000 "
    assert type(r3.raw_values["unit_price_toman_raw"]) is str

    # Row 4 asserts: Persian digits preserved as raw str
    r4 = bf_rows[u3]
    assert r4.raw_values["quantity_raw"] == "۱۲.۳۴"
    assert type(r4.raw_values["quantity_raw"]) is str
    assert r4.raw_values["unit_price_toman_raw"] == "۱۵۰۰۰۰۰"
    assert type(r4.raw_values["unit_price_toman_raw"]) is str

    # Row 5 asserts: Arabic digits preserved as raw str
    r5 = bf_rows[u4]
    assert r5.raw_values["quantity_raw"] == "١٢.٣٤"
    assert type(r5.raw_values["quantity_raw"]) is str
    assert r5.raw_values["unit_price_toman_raw"] == "١٥٠٠٠٠٠"
    assert type(r5.raw_values["unit_price_toman_raw"]) is str


def test_r5_01_numeric_xml_financial_preservation(tmp_path: Path) -> None:
    """Verify numeric XML directly becomes Decimal (preserving scale), not int."""
    u1 = _make_uuid7(b"0000000000000001")
    u2 = _make_uuid7(b"0000000000000002")
    u3 = _make_uuid7(b"0000000000000003")
    u4 = _make_uuid7(b"0000000000000004")
    u_dp = _make_uuid7(b"0000000000000005")
    u_vk = _make_uuid7(b"0000000000000006")
    u_lk = _make_uuid7(b"0000000000000007")

    builder = SyntheticXlsxBuilder()
    rows_bf = [
        # Row 2: G2 with <v>100.000</v> -> Decimal("100.000") (not int(100))
        {
            "__row_num__": 2,
            "A": "2",
            "B": "1403/05/15",
            "C": "بازرگانی احمدی",
            "D": "خرید",
            "E": "طلای ۱۸ عیار",
            "F": {"t": "n", "v": "12.3400"},
            "G": {"t": "n", "v": "100.000"},
            "H": "0",
            "J": "توضیحات ۱",
            "Z": str(u1),
        },
        # Row 3: G2 with <v>123456789012345678901234567890</v> -> large Decimal
        {
            "__row_num__": 3,
            "A": "3",
            "B": "1403/05/16",
            "C": "بازرگانی رضایی",
            "D": "فروش",
            "E": "طلای آبشده",
            "F": {"t": "n", "v": "50.0"},
            "G": {"t": "n", "v": "123456789012345678901234567890"},
            "H": "0",
            "J": "توضیحات ۲",
            "Z": str(u2),
        },
        # Row 4: G2 with XML exponent <v>1e30</v> -> Decimal
        {
            "__row_num__": 4,
            "A": "4",
            "B": "1403/05/17",
            "C": "بازرگانی کریمی",
            "D": "خرید",
            "E": "سکه بهار آزادی",
            "F": {"t": "n", "v": "1.0"},
            "G": {"t": "n", "v": "1e30"},
            "H": "0",
            "J": "توضیحات ۳",
            "Z": str(u3),
        },
        # Row 5: F2 with <v>1e2</v> -> Decimal("1e2")
        {
            "__row_num__": 5,
            "A": "5",
            "B": "1403/05/18",
            "C": "بازرگانی موسوی",
            "D": "فروش",
            "E": "طلای ۱۸ عیار",
            "F": {"t": "n", "v": "1e2"},
            "G": {"t": "n", "v": "5000000"},
            "H": "0",
            "J": "توضیحات ۴",
            "Z": str(u4),
        },
    ]

    builder.add_sheet_rows("خرید-فروش", rows_bf)
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg_path = tmp_path / "numeric_xml_financial.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg_path)
    bf_rows = {r.stable_id: r for r in res.snapshot.sheets["خرید-فروش"].rows}

    # Row 2: F2 is Decimal("12.3400"), G2 is Decimal("100.000") (not int)
    r2 = bf_rows[u1]
    f2_val = r2.raw_values["quantity_raw"]
    g2_val = r2.raw_values["unit_price_toman_raw"]
    assert type(f2_val) is Decimal
    assert f2_val.as_tuple() == Decimal("12.3400").as_tuple()
    assert type(g2_val) is Decimal
    assert not isinstance(g2_val, int)
    assert g2_val.as_tuple() == Decimal("100.000").as_tuple()

    # Row 3: G2 is exact large Decimal
    r3 = bf_rows[u2]
    g3_val = r3.raw_values["unit_price_toman_raw"]
    assert type(g3_val) is Decimal
    assert g3_val == Decimal("123456789012345678901234567890")

    # Row 4: G2 is Decimal("1e30")
    r4 = bf_rows[u3]
    g4_val = r4.raw_values["unit_price_toman_raw"]
    assert type(g4_val) is Decimal
    assert g4_val == Decimal("1e30")

    # Row 5: F2 is Decimal("1e2")
    r5 = bf_rows[u4]
    f5_val = r5.raw_values["quantity_raw"]
    assert type(f5_val) is Decimal
    assert f5_val == Decimal("1e2")


def test_r5_01_numeric_xml_in_raw_text_fields_across_all_four_sheets(
    tmp_path: Path,
) -> None:
    """Verify numeric XML in all 16 RAW_TEXT columns returns exact str (E-03)."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    # Fixed non-dialable test lexeme for RAW_TEXT columns
    test_lexeme = "123.4000"

    # Verify all 16 auxiliary RAW_TEXT fields from registry
    tested_fields_count = 0

    builder = SyntheticXlsxBuilder()

    # 1. Sheet "خرید-فروش": C (party), D (type), E (item), J (notes)
    row_bf = _sample_buy_sell_row_data(u_bf, 2)
    for col in RAW_CONTRACT_REGISTRY.sheets["خرید-فروش"].raw_columns:
        if col.value_kind == ValueKind.RAW_TEXT and col.field_name != "date_raw":
            row_bf[col.column_letter] = {"t": "n", "v": test_lexeme}
            tested_fields_count += 1
    builder.add_sheet_rows("خرید-فروش", [row_bf])

    # 2. Sheet "دریافت-پرداخت": C (party), D (type), F (notes), G (account),
    #    H (customer)
    row_dp = _sample_receipts_payments_row_data(u_dp, 2)
    for col in RAW_CONTRACT_REGISTRY.sheets["دریافت-پرداخت"].raw_columns:
        if col.value_kind == ValueKind.RAW_TEXT and col.field_name != "date_raw":
            row_dp[col.column_letter] = {"t": "n", "v": test_lexeme}
            tested_fields_count += 1
    builder.add_sheet_rows("دریافت-پرداخت", [row_dp])

    # 3. Sheet "ورود-خروج": C (party), D (type), E (item), I (notes), K (customer)
    row_vk = _sample_inventory_movements_row_data(u_vk, 2)
    for col in RAW_CONTRACT_REGISTRY.sheets["ورود-خروج"].raw_columns:
        if col.value_kind == ValueKind.RAW_TEXT and col.field_name != "date_raw":
            row_vk[col.column_letter] = {"t": "n", "v": test_lexeme}
            tested_fields_count += 1
    builder.add_sheet_rows("ورود-خروج", [row_vk])

    # 4. Sheet "لیست کسبه": B (party), C (phone)
    row_lk = _sample_business_parties_row_data(u_lk, 2)
    for col in RAW_CONTRACT_REGISTRY.sheets["لیست کسبه"].raw_columns:
        if col.value_kind == ValueKind.RAW_TEXT and col.field_name != "date_raw":
            row_lk[col.column_letter] = {"t": "n", "v": test_lexeme}
            tested_fields_count += 1
    builder.add_sheet_rows("لیست کسبه", [row_lk])

    # Assert exactly 16 RAW_TEXT non-date fields were identified and configured
    assert tested_fields_count == 16

    pkg_path = tmp_path / "all_16_raw_text_numeric_xml.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg_path)

    # Verify each sheet's extracted RAW_TEXT fields are exact str
    for s_name in RAW_CONTRACT_REGISTRY.sheets:
        extracted_row = res.snapshot.sheets[s_name].rows[0]
        for col in RAW_CONTRACT_REGISTRY.sheets[s_name].raw_columns:
            if col.value_kind == ValueKind.RAW_TEXT and col.field_name != "date_raw":
                extracted_val = extracted_row.raw_values[col.field_name]
                assert extracted_val == test_lexeme
                assert type(extracted_val) is str

    # Negative test 1: Numeric XML in date_raw MUST be rejected
    b_bad_date = SyntheticXlsxBuilder()
    row_bad_date = _sample_buy_sell_row_data(u_bf, 2)
    row_bad_date["B"] = {"t": "n", "v": "14030101"}
    b_bad_date.add_sheet_rows("خرید-فروش", [row_bad_date])
    b_bad_date.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    b_bad_date.add_sheet_rows(
        "ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)]
    )
    b_bad_date.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p_bad_date = tmp_path / "bad_date_num_xml.xlsx"
    p_bad_date.write_bytes(b_bad_date.build_bytes())
    with pytest.raises(XlsxCellError) as exc_date:
        read_xlsx_source_snapshot(p_bad_date)
    assert exc_date.value.reason == REASON_CELL_NUMERIC_XML_IN_TEXT_FIELD
    assert exc_date.value.cell_ref == "B2"

    # Negative test 2: Numeric XML in record_id UUID column MUST be rejected
    b_bad_id = SyntheticXlsxBuilder()
    row_bad_id = _sample_buy_sell_row_data(u_bf, 2)
    row_bad_id["Z"] = {"t": "n", "v": "123456"}
    b_bad_id.add_sheet_rows("خرید-فروش", [row_bad_id])
    b_bad_id.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    b_bad_id.add_sheet_rows(
        "ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)]
    )
    b_bad_id.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p_bad_id = tmp_path / "bad_id_num_xml.xlsx"
    p_bad_id.write_bytes(b_bad_id.build_bytes())
    with pytest.raises(XlsxCellError) as exc_id:
        read_xlsx_source_snapshot(p_bad_id)
    assert exc_id.value.reason == REASON_CELL_NUMERIC_XML_IN_TEXT_FIELD
    assert exc_id.value.cell_ref == "Z2"


def test_r5_01_namespace_strict_validation(tmp_path: Path) -> None:
    """Verify strict namespace validation on source text elements (A-02)."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    # 1. Negative: InlineStr with <t xmlns="">
    b1 = SyntheticXlsxBuilder()
    r1 = _sample_buy_sell_row_data(u_bf, 2)
    r1["C"] = {
        "t": "inlineStr",
        "raw_inner": '<is><t xmlns="">SYNTHETIC-RAW-BOUNDARY</t></is>',
    }
    b1.add_sheet_rows("خرید-فروش", [r1])
    b1.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b1.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b1.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p1 = tmp_path / "ns_inlinestr_t_empty.xlsx"
    p1.write_bytes(b1.build_bytes())
    with pytest.raises(XlsxCellError) as exc1:
        read_xlsx_source_snapshot(p1)
    assert exc1.value.reason == REASON_CELL_UNKNOWN_TYPE
    assert exc1.value.cell_ref == "C2"

    # 2. Negative: InlineStr with <r xmlns=""><t>...</t></r>
    b2 = SyntheticXlsxBuilder()
    r2 = _sample_buy_sell_row_data(u_bf, 2)
    r2["C"] = {
        "t": "inlineStr",
        "raw_inner": '<is><r xmlns=""><t>SYNTHETIC-RAW-BOUNDARY</t></r></is>',
    }
    b2.add_sheet_rows("خرید-فروش", [r2])
    b2.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b2.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b2.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p2 = tmp_path / "ns_inlinestr_r_empty.xlsx"
    p2.write_bytes(b2.build_bytes())
    with pytest.raises(XlsxCellError) as exc2:
        read_xlsx_source_snapshot(p2)
    assert exc2.value.reason == REASON_CELL_UNKNOWN_TYPE
    assert exc2.value.cell_ref == "C2"

    # 3. Negative: SST with <t xmlns="">
    b3 = SyntheticXlsxBuilder()
    r3 = _sample_buy_sell_row_data(u_bf, 2)
    r3["C"] = {"t": "s", "v": "0"}
    b3.override_sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'count="1" uniqueCount="1">\n'
        '  <si><t xmlns="">SYNTHETIC-RAW-BOUNDARY</t></si>\n'
        "</sst>"
    )
    b3.add_sheet_rows("خرید-فروش", [r3])
    b3.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b3.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b3.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p3 = tmp_path / "ns_sst_t_empty.xlsx"
    p3.write_bytes(b3.build_bytes())
    with pytest.raises(XlsxCellError) as exc3:
        read_xlsx_source_snapshot(p3)
    assert exc3.value.reason == REASON_CELL_UNKNOWN_TYPE

    # 4. Negative: SST with <r xmlns=""><t>...</t></r>
    b4 = SyntheticXlsxBuilder()
    r4 = _sample_buy_sell_row_data(u_bf, 2)
    r4["C"] = {"t": "s", "v": "0"}
    b4.override_sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'count="1" uniqueCount="1">\n'
        '  <si><r xmlns=""><t>SYNTHETIC-RAW-BOUNDARY</t></r></si>\n'
        "</sst>"
    )
    b4.add_sheet_rows("خرید-فروش", [r4])
    b4.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b4.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b4.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p4 = tmp_path / "ns_sst_r_empty.xlsx"
    p4.write_bytes(b4.build_bytes())
    with pytest.raises(XlsxCellError) as exc4:
        read_xlsx_source_snapshot(p4)
    assert exc4.value.reason == REASON_CELL_UNKNOWN_TYPE

    # 5. Negative: Foreign namespace
    b5 = SyntheticXlsxBuilder()
    r5 = _sample_buy_sell_row_data(u_bf, 2)
    r5["C"] = {
        "t": "inlineStr",
        "raw_inner": '<is><t xmlns="http://foreign.com">FOREIGN</t></is>',
    }
    b5.add_sheet_rows("خرید-فروش", [r5])
    b5.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b5.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b5.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p5 = tmp_path / "ns_foreign.xlsx"
    p5.write_bytes(b5.build_bytes())
    with pytest.raises(XlsxCellError) as exc5:
        read_xlsx_source_snapshot(p5)
    assert exc5.value.reason == REASON_CELL_UNKNOWN_TYPE
    assert exc5.value.cell_ref == "C2"

    # 6. Positive: Strict OpenXML namespace
    b6 = SyntheticXlsxBuilder(is_strict=True)
    b6.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u_bf, 2)])
    b6.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b6.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b6.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p6 = tmp_path / "ns_strict_valid.xlsx"
    p6.write_bytes(b6.build_bytes())
    res6 = read_xlsx_source_snapshot(p6)
    assert res6.snapshot.total_row_count == 4


def test_r5_01_coordinate_ascii_and_fullmatch_validation(
    tmp_path: Path,
) -> None:
    """Verify non-ASCII coordinates and newlines fail without partial snapshot
    (E-01).
    """
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    # Base row data for خرید-فروش: B2 date, C2 party activity, Z2 UUID
    row_base = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": "بازرگانی اول",
        "Z": str(u_bf),
    }

    # Helper builder to construct base XML
    b_base = SyntheticXlsxBuilder()
    sheet1_base_xml = b_base._build_sheet_xml("خرید-فروش", [row_base])
    assert 'r="C2"' in sheet1_base_xml

    # 1. Non-ASCII coordinates: Ü2, Α2 (Greek Alpha), K2 (Kelvin symbol)
    for bad_coord in ("Ü2", "Α2", "K2"):
        bad_xml = sheet1_base_xml.replace('r="C2"', f'r="{bad_coord}"')
        assert f'r="{bad_coord}"' in bad_xml

        b = SyntheticXlsxBuilder()
        b.add_sheet_rows("خرید-فروش", [row_base])
        b.raw_sheet_xml_overrides["خرید-فروش"] = bad_xml
        b.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
        b.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
        b.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

        pkg = tmp_path / f"bad_coord_{ord(bad_coord[0])}.xlsx"
        pkg.write_bytes(b.build_bytes())

        # Assert fixture XML bytes contain the exact attribute in row container
        with zipfile.ZipFile(pkg, "r") as zf:
            ws_text = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
            assert f'r="{bad_coord}"' in ws_text
            assert '<row r="2">' in ws_text

        with pytest.raises(XlsxStructureError) as exc:
            read_xlsx_source_snapshot(pkg)
        assert exc.value.reason == REASON_STRUCTURE_INVALID_CELL_REF
        assert exc.value.cell_ref == bad_coord

    # 2. Data cell with Line Feed character entity: C2&#10;
    bad_xml_lf = sheet1_base_xml.replace('r="C2"', 'r="C2&#10;"')
    assert 'r="C2&#10;"' in bad_xml_lf
    b_lf = SyntheticXlsxBuilder()
    b_lf.add_sheet_rows("خرید-فروش", [row_base])
    b_lf.raw_sheet_xml_overrides["خرید-فروش"] = bad_xml_lf
    b_lf.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b_lf.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b_lf.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    pkg_lf = tmp_path / "bad_coord_data_lf.xlsx"
    pkg_lf.write_bytes(b_lf.build_bytes())
    with zipfile.ZipFile(pkg_lf, "r") as zf:
        ws_text = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert 'r="C2&#10;"' in ws_text
    with pytest.raises(XlsxStructureError) as exc_lf:
        read_xlsx_source_snapshot(pkg_lf)
    assert exc_lf.value.reason == REASON_STRUCTURE_INVALID_CELL_REF

    # 3. Header cell with Line Feed character entity: B1&#10;
    bad_xml_hlf = sheet1_base_xml.replace('r="B1"', 'r="B1&#10;"')
    assert 'r="B1&#10;"' in bad_xml_hlf
    b_hlf = SyntheticXlsxBuilder()
    b_hlf.add_sheet_rows("خرید-فروش", [row_base])
    b_hlf.raw_sheet_xml_overrides["خرید-فروش"] = bad_xml_hlf
    b_hlf.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b_hlf.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b_hlf.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    pkg_hlf = tmp_path / "bad_coord_header_lf.xlsx"
    pkg_hlf.write_bytes(b_hlf.build_bytes())
    with zipfile.ZipFile(pkg_hlf, "r") as zf:
        ws_text = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert 'r="B1&#10;"' in ws_text
    with pytest.raises((XlsxStructureError, XlsxHeaderError)) as exc_hlf:
        read_xlsx_source_snapshot(pkg_hlf)
    assert exc_hlf.value.reason in (
        REASON_STRUCTURE_INVALID_CELL_REF,
        REASON_HEADER_TEXT_MISMATCH,
    )

    # 4. Out-of-bounds column: XFE2 (column 16385 > 16384)
    bad_xml_xfe = sheet1_base_xml.replace('r="C2"', 'r="XFE2"')
    assert 'r="XFE2"' in bad_xml_xfe
    b_xfe = SyntheticXlsxBuilder()
    b_xfe.add_sheet_rows("خرید-فروش", [row_base])
    b_xfe.raw_sheet_xml_overrides["خرید-فروش"] = bad_xml_xfe
    b_xfe.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b_xfe.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b_xfe.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    pkg_xfe = tmp_path / "bad_coord_xfe2.xlsx"
    pkg_xfe.write_bytes(b_xfe.build_bytes())
    with pytest.raises(XlsxStructureError) as exc_xfe:
        read_xlsx_source_snapshot(pkg_xfe)
    assert exc_xfe.value.reason == REASON_STRUCTURE_INVALID_CELL_REF
    assert exc_xfe.value.cell_ref == "XFE2"

    # 5. Out of bounds row: 1,048,577
    b_oob = SyntheticXlsxBuilder()
    row_oob = _sample_buy_sell_row_data(u_bf, 1048577)
    row_oob["__row_num__"] = 1048577
    b_oob.add_sheet_rows("خرید-فروش", [row_oob])
    b_oob.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b_oob.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b_oob.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p_oob = tmp_path / "oob_row.xlsx"
    p_oob.write_bytes(b_oob.build_bytes())
    with pytest.raises(XlsxStructureError) as exc_oob:
        read_xlsx_source_snapshot(p_oob)
    assert exc_oob.value.reason == REASON_STRUCTURE_ROW_OUT_OF_BOUNDS

    # 6. Valid ASCII coordinate C2 control succeeds
    b_valid = SyntheticXlsxBuilder()
    b_valid.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u_bf, 2)])
    b_valid.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    b_valid.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b_valid.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p_valid = tmp_path / "valid_coord.xlsx"
    p_valid.write_bytes(b_valid.build_bytes())
    res_valid = read_xlsx_source_snapshot(p_valid)
    assert res_valid.snapshot.sheets["خرید-فروش"].row_count == 1


def test_r5_01_negative_and_blank_activity_cases(tmp_path: Path) -> None:
    """Verify negative and blank cases for active and inactive rows (E-04)."""
    u_valid = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    # Case 1: Active row with blank fee G2 -> raises XlsxCellError
    b1 = SyntheticXlsxBuilder()
    row_empty_fee = _sample_buy_sell_row_data(u_valid, 2)
    row_empty_fee["G"] = {"t": "inlineStr", "is": ""}
    b1.add_sheet_rows("خرید-فروش", [row_empty_fee])
    b1.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b1.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b1.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p1 = tmp_path / "empty_str_fee.xlsx"
    p1.write_bytes(b1.build_bytes())
    with pytest.raises(XlsxCellError) as exc1:
        read_xlsx_source_snapshot(p1)
    assert exc1.value.reason == REASON_CELL_INVALID_NUMERIC_LEXEME
    assert exc1.value.cell_ref == "G2"
    assert exc1.value.physical_row_number == 2

    # Case 2: Active row with whitespace fee G2 -> raises XlsxCellError
    b2 = SyntheticXlsxBuilder()
    row_ws_fee = _sample_buy_sell_row_data(u_valid, 2)
    row_ws_fee["G"] = {"t": "inlineStr", "is": "   "}
    b2.add_sheet_rows("خرید-فروش", [row_ws_fee])
    b2.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b2.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b2.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p2 = tmp_path / "ws_fee.xlsx"
    p2.write_bytes(b2.build_bytes())
    with pytest.raises(XlsxCellError) as exc2:
        read_xlsx_source_snapshot(p2)
    assert exc2.value.reason == REASON_CELL_INVALID_NUMERIC_LEXEME
    assert exc2.value.cell_ref == "G2"

    # Case 3a: Active row with blank quantity F2 -> raises XlsxCellError
    b3_q = SyntheticXlsxBuilder()
    row_empty_q = _sample_buy_sell_row_data(u_valid, 2)
    row_empty_q["F"] = {"t": "inlineStr", "is": ""}
    b3_q.add_sheet_rows("خرید-فروش", [row_empty_q])
    b3_q.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b3_q.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b3_q.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p3_q = tmp_path / "empty_q.xlsx"
    p3_q.write_bytes(b3_q.build_bytes())
    with pytest.raises(XlsxCellError) as exc_q:
        read_xlsx_source_snapshot(p3_q)
    assert exc_q.value.reason == REASON_CELL_INVALID_NUMERIC_LEXEME
    assert exc_q.value.cell_ref == "F2"

    # Case 3b: Active row with whitespace quantity F2 -> raises XlsxCellError
    b3_ws_q = SyntheticXlsxBuilder()
    row_ws_q = _sample_buy_sell_row_data(u_valid, 2)
    row_ws_q["F"] = {"t": "inlineStr", "is": "   "}
    b3_ws_q.add_sheet_rows("خرید-فروش", [row_ws_q])
    b3_ws_q.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    b3_ws_q.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b3_ws_q.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p3_ws_q = tmp_path / "ws_q.xlsx"
    p3_ws_q.write_bytes(b3_ws_q.build_bytes())
    with pytest.raises(XlsxCellError) as exc_ws_q:
        read_xlsx_source_snapshot(p3_ws_q)
    assert exc_ws_q.value.reason == REASON_CELL_INVALID_NUMERIC_LEXEME
    assert exc_ws_q.value.cell_ref == "F2"

    # Case 4: Text "1e2" in numeric field -> rejected by WP-03 canonical validator
    b4 = SyntheticXlsxBuilder()
    row_text_exp = _sample_buy_sell_row_data(u_valid, 2)
    row_text_exp["G"] = {"t": "inlineStr", "is": "1e2"}
    b4.add_sheet_rows("خرید-فروش", [row_text_exp])
    b4.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b4.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b4.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p4 = tmp_path / "text_exp.xlsx"
    p4.write_bytes(b4.build_bytes())
    with pytest.raises(XlsxCellError) as exc4:
        read_xlsx_source_snapshot(p4)
    assert exc4.value.reason == REASON_CELL_INVALID_NUMERIC_LEXEME
    assert exc4.value.cell_ref == "G2"

    # Case 5: Fractional toman in numeric XML -> raises XlsxCellError
    b5 = SyntheticXlsxBuilder()
    row_frac_toman = _sample_buy_sell_row_data(u_valid, 2)
    row_frac_toman["G"] = {"t": "n", "v": "1500000.50"}
    b5.add_sheet_rows("خرید-فروش", [row_frac_toman])
    b5.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b5.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b5.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p5 = tmp_path / "frac_toman.xlsx"
    p5.write_bytes(b5.build_bytes())
    with pytest.raises(XlsxCellError) as exc5:
        read_xlsx_source_snapshot(p5)
    assert exc5.value.reason == REASON_CELL_INVALID_NUMERIC_LEXEME
    assert exc5.value.cell_ref == "G2"

    # Case 6: Truly missing optional field (e.g. discount H missing) -> None allowed
    b6 = SyntheticXlsxBuilder()
    row_no_discount = _sample_buy_sell_row_data(u_valid, 2)
    del row_no_discount["H"]  # cell H2 missing from XML
    b6.add_sheet_rows("خرید-فروش", [row_no_discount])
    b6.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b6.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b6.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p6 = tmp_path / "missing_discount.xlsx"
    p6.write_bytes(b6.build_bytes())
    res6 = read_xlsx_source_snapshot(p6)
    row6 = res6.snapshot.sheets["خرید-فروش"].rows[0]
    assert row6.raw_values["discount_toman_raw"] is None

    # Case 7: Inactive row where all activity columns are missing/whitespace
    b7 = SyntheticXlsxBuilder()
    b7.add_sheet_rows(
        "خرید-فروش",
        [
            _sample_buy_sell_row_data(u_valid, 2),
            {
                "__row_num__": 3,
                "A": "3",
                "B": "INVALID_DATE",  # Date is invalid but row is inactive
                "C": {"t": "inlineStr", "is": "   "},
                "D": None,
                "E": "",
                "F": {"t": "inlineStr", "is": "   "},
                "G": None,
                "H": None,
                "J": {"t": "inlineStr", "is": ""},
                "Z": "INVALID_UUID",  # UUID is invalid but row is inactive
            },
        ],
    )
    b7.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b7.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b7.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p7 = tmp_path / "inactive_row.xlsx"
    p7.write_bytes(b7.build_bytes())
    res7 = read_xlsx_source_snapshot(p7)
    assert res7.snapshot.sheets["خرید-فروش"].row_count == 1

    # Case 8: Isolated Numeric Zero as SOLE activity (other activity empty)
    # 8a: <v>0</v> numeric XML
    b8a = SyntheticXlsxBuilder()
    row_zero_xml = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": "",  # party empty
        "D": None,  # type missing
        "E": {"t": "inlineStr", "is": "   "},  # item whitespace
        "F": {"t": "n", "v": "0"},  # SOLE activity: numeric XML 0
        "G": None,
        "H": None,
        "J": None,
        "Z": str(u_valid),
    }
    b8a.add_sheet_rows("خرید-فروش", [row_zero_xml])
    b8a.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b8a.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b8a.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p8a = tmp_path / "isolated_zero_xml.xlsx"
    p8a.write_bytes(b8a.build_bytes())
    res8a = read_xlsx_source_snapshot(p8a)
    assert res8a.snapshot.sheets["خرید-فروش"].row_count == 1
    assert res8a.snapshot.sheets["خرید-فروش"].rows[0].raw_values[
        "quantity_raw"
    ] == Decimal("0")
    assert (
        type(res8a.snapshot.sheets["خرید-فروش"].rows[0].raw_values["quantity_raw"])
        is Decimal
    )

    # 8b: "0" ASCII text as SOLE activity
    b8b = SyntheticXlsxBuilder()
    row_zero_str = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": None,
        "D": None,
        "E": None,
        "F": None,
        "G": {"t": "inlineStr", "is": "0"},  # SOLE activity: ASCII text "0"
        "H": None,
        "J": None,
        "Z": str(u_valid),
    }
    b8b.add_sheet_rows("خرید-فروش", [row_zero_str])
    b8b.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b8b.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b8b.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p8b = tmp_path / "isolated_zero_str.xlsx"
    p8b.write_bytes(b8b.build_bytes())
    res8b = read_xlsx_source_snapshot(p8b)
    assert res8b.snapshot.sheets["خرید-فروش"].row_count == 1
    assert (
        res8b.snapshot.sheets["خرید-فروش"].rows[0].raw_values["unit_price_toman_raw"]
        == "0"
    )
    assert (
        type(
            res8b.snapshot.sheets["خرید-فروش"]
            .rows[0]
            .raw_values["unit_price_toman_raw"]
        )
        is str
    )

    # 8c: "۰" Persian text as SOLE activity
    b8c = SyntheticXlsxBuilder()
    row_zero_fa = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": None,
        "D": None,
        "E": None,
        "F": None,
        "G": {"t": "inlineStr", "is": "۰"},  # SOLE activity: Persian text "۰"
        "H": None,
        "J": None,
        "Z": str(u_valid),
    }
    b8c.add_sheet_rows("خرید-فروش", [row_zero_fa])
    b8c.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b8c.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b8c.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p8c = tmp_path / "isolated_zero_fa.xlsx"
    p8c.write_bytes(b8c.build_bytes())
    res8c = read_xlsx_source_snapshot(p8c)
    assert res8c.snapshot.sheets["خرید-فروش"].row_count == 1
    assert (
        res8c.snapshot.sheets["خرید-فروش"].rows[0].raw_values["unit_price_toman_raw"]
        == "۰"
    )
    assert (
        type(
            res8c.snapshot.sheets["خرید-فروش"]
            .rows[0]
            .raw_values["unit_price_toman_raw"]
        )
        is str
    )


def test_r5_01_independent_oracle_all_four_sheets(tmp_path: Path) -> None:
    """Type and scale-sensitive independent oracle verifying snapshots (E-02)."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    # 1. Independent raw dicts matching schema
    bf_raw_expected = {
        "date_raw": "1403/05/15",
        "party_name_raw": "بازرگانی احمدی",
        "transaction_type_raw": "خرید",
        "item_name_raw": "طلای آبشده",
        "quantity_raw": " +0012.3400 ",  # Raw string preserved
        "unit_price_toman_raw": Decimal("1500000.00"),  # Numeric XML Decimal preserved
        "discount_toman_raw": Decimal("0"),
        "notes_raw": "توضیحات فاکتور",
    }

    dp_raw_expected = {
        "date_raw": "1403/05/15",
        "party_name_raw": "بازرگانی احمدی",
        "entry_type_raw": "دریافت چک",
        "amount_toman_raw": " +005000000 ",  # Raw string preserved
        "notes_raw": "توضیحات دریافت",
        "account_code_raw": "123.4000",  # Numeric XML in RAW_TEXT returned as str
        "customer_flag_raw": None,
    }

    vk_raw_expected = {
        "date_raw": "1403/05/15",
        "party_name_raw": "بازرگانی احمدی",
        "movement_type_raw": "ورود",
        "item_name_raw": "طلای ۱۸ عیار",
        "quantity_raw": "۱۲.۳۴",  # Persian text preserved
        "purity_raw": Decimal("750"),
        "notes_raw": "توضیحات ورود",
        "customer_flag_raw": None,
    }

    lk_raw_expected = {
        "party_name_raw": "بازرگانی احمدی",
        "phone_number_raw": "SYNTHETIC-PHONE-001",
    }

    expected_raw_by_sheet: dict[str, dict[str, Any]] = {
        "خرید-فروش": bf_raw_expected,
        "دریافت-پرداخت": dp_raw_expected,
        "ورود-خروج": vk_raw_expected,
        "لیست کسبه": lk_raw_expected,
    }

    # 2. Build workbook with exact matching XML
    builder = SyntheticXlsxBuilder()
    builder.add_sheet_rows(
        "خرید-فروش",
        [
            {
                "__row_num__": 2,
                "A": "2",
                "B": "1403/05/15",
                "C": "بازرگانی احمدی",
                "D": "خرید",
                "E": "طلای آبشده",
                "F": {"t": "inlineStr", "is": " +0012.3400 "},
                "G": {"t": "n", "v": "1500000.00"},
                "H": {"t": "n", "v": "0"},
                "J": "توضیحات فاکتور",
                "Z": str(u_bf),
            }
        ],
    )
    builder.add_sheet_rows(
        "دریافت-پرداخت",
        [
            {
                "__row_num__": 2,
                "A": "2",
                "B": "1403/05/15",
                "C": "بازرگانی احمدی",
                "D": "دریافت چک",
                "E": {"t": "inlineStr", "is": " +005000000 "},
                "F": "توضیحات دریافت",
                "G": {"t": "n", "v": "123.4000"},  # Numeric XML in RAW_TEXT
                "P": str(u_dp),
            }
        ],
    )
    builder.add_sheet_rows(
        "ورود-خروج",
        [
            {
                "__row_num__": 2,
                "A": "2",
                "B": "1403/05/15",
                "C": "بازرگانی احمدی",
                "D": "ورود",
                "E": "طلای ۱۸ عیار",
                "F": {"t": "inlineStr", "is": "۱۲.۳۴"},
                "G": {"t": "n", "v": "750"},
                "I": "توضیحات ورود",
                "P": str(u_vk),
            }
        ],
    )
    builder.add_sheet_rows(
        "لیست کسبه",
        [
            {
                "__row_num__": 2,
                "A": "2",
                "B": "بازرگانی احمدی",
                "C": "SYNTHETIC-PHONE-001",
                "D": str(u_lk),
            }
        ],
    )

    pkg_path = tmp_path / "oracle_four_sheets.xlsx"
    pkg_path.write_bytes(builder.build_bytes())

    # 3. Read via reader
    res = read_xlsx_source_snapshot(pkg_path)

    # 4. Compute expected ValidatedSourceWorkbookSnapshot independently via WP-04
    expected_inputs = [
        ("خرید-فروش", [SourceRowInput(u_bf, bf_raw_expected)]),
        ("دریافت-پرداخت", [SourceRowInput(u_dp, dp_raw_expected)]),
        ("ورود-خروج", [SourceRowInput(u_vk, vk_raw_expected)]),
        ("لیست کسبه", [SourceRowInput(u_lk, lk_raw_expected)]),
    ]
    expected_snapshot = build_source_workbook_snapshot(expected_inputs)

    # 5. Full snapshot comparison
    assert res.snapshot == expected_snapshot
    assert res.snapshot.total_row_count == 4
    assert res.snapshot.total_row_count == expected_snapshot.total_row_count

    # 6. Detailed per-sheet, per-field type and scale checks
    for s_name in RAW_CONTRACT_REGISTRY.sheets:
        actual_sheet = res.snapshot.sheets[s_name]
        expected_sheet = expected_snapshot.sheets[s_name]
        expected_raw = expected_raw_by_sheet[s_name]

        assert actual_sheet.row_count == expected_sheet.row_count
        assert actual_sheet.sheet_snapshot_hash == expected_sheet.sheet_snapshot_hash
        assert actual_sheet.rows[0].source_hash == expected_sheet.rows[0].source_hash
        assert actual_sheet.rows[0].raw_values == expected_sheet.rows[0].raw_values

        # Check raw keys order strictly matches registry column order
        contract = RAW_CONTRACT_REGISTRY.sheets[s_name]
        expected_keys = [c.field_name for c in contract.raw_columns]
        assert list(actual_sheet.rows[0].raw_values.keys()) == expected_keys

        # Check exact field types and Decimal scale preservation
        for field_name, expected_val in expected_raw.items():
            actual_val = actual_sheet.rows[0].raw_values[field_name]
            assert type(actual_val) is type(expected_val)
            if isinstance(expected_val, Decimal):
                assert type(actual_val) is not int
                assert actual_val.as_tuple() == expected_val.as_tuple()
            else:
                assert actual_val == expected_val

        # Direct verification with WP-03 hashing from expected dict
        expected_row_hash = compute_source_hash(s_name, expected_raw).source_hash
        assert actual_sheet.rows[0].source_hash == expected_row_hash

    # 7. Type-and-scale mutation sensitivity check
    # Demonstrate that mutating Decimal to int or altering scale fails type/scale assert
    mutated_int_val = 1500000
    actual_g2 = (
        res.snapshot.sheets["خرید-فروش"].rows[0].raw_values["unit_price_toman_raw"]
    )
    assert type(actual_g2) is not type(mutated_int_val)

    mutated_scale_val = Decimal("1500000.0")
    assert actual_g2.as_tuple() != mutated_scale_val.as_tuple()
