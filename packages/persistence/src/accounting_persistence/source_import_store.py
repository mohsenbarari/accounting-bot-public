"""Atomic local SQLite persistence for source imports (ADR-0018 / WP-15)."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from accounting_contracts.canonical_date import parse_canonical_jalali_date
from accounting_contracts.raw_input_contracts import (
    BUSINESS_PARTIES_CONTRACT,
    RAW_CONTRACT_REGISTRY,
)
from accounting_contracts.source_binding import (
    SourceBindingDisposition,
    SourceBindingKey,
    SourceBindingRecord,
    SourceBindingRegistry,
    SourceBindingState,
    resolve_source_binding,
)
from accounting_contracts.source_change_plan import (
    IdentityLifecycle,
    PlanAction,
    PlanCounts,
    PriorIdentityRegistry,
    PriorIdentityState,
    ValidatedSourceWorkbookSnapshot,
    plan_source_changes,
)
from accounting_contracts.source_fiscal_evidence import evaluate_source_fiscal_evidence
from accounting_contracts.source_identity_projection import (
    SourceIdentityCatalog,
    project_source_prior,
)
from accounting_contracts.source_raw_codec import (
    decode_source_raw_row,
    encode_source_raw_row,
)
from accounting_contracts.source_requiredness import evaluate_source_requiredness

SOURCE_IMPORT_STORE_VERSION: str = "source-import-store.v1"
SOURCE_CHANGE_EVENT_VERSION: str = "source-change-event.v1"
RAW_SHEET_NAMES: tuple[str, ...] = tuple(RAW_CONTRACT_REGISTRY.sheets.keys())

__all__ = [
    "SOURCE_IMPORT_STORE_VERSION",
    "SourceImportStoreReason",
    "SourceImportStoreError",
    "SourceImportDisposition",
    "SourceImportRequest",
    "SourceImportReceipt",
    "SourceImportStoreView",
    "initialize_source_import_store",
    "read_source_import_store",
    "commit_source_import",
]


class SourceImportStoreReason(StrEnum):
    """Normalized reasons for source import store failures."""

    INVALID_INPUT = "invalid_input"
    INVALID_SCHEMA = "invalid_schema"
    VALIDATION_FAILED = "validation_failed"
    SOURCE_NOT_ACTIVE = "source_not_active"
    STALE_STATE = "stale_state"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    INCONSISTENT_STATE = "inconsistent_state"
    STORAGE_FAILURE = "storage_failure"


_REASON_MESSAGES: dict[SourceImportStoreReason, str] = {
    SourceImportStoreReason.INVALID_INPUT: "Invalid source import store input.",
    SourceImportStoreReason.INVALID_SCHEMA: "Invalid source import store schema.",
    SourceImportStoreReason.VALIDATION_FAILED: "Source import validation failed.",
    SourceImportStoreReason.SOURCE_NOT_ACTIVE: "Source is not active.",
    SourceImportStoreReason.STALE_STATE: "Source import state is stale.",
    SourceImportStoreReason.IDEMPOTENCY_CONFLICT: "Source import identity conflicts.",
    SourceImportStoreReason.INCONSISTENT_STATE: (
        "Source import store is inconsistent."
    ),
    SourceImportStoreReason.STORAGE_FAILURE: "Source import storage failed.",
}


class SourceImportStoreError(RuntimeError):
    """Sanitized public exception for source import store operations."""

    def __init__(self, reason: SourceImportStoreReason) -> None:
        if type(reason) is not SourceImportStoreReason:
            raise TypeError("Invalid source import store reason.")
        self.reason = reason
        super().__init__(_REASON_MESSAGES[reason])

    def __repr__(self) -> str:
        return f"SourceImportStoreError(SourceImportStoreReason.{self.reason.name})"

    def __str__(self) -> str:
        return _REASON_MESSAGES[self.reason]


class SourceImportDisposition(StrEnum):
    """Disposition of an import attempt."""

    COMMITTED = "committed"
    REPLAYED = "replayed"


@dataclass(frozen=True, slots=True)
class SourceImportRequest:
    """Frozen input for an atomic source import commit."""

    source_key: SourceBindingKey
    expected_generation: int
    import_id: uuid.UUID
    observed_at_utc: datetime
    file_sha256: str
    snapshot: ValidatedSourceWorkbookSnapshot
    event_ids: Mapping[uuid.UUID, uuid.UUID]

    def __post_init__(self) -> None:
        if (
            type(self.source_key) is not SourceBindingKey
            or type(self.source_key.source_id) is not uuid.UUID
            or self.source_key.source_id.version != 7
            or self.source_key.source_id.variant != uuid.RFC_4122
            or type(self.source_key.fiscal_year) is not int
            or isinstance(self.source_key.fiscal_year, bool)
            or self.source_key.fiscal_year < 1
            or type(self.expected_generation) is not int
            or isinstance(self.expected_generation, bool)
            or self.expected_generation < 0
            or type(self.import_id) is not uuid.UUID
            or self.import_id.version != 7
            or self.import_id.variant != uuid.RFC_4122
            or type(self.observed_at_utc) is not datetime
            or isinstance(self.observed_at_utc, bool)
            or self.observed_at_utc.tzinfo is not UTC
            or type(self.file_sha256) is not str
            or len(self.file_sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.file_sha256)
            or type(self.snapshot) is not ValidatedSourceWorkbookSnapshot
            or (
                type(self.event_ids) is not dict
                and not isinstance(self.event_ids, Mapping)
            )
            or isinstance(self.event_ids, (str, bytes, bytearray))
        ):
            raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT)

        # Defensively copy and freeze event_ids without calling external hooks
        frozen_events: dict[uuid.UUID, uuid.UUID] = {}
        seen_events: set[uuid.UUID] = set()
        try:
            for k, v in self.event_ids.items():
                if (
                    type(k) is not uuid.UUID
                    or k.version != 7
                    or k.variant != uuid.RFC_4122
                    or type(v) is not uuid.UUID
                    or v.version != 7
                    or v.variant != uuid.RFC_4122
                ):
                    raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT)
                if v in seen_events:
                    raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT)
                seen_events.add(v)
                frozen_events[k] = v
        except Exception as exc:
            if isinstance(exc, SourceImportStoreError):
                raise
            raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT) from exc

        object.__setattr__(self, "event_ids", MappingProxyType(frozen_events))

    def __repr__(self) -> str:
        return (
            f"SourceImportRequest(expected_generation={self.expected_generation}, "
            f"event_id_count={len(self.event_ids)})"
        )


@dataclass(frozen=True, slots=True)
class SourceImportReceipt:
    """Immutable receipt returned after a successful commit or replay."""

    disposition: SourceImportDisposition
    import_id: uuid.UUID
    source_id: uuid.UUID
    fiscal_year: int
    base_generation: int
    committed_generation: int
    file_sha256: str
    total_row_count: int
    total_counts: PlanCounts
    per_sheet_counts: MappingProxyType[str, PlanCounts]
    event_count: int
    first_sequence: int | None
    last_sequence: int | None

    def __init__(
        self,
        *,
        disposition: SourceImportDisposition,
        import_id: uuid.UUID,
        source_id: uuid.UUID,
        fiscal_year: int,
        base_generation: int,
        committed_generation: int,
        file_sha256: str,
        total_row_count: int,
        total_counts: PlanCounts,
        per_sheet_counts: Mapping[str, PlanCounts],
        event_count: int,
        first_sequence: int | None,
        last_sequence: int | None,
    ) -> None:
        if (
            type(disposition) is not SourceImportDisposition
            or type(import_id) is not uuid.UUID
            or import_id.version != 7
            or import_id.variant != uuid.RFC_4122
            or type(source_id) is not uuid.UUID
            or source_id.version != 7
            or source_id.variant != uuid.RFC_4122
            or type(fiscal_year) is not int
            or isinstance(fiscal_year, bool)
            or fiscal_year < 1
            or type(base_generation) is not int
            or isinstance(base_generation, bool)
            or base_generation < 0
            or type(committed_generation) is not int
            or isinstance(committed_generation, bool)
            or committed_generation != base_generation + 1
            or type(file_sha256) is not str
            or len(file_sha256) != 64
            or any(c not in "0123456789abcdef" for c in file_sha256)
            or type(total_row_count) is not int
            or isinstance(total_row_count, bool)
            or total_row_count < 0
            or type(total_counts) is not PlanCounts
            or (
                type(per_sheet_counts) is not dict
                and not isinstance(per_sheet_counts, Mapping)
            )
            or isinstance(per_sheet_counts, (str, bytes, bytearray))
            or type(event_count) is not int
            or isinstance(event_count, bool)
            or event_count < 0
        ):
            raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT)

        # Validate exactly four per-sheet counts in canonical order
        expected_sheets = RAW_SHEET_NAMES
        if tuple(per_sheet_counts.keys()) != expected_sheets:
            raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT)

        for s_name in expected_sheets:
            cnt = per_sheet_counts[s_name]
            if type(cnt) is not PlanCounts:
                raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT)

        sum_ins = sum(c.insert_count for c in per_sheet_counts.values())
        sum_edt = sum(c.edit_count for c in per_sheet_counts.values())
        sum_voi = sum(c.void_count for c in per_sheet_counts.values())
        sum_unc = sum(c.unchanged_count for c in per_sheet_counts.values())

        if (
            total_counts.insert_count != sum_ins
            or total_counts.edit_count != sum_edt
            or total_counts.void_count != sum_voi
            or total_counts.unchanged_count != sum_unc
            or event_count
            != total_counts.insert_count
            + total_counts.edit_count
            + total_counts.void_count
            or total_row_count
            != total_counts.insert_count
            + total_counts.edit_count
            + total_counts.unchanged_count
        ):
            raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT)

        if event_count == 0:
            if first_sequence is not None or last_sequence is not None:
                raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT)
        else:
            if (
                type(first_sequence) is not int
                or isinstance(first_sequence, bool)
                or first_sequence < 1
                or type(last_sequence) is not int
                or isinstance(last_sequence, bool)
                or last_sequence != first_sequence + event_count - 1
            ):
                raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT)

        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(self, "import_id", import_id)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "fiscal_year", fiscal_year)
        object.__setattr__(self, "base_generation", base_generation)
        object.__setattr__(self, "committed_generation", committed_generation)
        object.__setattr__(self, "file_sha256", file_sha256)
        object.__setattr__(self, "total_row_count", total_row_count)
        object.__setattr__(self, "total_counts", total_counts)
        object.__setattr__(
            self, "per_sheet_counts", MappingProxyType(dict(per_sheet_counts))
        )
        object.__setattr__(self, "event_count", event_count)
        object.__setattr__(self, "first_sequence", first_sequence)
        object.__setattr__(self, "last_sequence", last_sequence)

    def __repr__(self) -> str:
        return (
            f"SourceImportReceipt(disposition={self.disposition!r}, "
            f"base_generation={self.base_generation}, "
            f"committed_generation={self.committed_generation}, "
            f"total_row_count={self.total_row_count}, "
            f"event_count={self.event_count}, "
            f"first_sequence={self.first_sequence}, "
            f"last_sequence={self.last_sequence})"
        )


@dataclass(frozen=True, slots=True)
class SourceImportStoreView:
    """Consistent snapshot view of a committed generation."""

    version: str
    schema_version: int
    generation: int
    next_sequence: int
    device_id: uuid.UUID
    source_registry: SourceBindingRegistry = field(repr=False)
    last_import_id: uuid.UUID | None = None
    last_file_sha256: str | None = None
    last_observed_at_utc: datetime | None = None

    def __post_init__(self) -> None:
        if (
            self.version != SOURCE_IMPORT_STORE_VERSION
            or self.schema_version != 1
            or type(self.generation) is not int
            or isinstance(self.generation, bool)
            or self.generation < 0
            or type(self.next_sequence) is not int
            or isinstance(self.next_sequence, bool)
            or self.next_sequence < 1
            or type(self.device_id) is not uuid.UUID
            or self.device_id.version != 7
            or self.device_id.variant != uuid.RFC_4122
            or type(self.source_registry) is not SourceBindingRegistry
        ):
            raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT)

        # Validate optional View import/hash/time fields together
        optional_fields = (
            self.last_import_id,
            self.last_file_sha256,
            self.last_observed_at_utc,
        )
        all_none = all(f is None for f in optional_fields)
        all_present = all(f is not None for f in optional_fields)
        if not (all_none or all_present):
            raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT)

        if all_present:
            assert self.last_import_id is not None
            assert self.last_file_sha256 is not None
            assert self.last_observed_at_utc is not None
            if (
                type(self.last_import_id) is not uuid.UUID
                or self.last_import_id.version != 7
                or self.last_import_id.variant != uuid.RFC_4122
                or type(self.last_file_sha256) is not str
                or len(self.last_file_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.last_file_sha256)
                or type(self.last_observed_at_utc) is not datetime
                or isinstance(self.last_observed_at_utc, bool)
                or self.last_observed_at_utc.tzinfo is not UTC
            ):
                raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT)

    def __repr__(self) -> str:
        return (
            f"SourceImportStoreView(version={self.version!r}, "
            f"schema_version={self.schema_version}, "
            f"generation={self.generation}, "
            f"next_sequence={self.next_sequence})"
        )


# ============================================================================
# Schema DDL and Object Specifications (ADR-0018 Schema Version 1)
# ============================================================================

_SCHEMA_V1_DDL: list[str] = [
    # 1. source_store_meta
    """
    CREATE TABLE source_store_meta (
        singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
        store_version TEXT NOT NULL CHECK (store_version = 'source-import-store.v1'),
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        generation INTEGER NOT NULL CHECK (generation >= 0),
        next_sequence INTEGER NOT NULL CHECK (next_sequence >= 1),
        device_id BLOB NOT NULL CHECK (length(device_id) = 16)
    ) STRICT;
    """,
    """
    CREATE TRIGGER trg_prevent_delete_source_store_meta
    BEFORE DELETE ON source_store_meta
    BEGIN
        SELECT RAISE(ABORT, 'Cannot delete source_store_meta');
    END;
    """,
    """
    CREATE TRIGGER trg_guard_update_source_store_meta
    BEFORE UPDATE ON source_store_meta
    BEGIN
        SELECT CASE
            WHEN NEW.singleton_id != 1
                THEN RAISE(ABORT, 'Cannot alter singleton_id')
            WHEN NEW.store_version != OLD.store_version
                THEN RAISE(ABORT, 'Cannot alter store_version')
            WHEN NEW.schema_version != OLD.schema_version
                THEN RAISE(ABORT, 'Cannot alter schema_version')
            WHEN NEW.device_id != OLD.device_id
                THEN RAISE(ABORT, 'Cannot alter device_id')
            WHEN NEW.generation < OLD.generation
                THEN RAISE(ABORT, 'Cannot regress generation')
            WHEN NEW.next_sequence < OLD.next_sequence
                THEN RAISE(ABORT, 'Cannot regress next_sequence')
        END;
    END;
    """,
    # 2. source_bindings
    """
    CREATE TABLE source_bindings (
        source_id BLOB PRIMARY KEY CHECK (length(source_id) = 16),
        fiscal_year INTEGER NOT NULL UNIQUE CHECK (fiscal_year >= 1),
        state TEXT NOT NULL CHECK (state IN ('active', 'archived')),
        final_file_sha256 TEXT CHECK (
            final_file_sha256 IS NULL OR (
                length(final_file_sha256) = 64
                AND final_file_sha256 NOT GLOB '*[^0-9a-f]*'
            )
        ),
        last_import_id BLOB CHECK (
            last_import_id IS NULL OR length(last_import_id) = 16
        ),
        last_file_sha256 TEXT CHECK (
            last_file_sha256 IS NULL OR (
                length(last_file_sha256) = 64
                AND last_file_sha256 NOT GLOB '*[^0-9a-f]*'
            )
        ),
        last_observed_at_utc TEXT CHECK (
            last_observed_at_utc IS NULL OR length(last_observed_at_utc) >= 19
        ),
        CHECK (
            (state = 'active' AND final_file_sha256 IS NULL)
            OR (state = 'archived' AND final_file_sha256 IS NOT NULL)
        )
    ) STRICT;
    """,
    """
    CREATE UNIQUE INDEX uq_source_bindings_active
    ON source_bindings(state) WHERE state = 'active';
    """,
    """
    CREATE TRIGGER trg_prevent_delete_source_bindings
    BEFORE DELETE ON source_bindings
    BEGIN
        SELECT RAISE(ABORT, 'Cannot delete source_bindings');
    END;
    """,
    # 3. source_imports
    """
    CREATE TABLE source_imports (
        import_id BLOB PRIMARY KEY CHECK (length(import_id) = 16),
        request_digest TEXT NOT NULL UNIQUE CHECK (
            length(request_digest) = 64 AND request_digest NOT GLOB '*[^0-9a-f]*'
        ),
        source_id BLOB NOT NULL CHECK (length(source_id) = 16),
        fiscal_year INTEGER NOT NULL CHECK (fiscal_year >= 1),
        base_generation INTEGER NOT NULL CHECK (base_generation >= 0),
        committed_generation INTEGER NOT NULL CHECK (
            committed_generation = base_generation + 1
        ),
        observed_at_utc TEXT NOT NULL CHECK (length(observed_at_utc) >= 19),
        file_sha256 TEXT NOT NULL CHECK (
            length(file_sha256) = 64 AND file_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        total_row_count INTEGER NOT NULL CHECK (total_row_count >= 0),
        insert_count INTEGER NOT NULL CHECK (insert_count >= 0),
        edit_count INTEGER NOT NULL CHECK (edit_count >= 0),
        void_count INTEGER NOT NULL CHECK (void_count >= 0),
        unchanged_count INTEGER NOT NULL CHECK (unchanged_count >= 0),
        event_count INTEGER NOT NULL CHECK (
            event_count >= 0 AND event_count = insert_count + edit_count + void_count
        ),
        first_sequence INTEGER CHECK (
            (event_count = 0 AND first_sequence IS NULL)
            OR (event_count > 0 AND first_sequence IS NOT NULL AND first_sequence >= 1)
        ),
        last_sequence INTEGER CHECK (
            (event_count = 0 AND last_sequence IS NULL)
            OR (
                event_count > 0
                AND last_sequence IS NOT NULL
                AND last_sequence = first_sequence + event_count - 1
            )
        ),
        FOREIGN KEY (source_id) REFERENCES source_bindings(source_id)
    ) STRICT;
    """,
    """
    CREATE INDEX idx_source_imports_source_id ON source_imports(source_id);
    """,
    """
    CREATE TRIGGER trg_prevent_update_source_imports
    BEFORE UPDATE ON source_imports
    BEGIN
        SELECT RAISE(ABORT, 'Cannot update source_imports');
    END;
    """,
    """
    CREATE TRIGGER trg_prevent_delete_source_imports
    BEFORE DELETE ON source_imports
    BEGIN
        SELECT RAISE(ABORT, 'Cannot delete source_imports');
    END;
    """,
    # 4. source_import_sheets
    """
    CREATE TABLE source_import_sheets (
        import_id BLOB NOT NULL CHECK (length(import_id) = 16),
        sheet_order INTEGER NOT NULL CHECK (sheet_order >= 0 AND sheet_order <= 3),
        sheet_name TEXT NOT NULL CHECK (
            sheet_name IN (
                'خرید-فروش',
                'دریافت-پرداخت',
                'ورود-خروج',
                'لیست کسبه'
            )
        ),
        snapshot_hash TEXT NOT NULL CHECK (
            length(snapshot_hash) = 64 AND snapshot_hash NOT GLOB '*[^0-9a-f]*'
        ),
        row_count INTEGER NOT NULL CHECK (row_count >= 0),
        insert_count INTEGER NOT NULL CHECK (insert_count >= 0),
        edit_count INTEGER NOT NULL CHECK (edit_count >= 0),
        void_count INTEGER NOT NULL CHECK (void_count >= 0),
        unchanged_count INTEGER NOT NULL CHECK (unchanged_count >= 0),
        PRIMARY KEY (import_id, sheet_order),
        UNIQUE (import_id, sheet_name),
        FOREIGN KEY (import_id)
            REFERENCES source_imports(import_id) DEFERRABLE INITIALLY DEFERRED
    ) STRICT;
    """,
    """
    CREATE TRIGGER trg_prevent_update_source_import_sheets
    BEFORE UPDATE ON source_import_sheets
    BEGIN
        SELECT RAISE(ABORT, 'Cannot update source_import_sheets');
    END;
    """,
    """
    CREATE TRIGGER trg_prevent_delete_source_import_sheets
    BEFORE DELETE ON source_import_sheets
    BEGIN
        SELECT RAISE(ABORT, 'Cannot delete source_import_sheets');
    END;
    """,
    # 5. source_revisions
    """
    CREATE TABLE source_revisions (
        stable_id BLOB NOT NULL CHECK (length(stable_id) = 16),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        home_sheet TEXT NOT NULL CHECK (
            home_sheet IN (
                'خرید-فروش',
                'دریافت-پرداخت',
                'ورود-خروج',
                'لیست کسبه'
            )
        ),
        lifecycle TEXT NOT NULL CHECK (lifecycle IN ('active', 'voided')),
        source_hash TEXT CHECK (
            source_hash IS NULL OR (
                length(source_hash) = 64 AND source_hash NOT GLOB '*[^0-9a-f]*'
            )
        ),
        raw_payload BLOB,
        created_by_import_id BLOB NOT NULL CHECK (length(created_by_import_id) = 16),
        version_hash TEXT NOT NULL CHECK (
            length(version_hash) = 64 AND version_hash NOT GLOB '*[^0-9a-f]*'
        ),
        previous_version_hash TEXT CHECK (
            previous_version_hash IS NULL OR (
                length(previous_version_hash) = 64
                AND previous_version_hash NOT GLOB '*[^0-9a-f]*'
            )
        ),
        PRIMARY KEY (stable_id, revision),
        FOREIGN KEY (created_by_import_id)
            REFERENCES source_imports(import_id) DEFERRABLE INITIALLY DEFERRED,
        CHECK (
            (lifecycle = 'active'
             AND source_hash IS NOT NULL
             AND raw_payload IS NOT NULL)
            OR (lifecycle = 'voided' AND source_hash IS NULL AND raw_payload IS NULL)
        ),
        CHECK (
            (revision = 1 AND previous_version_hash IS NULL)
            OR (revision > 1 AND previous_version_hash IS NOT NULL)
        )
    ) STRICT;
    """,
    """
    CREATE TRIGGER trg_prevent_update_source_revisions
    BEFORE UPDATE ON source_revisions
    BEGIN
        SELECT RAISE(ABORT, 'Cannot update source_revisions');
    END;
    """,
    """
    CREATE TRIGGER trg_prevent_delete_source_revisions
    BEFORE DELETE ON source_revisions
    BEGIN
        SELECT RAISE(ABORT, 'Cannot delete source_revisions');
    END;
    """,
    # 6. source_memberships
    """
    CREATE TABLE source_memberships (
        source_id BLOB NOT NULL CHECK (length(source_id) = 16),
        stable_id BLOB NOT NULL CHECK (length(stable_id) = 16),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        first_import_id BLOB NOT NULL CHECK (length(first_import_id) = 16),
        last_import_id BLOB NOT NULL CHECK (length(last_import_id) = 16),
        PRIMARY KEY (source_id, stable_id),
        FOREIGN KEY (source_id) REFERENCES source_bindings(source_id),
        FOREIGN KEY (stable_id, revision)
            REFERENCES source_revisions(stable_id, revision),
        FOREIGN KEY (first_import_id)
            REFERENCES source_imports(import_id) DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (last_import_id)
            REFERENCES source_imports(import_id) DEFERRABLE INITIALLY DEFERRED
    ) STRICT;
    """,
    """
    CREATE INDEX idx_source_memberships_revision
    ON source_memberships(stable_id, revision);
    """,
    """
    CREATE TRIGGER trg_prevent_delete_source_memberships
    BEFORE DELETE ON source_memberships
    BEGIN
        SELECT RAISE(ABORT, 'Cannot delete source_memberships');
    END;
    """,
    """
    CREATE TRIGGER trg_guard_update_source_memberships
    BEFORE UPDATE ON source_memberships
    BEGIN
        SELECT CASE
            WHEN NEW.source_id != OLD.source_id
                THEN RAISE(ABORT, 'Cannot retarget source_memberships source_id')
            WHEN NEW.stable_id != OLD.stable_id
                THEN RAISE(ABORT, 'Cannot retarget source_memberships stable_id')
            WHEN NEW.first_import_id != OLD.first_import_id
                THEN RAISE(ABORT, 'Cannot retarget source_memberships first_import_id')
            WHEN NEW.revision < OLD.revision
                THEN RAISE(ABORT, 'Cannot regress source_memberships revision')
        END;
    END;
    """,
    # 7. change_events
    """
    CREATE TABLE change_events (
        sequence INTEGER PRIMARY KEY CHECK (sequence >= 1),
        event_id BLOB NOT NULL UNIQUE CHECK (length(event_id) = 16),
        device_id BLOB NOT NULL CHECK (length(device_id) = 16),
        import_id BLOB NOT NULL CHECK (length(import_id) = 16),
        source_id BLOB NOT NULL CHECK (length(source_id) = 16),
        stable_id BLOB NOT NULL CHECK (length(stable_id) = 16),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        operation TEXT NOT NULL CHECK (operation IN ('upsert', 'void')),
        fiscal_year INTEGER NOT NULL CHECK (fiscal_year >= 1),
        sheet_name TEXT NOT NULL CHECK (
            sheet_name IN (
                'خرید-فروش',
                'دریافت-پرداخت',
                'ورود-خروج',
                'لیست کسبه'
            )
        ),
        financial_date TEXT CHECK (
            financial_date IS NULL OR (
                length(financial_date) = 10
                AND financial_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
            )
        ),
        observed_at_utc TEXT NOT NULL CHECK (length(observed_at_utc) >= 19),
        canonical_payload BLOB NOT NULL,
        payload_hash TEXT NOT NULL CHECK (
            length(payload_hash) = 64 AND payload_hash NOT GLOB '*[^0-9a-f]*'
        ),
        previous_version_hash TEXT CHECK (
            previous_version_hash IS NULL OR (
                length(previous_version_hash) = 64
                AND previous_version_hash NOT GLOB '*[^0-9a-f]*'
            )
        ),
        FOREIGN KEY (import_id)
            REFERENCES source_imports(import_id) DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (source_id) REFERENCES source_bindings(source_id),
        FOREIGN KEY (stable_id, revision)
            REFERENCES source_revisions(stable_id, revision)
            DEFERRABLE INITIALLY DEFERRED
    ) STRICT;
    """,
    """
    CREATE INDEX idx_change_events_import_id ON change_events(import_id);
    """,
    """
    CREATE INDEX idx_change_events_source_id ON change_events(source_id);
    """,
    """
    CREATE INDEX idx_change_events_revision ON change_events(stable_id, revision);
    """,
    """
    CREATE TRIGGER trg_prevent_update_change_events
    BEFORE UPDATE ON change_events
    BEGIN
        SELECT RAISE(ABORT, 'Cannot update change_events');
    END;
    """,
    """
    CREATE TRIGGER trg_prevent_delete_change_events
    BEFORE DELETE ON change_events
    BEGIN
        SELECT RAISE(ABORT, 'Cannot delete change_events');
    END;
    """,
]

_EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "source_store_meta",
        "source_bindings",
        "source_imports",
        "source_import_sheets",
        "source_revisions",
        "source_memberships",
        "change_events",
    }
)

_EXPECTED_INDEXES: frozenset[str] = frozenset(
    {
        "uq_source_bindings_active",
        "idx_source_imports_source_id",
        "idx_source_memberships_revision",
        "idx_change_events_import_id",
        "idx_change_events_source_id",
        "idx_change_events_revision",
    }
)

_EXPECTED_TRIGGERS: frozenset[str] = frozenset(
    {
        "trg_prevent_delete_source_store_meta",
        "trg_guard_update_source_store_meta",
        "trg_prevent_delete_source_bindings",
        "trg_prevent_update_source_imports",
        "trg_prevent_delete_source_imports",
        "trg_prevent_update_source_import_sheets",
        "trg_prevent_delete_source_import_sheets",
        "trg_prevent_update_source_revisions",
        "trg_prevent_delete_source_revisions",
        "trg_prevent_delete_source_memberships",
        "trg_guard_update_source_memberships",
        "trg_prevent_update_change_events",
        "trg_prevent_delete_change_events",
    }
)

_EXPECTED_COLUMNS: dict[str, set[str]] = {
    "source_store_meta": {
        "singleton_id",
        "store_version",
        "schema_version",
        "generation",
        "next_sequence",
        "device_id",
    },
    "source_bindings": {
        "source_id",
        "fiscal_year",
        "state",
        "final_file_sha256",
        "last_import_id",
        "last_file_sha256",
        "last_observed_at_utc",
    },
    "source_imports": {
        "import_id",
        "request_digest",
        "source_id",
        "fiscal_year",
        "base_generation",
        "committed_generation",
        "observed_at_utc",
        "file_sha256",
        "total_row_count",
        "insert_count",
        "edit_count",
        "void_count",
        "unchanged_count",
        "event_count",
        "first_sequence",
        "last_sequence",
    },
    "source_import_sheets": {
        "import_id",
        "sheet_order",
        "sheet_name",
        "snapshot_hash",
        "row_count",
        "insert_count",
        "edit_count",
        "void_count",
        "unchanged_count",
    },
    "source_revisions": {
        "stable_id",
        "revision",
        "home_sheet",
        "lifecycle",
        "source_hash",
        "raw_payload",
        "created_by_import_id",
        "version_hash",
        "previous_version_hash",
    },
    "source_memberships": {
        "source_id",
        "stable_id",
        "revision",
        "first_import_id",
        "last_import_id",
    },
    "change_events": {
        "sequence",
        "event_id",
        "device_id",
        "import_id",
        "source_id",
        "stable_id",
        "revision",
        "operation",
        "fiscal_year",
        "sheet_name",
        "financial_date",
        "observed_at_utc",
        "canonical_payload",
        "payload_hash",
        "previous_version_hash",
    },
}


def _raw_cursor(connection: sqlite3.Connection) -> sqlite3.Cursor:
    """Acquire a clean cursor with standard tuple rows without altering connection."""
    cur = connection.cursor()
    cur.row_factory = None
    return cur


def _verify_caller_connection(connection: sqlite3.Connection) -> None:
    """Ensure caller foreign_keys PRAGMA is ON."""
    cur = _raw_cursor(connection)
    fk_enabled = cur.execute("PRAGMA foreign_keys;").fetchone()[0]
    if fk_enabled != 1:
        raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT)


def _handle_transaction_failure(
    connection: sqlite3.Connection,
    primary_exc: BaseException,
) -> None:
    """Roll back owned transaction and preserve failure identities."""
    rollback_exc: BaseException | None = None
    try:
        if connection.in_transaction:
            connection.execute("ROLLBACK;")
    except BaseException as r_exc:
        rollback_exc = r_exc

    if rollback_exc is not None:
        if isinstance(primary_exc, Exception) and isinstance(rollback_exc, Exception):
            raise ExceptionGroup(
                "Source import failure and rollback failure",
                [primary_exc, rollback_exc],
            ) from primary_exc
        raise BaseExceptionGroup(
            "Source import failure and rollback failure",
            [primary_exc, rollback_exc],
        ) from primary_exc

    if not isinstance(primary_exc, Exception):
        raise primary_exc
    if isinstance(primary_exc, SourceImportStoreError):
        raise primary_exc
    raise SourceImportStoreError(
        SourceImportStoreReason.STORAGE_FAILURE
    ) from primary_exc


def _inspect_and_validate_schema(cur: sqlite3.Cursor) -> None:
    """Validate full schema object inventory, columns, triggers, and indexes."""
    user_version = cur.execute("PRAGMA user_version;").fetchone()[0]
    if user_version != 1:
        raise SourceImportStoreError(SourceImportStoreReason.INVALID_SCHEMA)

    views = cur.execute(
        "SELECT name FROM sqlite_master WHERE type = 'view';"
    ).fetchall()
    if views:
        raise SourceImportStoreError(SourceImportStoreReason.INVALID_SCHEMA)

    tables = {
        row[0]
        for row in cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%';"
        ).fetchall()
    }
    if tables != _EXPECTED_TABLES:
        raise SourceImportStoreError(SourceImportStoreReason.INVALID_SCHEMA)

    triggers = {
        row[0]
        for row in cur.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger';"
        ).fetchall()
    }
    if triggers != _EXPECTED_TRIGGERS:
        raise SourceImportStoreError(SourceImportStoreReason.INVALID_SCHEMA)

    indexes = {
        row[0]
        for row in cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'index' AND name NOT LIKE 'sqlite_%';"
        ).fetchall()
    }
    if indexes != _EXPECTED_INDEXES:
        raise SourceImportStoreError(SourceImportStoreReason.INVALID_SCHEMA)

    for tbl, exp_cols in _EXPECTED_COLUMNS.items():
        cols = {row[1] for row in cur.execute(f"PRAGMA table_info({tbl});").fetchall()}
        if cols != exp_cols:
            raise SourceImportStoreError(SourceImportStoreReason.INVALID_SCHEMA)


def _read_source_import_store_internal(
    connection: sqlite3.Connection,
) -> SourceImportStoreView:
    """Internal reader running within caller's owned or active transaction."""
    cur = _raw_cursor(connection)
    _inspect_and_validate_schema(cur)

    meta_rows = cur.execute(
        "SELECT store_version, schema_version, generation, "
        "next_sequence, device_id FROM source_store_meta WHERE singleton_id = 1;"
    ).fetchall()
    if len(meta_rows) != 1:
        raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)

    store_ver, schema_ver, generation, next_sequence, device_id_bytes = meta_rows[0]
    if (
        store_ver != SOURCE_IMPORT_STORE_VERSION
        or schema_ver != 1
        or generation < 0
        or next_sequence < 1
        or len(device_id_bytes) != 16
    ):
        raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)

    try:
        device_id = uuid.UUID(bytes=device_id_bytes)
    except ValueError:
        raise SourceImportStoreError(
            SourceImportStoreReason.INCONSISTENT_STATE
        ) from None
    if device_id.version != 7 or device_id.variant != uuid.RFC_4122:
        raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)

    # Validate change_events sequence continuity and payload integrity
    ev_stats = cur.execute(
        "SELECT COUNT(*), MIN(sequence), MAX(sequence) FROM change_events;"
    ).fetchone()
    ev_count, min_seq, max_seq = ev_stats
    if next_sequence == 1:
        if ev_count != 0:
            raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)
    else:
        if (
            ev_count != next_sequence - 1
            or min_seq != 1
            or max_seq != next_sequence - 1
        ):
            raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)

    # Validate change_events rows for sequence, payload_hash, and JSON array
    ev_rows = cur.execute(
        """
        SELECT sequence, event_id, device_id, import_id, source_id,
               stable_id, revision, operation, fiscal_year, sheet_name,
               financial_date, observed_at_utc, canonical_payload,
               payload_hash, previous_version_hash
        FROM change_events ORDER BY sequence ASC;
        """
    ).fetchall()

    seen_event_ids: set[bytes] = set()
    event_payload_hashes_by_rev: dict[tuple[bytes, int], str] = {}

    for idx, ev in enumerate(ev_rows, start=1):
        (
            seq,
            e_id,
            d_id,
            i_id,
            s_id,
            st_id,
            rev,
            op,
            fy,
            sh_name,
            fin_dt,
            obs_utc,
            c_payload,
            p_hash,
            p_v_hash,
        ) = ev

        if seq != idx:
            raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)
        if len(e_id) != 16 or e_id in seen_event_ids:
            raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)
        seen_event_ids.add(e_id)
        if d_id != device_id_bytes:
            raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)

        calc_hash = hashlib.sha256(c_payload).hexdigest()
        if calc_hash != p_hash:
            raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)

        try:
            parsed_payload = json.loads(c_payload.decode("utf-8"))
        except Exception:
            raise SourceImportStoreError(
                SourceImportStoreReason.INCONSISTENT_STATE
            ) from None

        if not isinstance(parsed_payload, list) or len(parsed_payload) != 16:
            raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)
        if parsed_payload[0] != SOURCE_CHANGE_EVENT_VERSION:
            raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)

        event_payload_hashes_by_rev[(st_id, rev)] = p_hash

    # Validate revision continuity and previous_version_hash links
    rev_rows = cur.execute(
        """
        SELECT stable_id, revision, home_sheet, lifecycle, source_hash,
               raw_payload, created_by_import_id, version_hash,
               previous_version_hash
        FROM source_revisions
        ORDER BY stable_id ASC, revision ASC;
        """
    ).fetchall()

    revs_by_stable: dict[bytes, list[tuple[Any, ...]]] = {}
    for r in rev_rows:
        revs_by_stable.setdefault(r[0], []).append(r)

    for st_bytes, r_list in revs_by_stable.items():
        prev_hash: str | None = None
        for expected_rev, r_entry in enumerate(r_list, start=1):
            (
                _,
                actual_rev,
                h_sheet,
                l_cycle,
                s_hash,
                raw_bytes,
                c_import,
                v_hash,
                pv_hash,
            ) = r_entry

            if actual_rev != expected_rev:
                raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)

            if expected_rev == 1:
                if pv_hash is not None:
                    raise SourceImportStoreError(
                        SourceImportStoreReason.INCONSISTENT_STATE
                    )
            else:
                if pv_hash != prev_hash:
                    raise SourceImportStoreError(
                        SourceImportStoreReason.INCONSISTENT_STATE
                    )

            # Version hash must match event payload hash
            ev_p_hash = event_payload_hashes_by_rev.get((st_bytes, actual_rev))
            if ev_p_hash is not None and ev_p_hash != v_hash:
                raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)

            if l_cycle == "active":
                if s_hash is None or raw_bytes is None:
                    raise SourceImportStoreError(
                        SourceImportStoreReason.INCONSISTENT_STATE
                    )
                try:
                    dec = decode_source_raw_row(raw_bytes)
                    if (
                        dec.source_hash != s_hash
                        or dec.stable_id.bytes != st_bytes
                        or dec.sheet_name != h_sheet
                    ):
                        raise SourceImportStoreError(
                            SourceImportStoreReason.INCONSISTENT_STATE
                        )
                except Exception as exc:
                    raise SourceImportStoreError(
                        SourceImportStoreReason.INCONSISTENT_STATE
                    ) from exc
            else:
                if s_hash is not None or raw_bytes is not None:
                    raise SourceImportStoreError(
                        SourceImportStoreReason.INCONSISTENT_STATE
                    )

            prev_hash = v_hash

    # Validate imports and sheet reports
    imp_rows = cur.execute(
        """
        SELECT import_id, request_digest, source_id, fiscal_year,
               base_generation, committed_generation, observed_at_utc,
               file_sha256, total_row_count, insert_count, edit_count,
               void_count, unchanged_count, event_count, first_sequence, last_sequence
        FROM source_imports ORDER BY committed_generation ASC;
        """
    ).fetchall()

    for imp in imp_rows:
        (
            i_id,
            r_dig,
            s_id,
            f_yr,
            b_gen,
            c_gen,
            obs,
            f_sha,
            t_rows,
            i_cnt,
            e_cnt,
            v_cnt,
            u_cnt,
            ev_cnt,
            f_seq,
            l_seq,
        ) = imp

        if c_gen != b_gen + 1:
            raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)
        if ev_cnt != i_cnt + e_cnt + v_cnt:
            raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)

        s_rows = cur.execute(
            """
            SELECT sheet_order, sheet_name, snapshot_hash, row_count,
                   insert_count, edit_count, void_count, unchanged_count
            FROM source_import_sheets
            WHERE import_id = ? ORDER BY sheet_order ASC;
            """,
            (i_id,),
        ).fetchall()

        if len(s_rows) != 4:
            raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)

        expected_names = RAW_SHEET_NAMES
        sheet_sum_ins = 0
        sheet_sum_edt = 0
        sheet_sum_voi = 0
        sheet_sum_unc = 0

        for expected_order, s_entry in enumerate(s_rows):
            sh_ord, sh_nm, sh_hsh, sh_rc, s_i, s_e, s_v, s_u = s_entry
            if sh_ord != expected_order or sh_nm != expected_names[expected_order]:
                raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)
            sheet_sum_ins += s_i
            sheet_sum_edt += s_e
            sheet_sum_voi += s_v
            sheet_sum_unc += s_u

        if (
            sheet_sum_ins != i_cnt
            or sheet_sum_edt != e_cnt
            or sheet_sum_voi != v_cnt
            or sheet_sum_unc != u_cnt
        ):
            raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)

    # Validate source bindings and memberships
    binding_rows = cur.execute(
        "SELECT source_id, fiscal_year, state, final_file_sha256, "
        "last_import_id, last_file_sha256, last_observed_at_utc "
        "FROM source_bindings;"
    ).fetchall()

    records: list[SourceBindingRecord] = []
    active_source_metadata: tuple[Any, Any, Any] | None = None

    for b_row in binding_rows:
        b_src_bytes, b_year, b_state, b_final_hash, b_l_imp, b_l_file, b_l_obs = b_row
        try:
            b_src_id = uuid.UUID(bytes=b_src_bytes)
        except ValueError:
            raise SourceImportStoreError(
                SourceImportStoreReason.INCONSISTENT_STATE
            ) from None
        if b_src_id.version != 7 or b_src_id.variant != uuid.RFC_4122:
            raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)

        key = SourceBindingKey(source_id=b_src_id, fiscal_year=b_year)
        state = SourceBindingState(b_state)

        if state is SourceBindingState.ACTIVE:
            if active_source_metadata is not None:
                raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)
            if b_final_hash is not None:
                raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)
            active_source_metadata = (b_l_imp, b_l_file, b_l_obs)
        else:
            if b_final_hash is None or len(b_final_hash) != 64:
                raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)

        # Validate memberships for this source
        mem_rows = cur.execute(
            """
            SELECT m.stable_id, m.revision, r.home_sheet, r.lifecycle,
                   r.source_hash, r.raw_payload, r.version_hash,
                   r.previous_version_hash
            FROM source_memberships m
            JOIN source_revisions r
              ON m.stable_id = r.stable_id AND m.revision = r.revision
            WHERE m.source_id = ?;
            """,
            (b_src_bytes,),
        ).fetchall()

        identities: dict[uuid.UUID, PriorIdentityState] = {}
        for m_row in mem_rows:
            (
                s_id_bytes,
                rev,
                h_sheet,
                l_cycle,
                s_hash,
                r_payload,
                v_hash,
                p_v_hash,
            ) = m_row
            try:
                s_uuid = uuid.UUID(bytes=s_id_bytes)
            except ValueError:
                raise SourceImportStoreError(
                    SourceImportStoreReason.INCONSISTENT_STATE
                ) from None
            if s_uuid.version != 7 or s_uuid.variant != uuid.RFC_4122:
                raise SourceImportStoreError(SourceImportStoreReason.INCONSISTENT_STATE)

            lifecycle = IdentityLifecycle(l_cycle)
            p_state = PriorIdentityState(
                stable_id=s_uuid,
                canonical_uuid=str(s_uuid).lower(),
                home_sheet=h_sheet,
                latest_revision=rev,
                lifecycle=lifecycle,
                source_hash=s_hash,
            )
            identities[s_uuid] = p_state

        sorted_identities = MappingProxyType(
            {k: identities[k] for k in sorted(identities, key=lambda u: u.bytes)}
        )
        prior_registry = PriorIdentityRegistry(identities=sorted_identities)

        record = SourceBindingRecord(
            key=key,
            state=state,
            prior_registry=prior_registry,
            final_file_sha256=b_final_hash,
        )
        records.append(record)

    source_registry = SourceBindingRegistry(records=records)

    try:
        SourceIdentityCatalog(source_registry=source_registry)
    except Exception as exc:
        raise SourceImportStoreError(
            SourceImportStoreReason.INCONSISTENT_STATE
        ) from exc

    last_import_id: uuid.UUID | None = None
    last_file_sha256: str | None = None
    last_observed_at_utc: datetime | None = None

    if active_source_metadata is not None:
        l_imp, l_file, l_obs = active_source_metadata
        if l_imp is not None:
            last_import_id = uuid.UUID(bytes=l_imp)
        if l_file is not None:
            last_file_sha256 = l_file
        if l_obs is not None:
            last_observed_at_utc = datetime.fromisoformat(l_obs)

    return SourceImportStoreView(
        version=SOURCE_IMPORT_STORE_VERSION,
        schema_version=1,
        generation=generation,
        next_sequence=next_sequence,
        device_id=device_id,
        source_registry=source_registry,
        last_import_id=last_import_id,
        last_file_sha256=last_file_sha256,
        last_observed_at_utc=last_observed_at_utc,
    )


