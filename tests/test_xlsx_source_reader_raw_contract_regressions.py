"""Independent regression tests for R5-A: Raw value preservation and validation.

Verifies:
1. Text in numeric fields (inlineStr, direct str, SST) retains exact raw str.
2. Financial numeric XML (<v>) directly becomes Decimal, preserving scale.
3. XML exponent '1e2' in numeric XML is accepted as Decimal, text rejected.
4. Active rows with explicit blank text "" or "   " in numeric fields raise error.
5. Truly missing cells remain None.
6. Inactive rows are omitted without validating leftover date/UUID.
7. Numeric zero (<v>0</v>, "0", "۰") is active.
8. Independent oracle across all 4 sheets verifies Raw types, snapshot, and hashes.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
)
from accounting_contracts.source_change_plan import (
    SourceRowInput,
    build_source_workbook_snapshot,
)
from accounting_local_agent.xlsx_source_reader import (
    REASON_CELL_INVALID_NUMERIC_LEXEME,
    XlsxCellError,
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
    assert isinstance(r2.raw_values["quantity_raw"], str)
    assert r2.raw_values["unit_price_toman_raw"] == " +001000 "
    assert isinstance(r2.raw_values["unit_price_toman_raw"], str)

    # Row 3 asserts: exactly preserves direct string and shared string
    r3 = bf_rows[u2]
    assert r3.raw_values["quantity_raw"] == " -000.5000 "
    assert isinstance(r3.raw_values["quantity_raw"], str)
    assert r3.raw_values["unit_price_toman_raw"] == " +002000 "
    assert isinstance(r3.raw_values["unit_price_toman_raw"], str)

    # Row 4 asserts: Persian digits preserved as raw str
    r4 = bf_rows[u3]
    assert r4.raw_values["quantity_raw"] == "۱۲.۳۴"
    assert isinstance(r4.raw_values["quantity_raw"], str)
    assert r4.raw_values["unit_price_toman_raw"] == "۱۵۰۰۰۰۰"
    assert isinstance(r4.raw_values["unit_price_toman_raw"], str)

    # Row 5 asserts: Arabic digits preserved as raw str
    r5 = bf_rows[u4]
    assert r5.raw_values["quantity_raw"] == "١٢.٣٤"
    assert isinstance(r5.raw_values["quantity_raw"], str)
    assert r5.raw_values["unit_price_toman_raw"] == "١٥٠٠٠٠٠"
    assert isinstance(r5.raw_values["unit_price_toman_raw"], str)


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
    assert isinstance(f2_val, Decimal)
    assert f2_val.as_tuple() == Decimal("12.3400").as_tuple()
    assert isinstance(g2_val, Decimal)
    assert not isinstance(g2_val, int)
    assert g2_val.as_tuple() == Decimal("100.000").as_tuple()

    # Row 3: G2 is exact large Decimal
    r3 = bf_rows[u2]
    g3_val = r3.raw_values["unit_price_toman_raw"]
    assert isinstance(g3_val, Decimal)
    assert g3_val == Decimal("123456789012345678901234567890")

    # Row 4: G2 is Decimal("1e30")
    r4 = bf_rows[u3]
    g4_val = r4.raw_values["unit_price_toman_raw"]
    assert isinstance(g4_val, Decimal)
    assert g4_val == Decimal("1e30")

    # Row 5: F2 is Decimal("1e2")
    r5 = bf_rows[u4]
    f5_val = r5.raw_values["quantity_raw"]
    assert isinstance(f5_val, Decimal)
    assert f5_val == Decimal("1e2")


def test_r5_01_negative_and_blank_activity_cases(tmp_path: Path) -> None:
    """Verify negative and blank cases for active and inactive rows."""
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

    # Case 3: Text "1e2" in numeric field -> rejected by WP-03 canonical validator
    b3 = SyntheticXlsxBuilder()
    row_text_exp = _sample_buy_sell_row_data(u_valid, 2)
    row_text_exp["G"] = {"t": "inlineStr", "is": "1e2"}
    b3.add_sheet_rows("خرید-فروش", [row_text_exp])
    b3.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b3.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b3.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p3 = tmp_path / "text_exp.xlsx"
    p3.write_bytes(b3.build_bytes())
    with pytest.raises(XlsxCellError) as exc3:
        read_xlsx_source_snapshot(p3)
    assert exc3.value.reason == REASON_CELL_INVALID_NUMERIC_LEXEME
    assert exc3.value.cell_ref == "G2"

    # Case 4: Fractional toman in numeric XML -> raises XlsxCellError
    b4 = SyntheticXlsxBuilder()
    row_frac_toman = _sample_buy_sell_row_data(u_valid, 2)
    row_frac_toman["G"] = {"t": "n", "v": "1500000.50"}
    b4.add_sheet_rows("خرید-فروش", [row_frac_toman])
    b4.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b4.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b4.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p4 = tmp_path / "frac_toman.xlsx"
    p4.write_bytes(b4.build_bytes())
    with pytest.raises(XlsxCellError) as exc4:
        read_xlsx_source_snapshot(p4)
    assert exc4.value.reason == REASON_CELL_INVALID_NUMERIC_LEXEME
    assert exc4.value.cell_ref == "G2"

    # Case 5: Truly missing optional field (e.g. discount H missing) -> None allowed
    b5 = SyntheticXlsxBuilder()
    row_no_discount = _sample_buy_sell_row_data(u_valid, 2)
    del row_no_discount["H"]  # cell H2 missing from XML
    b5.add_sheet_rows("خرید-فروش", [row_no_discount])
    b5.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b5.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b5.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p5 = tmp_path / "missing_discount.xlsx"
    p5.write_bytes(b5.build_bytes())
    res5 = read_xlsx_source_snapshot(p5)
    row5 = res5.snapshot.sheets["خرید-فروش"].rows[0]
    assert row5.raw_values["discount_toman_raw"] is None

    # Case 6: Inactive row where all activity columns are missing/whitespace
    b6 = SyntheticXlsxBuilder()
    b6.add_sheet_rows(
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
    b6.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b6.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b6.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p6 = tmp_path / "inactive_row.xlsx"
    p6.write_bytes(b6.build_bytes())
    res6 = read_xlsx_source_snapshot(p6)
    assert res6.snapshot.sheets["خرید-فروش"].row_count == 1

    # Case 7: Numeric zero (<v>0</v> or "0" or "۰") in activity column counts as ACTIVE
    b7 = SyntheticXlsxBuilder()
    row_zero_act = _sample_buy_sell_row_data(u_valid, 2)
    row_zero_act["F"] = {"t": "n", "v": "0"}  # 0 quantity
    row_zero_act["G"] = {"t": "inlineStr", "is": "۰"}  # 0 toman fee in Persian
    b7.add_sheet_rows("خرید-فروش", [row_zero_act])
    b7.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b7.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b7.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p7 = tmp_path / "zero_act.xlsx"
    p7.write_bytes(b7.build_bytes())
    res7 = read_xlsx_source_snapshot(p7)
    assert res7.snapshot.sheets["خرید-فروش"].row_count == 1
    r7 = res7.snapshot.sheets["خرید-فروش"].rows[0]
    assert r7.raw_values["quantity_raw"] == Decimal("0")
    assert r7.raw_values["unit_price_toman_raw"] == "۰"

    # Case 8: Formula in numeric field with invalid value -> formula excluded
    b8 = SyntheticXlsxBuilder()
    row_f_num = _sample_buy_sell_row_data(u_valid, 2)
    row_f_num["H"] = {"raw_inner": "<f>F2*G2</f><v>NOT_A_NUMBER</v>"}
    b8.add_sheet_rows("خرید-فروش", [row_f_num])
    b8.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)])
    b8.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    b8.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p8 = tmp_path / "formula_numeric.xlsx"
    p8.write_bytes(b8.build_bytes())
    res8 = read_xlsx_source_snapshot(p8)
    assert (
        res8.snapshot.sheets["خرید-فروش"].rows[0].raw_values["discount_toman_raw"]
        is None
    )


def test_r5_01_independent_oracle_all_four_sheets(tmp_path: Path) -> None:
    """Independent oracle building raw fixture and verifying snapshots & hashes."""
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
        "account_code_raw": None,
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

    # 5. Full comparison
    assert res.snapshot.total_row_count == 4
    assert res.snapshot.total_row_count == expected_snapshot.total_row_count

    for s_name in RAW_CONTRACT_REGISTRY.sheets:
        actual_sheet = res.snapshot.sheets[s_name]
        expected_sheet = expected_snapshot.sheets[s_name]
        assert actual_sheet.row_count == expected_sheet.row_count
        assert actual_sheet.sheet_snapshot_hash == expected_sheet.sheet_snapshot_hash
        assert actual_sheet.rows[0].source_hash == expected_sheet.rows[0].source_hash
        assert actual_sheet.rows[0].raw_values == expected_sheet.rows[0].raw_values
