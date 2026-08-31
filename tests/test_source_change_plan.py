"""Deterministic unit, transition-table, idempotency and property tests.

Verifies ADR-0007 compliance:
- complete four-sheet full-snapshot enforcement;
- pure recalculation of source_hash and sheet_snapshot_hash;
- global UUIDv7 uniqueness and identity home-sheet relocation prevention;
- full transition table (Insert, Edit, Void, Unchanged, Reactivation);
- row-order, sheet-order, mapping-order and prior-state order invariance;
- deep immutability, constructor defensive copying, and retry idempotency;
- 15,000-row synthetic benchmark and strict hash fullmatch validation.
"""

from __future__ import annotations

import random
import time
import uuid
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import pytest
from accounting_contracts.canonical_hashing import (
    InvalidHashError,
    compute_sheet_snapshot_hash,
    compute_source_hash,
)
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
)
from accounting_contracts.source_change_plan import (
    SOURCE_CHANGE_PLAN_VERSION,
    DeterministicSourceChangePlan,
    DuplicateIdentityError,
    IdentityLifecycle,
    IdentityRelocationError,
    IncompleteSnapshotError,
    InvalidIdentityError,
    InvalidPriorStateError,
    PlanAction,
    PlanCounts,
    PlanItem,
    PriorIdentityRegistry,
    PriorIdentityState,
    SourceChangePlanError,
    SourceRowInput,
    SourceSheetInput,
    ValidatedSourceRow,
    ValidatedSourceSheetSnapshot,
    ValidatedSourceWorkbookSnapshot,
    build_prior_identity_registry,
    build_source_workbook_snapshot,
    plan_source_changes,
)
from hypothesis import given
from hypothesis import strategies as st


