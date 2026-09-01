"""Standalone regression tests for WP-05 R5-B (SST selection, activity, RB-01..04).

Verifies:
1. RB-01: Correct <is> container extraction in Pass 1 activity discovery across
   plain text, rich runs (<r><t>), XML comments/tails, ASCII/Persian zeros,
   and Strict vs Transitional OpenXML namespaces.
2. RB-02: Unified escape/whitespace semantics between discovery and decode:
   escaped whitespace (_x0020_, _x0009_, _x000D_, _x00A0_) is inactive,
   double-escaped _x005F_x0020_ is active, and _x0000_ (NUL) is active.
3. RB-03: Truly lazy secondary SST index evaluation and accurate consumer
   coordinates on typed cell errors.
4. RB-04: Full snapshot equality, WP-03 hashes, Decimal scale preservation with
   as_tuple(), equivalent representations, physical row/cell permutations,
   read-only integrity on success and failure, and observer pass bounds.
5. Cases 1-10: Inactive rows with corrupt SST 0, unsupported leaf tags,
   covered formula caches, XML comments in activity, and paired controls.
"""

from __future__ import annotations

import uuid
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from accounting_contracts.canonical_hashing import compute_source_hash
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
)
from accounting_contracts.source_change_plan import (
    SourceRowInput,
    SourceSheetInput,
    build_source_workbook_snapshot,
)
from accounting_local_agent import xlsx_source_reader
from accounting_local_agent.xlsx_source_reader import (
    REASON_CELL_INVALID_SST_INDEX,
    REASON_CELL_SST_INDEX_OUT_OF_RANGE,
    REASON_CELL_UNKNOWN_TYPE,
    REASON_CELL_UNPAIRED_SURROGATE,
    XlsxCellError,
    read_xlsx_source_snapshot,
)
from lxml import etree  # type: ignore[import-untyped]
from test_xlsx_source_reader import (
    SyntheticXlsxBuilder,
    _make_uuid7,
    _sample_business_parties_row_data,
    _sample_buy_sell_row_data,
    _sample_inventory_movements_row_data,
    _sample_receipts_payments_row_data,
)


def _assert_row_equality(actual_row: Any, expected_row: Any, sheet_name: str) -> None:
    """Assert strict type/value equality including Decimal.as_tuple() & key order."""
    assert actual_row.stable_id == expected_row.stable_id
    exp_dict = (
        expected_row.raw_values
        if hasattr(expected_row, "raw_values")
        else expected_row.source_values
    )
    act_dict = (
        actual_row.raw_values
        if hasattr(actual_row, "raw_values")
        else actual_row.source_values
    )
    expected_hash = compute_source_hash(sheet_name, exp_dict).source_hash
    assert actual_row.source_hash == expected_hash

    # Verify raw_values keys order strictly matches RAW_CONTRACT_REGISTRY column order
    expected_column_order = tuple(
        col.field_name for col in RAW_CONTRACT_REGISTRY.sheets[sheet_name].raw_columns
    )
    assert tuple(act_dict.keys()) == expected_column_order, (
        f"Sheet {sheet_name!r}: raw_values keys order mismatch "
        f"actual={tuple(act_dict.keys())} vs expected={expected_column_order}"
    )
    assert set(act_dict.keys()) == set(exp_dict.keys())

    for k, exp_v in exp_dict.items():
        act_v = act_dict[k]
        assert type(act_v) is type(exp_v), (
            f"Sheet {sheet_name!r}, Field {k!r}: type mismatch "
            f"actual={type(act_v)} vs expected={type(exp_v)}"
        )
        if isinstance(exp_v, Decimal):
            assert act_v.as_tuple() == exp_v.as_tuple(), (
                f"Sheet {sheet_name!r}, Field {k!r}: Decimal scale mismatch "
                f"actual={act_v.as_tuple()} vs expected={exp_v.as_tuple()}"
            )
        else:
            assert act_v == exp_v, (
                f"Sheet {sheet_name!r}, Field {k!r}: value mismatch "
                f"actual={act_v!r} vs expected={exp_v!r}"
            )


def _assert_snapshot_and_locations(
    actual_res: Any,
    expected_snapshot: Any,
    expected_locations: dict[uuid.UUID, int],
) -> None:
    """Assert full snapshot equality, sheet hashes, and physical row locations."""
    assert actual_res.snapshot == expected_snapshot
    assert actual_res.snapshot.total_row_count == expected_snapshot.total_row_count
    for sheet_name in RAW_CONTRACT_REGISTRY.sheets:
        act_sheet = actual_res.snapshot.sheets[sheet_name]
        exp_sheet = expected_snapshot.sheets[sheet_name]
        assert act_sheet.row_count == exp_sheet.row_count
        assert act_sheet.sheet_snapshot_hash == exp_sheet.sheet_snapshot_hash
        for act_row, exp_row in zip(act_sheet.rows, exp_sheet.rows, strict=True):
            _assert_row_equality(act_row, exp_row, sheet_name)
            assert act_row.stable_id in actual_res.locations_by_uuid
            loc = actual_res.locations_by_uuid[act_row.stable_id]
            assert loc.sheet_name == sheet_name
            assert loc.physical_row_number == expected_locations[act_row.stable_id]


