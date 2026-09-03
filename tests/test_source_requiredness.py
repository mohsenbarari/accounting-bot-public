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

import builtins
import importlib
import inspect
import socket
import sys
import threading
import time
import uuid
from dataclasses import FrozenInstanceError
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
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
from accounting_local_agent.xlsx_source_reader import (
    read_xlsx_source_snapshot,
)
from hypothesis import given, settings
from hypothesis import strategies as st
from test_xlsx_source_reader import (
    SyntheticXlsxBuilder,
    _make_uuid7,
    _sample_business_parties_row_data,
    _sample_buy_sell_row_data,
    _sample_inventory_movements_row_data,
    _sample_receipts_payments_row_data,
)

# ---------------------------------------------------------------------------
# Independent Required-Field Matrix & Text Fields (Authored Independently)
# ---------------------------------------------------------------------------

INDEPENDENT_APPROVED_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
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

INDEPENDENT_TEXT_FIELDS: dict[str, frozenset[str]] = {
    "خرید-فروش": frozenset(
        {"date_raw", "party_name_raw", "transaction_type_raw", "item_name_raw"}
    ),
    "دریافت-پرداخت": frozenset({"date_raw", "party_name_raw", "entry_type_raw"}),
    "ورود-خروج": frozenset(
        {"date_raw", "party_name_raw", "movement_type_raw", "item_name_raw"}
    ),
    "لیست کسبه": frozenset({"party_name_raw"}),
}

ALL_16_REQUIRED_SHEET_FIELDS: list[tuple[str, str]] = [
    (sheet, field)
    for sheet, fields in INDEPENDENT_APPROVED_REQUIRED_FIELDS.items()
    for field in fields
]

# The nine required raw-text entries across the 4 sheets
INDEPENDENT_REQUIRED_TEXT_FIELDS: list[tuple[str, str]] = [
    ("خرید-فروش", "party_name_raw"),
    ("خرید-فروش", "transaction_type_raw"),
    ("خرید-فروش", "item_name_raw"),
    ("دریافت-پرداخت", "party_name_raw"),
    ("دریافت-پرداخت", "entry_type_raw"),
    ("ورود-خروج", "party_name_raw"),
    ("ورود-خروج", "movement_type_raw"),
    ("ورود-خروج", "item_name_raw"),
    ("لیست کسبه", "party_name_raw"),
]

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

    # Pure library check: importing does not modify version constants
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


def test_sr01_fresh_import_under_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """SR-01: Fresh module import executes inertly under a side-effect guard."""
    target_mod_name = "accounting_contracts.source_requiredness"

    # Ensure module is evicted from sys.modules so import is guaranteed fresh
    sys.modules.pop(target_mod_name, None)

    def _forbidden_call(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Forbidden side-effect invoked during pure module import!")

    monkeypatch.setattr(socket, "socket", _forbidden_call)
    monkeypatch.setattr(time, "time", _forbidden_call)
    monkeypatch.setattr(time, "monotonic", _forbidden_call)
    monkeypatch.setattr(time, "monotonic_ns", _forbidden_call)
    monkeypatch.setattr(uuid, "uuid4", _forbidden_call)
    if hasattr(uuid, "uuid7"):
        monkeypatch.setattr(uuid, "uuid7", _forbidden_call)
    monkeypatch.setattr(threading.Thread, "start", _forbidden_call)
    monkeypatch.setattr(Path, "write_bytes", _forbidden_call)
    monkeypatch.setattr(Path, "write_text", _forbidden_call)

    # Permit Python module-loader reads while rejecting writes or data reads
    orig_open = builtins.open

    def guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(m in mode for m in ("w", "a", "+", "x")):
            raise AssertionError(f"File write forbidden during module import: {file}")
        f_str = str(file)
        if not (
            f_str.endswith(".py")
            or f_str.endswith(".pyc")
            or f_str.endswith(".so")
            or "/lib/" in f_str
            or "/site-packages/" in f_str
            or "/packages/" in f_str
        ):
            raise AssertionError(
                f"Application data read forbidden during module import: {file}"
            )
        return orig_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)

    # Import module afresh under guard
    mod = importlib.import_module(target_mod_name)
    assert mod is sys.modules[target_mod_name]
    assert mod.SOURCE_REQUIREDNESS_VERSION == "source-requiredness.v1"


# ---------------------------------------------------------------------------
# SR-02: Missing Value for Every Required Field Across All Four Sheets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sheet_name,req_field", ALL_16_REQUIRED_SHEET_FIELDS)
def test_sr02_missing_value_for_every_required_field_across_all_four_sheets(
    sheet_name: str, req_field: str
) -> None:
    """SR-02: For every of the 16 required fields, None emits MISSING_VALUE."""
    sheet_defaults = {
        "خرید-فروش": _valid_buy_sell_row,
        "دریافت-پرداخت": _valid_receipts_payments_row,
        "ورود-خروج": _valid_inventory_movements_row,
        "لیست کسبه": _valid_business_parties_row,
    }
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
    assert issue.reason is SourceRequirednessIssueReason.MISSING_VALUE


def test_sr02_multiple_rows_and_fields_aggregate_all_missing_issues() -> None:
    """SR-02: Multiple rows and missing fields aggregate expected issues."""
    u1 = _make_uuid7(b"sr02_multi_row1")
    r1 = _valid_buy_sell_row()
    r1["date_raw"] = None
    r1["quantity_raw"] = None

    u2 = _make_uuid7(b"sr02_multi_row2")
    r2 = _valid_buy_sell_row()
    r2["unit_price_toman_raw"] = None

    snap = _build_snapshot_with_rows(buy_sell_rows=[(u1, r1), (u2, r2)])
    report = evaluate_source_requiredness(snap)

    assert report.checked_row_count == 2
    assert report.failed_row_count == 2
    assert report.issue_count == 3
    assert report.passes_requiredness is False


def test_sr02_omission_of_transaction_type_raw_is_detected() -> None:
    """SR-02: Prove omission of transaction_type_raw causes failure."""
    u = _make_uuid7(b"sr02_omission_check")
    row_data = _valid_buy_sell_row()
    row_data["transaction_type_raw"] = None
    snap = _build_snapshot_with_rows(buy_sell_rows=[(u, row_data)])

    report = evaluate_source_requiredness(snap)
    assert report.passes_requiredness is False
    assert any(
        iss.sheet_name == "خرید-فروش"
        and iss.field_name == "transaction_type_raw"
        and iss.reason is SourceRequirednessIssueReason.MISSING_VALUE
        for iss in report.issues
    )


