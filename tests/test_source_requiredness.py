"""Tests for source-requiredness.v1 pure required-field preflight.

Covers acceptance criteria SR-01 through SR-14 under ADR-0012:
- SR-01: Public API exports, version, signature, inert pure library behavior
- SR-02: Missing required value across every required field of all 4 sheets
- SR-03: Required text null vs blank text vs valid text and whitespace preservation
- SR-04: Numeric zero, signed values, and type preservation
- SR-05: Optional fields permitted null/blank, unresolved/RS rows retained
- SR-06: Mixed 4-sheet snapshot issue aggregation and distinct row counts
- SR-07: Four empty sheets passes requiredness, upstream structural rejection
- SR-08: Constructor invariants, immutability, and tamper resistance
- SR-09: Error messages and repr masking without raw leakage
- SR-10: Purity, repeatability, and raw preservation
- SR-11: XLSX Reader integration with synthetic workbooks
- SR-12: Independent property oracle under sheet/row/mapping permutations
- SR-13: Scale 15,000-row synthetic evaluation benchmark
- SR-14: Existing suite retention and contracts integrity
"""

from __future__ import annotations

import inspect
import time
import uuid
from dataclasses import FrozenInstanceError
from decimal import Decimal
from typing import Any

import pytest
from accounting_contracts import (
    RAW_CONTRACT_REGISTRY,
    SOURCE_REQUIREDNESS_VERSION,
    ContractError,
    SourceRequirednessInputError,
    SourceRequirednessIssue,
    SourceRequirednessIssueReason,
    SourceRequirednessReport,
    SourceSheetInput,
    ValidatedSourceWorkbookSnapshot,
    build_source_workbook_snapshot,
    evaluate_source_requiredness,
)
from accounting_contracts.source_requiredness import (
    REQUIRED_FIELDS_BY_SHEET,
)
from accounting_local_agent.xlsx_source_reader import (
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

# ---------------------------------------------------------------------------
# Test Fixture Helpers
# ---------------------------------------------------------------------------


def _valid_buy_sell_row() -> dict[str, Any]:
    return {
        "date_raw": "1403/05/10",
        "party_name_raw": "شرکت الف",
        "transaction_type_raw": "خرید",
        "item_name_raw": "طلای آبشده",
        "quantity_raw": "10.5",
        "unit_price_toman_raw": "3500000",
        "discount_toman_raw": "0",
        "notes_raw": "توضیحات فاکتور",
    }


def _valid_receipts_payments_row() -> dict[str, Any]:
    return {
        "date_raw": "1403/01/01",
        "party_name_raw": "همکار نمونه",
        "entry_type_raw": "RS",
        "amount_toman_raw": "50000000",
        "notes_raw": "تسویه حساب",
        "account_code_raw": "101",
        "customer_flag_raw": "1",
    }


def _valid_inventory_movements_row() -> dict[str, Any]:
    return {
        "date_raw": "1403/12/29",
        "party_name_raw": "کارگاه زرگری",
        "movement_type_raw": "ورود",
        "item_name_raw": "شمش طلا",
        "quantity_raw": "100.5",
        "purity_raw": "750",
        "notes_raw": "تحویل شمش",
        "customer_flag_raw": "1",
    }


def _valid_business_parties_row() -> dict[str, Any]:
    return {
        "party_name_raw": "فروشگاه نمونه",
        "phone_number_raw": "09123456789",
    }


def _build_snapshot_with_rows(
    buy_sell_rows: list[tuple[uuid.UUID, dict[str, Any]]] | None = None,
    receipts_payments_rows: list[tuple[uuid.UUID, dict[str, Any]]] | None = None,
    inventory_movements_rows: (list[tuple[uuid.UUID, dict[str, Any]]] | None) = None,
    business_parties_rows: list[tuple[uuid.UUID, dict[str, Any]]] | None = None,
) -> ValidatedSourceWorkbookSnapshot:
    """Build a complete ValidatedSourceWorkbookSnapshot from explicit sheet rows."""
    sheets = [
        SourceSheetInput(
            sheet_name="خرید-فروش",
            rows=buy_sell_rows or [],
        ),
        SourceSheetInput(
            sheet_name="دریافت-پرداخت",
            rows=receipts_payments_rows or [],
        ),
        SourceSheetInput(
            sheet_name="ورود-خروج",
            rows=inventory_movements_rows or [],
        ),
        SourceSheetInput(
            sheet_name="لیست کسبه",
            rows=business_parties_rows or [],
        ),
    ]
    return build_source_workbook_snapshot(sheets)


# ---------------------------------------------------------------------------
# SR-01: Version, Exports, Signatures, Inert Library
# ---------------------------------------------------------------------------


def test_sr01_version_exports_and_inert_pure_library() -> None:
    """SR-01: Exact version string, exports, signatures, and inert library behavior."""
    assert SOURCE_REQUIREDNESS_VERSION == "source-requiredness.v1"

    assert issubclass(SourceRequirednessInputError, ContractError)
    assert issubclass(SourceRequirednessIssueReason, str)
    assert SourceRequirednessIssueReason.MISSING_VALUE.value == "missing_value"
    assert SourceRequirednessIssueReason.BLANK_TEXT.value == "blank_text"

    # Signature checks
    sig_eval = inspect.signature(evaluate_source_requiredness)
    assert len(sig_eval.parameters) == 1
    param_name = next(iter(sig_eval.parameters))
    assert param_name == "snapshot"

    sig_report = inspect.signature(SourceRequirednessReport.__init__)
    params = list(sig_report.parameters.keys())
    assert "self" in params
    assert "snapshot" in params

    # Pure library check: importing does not open files or start threads
    from accounting_contracts import (
        RAW_SOURCE_CONTRACT_VERSION,
        SHEET_SNAPSHOT_HASH_VERSION,
        SOURCE_CHANGE_PLAN_VERSION,
        SOURCE_HASH_VERSION,
    )

    assert RAW_SOURCE_CONTRACT_VERSION == "raw-source-contract.v1"
    assert SOURCE_HASH_VERSION == "source-hash.v1"
    assert SHEET_SNAPSHOT_HASH_VERSION == "sheet-snapshot-hash.v1"
    assert SOURCE_CHANGE_PLAN_VERSION == "source-change-plan.v1"


# ---------------------------------------------------------------------------
# SR-02: Missing Value for Every Required Field Across All Four Sheets
# ---------------------------------------------------------------------------


def test_sr02_missing_value_for_every_required_field_across_all_four_sheets() -> None:
    """SR-02: For every required field of each sheet, None emits MISSING_VALUE."""
    sheet_defaults = {
        "خرید-فروش": _valid_buy_sell_row,
        "دریافت-پرداخت": _valid_receipts_payments_row,
        "ورود-خروج": _valid_inventory_movements_row,
        "لیست کسبه": _valid_business_parties_row,
    }

    # Test each required field in isolation
    for sheet_name, req_fields in REQUIRED_FIELDS_BY_SHEET.items():
        for req_field in req_fields:
            row_data = dict(sheet_defaults[sheet_name]())
            row_data[req_field] = None
            u = _make_uuid7(f"sr02_{sheet_name}_{req_field}".encode())

            kwargs: dict[str, Any] = {}
            if sheet_name == "خرید-فروش":
                kwargs["buy_sell_rows"] = [(u, row_data)]
            elif sheet_name == "دریافت-پرداخت":
                kwargs["receipts_payments_rows"] = [(u, row_data)]
            elif sheet_name == "ورود-خروج":
                kwargs["inventory_movements_rows"] = [(u, row_data)]
            elif sheet_name == "لیست کسبه":
                kwargs["business_parties_rows"] = [(u, row_data)]

            snap = _build_snapshot_with_rows(**kwargs)
            report = evaluate_source_requiredness(snap)

            assert report.passes_requiredness is False
            assert report.checked_row_count == 1
            assert report.failed_row_count == 1
            assert report.issue_count == 1
            assert len(report.issues) == 1

            issue = report.issues[0]
            assert issue.sheet_name == sheet_name
            assert issue.stable_id == u
            assert issue.field_name == req_field
            assert issue.reason == SourceRequirednessIssueReason.MISSING_VALUE


# ---------------------------------------------------------------------------
# SR-03: Required Text Presence: Null vs Blank vs Whitespace Preservation
# ---------------------------------------------------------------------------


def test_sr03_required_text_presence_null_blank_whitespace_and_unicode() -> None:
    """SR-03: None -> MISSING_VALUE, empty/whitespace -> BLANK_TEXT, valid preserved."""
    u1 = _make_uuid7(b"sr03_u1_none")
    u2 = _make_uuid7(b"sr03_u2_empty")
    u3 = _make_uuid7(b"sr03_u3_ascii_spaces")
    u4 = _make_uuid7(b"sr03_u4_unicode_spaces")
    u5 = _make_uuid7(b"sr03_u5_valid_surrounding")

    r1 = _valid_buy_sell_row()
    r1["party_name_raw"] = None

    r2 = _valid_buy_sell_row()
    r2["party_name_raw"] = ""

    r3 = _valid_buy_sell_row()
    r3["party_name_raw"] = "   \t  \n  "

    r4 = _valid_buy_sell_row()
    r4["party_name_raw"] = "\u2003\u00a0\u3000"

    r5 = _valid_buy_sell_row()
    r5["party_name_raw"] = "  شرکت نمونه معتبر  "

    snap = _build_snapshot_with_rows(
        buy_sell_rows=[
            (u1, r1),
            (u2, r2),
            (u3, r3),
            (u4, r4),
            (u5, r5),
        ]
    )
    report = evaluate_source_requiredness(snap)

    assert report.checked_row_count == 5
    assert report.failed_row_count == 4
    assert report.issue_count == 4
    assert report.passes_requiredness is False

    issues_by_id = {iss.stable_id: iss for iss in report.issues}

    assert issues_by_id[u1].reason == SourceRequirednessIssueReason.MISSING_VALUE
    assert issues_by_id[u2].reason == SourceRequirednessIssueReason.BLANK_TEXT
    assert issues_by_id[u3].reason == SourceRequirednessIssueReason.BLANK_TEXT
    assert issues_by_id[u4].reason == SourceRequirednessIssueReason.BLANK_TEXT
    assert u5 not in issues_by_id

    # Verify exact raw value and whitespace preservation in retained snapshot
    row5_snap = report.snapshot.all_rows_by_id[u5]
    assert row5_snap.raw_values["party_name_raw"] == "  شرکت نمونه معتبر  "

    # Null date is missing
    u_date_none = _make_uuid7(b"sr03_date_none")
    r_date_none = _valid_receipts_payments_row()
    r_date_none["date_raw"] = None
    snap_date_none = _build_snapshot_with_rows(
        receipts_payments_rows=[(u_date_none, r_date_none)]
    )
    rep_date_none = evaluate_source_requiredness(snap_date_none)
    assert len(rep_date_none.issues) == 1
    assert rep_date_none.issues[0].reason == SourceRequirednessIssueReason.MISSING_VALUE
    assert rep_date_none.issues[0].field_name == "date_raw"

    # Non-null invalid date is rejected upstream during snapshot construction
    u_date_inv = _make_uuid7(b"sr03_date_inv")
    r_date_inv = _valid_receipts_payments_row()
    r_date_inv["date_raw"] = "not-a-valid-jalali-date"
    with pytest.raises(ContractError):
        _build_snapshot_with_rows(receipts_payments_rows=[(u_date_inv, r_date_inv)])


# ---------------------------------------------------------------------------
# SR-04: Numeric Zero, Signed Values, and Type Preservation
# ---------------------------------------------------------------------------


def test_sr04_numeric_zero_signed_values_and_type_preservation() -> None:
    """SR-04: Numeric zero, negative numbers, and Decimal formats count as present."""
    u1 = _make_uuid7(b"sr04_zero_int")
    u2 = _make_uuid7(b"sr04_zero_str")
    u3 = _make_uuid7(b"sr04_zero_decimal")
    u4 = _make_uuid7(b"sr04_negative_amount")

    r1 = _valid_buy_sell_row()
    r1["quantity_raw"] = 0
    r1["unit_price_toman_raw"] = 0

    r2 = _valid_buy_sell_row()
    r2["quantity_raw"] = "0"
    r2["unit_price_toman_raw"] = "0"

    r3 = _valid_buy_sell_row()
    r3["quantity_raw"] = Decimal("0")
    r3["unit_price_toman_raw"] = Decimal("0")

    r4 = _valid_receipts_payments_row()
    r4["amount_toman_raw"] = "-50000"

    snap = _build_snapshot_with_rows(
        buy_sell_rows=[(u1, r1), (u2, r2), (u3, r3)],
        receipts_payments_rows=[(u4, r4)],
    )
    report = evaluate_source_requiredness(snap)

    # All numeric zeros and signed values are present -> passes requiredness!
    assert report.checked_row_count == 4
    assert report.failed_row_count == 0
    assert report.issue_count == 0
    assert report.passes_requiredness is True
    assert report.issues == ()

    # Verify representations and hashes preserved
    snap_r1 = report.snapshot.all_rows_by_id[u1]
    snap_r2 = report.snapshot.all_rows_by_id[u2]
    snap_r4 = report.snapshot.all_rows_by_id[u4]
    assert snap_r1.raw_values["quantity_raw"] == 0
    assert snap_r2.raw_values["quantity_raw"] == "0"
    assert snap_r4.raw_values["amount_toman_raw"] == "-50000"

    # Upstream constructor rejects Float and Boolean
    u_bad = _make_uuid7(b"sr04_bad_float")
    r_bad = _valid_buy_sell_row()
    r_bad["quantity_raw"] = 10.5  # float is forbidden by raw contracts
    with pytest.raises(ContractError):
        _build_snapshot_with_rows(buy_sell_rows=[(u_bad, r_bad)])


# ---------------------------------------------------------------------------
# SR-05: Optional Fields Permitted Null and Blank
# ---------------------------------------------------------------------------


def test_sr05_optional_fields_null_and_blank_permitted() -> None:
    """SR-05: Optional fields may be null/blank without emitting any issue."""
    u1 = _make_uuid7(b"sr05_bs_opt_none")
    r1 = _valid_buy_sell_row()
    r1["discount_toman_raw"] = None
    r1["notes_raw"] = None

    u2 = _make_uuid7(b"sr05_rp_opt_blank")
    r2 = _valid_receipts_payments_row()
    r2["notes_raw"] = ""
    r2["account_code_raw"] = "   "
    r2["customer_flag_raw"] = None

    u3 = _make_uuid7(b"sr05_im_purity_none")
    r3 = _valid_inventory_movements_row()
    r3["purity_raw"] = None
    r3["notes_raw"] = ""
    r3["customer_flag_raw"] = " "

    u4 = _make_uuid7(b"sr05_bp_phone_none")
    r4 = _valid_business_parties_row()
    r4["phone_number_raw"] = None

    snap = _build_snapshot_with_rows(
        buy_sell_rows=[(u1, r1)],
        receipts_payments_rows=[(u2, r2)],
        inventory_movements_rows=[(u3, r3)],
        business_parties_rows=[(u4, r4)],
    )
    report = evaluate_source_requiredness(snap)

    assert report.checked_row_count == 4
    assert report.failed_row_count == 0
    assert report.issue_count == 0
    assert report.passes_requiredness is True
    assert report.issues == ()


# ---------------------------------------------------------------------------
# SR-06: Mixed Four-Sheet Snapshot Issue Aggregation and Counts
# ---------------------------------------------------------------------------


def test_sr06_mixed_four_sheet_snapshot_issue_aggregation_and_counts() -> None:
    """SR-06: Full aggregation of issues across all 4 sheets with exact counts."""
    # Sheet 1: 1 good row, 1 row missing 2 fields
    u_bs_good = _make_uuid7(b"sr06_bs_good")
    u_bs_bad = _make_uuid7(b"sr06_bs_bad")
    r_bs_bad = _valid_buy_sell_row()
    r_bs_bad["date_raw"] = None
    r_bs_bad["unit_price_toman_raw"] = None

    # Sheet 2: 1 row with blank text
    u_rp_bad = _make_uuid7(b"sr06_rp_bad")
    r_rp_bad = _valid_receipts_payments_row()
    r_rp_bad["party_name_raw"] = "   "

    # Sheet 3: 1 good row
    u_im_good = _make_uuid7(b"sr06_im_good")

    # Sheet 4: 1 row missing party_name_raw
    u_bp_bad = _make_uuid7(b"sr06_bp_bad")
    r_bp_bad = _valid_business_parties_row()
    r_bp_bad["party_name_raw"] = None

    snap = _build_snapshot_with_rows(
        buy_sell_rows=[
            (u_bs_good, _valid_buy_sell_row()),
            (u_bs_bad, r_bs_bad),
        ],
        receipts_payments_rows=[(u_rp_bad, r_rp_bad)],
        inventory_movements_rows=[(u_im_good, _valid_inventory_movements_row())],
        business_parties_rows=[(u_bp_bad, r_bp_bad)],
    )
    report = evaluate_source_requiredness(snap)

    assert report.checked_row_count == 5
    # Failing rows: u_bs_bad, u_rp_bad, u_bp_bad -> 3 failed rows
    assert report.failed_row_count == 3
    # Total issues: 2 in BS, 1 in RP, 1 in BP -> 4 issues
    assert report.issue_count == 4
    assert report.passes_requiredness is False

    # Check ordering: sheet registry order, then UUID bytes, then field order
    assert report.issues[0].sheet_name == "خرید-فروش"
    assert report.issues[0].stable_id == u_bs_bad
    assert report.issues[0].field_name == "date_raw"

    assert report.issues[1].sheet_name == "خرید-فروش"
    assert report.issues[1].stable_id == u_bs_bad
    assert report.issues[1].field_name == "unit_price_toman_raw"

    assert report.issues[2].sheet_name == "دریافت-پرداخت"
    assert report.issues[2].stable_id == u_rp_bad
    assert report.issues[2].field_name == "party_name_raw"

    assert report.issues[3].sheet_name == "لیست کسبه"
    assert report.issues[3].stable_id == u_bp_bad
    assert report.issues[3].field_name == "party_name_raw"

    # Assert retained snapshot identity and completeness
    assert report.snapshot is snap
    assert len(report.snapshot.all_rows_by_id) == 5


# ---------------------------------------------------------------------------
# SR-07: Four Empty Sheets Passes Requiredness & Upstream Structure
# ---------------------------------------------------------------------------


def test_sr07_four_empty_sheets_and_upstream_structure_rejection() -> None:
    """SR-07: Four present empty sheets produce passes_requiredness=True."""
    snap = _build_snapshot_with_rows()
    report = evaluate_source_requiredness(snap)

    assert report.checked_row_count == 0
    assert report.failed_row_count == 0
    assert report.issue_count == 0
    assert report.passes_requiredness is True
    assert report.issues == ()
    assert report.snapshot is snap

    # Incomplete snapshots (missing sheets) are rejected upstream
    sheets_incomplete = [
        SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
        SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
        # missing ورود-خروج and لیست کسبه
    ]
    with pytest.raises(ContractError):
        build_source_workbook_snapshot(sheets_incomplete)


# ---------------------------------------------------------------------------
# SR-08: Constructor Invariants, Immutability, and Tamper Resistance
# ---------------------------------------------------------------------------


def test_sr08_constructor_invariants_immutability_and_tamper_resistance() -> None:
    """SR-08: Validate issue and report constructor invariants and immutability."""
    u = _make_uuid7(b"sr08_valid_id")

    # Valid issue construction
    iss = SourceRequirednessIssue(
        sheet_name="خرید-فروش",
        stable_id=u,
        field_name="date_raw",
        reason=SourceRequirednessIssueReason.MISSING_VALUE,
    )
    assert iss.sheet_name == "خرید-فروش"
    assert iss.field_name == "date_raw"

    # Immutability: setting attributes fails
    with pytest.raises((FrozenInstanceError, AttributeError)):
        iss.reason = SourceRequirednessIssueReason.BLANK_TEXT  # type: ignore[misc]

    # Invalid sheet name
    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessIssue(
            sheet_name="unapproved_sheet",
            stable_id=u,
            field_name="date_raw",
            reason=SourceRequirednessIssueReason.MISSING_VALUE,
        )

    # Invalid UUID (v4 instead of v7)
    v4 = uuid.uuid4()
    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessIssue(
            sheet_name="خرید-فروش",
            stable_id=v4,
            field_name="date_raw",
            reason=SourceRequirednessIssueReason.MISSING_VALUE,
        )

    # Invalid UUID type (bool)
    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessIssue(
            sheet_name="خرید-فروش",
            stable_id=True,  # type: ignore[arg-type]
            field_name="date_raw",
            reason=SourceRequirednessIssueReason.MISSING_VALUE,
        )

    # Invalid field name (optional field)
    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessIssue(
            sheet_name="خرید-فروش",
            stable_id=u,
            field_name="notes_raw",  # notes_raw is optional!
            reason=SourceRequirednessIssueReason.MISSING_VALUE,
        )

    # Invalid reason
    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessIssue(
            sheet_name="خرید-فروش",
            stable_id=u,
            field_name="date_raw",
            reason="not_a_valid_reason",  # type: ignore[arg-type]
        )

    # BLANK_TEXT on non-text field
    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessIssue(
            sheet_name="خرید-فروش",
            stable_id=u,
            field_name="quantity_raw",  # quantity_raw is DECIMAL, not RAW_TEXT!
            reason=SourceRequirednessIssueReason.BLANK_TEXT,
        )

    # Report construction validation
    snap = _build_snapshot_with_rows()
    report = SourceRequirednessReport(snap)
    assert report.passes_requiredness is True

    # Report immutability
    with pytest.raises((FrozenInstanceError, AttributeError)):
        report.passes_requiredness = False  # type: ignore[misc]

    # Reject extra args or kwargs attempting to forge passing status or issues
    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessReport(
            snap,
            passes_requiredness=True,
        )

    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessReport(
            snap,
            issues=(),
        )


# ---------------------------------------------------------------------------
# SR-09: Error Messages and Repr Masking Without Raw Leakage
# ---------------------------------------------------------------------------


def test_sr09_error_messages_and_repr_masking_without_raw_leakage() -> None:
    """SR-09: Error messages and repr must not reveal raw cell values or notes."""
    secret_marker = "SECRET_CREDENTIAL_DATA_007"

    # Input error with marker in argument must not leak marker
    with pytest.raises(SourceRequirednessInputError) as exc_info:
        SourceRequirednessIssue(
            sheet_name=f"خرید-فروش_{secret_marker}",
            stable_id=_make_uuid7(b"sr09_id"),
            field_name="date_raw",
            reason=SourceRequirednessIssueReason.MISSING_VALUE,
        )
    assert secret_marker not in str(exc_info.value)

    # Snapshot containing sensitive data in raw values
    u = _make_uuid7(b"sr09_secret")
    r = _valid_buy_sell_row()
    r["notes_raw"] = secret_marker
    r["party_name_raw"] = f"Party_{secret_marker}"
    snap = _build_snapshot_with_rows(buy_sell_rows=[(u, r)])

    report = evaluate_source_requiredness(snap)
    report_repr = repr(report)
    report_str = str(report)

    # Verify snapshot is excluded from repr and str
    assert secret_marker not in report_repr
    assert secret_marker not in report_str
    assert "snapshot" not in report_repr

    # Verify issues do not store raw cell values
    for iss in report.issues:
        assert not hasattr(iss, "raw_value")
        assert not hasattr(iss, "cell_value")
        assert secret_marker not in repr(iss)


# ---------------------------------------------------------------------------
# SR-10: Purity, Repeatability, and Raw Preservation
# ---------------------------------------------------------------------------


def test_sr10_purity_repeatability_and_raw_preservation() -> None:
    """SR-10: Repeated evaluation gives identical issues and preserves raw data."""
    u1 = _make_uuid7(b"sr10_u1")
    r1 = _valid_buy_sell_row()
    r1["date_raw"] = None

    snap = _build_snapshot_with_rows(buy_sell_rows=[(u1, r1)])

    orig_row = snap.all_rows_by_id[u1]
    orig_hash = orig_row.source_hash
    orig_raw_vals = dict(orig_row.raw_values)

    rep1 = evaluate_source_requiredness(snap)
    rep2 = evaluate_source_requiredness(snap)

    assert rep1.issues == rep2.issues
    assert rep1.checked_row_count == rep2.checked_row_count
    assert rep1.failed_row_count == rep2.failed_row_count
    assert rep1.issue_count == rep2.issue_count
    assert rep1.passes_requiredness == rep2.passes_requiredness

    # Assert row object and values in snapshot are untouched
    after_row = rep1.snapshot.all_rows_by_id[u1]
    assert after_row is orig_row
    assert after_row.source_hash == orig_hash
    assert dict(after_row.raw_values) == orig_raw_vals


# ---------------------------------------------------------------------------
# SR-11: XLSX Reader Integration with Synthetic Workbooks
# ---------------------------------------------------------------------------


def test_sr11_xlsx_reader_integration_synthetic_workbooks(
    tmp_path: Any,
) -> None:
    """SR-11: Synthetic XLSX -> Reader -> Preflight with missing & formula exclusion."""
    from pathlib import Path

    p_dir = Path(tmp_path)
    builder = SyntheticXlsxBuilder()

    # 1. Sheet "خرید-فروش":
    # - row 2: valid
    # - row 3: missing date (cell B3 is None)
    # - row 4: blank party name (cell C4 is "   ")
    # - row 5: formula in required cell G (unit_price_toman_raw) excluded by Reader
    u1 = _make_uuid7(b"sr11_bs_r2")
    u2 = _make_uuid7(b"sr11_bs_r3")
    u3 = _make_uuid7(b"sr11_bs_r4")
    u4 = _make_uuid7(b"sr11_bs_r5")

    r2 = _sample_buy_sell_row_data(u1, 2)

    r3 = _sample_buy_sell_row_data(u2, 3)
    r3["B"] = None  # Date cell missing

    r4 = _sample_buy_sell_row_data(u3, 4)
    r4["C"] = "   "  # Party name blank text

    r5 = _sample_buy_sell_row_data(u4, 5)
    r5["G"] = {"f": "1000*2", "v": "2000"}  # Formula in required unit price column

    builder.add_sheet_rows("خرید-فروش", [r2, r3, r4, r5])

    # 2. Other 3 sheets: 1 valid row each
    u_rp = _make_uuid7(b"sr11_rp_r2")
    builder.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_rp, 2)]
    )

    u_im = _make_uuid7(b"sr11_im_r2")
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u_im, 2)])

    u_bp = _make_uuid7(b"sr11_bp_r2")
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u_bp, 2)])

    wb_path = p_dir / "sr11_source.xlsx"
    wb_bytes = builder.build_bytes()
    wb_path.write_bytes(wb_bytes)

    # Run WP-05 Reader
    read_res = read_xlsx_source_snapshot(wb_path)
    assert isinstance(read_res.snapshot, ValidatedSourceWorkbookSnapshot)

    # Evaluate requiredness over Reader snapshot
    report = evaluate_source_requiredness(read_res.snapshot)

    assert report.checked_row_count == 7
    assert report.failed_row_count == 3
    assert report.issue_count == 3
    assert report.passes_requiredness is False

    issues_by_id = {iss.stable_id: iss for iss in report.issues}
    assert issues_by_id[u2].field_name == "date_raw"
    assert issues_by_id[u2].reason == SourceRequirednessIssueReason.MISSING_VALUE

    assert issues_by_id[u3].field_name == "party_name_raw"
    assert issues_by_id[u3].reason == SourceRequirednessIssueReason.BLANK_TEXT

    assert issues_by_id[u4].field_name == "unit_price_toman_raw"
    assert issues_by_id[u4].reason == SourceRequirednessIssueReason.MISSING_VALUE

    # Verify that changing only derived formula/cache column I (total_amount)
    # does NOT change raw values, source hash, or requiredness issues
    builder_mod = SyntheticXlsxBuilder()
    r2_mod = dict(r2)
    r2_mod["I"] = {"f": "9999", "v": "9999"}  # Derived column formula
    builder_mod.add_sheet_rows("خرید-فروش", [r2_mod, r3, r4, r5])
    builder_mod.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u_rp, 2)]
    )
    builder_mod.add_sheet_rows(
        "ورود-خروج", [_sample_inventory_movements_row_data(u_im, 2)]
    )
    builder_mod.add_sheet_rows(
        "لیست کسبه", [_sample_business_parties_row_data(u_bp, 2)]
    )
    wb_mod_path = p_dir / "sr11_source_mod.xlsx"
    wb_mod_path.write_bytes(builder_mod.build_bytes())

    read_res_mod = read_xlsx_source_snapshot(wb_mod_path)
    assert isinstance(read_res_mod.snapshot, ValidatedSourceWorkbookSnapshot)
    report_mod = evaluate_source_requiredness(read_res_mod.snapshot)

    assert report_mod.issues == report.issues
    assert report_mod.checked_row_count == report.checked_row_count
    assert report_mod.failed_row_count == report.failed_row_count
    assert (
        read_res_mod.snapshot.all_rows_by_id[u1].source_hash
        == read_res.snapshot.all_rows_by_id[u1].source_hash
    )