def _make_sample_expected_rows_3_sheets(
    u_dp: uuid.UUID, u_vk: uuid.UUID, u_lk: uuid.UUID
) -> tuple[SourceRowInput, SourceRowInput, SourceRowInput]:
    """Construct independent expected rows matching the standard 3 sample sheets."""
    exp_dp = SourceRowInput(
        stable_id=u_dp,
        source_values={
            "date_raw": "1403/01/01",
            "party_name_raw": "همکار نمونه",
            "entry_type_raw": "RS",
            "amount_toman_raw": "50000000",
            "notes_raw": "تسویه حساب",
            "account_code_raw": "101",
            "customer_flag_raw": "1",
        },
    )
    exp_vk = SourceRowInput(
        stable_id=u_vk,
        source_values={
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
    exp_lk = SourceRowInput(
        stable_id=u_lk,
        source_values={
            "party_name_raw": "فروشگاه نمونه",
            "phone_number_raw": "SYNTHETIC-PHONE-001",
        },
    )
    return exp_dp, exp_vk, exp_lk


# ==============================================================================
# RB-01: Correct container in inlineStr activity extraction & Namespace Matrix
# ==============================================================================


def test_rb_01_inlinestr_activity_with_sst_id_and_date_across_all_four_sheets(
    tmp_path: Path,
) -> None:
    """RB-01: inlineStr activity with SST ID and SST date across all 4 sheets."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = [
        str(u_bf),
        str(u_dp),
        str(u_vk),
        str(u_lk),
        "1403/05/15",
    ]

    # 1. خرید-فروش: inlineStr plain text in C2, native numeric XML in G2, Z2 uses SST 0
    row_bf = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {"t": "inlineStr", "raw_inner": "<is><t>بازرگانی فعال اینلاین</t></is>"},
        "D": "خرید",
        "E": "طلای ۱۸ عیار",
        "F": "10.500",
        "G": {"t": "", "v": "1500000.00"},  # Native financial numeric XML with scale
        "H": "0",
        "J": "توضیحات",
        "Z": {"t": "s", "v": "0"},
    }
    builder.add_sheet_rows("خرید-فروش", [row_bf])

    # 2. دریافت-پرداخت: inlineStr rich runs in C2, P2 uses SST 1
    row_dp = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {
            "t": "inlineStr",
            "raw_inner": "<is><r><t>طرف </t></r><r><t>حساب</t></r></is>",
        },
        "D": "RS",
        "E": "5000000",
        "F": "توضیحات دریافت",
        "G": "101",
        "H": "1",
        "P": {"t": "s", "v": "1"},
    }
    builder.add_sheet_rows("دریافت-پرداخت", [row_dp])

    # 3. ورود-خروج: inlineStr with comment in C2, B2 uses SST 4, P2 uses SST 2
    row_vk = {
        "__row_num__": 2,
        "A": "2",
        "B": {"t": "s", "v": "4"},
        "C": {
            "t": "inlineStr",
            "raw_inner": "<is><t><!--comment-->انبار مرکزی</t></is>",
        },
        "D": "ورود",
        "E": "طلای ۱۸ عیار",
        "F": "10",
        "G": "750",
        "I": "توضیحات ورود",
        "K": "1",
        "P": {"t": "s", "v": "2"},
    }
    builder.add_sheet_rows("ورود-خروج", [row_vk])

    # 4. لیست کسبه: inlineStr in B2, D2 uses SST 3
    row_lk = {
        "__row_num__": 2,
        "A": "2",
        "B": {"t": "inlineStr", "raw_inner": "<is><t>کاسب نمونه</t></is>"},
        "C": "SYNTHETIC-PHONE-001",
        "D": {"t": "s", "v": "3"},
    }
    builder.add_sheet_rows("لیست کسبه", [row_lk])

    pkg = tmp_path / "rb_01_all_four_sheets.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)

    # Build expected snapshot independently
    exp_bf = SourceRowInput(
        stable_id=u_bf,
        source_values={
            "date_raw": "1403/05/15",
            "party_name_raw": "بازرگانی فعال اینلاین",
            "transaction_type_raw": "خرید",
            "item_name_raw": "طلای ۱۸ عیار",
            "quantity_raw": "10.500",
            "unit_price_toman_raw": Decimal("1500000.00"),
            "discount_toman_raw": "0",
            "notes_raw": "توضیحات",
        },
    )
    exp_dp = SourceRowInput(
        stable_id=u_dp,
        source_values={
            "date_raw": "1403/05/15",
            "party_name_raw": "طرف حساب",
            "entry_type_raw": "RS",
            "amount_toman_raw": "5000000",
            "notes_raw": "توضیحات دریافت",
            "account_code_raw": "101",
            "customer_flag_raw": "1",
        },
    )
    exp_vk = SourceRowInput(
        stable_id=u_vk,
        source_values={
            "date_raw": "1403/05/15",
            "party_name_raw": "انبار مرکزی",
            "movement_type_raw": "ورود",
            "item_name_raw": "طلای ۱۸ عیار",
            "quantity_raw": "10",
            "purity_raw": "750",
            "notes_raw": "توضیحات ورود",
            "customer_flag_raw": "1",
        },
    )
    exp_lk = SourceRowInput(
        stable_id=u_lk,
        source_values={
            "party_name_raw": "کاسب نمونه",
            "phone_number_raw": "SYNTHETIC-PHONE-001",
        },
    )
    expected_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[exp_bf]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )

    expected_locations = {
        u_bf: 2,
        u_dp: 2,
        u_vk: 2,
        u_lk: 2,
    }
    _assert_snapshot_and_locations(res, expected_snapshot, expected_locations)


def test_rb_01_inlinestr_zeros_persian_and_ascii_count_as_activity(
    tmp_path: Path,
) -> None:
    """RB-01: Persian zero '۰' and ASCII zero '0' in inlineStr count as activity."""
    u1 = _make_uuid7(b"0000000000000001")
    u2 = _make_uuid7(b"0000000000000002")
    u_dp = _make_uuid7(b"0000000000000003")
    u_vk = _make_uuid7(b"0000000000000004")
    u_lk = _make_uuid7(b"0000000000000005")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = [str(u1), str(u2)]

    rows_bf = [
        {
            "__row_num__": 2,
            "A": "2",
            "B": "1403/05/15",
            "C": {"t": "inlineStr", "raw_inner": "<is><t>0</t></is>"},
            "Z": {"t": "s", "v": "0"},
        },
        {
            "__row_num__": 3,
            "A": "3",
            "B": "1403/05/16",
            "C": {"t": "inlineStr", "raw_inner": "<is><t>۰</t></is>"},
            "Z": {"t": "s", "v": "1"},
        },
    ]
    builder.add_sheet_rows("خرید-فروش", rows_bf)
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "rb_01_inlinestr_zeros.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_r1 = SourceRowInput(
        stable_id=u1,
        source_values={
            "date_raw": "1403/05/15",
            "party_name_raw": "0",
            "transaction_type_raw": None,
            "item_name_raw": None,
            "quantity_raw": None,
            "unit_price_toman_raw": None,
            "discount_toman_raw": None,
            "notes_raw": None,
        },
    )
    exp_r2 = SourceRowInput(
        stable_id=u2,
        source_values={
            "date_raw": "1403/05/16",
            "party_name_raw": "۰",
            "transaction_type_raw": None,
            "item_name_raw": None,
            "quantity_raw": None,
            "unit_price_toman_raw": None,
            "discount_toman_raw": None,
            "notes_raw": None,
        },
    )
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[exp_r1, exp_r2]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u1: 2, u2: 3, u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


@pytest.mark.parametrize("is_strict", [False, True])
def test_rb_01_sst_date_and_literal_id_with_inline_activity_strict_and_transitional(
    tmp_path: Path,
    is_strict: bool,
) -> None:
    """RB-01: Strict & Transitional: literal ID, SST date, inline activity."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder(is_strict=is_strict)
    builder.shared_strings = ["1403/05/15", "1403/05/16", "1403/05/17"]

    # In 3 date sheets: date is SST, ID is literal string, sole activity is inlineStr
    builder.add_sheet_rows(
        "خرید-فروش",
        [
            {
                "__row_num__": 2,
                "A": "2",
                "B": {"t": "s", "v": "0"},
                "C": {"t": "inlineStr", "raw_inner": "<is><t>بازرگانی اول</t></is>"},
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
                "B": {"t": "s", "v": "1"},
                "C": {"t": "inlineStr", "raw_inner": "<is><t>طرف حساب دوم</t></is>"},
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
                "B": {"t": "s", "v": "2"},
                "C": {"t": "inlineStr", "raw_inner": "<is><t>انبار سوم</t></is>"},
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
                "B": {"t": "inlineStr", "raw_inner": "<is><t>کاسب چهارم</t></is>"},
                "D": str(u_lk),
            }
        ],
    )

    pkg = tmp_path / f"rb_01_strict_{is_strict}.xlsx"
    pkg.write_bytes(builder.build_bytes())

    # Verify actual generated XML namespace in ZIP before calling reader
    expected_ns = (
        "http://purl.oclc.org/ooxml/spreadsheetml/main"
        if is_strict
        else "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    )
    with zipfile.ZipFile(pkg, "r") as zf_check:
        for ws_name in [
            "xl/worksheets/sheet1.xml",
            "xl/worksheets/sheet2.xml",
            "xl/worksheets/sheet3.xml",
            "xl/worksheets/sheet4.xml",
        ]:
            xml_text = zf_check.read(ws_name).decode("utf-8")
            assert expected_ns in xml_text, (
                f"Expected namespace {expected_ns} not found in {ws_name}"
            )

    res = read_xlsx_source_snapshot(pkg)

    exp_bf = SourceRowInput(
        stable_id=u_bf,
        source_values={
            "date_raw": "1403/05/15",
            "party_name_raw": "بازرگانی اول",
            "transaction_type_raw": None,
            "item_name_raw": None,
            "quantity_raw": None,
            "unit_price_toman_raw": None,
            "discount_toman_raw": None,
            "notes_raw": None,
        },
    )
    exp_dp = SourceRowInput(
        stable_id=u_dp,
        source_values={
            "date_raw": "1403/05/16",
            "party_name_raw": "طرف حساب دوم",
            "entry_type_raw": None,
            "amount_toman_raw": None,
            "notes_raw": None,
            "account_code_raw": None,
            "customer_flag_raw": None,
        },
    )
    exp_vk = SourceRowInput(
        stable_id=u_vk,
        source_values={
            "date_raw": "1403/05/17",
            "party_name_raw": "انبار سوم",
            "movement_type_raw": None,
            "item_name_raw": None,
            "quantity_raw": None,
            "purity_raw": None,
            "notes_raw": None,
            "customer_flag_raw": None,
        },
    )
    exp_lk = SourceRowInput(
        stable_id=u_lk,
        source_values={
            "party_name_raw": "کاسب چهارم",
            "phone_number_raw": None,
        },
    )
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[exp_bf]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_bf: 2, u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


@pytest.mark.parametrize(
    "sheet_name",
    ["خرید-فروش", "دریافت-پرداخت", "ورود-خروج", "لیست کسبه"],
)
@pytest.mark.parametrize(
    ("mode_name", "raw_inner_xml", "expected_val"),
    [
        ("inline_plain", "<is><t>متن ساده</t></is>", "متن ساده"),
        (
            "rich_runs",
            "<is><r><t>متن </t></r><r><t>ترکیبی</t></r></is>",
            "متن ترکیبی",
        ),
        (
            "comment_tail",
            "<is><t><!--cmt-->متن با کامنت</t></is>",
            "متن با کامنت",
        ),
        ("ascii_zero", "<is><t>0</t></is>", "0"),
        ("persian_zero", "<is><t>۰</t></is>", "۰"),
    ],
)
def test_rb_01_four_sheets_five_activity_modes_matrix(
    tmp_path: Path,
    sheet_name: str,
    mode_name: str,
    raw_inner_xml: str,
    expected_val: str,
) -> None:
    """RB-01: 4 sheets × 5 activity modes (plain, rich, comment, 0, ۰)."""
    u_target = _make_uuid7(f"{sheet_name[:4]}_{mode_name[:4]}".encode())
    builder = SyntheticXlsxBuilder()
    builder.shared_strings = [str(u_target), "1403/05/15"]

    sheet_contract = RAW_CONTRACT_REGISTRY.sheets[sheet_name]
    id_col_letter = sheet_contract.stable_id_column.column_letter

    raw_col_by_letter = {c.column_letter: c for c in sheet_contract.raw_columns}
    act_col_letter = sheet_contract.activity_columns[0]
    act_col_field_name = raw_col_by_letter[act_col_letter].field_name

    date_col = next(
        (c for c in sheet_contract.raw_columns if c.field_name == "date_raw"),
        None,
    )
    date_col_letter = date_col.column_letter if date_col is not None else None

    row_data: dict[str, Any] = {
        "__row_num__": 2,
        "A": "2",
        id_col_letter: {"t": "s", "v": "0"},
        act_col_letter: {"t": "inlineStr", "raw_inner": raw_inner_xml},
    }
    if date_col_letter is not None:
        row_data[date_col_letter] = {"t": "s", "v": "1"}

    # Build workbook with all 4 sheets present (3 empty sheets, 1 target sheet)
    for s_name in RAW_CONTRACT_REGISTRY.sheets:
        if s_name == sheet_name:
            builder.add_sheet_rows(s_name, [row_data])
        else:
            builder.add_sheet_rows(s_name, [])

    pkg = tmp_path / f"matrix_{sheet_name}_{mode_name}.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)

    # Build expected source values with None for all other fields
    expected_source_values: dict[str, Any] = {
        col.field_name: None for col in sheet_contract.raw_columns
    }
    expected_source_values[act_col_field_name] = expected_val
    if date_col_letter is not None:
        expected_source_values["date_raw"] = "1403/05/15"

    exp_target_row = SourceRowInput(
        stable_id=u_target,
        source_values=expected_source_values,
    )
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(
                sheet_name=s_name,
                rows=[exp_target_row] if s_name == sheet_name else [],
            )
            for s_name in RAW_CONTRACT_REGISTRY.sheets
        ]
    )
    exp_locations = {u_target: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


# ==============================================================================
# RB-02: Unified escape and whitespace semantics between discovery & decode
# ==============================================================================


@pytest.mark.parametrize(
    "escaped_ws",
    ["_x0020_", "_x0009_", "_x000D_", "_x00A0_"],
)
def test_rb_02_escaped_whitespace_in_direct_string_is_inactive(
    tmp_path: Path,
    escaped_ws: str,
) -> None:
    """RB-02: Escaped whitespace in t='str' is inactive and skips corrupt SST 0."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.override_sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'count="1" uniqueCount="1">\n'
        "  <si><t>_xD800_</t></si>\n"
        "</sst>"
    )

    # C2 has t="str" with escaped whitespace, Z2 uses SST entry 0
    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {"t": "str", "v": escaped_ws},
        "Z": {"t": "s", "v": "0"},
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / f"rb_02_ws_{escaped_ws}.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


def test_rb_02_single_pass_escaped_literal_is_active(
    tmp_path: Path,
) -> None:
    """RB-02: Single-pass _x005F_x0020_ decodes to literal _x0020_ and is active."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = [str(u_bf)]

    # C2 has t="str" with literal _x005F_x0020_
    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {"t": "str", "v": "_x005F_x0020_"},
        "G": {"t": "", "v": "1500000.00"},  # Native financial numeric scale test
        "Z": {"t": "s", "v": "0"},
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "rb_02_single_pass_literal.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_bf = SourceRowInput(
        stable_id=u_bf,
        source_values={
            "date_raw": "1403/05/15",
            "party_name_raw": "_x0020_",
            "transaction_type_raw": None,
            "item_name_raw": None,
            "quantity_raw": None,
            "unit_price_toman_raw": Decimal("1500000.00"),
            "discount_toman_raw": None,
            "notes_raw": None,
        },
    )
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[exp_bf]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_bf: 2, u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


def test_rb_02_escaped_nul_control_char_is_active_and_preserved(
    tmp_path: Path,
) -> None:
    """RB-02: _x0000_ (NUL) is active (not whitespace) and preserved."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = [str(u_bf)]

    # C2 has t="str" with _x0000_
    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {"t": "str", "v": "_x0000_"},
        "Z": {"t": "s", "v": "0"},
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "rb_02_nul_char.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_bf = SourceRowInput(
        stable_id=u_bf,
        source_values={
            "date_raw": "1403/05/15",
            "party_name_raw": "\x00",
            "transaction_type_raw": None,
            "item_name_raw": None,
            "quantity_raw": None,
            "unit_price_toman_raw": None,
            "discount_toman_raw": None,
            "notes_raw": None,
        },
    )
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[exp_bf]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_bf: 2, u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


# ==============================================================================
# RB-03: Truly lazy secondary SST index & accurate consumer error coordinates
# ==============================================================================


def test_rb_03_oversized_sst_index_on_inactive_row_is_safely_ignored(
    tmp_path: Path,
) -> None:
    """RB-03: Inactive row with 5000-digit string in Z2 is omitted without error."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = ["valid_string"]

    oversized_idx_str = "1" * 5000
    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "Z": {"t": "s", "v": oversized_idx_str},
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "rb_03_oversized_inactive.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


def test_rb_03_oversized_sst_index_on_active_row_fails_with_cell_error(
    tmp_path: Path,
) -> None:
    """RB-03: Active row with 5000-digit SST index in Z2 raises typed error."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = ["valid_string"]

    oversized_idx_str = "1" * 5000
    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": "بازرگانی فعال",
        "Z": {"t": "s", "v": oversized_idx_str},
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "rb_03_oversized_active.xlsx"
    pkg.write_bytes(builder.build_bytes())

    with pytest.raises(XlsxCellError) as exc:
        read_xlsx_source_snapshot(pkg)
    assert exc.value.reason == REASON_CELL_SST_INDEX_OUT_OF_RANGE
    assert exc.value.sheet_name == "خرید-فروش"
    assert exc.value.cell_ref == "Z2"
    assert exc.value.physical_row_number == 2


def test_rb_03_active_row_corrupt_sst_has_exact_consumer_coordinates(
    tmp_path: Path,
) -> None:
    """RB-03: Corrupt SST error carries exact consumer sheet, cell_ref & row."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.override_sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'count="1" uniqueCount="1">\n'
        "  <si><t>_xD800_</t></si>\n"
        "</sst>"
    )

    # Active row where C2 is candidate activity using corrupt SST entry 0
    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {"t": "s", "v": "0"},
        "Z": str(_make_uuid7(b"0000000000000001")),
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "rb_03_corrupt_activity_coords.xlsx"
    pkg.write_bytes(builder.build_bytes())

    with pytest.raises(XlsxCellError) as exc:
        read_xlsx_source_snapshot(pkg)
    assert exc.value.reason == REASON_CELL_UNPAIRED_SURROGATE
    assert exc.value.sheet_name == "خرید-فروش"
    assert exc.value.cell_ref == "C2"
    assert exc.value.physical_row_number == 2


