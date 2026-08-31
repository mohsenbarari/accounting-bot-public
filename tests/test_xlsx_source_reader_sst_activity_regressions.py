"""Standalone regression tests for WP-05 R5-B (SST selection, activity, RB-01..04).

Verifies:
1. RB-01: Correct <is> container extraction in Pass 1 activity discovery across
   plain text, rich runs (<r><t>), XML comments/tails, and ASCII/Persian zeros.
2. RB-02: Unified escape/whitespace semantics between discovery and decode:
   escaped whitespace (_x0020_, _x0009_, _x000D_, _x00A0_) is inactive,
   double-escaped _x005F_x0020_ is active, and _x0000_ (NUL) is active.
3. RB-03: Lazy secondary SST index evaluation (5000-digit integer avoidance)
   and accurate consumer coordinate metadata on needed SST failures.
4. RB-04: Full snapshot equality, WP-03 hashes, Decimal scale preservation with
   as_tuple(), equivalent representations, physical row/cell permutations,
   and observer-based stream pass bounds for N=1 and N=200.
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
    REASON_CELL_SST_INDEX_OUT_OF_RANGE,
    REASON_CELL_UNKNOWN_TYPE,
    REASON_CELL_UNPAIRED_SURROGATE,
    XlsxCellError,
    read_xlsx_source_snapshot,
)
from test_xlsx_source_reader import (
    SyntheticXlsxBuilder,
    _make_uuid7,
    _sample_business_parties_row_data,
    _sample_inventory_movements_row_data,
    _sample_receipts_payments_row_data,
)


def _assert_row_equality(actual_row: Any, expected_row: Any, sheet_name: str) -> None:
    """Assert strict type and value equality including Decimal.as_tuple()."""
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


# ==============================================================================
# RB-01: Correct container in inlineStr activity extraction
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
    _assert_row_equality(res.snapshot.sheets["خرید-فروش"].rows[0], exp_r1, "خرید-فروش")
    _assert_row_equality(res.snapshot.sheets["خرید-فروش"].rows[1], exp_r2, "خرید-فروش")


def test_rb_01_sst_date_and_literal_id_with_inline_activity_strict_and_transitional(
    tmp_path: Path,
) -> None:
    """RB-01: 3 sheets with date_raw: literal ID, SST date, inline activity."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = ["1403/05/15", "1403/05/16", "1403/05/17"]

    # In 3 date sheets: date is SST, ID is literal string, activity is inlineStr
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
                "B": "کاسب چهارم",
                "C": "SYNTHETIC-PHONE-001",
                "D": str(u_lk),
            }
        ],
    )

    pkg = tmp_path / "rb_01_strict_transitional.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    assert res.snapshot.total_row_count == 4
    assert (
        res.snapshot.sheets["خرید-فروش"].rows[0].raw_values["date_raw"] == "1403/05/15"
    )
    assert (
        res.snapshot.sheets["دریافت-پرداخت"].rows[0].raw_values["date_raw"]
        == "1403/05/16"
    )
    assert (
        res.snapshot.sheets["ورود-خروج"].rows[0].raw_values["date_raw"] == "1403/05/17"
    )


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
    assert res.snapshot.total_row_count == 3
    assert res.snapshot.sheets["خرید-فروش"].row_count == 0


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
    assert res.snapshot.total_row_count == 4
    r_bf = res.snapshot.sheets["خرید-فروش"].rows[0]
    assert r_bf.raw_values["party_name_raw"] == "_x0020_"
    assert r_bf.stable_id == u_bf


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
    assert res.snapshot.total_row_count == 4
    r_bf = res.snapshot.sheets["خرید-فروش"].rows[0]
    assert r_bf.raw_values["party_name_raw"] == "\x00"
    assert r_bf.stable_id == u_bf


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
    assert res.snapshot.total_row_count == 3
    assert res.snapshot.sheets["خرید-فروش"].row_count == 0


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