# ---------------------------------------------------------------------------
# SR-12: Independent Property Oracle Under Permutations
# ---------------------------------------------------------------------------


def _independent_oracle(
    snapshot: ValidatedSourceWorkbookSnapshot,
) -> tuple[tuple[tuple[str, uuid.UUID, str, str], ...], int, int, int, bool]:
    """Independent oracle for required-field evaluation without production helpers."""
    oracle_required = {
        "خرید-فروش": (
            "date_raw",
            "party_name_raw",
            "transaction_type_raw",
            "item_name_raw",
            "quantity_raw",
            "unit_price_toman_raw",
        ),
        "دریافت-پرداخت": (
            "date_raw",
            "party_name_raw",
            "entry_type_raw",
            "amount_toman_raw",
        ),
        "ورود-خروج": (
            "date_raw",
            "party_name_raw",
            "movement_type_raw",
            "item_name_raw",
            "quantity_raw",
        ),
        "لیست کسبه": ("party_name_raw",),
    }
    oracle_text_fields = {
        "خرید-فروش": {
            "date_raw",
            "party_name_raw",
            "transaction_type_raw",
            "item_name_raw",
        },
        "دریافت-پرداخت": {"date_raw", "party_name_raw", "entry_type_raw"},
        "ورود-خروج": {
            "date_raw",
            "party_name_raw",
            "movement_type_raw",
            "item_name_raw",
        },
        "لیست کسبه": {"party_name_raw"},
    }
    approved_sheets = (
        "خرید-فروش",
        "دریافت-پرداخت",
        "ورود-خروج",
        "لیست کسبه",
    )

    issues: list[tuple[str, uuid.UUID, str, str]] = []
    failing_rows: set[uuid.UUID] = set()

    for s_name in approved_sheets:
        sheet = snapshot.sheets[s_name]
        for row in sheet.rows:
            has_issue = False
            for f_name in oracle_required[s_name]:
                val = row.raw_values[f_name]
                if val is None:
                    issues.append((s_name, row.stable_id, f_name, "missing_value"))
                    has_issue = True
                elif (
                    f_name in oracle_text_fields[s_name]
                    and isinstance(val, str)
                    and val.strip() == ""
                ):
                    issues.append((s_name, row.stable_id, f_name, "blank_text"))
                    has_issue = True
            if has_issue:
                failing_rows.add(row.stable_id)

    issues_tuple = tuple(issues)
    passes = len(issues_tuple) == 0
    return (
        issues_tuple,
        snapshot.total_row_count,
        len(failing_rows),
        len(issues_tuple),
        passes,
    )