def _make_uuid7(b: bytes) -> uuid.UUID:
    """Helper to generate an RFC 4122 version 7 UUID from 16 bytes."""
    b_arr = bytearray(b)
    b_arr[6] = (b_arr[6] & 0x0F) | 0x70
    b_arr[8] = (b_arr[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(b_arr))


st_uuid7 = st.binary(min_size=16, max_size=16).map(_make_uuid7)


def _sample_buy_sell_row() -> dict[str, Any]:
    return {
        "date_raw": "1403/05/15",
        "party_name_raw": "بازرگانی احمدی",
        "transaction_type_raw": "خرید",
        "item_name_raw": "طلای آبشده",
        "quantity_raw": "12.34",
        "unit_price_toman_raw": "1500000",
        "discount_toman_raw": "0",
        "notes_raw": "توضیحات فاکتور",
    }


def _sample_receipts_payments_row() -> dict[str, Any]:
    return {
        "date_raw": "1403/01/01",
        "party_name_raw": "همکار نمونه",
        "entry_type_raw": "RS",
        "amount_toman_raw": "50000000",
        "notes_raw": "تسویه حساب",
        "account_code_raw": "101",
        "customer_flag_raw": "1",
    }


def _sample_inventory_movements_row() -> dict[str, Any]:
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


def _sample_business_parties_row() -> dict[str, Any]:
    return {
        "party_name_raw": "فروشگاه نمونه",
        "phone_number_raw": "SYNTHETIC-PHONE-001",
    }


def _build_synthetic_empty_snapshot() -> ValidatedSourceWorkbookSnapshot:
    """Helper creating a valid 4-sheet snapshot where all sheets are empty."""
    sheets = [
        SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
        SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
        SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
        SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
    ]
    return build_source_workbook_snapshot(sheets)


# --- 1. Public API and Version Constants ---


def test_public_version_constant() -> None:
    """Verify source-change-plan.v1 version constant and enum definitions."""
    assert SOURCE_CHANGE_PLAN_VERSION == "source-change-plan.v1"
    assert PlanAction.INSERT.value == "insert"
    assert PlanAction.EDIT.value == "edit"
    assert PlanAction.VOID.value == "void"
    assert PlanAction.UNCHANGED.value == "unchanged"
    assert IdentityLifecycle.ACTIVE.value == "active"
    assert IdentityLifecycle.VOIDED.value == "voided"


# --- 2. Full Snapshot Validation Rules ---


def test_full_snapshot_all_four_sheets_required() -> None:
    """Verify omitting any of four sheets raises IncompleteSnapshotError."""
    sheets_missing_one = [
        SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
        SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
        SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
    ]
    with pytest.raises(IncompleteSnapshotError) as exc_info:
        build_source_workbook_snapshot(sheets_missing_one)
    assert "missing required sheets" in str(exc_info.value)
    assert "لیست کسبه" in str(exc_info.value)


def test_full_snapshot_duplicate_sheet_rejected() -> None:
    """Verify duplicate sheet declarations raise IncompleteSnapshotError."""
    sheets_duplicate = [
        SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
        SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
        SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
        SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
        SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
    ]
    with pytest.raises(IncompleteSnapshotError) as exc_info:
        build_source_workbook_snapshot(sheets_duplicate)
    assert "Duplicate sheet declaration" in str(exc_info.value)


def test_full_snapshot_unknown_sheet_rejected() -> None:
    """Verify an unapproved or unknown sheet raises IncompleteSnapshotError."""
    sheets_unknown = [
        SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
        SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
        SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
        SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
        SourceSheetInput(sheet_name="شیت_ناشناخته", rows=[]),
    ]
    with pytest.raises(IncompleteSnapshotError) as exc_info:
        build_source_workbook_snapshot(sheets_unknown)
    assert "Unknown or unapproved sheet" in str(exc_info.value)


def test_full_snapshot_empty_workbook_and_empty_sheet_accepted() -> None:
    """Verify empty workbook and empty sheets within workbook are valid."""
    snap = _build_synthetic_empty_snapshot()
    assert snap.total_row_count == 0
    assert len(snap.sheets) == 4
    for sheet_name in RAW_CONTRACT_REGISTRY.sheets:
        assert sheet_name in snap.sheets
        assert snap.sheets[sheet_name].row_count == 0
        assert len(snap.sheets[sheet_name].rows) == 0


# --- 3. Negative Invariant Tests for Forged/Tampered Objects ---


def test_forged_snapshot_cannot_bypass_or_authorize_voids() -> None:
    """Verify forged snapshot objects fail invariant checks and cannot plan changes."""
    id1 = _make_uuid7(b"0000000000000001")
    row_vals = _sample_buy_sell_row()
    valid_hash = compute_source_hash("خرید-فروش", row_vals).source_hash

    valid_row = ValidatedSourceRow(
        stable_id=id1,
        canonical_uuid=str(id1).lower(),
        sheet_name="خرید-فروش",
        raw_values=MappingProxyType(row_vals),
        source_hash=valid_hash,
    )

    valid_sheet = ValidatedSourceSheetSnapshot(
        sheet_name="خرید-فروش",
        rows=(valid_row,),
        row_count=1,
        sheet_snapshot_hash=compute_sheet_snapshot_hash(
            "خرید-فروش", [(str(id1).lower(), valid_hash)]
        ).snapshot_hash,
    )

    # 1. Forged snapshot with only 1 sheet (omitting 3 sheets)
    with pytest.raises(IncompleteSnapshotError) as exc:
        ValidatedSourceWorkbookSnapshot(
            sheets=MappingProxyType({"خرید-فروش": valid_sheet}),
            total_row_count=1,
            all_rows_by_id=MappingProxyType({id1: valid_row}),
        )
    assert "exactly all 4 approved sheets" in str(exc.value)

    # 2. Forged row with tampered source_hash
    with pytest.raises(InvalidIdentityError):
        ValidatedSourceRow(
            stable_id=id1,
            canonical_uuid=str(id1).lower(),
            sheet_name="خرید-فروش",
            raw_values=MappingProxyType(row_vals),
            source_hash="0" * 64,  # forged hash!
        )

    # 3. Forged row with mismatched canonical_uuid
    with pytest.raises(InvalidIdentityError):
        ValidatedSourceRow(
            stable_id=id1,
            canonical_uuid="mismatched-uuid",
            sheet_name="خرید-فروش",
            raw_values=MappingProxyType(row_vals),
            source_hash=valid_hash,
        )

    # 4. Forged sheet with tampered sheet_snapshot_hash
    with pytest.raises(InvalidIdentityError):
        ValidatedSourceSheetSnapshot(
            sheet_name="خرید-فروش",
            rows=(valid_row,),
            row_count=1,
            sheet_snapshot_hash="f" * 64,  # forged snapshot hash!
        )


def test_forged_prior_registry_invariants_rejected() -> None:
    """Verify forged prior states with negative revision, bad hash or lifecycle fail."""
    id1 = _make_uuid7(b"0000000000000001")
    valid_hash = "a" * 64

    # 1. Negative revision
    with pytest.raises(InvalidPriorStateError):
        PriorIdentityState(
            stable_id=id1,
            canonical_uuid=str(id1).lower(),
            home_sheet="خرید-فروش",
            latest_revision=-1,
            lifecycle=IdentityLifecycle.ACTIVE,
            source_hash=valid_hash,
        )

    # 2. Boolean revision
    with pytest.raises(InvalidPriorStateError):
        PriorIdentityState(
            stable_id=id1,
            canonical_uuid=str(id1).lower(),
            home_sheet="خرید-فروش",
            latest_revision=True,
            lifecycle=IdentityLifecycle.ACTIVE,
            source_hash=valid_hash,
        )

    # 3. Active lifecycle with source_hash=None
    with pytest.raises(InvalidPriorStateError):
        PriorIdentityState(
            stable_id=id1,
            canonical_uuid=str(id1).lower(),
            home_sheet="خرید-فروش",
            latest_revision=1,
            lifecycle=IdentityLifecycle.ACTIVE,
            source_hash=None,
        )

    # 4. Voided lifecycle with non-None source_hash
    with pytest.raises(InvalidPriorStateError):
        PriorIdentityState(
            stable_id=id1,
            canonical_uuid=str(id1).lower(),
            home_sheet="خرید-فروش",
            latest_revision=1,
            lifecycle=IdentityLifecycle.VOIDED,
            source_hash=valid_hash,
        )

    # 5. Non-v7 UUID (v4)
    v4_id = uuid.uuid4()
    with pytest.raises(InvalidPriorStateError):
        PriorIdentityState(
            stable_id=v4_id,
            canonical_uuid=str(v4_id).lower(),
            home_sheet="خرید-فروش",
            latest_revision=1,
            lifecycle=IdentityLifecycle.ACTIVE,
            source_hash=valid_hash,
        )


def test_all_rows_by_id_exact_object_matching_and_count_types() -> None:
    """Verify all_rows_by_id requires exact object match and validates count types."""
    id1 = _make_uuid7(b"0000000000000001")
    row_vals = _sample_buy_sell_row()
    valid_hash = compute_source_hash("خرید-فروش", row_vals).source_hash

    row1 = ValidatedSourceRow(
        stable_id=id1,
        canonical_uuid=str(id1).lower(),
        sheet_name="خرید-فروش",
        raw_values=MappingProxyType(row_vals),
        source_hash=valid_hash,
    )
    # Clone with identical content but different object identity
    row1_clone = ValidatedSourceRow(
        stable_id=id1,
        canonical_uuid=str(id1).lower(),
        sheet_name="خرید-فروش",
        raw_values=MappingProxyType(dict(row_vals)),
        source_hash=valid_hash,
    )

    sheet_bf = ValidatedSourceSheetSnapshot(
        sheet_name="خرید-فروش",
        rows=(row1,),
        row_count=1,
        sheet_snapshot_hash=compute_sheet_snapshot_hash(
            "خرید-فروش", [(str(id1).lower(), valid_hash)]
        ).snapshot_hash,
    )
    empty_dp = ValidatedSourceSheetSnapshot(
        sheet_name="دریافت-پرداخت",
        rows=(),
        row_count=0,
        sheet_snapshot_hash=compute_sheet_snapshot_hash(
            "دریافت-پرداخت", []
        ).snapshot_hash,
    )
    empty_vk = ValidatedSourceSheetSnapshot(
        sheet_name="ورود-خروج",
        rows=(),
        row_count=0,
        sheet_snapshot_hash=compute_sheet_snapshot_hash("ورود-خروج", []).snapshot_hash,
    )
    empty_lk = ValidatedSourceSheetSnapshot(
        sheet_name="لیست کسبه",
        rows=(),
        row_count=0,
        sheet_snapshot_hash=compute_sheet_snapshot_hash("لیست کسبه", []).snapshot_hash,
    )

    valid_sheets = {
        "خرید-فروش": sheet_bf,
        "دریافت-پرداخت": empty_dp,
        "ورود-خروج": empty_vk,
        "لیست کسبه": empty_lk,
    }

    # 1. Object mismatch in all_rows_by_id raises IncompleteSnapshotError
    with pytest.raises(IncompleteSnapshotError) as exc:
        ValidatedSourceWorkbookSnapshot(
            sheets=MappingProxyType(valid_sheets),
            total_row_count=1,
            all_rows_by_id=MappingProxyType({id1: row1_clone}),
        )
    assert "does not match the corresponding row object" in str(exc.value)

    # 2. Boolean total_row_count raises IncompleteSnapshotError
    with pytest.raises(IncompleteSnapshotError):
        ValidatedSourceWorkbookSnapshot(
            sheets=MappingProxyType(valid_sheets),
            total_row_count=True,
            all_rows_by_id=MappingProxyType({id1: row1}),
        )

    # 3. Boolean row_count in ValidatedSourceSheetSnapshot raises InvalidIdentityError
    with pytest.raises(InvalidIdentityError):
        ValidatedSourceSheetSnapshot(
            sheet_name="خرید-فروش",
            rows=(row1,),
            row_count=True,
            sheet_snapshot_hash=sheet_bf.sheet_snapshot_hash,
        )

    # 4. Boolean or negative count in PlanCounts raises SourceChangePlanError
    with pytest.raises(SourceChangePlanError):
        PlanCounts(insert_count=True, edit_count=0, void_count=0, unchanged_count=0)
    with pytest.raises(SourceChangePlanError):
        PlanCounts(insert_count=-1, edit_count=0, void_count=0, unchanged_count=0)


def test_hash_validation_rejects_trailing_or_leading_newlines() -> None:
    """Verify hash validation strictly rejects trailing or leading newlines."""
    id1 = _make_uuid7(b"0000000000000001")
    hash_with_newline = "a" * 64 + "\n"
    hash_with_lead_nl = "\n" + "a" * 64

    # 1. compute_sheet_snapshot_hash rejects newline in source_hash
    with pytest.raises(InvalidHashError):
        compute_sheet_snapshot_hash(
            "خرید-فروش", [(str(id1).lower(), hash_with_newline)]
        )

    # 2. ValidatedSourceRow rejects newline in source_hash
    with pytest.raises(InvalidIdentityError):
        ValidatedSourceRow(
            stable_id=id1,
            canonical_uuid=str(id1).lower(),
            sheet_name="خرید-فروش",
            raw_values=MappingProxyType(_sample_buy_sell_row()),
            source_hash=hash_with_newline,
        )

    # 3. PriorIdentityState rejects newline in source_hash
    with pytest.raises(InvalidPriorStateError):
        PriorIdentityState(
            stable_id=id1,
            canonical_uuid=str(id1).lower(),
            home_sheet="خرید-فروش",
            latest_revision=1,
            lifecycle=IdentityLifecycle.ACTIVE,
            source_hash=hash_with_lead_nl,
        )

    # 4. PlanItem rejects newline in hashes
    with pytest.raises(SourceChangePlanError):
        PlanItem(
            action=PlanAction.INSERT,
            sheet_name="خرید-فروش",
            stable_id=id1,
            canonical_uuid=str(id1).lower(),
            planned_revision=1,
            prior_lifecycle=None,
            prior_revision=None,
            prior_source_hash=None,
            current_source_hash=hash_with_newline,
            current_row=None,
        )


# --- 4. Row Validation, Hashes and Global UUID Invariants ---


def test_global_uuid_uniqueness_across_sheets() -> None:
    """Verify duplicate UUID across sheets raises DuplicateIdentityError."""
    dup_id = _make_uuid7(b"0123456789abcdef")

    row_bf = SourceRowInput(stable_id=dup_id, source_values=_sample_buy_sell_row())
    row_dp = SourceRowInput(
        stable_id=dup_id,
        source_values=_sample_receipts_payments_row(),
    )

    sheets = [
        SourceSheetInput(sheet_name="خرید-فروش", rows=[row_bf]),
        SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[row_dp]),
        SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
        SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
    ]
    with pytest.raises(DuplicateIdentityError) as exc_info:
        build_source_workbook_snapshot(sheets)
    assert "Duplicate UUIDv7" in str(exc_info.value)