def initialize_source_import_store(
    connection: sqlite3.Connection,
    *,
    device_id: uuid.UUID,
    active_source: SourceBindingKey,
) -> SourceImportStoreView:
    """Create only a brand-new empty local store and its first active key."""
    _verify_caller_connection(connection)
    if (
        type(device_id) is not uuid.UUID
        or device_id.version != 7
        or device_id.variant != uuid.RFC_4122
        or type(active_source) is not SourceBindingKey
        or type(active_source.source_id) is not uuid.UUID
        or active_source.source_id.version != 7
        or active_source.source_id.variant != uuid.RFC_4122
        or type(active_source.fiscal_year) is not int
        or isinstance(active_source.fiscal_year, bool)
        or active_source.fiscal_year < 1
    ):
        raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT)

    if connection.in_transaction:
        raise SourceImportStoreError(SourceImportStoreReason.STORAGE_FAILURE)

    cur = _raw_cursor(connection)
    user_version = cur.execute("PRAGMA user_version;").fetchone()[0]

    existing_tables = [
        row[0]
        for row in cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%';"
        ).fetchall()
    ]

    if user_version == 1:
        # Repeated call: validate exact schema and supplied device/source
        try:
            view = _read_source_import_store_internal(connection)
        except Exception as exc:
            raise SourceImportStoreError(
                SourceImportStoreReason.INVALID_SCHEMA
            ) from exc

        if view.device_id != device_id:
            raise SourceImportStoreError(SourceImportStoreReason.INVALID_SCHEMA)
        resolution = resolve_source_binding(active_source, view.source_registry)
        if resolution.disposition is not SourceBindingDisposition.ACTIVE:
            raise SourceImportStoreError(SourceImportStoreReason.INVALID_SCHEMA)
        return view

    if user_version != 0 or len(existing_tables) > 0:
        raise SourceImportStoreError(SourceImportStoreReason.INVALID_SCHEMA)

    existing_other = cur.execute(
        "SELECT count(*) FROM sqlite_master WHERE type IN ('view', 'trigger');"
    ).fetchone()[0]
    if existing_other > 0:
        raise SourceImportStoreError(SourceImportStoreReason.INVALID_SCHEMA)

    try:
        connection.execute("BEGIN IMMEDIATE;")
    except Exception as exc:
        raise SourceImportStoreError(SourceImportStoreReason.STORAGE_FAILURE) from exc

    try:
        cur = _raw_cursor(connection)
        for stmt in _SCHEMA_V1_DDL:
            cur.execute(stmt)
        cur.execute("PRAGMA user_version = 1;")

        cur.execute(
            """
            INSERT INTO source_store_meta (
                singleton_id, store_version, schema_version, generation,
                next_sequence, device_id
            ) VALUES (1, ?, 1, 0, 1, ?);
            """,
            (SOURCE_IMPORT_STORE_VERSION, device_id.bytes),
        )

        cur.execute(
            """
            INSERT INTO source_bindings (
                source_id, fiscal_year, state, final_file_sha256,
                last_import_id, last_file_sha256, last_observed_at_utc
            ) VALUES (?, ?, 'active', NULL, NULL, NULL, NULL);
            """,
            (active_source.source_id.bytes, active_source.fiscal_year),
        )

        view = _read_source_import_store_internal(connection)
        connection.execute("COMMIT;")
        return view
    except BaseException as exc:
        _handle_transaction_failure(connection, exc)
        raise