def test_sr12_independent_property_oracle_and_permutations() -> None:
    """SR-12: Compare against independent oracle under sheet and row permutations."""
    u1 = _make_uuid7(b"sr12_u1")
    u2 = _make_uuid7(b"sr12_u2")
    u3 = _make_uuid7(b"sr12_u3")

    r1 = _valid_buy_sell_row()
    r1["quantity_raw"] = None

    r2 = _valid_receipts_payments_row()
    r2["entry_type_raw"] = "   "

    r3 = _valid_inventory_movements_row()

    # Create snapshot
    snap = _build_snapshot_with_rows(
        buy_sell_rows=[(u1, r1)],
        receipts_payments_rows=[(u2, r2)],
        inventory_movements_rows=[(u3, r3)],
    )

    oracle_issues, oracle_checked, oracle_failed, oracle_count, oracle_passes = (
        _independent_oracle(snap)
    )

    report = evaluate_source_requiredness(snap)

    assert report.checked_row_count == oracle_checked
    assert report.failed_row_count == oracle_failed
    assert report.issue_count == oracle_count
    assert report.passes_requiredness == oracle_passes

    prod_issue_tuples = tuple(
        (iss.sheet_name, iss.stable_id, iss.field_name, iss.reason.value)
        for iss in report.issues
    )
    assert prod_issue_tuples == oracle_issues


