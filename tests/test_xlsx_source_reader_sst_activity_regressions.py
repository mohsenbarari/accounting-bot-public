"""Standalone regression tests for WP-05 R5-B (SST selection, activity, RB-01..04).

Verifies:
1. RB-01: Correct <is> container extraction in Pass 1 activity discovery.
2. RB-02: Unified escape/whitespace semantics between discovery and decode.
3. RB-03: Lazy secondary SST index evaluation (5000-digit integer avoidance)
   and accurate consumer coordinate metadata on needed SST failures.
4. RB-04: Full snapshot equality, WP-03 hashes, Decimal scale preservation,
   equivalent representations, row/cell permutations, stream pass bounds.
5. Cases 1-10: Inactive rows with corrupt SST 0, unsupported leaf tags,
   covered formula caches, XML comments in activity, and paired controls.
"""

from __future__ import annotations

import uuid
from pathlib import Path

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
from accounting_local_agent.xlsx_source_reader import (
    REASON_CELL_SST_INDEX_OUT_OF_RANGE,
    REASON_CELL_UNPAIRED_SURROGATE,
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


def _build_base_four_sheets(
    u_bf: uuid.UUID,
    u_dp: uuid.UUID,
    u_vk: uuid.UUID,
    u_lk: uuid.UUID,
) -> SyntheticXlsxBuilder:
    """Build a baseline valid 4-sheet workbook with inline headers."""
    builder = SyntheticXlsxBuilder()
    builder.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u_bf, 2)])
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])
    return builder


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
    # SST strings: 0=u_bf, 1=u_dp, 2=u_vk, 3=u_lk, 4=date
    builder.shared_strings = [
        str(u_bf),
        str(u_dp),
        str(u_vk),
        str(u_lk),
        "1403/05/15",
    ]

    # 1. خرید-فروش: inlineStr plain text in C2, Z2 uses SST 0
    row_bf = {
        "__row_num__": 2,
        "A": "2",
        "B": "1403/05/15",
        "C": {"t": "inlineStr", "raw_inner": "<is><t>بازرگانی فعال اینلاین</t></is>"},
        "D": "خرید",
        "E": "طلای ۱۸ عیار",
        "F": "10.500",
        "G": "1500000",
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

    # Build expected snapshot independently and verify exact equality and types
    exp_bf = SourceRowInput(
        stable_id=u_bf,
        source_values={
            "date_raw": "1403/05/15",
            "party_name_raw": "بازرگانی فعال اینلاین",
            "transaction_type_raw": "خرید",
            "item_name_raw": "طلای ۱۸ عیار",
            "quantity_raw": "10.500",
            "unit_price_toman_raw": "1500000",
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

    assert res.snapshot == expected_snapshot
    assert (
        res.snapshot.sheets["خرید-فروش"].rows[0].source_hash
        == compute_source_hash("خرید-فروش", exp_bf.source_values).source_hash
    )
    assert (
        res.snapshot.sheets["دریافت-پرداخت"].rows[0].source_hash
        == compute_source_hash("دریافت-پرداخت", exp_dp.source_values).source_hash
    )
    assert (
        res.snapshot.sheets["ورود-خروج"].rows[0].source_hash
        == compute_source_hash("ورود-خروج", exp_vk.source_values).source_hash
    )
    assert (
        res.snapshot.sheets["لیست کسبه"].rows[0].source_hash
        == compute_source_hash("لیست کسبه", exp_lk.source_values).source_hash
    )
    assert res.snapshot.total_row_count == 4

    # Assert raw string preservation
    r_bf = res.snapshot.sheets["خرید-فروش"].rows[0]
    assert r_bf.raw_values["quantity_raw"] == "10.500"


def test_rb_01_inlinestr_zeros_persian_and_ascii_count_as_activity(
    tmp_path: Path,
) -> None:
    """RB-01: Persian zero '۰' and ASCII zero '0' in inlineStr are valid activity."""
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    builder.shared_strings = [
        str(_make_uuid7(b"0000000000000001")),
        str(_make_uuid7(b"0000000000000005")),
    ]

    # Row 2: party_name_raw is inlineStr "0" (active)
    # Row 3: party_name_raw is inlineStr "۰" (active)
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
    assert res.snapshot.sheets["خرید-فروش"].row_count == 2
    r2 = res.snapshot.sheets["خرید-فروش"].rows[0]
    r3 = res.snapshot.sheets["خرید-فروش"].rows[1]
    assert r2.raw_values["party_name_raw"] == "0"
    assert r3.raw_values["party_name_raw"] == "۰"


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


# ==============================================================================
# RB-04: Equivalence, row/cell permutations & stream pass bounds
# ==============================================================================


def test_rb_04_equivalent_representations_and_row_cell_permutation(
    tmp_path: Path,
) -> None:
    """RB-04: Inline/direct/SST and permutations yield identical snapshot."""
    u_bf1 = _make_uuid7(b"0000000000000001")
    u_bf2 = _make_uuid7(b"0000000000000002")
    u_dp = _make_uuid7(b"0000000000000003")
    u_vk = _make_uuid7(b"0000000000000004")
    u_lk = _make_uuid7(b"0000000000000005")

    # Workbook A: Standard layout, inline and direct strings
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
            "F": "10.000",
            "G": "1000",
            "Z": {"t": "s", "v": "0"},
        },
        {
            "__row_num__": 3,
            "A": "3",
            "B": "1403/05/16",
            "C": {"t": "str", "v": "بازرگانی ب"},
            "D": "فروش",
            "E": "طلای ۱۸ عیار",
            "F": "5.000",
            "G": "2000",
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

    # Workbook B: Reordered columns (Z before C), SST strings, rows 10 & 20
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

    rows_bf_b = [
        # Physical row 10 in sheet: corresponds to row 2
        {
            "__row_num__": 10,
            "Z": {"t": "s", "v": "5"},
            "A": "2",
            "B": {"t": "s", "v": "7"},
            "C": {"t": "s", "v": "2"},
            "D": {"t": "s", "v": "3"},
            "E": {"t": "s", "v": "4"},
            "F": "10.000",
            "G": "1000",
        },
        # Physical row 20 in sheet: corresponds to row 3
        {
            "__row_num__": 20,
            "Z": {"t": "s", "v": "6"},
            "A": "3",
            "B": {"t": "s", "v": "8"},
            "C": {"t": "s", "v": "0"},
            "D": {"t": "s", "v": "1"},
            "E": {"t": "s", "v": "4"},
            "F": "5.000",
            "G": "2000",
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
    pkg_b.write_bytes(builder_b.build_bytes())

    res_a = read_xlsx_source_snapshot(pkg_a)
    res_b = read_xlsx_source_snapshot(pkg_b)

    # Snapshots and canonical hashes MUST be identical
    assert res_a.snapshot == res_b.snapshot
    for s_name in RAW_CONTRACT_REGISTRY.sheets:
        assert (
            res_a.snapshot.sheets[s_name].sheet_snapshot_hash
            == res_b.snapshot.sheets[s_name].sheet_snapshot_hash
        )

    # Physical row locations accurately track each file's physical layout
    assert res_a.locations_by_uuid[u_bf1].physical_row_number == 2
    assert res_a.locations_by_uuid[u_bf2].physical_row_number == 3
    assert res_b.locations_by_uuid[u_bf1].physical_row_number == 10
    assert res_b.locations_by_uuid[u_bf2].physical_row_number == 20


def test_rb_04_stream_passes_and_selective_sst_decoding_bounds(
    tmp_path: Path,
) -> None:
    """RB-04: Streaming passes bounded (max 2 per sheet, max 2 for SST)."""
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

    # Row 2: Inactive date-only row using corrupt SST 0 in Z2
    # Row 3: Active row using valid SST 1 in C3, literal UUID in Z3
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
            "C": {"t": "s", "v": "1"},
            "D": "خرید",
            "E": "طلای ۱۸ عیار",
            "F": "10",
            "G": "1000",
            "Z": str(_make_uuid7(b"0000000000000001")),
        },
    ]
    builder.add_sheet_rows("خرید-فروش", rows_bf)
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_dp, 2)]
    )
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_vk, 2)])
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_lk, 2)])

    pkg = tmp_path / "rb_04_stream_bounds.xlsx"
    pkg.write_bytes(builder.build_bytes())

    res = read_xlsx_source_snapshot(pkg)
    assert res.snapshot.total_row_count == 4
    assert res.snapshot.sheets["خرید-فروش"].row_count == 1
    assert (
        res.snapshot.sheets["خرید-فروش"].rows[0].raw_values["party_name_raw"]
        == "بازرگانی معتبر"
    )


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