# ---------------------------------------------------------------------------
# SR-03: Required Text Presence: Matrix of All 9 Text Fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sheet_name,text_field", INDEPENDENT_REQUIRED_TEXT_FIELDS)
def test_sr03_required_text_presence_matrix_across_all_nine_text_fields(
    sheet_name: str, text_field: str
) -> None:
    """SR-03: Cover present/whitespace/None/empty/blank across 9 text fields."""
    sheet_defaults = {
        "خرید-فروش": _valid_buy_sell_row,
        "دریافت-پرداخت": _valid_receipts_payments_row,
        "ورود-خروج": _valid_inventory_movements_row,
        "لیست کسبه": _valid_business_parties_row,
    }

    def _make_snap_with_value(
        u_val: uuid.UUID, val: Any
    ) -> ValidatedSourceWorkbookSnapshot:
        row = dict(sheet_defaults[sheet_name]())
        row[text_field] = val
        kwargs: dict[str, Any] = {}
        if sheet_name == "خرید-فروش":
            kwargs["buy_sell_rows"] = [(u_val, row)]
        elif sheet_name == "دریافت-پرداخت":
            kwargs["receipts_payments_rows"] = [(u_val, row)]
        elif sheet_name == "ورود-خروج":
            kwargs["inventory_movements_rows"] = [(u_val, row)]
        elif sheet_name == "لیست کسبه":
            kwargs["business_parties_rows"] = [(u_val, row)]
        return _build_snapshot_with_rows(**kwargs)

    # 1. Valid present text -> 0 issues, passes
    u_valid = _make_uuid7(f"sr03_{sheet_name}_{text_field}_v".encode())
    snap_valid = _make_snap_with_value(u_valid, "مقدار_معتبر_آزمون")
    rep_valid = evaluate_source_requiredness(snap_valid)
    assert rep_valid.passes_requiredness is True
    assert rep_valid.issue_count == 0

    # 2. Valid text with surrounding whitespace -> preserved verbatim, passes
    u_ws = _make_uuid7(f"sr03_{sheet_name}_{text_field}_ws".encode())
    ws_val = "  شرکت معتبر با فاصله  "
    snap_ws = _make_snap_with_value(u_ws, ws_val)
    rep_ws = evaluate_source_requiredness(snap_ws)
    assert rep_ws.passes_requiredness is True
    assert rep_ws.issue_count == 0
    assert rep_ws.snapshot.all_rows_by_id[u_ws].raw_values[text_field] == ws_val

    # 3. None -> MISSING_VALUE
    u_none = _make_uuid7(f"sr03_{sheet_name}_{text_field}_n".encode())
    snap_none = _make_snap_with_value(u_none, None)
    rep_none = evaluate_source_requiredness(snap_none)
    assert rep_none.passes_requiredness is False
    assert rep_none.issue_count == 1
    assert rep_none.issues[0].field_name == text_field
    assert rep_none.issues[0].reason is SourceRequirednessIssueReason.MISSING_VALUE

    # 4. Empty string -> BLANK_TEXT
    u_empty = _make_uuid7(f"sr03_{sheet_name}_{text_field}_e".encode())
    snap_empty = _make_snap_with_value(u_empty, "")
    rep_empty = evaluate_source_requiredness(snap_empty)
    assert rep_empty.passes_requiredness is False
    assert rep_empty.issue_count == 1
    assert rep_empty.issues[0].field_name == text_field
    assert rep_empty.issues[0].reason is SourceRequirednessIssueReason.BLANK_TEXT

    # 5. ASCII whitespace -> BLANK_TEXT
    u_ascii = _make_uuid7(f"sr03_{sheet_name}_{text_field}_a".encode())
    snap_ascii = _make_snap_with_value(u_ascii, "   \t  \n  ")
    rep_ascii = evaluate_source_requiredness(snap_ascii)
    assert rep_ascii.passes_requiredness is False
    assert rep_ascii.issue_count == 1
    assert rep_ascii.issues[0].field_name == text_field
    assert rep_ascii.issues[0].reason is SourceRequirednessIssueReason.BLANK_TEXT

    # 6. Unicode whitespace -> BLANK_TEXT
    u_uni = _make_uuid7(f"sr03_{sheet_name}_{text_field}_u".encode())
    snap_uni = _make_snap_with_value(u_uni, "\u2003\u00a0\u3000")
    rep_uni = evaluate_source_requiredness(snap_uni)
    assert rep_uni.passes_requiredness is False
    assert rep_uni.issue_count == 1
    assert rep_uni.issues[0].field_name == text_field
    assert rep_uni.issues[0].reason is SourceRequirednessIssueReason.BLANK_TEXT


def test_sr03_omission_of_receipts_payments_blank_entry_type_is_detected() -> None:
    """SR-03: Prove omission of entry_type_raw blank checking causes failure."""
    u = _make_uuid7(b"sr03_blank_entry")
    row_data = _valid_receipts_payments_row()
    row_data["entry_type_raw"] = "   "  # Blank text in receipts_payments entry_type_raw
    snap = _build_snapshot_with_rows(receipts_payments_rows=[(u, row_data)])

    report = evaluate_source_requiredness(snap)
    assert report.passes_requiredness is False
    assert report.issue_count == 1
    assert report.issues[0].sheet_name == "دریافت-پرداخت"
    assert report.issues[0].field_name == "entry_type_raw"
    assert report.issues[0].reason is SourceRequirednessIssueReason.BLANK_TEXT


def test_sr03_date_presence_and_upstream_rejection() -> None:
    """SR-03: Null date yields MISSING_VALUE; non-null invalid date fails upstream."""
    # Null date is missing
    u_date_none = _make_uuid7(b"sr03_date_none")
    r_date_none = _valid_receipts_payments_row()
    r_date_none["date_raw"] = None
    snap_date_none = _build_snapshot_with_rows(
        receipts_payments_rows=[(u_date_none, r_date_none)]
    )
    rep_date_none = evaluate_source_requiredness(snap_date_none)
    assert len(rep_date_none.issues) == 1
    assert rep_date_none.issues[0].reason is SourceRequirednessIssueReason.MISSING_VALUE
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
    """SR-04: Numeric zero, negative numbers, and Decimal formats count as present.

    Cites upstream canonicalization test for Boolean, Float, and nonfinite rejection:
    - tests/test_canonical_hashing.py::test_number_canonicalization_and_type_rejections
    """
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
    u_bad_float = _make_uuid7(b"sr04_bad_float")
    r_bad_float = _valid_buy_sell_row()
    r_bad_float["quantity_raw"] = 10.5
    with pytest.raises(ContractError):
        _build_snapshot_with_rows(buy_sell_rows=[(u_bad_float, r_bad_float)])

    u_bad_bool = _make_uuid7(b"sr04_bad_bool")
    r_bad_bool = _valid_buy_sell_row()
    r_bad_bool["unit_price_toman_raw"] = True
    with pytest.raises(ContractError):
        _build_snapshot_with_rows(buy_sell_rows=[(u_bad_bool, r_bad_bool)])