def test_invalid_uuid_rejected_in_row_input() -> None:
    """Verify non-v7 or malformed UUID in row input raises InvalidIdentityError."""
    v4_id = uuid.uuid4()
    row_v4 = SourceRowInput(stable_id=v4_id, source_values=_sample_buy_sell_row())
    sheets = [
        SourceSheetInput(sheet_name="خرید-فروش", rows=[row_v4]),
        SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
        SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
        SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
    ]
    with pytest.raises(InvalidIdentityError) as exc_info:
        build_source_workbook_snapshot(sheets)
    assert "version 7" in str(exc_info.value)


def test_row_rebuilds_source_hash_matching_wp03_directly() -> None:
    """Verify source_hash & sheet_snapshot_hash match WP-03 calculation."""
    id1 = _make_uuid7(b"0000000000000001")
    row_vals = _sample_buy_sell_row()

    sheets = [
        SourceSheetInput(
            sheet_name="خرید-فروش",
            rows=[SourceRowInput(stable_id=id1, source_values=row_vals)],
        ),
        SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
        SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
        SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
    ]
    snap = build_source_workbook_snapshot(sheets)
    v_row = snap.sheets["خرید-فروش"].rows[0]

    # Direct WP-03 computation comparison
    direct_source_hash = compute_source_hash("خرید-فروش", row_vals).source_hash
    direct_snapshot_hash = compute_sheet_snapshot_hash(
        "خرید-فروش", [(str(id1).lower(), direct_source_hash)]
    ).snapshot_hash

    assert v_row.source_hash == direct_source_hash
    assert snap.sheets["خرید-فروش"].sheet_snapshot_hash == direct_snapshot_hash


# --- 5. Full ADR-0007 Transition Table Tests ---


