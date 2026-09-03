"""Pure annual source routing over trusted registry metadata (ADR-0014)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from accounting_contracts.canonical_date import (
    CanonicalDateError,
    parse_canonical_jalali_date,
)
from accounting_contracts.raw_input_contracts import ContractError
from accounting_contracts.source_change_plan import PriorIdentityRegistry

SOURCE_BINDING_VERSION = "source-binding.v1"

__all__ = [
    "SOURCE_BINDING_VERSION",
    "SourceBindingInputError",
    "SourceBindingKey",
    "SourceBindingState",
    "SourceBindingRecord",
    "SourceBindingRegistry",
    "SourceBindingDisposition",
    "SourceBindingResolution",
    "resolve_source_binding",
]


class SourceBindingInputError(ContractError):
    """Invalid binding metadata; public messages contain no supplied values."""


class SourceBindingState(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class SourceBindingDisposition(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    UNREGISTERED = "unregistered"


@dataclass(frozen=True, slots=True)
class SourceBindingKey:
    """Logical annual key, without attestation of physical workbook identity."""

    source_id: uuid.UUID
    fiscal_year: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_id, uuid.UUID)
            or self.source_id.variant != uuid.RFC_4122
            or self.source_id.version != 7
        ):
            raise SourceBindingInputError("Invalid source ID.")
        if type(self.fiscal_year) is not int:
            raise SourceBindingInputError("Invalid source fiscal year.")
        try:
            parsed = parse_canonical_jalali_date(f"{self.fiscal_year:04d}/01/01")
        except (CanonicalDateError, ValueError, OverflowError):
            raise SourceBindingInputError("Invalid source fiscal year.") from None
        if parsed is None or parsed.fiscal_year != self.fiscal_year:
            raise SourceBindingInputError("Invalid source fiscal year.")


@dataclass(frozen=True, slots=True)
class SourceBindingRecord:
    """Trusted historical view; construction does not prove a final import."""

    key: SourceBindingKey
    state: SourceBindingState
    prior_registry: PriorIdentityRegistry = field(repr=False)
    final_file_sha256: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.key, SourceBindingKey):
            raise SourceBindingInputError("Invalid source binding key.")
        if type(self.state) is not SourceBindingState:
            raise SourceBindingInputError("Invalid source binding state.")
        if not isinstance(self.prior_registry, PriorIdentityRegistry):
            raise SourceBindingInputError("Invalid source prior registry.")
        digest = self.final_file_sha256
        if self.state is SourceBindingState.ACTIVE:
            if digest is not None:
                raise SourceBindingInputError("Active source cannot have a final hash.")
        elif (
            type(digest) is not str
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise SourceBindingInputError("Invalid archived source final hash.")


@dataclass(frozen=True, slots=True, init=False)
class SourceBindingRegistry:
    """Immutable annual records and a source-ID index; nested rows are untouched."""

    records: tuple[SourceBindingRecord, ...]
    active_record: SourceBindingRecord | None
    _by_source_id: Mapping[uuid.UUID, SourceBindingRecord] = field(
        repr=False, compare=False
    )

    def __init__(self, records: Iterable[SourceBindingRecord]) -> None:
        if not isinstance(records, Iterable) or isinstance(
            records, (str, bytes, bytearray, Mapping)
        ):
            raise SourceBindingInputError("Invalid source binding records.")
        by_id: dict[uuid.UUID, SourceBindingRecord] = {}
        years: set[int] = set()
        active = None
        for record in records:
            if not isinstance(record, SourceBindingRecord):
                raise SourceBindingInputError("Invalid source binding record.")
            if record.key.source_id in by_id:
                raise SourceBindingInputError("Duplicate source ID.")
            if record.key.fiscal_year in years:
                raise SourceBindingInputError("Duplicate source fiscal year.")
            if record.state is SourceBindingState.ACTIVE:
                if active is not None:
                    raise SourceBindingInputError("Multiple active sources.")
                active = record
            by_id[record.key.source_id] = record
            years.add(record.key.fiscal_year)
        ordered = tuple(
            sorted(
                by_id.values(), key=lambda r: (r.key.fiscal_year, r.key.source_id.bytes)
            )
        )
        object.__setattr__(self, "records", ordered)
        object.__setattr__(self, "active_record", active)
        object.__setattr__(self, "_by_source_id", MappingProxyType(by_id))


@dataclass(frozen=True, slots=True, init=False)
class SourceBindingResolution:
    """Computed routing only; ACTIVE grants no import or financial permission."""

    key: SourceBindingKey
    registry: SourceBindingRegistry = field(repr=False)
    disposition: SourceBindingDisposition
    record: SourceBindingRecord | None
    prior_registry: PriorIdentityRegistry | None = field(repr=False)

    def __init__(self, key: SourceBindingKey, registry: SourceBindingRegistry) -> None:
        if not isinstance(key, SourceBindingKey):
            raise SourceBindingInputError("Invalid source binding key.")
        if not isinstance(registry, SourceBindingRegistry):
            raise SourceBindingInputError("Invalid source binding registry.")
        record = registry._by_source_id.get(key.source_id)
        prior = None
        if record is None:
            disposition = SourceBindingDisposition.UNREGISTERED
        else:
            if record.key.fiscal_year != key.fiscal_year:
                raise SourceBindingInputError("Registered source fiscal year mismatch.")
            if record.state is SourceBindingState.ACTIVE:
                disposition = SourceBindingDisposition.ACTIVE
                prior = record.prior_registry
            else:
                disposition = SourceBindingDisposition.ARCHIVED
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "registry", registry)
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(self, "record", record)
        object.__setattr__(self, "prior_registry", prior)


def resolve_source_binding(
    key: SourceBindingKey, registry: SourceBindingRegistry
) -> SourceBindingResolution:
    """Select only the exact registered active source's supplied prior view."""
    return SourceBindingResolution(key, registry)