def read_source_import_store(
    connection: sqlite3.Connection,
) -> SourceImportStoreView:
    """Read and validate one self-consistent committed generation."""
    _verify_caller_connection(connection)
    if connection.in_transaction:
        raise SourceImportStoreError(SourceImportStoreReason.STORAGE_FAILURE)

    try:
        connection.execute("BEGIN;")
    except Exception as exc:
        raise SourceImportStoreError(SourceImportStoreReason.STORAGE_FAILURE) from exc

    try:
        view = _read_source_import_store_internal(connection)
        connection.execute("COMMIT;")
        return view
    except BaseException as exc:
        _handle_transaction_failure(connection, exc)
        raise


def _compute_request_digest(request: SourceImportRequest) -> str:
    """Compute a streaming domain-separated SHA-256 digest of the request."""
    hasher = hashlib.sha256()
    hasher.update(b"source-import-request.v1:")
    hasher.update(request.source_key.source_id.bytes)
    hasher.update(f":{request.source_key.fiscal_year}:".encode("ascii"))
    hasher.update(f"{request.expected_generation}:".encode("ascii"))
    hasher.update(request.import_id.bytes)
    hasher.update(f":{request.observed_at_utc.isoformat()}:".encode())
    hasher.update(request.file_sha256.encode("ascii"))
    hasher.update(b":events:")
    for s_bytes, e_bytes in sorted(
        ((k.bytes, v.bytes) for k, v in request.event_ids.items()),
        key=lambda pair: pair[0],
    ):
        hasher.update(s_bytes)
        hasher.update(e_bytes)
    hasher.update(b":sheets:")
    for sheet_name in RAW_CONTRACT_REGISTRY.sheets:
        sheet = request.snapshot.sheets[sheet_name]
        hasher.update(sheet_name.encode("utf-8"))
        hasher.update(
            f":{sheet.row_count}:{sheet.sheet_snapshot_hash}:".encode("ascii")
        )
        for row in sheet.rows:
            raw_bytes = encode_source_raw_row(row)
            hasher.update(raw_bytes)
    return hasher.hexdigest()