# ---------------------------------------------------------------------------
# SR-05: Optional Fields Permitted Null and Blank
# ---------------------------------------------------------------------------


def test_sr05_optional_fields_null_and_blank_permitted() -> None:
    """SR-05: Optional fields null/blank; C/D/H/HA/HS and unresolved rows retained."""
    # 1. Individual optional null fields across all 4 sheets
    u_bs_disc = _make_uuid7(b"sr05_bs_disc")
    r_bs_disc = _valid_buy_sell_row()
    r_bs_disc["discount_toman_raw"] = None

    u_bs_notes = _make_uuid7(b"sr05_bs_notes")
    r_bs_notes = _valid_buy_sell_row()
    r_bs_notes["notes_raw"] = None

    u_bs_all_opt = _make_uuid7(b"sr05_bs_all_opt")
    r_bs_all_opt = _valid_buy_sell_row()
    r_bs_all_opt["discount_toman_raw"] = None
    r_bs_all_opt["notes_raw"] = None

    u_rp_notes = _make_uuid7(b"sr05_rp_notes")
    r_rp_notes = _valid_receipts_payments_row()
    r_rp_notes["notes_raw"] = None

    u_rp_acc = _make_uuid7(b"sr05_rp_acc")
    r_rp_acc = _valid_receipts_payments_row()
    r_rp_acc["account_code_raw"] = None

    u_rp_cust = _make_uuid7(b"sr05_rp_cust")
    r_rp_cust = _valid_receipts_payments_row()
    r_rp_cust["customer_flag_raw"] = None

    u_rp_all_opt = _make_uuid7(b"sr05_rp_all_opt")
    r_rp_all_opt = _valid_receipts_payments_row()
    r_rp_all_opt["notes_raw"] = None
    r_rp_all_opt["account_code_raw"] = None
    r_rp_all_opt["customer_flag_raw"] = None

    u_im_purity = _make_uuid7(b"sr05_im_purity")
    r_im_purity = _valid_inventory_movements_row()
    r_im_purity["purity_raw"] = None

    u_im_notes = _make_uuid7(b"sr05_im_notes")
    r_im_notes = _valid_inventory_movements_row()
    r_im_notes["notes_raw"] = None

    u_im_cust = _make_uuid7(b"sr05_im_cust")
    r_im_cust = _valid_inventory_movements_row()
    r_im_cust["customer_flag_raw"] = None

    u_im_all_opt = _make_uuid7(b"sr05_im_all_opt")
    r_im_all_opt = _valid_inventory_movements_row()
    r_im_all_opt["purity_raw"] = None
    r_im_all_opt["notes_raw"] = None
    r_im_all_opt["customer_flag_raw"] = None

    u_bp_phone = _make_uuid7(b"sr05_bp_phone")
    r_bp_phone = _valid_business_parties_row()
    r_bp_phone["phone_number_raw"] = None

    snap_opts = _build_snapshot_with_rows(
        buy_sell_rows=[
            (u_bs_disc, r_bs_disc),
            (u_bs_notes, r_bs_notes),
            (u_bs_all_opt, r_bs_all_opt),
        ],
        receipts_payments_rows=[
            (u_rp_notes, r_rp_notes),
            (u_rp_acc, r_rp_acc),
            (u_rp_cust, r_rp_cust),
            (u_rp_all_opt, r_rp_all_opt),
        ],
        inventory_movements_rows=[
            (u_im_purity, r_im_purity),
            (u_im_notes, r_im_notes),
            (u_im_cust, r_im_cust),
            (u_im_all_opt, r_im_all_opt),
        ],
        business_parties_rows=[(u_bp_phone, r_bp_phone)],
    )
    rep_opts = evaluate_source_requiredness(snap_opts)
    assert rep_opts.passes_requiredness is True
    assert rep_opts.issue_count == 0

    # 2. C/D/H/HA/HS with blank notes in دریافت-پرداخت
    cd_rows = []
    for code in ("C", "D", "H", "HA", "HS"):
        u_empty = _make_uuid7(f"sr05_code_{code}_empty".encode())
        r_empty = _valid_receipts_payments_row()
        r_empty["entry_type_raw"] = code
        r_empty["notes_raw"] = ""
        cd_rows.append((u_empty, r_empty))

        u_spaces = _make_uuid7(f"sr05_code_{code}_spaces".encode())
        r_spaces = _valid_receipts_payments_row()
        r_spaces["entry_type_raw"] = code
        r_spaces["notes_raw"] = "   \t\n "
        cd_rows.append((u_spaces, r_spaces))

    snap_cd = _build_snapshot_with_rows(receipts_payments_rows=cd_rows)
    rep_cd = evaluate_source_requiredness(snap_cd)
    assert rep_cd.passes_requiredness is True
    assert rep_cd.checked_row_count == 10
    assert rep_cd.issue_count == 0

    # 3. Nonblank unknown names/items/codes and RS rows preserved
    u_unknown = _make_uuid7(b"sr05_unknown_fields")
    r_unknown_bs = _valid_buy_sell_row()
    r_unknown_bs["party_name_raw"] = "شخص_ناشناخته_در_مستر_۱"
    r_unknown_bs["item_name_raw"] = "کالای_ناشناخته_در_مستر_۲"
    r_unknown_bs["transaction_type_raw"] = "کد_نامشخص_۳"

    u_rs = _make_uuid7(b"sr05_unpaired_rs")
    r_rs = _valid_receipts_payments_row()
    r_rs["entry_type_raw"] = "RS"

    snap_unres = _build_snapshot_with_rows(
        buy_sell_rows=[(u_unknown, r_unknown_bs)],
        receipts_payments_rows=[(u_rs, r_rs)],
    )
    rep_unres = evaluate_source_requiredness(snap_unres)
    assert rep_unres.passes_requiredness is True
    assert rep_unres.issue_count == 0
    assert (
        rep_unres.snapshot.all_rows_by_id[u_unknown].raw_values["party_name_raw"]
        == "شخص_ناشناخته_در_مستر_۱"
    )
    assert rep_unres.snapshot.all_rows_by_id[u_rs].raw_values["entry_type_raw"] == "RS"


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
    """SR-07: Four present empty sheets produce passes_requiredness=True.

    Cites upstream structural rejection tests:
    - tests/test_source_change_plan.py::test_full_snapshot_duplicate_sheet_rejected
    - tests/test_source_change_plan.py::test_full_snapshot_unknown_sheet_rejected
    - tests/test_source_change_plan.py::test_invalid_uuid_rejected_in_row_input
    """
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
    ]
    with pytest.raises(ContractError):
        build_source_workbook_snapshot(sheets_incomplete)