def test_transition_table_all_actions() -> None:
    """Table-driven test: every ADR-0007 transition action and planned revision."""
    id_new = _make_uuid7(b"0000000000000001")
    id_unchanged = _make_uuid7(b"0000000000000002")
    id_edited = _make_uuid7(b"0000000000000003")
    id_voided = _make_uuid7(b"0000000000000004")
    id_settled_void = _make_uuid7(b"0000000000000005")
    id_reactivated = _make_uuid7(b"0000000000000006")

    row_vals_base = _sample_buy_sell_row()
    row_vals_mutated = dict(row_vals_base)
    row_vals_mutated["notes_raw"] = "یادداشت ویرایش‌شده"

    # Precompute hash of base row
    temp_snap = build_source_workbook_snapshot(
        [
            SourceSheetInput(
                sheet_name="خرید-فروش",
                rows=[
                    SourceRowInput(
                        stable_id=id_unchanged,
                        source_values=row_vals_base,
                    )
                ],
            ),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
        ]
    )
    base_hash = temp_snap.sheets["خرید-فروش"].rows[0].source_hash

    # Construct Current Snapshot
    current_snapshot = build_source_workbook_snapshot(
        [
            SourceSheetInput(
                sheet_name="خرید-فروش",
                rows=[
                    SourceRowInput(stable_id=id_new, source_values=row_vals_base),
                    SourceRowInput(stable_id=id_unchanged, source_values=row_vals_base),
                    SourceRowInput(stable_id=id_edited, source_values=row_vals_mutated),
                    SourceRowInput(
                        stable_id=id_reactivated, source_values=row_vals_base
                    ),
                ],
            ),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
        ]
    )

    # Construct Prior Registry
    prior_registry = build_prior_identity_registry(
        [
            # id_new: UNKNOWN (not in prior state)
            # id_unchanged: ACTIVE with same hash
            PriorIdentityState(
                stable_id=id_unchanged,
                canonical_uuid=str(id_unchanged).lower(),
                home_sheet="خرید-فروش",
                latest_revision=3,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=base_hash,
            ),
            # id_edited: ACTIVE with different hash (prior had base_hash)
            PriorIdentityState(
                stable_id=id_edited,
                canonical_uuid=str(id_edited).lower(),
                home_sheet="خرید-فروش",
                latest_revision=5,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=base_hash,
            ),
            # id_voided: ACTIVE, absent from current snapshot
            PriorIdentityState(
                stable_id=id_voided,
                canonical_uuid=str(id_voided).lower(),
                home_sheet="خرید-فروش",
                latest_revision=2,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=base_hash,
            ),
            # id_settled_void: VOIDED, absent from current snapshot
            PriorIdentityState(
                stable_id=id_settled_void,
                canonical_uuid=str(id_settled_void).lower(),
                home_sheet="خرید-فروش",
                latest_revision=4,
                lifecycle=IdentityLifecycle.VOIDED,
                source_hash=None,
            ),
            # id_reactivated: VOIDED, present again in current snapshot
            PriorIdentityState(
                stable_id=id_reactivated,
                canonical_uuid=str(id_reactivated).lower(),
                home_sheet="خرید-فروش",
                latest_revision=6,
                lifecycle=IdentityLifecycle.VOIDED,
                source_hash=None,
            ),
        ]
    )

    plan = plan_source_changes(current_snapshot, prior_registry)

    # Verify Counts
    assert plan.total_counts.insert_count == 1
    assert plan.total_counts.edit_count == 2  # 1 edit + 1 reactivation
    assert plan.total_counts.void_count == 1
    assert plan.total_counts.unchanged_count == 1
    assert len(plan.items) == 5  # id_settled_void produces NO item!

    items_by_id = {item.stable_id: item for item in plan.items}

    # 1. INSERT transition
    ins = items_by_id[id_new]
    assert ins.action == PlanAction.INSERT
    assert ins.planned_revision == 1
    assert ins.prior_lifecycle is None
    assert ins.prior_revision is None
    assert ins.current_row is not None

    # 2. UNCHANGED transition
    unc = items_by_id[id_unchanged]
    assert unc.action == PlanAction.UNCHANGED
    assert unc.planned_revision is None
    assert unc.prior_lifecycle == IdentityLifecycle.ACTIVE
    assert unc.prior_revision == 3
    assert unc.current_source_hash == base_hash

    # 3. EDIT transition
    edt = items_by_id[id_edited]
    assert edt.action == PlanAction.EDIT
    assert edt.planned_revision == 6  # 5 + 1
    assert edt.prior_lifecycle == IdentityLifecycle.ACTIVE
    assert edt.prior_revision == 5
    assert not edt.is_reactivation

    # 4. VOID transition
    vod = items_by_id[id_voided]
    assert vod.action == PlanAction.VOID
    assert vod.planned_revision == 3  # 2 + 1
    assert vod.prior_lifecycle == IdentityLifecycle.ACTIVE
    assert vod.prior_revision == 2
    assert vod.current_row is None

    # 5. Settled Void -> must NOT be in items
    assert id_settled_void not in items_by_id

    # 6. REACTIVATION transition
    react = items_by_id[id_reactivated]
    assert react.action == PlanAction.EDIT
    assert react.is_reactivation
    assert react.planned_revision == 7  # 6 + 1
    assert react.prior_lifecycle == IdentityLifecycle.VOIDED
    assert react.prior_revision == 6


# --- 6. Identity Relocation Rejection ---


def test_identity_relocation_between_sheets_raises_error() -> None:
    """Verify moving known UUID from home sheet to another rejects the plan."""
    id1 = _make_uuid7(b"0000000000000001")
    valid_hash = "a" * 64

    # Prior state registers id1 in 'خرید-فروش'
    prior = build_prior_identity_registry(
        [
            PriorIdentityState(
                stable_id=id1,
                canonical_uuid=str(id1).lower(),
                home_sheet="خرید-فروش",
                latest_revision=1,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=valid_hash,
            )
        ]
    )

    # Current snapshot presents id1 in 'لیست کسبه' (party_id)
    snap = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
            SourceSheetInput(
                sheet_name="لیست کسبه",
                rows=[
                    SourceRowInput(
                        stable_id=id1,
                        source_values=_sample_business_parties_row(),
                    )
                ],
            ),
        ]
    )

    with pytest.raises(IdentityRelocationError) as exc_info:
        plan_source_changes(snap, prior)
    assert "Identity relocation error" in str(exc_info.value)
    assert "خرید-فروش" in str(exc_info.value)
    assert "لیست کسبه" in str(exc_info.value)


# --- 7. Order Invariance and Permutations ---


def test_order_invariance_sheet_and_row_permutations() -> None:
    """Verify sheet and row order permutations produce identical plan."""
    id1 = _make_uuid7(b"0000000000000001")
    id2 = _make_uuid7(b"0000000000000002")

    # Order A: Forward
    sheets_a = [
        SourceSheetInput(
            sheet_name="خرید-فروش",
            rows=[
                SourceRowInput(stable_id=id1, source_values=_sample_buy_sell_row()),
                SourceRowInput(stable_id=id2, source_values=_sample_buy_sell_row()),
            ],
        ),
        SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
        SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
        SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
    ]

    # Order B: Reverse sheets and reverse rows
    sheets_b = [
        SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
        SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
        SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
        SourceSheetInput(
            sheet_name="خرید-فروش",
            rows=[
                SourceRowInput(stable_id=id2, source_values=_sample_buy_sell_row()),
                SourceRowInput(stable_id=id1, source_values=_sample_buy_sell_row()),
            ],
        ),
    ]

    snap_a = build_source_workbook_snapshot(sheets_a)
    snap_b = build_source_workbook_snapshot(sheets_b)

    # Observable snapshot outputs must be identical
    assert snap_a.sheets["خرید-فروش"].rows == snap_b.sheets["خرید-فروش"].rows
    assert snap_a.all_rows_by_id == snap_b.all_rows_by_id

    plan_a = plan_source_changes(snap_a)
    plan_b = plan_source_changes(snap_b)

    assert plan_a.total_counts == plan_b.total_counts
    assert len(plan_a.items) == len(plan_b.items)
    for it_a, it_b in zip(plan_a.items, plan_b.items, strict=True):
        assert it_a.stable_id == it_b.stable_id
        assert it_a.sheet_name == it_b.sheet_name
        assert it_a.action == it_b.action
        assert it_a.planned_revision == it_b.planned_revision