def test_rb_03_malformed_activity_controls_and_error_coordinates(
    tmp_path: Path,
) -> None:
    """RB-03: Malformed activity cell controls (unknown leaf tag, out-of-range SST)."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    # Control 1: Unknown leaf tag in inlineStr activity
    builder1 = SyntheticXlsxBuilder()
    builder1.shared_strings = [str(_make_uuid7(b"0000000000000001"))]
    row_unknown_leaf = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {
            "t": "inlineStr",
            "raw_inner": "<is><t><badtag>متن</badtag></t></is>",
        },
        "Z": {"t": "s", "v": "0"},
    }
    builder1.add_sheet_rows("خرید-فروش", [row_unknown_leaf])
    builder1.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder1.add_sheet_rows(
        "ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)]
    )
    builder1.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg1 = tmp_path / "unknown_leaf.xlsx"
    pkg1.write_bytes(builder1.build_bytes())

    with pytest.raises(XlsxCellError) as exc1:
        read_xlsx_source_snapshot(pkg1)
    assert exc1.value.reason == REASON_CELL_UNKNOWN_TYPE
    assert exc1.value.sheet_name == "خرید-فروش"
    assert exc1.value.cell_ref == "C2"
    assert exc1.value.physical_row_number == 2


# ==============================================================================
# RB-04: Equivalence, row/cell permutations & stream pass bounds
# ==============================================================================


def test_rb_04_equivalent_representations_and_row_cell_permutation(
    tmp_path: Path,
) -> None:
    """RB-04: Inline/direct/SST and permutations yield Oracle match."""
    u_bf1 = _make_uuid7(b"0000000000000001")
    u_bf2 = _make_uuid7(b"0000000000000002")
    u_dp = _make_uuid7(b"0000000000000003")
    u_vk = _make_uuid7(b"0000000000000004")
    u_lk = _make_uuid7(b"0000000000000005")

    # Workbook A: Standard layout, row 2 then row 3, inline and direct strings
    builder_a = SyntheticXlsxBuilder()
    builder_a.shared_strings = [str(u_bf1), str(u_bf2)]

    rows_bf_a = [
        {
            "__row_num__": 2,
            "A": "2",
            "B": "1403/05/15",
            "C": "بازرگانی الف",
            "D": "خرید",
            "E": "طلای ۱۸ عیار",
            "F": {"t": "", "v": "10.000"},
            "G": {"t": "", "v": "1000.00"},
            "Z": {"t": "s", "v": "0"},
        },
        {
            "__row_num__": 3,
            "A": "3",
            "B": "1403/05/16",
            "C": {"t": "str", "v": "بازرگانی ب"},
            "D": "فروش",
            "E": "طلای ۱۸ عیار",
            "F": {"t": "", "v": "5.000"},
            "G": {"t": "", "v": "2000.00"},
            "Z": {"t": "s", "v": "1"},
        },
    ]
    builder_a.add_sheet_rows("خرید-فروش", rows_bf_a)
    builder_a.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder_a.add_sheet_rows(
        "ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)]
    )
    builder_a.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg_a = tmp_path / "perm_a.xlsx"
    pkg_a.write_bytes(builder_a.build_bytes())

    # Workbook B: Reordered XML row elements (row 20 before row 10),
    # column elements reordered (Z before A), and SST strings remapped
    builder_b = SyntheticXlsxBuilder()
    builder_b.shared_strings = [
        "بازرگانی ب",  # SST 0
        "فروش",  # SST 1
        "بازرگانی الف",  # SST 2
        "خرید",  # SST 3
        "طلای ۱۸ عیار",  # SST 4
        str(u_bf1),  # SST 5
        str(u_bf2),  # SST 6
        "1403/05/15",  # SST 7
        "1403/05/16",  # SST 8
    ]

    # Order rows in XML: row 20 (u_bf2) placed BEFORE row 10 (u_bf1)
    rows_bf_b = [
        # Physical row 20: corresponds to u_bf2
        {
            "__row_num__": 20,
            "Z": {"t": "s", "v": "6"},
            "G": {"t": "", "v": "2000.00"},
            "F": {"t": "", "v": "5.000"},
            "E": {"t": "s", "v": "4"},
            "D": {"t": "s", "v": "1"},
            "C": {"t": "s", "v": "0"},
            "B": {"t": "s", "v": "8"},
            "A": "3",
        },
        # Physical row 10: corresponds to u_bf1
        {
            "__row_num__": 10,
            "Z": {"t": "s", "v": "5"},
            "G": {"t": "", "v": "1000.00"},
            "F": {"t": "", "v": "10.000"},
            "E": {"t": "s", "v": "4"},
            "D": {"t": "s", "v": "3"},
            "C": {"t": "s", "v": "2"},
            "B": {"t": "s", "v": "7"},
            "A": "2",
        },
    ]
    builder_b.add_sheet_rows("خرید-فروش", rows_bf_b)
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

    # Assert that XML structure of pkg_b actually has inverted row order
    with zipfile.ZipFile(pkg_b, "r") as zf_check:
        sheet1_xml = zf_check.read("xl/worksheets/sheet1.xml").decode("utf-8")
        pos_r20 = sheet1_xml.find('<row r="20"')
        pos_r10 = sheet1_xml.find('<row r="10"')
        assert pos_r20 != -1 and pos_r10 != -1
        assert pos_r20 < pos_r10, (
            "Physical row 20 must appear before row 10 in XML for permutation test"
        )

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
        stable_id=u_bf1,
        source_values={
            "date_raw": "1403/05/15",
            "party_name_raw": "بازرگانی الف",
            "transaction_type_raw": "خرید",
            "item_name_raw": "طلای ۱۸ عیار",
            "quantity_raw": Decimal("10.000"),
            "unit_price_toman_raw": Decimal("1000.00"),
            "discount_toman_raw": None,
            "notes_raw": None,
        },
    )
    exp_bf2 = SourceRowInput(
        stable_id=u_bf2,
        source_values={
            "date_raw": "1403/05/16",
            "party_name_raw": "بازرگانی ب",
            "transaction_type_raw": "فروش",
            "item_name_raw": "طلای ۱۸ عیار",
            "quantity_raw": Decimal("5.000"),
            "unit_price_toman_raw": Decimal("2000.00"),
            "discount_toman_raw": None,
            "notes_raw": None,
        },
    )
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
    exp_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[exp_bf1, exp_bf2]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[exp_dp]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[exp_vk]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[exp_lk]),
        ]
    )

    exp_loc_a = {u_bf1: 2, u_bf2: 3, u_dp: 2, u_vk: 2, u_lk: 2}
    exp_loc_b = {u_bf1: 10, u_bf2: 20, u_dp: 2, u_vk: 2, u_lk: 2}

    # In expected snapshot, rows are sorted deterministically by stable_id
    assert res_a.snapshot == res_b.snapshot
    _assert_snapshot_and_locations(res_a, exp_snapshot, exp_loc_a)
    _assert_snapshot_and_locations(res_b, exp_snapshot, exp_loc_b)


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
# Preserved Initial 10 Cases & Paired Controls
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
    assert res.snapshot.total_row_count == 3
    assert res.snapshot.sheets["خرید-فروش"].row_count == 0


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
    assert res.snapshot.total_row_count == 3
    assert res.snapshot.sheets["خرید-فروش"].row_count == 0


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
    assert res.snapshot.total_row_count == 3
    assert res.snapshot.sheets["خرید-فروش"].row_count == 0


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
    assert res.snapshot.total_row_count == 3
    assert res.snapshot.sheets["خرید-فروش"].row_count == 0


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
    assert res.snapshot.total_row_count == 3
    assert res.snapshot.sheets["خرید-فروش"].row_count == 0


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
    assert res.snapshot.total_row_count == 3
    assert res.snapshot.sheets["خرید-فروش"].row_count == 0


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
    assert res.snapshot.total_row_count == 3
    assert res.snapshot.sheets["خرید-فروش"].row_count == 0


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
    assert res.snapshot.total_row_count == 3
    assert res.snapshot.sheets["خرید-فروش"].row_count == 0


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
        "G": "1500000",
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
    assert res.snapshot.total_row_count == 4
    r_bf = res.snapshot.sheets["خرید-فروش"].rows[0]
    assert r_bf.raw_values["discount_toman_raw"] is None


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
    assert res.snapshot.total_row_count == 4
    r_bf = res.snapshot.sheets["خرید-فروش"].rows[0]
    assert r_bf.raw_values["party_name_raw"] == "SYNTHETIC-ACTIVITY-MARKER"
    assert r_bf.stable_id == u_bf


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
    assert res.snapshot.total_row_count == 4
    assert res.snapshot.sheets["خرید-فروش"].row_count == 1
    r3 = res.snapshot.sheets["خرید-فروش"].rows[0]
    assert r3.raw_values["party_name_raw"] == "بازرگانی مشترک"
    assert r3.stable_id == u_bf_active


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
    assert res.snapshot.total_row_count == 4
    for s_name in RAW_CONTRACT_REGISTRY.sheets:
        s = res.snapshot.sheets[s_name]
        assert s.row_count == 1
        assert s.rows[0].raw_values["party_name_raw"] == "بازرگانی معتبر"