@pytest.mark.parametrize(
    ("tag_mode", "raw_inner_xml"),
    [
        ("inline_unknown_leaf", "<is><t><unknownleaf>متن</unknownleaf></t></is>"),
        ("direct_unknown_leaf", "<v><unknownleaf>متن</unknownleaf></v>"),
    ],
)
def test_rb_03_malformed_activity_controls_and_error_coordinates(
    tmp_path: Path,
    tag_mode: str,
    raw_inner_xml: str,
) -> None:
    """RB-03: Malformed activity cell controls (unknown leaf tag in inline & direct)."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = [str(_make_uuid7(b"0000000000000001"))]
    t_val = "inlineStr" if "inline" in tag_mode else "str"
    row_unknown_leaf = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {
            "t": t_val,
            "raw_inner": raw_inner_xml,
        },
        "Z": {"t": "s", "v": "0"},
    }
    builder.add_sheet_rows("خرید-فروش", [row_unknown_leaf])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / f"unknown_leaf_{tag_mode}.xlsx"
    pkg.write_bytes(builder.build_bytes())

    with pytest.raises(XlsxCellError) as exc:
        read_xlsx_source_snapshot(pkg)
    assert exc.value.reason == REASON_CELL_UNKNOWN_TYPE
    assert exc.value.sheet_name == "خرید-فروش"
    assert exc.value.cell_ref == "C2"
    assert exc.value.physical_row_number == 2


@pytest.mark.parametrize(
    "invalid_idx_str",
    ["abc", "-1", "01", "1.5"],
)
def test_rb_03_invalid_sst_index_grammar_fails_with_coordinates(
    tmp_path: Path,
    invalid_idx_str: str,
) -> None:
    """RB-03: Invalid SST index grammar raises error with consumer coordinates."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = ["valid_string"]

    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {"t": "s", "v": invalid_idx_str},
        "Z": str(_make_uuid7(b"0000000000000001")),
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / f"invalid_grammar_{invalid_idx_str}.xlsx"
    pkg.write_bytes(builder.build_bytes())

    with pytest.raises(XlsxCellError) as exc:
        read_xlsx_source_snapshot(pkg)
    assert exc.value.reason == REASON_CELL_INVALID_SST_INDEX
    assert exc.value.sheet_name == "خرید-فروش"
    assert exc.value.cell_ref == "C2"
    assert exc.value.physical_row_number == 2


