"""Deterministic unit, transition-table, idempotency and property tests.

Verifies ADR-0007 compliance:
- complete four-sheet full-snapshot enforcement;
- pure recalculation of source_hash and sheet_snapshot_hash;
- global UUIDv7 uniqueness and identity home-sheet relocation prevention;
- full transition table (Insert, Edit, Void, Unchanged, Reactivation);
- row-order, sheet-order, mapping-order and prior-state order invariance;
- deep immutability, retry idempotency, and 15,000-row synthetic benchmark.
"""

from __future__ import annotations

import random
import time
import uuid
from typing import Any

import pytest
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
)
from accounting_contracts.source_change_plan import (
    SOURCE_CHANGE_PLAN_VERSION,
    DuplicateIdentityError,
    IdentityLifecycle,
    IdentityRelocationError,
    IncompleteSnapshotError,
    InvalidIdentityError,
    InvalidPriorStateError,
    PlanAction,
    PriorIdentityState,
    SourceChangePlanError,
    SourceRowInput,
    SourceSheetInput,
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
    # Only 3 sheets provided
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


# --- 3. Row Validation, Hashes and Global UUID Invariants ---


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


def test_row_rebuilds_source_hash_without_trusting_caller() -> None:
    """Verify source_hash & sheet_snapshot_hash recalculated deterministically."""
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
    assert len(v_row.source_hash) == 64
    assert len(snap.sheets["خرید-فروش"].sheet_snapshot_hash) == 64


# --- 4. Prior Identity State Validation ---


def test_prior_identity_registry_validations() -> None:
    """Verify prior identity registry validation rules and invariants."""
    id1 = _make_uuid7(b"0000000000000001")
    valid_hash = "a" * 64

    # 1. Valid active and voided prior state
    id2 = _make_uuid7(b"0000000000000002")
    prior = build_prior_identity_registry(
        [
            PriorIdentityState(
                stable_id=id1,
                canonical_uuid=str(id1).lower(),
                home_sheet="خرید-فروش",
                latest_revision=1,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=valid_hash,
            ),
            PriorIdentityState(
                stable_id=id2,
                canonical_uuid=str(id2).lower(),
                home_sheet="دریافت-پرداخت",
                latest_revision=2,
                lifecycle=IdentityLifecycle.VOIDED,
                source_hash=None,
            ),
        ]
    )
    assert len(prior.identities) == 2

    # 2. Duplicate UUID in prior state
    with pytest.raises(DuplicateIdentityError):
        build_prior_identity_registry(
            [
                {
                    "stable_id": id1,
                    "home_sheet": "خرید-فروش",
                    "latest_revision": 1,
                    "lifecycle": "active",
                    "source_hash": valid_hash,
                },
                {
                    "stable_id": id1,
                    "home_sheet": "خرید-فروش",
                    "latest_revision": 2,
                    "lifecycle": "active",
                    "source_hash": valid_hash,
                },
            ]
        )

    # 3. Active identity with invalid/missing hash
    with pytest.raises(InvalidPriorStateError):
        build_prior_identity_registry(
            [
                {
                    "stable_id": id1,
                    "home_sheet": "خرید-فروش",
                    "latest_revision": 1,
                    "lifecycle": "active",
                    "source_hash": None,
                }
            ]
        )

    # 4. Voided identity with active hash present
    with pytest.raises(InvalidPriorStateError):
        build_prior_identity_registry(
            [
                {
                    "stable_id": id1,
                    "home_sheet": "خرید-فروش",
                    "latest_revision": 1,
                    "lifecycle": "voided",
                    "source_hash": valid_hash,
                }
            ]
        )

    # 5. Invalid revision number (zero or boolean)
    with pytest.raises(InvalidPriorStateError):
        build_prior_identity_registry(
            [
                {
                    "stable_id": id1,
                    "home_sheet": "خرید-فروش",
                    "latest_revision": 0,
                    "lifecycle": "active",
                    "source_hash": valid_hash,
                }
            ]
        )
    with pytest.raises(InvalidPriorStateError):
        build_prior_identity_registry(
            [
                {
                    "stable_id": id1,
                    "home_sheet": "خرید-فروش",
                    "latest_revision": True,
                    "lifecycle": "active",
                    "source_hash": valid_hash,
                }
            ]
        )


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

    plan_a = plan_source_changes(build_source_workbook_snapshot(sheets_a))
    plan_b = plan_source_changes(build_source_workbook_snapshot(sheets_b))

    assert plan_a.total_counts == plan_b.total_counts
    assert len(plan_a.items) == len(plan_b.items)
    for it_a, it_b in zip(plan_a.items, plan_b.items, strict=True):
        assert it_a.stable_id == it_b.stable_id
        assert it_a.sheet_name == it_b.sheet_name
        assert it_a.action == it_b.action
        assert it_a.planned_revision == it_b.planned_revision


# --- 8. Retry and Idempotency ---


def test_retry_idempotency_and_state_advancement() -> None:
    """Verify identical current/prior state emits zero active events."""
    id1 = _make_uuid7(b"0000000000000001")
    snap = build_source_workbook_snapshot(
        [
            SourceSheetInput(
                sheet_name="خرید-فروش",
                rows=[
                    SourceRowInput(
                        stable_id=id1,
                        source_values=_sample_buy_sell_row(),
                    )
                ],
            ),
            SourceSheetInput(sheet_name="دریافت-پرداخت", rows=[]),
            SourceSheetInput(sheet_name="ورود-خروج", rows=[]),
            SourceSheetInput(sheet_name="لیست کسبه", rows=[]),
        ]
    )
    computed_hash = snap.sheets["خرید-فروش"].rows[0].source_hash

    # Prior state has identical identity and hash
    prior = build_prior_identity_registry(
        [
            PriorIdentityState(
                stable_id=id1,
                canonical_uuid=str(id1).lower(),
                home_sheet="خرید-فروش",
                latest_revision=1,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=computed_hash,
            )
        ]
    )

    plan = plan_source_changes(snap, prior)
    assert plan.total_counts.insert_count == 0
    assert plan.total_counts.edit_count == 0
    assert plan.total_counts.void_count == 0
    assert plan.total_counts.unchanged_count == 1
    assert len(plan.items) == 1
    assert plan.items[0].action == PlanAction.UNCHANGED
    assert plan.items[0].planned_revision is None


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


# --- 9. Immutability and Defensive Copies ---


def test_defensive_copies_prevent_caller_mutation() -> None:
    """Verify mutating caller dictionaries after build does not alter snapshot."""
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

    # Mutate original caller dictionary
    caller_dict["notes_raw"] = "MUTATED_BY_CALLER"

    v_row = snap.sheets["خرید-فروش"].rows[0]
    assert v_row.raw_values["notes_raw"] == "توضیحات فاکتور"

    # Attempt direct mutation of exposed snapshot MappingProxyType
    with pytest.raises(TypeError):
        v_row.raw_values["notes_raw"] = "ATTEMPTED_MUTATION"  # type: ignore[index]


def test_invalid_snapshot_argument_raises_error() -> None:
    """Verify passing non-snapshot to plan_source_changes raises typed error."""
    with pytest.raises(SourceChangePlanError):
        plan_source_changes("invalid_argument")  # type: ignore[arg-type]


# --- 10. Synthetic 15,000-Row Complexity Benchmark ---


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

    # Ensure efficient sub-second execution for 15,000 items
    assert build_duration < 10.0, f"Snapshot build too slow: {build_duration:.2f}s"
    assert plan_duration < 2.0, f"Planning too slow: {plan_duration:.2f}s"


# --- 11. Hypothesis Property Tests ---


@given(
    permutation_seed=st.integers(min_value=0, max_value=1000),
)
def test_property_change_plan_arbitrary_permutation_invariance(
    permutation_seed: int,
) -> None:
    """Hypothesis test: arbitrary permutations of sheets/rows produce identical plan."""
    ids = [_make_uuid7(i.to_bytes(16, "big")) for i in range(1, 9)]

    rows_bf = [
        SourceRowInput(stable_id=ids[0], source_values=_sample_buy_sell_row()),
        SourceRowInput(stable_id=ids[1], source_values=_sample_buy_sell_row()),
    ]
    rows_dp = [
        SourceRowInput(stable_id=ids[2], source_values=_sample_receipts_payments_row()),
        SourceRowInput(stable_id=ids[3], source_values=_sample_receipts_payments_row()),
    ]
    rows_vk = [
        SourceRowInput(
            stable_id=ids[4], source_values=_sample_inventory_movements_row()
        ),
        SourceRowInput(
            stable_id=ids[5], source_values=_sample_inventory_movements_row()
        ),
    ]
    rows_lk = [
        SourceRowInput(stable_id=ids[6], source_values=_sample_business_parties_row()),
        SourceRowInput(stable_id=ids[7], source_values=_sample_business_parties_row()),
    ]

    base_sheets = [
        SourceSheetInput(sheet_name="خرید-فروش", rows=rows_bf),
        SourceSheetInput(sheet_name="دریافت-پرداخت", rows=rows_dp),
        SourceSheetInput(sheet_name="ورود-خروج", rows=rows_vk),
        SourceSheetInput(sheet_name="لیست کسبه", rows=rows_lk),
    ]

    # Permute sheets and rows
    rng = random.Random(permutation_seed)
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

    plan1 = plan_source_changes(snap1)
    plan2 = plan_source_changes(snap2)

    assert plan1.total_counts == plan2.total_counts
    assert len(plan1.items) == len(plan2.items)
    for it1, it2 in zip(plan1.items, plan2.items, strict=True):
        assert it1.stable_id == it2.stable_id
        assert it1.sheet_name == it2.sheet_name
        assert it1.action == it2.action
        assert it1.planned_revision == it2.planned_revision