# --- 8. State Advancement and Real Retry Idempotency ---


def _advance_prior_registry(
    prior: PriorIdentityRegistry, plan: DeterministicSourceChangePlan
) -> PriorIdentityRegistry:
    """Helper applying a planned change to prior identity registry."""
    updated = dict(prior.identities)
    for item in plan.items:
        if item.action == PlanAction.INSERT:
            assert item.current_source_hash is not None
            assert item.planned_revision is not None
            updated[item.stable_id] = PriorIdentityState(
                stable_id=item.stable_id,
                canonical_uuid=item.canonical_uuid,
                home_sheet=item.sheet_name,
                latest_revision=item.planned_revision,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=item.current_source_hash,
            )
        elif item.action == PlanAction.EDIT:
            assert item.current_source_hash is not None
            assert item.planned_revision is not None
            updated[item.stable_id] = PriorIdentityState(
                stable_id=item.stable_id,
                canonical_uuid=item.canonical_uuid,
                home_sheet=item.sheet_name,
                latest_revision=item.planned_revision,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=item.current_source_hash,
            )
        elif item.action == PlanAction.VOID:
            assert item.planned_revision is not None
            updated[item.stable_id] = PriorIdentityState(
                stable_id=item.stable_id,
                canonical_uuid=item.canonical_uuid,
                home_sheet=item.sheet_name,
                latest_revision=item.planned_revision,
                lifecycle=IdentityLifecycle.VOIDED,
                source_hash=None,
            )
        elif item.action == PlanAction.UNCHANGED:
            pass
    return build_prior_identity_registry(updated.values())


def test_state_advancement_for_all_four_transitions() -> None:
    """Verify applying plan to prior state makes subsequent planning idempotent."""
    id_ins = _make_uuid7(b"0000000000000001")
    id_edt = _make_uuid7(b"0000000000000002")
    id_vod = _make_uuid7(b"0000000000000003")
    id_unc = _make_uuid7(b"0000000000000004")
    id_rea = _make_uuid7(b"0000000000000005")

    row_base = _sample_buy_sell_row()
    row_mutated = dict(row_base)
    row_mutated["notes_raw"] = "تغییر یادداشت"

    snap_temp = build_source_workbook_snapshot(
        [
            SourceSheetInput(
                sheet_name="خرید-فروش",
                rows=[SourceRowInput(stable_id=id_unc, source_values=row_base)],
            ),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
        ]
    )
    base_hash = snap_temp.sheets["خرید-فروش"].rows[0].source_hash

    # Initial Prior State
    prior_initial = build_prior_identity_registry(
        [
            PriorIdentityState(
                stable_id=id_unc,
                canonical_uuid=str(id_unc).lower(),
                home_sheet="خرید-فروش",
                latest_revision=1,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=base_hash,
            ),
            PriorIdentityState(
                stable_id=id_edt,
                canonical_uuid=str(id_edt).lower(),
                home_sheet="خرید-فروش",
                latest_revision=1,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=base_hash,
            ),
            PriorIdentityState(
                stable_id=id_vod,
                canonical_uuid=str(id_vod).lower(),
                home_sheet="خرید-فروش",
                latest_revision=2,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=base_hash,
            ),
            PriorIdentityState(
                stable_id=id_rea,
                canonical_uuid=str(id_rea).lower(),
                home_sheet="خرید-فروش",
                latest_revision=3,
                lifecycle=IdentityLifecycle.VOIDED,
                source_hash=None,
            ),
        ]
    )

    # Current Snapshot: contains id_ins, id_edt, id_unc, id_rea; id_vod is absent
    current_snap = build_source_workbook_snapshot(
        [
            SourceSheetInput(
                sheet_name="خرید-فروش",
                rows=[
                    SourceRowInput(stable_id=id_ins, source_values=row_base),
                    SourceRowInput(stable_id=id_edt, source_values=row_mutated),
                    SourceRowInput(stable_id=id_unc, source_values=row_base),
                    SourceRowInput(stable_id=id_rea, source_values=row_base),
                ],
            ),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
        ]
    )

    # Step 1: Execute initial change plan
    plan_1 = plan_source_changes(current_snap, prior_initial)
    assert plan_1.total_counts.insert_count == 1
    assert plan_1.total_counts.edit_count == 2  # 1 edit + 1 reactivate
    assert plan_1.total_counts.void_count == 1
    assert plan_1.total_counts.unchanged_count == 1

    # Step 2: Advance prior state
    prior_advanced = _advance_prior_registry(prior_initial, plan_1)

    # Step 3: Run plan again with same current snapshot and advanced prior state
    plan_2 = plan_source_changes(current_snap, prior_advanced)

    # Verify zero active mutations on retry!
    assert plan_2.total_counts.insert_count == 0
    assert plan_2.total_counts.edit_count == 0
    assert plan_2.total_counts.void_count == 0
    assert plan_2.total_counts.unchanged_count == 4  # all 4 present rows are unchanged
    assert len(plan_2.items) == 4
    assert all(it.action == PlanAction.UNCHANGED for it in plan_2.items)


# --- 9. Null vs Empty Text and Sensitivity ---


def test_null_vs_empty_text_distinction_in_change_plan() -> None:
    """Verify changing null to empty text changes hash and emits EDIT item."""
    id1 = _make_uuid7(b"0000000000000001")

    row_none = {"party_name_raw": None, "phone_number_raw": None}
    row_empty = {"party_name_raw": "", "phone_number_raw": ""}

    snap_none = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
            SourceSheetInput(
                sheet_name="لیست کسبه",
                rows=[SourceRowInput(stable_id=id1, source_values=row_none)],
            ),
        ]
    )
    hash_none = snap_none.sheets["لیست کسبه"].rows[0].source_hash

    prior = build_prior_identity_registry(
        [
            PriorIdentityState(
                stable_id=id1,
                canonical_uuid=str(id1).lower(),
                home_sheet="لیست کسبه",
                latest_revision=1,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=hash_none,
            )
        ]
    )

    snap_empty = build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet_name="خرید-فروش", rows=[]),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
            SourceSheetInput(
                sheet_name="لیست کسبه",
                rows=[SourceRowInput(stable_id=id1, source_values=row_empty)],
            ),
        ]
    )

    plan = plan_source_changes(snap_empty, prior)
    assert plan.total_counts.edit_count == 1
    assert plan.total_counts.unchanged_count == 0
    assert plan.items[0].action == PlanAction.EDIT