def test_rb_03_missing_sst_v_element_on_active_row_fails_and_inactive_row_ignored(
    tmp_path: Path,
) -> None:
    """RB-03: Missing <v> in active SST fails; inactive row missing <v> is ignored."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    # 1. Active row where C2 has t="s" but no <v> element -> XlsxCellError
    builder1 = SyntheticXlsxBuilder()
    builder1.shared_strings = ["valid_string"]
    row_active_missing_v = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {"t": "s", "raw_inner": ""},  # <c r="C2" t="s"/> without <v>
        "Z": str(u_bf),
    }
    builder1.add_sheet_rows("خرید-فروش", [row_active_missing_v])
    builder1.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder1.add_sheet_rows(
        "ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)]
    )
    builder1.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p1 = tmp_path / "missing_v_active.xlsx"
    p1.write_bytes(builder1.build_bytes())

    with pytest.raises(XlsxCellError) as exc1:
        read_xlsx_source_snapshot(p1)
    assert exc1.value.reason == REASON_CELL_INVALID_SST_INDEX
    assert exc1.value.sheet_name == "خرید-فروش"
    assert exc1.value.cell_ref == "C2"
    assert exc1.value.physical_row_number == 2

    # 2. Inactive tail row where Z3 has t="s" but no <v> element -> ignored cleanly
    builder2 = SyntheticXlsxBuilder()
    builder2.shared_strings = ["valid_string"]
    row_inactive_missing_v = {
        "__row_num__": 3,
        "A": "3",
        "B": "",
        "C": "",
        "Z": {"t": "s", "raw_inner": ""},  # Inactive row missing <v> on Z -> ignored
    }
    builder2.add_sheet_rows(
        "خرید-فروش",
        [_sample_buy_sell_row_data(u_bf, 2), row_inactive_missing_v],
    )
    builder2.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder2.add_sheet_rows(
        "ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)]
    )
    builder2.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    p2 = tmp_path / "missing_v_inactive.xlsx"
    p2.write_bytes(builder2.build_bytes())

    res2 = read_xlsx_source_snapshot(p2)
    assert res2.snapshot.sheets["خرید-فروش"].row_count == 1
    assert res2.snapshot.sheets["خرید-فروش"].rows[0].canonical_uuid == str(u_bf).lower()


# ==============================================================================
# RB-04: Equivalence, row/cell permutations, stream pass bounds & failure cleanup
# ==============================================================================


def test_rb_04_equivalent_representations_and_row_cell_permutation(
    tmp_path: Path,
) -> None:
    """RB-04: Test invariance under row/cell permutations and SST index remapping."""
    u_bf_1 = _make_uuid7(b"0000000000000001")
    u_bf_2 = _make_uuid7(b"0000000000000002")
    u_dp = _make_uuid7(b"0000000000000003")
    u_vk = _make_uuid7(b"0000000000000004")
    u_lk = _make_uuid7(b"0000000000000005")

    # Workbook A: canonical ascending row order, inline strings and normal SST
    builder_a = SyntheticXlsxBuilder()
    builder_a.shared_strings = [
        "1403/05/15",
        "بازرگانی احمدی",
        "خرید",
        "طلای آبشده",
        "12.34",
        "1500000",
        "0",
        "فاکتور ۱",
        str(u_bf_1),
        "1403/05/16",
        "فروشگاه زرین",
        "فروش",
        "سکه بهار آزادی",
        "5",
        "45000000",
        "0",
        "فاکتور ۲",
        str(u_bf_2),
    ]
    row10_a = {
        "__row_num__": 10,
        "A": "10",
        "B": {"t": "s", "v": "0"},
        "C": {"t": "s", "v": "1"},
        "D": {"t": "s", "v": "2"},
        "E": {"t": "s", "v": "3"},
        "F": {"t": "s", "v": "4"},
        "G": {"t": "s", "v": "5"},
        "H": {"t": "s", "v": "6"},
        "J": {"t": "s", "v": "7"},
        "Z": {"t": "s", "v": "8"},
    }
    row20_a = {
        "__row_num__": 20,
        "A": "20",
        "B": {"t": "s", "v": "9"},
        "C": {"t": "s", "v": "10"},
        "D": {"t": "s", "v": "11"},
        "E": {"t": "s", "v": "12"},
        "F": {"t": "s", "v": "13"},
        "G": {"t": "s", "v": "14"},
        "H": {"t": "s", "v": "15"},
        "J": {"t": "s", "v": "16"},
        "Z": {"t": "s", "v": "17"},
    }
    builder_a.add_sheet_rows("خرید-فروش", [row10_a, row20_a])
    builder_a.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder_a.add_sheet_rows(
        "ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)]
    )
    builder_a.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    pkg_a = tmp_path / "perm_a.xlsx"
    pkg_a.write_bytes(builder_a.build_bytes())

    # Workbook B: Inverted row order in XML (row 20 before row 10),
    # inverted cell column order (Z before A), and completely remapped SST indices
    builder_b = SyntheticXlsxBuilder()
    # SST in reversed order with dummy strings interleaved
    sst_b = [
        "DUMMY_UNUSED_0",
        str(u_bf_2),  # 1 (was 17 in A)
        "فاکتور ۲",  # 2 (was 16 in A)
        "0",  # 3 (was 6 and 15 in A)
        "45000000",  # 4 (was 14 in A)
        "5",  # 5 (was 13 in A)
        "سکه بهار آزادی",  # 6 (was 12 in A)
        "فروش",  # 7 (was 11 in A)
        "فروشگاه زرین",  # 8 (was 10 in A)
        "1403/05/16",  # 9 (was 9 in A)
        str(u_bf_1),  # 10 (was 8 in A)
        "فاکتور ۱",  # 11 (was 7 in A)
        "1500000",  # 12 (was 5 in A)
        "12.34",  # 13 (was 4 in A)
        "طلای آبشده",  # 14 (was 3 in A)
        "خرید",  # 15 (was 2 in A)
        "بازرگانی احمدی",  # 16 (was 1 in A)
        "1403/05/15",  # 17 (was 0 in A)
        "DUMMY_UNUSED_1",
    ]
    builder_b.shared_strings = sst_b

    # Cells in reverse column order: Z, J, H, G, F, E, D, C, B, A
    row20_b = {
        "__row_num__": 20,
        "Z": {"t": "s", "v": "1"},
        "J": {"t": "s", "v": "2"},
        "H": {"t": "s", "v": "3"},
        "G": {"t": "s", "v": "4"},
        "F": {"t": "s", "v": "5"},
        "E": {"t": "s", "v": "6"},
        "D": {"t": "s", "v": "7"},
        "C": {"t": "s", "v": "8"},
        "B": {"t": "s", "v": "9"},
        "A": "20",
    }
    row10_b = {
        "__row_num__": 10,
        "Z": {"t": "s", "v": "10"},
        "J": {"t": "s", "v": "11"},
        "H": {"t": "s", "v": "3"},
        "G": {"t": "s", "v": "12"},
        "F": {"t": "s", "v": "13"},
        "E": {"t": "s", "v": "14"},
        "D": {"t": "s", "v": "15"},
        "C": {"t": "s", "v": "16"},
        "B": {"t": "s", "v": "17"},
        "A": "10",
    }
    # Add row 20 BEFORE row 10 in sheetData
    builder_b.add_sheet_rows("خرید-فروش", [row20_b, row10_b])
    builder_b.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder_b.add_sheet_rows(
        "ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)]
    )
    builder_b.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg_b = tmp_path / "perm_b.xlsx"
    pkg_b_bytes = builder_b.build_bytes()
    pkg_b.write_bytes(pkg_b_bytes)

    # Assert XML of pkg_b has inverted row order, col order and exact SST remapping
    with zipfile.ZipFile(pkg_b, "r") as zf_check:
        sheet1_xml = zf_check.read("xl/worksheets/sheet1.xml").decode("utf-8")
        pos_r20 = sheet1_xml.find('<row r="20"')
        pos_r10 = sheet1_xml.find('<row r="10"')
        assert pos_r20 != -1 and pos_r10 != -1
        assert pos_r20 < pos_r10, (
            "Physical row 20 must appear before row 10 in XML for permutation test"
        )
        pos_z20 = sheet1_xml.find('r="Z20"')
        pos_a20 = sheet1_xml.find('r="A20"')
        assert pos_z20 != -1 and pos_a20 != -1
        assert pos_z20 < pos_a20, (
            "Column Z20 must appear before A20 in XML for permutation test"
        )

        sst_tree = etree.fromstring(
            zf_check.read("xl/sharedStrings.xml"),
            parser=xlsx_source_reader._get_secure_xml_parser(),
        )
        si_elements = [
            elem for elem in sst_tree if elem.tag in xlsx_source_reader._TAG_SI
        ]
        assert len(si_elements) == len(sst_b)
        for idx, si_el in enumerate(si_elements):
            text_extracted = xlsx_source_reader._extract_text_from_si_or_is(
                si_el,
                sheet_name="[sharedStrings.xml]",
            )
            assert text_extracted == sst_b[idx]

    # Verify read-only archive integrity before and after read
    stat_before = pkg_a.stat()
    bytes_before = pkg_a.read_bytes()

    res_a = read_xlsx_source_snapshot(pkg_a)
    res_b = read_xlsx_source_snapshot(pkg_b)

    stat_after = pkg_a.stat()
    bytes_after = pkg_a.read_bytes()
    assert bytes_before == bytes_after
    assert stat_before.st_size == stat_after.st_size
    assert stat_before.st_mtime == stat_after.st_mtime

    # Verify file can be closed, renamed and deleted
    renamed_pkg = tmp_path / "perm_a_renamed.xlsx"
    pkg_a.rename(renamed_pkg)
    assert renamed_pkg.is_file()
    renamed_pkg.unlink()

    # Build independent Expected snapshot
    exp_bf1 = SourceRowInput(
        stable_id=u_bf_1,
        source_values={
            "date_raw": "1403/05/15",
            "party_name_raw": "بازرگانی احمدی",
            "transaction_type_raw": "خرید",
            "item_name_raw": "طلای آبشده",
            "quantity_raw": "12.34",
            "unit_price_toman_raw": "1500000",
            "discount_toman_raw": "0",
            "notes_raw": "فاکتور ۱",
        },
    )
    exp_bf2 = SourceRowInput(
        stable_id=u_bf_2,
        source_values={
            "date_raw": "1403/05/16",
            "party_name_raw": "فروشگاه زرین",
            "transaction_type_raw": "فروش",
            "item_name_raw": "سکه بهار آزادی",
            "quantity_raw": "5",
            "unit_price_toman_raw": "45000000",
            "discount_toman_raw": "0",
            "notes_raw": "فاکتور ۲",
        },
    )
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[exp_bf1, exp_bf2]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )

    exp_loc_a = {u_bf_1: 10, u_bf_2: 20, u_dp: 2, u_vk: 2, u_lk: 2}
    exp_loc_b = {u_bf_1: 10, u_bf_2: 20, u_dp: 2, u_vk: 2, u_lk: 2}

    # In expected snapshot, rows are sorted deterministically by stable_id
    assert res_a.snapshot == res_b.snapshot
    _assert_snapshot_and_locations(res_a, exp_snapshot, exp_loc_a)
    _assert_snapshot_and_locations(res_b, exp_snapshot, exp_loc_b)


def test_rb_04_read_only_integrity_and_cleanup_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RB-04: Read-only integrity and handle release on required SST error."""
    builder = SyntheticXlsxBuilder()
    builder.override_sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'count="1" uniqueCount="1">\n'
        "  <si><t>_xD800_</t></si>\n"
        "</sst>"
    )

    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": "بازرگانی فعال",
        "J": {"t": "s", "v": "0"},  # Active row referencing corrupt SST 0
        "Z": str(_make_uuid7(b"0000000000000001")),
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows("دریافت-پرداخت", [])
    builder.add_sheet_rows("ورود-خروج", [])
    builder.add_sheet_rows("لیست کسبه", [])

    pkg = tmp_path / "failure_integrity.xlsx"
    pkg.write_bytes(builder.build_bytes())

    stat_before = pkg.stat()
    bytes_before = pkg.read_bytes()

    zip_closed = False
    orig_zip_close = zipfile.ZipFile.close

    def tracking_zip_close(zf_self: Any) -> Any:
        nonlocal zip_closed
        zip_closed = True
        return orig_zip_close(zf_self)

    monkeypatch.setattr(zipfile.ZipFile, "close", tracking_zip_close)

    with pytest.raises(XlsxCellError) as exc:
        read_xlsx_source_snapshot(pkg)
    assert exc.value.reason == REASON_CELL_UNPAIRED_SURROGATE

    # Verify ZipFile handle was cleanly closed
    assert zip_closed, "ZipFile.close was not called during exception unwinding"

    # Verify bytes, size, and mtime remain completely unmodified
    stat_after = pkg.stat()
    bytes_after = pkg.read_bytes()
    assert bytes_before == bytes_after
    assert stat_before.st_size == stat_after.st_size
    assert stat_before.st_mtime == stat_after.st_mtime

    # Verify file can be cleanly renamed and deleted after failure
    renamed_pkg = tmp_path / "failure_integrity_renamed.xlsx"
    pkg.rename(renamed_pkg)
    assert renamed_pkg.is_file()
    renamed_pkg.unlink()