def commit_source_import(
    connection: sqlite3.Connection,
    request: SourceImportRequest,
) -> SourceImportReceipt:
    """Validate, plan and atomically commit or replay one import."""
    _verify_caller_connection(connection)

    if not isinstance(request, SourceImportRequest):
        raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT)

    # 1. Domain-separated request digest
    request_digest = _compute_request_digest(request)

    # 2. WP-09 Requiredness evaluation
    requiredness_report = evaluate_source_requiredness(request.snapshot)
    if not requiredness_report.passes_requiredness:
        raise SourceImportStoreError(SourceImportStoreReason.VALIDATION_FAILED)

    # WP-10 fiscal evidence evaluation (retained / executed)
    evaluate_source_fiscal_evidence(request.snapshot)

    # 3. Transaction start
    if connection.in_transaction:
        raise SourceImportStoreError(SourceImportStoreReason.STORAGE_FAILURE)

    try:
        connection.execute("BEGIN IMMEDIATE;")
    except Exception as exc:
        raise SourceImportStoreError(SourceImportStoreReason.STORAGE_FAILURE) from exc

    try:
        cur = _raw_cursor(connection)
        # Load and validate current generation
        meta_cur = cur.execute(
            "SELECT store_version, schema_version, generation, "
            "next_sequence, device_id FROM source_store_meta WHERE singleton_id = 1;"
        )
        meta_row = meta_cur.fetchone()
        if meta_row is None:
            raise SourceImportStoreError(SourceImportStoreReason.INVALID_SCHEMA)
        store_ver, schema_ver, stored_gen, stored_next_seq, device_id_bytes = meta_row
        device_id = uuid.UUID(bytes=device_id_bytes)

        # Resolve active source
        binding_cur = cur.execute(
            "SELECT source_id, fiscal_year, state "
            "FROM source_bindings WHERE source_id = ?;",
            (request.source_key.source_id.bytes,),
        )
        binding_row = binding_cur.fetchone()
        if binding_row is None:
            raise SourceImportStoreError(SourceImportStoreReason.SOURCE_NOT_ACTIVE)
        b_src_bytes, b_year, b_state = binding_row
        if (
            b_year != request.source_key.fiscal_year
            or b_state != SourceBindingState.ACTIVE
        ):
            raise SourceImportStoreError(SourceImportStoreReason.SOURCE_NOT_ACTIVE)

        # 4. Idempotency replay check BEFORE stale generation check
        imp_cur = cur.execute(
            """
            SELECT request_digest, base_generation, committed_generation,
                   file_sha256, total_row_count, insert_count, edit_count,
                   void_count, unchanged_count, event_count, first_sequence,
                   last_sequence, source_id, fiscal_year
            FROM source_imports WHERE import_id = ?;
            """,
            (request.import_id.bytes,),
        )
        imp_row = imp_cur.fetchone()
        if imp_row is not None:
            stored_digest = imp_row[0]
            if stored_digest != request_digest:
                raise SourceImportStoreError(
                    SourceImportStoreReason.IDEMPOTENCY_CONFLICT
                )

            # Validate protected generation/schema/history needed for the receipt
            try:
                _read_source_import_store_internal(connection)
            except Exception as exc:
                raise SourceImportStoreError(
                    SourceImportStoreReason.INCONSISTENT_STATE
                ) from exc

            # Reconstruct original receipt for REPLAYED
            sheet_rows = cur.execute(
                """
                SELECT sheet_name, insert_count, edit_count, void_count, unchanged_count
                FROM source_import_sheets
                WHERE import_id = ? ORDER BY sheet_order ASC;
                """,
                (request.import_id.bytes,),
            ).fetchall()

            per_sheet: dict[str, PlanCounts] = {}
            for s_name, i_cnt, e_cnt, v_cnt, u_cnt in sheet_rows:
                per_sheet[s_name] = PlanCounts(
                    insert_count=i_cnt,
                    edit_count=e_cnt,
                    void_count=v_cnt,
                    unchanged_count=u_cnt,
                )

            total_counts = PlanCounts(
                insert_count=imp_row[5],
                edit_count=imp_row[6],
                void_count=imp_row[7],
                unchanged_count=imp_row[8],
            )

            receipt = SourceImportReceipt(
                disposition=SourceImportDisposition.REPLAYED,
                import_id=request.import_id,
                source_id=request.source_key.source_id,
                fiscal_year=request.source_key.fiscal_year,
                base_generation=imp_row[1],
                committed_generation=imp_row[2],
                file_sha256=imp_row[3],
                total_row_count=imp_row[4],
                total_counts=total_counts,
                per_sheet_counts=per_sheet,
                event_count=imp_row[9],
                first_sequence=imp_row[10],
                last_sequence=imp_row[11],
            )
            # Replay requires no writes; release transaction cleanly
            connection.execute("ROLLBACK;")
            return receipt

        # Stale state check
        if stored_gen != request.expected_generation:
            raise SourceImportStoreError(SourceImportStoreReason.STALE_STATE)

        # Reconstruct catalog and project prior via internal reader
        store_view = _read_source_import_store_internal(connection)
        resolution = resolve_source_binding(
            request.source_key, store_view.source_registry
        )
        if resolution.disposition is not SourceBindingDisposition.ACTIVE:
            raise SourceImportStoreError(SourceImportStoreReason.SOURCE_NOT_ACTIVE)

        catalog = SourceIdentityCatalog(source_registry=store_view.source_registry)
        prior_registry = project_source_prior(
            request.source_key, request.snapshot, catalog
        )

        # Rerun Planner
        plan = plan_source_changes(request.snapshot, prior_registry)

        # 5. Validate event_ids against all and only changed plan items
        changed_items = [
            item
            for item in plan.items
            if item.action in (PlanAction.INSERT, PlanAction.EDIT, PlanAction.VOID)
        ]
        # Build canonical UUID lookup once (R6 complexity fix: O(1) lookup)
        changed_items_by_id = {item.stable_id: item for item in changed_items}
        changed_uuids = set(changed_items_by_id.keys())
        supplied_uuids = set(request.event_ids.keys())

        if changed_uuids != supplied_uuids:
            raise SourceImportStoreError(SourceImportStoreReason.INVALID_INPUT)

        # Allocate sequence numbers
        event_count = len(changed_items)
        if event_count > 0:
            first_sequence = stored_next_seq
            last_sequence = stored_next_seq + event_count - 1
            next_seq_val = last_sequence + 1
        else:
            first_sequence = None
            last_sequence = None
            next_seq_val = stored_next_seq

        # Insert source_imports
        cur.execute(
            """
            INSERT INTO source_imports (
                import_id, request_digest, source_id, fiscal_year,
                base_generation, committed_generation, observed_at_utc,
                file_sha256, total_row_count, insert_count, edit_count,
                void_count, unchanged_count, event_count, first_sequence, last_sequence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                request.import_id.bytes,
                request_digest,
                request.source_key.source_id.bytes,
                request.source_key.fiscal_year,
                stored_gen,
                stored_gen + 1,
                request.observed_at_utc.isoformat(),
                request.file_sha256,
                request.snapshot.total_row_count,
                plan.total_counts.insert_count,
                plan.total_counts.edit_count,
                plan.total_counts.void_count,
                plan.total_counts.unchanged_count,
                event_count,
                first_sequence,
                last_sequence,
            ),
        )

        # Insert exactly four sheet rows in canonical order
        for order, sheet_name in enumerate(RAW_CONTRACT_REGISTRY.sheets):
            sheet = request.snapshot.sheets[sheet_name]
            p_counts = plan.per_sheet_counts[sheet_name]
            cur.execute(
                """
                INSERT INTO source_import_sheets (
                    import_id, sheet_order, sheet_name, snapshot_hash,
                    row_count, insert_count, edit_count, void_count, unchanged_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    request.import_id.bytes,
                    order,
                    sheet_name,
                    sheet.sheet_snapshot_hash,
                    sheet.row_count,
                    p_counts.insert_count,
                    p_counts.edit_count,
                    p_counts.void_count,
                    p_counts.unchanged_count,
                ),
            )

        # Process revisions and events for changed items
        cur_seq = stored_next_seq
        for item in changed_items:
            event_id = request.event_ids[item.stable_id]

            if item.action in (PlanAction.INSERT, PlanAction.EDIT):
                assert item.current_row is not None
                revision = (
                    1 if item.action == PlanAction.INSERT else item.planned_revision
                )
                assert revision is not None
                operation = "upsert"
                source_hash = item.current_source_hash
                raw_payload = encode_source_raw_row(item.current_row)
                raw_payload_base64 = base64.b64encode(raw_payload).decode("ascii")

                if revision == 1:
                    previous_version_hash = None
                else:
                    prior_rev = item.prior_revision
                    assert prior_rev is not None
                    cur.execute(
                        "SELECT version_hash FROM source_revisions "
                        "WHERE stable_id = ? AND revision = ?;",
                        (item.stable_id.bytes, prior_rev),
                    )
                    p_row = cur.fetchone()
                    if p_row is None:
                        raise SourceImportStoreError(
                            SourceImportStoreReason.INCONSISTENT_STATE
                        )
                    previous_version_hash = p_row[0]

                if item.sheet_name == BUSINESS_PARTIES_CONTRACT.sheet_name:
                    financial_date = None
                else:
                    raw_date_val = item.current_row.raw_values["date_raw"]
                    parsed_j_date = parse_canonical_jalali_date(raw_date_val)
                    financial_date = (
                        parsed_j_date.canonical_date
                        if parsed_j_date is not None
                        else None
                    )
            else:
                # VOID
                revision = item.planned_revision
                assert revision is not None
                operation = "void"
                source_hash = None
                raw_payload = None
                raw_payload_base64 = None

                prior_rev = item.prior_revision
                assert prior_rev is not None
                cur.execute(
                    "SELECT version_hash FROM source_revisions "
                    "WHERE stable_id = ? AND revision = ?;",
                    (item.stable_id.bytes, prior_rev),
                )
                p_row = cur.fetchone()
                if p_row is None:
                    raise SourceImportStoreError(
                        SourceImportStoreReason.INCONSISTENT_STATE
                    )
                previous_version_hash = p_row[0]

                if item.sheet_name == BUSINESS_PARTIES_CONTRACT.sheet_name:
                    financial_date = None
                else:
                    cur.execute(
                        """
                        SELECT raw_payload FROM source_revisions
                        WHERE stable_id = ? AND lifecycle = 'active'
                        ORDER BY revision DESC LIMIT 1;
                        """,
                        (item.stable_id.bytes,),
                    )
                    act_row = cur.fetchone()
                    if act_row is None or act_row[0] is None:
                        raise SourceImportStoreError(
                            SourceImportStoreReason.INCONSISTENT_STATE
                        )
                    prior_active_row = decode_source_raw_row(act_row[0])
                    raw_date_val = prior_active_row.raw_values["date_raw"]
                    parsed_j_date = parse_canonical_jalali_date(raw_date_val)
                    financial_date = (
                        parsed_j_date.canonical_date
                        if parsed_j_date is not None
                        else None
                    )

            event_array = [
                SOURCE_CHANGE_EVENT_VERSION,
                str(device_id).lower(),
                str(event_id).lower(),
                str(request.import_id).lower(),
                str(cur_seq),
                str(request.source_key.source_id).lower(),
                str(request.source_key.fiscal_year),
                item.sheet_name,
                str(item.stable_id).lower(),
                str(revision),
                operation,
                financial_date,
                source_hash,
                raw_payload_base64,
                previous_version_hash,
                request.observed_at_utc.isoformat(),
            ]

            event_bytes = json.dumps(
                event_array,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            payload_hash = hashlib.sha256(event_bytes).hexdigest()
            version_hash = payload_hash

            # Insert source_revisions
            cur.execute(
                """
                INSERT INTO source_revisions (
                    stable_id, revision, home_sheet, lifecycle, source_hash,
                    raw_payload, created_by_import_id, version_hash,
                    previous_version_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    item.stable_id.bytes,
                    revision,
                    item.sheet_name,
                    "active" if operation == "upsert" else "voided",
                    source_hash,
                    raw_payload,
                    request.import_id.bytes,
                    version_hash,
                    previous_version_hash,
                ),
            )

            # Insert change_events
            cur.execute(
                """
                INSERT INTO change_events (
                    sequence, event_id, device_id, import_id, source_id,
                    stable_id, revision, operation, fiscal_year, sheet_name,
                    financial_date, observed_at_utc, canonical_payload,
                    payload_hash, previous_version_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    cur_seq,
                    event_id.bytes,
                    device_id.bytes,
                    request.import_id.bytes,
                    request.source_key.source_id.bytes,
                    item.stable_id.bytes,
                    revision,
                    operation,
                    request.source_key.fiscal_year,
                    item.sheet_name,
                    financial_date,
                    request.observed_at_utc.isoformat(),
                    event_bytes,
                    payload_hash,
                    previous_version_hash,
                ),
            )
            cur_seq += 1

        # Advance membership for changed VOID items (preserving first_import_id)
        for item in changed_items:
            if item.action == PlanAction.VOID:
                assert item.planned_revision is not None
                cur.execute(
                    """
                    UPDATE source_memberships
                    SET revision = ?, last_import_id = ?
                    WHERE source_id = ? AND stable_id = ?;
                    """,
                    (
                        item.planned_revision,
                        request.import_id.bytes,
                        request.source_key.source_id.bytes,
                        item.stable_id.bytes,
                    ),
                )

        # 6. Upsert membership for every current row in the snapshot (R3 & R6)
        # Always update last_import_id, preserve first_import_id, never regress revision
        for row in request.snapshot.all_rows_by_id.values():
            plan_it = changed_items_by_id.get(row.stable_id)
            if plan_it is not None and plan_it.action in (
                PlanAction.INSERT,
                PlanAction.EDIT,
            ):
                row_rev = plan_it.planned_revision
            else:
                prior_st = prior_registry.identities.get(row.stable_id)
                if prior_st is None:
                    row_rev = 1
                else:
                    row_rev = prior_st.latest_revision

            assert row_rev is not None
            cur.execute(
                """
                INSERT INTO source_memberships (
                    source_id, stable_id, revision, first_import_id, last_import_id
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (source_id, stable_id) DO UPDATE SET
                    revision = MAX(source_memberships.revision, excluded.revision),
                    last_import_id = excluded.last_import_id;
                """,
                (
                    request.source_key.source_id.bytes,
                    row.stable_id.bytes,
                    row_rev,
                    request.import_id.bytes,
                    request.import_id.bytes,
                ),
            )

        # 7. Update source metadata and store generation
        cur.execute(
            """
            UPDATE source_bindings
            SET last_import_id = ?, last_file_sha256 = ?, last_observed_at_utc = ?
            WHERE source_id = ?;
            """,
            (
                request.import_id.bytes,
                request.file_sha256,
                request.observed_at_utc.isoformat(),
                request.source_key.source_id.bytes,
            ),
        )

        cur.execute(
            """
            UPDATE source_store_meta
            SET generation = generation + 1, next_sequence = ?
            WHERE singleton_id = 1;
            """,
            (next_seq_val,),
        )

        receipt = SourceImportReceipt(
            disposition=SourceImportDisposition.COMMITTED,
            import_id=request.import_id,
            source_id=request.source_key.source_id,
            fiscal_year=request.source_key.fiscal_year,
            base_generation=stored_gen,
            committed_generation=stored_gen + 1,
            file_sha256=request.file_sha256,
            total_row_count=request.snapshot.total_row_count,
            total_counts=plan.total_counts,
            per_sheet_counts=plan.per_sheet_counts,
            event_count=event_count,
            first_sequence=first_sequence,
            last_sequence=last_sequence,
        )

        connection.execute("COMMIT;")
        return receipt

    except BaseException as exc:
        _handle_transaction_failure(connection, exc)
        raise