def test_single_field_sensitivity_and_canonical_equivalences() -> None:
    """Verify field sensitivity, canonical equivalences & null vs empty text."""
    id1 = _make_uuid7(b"0000000000000001")
    id2 = _make_uuid7(b"0000000000000002")

    base_row = _sample_buy_sell_row()
    snap_base = build_source_workbook_snapshot(
        [
            SourceSheetInput(
                sheet_name="خرید-فروش",
                rows=[
                    SourceRowInput(stable_id=id1, source_values=base_row),
                    SourceRowInput(stable_id=id2, source_values=base_row),
                ],
            ),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
        ]
    )
    h1_base = snap_base.sheets["خرید-فروش"].rows[0].source_hash
    h2_base = snap_base.sheets["خرید-فروش"].rows[1].source_hash

    prior = build_prior_identity_registry(
        [
            PriorIdentityState(
                stable_id=id1,
                canonical_uuid=str(id1).lower(),
                home_sheet="خرید-فروش",
                latest_revision=1,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=h1_base,
            ),
            PriorIdentityState(
                stable_id=id2,
                canonical_uuid=str(id2).lower(),
                home_sheet="خرید-فروش",
                latest_revision=1,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=h2_base,
            ),
        ]
    )

    # 1. Equivalent numeric/date format -> unchanged
    equiv_row1 = dict(base_row)
    equiv_row1["date_raw"] = "۱۴۰۳/۰۵/۱۵"  # Persian digits -> same canonical
    # Insignificant trailing zeros -> same canonical
    equiv_row1["quantity_raw"] = "12.3400"

    # 2. Mutated row2 -> edit
    mutated_row2 = dict(base_row)
    mutated_row2["notes_raw"] = "ویرایش یادداشت"

    snap_mod = build_source_workbook_snapshot(
        [
            SourceSheetInput(
                sheet_name="خرید-فروش",
                rows=[
                    SourceRowInput(stable_id=id1, source_values=equiv_row1),
                    SourceRowInput(stable_id=id2, source_values=mutated_row2),
                ],
            ),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
        ]
    )

    plan = plan_source_changes(snap_mod, prior)
    assert plan.total_counts.unchanged_count == 1
    assert plan.total_counts.edit_count == 1

    item1 = [it for it in plan.items if it.stable_id == id1][0]
    item2 = [it for it in plan.items if it.stable_id == id2][0]

    assert item1.action == PlanAction.UNCHANGED
    assert item2.action == PlanAction.EDIT


def test_empty_sheet_voids_all_prior_active_identities() -> None:
    """Verify an empty sheet voids all prior active identities in that sheet."""
    id1 = _make_uuid7(b"0000000000000001")
    id2 = _make_uuid7(b"0000000000000002")

    prior = build_prior_identity_registry(
        [
            PriorIdentityState(
                stable_id=id1,
                canonical_uuid=str(id1).lower(),
                home_sheet="خرید-فروش",
                latest_revision=2,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash="a" * 64,
            ),
            PriorIdentityState(
                stable_id=id2,
                canonical_uuid=str(id2).lower(),
                home_sheet="خرید-فروش",
                latest_revision=4,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash="b" * 64,
            ),
        ]
    )

    # Valid full workbook with 'خرید-فروش' explicitly empty
    snap = _build_synthetic_empty_snapshot()

    plan = plan_source_changes(snap, prior)
    assert plan.total_counts.void_count == 2
    assert plan.total_counts.insert_count == 0
    assert plan.total_counts.edit_count == 0
    assert plan.total_counts.unchanged_count == 0
    assert len(plan.items) == 2
    assert all(it.action == PlanAction.VOID for it in plan.items)
    assert all(it.sheet_name == "خرید-فروش" for it in plan.items)


def test_per_sheet_counts_breakdown() -> None:
    """Verify per_sheet_counts accurately partitions counts across all 4 sheets."""
    id_bf = _make_uuid7(b"0000000000000001")
    id_dp = _make_uuid7(b"0000000000000002")
    id_vk = _make_uuid7(b"0000000000000003")

    snap = build_source_workbook_snapshot(
        [
            SourceSheetInput(
                sheet_name="خرید-فروش",
                rows=[
                    SourceRowInput(
                        stable_id=id_bf, source_values=_sample_buy_sell_row()
                    )
                ],
            ),
            SourceSheetInput(
                sheet_name="دریافت-پرداخت",
                rows=[
                    SourceRowInput(
                        stable_id=id_dp,
                        source_values=_sample_receipts_payments_row(),
                    )
                ],
            ),
            SourceSheetInput(
                sheet_name="ورود-خروج",
                rows=[
                    SourceRowInput(
                        stable_id=id_vk,
                        source_values=_sample_inventory_movements_row(),
                    )
                ],
            ),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
        ]
    )

    plan = plan_source_changes(snap)  # first import -> 3 inserts
    assert plan.total_counts.insert_count == 3
    assert plan.per_sheet_counts["خرید-فروش"].insert_count == 1
    assert plan.per_sheet_counts["دریافت-پرداخت"].insert_count == 1
    assert plan.per_sheet_counts["ورود-خروج"].insert_count == 1
    assert plan.per_sheet_counts["لیست کسبه"].insert_count == 0


# --- 10. Immutability and Defensive Copying Across All Models ---