def test_rb_04_raw_text_numeric_appearance_leading_zeros_and_spaces(
    tmp_path: Path,
) -> None:
    """RB-04: Raw text with leading zeros and whitespace preserves exact str."""
    u_lk = _make_uuid7(b"0000000000000001")
    builder = SyntheticXlsxBuilder()
    synthetic_numeric_text = " 000123 "

    row_data = {
        "__row_num__": 2,
        "A": "2",
        "B": "کاسب نمونه",
        "C": {
            "t": "inlineStr",
            "raw_inner": f"<is><t>{synthetic_numeric_text}</t></is>",
        },
        "D": str(u_lk),
    }
    builder.add_sheet_rows("لیست کسبه", [row_data])
    builder.add_sheet_rows("خرید-فروش", [])
    builder.add_sheet_rows("دریافت-پرداخت", [])
    builder.add_sheet_rows("ورود-خروج", [])

    pkg = tmp_path / "leading_zeros_spaces.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    lk_row = res.snapshot.sheets["لیست کسبه"].rows[0]

    # Assert exact type, value, codepoints and hash
    actual_val = lk_row.raw_values["phone_number_raw"]
    assert isinstance(actual_val, str)
    assert type(actual_val) is str
    assert actual_val == " 000123 "
    assert actual_val.startswith(" ") and actual_val.endswith(" ")
    assert actual_val[1:4] == "000"

    # Verify independent Oracle snapshot and WP-03 hash matching
    exp_lk = SourceRowInput(
        stable_id=u_lk,
        source_values={
            "party_name_raw": "کاسب نمونه",
            "phone_number_raw": " 000123 ",
        },
    )
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    _assert_snapshot_and_locations(res, exp_snapshot, {u_lk: 2})