# ---------------------------------------------------------------------------
# SR-08: Constructor Invariants, Immutability, and Tamper Resistance
# ---------------------------------------------------------------------------


def test_sr08_constructor_invariants_immutability_and_tamper_resistance() -> None:
    """SR-08: Validate constructor invariants, immutability, and reason types."""
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
    assert iss.reason is SourceRequirednessIssueReason.MISSING_VALUE

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

    # R1: Four invalid reason type cases
    class ForeignReason(StrEnum):
        MISSING_VALUE = "missing_value"
        BLANK_TEXT = "blank_text"

    # 1. Canonical string "missing_value"
    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessIssue(
            sheet_name="خرید-فروش",
            stable_id=u,
            field_name="date_raw",
            reason="missing_value",  # type: ignore[arg-type]
        )

    # 2. Canonical string "blank_text"
    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessIssue(
            sheet_name="خرید-فروش",
            stable_id=u,
            field_name="date_raw",
            reason="blank_text",  # type: ignore[arg-type]
        )

    # 3. Foreign StrEnum member "missing_value"
    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessIssue(
            sheet_name="خرید-فروش",
            stable_id=u,
            field_name="date_raw",
            reason=ForeignReason.MISSING_VALUE,  # type: ignore[arg-type]
        )

    # 4. Foreign StrEnum member "blank_text"
    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessIssue(
            sheet_name="خرید-فروش",
            stable_id=u,
            field_name="date_raw",
            reason=ForeignReason.BLANK_TEXT,  # type: ignore[arg-type]
        )

    # Genuine member identity retention
    iss_mv = SourceRequirednessIssue(
        sheet_name="خرید-فروش",
        stable_id=u,
        field_name="date_raw",
        reason=SourceRequirednessIssueReason.MISSING_VALUE,
    )
    assert iss_mv.reason is SourceRequirednessIssueReason.MISSING_VALUE

    iss_bt = SourceRequirednessIssue(
        sheet_name="خرید-فروش",
        stable_id=u,
        field_name="party_name_raw",
        reason=SourceRequirednessIssueReason.BLANK_TEXT,
    )
    assert iss_bt.reason is SourceRequirednessIssueReason.BLANK_TEXT

    # BLANK_TEXT on non-text field
    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessIssue(
            sheet_name="خرید-فروش",
            stable_id=u,
            field_name="quantity_raw",  # quantity_raw is DECIMAL, not RAW_TEXT!
            reason=SourceRequirednessIssueReason.BLANK_TEXT,
        )

    # Report direct construction on an ACTUALLY FAILING snapshot
    u_fail = _make_uuid7(b"sr08_failing_row")
    r_fail = _valid_buy_sell_row()
    r_fail["date_raw"] = None
    r_fail["party_name_raw"] = "   "
    failing_snap = _build_snapshot_with_rows(buy_sell_rows=[(u_fail, r_fail)])

    # Construct directly
    failing_report = SourceRequirednessReport(failing_snap)
    assert failing_report.passes_requiredness is False
    assert failing_report.checked_row_count == 1
    assert failing_report.failed_row_count == 1
    assert failing_report.issue_count == 2
    assert len(failing_report.issues) == 2
    assert failing_report.issues[0].field_name == "date_raw"
    assert (
        failing_report.issues[0].reason is SourceRequirednessIssueReason.MISSING_VALUE
    )
    assert failing_report.issues[1].field_name == "party_name_raw"
    assert failing_report.issues[1].reason is SourceRequirednessIssueReason.BLANK_TEXT

    # Report immutability
    with pytest.raises((FrozenInstanceError, AttributeError)):
        failing_report.passes_requiredness = True  # type: ignore[misc]

    # Reject attempts to inject passing flags, omit issues, or supply fabricated counts
    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessReport(failing_snap, passes_requiredness=True)

    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessReport(failing_snap, issues=())

    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessReport(failing_snap, checked_row_count=0)

    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessReport(failing_snap, failed_row_count=0)

    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessReport(failing_snap, issue_count=0)

    with pytest.raises(SourceRequirednessInputError):
        SourceRequirednessReport(failing_snap, "unsupported_positional_arg")


# ---------------------------------------------------------------------------
# SR-09: Error Messages and Repr Masking Without Raw Leakage
# ---------------------------------------------------------------------------


def test_sr09_error_messages_and_repr_masking_without_raw_leakage() -> None:
    """SR-09: Error messages and repr must not reveal raw cell values or notes."""
    secret_marker = "SECRET_CREDENTIAL_DATA_007"

    # 1. Invalid supplied snapshot arguments to both public entry points
    for bad_snap in (None, "not_a_snapshot", 12345, {"sheet": "fake"}):
        with pytest.raises(SourceRequirednessInputError) as exc1:
            evaluate_source_requiredness(bad_snap)  # type: ignore[arg-type]
        assert str(bad_snap) not in str(exc1.value)

        with pytest.raises(SourceRequirednessInputError) as exc2:
            SourceRequirednessReport(bad_snap)  # type: ignore[arg-type]
        assert str(bad_snap) not in str(exc2.value)

    # 2. Ordinary signature errors remain TypeError
    with pytest.raises(TypeError):
        evaluate_source_requiredness()  # type: ignore[call-arg]

    with pytest.raises(TypeError):
        SourceRequirednessReport()  # type: ignore[call-arg]

    # 3. Invalid Issue metadata with marker-bearing values
    with pytest.raises(SourceRequirednessInputError) as exc_sheet:
        SourceRequirednessIssue(
            sheet_name=f"خرید-فروش_{secret_marker}",
            stable_id=_make_uuid7(b"sr09_id"),
            field_name="date_raw",
            reason=SourceRequirednessIssueReason.MISSING_VALUE,
        )
    assert secret_marker not in str(exc_sheet.value)

    with pytest.raises(SourceRequirednessInputError) as exc_field:
        SourceRequirednessIssue(
            sheet_name="خرید-فروش",
            stable_id=_make_uuid7(b"sr09_id"),
            field_name=f"field_{secret_marker}",
            reason=SourceRequirednessIssueReason.MISSING_VALUE,
        )
    assert secret_marker not in str(exc_field.value)

    # 4. Snapshot with a REAL ISSUE and synthetic markers in names/notes/contact
    u = _make_uuid7(b"sr09_secret")
    r_bs = _valid_buy_sell_row()
    r_bs["notes_raw"] = f"Note_{secret_marker}"
    r_bs["party_name_raw"] = f"Party_{secret_marker}"
    r_bs["unit_price_toman_raw"] = None  # REAL ISSUE!

    u_bp = _make_uuid7(b"sr09_secret_bp")
    r_bp = _valid_business_parties_row()
    r_bp["phone_number_raw"] = f"Phone_{secret_marker}"

    snap = _build_snapshot_with_rows(
        buy_sell_rows=[(u, r_bs)],
        business_parties_rows=[(u_bp, r_bp)],
    )

    report = evaluate_source_requiredness(snap)
    assert len(report.issues) > 0  # Assert non-empty issues tuple!
    assert report.passes_requiredness is False

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