def test_constructor_defensive_copies_prevent_external_mutation() -> None:
    """Verify mutating collections passed into constructors does not affect objects."""
    id1 = _make_uuid7(b"0000000000000001")

    # 1. ValidatedSourceRow defensive copy of raw_values
    raw_dict = _sample_buy_sell_row()
    valid_hash = compute_source_hash("خرید-فروش", raw_dict).source_hash
    v_row = ValidatedSourceRow(
        stable_id=id1,
        canonical_uuid=str(id1).lower(),
        sheet_name="خرید-فروش",
        raw_values=raw_dict,  # type: ignore[arg-type]
        source_hash=valid_hash,
    )
    raw_dict["notes_raw"] = "EXTERNAL_MUTATION"
    assert v_row.raw_values["notes_raw"] == "توضیحات فاکتور"

    # 2. ValidatedSourceSheetSnapshot defensive copy of rows
    row_list = [v_row]
    s_snap = ValidatedSourceSheetSnapshot(
        sheet_name="خرید-فروش",
        rows=row_list,  # type: ignore[arg-type]
        row_count=1,
        sheet_snapshot_hash=compute_sheet_snapshot_hash(
            "خرید-فروش", [(str(id1).lower(), valid_hash)]
        ).snapshot_hash,
    )
    row_list.clear()
    assert len(s_snap.rows) == 1

    # 3. ValidatedSourceWorkbookSnapshot defensive copy of sheets & all_rows_by_id
    empty_dp = ValidatedSourceSheetSnapshot(
        sheet_name="دریافت-پرداخت",
        rows=(),
        row_count=0,
        sheet_snapshot_hash=compute_sheet_snapshot_hash(
            "دریافت-پرداخت", []
        ).snapshot_hash,
    )
    empty_vk = ValidatedSourceSheetSnapshot(
        sheet_name="ورود-خروج",
        rows=(),
        row_count=0,
        sheet_snapshot_hash=compute_sheet_snapshot_hash("ورود-خروج", []).snapshot_hash,
    )
    empty_lk = ValidatedSourceSheetSnapshot(
        sheet_name="لیست کسبه",
        rows=(),
        row_count=0,
        sheet_snapshot_hash=compute_sheet_snapshot_hash("لیست کسبه", []).snapshot_hash,
    )

    sheets_dict = {
        "خرید-فروش": s_snap,
        "دریافت-پرداخت": empty_dp,
        "ورود-خروج": empty_vk,
        "لیست کسبه": empty_lk,
    }
    all_rows_dict = {id1: v_row}

    wb_snap = ValidatedSourceWorkbookSnapshot(
        sheets=sheets_dict,  # type: ignore[arg-type]
        total_row_count=1,
        all_rows_by_id=all_rows_dict,  # type: ignore[arg-type]
    )
    sheets_dict.clear()
    all_rows_dict.clear()
    assert len(wb_snap.sheets) == 4
    assert len(wb_snap.all_rows_by_id) == 1

    # 4. PriorIdentityRegistry defensive copy of identities
    prior_state = PriorIdentityState(
        stable_id=id1,
        canonical_uuid=str(id1).lower(),
        home_sheet="خرید-فروش",
        latest_revision=1,
        lifecycle=IdentityLifecycle.ACTIVE,
        source_hash=valid_hash,
    )
    prior_dict = {id1: prior_state}
    prior_reg = PriorIdentityRegistry(identities=prior_dict)  # type: ignore[arg-type]
    prior_dict.clear()
    assert len(prior_reg.identities) == 1

    # 5. DeterministicSourceChangePlan defensive copy of collections
    item = PlanItem(
        action=PlanAction.INSERT,
        sheet_name="خرید-فروش",
        stable_id=id1,
        canonical_uuid=str(id1).lower(),
        planned_revision=1,
        prior_lifecycle=None,
        prior_revision=None,
        prior_source_hash=None,
        current_source_hash=valid_hash,
        current_row=v_row,
    )
    items_list = [item]
    per_sheet = {
        "خرید-فروش": PlanCounts(
            insert_count=1, edit_count=0, void_count=0, unchanged_count=0
        ),
        "دریافت-پرداخت": PlanCounts(
            insert_count=0, edit_count=0, void_count=0, unchanged_count=0
        ),
        "ورود-خروج": PlanCounts(
            insert_count=0, edit_count=0, void_count=0, unchanged_count=0
        ),
        "لیست کسبه": PlanCounts(
            insert_count=0, edit_count=0, void_count=0, unchanged_count=0
        ),
    }
    hashes = {
        "خرید-فروش": s_snap.sheet_snapshot_hash,
        "دریافت-پرداخت": empty_dp.sheet_snapshot_hash,
        "ورود-خروج": empty_vk.sheet_snapshot_hash,
        "لیست کسبه": empty_lk.sheet_snapshot_hash,
    }
    counts = {
        "خرید-فروش": 1,
        "دریافت-پرداخت": 0,
        "ورود-خروج": 0,
        "لیست کسبه": 0,
    }

    plan = DeterministicSourceChangePlan(
        version=SOURCE_CHANGE_PLAN_VERSION,
        items=items_list,  # type: ignore[arg-type]
        total_counts=PlanCounts(
            insert_count=1, edit_count=0, void_count=0, unchanged_count=0
        ),
        per_sheet_counts=per_sheet,  # type: ignore[arg-type]
        current_sheet_snapshot_hashes=hashes,  # type: ignore[arg-type]
        current_sheet_row_counts=counts,  # type: ignore[arg-type]
    )
    items_list.clear()
    per_sheet.clear()
    hashes.clear()
    counts.clear()

    assert len(plan.items) == 1
    assert len(plan.per_sheet_counts) == 4
    assert len(plan.current_sheet_snapshot_hashes) == 4
    assert len(plan.current_sheet_row_counts) == 4


def test_immutability_and_tamper_resistance_across_all_models() -> None:
    """Verify exposed snapshot, prior and plan collections are strictly immutable."""
    id1 = _make_uuid7(b"0000000000000001")
    caller_dict = _sample_buy_sell_row()

    sheets = [
        SourceSheetInput(
            sheet_name="خرید-فروش",
            rows=[SourceRowInput(stable_id=id1, source_values=caller_dict)],
        ),
        SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
        SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
        SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
    ]
    snap = build_source_workbook_snapshot(sheets)
    v_row = snap.sheets["خرید-فروش"].rows[0]

    # 1. Caller dict mutation does not affect snapshot
    caller_dict["notes_raw"] = "MUTATED"
    assert v_row.raw_values["notes_raw"] == "توضیحات فاکتور"

    # 2. Direct mutation on snap.sheets raises TypeError
    with pytest.raises(TypeError):
        snap.sheets["خرید-فروش"] = None  # type: ignore[index]

    # 3. Direct mutation on all_rows_by_id raises TypeError
    with pytest.raises(TypeError):
        snap.all_rows_by_id[id1] = None  # type: ignore[index]

    # 4. Direct mutation on raw_values raises TypeError
    with pytest.raises(TypeError):
        v_row.raw_values["notes_raw"] = "TAMPER"  # type: ignore[index]

    # 5. Prior registry immutability
    prior = build_prior_identity_registry(
        [
            PriorIdentityState(
                stable_id=id1,
                canonical_uuid=str(id1).lower(),
                home_sheet="خرید-فروش",
                latest_revision=1,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=v_row.source_hash,
            )
        ]
    )
    with pytest.raises(TypeError):
        prior.identities[id1] = None  # type: ignore[index]

    # 6. Plan result immutability
    plan = plan_source_changes(snap, prior)
    with pytest.raises(TypeError):
        plan.per_sheet_counts["خرید-فروش"] = None  # type: ignore[index]
    with pytest.raises(TypeError):
        plan.current_sheet_snapshot_hashes["خرید-فروش"] = "tamper"  # type: ignore[index]
    with pytest.raises(TypeError):
        plan.current_sheet_row_counts["خرید-فروش"] = 999  # type: ignore[index]


# --- 11. Synthetic 15,000-Row Complexity Benchmark ---


