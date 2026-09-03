"""Synthetic inputs and independent tuple/dictionary oracles for IP-01..16."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from types import MappingProxyType
from typing import Any

from accounting_contracts import (
    DeterministicSourceChangePlan,
    IdentityLifecycle,
    PriorIdentityRegistry,
    PriorIdentityState,
    SourceBindingKey,
    SourceBindingRecord,
    SourceBindingState,
    SourceSheetInput,
    ValidatedSourceWorkbookSnapshot,
    build_prior_identity_registry,
    build_source_workbook_snapshot,
)

SHEETS = ("خرید-فروش", "دریافت-پرداخت", "ورود-خروج", "لیست کسبه")
RAW: tuple[dict[str, Any], ...] = (
    {
        "date_raw": "1405/01/01",
        "party_name_raw": "SYNTHETIC",
        "transaction_type_raw": "خرید",
        "item_name_raw": "SYNTHETIC",
        "quantity_raw": "1",
        "unit_price_toman_raw": 100,
        "discount_toman_raw": None,
        "notes_raw": None,
    },
    {
        "date_raw": "1405/01/01",
        "party_name_raw": "SYNTHETIC",
        "entry_type_raw": "C",
        "amount_toman_raw": 100,
        "notes_raw": None,
        "account_code_raw": None,
        "customer_flag_raw": None,
    },
    {
        "date_raw": "1405/01/01",
        "party_name_raw": "SYNTHETIC",
        "movement_type_raw": "ورود",
        "item_name_raw": "SYNTHETIC",
        "quantity_raw": "1",
        "purity_raw": None,
        "notes_raw": None,
        "customer_flag_raw": None,
    },
    {"party_name_raw": "SYNTHETIC", "phone_number_raw": None},
)
type RowSpec = tuple[int, int, str]
type StateView = tuple[str, int, str, str | None]
type Model = dict[uuid.UUID, StateView]
type PlanView = tuple[
    uuid.UUID, str, str, int | None, int | None, str | None, str | None, str | None
]


def uid(number: int) -> uuid.UUID:
    return uuid.UUID(int=(7 << 76) | (2 << 62) | number)


def snapshot(
    rows: Iterable[RowSpec] = (), sheet_order: tuple[int, ...] = (0, 1, 2, 3)
) -> ValidatedSourceWorkbookSnapshot:
    grouped: dict[int, list[tuple[uuid.UUID, dict[str, Any]]]] = {
        i: [] for i in range(4)
    }
    for sheet, number, label in rows:
        raw = dict(RAW[sheet], party_name_raw="SYNTHETIC-" + label)
        grouped[sheet].append((uid(number), raw))
    return build_source_workbook_snapshot(
        [SourceSheetInput(SHEETS[i], grouped[i]) for i in sheet_order]
    )


def state(
    sheet: int,
    number: int,
    revision: int = 7,
    *,
    voided: bool = False,
    digest: str = "a" * 64,
) -> PriorIdentityState:
    return PriorIdentityState(
        uid(number),
        str(uid(number)),
        SHEETS[sheet],
        revision,
        IdentityLifecycle.VOIDED if voided else IdentityLifecycle.ACTIVE,
        None if voided else digest,
    )


def prior_from_snapshot(
    source: ValidatedSourceWorkbookSnapshot, revision: int = 7
) -> PriorIdentityRegistry:
    return build_prior_identity_registry(
        [
            PriorIdentityState(
                row.stable_id,
                row.canonical_uuid,
                row.sheet_name,
                revision,
                IdentityLifecycle.ACTIVE,
                row.source_hash,
            )
            for row in source.all_rows_by_id.values()
        ]
    )


def record(
    year: int,
    entries: Iterable[PriorIdentityState] = (),
    *,
    active: bool = False,
    prior: PriorIdentityRegistry | None = None,
) -> SourceBindingRecord:
    return SourceBindingRecord(
        SourceBindingKey(uid(1_000_000 + year), year),
        SourceBindingState.ACTIVE if active else SourceBindingState.ARCHIVED,
        PriorIdentityRegistry(
            MappingProxyType({item.stable_id: item for item in entries})
        )
        if prior is None
        else prior,
        None if active else "d" * 64,
    )


def view(prior: PriorIdentityRegistry) -> Model:
    return {
        key: (
            entry.home_sheet,
            entry.latest_revision,
            entry.lifecycle.value,
            entry.source_hash,
        )
        for key, entry in prior.identities.items()
    }


def from_model(model: Model) -> PriorIdentityRegistry:
    return build_prior_identity_registry(
        [
            PriorIdentityState(
                key, str(key), home, revision, IdentityLifecycle(life), digest
            )
            for key, (home, revision, life, digest) in model.items()
        ]
    )


def plan_view(plan: DeterministicSourceChangePlan) -> list[PlanView]:
    return [
        (
            item.stable_id,
            item.sheet_name,
            item.action.value,
            item.prior_revision,
            item.planned_revision,
            None if item.prior_lifecycle is None else item.prior_lifecycle.value,
            item.prior_source_hash,
            item.current_source_hash,
        )
        for item in plan.items
    ]


def expected_plan(
    current: ValidatedSourceWorkbookSnapshot, prior: Model
) -> list[PlanView]:
    """Literal WP-04 transition table, independent of the production projector."""
    result: list[PlanView] = []
    for key in set(current.all_rows_by_id) | set(prior):
        old, row = prior.get(key), current.all_rows_by_id.get(key)
        if old is None:
            assert row is not None
            result.append(
                (key, row.sheet_name, "insert", None, 1, None, None, row.source_hash)
            )
        elif row is None:
            home, revision, life, digest = old
            if life == "active":
                result.append(
                    (key, home, "void", revision, revision + 1, life, digest, None)
                )
        else:
            home, revision, life, digest = old
            assert home == row.sheet_name
            same = life == "active" and digest == row.source_hash
            result.append(
                (
                    key,
                    home,
                    "unchanged" if same else "edit",
                    revision,
                    None if same else revision + 1,
                    life,
                    digest,
                    row.source_hash,
                )
            )
    return sorted(result, key=lambda item: (SHEETS.index(item[1]), item[0].bytes))


def committed_model(
    current: ValidatedSourceWorkbookSnapshot, comparison: Model
) -> Model:
    """Test-only metadata evolution. Never consumes the actual production plan."""
    result: Model = {}
    for key, (home, revision, life, _digest) in comparison.items():
        if key not in current.all_rows_by_id:
            result[key] = (home, revision + int(life == "active"), "voided", None)
    for key, row in current.all_rows_by_id.items():
        old = comparison.get(key)
        revision = (
            1
            if old is None
            else old[1] + int(old[2] != "active" or old[3] != row.source_hash)
        )
        # This includes newly borrowed parties whose action was UNCHANGED.
        result[key] = (row.sheet_name, revision, "active", row.source_hash)
    return result