def test_sr10_purity_repeatability_and_raw_preservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SR-10: Repeated evaluation gives identical issues without seams."""
    u1 = _make_uuid7(b"sr10_u1")
    r1 = _valid_buy_sell_row()
    r1["date_raw"] = None

    # 1. Build fixtures BEFORE applying the side-effect guard
    snap = _build_snapshot_with_rows(buy_sell_rows=[(u1, r1)])

    orig_row = snap.all_rows_by_id[u1]
    orig_hash = orig_row.source_hash
    orig_raw_vals = dict(orig_row.raw_values)

    # 2. Forbid filesystem I/O, network, clock, and UUID generation during evaluation
    def _forbidden_call(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "Forbidden side-effect invoked during pure requiredness evaluation!"
        )

    monkeypatch.setattr(time, "time", _forbidden_call)
    monkeypatch.setattr(time, "monotonic", _forbidden_call)
    monkeypatch.setattr(time, "monotonic_ns", _forbidden_call)
    monkeypatch.setattr(uuid, "uuid4", _forbidden_call)
    if hasattr(uuid, "uuid7"):
        monkeypatch.setattr(uuid, "uuid7", _forbidden_call)
    monkeypatch.setattr(socket, "socket", _forbidden_call)
    monkeypatch.setattr(builtins, "open", _forbidden_call)
    monkeypatch.setattr(Path, "read_bytes", _forbidden_call)
    monkeypatch.setattr(Path, "write_bytes", _forbidden_call)
    monkeypatch.setattr(Path, "read_text", _forbidden_call)
    monkeypatch.setattr(Path, "write_text", _forbidden_call)

    # 3. Evaluate and directly construct reports under the guard
    rep1 = evaluate_source_requiredness(snap)
    rep2 = SourceRequirednessReport(snap)

    assert rep1.issues == rep2.issues
    assert rep1.checked_row_count == rep2.checked_row_count == 1
    assert rep1.failed_row_count == rep2.failed_row_count == 1
    assert rep1.issue_count == rep2.issue_count == 1
    assert rep1.passes_requiredness == rep2.passes_requiredness is False

    # Assert exact identities and raw preservation
    after_row1 = rep1.snapshot.all_rows_by_id[u1]
    after_row2 = rep2.snapshot.all_rows_by_id[u1]
    assert after_row1 is orig_row
    assert after_row2 is orig_row
    assert after_row1.raw_values is orig_row.raw_values
    assert after_row1.source_hash == orig_hash
    assert dict(after_row1.raw_values) == orig_raw_vals


# ---------------------------------------------------------------------------
# SR-11: XLSX Reader Integration with Synthetic Workbooks
# ---------------------------------------------------------------------------


def test_sr11_xlsx_reader_integration_synthetic_workbooks(
    tmp_path: Any,
) -> None:
    """SR-11: Synthetic XLSX -> Reader -> Preflight with missing & formula exclusion.

    Cites existing accepted Reader failure/preservation tests:
    - tests/test_xlsx_source_reader.py::test_r6_all_or_nothing_fourth_sheet_late_failure
    - tests/test_xlsx_source_reader.py::test_r6_read_only_integrity_and_clean_cleanup
    """
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

    # Assert read-only byte preservation across Reader -> Preflight
    orig_bytes_gen1 = wb_path.read_bytes()
    read_res = read_xlsx_source_snapshot(wb_path)
    assert isinstance(read_res.snapshot, ValidatedSourceWorkbookSnapshot)

    report = evaluate_source_requiredness(read_res.snapshot)
    assert wb_path.read_bytes() == orig_bytes_gen1  # Read-only bytes unchanged!

    assert report.checked_row_count == 7
    assert report.failed_row_count == 3
    assert report.issue_count == 3
    assert report.passes_requiredness is False

    issues_by_id = {iss.stable_id: iss for iss in report.issues}
    assert issues_by_id[u2].field_name == "date_raw"
    assert issues_by_id[u2].reason is SourceRequirednessIssueReason.MISSING_VALUE

    assert issues_by_id[u3].field_name == "party_name_raw"
    assert issues_by_id[u3].reason is SourceRequirednessIssueReason.BLANK_TEXT

    assert issues_by_id[u4].field_name == "unit_price_toman_raw"
    assert issues_by_id[u4].reason is SourceRequirednessIssueReason.MISSING_VALUE

    # Generation 2: Changing only derived formula/cache column I (total_amount)
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

    orig_bytes_gen2 = wb_mod_path.read_bytes()
    read_res_mod = read_xlsx_source_snapshot(wb_mod_path)
    assert isinstance(read_res_mod.snapshot, ValidatedSourceWorkbookSnapshot)

    report_mod = evaluate_source_requiredness(read_res_mod.snapshot)
    assert wb_mod_path.read_bytes() == orig_bytes_gen2  # Read-only bytes unchanged!

    assert report_mod.issues == report.issues
    assert report_mod.checked_row_count == report.checked_row_count
    assert report_mod.failed_row_count == report.failed_row_count
    assert (
        read_res_mod.snapshot.all_rows_by_id[u1].source_hash
        == read_res.snapshot.all_rows_by_id[u1].source_hash
    )


# ---------------------------------------------------------------------------
# SR-12: Independent Property Oracle Under Permutations & Transitions
# ---------------------------------------------------------------------------


def _independent_oracle(
    sheet_rows_map: dict[str, list[tuple[uuid.UUID, dict[str, Any]]]],
) -> tuple[tuple[tuple[str, uuid.UUID, str, str], ...], int, int, int, bool]:
    """Independent oracle deriving expected issues directly from input specs."""
    approved_sheets = (
        "خرید-فروش",
        "دریافت-پرداخت",
        "ورود-خروج",
        "لیست کسبه",
    )
    issues: list[tuple[str, uuid.UUID, str, str]] = []
    failing_rows: set[uuid.UUID] = set()
    total_rows = sum(len(rows) for rows in sheet_rows_map.values())

    for s_name in approved_sheets:
        rows = sheet_rows_map.get(s_name, [])
        # In snapshot, rows are sorted by UUID bytes
        sorted_rows = sorted(rows, key=lambda pair: pair[0].bytes)
        req_fields = INDEPENDENT_APPROVED_REQUIRED_FIELDS[s_name]
        text_fields = INDEPENDENT_TEXT_FIELDS[s_name]

        for u, raw_vals in sorted_rows:
            has_issue = False
            for f_name in req_fields:
                val = raw_vals.get(f_name)
                if val is None:
                    issues.append((s_name, u, f_name, "missing_value"))
                    has_issue = True
                elif (
                    f_name in text_fields and isinstance(val, str) and val.strip() == ""
                ):
                    issues.append((s_name, u, f_name, "blank_text"))
                    has_issue = True
            if has_issue:
                failing_rows.add(u)

    issues_tuple = tuple(issues)
    passes = len(issues_tuple) == 0
    return (
        issues_tuple,
        total_rows,
        len(failing_rows),
        len(issues_tuple),
        passes,
    )


def test_sr12_property_all_four_sheets_permutations_and_independent_oracle() -> None:
    """SR-12: Four-sheet inputs, inverted UUID byte order, and bounded permutations."""
    # 1. Generate 4-sheet rows with input order inverted from UUID byte order
    u_bs_high = uuid.UUID("01955f00-0000-7000-8000-000000000009")
    u_bs_low = uuid.UUID("01955f00-0000-7000-8000-000000000001")

    r_bs_high: dict[str, Any] = {
        "notes_raw": "یادداشت فاکتور",
        "unit_price_toman_raw": "2000000",
        "discount_toman_raw": "0",
        "date_raw": "1403/01/02",
        "party_name_raw": "   ",  # Blank text issue
        "transaction_type_raw": "فروش",
        "item_name_raw": "سکه",
        "quantity_raw": "5",
    }
    r_bs_low: dict[str, Any] = {
        "notes_raw": "توضیحات",
        "unit_price_toman_raw": None,  # Missing value issue
        "quantity_raw": "10",
        "item_name_raw": "طلا",
        "transaction_type_raw": "خرید",
        "party_name_raw": "علی",
        "discount_toman_raw": "0",
        "date_raw": "1403/01/01",
    }
    bs_rows: list[tuple[uuid.UUID, dict[str, Any]]] = [
        (u_bs_high, r_bs_high),
        (u_bs_low, r_bs_low),
    ]

    u_rp_high = uuid.UUID("01955f00-0000-7000-8000-000000000008")
    u_rp_low = uuid.UUID("01955f00-0000-7000-8000-000000000002")
    r_rp_high: dict[str, Any] = {
        "customer_flag_raw": None,
        "account_code_raw": "10",
        "notes_raw": None,
        "amount_toman_raw": "5000",
        "entry_type_raw": "   ",  # Blank entry_type issue
        "party_name_raw": "رضا",
        "date_raw": "1403/01/03",
    }
    r_rp_low: dict[str, Any] = _valid_receipts_payments_row()
    rp_rows: list[tuple[uuid.UUID, dict[str, Any]]] = [
        (u_rp_high, r_rp_high),
        (u_rp_low, r_rp_low),
    ]

    u_im_high = uuid.UUID("01955f00-0000-7000-8000-000000000007")
    u_im_low = uuid.UUID("01955f00-0000-7000-8000-000000000003")
    r_im_high: dict[str, Any] = _valid_inventory_movements_row()
    r_im_low: dict[str, Any] = {
        "purity_raw": "750",
        "notes_raw": None,
        "customer_flag_raw": "1",
        "quantity_raw": "50",
        "item_name_raw": "   ",  # Blank item_name issue
        "movement_type_raw": "ورود",
        "party_name_raw": "زرگری",
        "date_raw": "1403/01/04",
    }
    im_rows: list[tuple[uuid.UUID, dict[str, Any]]] = [
        (u_im_high, r_im_high),
        (u_im_low, r_im_low),
    ]

    u_bp_high = uuid.UUID("01955f00-0000-7000-8000-000000000006")
    u_bp_low = uuid.UUID("01955f00-0000-7000-8000-000000000004")
    r_bp_high: dict[str, Any] = {
        "phone_number_raw": None,
        "party_name_raw": None,  # Missing party_name issue
    }
    r_bp_low: dict[str, Any] = _valid_business_parties_row()
    bp_rows: list[tuple[uuid.UUID, dict[str, Any]]] = [
        (u_bp_high, r_bp_high),
        (u_bp_low, r_bp_low),
    ]

    sheet_specs: dict[str, list[tuple[uuid.UUID, dict[str, Any]]]] = {
        "خرید-فروش": bs_rows,
        "دریافت-پرداخت": rp_rows,
        "ورود-خروج": im_rows,
        "لیست کسبه": bp_rows,
    }

    # Oracle expectation derived directly from input specs
    oracle_issues, oracle_checked, oracle_failed, oracle_count, oracle_passes = (
        _independent_oracle(sheet_specs)
    )

    # 2. Test multiple actual permutations of sheet order
    permutations = [
        # Reversed order
        [
            SourceSheetInput(sheet_name="لیست کسبه", rows=bp_rows),
            SourceSheetInput(sheet_name="ورود-خروج", rows=im_rows),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=rp_rows),
            SourceSheetInput(sheet_name="خرید-فروش", rows=bs_rows),
        ],
        # Rotated order
        [
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=rp_rows),
            SourceSheetInput(sheet_name="لیست کسبه", rows=bp_rows),
            SourceSheetInput(sheet_name="خرید-فروش", rows=bs_rows),
            SourceSheetInput(sheet_name="ورود-خروج", rows=im_rows),
        ],
        # Another permutation
        [
            SourceSheetInput(sheet_name="ورود-خروج", rows=im_rows),
            SourceSheetInput(sheet_name="خرید-فروش", rows=bs_rows),
            SourceSheetInput(sheet_name="لیست کسبه", rows=bp_rows),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=rp_rows),
        ],
    ]

    for p_inputs in permutations:
        snap = build_source_workbook_snapshot(p_inputs)
        report = evaluate_source_requiredness(snap)

        assert report.checked_row_count == oracle_checked == 8
        assert report.failed_row_count == oracle_failed == 5
        assert report.issue_count == oracle_count == 5
        assert report.passes_requiredness == oracle_passes is False

        prod_issue_tuples = tuple(
            (iss.sheet_name, iss.stable_id, iss.field_name, iss.reason.value)
            for iss in report.issues
        )
        assert prod_issue_tuples == oracle_issues


def test_sr12_property_single_value_mutation_transitions() -> None:
    """SR-12: Mutating one required value changes only that issue."""
    u = _make_uuid7(b"sr12_mutation_target")

    # State 1: Present (valid nonblank text)
    r_present = _valid_buy_sell_row()
    r_present["party_name_raw"] = "  شرکت نمونه معتبر  "  # nonblank surrounding ws
    snap1 = _build_snapshot_with_rows(buy_sell_rows=[(u, r_present)])
    rep1 = evaluate_source_requiredness(snap1)
    assert rep1.passes_requiredness is True
    assert rep1.issue_count == 0

    # State 2: None (missing)
    r_none = dict(r_present)
    r_none["party_name_raw"] = None
    snap2 = _build_snapshot_with_rows(buy_sell_rows=[(u, r_none)])
    rep2 = evaluate_source_requiredness(snap2)
    assert rep2.passes_requiredness is False
    assert rep2.issue_count == 1
    assert rep2.issues[0].field_name == "party_name_raw"
    assert rep2.issues[0].reason is SourceRequirednessIssueReason.MISSING_VALUE

    # State 3: Blank text ("   \t")
    r_blank = dict(r_present)
    r_blank["party_name_raw"] = "   \t"
    snap3 = _build_snapshot_with_rows(buy_sell_rows=[(u, r_blank)])
    rep3 = evaluate_source_requiredness(snap3)
    assert rep3.passes_requiredness is False
    assert rep3.issue_count == 1
    assert rep3.issues[0].field_name == "party_name_raw"
    assert rep3.issues[0].reason is SourceRequirednessIssueReason.BLANK_TEXT


def test_sr12_property_exact_snapshot_raw_and_hash_retention() -> None:
    """SR-12: Snapshot, raw values, UUID, zero/signed values, and hashes retained."""
    u1 = _make_uuid7(b"sr12_ret_u1")
    r1 = _valid_buy_sell_row()
    r1["unit_price_toman_raw"] = 0  # Numeric zero
    r1["quantity_raw"] = Decimal("0")  # Decimal zero
    r1["party_name_raw"] = "  شرکت بازرگانی پارس  "  # Surrounding whitespace

    u2 = _make_uuid7(b"sr12_ret_u2")
    r2 = _valid_receipts_payments_row()
    r2["amount_toman_raw"] = "-1500000"  # Signed value

    snap = _build_snapshot_with_rows(
        buy_sell_rows=[(u1, r1)],
        receipts_payments_rows=[(u2, r2)],
    )

    report = evaluate_source_requiredness(snap)
    assert report.passes_requiredness is True
    assert report.snapshot is snap

    row1 = report.snapshot.all_rows_by_id[u1]
    assert row1.raw_values["unit_price_toman_raw"] == 0
    assert row1.raw_values["quantity_raw"] == Decimal("0")
    assert row1.raw_values["party_name_raw"] == "  شرکت بازرگانی پارس  "
    assert row1.source_hash == snap.all_rows_by_id[u1].source_hash

    row2 = report.snapshot.all_rows_by_id[u2]
    assert row2.raw_values["amount_toman_raw"] == "-1500000"
    assert row2.source_hash == snap.all_rows_by_id[u2].source_hash


@given(
    st.tuples(
        st.lists(
            st.tuples(
                st.sampled_from(["present", "none", "blank"]),
                st.sampled_from(["present", "none"]),
            ),
            min_size=1,
            max_size=3,
        ),
        st.lists(
            st.tuples(
                st.sampled_from(["present", "none", "blank"]),
                st.sampled_from(["present", "none"]),
            ),
            min_size=1,
            max_size=3,
        ),
        st.lists(
            st.tuples(
                st.sampled_from(["present", "none", "blank"]),
                st.sampled_from(["present", "none"]),
            ),
            min_size=1,
            max_size=3,
        ),
        st.lists(
            st.sampled_from(["present", "none", "blank"]),
            min_size=1,
            max_size=3,
        ),
    )
)
@settings(max_examples=15, deadline=None)
def test_sr12_hypothesis_randomized_presence_combinations(
    specs_tuple: tuple[
        list[tuple[str, str]],
        list[tuple[str, str]],
        list[tuple[str, str]],
        list[str],
    ],
) -> None:
    """SR-12: Hypothesis property test verifying oracle across all four sheets."""
    bs_specs, rp_specs, im_specs, bp_specs = specs_tuple

    bs_rows: list[tuple[uuid.UUID, dict[str, Any]]] = []
    for idx, (party_mode, price_mode) in enumerate(bs_specs):
        u = _make_uuid7(f"hypo_bs_{idx}".encode())
        data = _valid_buy_sell_row()
        if party_mode == "none":
            data["party_name_raw"] = None
        elif party_mode == "blank":
            data["party_name_raw"] = "   "
        if price_mode == "none":
            data["unit_price_toman_raw"] = None
        bs_rows.append((u, data))

    rp_rows: list[tuple[uuid.UUID, dict[str, Any]]] = []
    for idx, (party_mode, entry_mode) in enumerate(rp_specs):
        u = _make_uuid7(f"hypo_rp_{idx}".encode())
        data = _valid_receipts_payments_row()
        if party_mode == "none":
            data["party_name_raw"] = None
        elif party_mode == "blank":
            data["party_name_raw"] = "   "
        if entry_mode == "none":
            data["entry_type_raw"] = None
        elif entry_mode == "blank":
            data["entry_type_raw"] = "   "
        rp_rows.append((u, data))

    im_rows: list[tuple[uuid.UUID, dict[str, Any]]] = []
    for idx, (item_mode, qty_mode) in enumerate(im_specs):
        u = _make_uuid7(f"hypo_im_{idx}".encode())
        data = _valid_inventory_movements_row()
        if item_mode == "none":
            data["item_name_raw"] = None
        elif item_mode == "blank":
            data["item_name_raw"] = "   "
        if qty_mode == "none":
            data["quantity_raw"] = None
        im_rows.append((u, data))

    bp_rows: list[tuple[uuid.UUID, dict[str, Any]]] = []
    for idx, party_mode in enumerate(bp_specs):
        u = _make_uuid7(f"hypo_bp_{idx}".encode())
        data = _valid_business_parties_row()
        if party_mode == "none":
            data["party_name_raw"] = None
        elif party_mode == "blank":
            data["party_name_raw"] = "   "
        bp_rows.append((u, data))

    sheet_specs = {
        "خرید-فروش": bs_rows,
        "دریافت-پرداخت": rp_rows,
        "ورود-خروج": im_rows,
        "لیست کسبه": bp_rows,
    }

    snap = _build_snapshot_with_rows(
        buy_sell_rows=bs_rows,
        receipts_payments_rows=rp_rows,
        inventory_movements_rows=im_rows,
        business_parties_rows=bp_rows,
    )
    report = evaluate_source_requiredness(snap)

    oracle_issues, oracle_checked, oracle_failed, oracle_count, oracle_passes = (
        _independent_oracle(sheet_specs)
    )

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
    total_rows = 15000
    rows_per_sheet = total_rows // 4  # 3,750 per sheet

    t_fixture_start = time.perf_counter()

    # Sheet 1: خرید-فروش (1,000 missing unit_price, 2,750 valid)
    bs_rows: list[tuple[uuid.UUID, dict[str, Any]]] = []
    for i in range(rows_per_sheet):
        u = _make_uuid7(f"sr13_s1_r{i:05d}".encode())
        data = _valid_buy_sell_row()
        if i < 1000:
            data["unit_price_toman_raw"] = None
        bs_rows.append((u, data))

    # Sheet 2: دریافت-پرداخت (500 missing party_name, 500 blank party_name, 2,750 valid)
    rp_rows: list[tuple[uuid.UUID, dict[str, Any]]] = []
    for i in range(rows_per_sheet):
        u = _make_uuid7(f"sr13_s2_r{i:05d}".encode())
        data = _valid_receipts_payments_row()
        if i < 500:
            data["party_name_raw"] = None
        elif i < 1000:
            data["party_name_raw"] = "   "
        rp_rows.append((u, data))

    # Sheet 3: ورود-خروج (500 missing item_name, 500 blank item_name, 2,750 valid)
    im_rows: list[tuple[uuid.UUID, dict[str, Any]]] = []
    for i in range(rows_per_sheet):
        u = _make_uuid7(f"sr13_s3_r{i:05d}".encode())
        data = _valid_inventory_movements_row()
        if i < 500:
            data["item_name_raw"] = None
        elif i < 1000:
            data["item_name_raw"] = "   "
        im_rows.append((u, data))

    # Sheet 4: لیست کسبه (3,750 valid)
    bp_rows: list[tuple[uuid.UUID, dict[str, Any]]] = []
    for i in range(rows_per_sheet):
        u = _make_uuid7(f"sr13_s4_r{i:05d}".encode())
        data = _valid_business_parties_row()
        bp_rows.append((u, data))

    # Derive full independently expected ordered tuple directly from input specs
    expected_issues_list: list[tuple[str, uuid.UUID, str, str]] = []

    # Sheet 1 expected issues in sorted UUID byte order
    bs_sorted = sorted(bs_rows, key=lambda pair: pair[0].bytes)
    for u, row in bs_sorted:
        if row["unit_price_toman_raw"] is None:
            expected_issues_list.append(
                ("خرید-فروش", u, "unit_price_toman_raw", "missing_value")
            )

    # Sheet 2 expected issues in sorted UUID byte order
    rp_sorted = sorted(rp_rows, key=lambda pair: pair[0].bytes)
    for u, row in rp_sorted:
        if row["party_name_raw"] is None:
            expected_issues_list.append(
                ("دریافت-پرداخت", u, "party_name_raw", "missing_value")
            )
        elif row["party_name_raw"] == "   ":
            expected_issues_list.append(
                ("دریافت-پرداخت", u, "party_name_raw", "blank_text")
            )

    # Sheet 3 expected issues in sorted UUID byte order
    im_sorted = sorted(im_rows, key=lambda pair: pair[0].bytes)
    for u, row in im_sorted:
        if row["item_name_raw"] is None:
            expected_issues_list.append(
                ("ورود-خروج", u, "item_name_raw", "missing_value")
            )
        elif row["item_name_raw"] == "   ":
            expected_issues_list.append(("ورود-خروج", u, "item_name_raw", "blank_text"))

    expected_issues = tuple(expected_issues_list)
    assert len(expected_issues) == 3000

    snap = _build_snapshot_with_rows(
        buy_sell_rows=bs_rows,
        receipts_payments_rows=rp_rows,
        inventory_movements_rows=im_rows,
        business_parties_rows=bp_rows,
    )
    t_fixture_duration = time.perf_counter() - t_fixture_start
    assert snap.total_row_count == 15000

    # Pre-capture row references, raw mappings, and source hashes BEFORE evaluation
    pre_captured_rows = {
        u: (row, row.raw_values, row.source_hash)
        for u, row in snap.all_rows_by_id.items()
    }
    assert len(pre_captured_rows) == 15000

    # Measure pure requiredness evaluation time separately from fixture generation
    t_eval_start = time.perf_counter()
    report = evaluate_source_requiredness(snap)
    t_eval_duration = time.perf_counter() - t_eval_start

    print(
        f"\n[SR-13 SCALE BENCHMARK] 15,000 rows -> "
        f"eval_seconds: {t_eval_duration:.4f}s | "
        f"fixture_build_seconds: {t_fixture_duration:.4f}s | "
        f"checked_rows: {report.checked_row_count} | "
        f"failed_rows: {report.failed_row_count} | "
        f"issues: {report.issue_count}"
    )

    # Compare every emitted issue against independently expected ordered tuple
    actual_issues = tuple(
        (iss.sheet_name, iss.stable_id, iss.field_name, iss.reason.value)
        for iss in report.issues
    )
    assert actual_issues == expected_issues

    assert report.checked_row_count == 15000
    assert report.failed_row_count == 3000
    assert report.issue_count == 3000
    assert report.passes_requiredness is False
    assert report.snapshot is snap

    # Verify exact row object, raw mapping, and hash retention across 15k rows
    assert len(report.snapshot.all_rows_by_id) == 15000
    for u, (orig_row, orig_raw, orig_hash) in pre_captured_rows.items():
        after_row = report.snapshot.all_rows_by_id[u]
        assert after_row is orig_row
        assert after_row.raw_values is orig_raw
        assert after_row.source_hash == orig_hash


# ---------------------------------------------------------------------------
# SR-14: Existing Suite Retention & Contracts Integrity
# ---------------------------------------------------------------------------


def test_sr14_existing_suite_retention_and_contracts_integrity() -> None:
    """SR-14: Existing contracts registry, version constants, and sheets untouched."""
    assert len(RAW_CONTRACT_REGISTRY.sheets) == 4
    expected_sheets = ("خرید-فروش", "دریافت-پرداخت", "ورود-خروج", "لیست کسبه")
    assert tuple(RAW_CONTRACT_REGISTRY.sheets.keys()) == expected_sheets
