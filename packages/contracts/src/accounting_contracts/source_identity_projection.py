"""Pure active-source prior projection over trusted memberships (ADR-0016)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from accounting_contracts.raw_input_contracts import (
    BUSINESS_PARTIES_CONTRACT,
    ContractError,
)
from accounting_contracts.source_binding import (
    SourceBindingDisposition,
    SourceBindingInputError,
    SourceBindingKey,
    SourceBindingRegistry,
    resolve_source_binding,
)
from accounting_contracts.source_change_plan import (
    PriorIdentityRegistry,
    PriorIdentityState,
    ValidatedSourceWorkbookSnapshot,
)

SOURCE_IDENTITY_PROJECTION_VERSION = "source-identity-projection.v1"

__all__ = [
    "SOURCE_IDENTITY_PROJECTION_VERSION",
    "SourceIdentityProjectionReason",
    "SourceIdentityProjectionError",
    "SourceIdentityCatalog",
    "project_source_prior",
]


class SourceIdentityProjectionReason(StrEnum):
    INVALID_INPUT = "invalid_input"
    INCONSISTENT_CATALOG = "inconsistent_catalog"
    SOURCE_NOT_ACTIVE = "source_not_active"
    IDENTITY_RELOCATION = "identity_relocation"
    TRANSACTION_SOURCE_CONFLICT = "transaction_source_conflict"


class SourceIdentityProjectionError(ContractError):
    """A fixed public diagnostic; the cause may retain underlying diagnostics."""

    def __init__(self, reason: SourceIdentityProjectionReason) -> None:
        if type(reason) is not SourceIdentityProjectionReason:
            raise TypeError("Invalid source identity projection reason.")
        self.reason = reason
        super().__init__(
            {
                SourceIdentityProjectionReason.INVALID_INPUT: (
                    "Invalid projection input."
                ),
                SourceIdentityProjectionReason.INCONSISTENT_CATALOG: (
                    "Inconsistent source identity catalog."
                ),
                SourceIdentityProjectionReason.SOURCE_NOT_ACTIVE: (
                    "Source is not registered active."
                ),
                SourceIdentityProjectionReason.IDENTITY_RELOCATION: (
                    "Permanent identity cannot change its home sheet."
                ),
                SourceIdentityProjectionReason.TRANSACTION_SOURCE_CONFLICT: (
                    "Transaction belongs to another source."
                ),
            }[reason]
        )


@dataclass(frozen=True, slots=True)
class SourceIdentityCatalog:
    """Validated metadata, not proof of complete history or a durable commit."""

    source_registry: SourceBindingRegistry = field(repr=False)
    version: str = field(init=False, default=SOURCE_IDENTITY_PROJECTION_VERSION)
    identity_count: int = field(init=False)
    _global_heads: Mapping[uuid.UUID, PriorIdentityState] = field(
        init=False, repr=False, compare=False
    )
    _transaction_owners: Mapping[uuid.UUID, SourceBindingKey] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.source_registry, SourceBindingRegistry):
            raise SourceIdentityProjectionError(
                SourceIdentityProjectionReason.INVALID_INPUT
            )
        heads: dict[uuid.UUID, PriorIdentityState] = {}
        owners: dict[uuid.UUID, SourceBindingKey] = {}
        revisions: dict[tuple[uuid.UUID, int], PriorIdentityState] = {}
        for record in self.source_registry.records:
            for state in record.prior_registry.identities.values():
                stable_id = state.stable_id
                head = heads.get(stable_id)
                if head is not None and state.home_sheet != head.home_sheet:
                    raise SourceIdentityProjectionError(
                        SourceIdentityProjectionReason.INCONSISTENT_CATALOG
                    )
                if state.home_sheet != BUSINESS_PARTIES_CONTRACT.sheet_name:
                    owner = owners.setdefault(stable_id, record.key)
                    if owner != record.key:
                        raise SourceIdentityProjectionError(
                            SourceIdentityProjectionReason.INCONSISTENT_CATALOG
                        )
                revision_key = (stable_id, state.latest_revision)
                previous = revisions.setdefault(revision_key, state)
                if previous != state:
                    raise SourceIdentityProjectionError(
                        SourceIdentityProjectionReason.INCONSISTENT_CATALOG
                    )
                if head is None or state.latest_revision > head.latest_revision:
                    heads[stable_id] = state

        active = self.source_registry.active_record
        if active is not None:
            for state in active.prior_registry.identities.values():
                if state != heads[state.stable_id]:
                    raise SourceIdentityProjectionError(
                        SourceIdentityProjectionReason.INCONSISTENT_CATALOG
                    )
        # Only the indexes survive construction, not the revision-validation table.
        ordered = sorted(heads, key=lambda identity: identity.bytes)
        object.__setattr__(self, "identity_count", len(heads))
        object.__setattr__(
            self,
            "_global_heads",
            MappingProxyType({key: heads[key] for key in ordered}),
        )
        object.__setattr__(
            self,
            "_transaction_owners",
            MappingProxyType({key: owners[key] for key in ordered if key in owners}),
        )


def project_source_prior(
    key: SourceBindingKey,
    snapshot: ValidatedSourceWorkbookSnapshot,
    catalog: SourceIdentityCatalog,
) -> PriorIdentityRegistry:
    """Keep active membership; borrow heads only for present nonmember parties."""
    if (
        not isinstance(key, SourceBindingKey)
        or not isinstance(snapshot, ValidatedSourceWorkbookSnapshot)
        or not isinstance(catalog, SourceIdentityCatalog)
    ):
        raise SourceIdentityProjectionError(
            SourceIdentityProjectionReason.INVALID_INPUT
        )
    try:
        resolution = resolve_source_binding(key, catalog.source_registry)
    except SourceBindingInputError as exc:
        raise SourceIdentityProjectionError(
            SourceIdentityProjectionReason.INVALID_INPUT
        ) from exc
    if resolution.disposition is not SourceBindingDisposition.ACTIVE:
        raise SourceIdentityProjectionError(
            SourceIdentityProjectionReason.SOURCE_NOT_ACTIVE
        )
    assert resolution.prior_registry is not None
    projected = dict(resolution.prior_registry.identities)
    # Validated snapshots already store sheets and rows in the canonical order.
    for sheet in snapshot.sheets.values():
        for row in sheet.rows:
            head = catalog._global_heads.get(row.stable_id)
            if head is None:
                continue
            if head.home_sheet != row.sheet_name:
                raise SourceIdentityProjectionError(
                    SourceIdentityProjectionReason.IDENTITY_RELOCATION
                )
            if head.home_sheet != BUSINESS_PARTIES_CONTRACT.sheet_name:
                if catalog._transaction_owners[row.stable_id] != key:
                    raise SourceIdentityProjectionError(
                        SourceIdentityProjectionReason.TRANSACTION_SOURCE_CONFLICT
                    )
            elif row.stable_id not in projected:
                projected[row.stable_id] = head
    return PriorIdentityRegistry(
        MappingProxyType(
            {
                key: projected[key]
                for key in sorted(projected, key=lambda item: item.bytes)
            }
        )
    )