@pytest.mark.parametrize("active_n", [1, 200])
def test_rb_04_stream_passes_and_selective_sst_decoding_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    active_n: int,
) -> None:
    """RB-04: Observer verifies pass bounds (max 2 per part) for N=1 and N=200."""
    builder = SyntheticXlsxBuilder()

    # 4 valid sheets with full headers; other 3 sheets have only headers (0 data rows)
    # Sheet 1 has:
    # - Row 2: Inactive row (B2=SST 1 valid date, Z2=SST 0 corrupt surrogate)
    # - Rows 3 to N+2: Active rows (B=SST 1, sole activity C=SST 2, Z=SST 3..N+2)

    active_uuids = [
        _make_uuid7(f"00000000{i:08x}".encode("ascii")) for i in range(1, active_n + 1)
    ]

    # Shared strings:
    # 0: _xD800_ (corrupt surrogate, inactive consumer in Z2)
    # 1: 1403/05/15 (valid date)
    # 2: SYNTHETIC-ACTIVITY (sole activity string for all active rows)
    # 3 to N+2: UUID strings for active rows
    # N+3, N+4, N+5: unused corrupt surrogate tail entries
    sst_list = (
        [
            "_xD800_",
            "1403/05/15",
            "SYNTHETIC-ACTIVITY",
        ]
        + [str(u) for u in active_uuids]
        + [
            "_xD800_",
            "_xD800_",
            "_xD800_",
        ]
    )

    builder.shared_strings = sst_list

    # Sheet 1 data rows
    rows_bf: list[dict[str, Any]] = [
        # Inactive row 2
        {
            "__row_num__": 2,
            "A": "2",
            "B": {"t": "s", "v": "1"},
            "Z": {"t": "s", "v": "0"},
        },
    ]

    # Active rows 3 to active_n + 2
    # SOLE activity is SST index 2 in C (party_name_raw)
    # date from SST 1, ID from SST idx
    for i, _u in enumerate(active_uuids, start=1):
        row_num = i + 2
        sst_idx_id = i + 2  # index 3 for i=1
        rows_bf.append(
            {
                "__row_num__": row_num,
                "A": str(row_num),
                "B": {"t": "s", "v": "1"},
                "C": {"t": "s", "v": "2"},
                "Z": {"t": "s", "v": str(sst_idx_id)},
            }
        )

    builder.add_sheet_rows("خرید-فروش", rows_bf)
    builder.add_sheet_rows("دریافت-پرداخت", [])
    builder.add_sheet_rows("ورود-خروج", [])
    builder.add_sheet_rows("لیست کسبه", [])

    pkg = tmp_path / f"rb_04_stream_bounds_{active_n}.xlsx"
    pkg.write_bytes(builder.build_bytes())

    # Set up non-invasive observers for ZIP part openings and SST decode requests
    opened_parts: list[str] = []
    orig_zip_open = zipfile.ZipFile.open

    def counting_zip_open(zf_self: Any, name: Any, *args: Any, **kwargs: Any) -> Any:
        fn = name.filename if hasattr(name, "filename") else str(name)
        opened_parts.append(fn)
        return orig_zip_open(zf_self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", counting_zip_open)

    sst_calls: list[set[int]] = []
    orig_parse_sst = xlsx_source_reader._parse_shared_strings_table

    def observing_parse_sst(zf_arg: Any, sst_target: Any, needed_consumers: Any) -> Any:
        sst_calls.append(set(needed_consumers.keys()))
        return orig_parse_sst(zf_arg, sst_target, needed_consumers)

    monkeypatch.setattr(
        xlsx_source_reader, "_parse_shared_strings_table", observing_parse_sst
    )

    res = read_xlsx_source_snapshot(pkg)

    # 1. Assert SST decode call set contents
    assert len(sst_calls) == 2, f"Expected 2 SST parse passes, got {len(sst_calls)}"
    expected_phase1_sst = {2}  # only activity SST candidate
    expected_phase2_sst = {1} | set(range(3, active_n + 3))  # date + IDs of active rows

    assert sst_calls[0] == expected_phase1_sst, (
        f"Phase 1 SST set mismatch: {sst_calls[0]} vs {expected_phase1_sst}"
    )
    assert sst_calls[1] == expected_phase2_sst, (
        f"Phase 2 SST set mismatch: {sst_calls[1]} vs {expected_phase2_sst}"
    )

    # Corrupt entry 0 and tail entries never requested
    assert 0 not in sst_calls[0] and 0 not in sst_calls[1]
    for corrupt_tail in range(active_n + 3, len(sst_list)):
        assert corrupt_tail not in sst_calls[0]
        assert corrupt_tail not in sst_calls[1]

    # 2. Assert bounded ZIP openings
    # Worksheet 1 (خرید-فروش) opened at most 2 times (Pass 1 discovery + Pass 2 stream)
    ws1_opens = opened_parts.count("xl/worksheets/sheet1.xml")
    assert ws1_opens <= 2, f"sheet1.xml opened {ws1_opens} times (max 2)"

    # Empty sheets opened at most 2 times
    for ws_name in [
        "xl/worksheets/sheet2.xml",
        "xl/worksheets/sheet3.xml",
        "xl/worksheets/sheet4.xml",
    ]:
        ws_opens = opened_parts.count(ws_name)
        assert ws_opens <= 2, f"{ws_name} opened {ws_opens} times (max 2)"

    # SST table opened at most 2 times
    sst_opens = opened_parts.count("xl/sharedStrings.xml")
    assert sst_opens <= 2, f"sharedStrings.xml opened {sst_opens} times (max 2)"

    # 3. Assert snapshot equality against independent expected Oracle
    exp_rows = [
        SourceRowInput(
            stable_id=u,
            source_values={
                "date_raw": "1403/05/15",
                "party_name_raw": "SYNTHETIC-ACTIVITY",
                "transaction_type_raw": None,
                "item_name_raw": None,
                "quantity_raw": None,
                "unit_price_toman_raw": None,
                "discount_toman_raw": None,
                "notes_raw": None,
            },
        )
        for u in active_uuids
    ]
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=exp_rows),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
        ]
    )
    exp_locations = {u: i + 2 for i, u in enumerate(active_uuids, start=1)}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


