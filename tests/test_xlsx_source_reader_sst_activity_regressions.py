"""Standalone regression tests for WP-05 R5-02 (SST index selection and activity).

Verifies:
1. Cases 1-6: Inactive rows with corrupted SST index 0 (_xD800_) in record_id do NOT
   trigger decoding of entry 0 across inlineStr empty, inlineStr whitespace, str
   whitespace, SST empty, SST whitespace, and formula-covered literal activity.
2. Cases 7-8: Unneeded cells with unsupported value leaf tags
   (<unsupported>0</unsupported>) in date_raw / record_id of inactive rows are not
   decoded and do not raise errors.
3. Case 9: Covered formula cache on active row with unsupported value leaf tag is
   excluded without decoding, returning discount_toman_raw=None.
4. Case 10: Activity cell containing XML comment inside <v> node is recognized as
   valid activity and resolves SST UUIDv7 for record_id.
5. Paired controls: Corrupted SST entry referenced by an active row raises typed error;
   shared SST entry between inactive and active consumers resolves correctly.
6. Permutations and registry coverage across all four sheets.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
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
    _sample_inventory_movements_row_data,
    _sample_receipts_payments_row_data,
)


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

    # Row 2 in خرید-فروش: date B2, Z2 t="s" v="0", C2 inlineStr empty
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

    # Assert raw fixture XML has entry 0 and row 2 container
    with zipfile.ZipFile(pkg, "r") as zf:
        sst_text = zf.read("xl/sharedStrings.xml").decode("utf-8")
        assert "_xD800_" in sst_text
        ws_text = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert '<c r="Z2" t="s"><v>0</v></c>' in ws_text

    res = read_xlsx_source_snapshot(pkg)
    assert res.snapshot.total_row_count == 3
    assert res.snapshot.sheets["خرید-فروش"].row_count == 0
    assert res.snapshot.sheets["دریافت-پرداخت"].row_count == 1
    assert res.snapshot.sheets["ورود-خروج"].row_count == 1
    assert res.snapshot.sheets["لیست کسبه"].row_count == 1


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

    # Active row (party_name_raw has text) referencing corrupt index 0 in notes_raw J2
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


def test_r5_02_activity_and_sst_across_all_four_sheets(
    tmp_path: Path,
) -> None:
    """Verify activity detection and selective SST exclusion across all 4 sheets."""
    u_bf = _make_uuid7(b"0000000000000001")
    u_dp = _make_uuid7(b"0000000000000002")
    u_vk = _make_uuid7(b"0000000000000003")
    u_lk = _make_uuid7(b"0000000000000004")

    builder = SyntheticXlsxBuilder()
    # SST strings: 0: corrupt _xD800_, 1: valid party text
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