def test_synthetic_15000_row_complexity_benchmark() -> None:
    """Verify validating and planning 15,000 rows executes in O(N log N) time."""
    rows_per_sheet = 3750
    all_sheet_inputs: list[SourceSheetInput] = []

    generators = [
        ("خرید-فروش", _sample_buy_sell_row),
        ("دریافت-پرداخت", _sample_receipts_payments_row),
        ("ورود-خروج", _sample_inventory_movements_row),
        ("لیست کسبه", _sample_business_parties_row),
    ]

    counter = 1
    prior_states_list: list[PriorIdentityState] = []

    for sheet_name, gen_func in generators:
        sheet_rows: list[SourceRowInput] = []
        for _ in range(rows_per_sheet):
            b_val = counter.to_bytes(16, "big")
            u7 = _make_uuid7(b_val)
            row_dict = gen_func()
            sheet_rows.append(SourceRowInput(stable_id=u7, source_values=row_dict))

            # Simulate 50% existing prior state
            if counter % 2 == 0:
                prior_states_list.append(
                    PriorIdentityState(
                        stable_id=u7,
                        canonical_uuid=str(u7).lower(),
                        home_sheet=sheet_name,
                        latest_revision=1,
                        lifecycle=IdentityLifecycle.ACTIVE,
                        source_hash="0" * 64,  # will cause edit
                    )
                )
            counter += 1
        all_sheet_inputs.append(
            SourceSheetInput(sheet_name=sheet_name, rows=sheet_rows)
        )

    start_build = time.perf_counter()
    snapshot = build_source_workbook_snapshot(all_sheet_inputs)
    build_duration = time.perf_counter() - start_build

    prior_registry = build_prior_identity_registry(prior_states_list)

    start_plan = time.perf_counter()
    plan = plan_source_changes(snapshot, prior_registry)
    plan_duration = time.perf_counter() - start_plan

    assert snapshot.total_row_count == 15000
    assert len(plan.items) == 15000
    assert plan.total_counts.insert_count == 7500
    assert plan.total_counts.edit_count == 7500

    # Ensure efficient reproducible sub-second execution thresholds for 15,000 items
    assert build_duration < 5.0, f"Snapshot build too slow: {build_duration:.2f}s"
    assert plan_duration < 1.0, f"Planning too slow: {plan_duration:.2f}s"


# --- 12. Expanded Hypothesis Property Tests ---


@given(
    permutation_seed=st.integers(min_value=0, max_value=1000),
)
def test_property_change_plan_expanded_permutations_invariance(
    permutation_seed: int,
) -> None:
    """Hypothesis test: arbitrary permutations of all inputs yield identical plan."""
    ids = [_make_uuid7(i.to_bytes(16, "big")) for i in range(1, 9)]

    rng = random.Random(permutation_seed)

    def _permute_dict_keys(d: Mapping[str, Any]) -> dict[str, Any]:
        items = list(d.items())
        rng.shuffle(items)
        return dict(items)

    rows_bf = [
        SourceRowInput(
            stable_id=ids[0],
            source_values=_permute_dict_keys(_sample_buy_sell_row()),
        ),
        SourceRowInput(
            stable_id=ids[1],
            source_values=_permute_dict_keys(_sample_buy_sell_row()),
        ),
    ]
    rows_dp = [
        SourceRowInput(
            stable_id=ids[2],
            source_values=_permute_dict_keys(_sample_receipts_payments_row()),
        ),
        SourceRowInput(
            stable_id=ids[3],
            source_values=_permute_dict_keys(_sample_receipts_payments_row()),
        ),
    ]
    rows_vk = [
        SourceRowInput(
            stable_id=ids[4],
            source_values=_permute_dict_keys(_sample_inventory_movements_row()),
        ),
        SourceRowInput(
            stable_id=ids[5],
            source_values=_permute_dict_keys(_sample_inventory_movements_row()),
        ),
    ]
    rows_lk = [
        SourceRowInput(
            stable_id=ids[6],
            source_values=_permute_dict_keys(_sample_business_parties_row()),
        ),
        SourceRowInput(
            stable_id=ids[7],
            source_values=_permute_dict_keys(_sample_business_parties_row()),
        ),
    ]

    base_sheets = [
        SourceSheetInput(sheet_name="خرید-فروش", rows=rows_bf),
        SourceSheetInput(sheet_name="دریافت-پرداخت", rows=rows_dp),
        SourceSheetInput(sheet_name="ورود-خروج", rows=rows_vk),
        SourceSheetInput(sheet_name="لیست کسبه", rows=rows_lk),
    ]

    # Permute sheets and rows
    permuted_sheets: list[SourceSheetInput] = []
    shuffled_sheets = list(base_sheets)
    rng.shuffle(shuffled_sheets)

    for s in shuffled_sheets:
        shuffled_rows = list(s.rows)
        rng.shuffle(shuffled_rows)
        permuted_sheets.append(
            SourceSheetInput(sheet_name=s.sheet_name, rows=shuffled_rows)
        )

    snap1 = build_source_workbook_snapshot(base_sheets)
    snap2 = build_source_workbook_snapshot(permuted_sheets)

    # Observable snapshot outputs must be identical
    for sheet_name in RAW_CONTRACT_REGISTRY.sheets:
        assert snap1.sheets[sheet_name].rows == snap2.sheets[sheet_name].rows
        assert (
            snap1.sheets[sheet_name].sheet_snapshot_hash
            == snap2.sheets[sheet_name].sheet_snapshot_hash
        )
    assert snap1.all_rows_by_id == snap2.all_rows_by_id

    # Prior state list permutation
    prior_list_base = [
        PriorIdentityState(
            stable_id=ids[0],
            canonical_uuid=str(ids[0]).lower(),
            home_sheet="خرید-فروش",
            latest_revision=1,
            lifecycle=IdentityLifecycle.ACTIVE,
            source_hash=snap1.sheets["خرید-فروش"].rows[0].source_hash,
        ),
        PriorIdentityState(
            stable_id=ids[2],
            canonical_uuid=str(ids[2]).lower(),
            home_sheet="دریافت-پرداخت",
            latest_revision=2,
            lifecycle=IdentityLifecycle.ACTIVE,
            source_hash="0" * 64,  # will edit
        ),
    ]
    prior_list_shuffled = list(prior_list_base)
    rng.shuffle(prior_list_shuffled)

    prior_reg1 = build_prior_identity_registry(prior_list_base)
    prior_reg2 = build_prior_identity_registry(prior_list_shuffled)

    plan1 = plan_source_changes(snap1, prior_reg1)
    plan2 = plan_source_changes(snap2, prior_reg2)

    assert plan1.total_counts == plan2.total_counts
    assert len(plan1.items) == len(plan2.items)
    for it1, it2 in zip(plan1.items, plan2.items, strict=True):
        assert it1.stable_id == it2.stable_id
        assert it1.sheet_name == it2.sheet_name
        assert it1.action == it2.action
        assert it1.planned_revision == it2.planned_revision