# ==============================================================================
# Preserved Initial 10 Cases & Paired Controls with Complete Expected Oracle
# ==============================================================================


def test_r5_02_case_01_inactive_inlinestr_empty_with_corrupt_id_sst(
    tmp_path: Path,
) -> None:
    """Case 1: Inactive row (C2 inlineStr empty) with corrupt SST entry 0 in Z2."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.override_sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'count="1" uniqueCount="1">\n'
        "  <si><t>_xD800_</t></si>\n"
        "</sst>"
    )

    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {"t": "inlineStr", "raw_inner": "<is><t/></is>"},
        "Z": {"t": "s", "v": "0"},
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "r5_02_case_01.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


def test_r5_02_case_02_inactive_inlinestr_whitespace_with_corrupt_id_sst(
    tmp_path: Path,
) -> None:
    """Case 2: Inactive row (C2 inlineStr whitespace) with corrupt SST entry 0 in Z2."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.override_sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'count="1" uniqueCount="1">\n'
        "  <si><t>_xD800_</t></si>\n"
        "</sst>"
    )

    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {"t": "inlineStr", "is": "   "},
        "Z": {"t": "s", "v": "0"},
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "r5_02_case_02.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


def test_r5_02_case_03_inactive_str_whitespace_with_corrupt_id_sst(
    tmp_path: Path,
) -> None:
    """Case 3: Inactive row (C2 t='str' whitespace) with corrupt SST entry 0 in Z2."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.override_sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'count="1" uniqueCount="1">\n'
        "  <si><t>_xD800_</t></si>\n"
        "</sst>"
    )

    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {"t": "str", "v": "   "},
        "Z": {"t": "s", "v": "0"},
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "r5_02_case_03.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


def test_r5_02_case_04_inactive_sst_empty_with_corrupt_id_sst(
    tmp_path: Path,
) -> None:
    """Case 4: Inactive row (C2 t='s' empty SST) with corrupt SST entry 0 in Z2."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.override_sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'count="2" uniqueCount="2">\n'
        "  <si><t>_xD800_</t></si>\n"
        "  <si><t/></si>\n"
        "</sst>"
    )

    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {"t": "s", "v": "1"},
        "Z": {"t": "s", "v": "0"},
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "r5_02_case_04.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


def test_r5_02_case_05_inactive_sst_whitespace_with_corrupt_id_sst(
    tmp_path: Path,
) -> None:
    """Case 5: Inactive row (C2 t='s' whitespace SST) with corrupt SST entry 0 in Z2."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.override_sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'count="2" uniqueCount="2">\n'
        "  <si><t>_xD800_</t></si>\n"
        "  <si><t>   </t></si>\n"
        "</sst>"
    )

    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {"t": "s", "v": "1"},
        "Z": {"t": "s", "v": "0"},
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "r5_02_case_05.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


def test_r5_02_case_06_inactive_covered_literal_with_corrupt_id_sst(
    tmp_path: Path,
) -> None:
    """Case 6: Inactive row (C2 covered by K2 array formula) with corrupt SST in Z2."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.override_sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'count="1" uniqueCount="1">\n'
        "  <si><t>_xD800_</t></si>\n"
        "</sst>"
    )

    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": "SYNTHETIC-CACHED-ACTIVITY",
        "K": {"f": "SYNTHETIC()", "f_t": "array", "f_ref": "C2:K2", "v": "0"},
        "Z": {"t": "s", "v": "0"},
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "r5_02_case_06.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


def test_r5_02_case_07_inactive_unsupported_leaf_in_date(
    tmp_path: Path,
) -> None:
    """Case 7: Inactive row with unsupported leaf tag in date_raw B2."""
    u_valid = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = ["1403/05/15"]

    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": {
            "t": "s",
            "raw_inner": "<v><unsupported>0</unsupported></v>",
        },
        "Z": str(u_valid),
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "r5_02_case_07.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


def test_r5_02_case_08_inactive_unsupported_leaf_in_id(
    tmp_path: Path,
) -> None:
    """Case 8: Inactive row with unsupported leaf tag in record_id Z2."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = [str(_make_uuid7(b"0000000000000001"))]

    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "Z": {
            "t": "s",
            "raw_inner": "<v><unsupported>0</unsupported></v>",
        },
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "r5_02_case_08.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


def test_r5_02_case_09_covered_formula_cache_on_active_row(
    tmp_path: Path,
) -> None:
    """Case 9: Active row with covered formula cache having unsupported tag."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = ["0"]

    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": "بازرگانی فعال",
        "D": "خرید",
        "E": "طلای ۱۸ عیار",
        "F": "10.5",
        "G": {"t": "", "v": "1500000.00"},  # Native financial numeric scale test
        "H": {
            "t": "s",
            "raw_inner": "<v><unsupported>0</unsupported></v>",
        },
        "J": "توضیحات",
        "K": {"f": "SYNTHETIC()", "f_t": "array", "f_ref": "H2:K2", "v": "0"},
        "Z": str(u_bf),
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "r5_02_case_09.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_bf = SourceRowInput(
        stable_id=u_bf,
        source_values={
            "date_raw": "1403/05/15",
            "party_name_raw": "بازرگانی فعال",
            "transaction_type_raw": "خرید",
            "item_name_raw": "طلای ۱۸ عیار",
            "quantity_raw": "10.5",
            "unit_price_toman_raw": Decimal("1500000.00"),
            "discount_toman_raw": None,
            "notes_raw": None,
        },
    )
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[exp_bf]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_bf: 2, u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


