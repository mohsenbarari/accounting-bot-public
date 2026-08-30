"""Validated full-source snapshots and deterministic change planning.

Implements ADR-0007 for validating a complete four-sheet source workbook,
rebuilding source and sheet hashes without trusting caller inputs, maintaining
prior identity state, and computing deterministic Insert/Edit/Void/Unchanged plans.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from accounting_contracts.canonical_hashing import (
    HEX_DIGEST_64_REGEX,
    compute_sheet_snapshot_hash,
    compute_source_hash,
)
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
    ContractError,
    UnknownSheetError,
)

SOURCE_CHANGE_PLAN_VERSION: str = "source-change-plan.v1"


class PlanAction(StrEnum):
    """Action to be taken for an identity in the change plan."""

    INSERT = "insert"
    EDIT = "edit"
    VOID = "void"
    UNCHANGED = "unchanged"


class IdentityLifecycle(StrEnum):
    """Lifecycle state of an identity in the source workbook."""

    ACTIVE = "active"
    VOIDED = "voided"


class SourceChangePlanError(ContractError):
    """Base exception for full snapshot and change planning errors."""


class IncompleteSnapshotError(SourceChangePlanError):
    """Raised when a snapshot does not contain exactly four approved sheets."""


class DuplicateIdentityError(SourceChangePlanError):
    """Raised when duplicate UUIDv7 is found across workbook or prior state."""


class InvalidIdentityError(SourceChangePlanError):
    """Raised when identity contains invalid fields, types or lifecycle state."""


class IdentityRelocationError(SourceChangePlanError):
    """Raised when known UUID is presented in different sheet than home sheet."""


class InvalidPriorStateError(SourceChangePlanError):
    """Raised when prior identity registry contains invalid/inconsistent records."""


def _parse_and_validate_uuid7(val: Any) -> uuid.UUID:
    """Parse and strictly validate an RFC 4122 version 7 UUID."""
    if isinstance(val, uuid.UUID):
        parsed = val
    elif isinstance(val, str):
        try:
            parsed = uuid.UUID(val.strip())
        except (ValueError, AttributeError) as exc:
            msg = f"Malformed UUID string: '{val}'"
            raise InvalidIdentityError(msg) from exc
    else:
        msg = f"Expected UUID or str for stable ID, got {type(val).__name__}"
        raise InvalidIdentityError(msg)

    if parsed.version != 7:
        msg = f"UUID must be version 7, got version {parsed.version} ('{parsed}')"
        raise InvalidIdentityError(msg)

    return parsed


@dataclass(frozen=True, slots=True)
class SourceRowInput:
    """Input representation of a raw source row before snapshot validation."""

    stable_id: uuid.UUID | str
    source_values: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class SourceSheetInput:
    """Input representation of a sheet and its rows before snapshot validation."""

    sheet_name: str
    rows: Iterable[SourceRowInput | tuple[uuid.UUID | str, Mapping[str, Any]]]


@dataclass(frozen=True, slots=True)
class ValidatedSourceRow:
    """Immutable validated row with canonical ID and recalculated source_hash."""

    stable_id: uuid.UUID
    canonical_uuid: str
    sheet_name: str
    raw_values: MappingProxyType[str, Any]
    source_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.stable_id, uuid.UUID) or self.stable_id.version != 7:
            msg = f"Row stable_id must be UUIDv7, got {self.stable_id!r}"
            raise InvalidIdentityError(msg)
        if self.canonical_uuid != str(self.stable_id).lower():
            msg = (
                f"canonical_uuid '{self.canonical_uuid}' does not match "
                f"stable_id '{self.stable_id}'"
            )
            raise InvalidIdentityError(msg)
        if self.sheet_name not in RAW_CONTRACT_REGISTRY.sheets:
            msg = f"Unknown sheet '{self.sheet_name}' in ValidatedSourceRow"
            raise UnknownSheetError(msg)
        if not isinstance(self.raw_values, (Mapping, MappingProxyType)):
            msg = f"raw_values must be a Mapping, got {type(self.raw_values).__name__}"
            raise InvalidIdentityError(msg)
        recomputed_hash = compute_source_hash(
            self.sheet_name, self.raw_values
        ).source_hash
        if self.source_hash != recomputed_hash:
            msg = (
                f"Forged/mismatched source_hash '{self.source_hash}' "
                f"(expected '{recomputed_hash}')"
            )
            raise InvalidIdentityError(msg)


@dataclass(frozen=True, slots=True)
class ValidatedSourceSheetSnapshot:
    """Immutable validated snapshot of a single sheet."""

    sheet_name: str
    rows: tuple[ValidatedSourceRow, ...]
    row_count: int
    sheet_snapshot_hash: str

    def __post_init__(self) -> None:
        if self.sheet_name not in RAW_CONTRACT_REGISTRY.sheets:
            msg = f"Unknown sheet '{self.sheet_name}' in ValidatedSourceSheetSnapshot"
            raise UnknownSheetError(msg)
        if not isinstance(self.rows, tuple):
            msg = f"rows must be a tuple, got {type(self.rows).__name__}"
            raise InvalidIdentityError(msg)
        for r in self.rows:
            if not isinstance(r, ValidatedSourceRow) or r.sheet_name != self.sheet_name:
                msg = f"Invalid row {r!r} in sheet snapshot '{self.sheet_name}'"
                raise InvalidIdentityError(msg)
        sorted_bytes = [r.stable_id.bytes for r in self.rows]
        if sorted_bytes != sorted(sorted_bytes):
            msg = f"Rows in sheet '{self.sheet_name}' are not sorted by UUID bytes"
            raise InvalidIdentityError(msg)
        if self.row_count != len(self.rows):
            msg = (
                f"row_count {self.row_count} does not match len(rows) {len(self.rows)}"
            )
            raise InvalidIdentityError(msg)
        pairs = [(r.canonical_uuid, r.source_hash) for r in self.rows]
        recomputed_hash = compute_sheet_snapshot_hash(
            self.sheet_name, pairs
        ).snapshot_hash
        if self.sheet_snapshot_hash != recomputed_hash:
            msg = (
                f"Forged/mismatched sheet_snapshot_hash '{self.sheet_snapshot_hash}' "
                f"(expected '{recomputed_hash}')"
            )
            raise InvalidIdentityError(msg)


@dataclass(frozen=True, slots=True)
class ValidatedSourceWorkbookSnapshot:
    """Immutable validated snapshot containing exactly four approved sheets."""

    sheets: MappingProxyType[str, ValidatedSourceSheetSnapshot]
    total_row_count: int
    all_rows_by_id: MappingProxyType[uuid.UUID, ValidatedSourceRow]

    def __post_init__(self) -> None:
        approved_sheets = tuple(RAW_CONTRACT_REGISTRY.sheets.keys())
        if not isinstance(self.sheets, (Mapping, MappingProxyType)):
            msg = f"sheets must be a Mapping, got {type(self.sheets).__name__}"
            raise IncompleteSnapshotError(msg)
        if tuple(self.sheets.keys()) != approved_sheets:
            msg = (
                "Workbook snapshot must contain exactly all 4 approved sheets "
                f"in order: {approved_sheets}, got {tuple(self.sheets.keys())}"
            )
            raise IncompleteSnapshotError(msg)
        seen_uuids: set[uuid.UUID] = set()
        expected_total = 0
        for sheet_name, s_snap in self.sheets.items():
            if (
                not isinstance(s_snap, ValidatedSourceSheetSnapshot)
                or s_snap.sheet_name != sheet_name
            ):
                msg = f"Invalid sheet snapshot for '{sheet_name}'"
                raise IncompleteSnapshotError(msg)
            expected_total += s_snap.row_count
            for r in s_snap.rows:
                if r.stable_id in seen_uuids:
                    msg = f"Duplicate UUIDv7 '{r.stable_id}' across workbook snapshot"
                    raise DuplicateIdentityError(msg)
                seen_uuids.add(r.stable_id)

        if self.total_row_count != expected_total:
            msg = (
                f"total_row_count {self.total_row_count} does not match "
                f"expected sum {expected_total}"
            )
            raise IncompleteSnapshotError(msg)

        if not isinstance(self.all_rows_by_id, (Mapping, MappingProxyType)):
            msg = "all_rows_by_id must be a Mapping"
            raise IncompleteSnapshotError(msg)
        if set(self.all_rows_by_id.keys()) != seen_uuids:
            msg = "all_rows_by_id keys do not match workbook rows"
            raise IncompleteSnapshotError(msg)
        if list(self.all_rows_by_id.keys()) != sorted(
            seen_uuids, key=lambda u: u.bytes
        ):
            msg = "all_rows_by_id is not sorted by UUID bytes"
            raise IncompleteSnapshotError(msg)


def build_source_workbook_snapshot(
    sheet_inputs: Iterable[
        SourceSheetInput
        | tuple[
            str,
            Iterable[SourceRowInput | tuple[uuid.UUID | str, Mapping[str, Any]]],
        ]
    ],
) -> ValidatedSourceWorkbookSnapshot:
    """Validate and build a complete four-sheet source workbook snapshot.

    Requires every sheet in RAW_CONTRACT_REGISTRY exactly once.
    Recalculates all source hashes and sheet snapshot hashes deterministically.
    Enforces global UUID uniqueness across all four sheets.
    Sorts rows inside each sheet and all_rows_by_id by UUID bytes.
    """
    approved_sheets = tuple(RAW_CONTRACT_REGISTRY.sheets.keys())
    expected_sheets_set = set(approved_sheets)

    seen_sheets: set[str] = set()
    input_sheets_dict: dict[
        str,
        Iterable[SourceRowInput | tuple[uuid.UUID | str, Mapping[str, Any]]],
    ] = {}

    for item in sheet_inputs:
        if isinstance(item, SourceSheetInput):
            s_name = item.sheet_name
            s_rows = item.rows
        elif isinstance(item, tuple) and len(item) == 2:
            s_name = item[0]
            s_rows = item[1]
        else:
            msg = (
                "Invalid sheet input format: expected SourceSheetInput or tuple, "
                f"got {type(item).__name__}"
            )
            raise IncompleteSnapshotError(msg)

        if not isinstance(s_name, str):
            msg = f"Sheet name must be str, got {type(s_name).__name__}"
            raise IncompleteSnapshotError(msg)

        if s_name not in expected_sheets_set:
            msg = f"Unknown or unapproved sheet '{s_name}' in workbook input"
            raise IncompleteSnapshotError(msg)

        if s_name in seen_sheets:
            msg = f"Duplicate sheet declaration for '{s_name}' in workbook input"
            raise IncompleteSnapshotError(msg)

        seen_sheets.add(s_name)
        input_sheets_dict[s_name] = s_rows

    missing_sheets = expected_sheets_set - seen_sheets
    if missing_sheets:
        msg = f"Incomplete snapshot: missing required sheets {sorted(missing_sheets)}"
        raise IncompleteSnapshotError(msg)

    global_seen_uuids: set[uuid.UUID] = set()
    all_rows_map: dict[uuid.UUID, ValidatedSourceRow] = {}
    validated_sheets_map: dict[str, ValidatedSourceSheetSnapshot] = {}
    total_rows = 0

    # Build sheets in authoritative registry order
    for sheet_name in approved_sheets:
        raw_rows_iterable = input_sheets_dict[sheet_name]
        validated_rows_list: list[ValidatedSourceRow] = []

        for row_item in raw_rows_iterable:
            if isinstance(row_item, SourceRowInput):
                raw_id = row_item.stable_id
                raw_vals = row_item.source_values
            elif isinstance(row_item, tuple) and len(row_item) == 2:
                raw_id = row_item[0]
                raw_vals = row_item[1]
            else:
                msg = f"Invalid row input format in sheet '{sheet_name}': {row_item!r}"
                raise InvalidIdentityError(msg)

            if not isinstance(raw_vals, Mapping):
                msg = f"Row values must be a Mapping, got {type(raw_vals).__name__}"
                raise InvalidIdentityError(msg)

            parsed_uuid = _parse_and_validate_uuid7(raw_id)

            if parsed_uuid in global_seen_uuids:
                msg = (
                    f"Duplicate UUIDv7 '{parsed_uuid}' detected in source workbook. "
                    "UUIDs must be globally unique across all sheets."
                )
                raise DuplicateIdentityError(msg)

            global_seen_uuids.add(parsed_uuid)
            canon_uuid_str = str(parsed_uuid).lower()

            # Defensive copy of scalar raw values mapping
            defensive_raw_values = dict(raw_vals)

            # Compute source hash enforcing raw contracts and canonical format
            source_hash_res = compute_source_hash(sheet_name, defensive_raw_values)

            v_row = ValidatedSourceRow(
                stable_id=parsed_uuid,
                canonical_uuid=canon_uuid_str,
                sheet_name=sheet_name,
                raw_values=MappingProxyType(defensive_raw_values),
                source_hash=source_hash_res.source_hash,
            )
            validated_rows_list.append(v_row)
            all_rows_map[parsed_uuid] = v_row

        # Sort rows deterministically by UUID bytes
        validated_rows_list.sort(key=lambda r: r.stable_id.bytes)

        # Compute sheet snapshot hash via WP-03 canonical hashing
        snapshot_pairs = [
            (r.canonical_uuid, r.source_hash) for r in validated_rows_list
        ]
        sheet_snapshot_res = compute_sheet_snapshot_hash(sheet_name, snapshot_pairs)

        sheet_snapshot = ValidatedSourceSheetSnapshot(
            sheet_name=sheet_name,
            rows=tuple(validated_rows_list),
            row_count=len(validated_rows_list),
            sheet_snapshot_hash=sheet_snapshot_res.snapshot_hash,
        )
        validated_sheets_map[sheet_name] = sheet_snapshot
        total_rows += len(validated_rows_list)

    # Sort all_rows_map by UUID bytes
    sorted_all_rows_map = {
        k: all_rows_map[k] for k in sorted(all_rows_map.keys(), key=lambda u: u.bytes)
    }

    return ValidatedSourceWorkbookSnapshot(
        sheets=MappingProxyType(validated_sheets_map),
        total_row_count=total_rows,
        all_rows_by_id=MappingProxyType(sorted_all_rows_map),
    )


@dataclass(frozen=True, slots=True)
class PriorIdentityState:
    """Immutable state of a previously known identity in the repository."""

    stable_id: uuid.UUID
    canonical_uuid: str
    home_sheet: str
    latest_revision: int
    lifecycle: IdentityLifecycle
    source_hash: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.stable_id, uuid.UUID) or self.stable_id.version != 7:
            msg = f"stable_id must be UUIDv7, got {self.stable_id!r}"
            raise InvalidPriorStateError(msg)
        if self.canonical_uuid != str(self.stable_id).lower():
            msg = (
                f"canonical_uuid '{self.canonical_uuid}' does not match "
                f"stable_id '{self.stable_id}'"
            )
            raise InvalidPriorStateError(msg)
        if self.home_sheet not in RAW_CONTRACT_REGISTRY.sheets:
            msg = f"Unknown home_sheet '{self.home_sheet}' in PriorIdentityState"
            raise InvalidPriorStateError(msg)
        if (
            isinstance(self.latest_revision, bool)
            or not isinstance(self.latest_revision, int)
            or self.latest_revision < 1
        ):
            msg = (
                "latest_revision must be a positive integer, got "
                f"{self.latest_revision!r}"
            )
            raise InvalidPriorStateError(msg)
        if not isinstance(self.lifecycle, IdentityLifecycle):
            msg = f"lifecycle must be an IdentityLifecycle enum, got {self.lifecycle!r}"
            raise InvalidPriorStateError(msg)
        if self.lifecycle == IdentityLifecycle.ACTIVE:
            if not isinstance(self.source_hash, str) or not HEX_DIGEST_64_REGEX.match(
                self.source_hash
            ):
                msg = (
                    "Active prior identity requires 64-hex lowercase "
                    f"source_hash, got {self.source_hash!r}"
                )
                raise InvalidPriorStateError(msg)
        else:
            if self.source_hash is not None:
                msg = (
                    "Voided prior identity must have source_hash=None, "
                    f"got {self.source_hash!r}"
                )
                raise InvalidPriorStateError(msg)


@dataclass(frozen=True, slots=True)
class PriorIdentityRegistry:
    """Immutable registry of all known prior identities."""

    identities: MappingProxyType[uuid.UUID, PriorIdentityState]

    def __post_init__(self) -> None:
        if not isinstance(self.identities, (Mapping, MappingProxyType)):
            msg = f"identities must be a Mapping, got {type(self.identities).__name__}"
            raise InvalidPriorStateError(msg)
        for k, v in self.identities.items():
            if not isinstance(v, PriorIdentityState) or k != v.stable_id:
                msg = f"Invalid entry in PriorIdentityRegistry: {k!r} -> {v!r}"
                raise InvalidPriorStateError(msg)


def build_prior_identity_registry(
    identities: Iterable[PriorIdentityState | Mapping[str, Any]],
) -> PriorIdentityRegistry:
    """Validate and build an immutable registry of prior identities.

    Validates global UUID uniqueness, approved home sheets, positive integer revisions,
    and lifecycle-to-hash consistency.
    """
    identities_map: dict[uuid.UUID, PriorIdentityState] = {}
    approved_sheets = set(RAW_CONTRACT_REGISTRY.sheets.keys())

    for item in identities:
        raw_id: Any
        home_sheet: Any
        latest_revision: Any
        lifecycle: Any
        source_hash: Any

        if isinstance(item, PriorIdentityState):
            raw_id = item.stable_id
            home_sheet = item.home_sheet
            latest_revision = item.latest_revision
            lifecycle = item.lifecycle
            source_hash = item.source_hash
        elif isinstance(item, Mapping):
            raw_id = item.get("stable_id")
            home_sheet = item.get("home_sheet")
            latest_revision = item.get("latest_revision")
            lifecycle = item.get("lifecycle")
            source_hash = item.get("source_hash")
        else:
            msg = f"Expected PriorIdentityState or Mapping, got {type(item).__name__}"
            raise InvalidPriorStateError(msg)

        parsed_uuid = _parse_and_validate_uuid7(raw_id)
        canon_uuid_str = str(parsed_uuid).lower()

        if parsed_uuid in identities_map:
            msg = f"Duplicate prior UUID '{parsed_uuid}' in prior state"
            raise DuplicateIdentityError(msg)

        if not isinstance(home_sheet, str) or home_sheet not in approved_sheets:
            msg = (
                f"Invalid or unknown home_sheet '{home_sheet}' "
                f"for prior identity '{parsed_uuid}'"
            )
            raise InvalidPriorStateError(msg)

        if (
            isinstance(latest_revision, bool)
            or not isinstance(latest_revision, int)
            or latest_revision < 1
        ):
            msg = (
                f"latest_revision must be a positive non-Boolean integer, "
                f"got {latest_revision!r} for '{parsed_uuid}'"
            )
            raise InvalidPriorStateError(msg)

        try:
            parsed_lifecycle = IdentityLifecycle(lifecycle)
        except (ValueError, TypeError) as exc:
            msg = f"Invalid lifecycle '{lifecycle}' for prior identity '{parsed_uuid}'"
            raise InvalidPriorStateError(msg) from exc

        if parsed_lifecycle == IdentityLifecycle.ACTIVE:
            if not isinstance(source_hash, str) or not HEX_DIGEST_64_REGEX.match(
                source_hash
            ):
                msg = (
                    f"Active prior identity '{parsed_uuid}' requires 64-hex lowercase "
                    f"source_hash, got {source_hash!r}"
                )
                raise InvalidPriorStateError(msg)
            clean_hash: str | None = source_hash
        else:
            # VOIDED
            if source_hash is not None:
                msg = (
                    f"Voided prior identity '{parsed_uuid}' must have "
                    f"source_hash=None, got {source_hash!r}"
                )
                raise InvalidPriorStateError(msg)
            clean_hash = None

        state = PriorIdentityState(
            stable_id=parsed_uuid,
            canonical_uuid=canon_uuid_str,
            home_sheet=home_sheet,
            latest_revision=latest_revision,
            lifecycle=parsed_lifecycle,
            source_hash=clean_hash,
        )
        identities_map[parsed_uuid] = state

    return PriorIdentityRegistry(identities=MappingProxyType(identities_map))


@dataclass(frozen=True, slots=True)
class PlanCounts:
    """Summary counts of change actions."""

    insert_count: int
    edit_count: int
    void_count: int
    unchanged_count: int


@dataclass(frozen=True, slots=True)
class PlanItem:
    """Single item transition in the deterministic change plan."""

    action: PlanAction
    sheet_name: str
    stable_id: uuid.UUID
    canonical_uuid: str
    planned_revision: int | None
    prior_lifecycle: IdentityLifecycle | None
    prior_revision: int | None
    prior_source_hash: str | None
    current_source_hash: str | None
    current_row: ValidatedSourceRow | None

    @property
    def is_reactivation(self) -> bool:
        """True if item is reactivation of previously voided identity."""
        return (
            self.prior_lifecycle == IdentityLifecycle.VOIDED
            and self.action == PlanAction.EDIT
        )


@dataclass(frozen=True, slots=True)
class DeterministicSourceChangePlan:
    """Immutable deterministic change plan result."""

    version: str
    items: tuple[PlanItem, ...]
    total_counts: PlanCounts
    per_sheet_counts: MappingProxyType[str, PlanCounts]
    current_sheet_snapshot_hashes: MappingProxyType[str, str]
    current_sheet_row_counts: MappingProxyType[str, int]


def plan_source_changes(
    current_snapshot: ValidatedSourceWorkbookSnapshot,
    prior_state: (
        PriorIdentityRegistry | Iterable[PriorIdentityState | Mapping[str, Any]]
    ) = (),
) -> DeterministicSourceChangePlan:
    """Compute the deterministic source change plan.

    Compares a ValidatedSourceWorkbookSnapshot against prior identities.
    Returns a sorted, deeply immutable DeterministicSourceChangePlan.
    """
    if not isinstance(current_snapshot, ValidatedSourceWorkbookSnapshot):
        msg = (
            "current_snapshot must be a ValidatedSourceWorkbookSnapshot instance; "
            f"got {type(current_snapshot).__name__}"
        )
        raise SourceChangePlanError(msg)

    if isinstance(prior_state, PriorIdentityRegistry):
        prior_registry = prior_state
    else:
        prior_registry = build_prior_identity_registry(prior_state)

    approved_sheets = tuple(RAW_CONTRACT_REGISTRY.sheets.keys())
    sheet_order_index = {name: idx for idx, name in enumerate(approved_sheets)}

    items_list: list[PlanItem] = []

    # Map of all current rows
    current_rows_by_id = current_snapshot.all_rows_by_id
    prior_identities = prior_registry.identities

    # 1. Process all current rows
    for stable_id, current_row in current_rows_by_id.items():
        prior_identity = prior_identities.get(stable_id)

        if prior_identity is None:
            # UNKNOWN -> INSERT (revision 1)
            item = PlanItem(
                action=PlanAction.INSERT,
                sheet_name=current_row.sheet_name,
                stable_id=stable_id,
                canonical_uuid=current_row.canonical_uuid,
                planned_revision=1,
                prior_lifecycle=None,
                prior_revision=None,
                prior_source_hash=None,
                current_source_hash=current_row.source_hash,
                current_row=current_row,
            )
            items_list.append(item)
        else:
            # Known identity
            if prior_identity.home_sheet != current_row.sheet_name:
                msg = (
                    f"Identity relocation error: UUID '{stable_id}' registered in "
                    f"home sheet '{prior_identity.home_sheet}' but present in "
                    f"'{current_row.sheet_name}'"
                )
                raise IdentityRelocationError(msg)

            if prior_identity.lifecycle == IdentityLifecycle.ACTIVE:
                if current_row.source_hash == prior_identity.source_hash:
                    # ACTIVE + SAME HASH -> UNCHANGED (no new revision)
                    item = PlanItem(
                        action=PlanAction.UNCHANGED,
                        sheet_name=current_row.sheet_name,
                        stable_id=stable_id,
                        canonical_uuid=current_row.canonical_uuid,
                        planned_revision=None,
                        prior_lifecycle=prior_identity.lifecycle,
                        prior_revision=prior_identity.latest_revision,
                        prior_source_hash=prior_identity.source_hash,
                        current_source_hash=current_row.source_hash,
                        current_row=current_row,
                    )
                else:
                    # ACTIVE + DIFFERENT HASH -> EDIT (revision n+1)
                    item = PlanItem(
                        action=PlanAction.EDIT,
                        sheet_name=current_row.sheet_name,
                        stable_id=stable_id,
                        canonical_uuid=current_row.canonical_uuid,
                        planned_revision=prior_identity.latest_revision + 1,
                        prior_lifecycle=prior_identity.lifecycle,
                        prior_revision=prior_identity.latest_revision,
                        prior_source_hash=prior_identity.source_hash,
                        current_source_hash=current_row.source_hash,
                        current_row=current_row,
                    )
                items_list.append(item)
            elif prior_identity.lifecycle == IdentityLifecycle.VOIDED:
                # VOIDED + PRESENT -> EDIT / Reactivation (revision n+1)
                item = PlanItem(
                    action=PlanAction.EDIT,
                    sheet_name=current_row.sheet_name,
                    stable_id=stable_id,
                    canonical_uuid=current_row.canonical_uuid,
                    planned_revision=prior_identity.latest_revision + 1,
                    prior_lifecycle=prior_identity.lifecycle,
                    prior_revision=prior_identity.latest_revision,
                    prior_source_hash=None,
                    current_source_hash=current_row.source_hash,
                    current_row=current_row,
                )
                items_list.append(item)

    # 2. Process prior identities absent from the current snapshot
    for stable_id, prior_identity in prior_identities.items():
        if stable_id not in current_rows_by_id:
            if prior_identity.lifecycle == IdentityLifecycle.ACTIVE:
                # ACTIVE + ABSENT -> VOID (revision n+1)
                item = PlanItem(
                    action=PlanAction.VOID,
                    sheet_name=prior_identity.home_sheet,
                    stable_id=stable_id,
                    canonical_uuid=prior_identity.canonical_uuid,
                    planned_revision=prior_identity.latest_revision + 1,
                    prior_lifecycle=prior_identity.lifecycle,
                    prior_revision=prior_identity.latest_revision,
                    prior_source_hash=prior_identity.source_hash,
                    current_source_hash=None,
                    current_row=None,
                )
                items_list.append(item)
            elif prior_identity.lifecycle == IdentityLifecycle.VOIDED:
                # VOIDED + ABSENT -> Settled void (no item emitted)
                pass

    # Sort items deterministically: authoritative sheet order first, then UUID bytes
    items_list.sort(
        key=lambda item: (sheet_order_index[item.sheet_name], item.stable_id.bytes)
    )

    # Compute total and per-sheet counts
    per_sheet_counts_map: dict[str, PlanCounts] = {}
    tot_insert = 0
    tot_edit = 0
    tot_void = 0
    tot_unchanged = 0

    for sheet_name in approved_sheets:
        s_items = [it for it in items_list if it.sheet_name == sheet_name]
        s_ins = sum(1 for it in s_items if it.action == PlanAction.INSERT)
        s_edt = sum(1 for it in s_items if it.action == PlanAction.EDIT)
        s_vod = sum(1 for it in s_items if it.action == PlanAction.VOID)
        s_unc = sum(1 for it in s_items if it.action == PlanAction.UNCHANGED)

        per_sheet_counts_map[sheet_name] = PlanCounts(
            insert_count=s_ins,
            edit_count=s_edt,
            void_count=s_vod,
            unchanged_count=s_unc,
        )
        tot_insert += s_ins
        tot_edit += s_edt
        tot_void += s_vod
        tot_unchanged += s_unc

    total_counts = PlanCounts(
        insert_count=tot_insert,
        edit_count=tot_edit,
        void_count=tot_void,
        unchanged_count=tot_unchanged,
    )

    current_snapshot_hashes = {
        s_name: snapshot.sheet_snapshot_hash
        for s_name, snapshot in current_snapshot.sheets.items()
    }
    current_row_counts = {
        s_name: snapshot.row_count
        for s_name, snapshot in current_snapshot.sheets.items()
    }

    return DeterministicSourceChangePlan(
        version=SOURCE_CHANGE_PLAN_VERSION,
        items=tuple(items_list),
        total_counts=total_counts,
        per_sheet_counts=MappingProxyType(per_sheet_counts_map),
        current_sheet_snapshot_hashes=MappingProxyType(current_snapshot_hashes),
        current_sheet_row_counts=MappingProxyType(current_row_counts),
    )