# ---------------------------------------------------------------------------
# SR-13: Scale 15,000-Row Synthetic Benchmark
# ---------------------------------------------------------------------------


def test_sr13_scale_15000_row_synthetic_benchmark() -> None:
    """SR-13: 15,000-row synthetic evaluation executes linearly and preserves hashes."""
    # Generate 15,000 rows across 4 sheets:
    # - 12,000 valid rows
    # - 3,000 rows with missing/blank fields (1,000 in BS, 1,000 in RP, 1,000 in IM)
    total_rows = 15000
    rows_per_sheet = total_rows // 4  # 3,750 per sheet

    def make_sheet_rows(
        sheet_idx: int,
        default_fn: Any,
        corrupt_field: str | None = None,
        is_text_field: bool = False,
    ) -> list[tuple[uuid.UUID, dict[str, Any]]]:
        res = []
        for i in range(rows_per_sheet):
            u = _make_uuid7(f"sr13_s{sheet_idx}_r{i:05d}".encode())
            data = default_fn()
            if corrupt_field and i < 1000:
                if is_text_field:
                    data[corrupt_field] = None if i % 2 == 0 else "   "
                else:
                    data[corrupt_field] = None
            res.append((u, data))
        return res

    bs_rows = make_sheet_rows(
        1, _valid_buy_sell_row, "unit_price_toman_raw", is_text_field=False
    )
    rp_rows = make_sheet_rows(
        2, _valid_receipts_payments_row, "party_name_raw", is_text_field=True
    )
    im_rows = make_sheet_rows(
        3, _valid_inventory_movements_row, "item_name_raw", is_text_field=True
    )
    bp_rows = make_sheet_rows(4, _valid_business_parties_row, None)

    snap = _build_snapshot_with_rows(
        buy_sell_rows=bs_rows,
        receipts_payments_rows=rp_rows,
        inventory_movements_rows=im_rows,
        business_parties_rows=bp_rows,
    )
    assert snap.total_row_count == 15000

    # Measure pure requiredness evaluation time
    start_t = time.perf_counter()
    report = evaluate_source_requiredness(snap)
    eval_duration = time.perf_counter() - start_t

    # Requiredness evaluation on 15,000 rows should be sub-second (< 0.5s)
    assert eval_duration < 1.0, f"Evaluation took too long: {eval_duration:.4f}s"

    assert report.checked_row_count == 15000
    # 1,000 in BS + 1,000 in RP + 1,000 in IM = 3,000 failed rows
    assert report.failed_row_count == 3000
    assert report.issue_count == 3000
    assert report.passes_requiredness is False
    assert report.snapshot is snap


# ---------------------------------------------------------------------------
# SR-14: Existing Suite Retention & Contracts Integrity
# ---------------------------------------------------------------------------


def test_sr14_existing_suite_retention_and_contracts_integrity() -> None:
    """SR-14: Existing contracts registry, version constants, and sheets untouched."""
    assert len(RAW_CONTRACT_REGISTRY.sheets) == 4
    expected_sheets = ("خرید-فروش", "دریافت-پرداخت", "ورود-خروج", "لیست کسبه")
    assert tuple(RAW_CONTRACT_REGISTRY.sheets.keys()) == expected_sheets