def test_r5_02_case_10_activity_with_xml_comment_and_sst_id(
    tmp_path: Path,
) -> None:
    """Case 10: Sole activity C2 has XML comment in <v>, Z2 uses SST for UUID."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = [str(u_bf)]

    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {
            "t": "str",
            "raw_inner": "<v><!--transport-comment-->SYNTHETIC-ACTIVITY-MARKER</v>",
        },
        "Z": {"t": "s", "v": "0"},
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "r5_02_case_10.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_bf = SourceRowInput(
        stable_id=u_bf,
        source_values={
            "date_raw": "1403/05/15",
            "party_name_raw": "SYNTHETIC-ACTIVITY-MARKER",
            "transaction_type_raw": None,
            "item_name_raw": None,
            "quantity_raw": None,
            "unit_price_toman_raw": None,
            "discount_toman_raw": None,
            "notes_raw": None,
        },
    )
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[exp_bf]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_bf: 2, u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


def test_r5_02_paired_control_corrupt_sst_in_active_row_fails(
    tmp_path: Path,
) -> None:
    """Paired control: Corrupt SST entry 0 in an ACTIVE row MUST fail."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.override_sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'count="1" uniqueCount="1">\n'
        "  <si><t>_xD800_</t></si>\n"
        "</sst>"
    )

    # Active row referencing corrupt index 0 in notes_raw J2
    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": "بازرگانی فعال",
        "D": "خرید",
        "E": "طلای ۱۸ عیار",
        "F": "10",
        "G": "1000",
        "J": {"t": "s", "v": "0"},  # Active row needs entry 0
        "Z": str(_make_uuid7(b"0000000000000001")),
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "paired_corrupt_active.xlsx"
    pkg.write_bytes(builder.build_bytes())

    with pytest.raises(XlsxCellError) as exc:
        read_xlsx_source_snapshot(pkg)
    assert exc.value.reason == REASON_CELL_UNPAIRED_SURROGATE
    assert exc.value.sheet_name == "خرید-فروش"
    assert exc.value.cell_ref == "J2"
    assert exc.value.physical_row_number == 2


def test_r5_02_shared_sst_entry_between_inactive_and_active_consumers(
    tmp_path: Path,
) -> None:
    """Shared SST entry between inactive row and active row resolves correctly."""
    u_bf_active = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = ["بازرگانی مشترک"]

    # Row 2 is inactive (date-only) using SST 0 in record_id Z2
    # Row 3 is active using SST 0 in party C3
    rows_bf = [
        {
            "__row_num__": 2,
            "A": "2",
            "B": "1403/05/15",
            "Z": {"t": "s", "v": "0"},
        },
        {
            "__row_num__": 3,
            "A": "3",
            "B": "1403/05/16",
            "C": {"t": "s", "v": "0"},
            "D": "خرید",
            "E": "طلای ۱۸ عیار",
            "F": "10",
            "G": "1000",
            "Z": str(u_bf_active),
        },
    ]
    builder.add_sheet_rows("خرید-فروش", rows_bf)
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "shared_sst_entry.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_bf = SourceRowInput(
        stable_id=u_bf_active,
        source_values={
            "date_raw": "1403/05/16",
            "party_name_raw": "بازرگانی مشترک",
            "transaction_type_raw": "خرید",
            "item_name_raw": "طلای ۱۸ عیار",
            "quantity_raw": "10",
            "unit_price_toman_raw": "1000",
            "discount_toman_raw": None,
            "notes_raw": None,
        },
    )
    exp_dp, exp_vk, exp_lk = _make_sample_expected_rows_3_sheets(u_dp, u_vk, u_lk)
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[exp_bf]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_bf_active: 3, u_dp: 2, u_vk: 2, u_lk: 2}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)


def test_r5_02_invalid_sst_index_in_activity_column_raises_typed_error(
    tmp_path: Path,
) -> None:
    """Invalid SST index (out of range) in activity column raises typed error."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = ["تنها_رشته"]

    # C2 has SST index 9999 (out of range)
    row_target = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {"t": "s", "v": "9999"},
        "Z": str(_make_uuid7(b"0000000000000001")),
    }
    builder.add_sheet_rows("خرید-فروش", [row_target])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "sst_out_of_range.xlsx"
    pkg.write_bytes(builder.build_bytes())

    with pytest.raises(XlsxCellError) as exc:
        read_xlsx_source_snapshot(pkg)
    assert exc.value.reason == REASON_CELL_SST_INDEX_OUT_OF_RANGE
    assert exc.value.sheet_name == "خرید-فروش"
    assert exc.value.cell_ref == "C2"
    assert exc.value.physical_row_number == 2


def test_r5_02_activity_and_sst_across_all_four_sheets(
    tmp_path: Path,
) -> None:
    """Verify activity detection and selective SST exclusion across all 4 sheets."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.override_sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'count="2" uniqueCount="2">\n'
        "  <si><t>_xD800_</t></si>\n"
        "  <si><t>بازرگانی معتبر</t></si>\n"
        "</sst>"
    )

    # In each sheet: row 2 is inactive (date/ID only or ID only with Z2/P2/D2 = SST 0)
    # Row 3 is active (party C3/B3 = SST 1)
    builder.add_sheet_rows(
        "خرید-فروش",
        [
            {
                "__row_num__": 2,
                "A": "2",
                "B": "1403/05/15",
                "Z": {"t": "s", "v": "0"},
            },
            {
                "__row_num__": 3,
                "A": "3",
                "B": "1403/05/16",
                "C": {"t": "s", "v": "1"},
                "D": "خرید",
                "E": "طلای ۱۸ عیار",
                "F": "10",
                "G": "1000",
                "Z": str(u_bf),
            },
        ],
    )
    builder.add_sheet_rows(
        "دریافت-پرداخت",
        [
            {
                "__row_num__": 2,
                "A": "2",
                "B": "1403/05/15",
                "P": {"t": "s", "v": "0"},
            },
            {
                "__row_num__": 3,
                "A": "3",
                "B": "1403/05/16",
                "C": {"t": "s", "v": "1"},
                "D": "دریافت چک",
                "E": "5000000",
                "P": str(u_dp),
            },
        ],
    )
    builder.add_sheet_rows(
        "ورود-خروج",
        [
            {
                "__row_num__": 2,
                "A": "2",
                "B": "1403/05/15",
                "P": {"t": "s", "v": "0"},
            },
            {
                "__row_num__": 3,
                "A": "3",
                "B": "1403/05/16",
                "C": {"t": "s", "v": "1"},
                "D": "ورود",
                "E": "طلای ۱۸ عیار",
                "F": "10",
                "G": "750",
                "P": str(u_vk),
            },
        ],
    )
    builder.add_sheet_rows(
        "لیست کسبه",
        [
            {
                "__row_num__": 2,
                "A": "2",
                "D": {"t": "s", "v": "0"},
            },
            {
                "__row_num__": 3,
                "A": "3",
                "B": {"t": "s", "v": "1"},
                "C": "SYNTHETIC-PHONE-001",
                "D": str(u_lk),
            },
        ],
    )

    pkg = tmp_path / "all_four_sheets_selective_sst.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    exp_bf = SourceRowInput(
        stable_id=u_bf,
        source_values={
            "date_raw": "1403/05/16",
            "party_name_raw": "بازرگانی معتبر",
            "transaction_type_raw": "خرید",
            "item_name_raw": "طلای ۱۸ عیار",
            "quantity_raw": "10",
            "unit_price_toman_raw": "1000",
            "discount_toman_raw": None,
            "notes_raw": None,
        },
    )
    exp_dp = SourceRowInput(
        stable_id=u_dp,
        source_values={
            "date_raw": "1403/05/16",
            "party_name_raw": "بازرگانی معتبر",
            "entry_type_raw": "دریافت چک",
            "amount_toman_raw": "5000000",
            "notes_raw": None,
            "account_code_raw": None,
            "customer_flag_raw": None,
        },
    )
    exp_vk = SourceRowInput(
        stable_id=u_vk,
        source_values={
            "date_raw": "1403/05/16",
            "party_name_raw": "بازرگانی معتبر",
            "movement_type_raw": "ورود",
            "item_name_raw": "طلای ۱۸ عیار",
            "quantity_raw": "10",
            "purity_raw": "750",
            "notes_raw": None,
            "customer_flag_raw": None,
        },
    )
    exp_lk = SourceRowInput(
        stable_id=u_lk,
        source_values={
            "party_name_raw": "بازرگانی معتبر",
            "phone_number_raw": "SYNTHETIC-PHONE-001",
        },
    )
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[exp_bf]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )
    exp_locations = {u_bf: 3, u_dp: 3, u_vk: 3, u_lk: 3}
    _assert_snapshot_and_locations(res, exp_snapshot, exp_locations)
