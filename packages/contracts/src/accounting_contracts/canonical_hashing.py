"""Deterministic canonical hashing and value normalization.

Implements ADR-0006 for calculating source_hash (per row) and
sheet_snapshot_hash (per sheet collection) using exact versioned UTF-8 JSON
byte serialization and SHA-256 digests.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from accounting_contracts.canonical_date import (
    normalize_digits,
    parse_canonical_jalali_date,
)
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
    RAW_SOURCE_CONTRACT_VERSION,
    ContractError,
    UnknownSheetError,
    ValueKind,
)

SOURCE_HASH_VERSION: str = "source-hash.v1"
SHEET_SNAPSHOT_HASH_VERSION: str = "sheet-snapshot-hash.v1"

HEX_DIGEST_64_REGEX = re.compile(r"^[0-9a-f]{64}$")
INTEGER_TOMAN_REGEX = re.compile(r"^[+-]?\d+$")
DECIMAL_REGEX = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")

TRANSACTIONAL_SHEETS_WITH_JALALI_DATE = frozenset(
    {"خرید-فروش", "دریافت-پرداخت", "ورود-خروج"}
)


class TypeTag(StrEnum):
    """Canonical value type tag for byte serialization."""

    RAW_TEXT = "raw_text"
    JALALI_DATE = "jalali_date"
    INTEGER_TOMAN = "integer_toman"
    DECIMAL = "decimal"


class CanonicalHashingError(ContractError):
    """Base exception for canonical serialization and hashing errors."""


class CanonicalValueError(CanonicalHashingError):
    """Raised when a field value violates canonical type or format rules."""


class CanonicalMappingError(CanonicalHashingError):
    """Raised when input mapping keys do not match the sheet raw contract."""


class InvalidUUIDError(CanonicalHashingError):
    """Raised when a record ID is not a valid RFC 4122 version 7 UUID."""


class InvalidHashError(CanonicalHashingError):
    """Raised when a source hash is not a 64-character lowercase hex digest."""


class DuplicateRecordIdError(CanonicalHashingError):
    """Raised when a duplicate record ID is encountered in snapshot rows."""


def canonicalize_value(type_tag: str | TypeTag, value: Any) -> Any:
    """Convert an accepted literal source value to deterministic canonical format.

    Rejects Boolean, Float, NaN, Infinity, exponent notation, and ambiguous formatting.
    """
    if value is None:
        return None

    tag = TypeTag(type_tag)

    if tag == TypeTag.RAW_TEXT:
        if isinstance(value, bool) or not isinstance(value, str):
            msg = f"raw_text field requires str or None, got {type(value).__name__}"
            raise CanonicalValueError(msg)
        return value

    if tag == TypeTag.JALALI_DATE:
        if isinstance(value, bool) or not isinstance(value, str):
            msg = f"jalali_date field requires str or None, got {type(value).__name__}"
            raise CanonicalValueError(msg)
        parsed = parse_canonical_jalali_date(value)
        return parsed.canonical_date if parsed is not None else None

    if tag == TypeTag.INTEGER_TOMAN:
        if isinstance(value, bool) or isinstance(value, float):
            msg = (
                "integer_toman rejects Boolean and Float types; "
                f"got {type(value).__name__}"
            )
            raise CanonicalValueError(msg)

        if isinstance(value, int):
            return str(value)

        if isinstance(value, Decimal):
            if not value.is_finite():
                raise CanonicalValueError(
                    f"integer_toman rejects non-finite Decimal: {value}"
                )
            if value != value.to_integral_value():
                raise CanonicalValueError(
                    f"integer_toman requires integral Decimal, got: {value}"
                )
            return str(int(value))

        if isinstance(value, str):
            cleaned = normalize_digits(value.strip())
            if not INTEGER_TOMAN_REGEX.match(cleaned):
                raise CanonicalValueError(
                    f"integer_toman text format invalid: '{value}'"
                )
            return str(int(cleaned))

        raise CanonicalValueError(
            f"Unsupported type for integer_toman: {type(value).__name__}"
        )

    if tag == TypeTag.DECIMAL:
        if isinstance(value, bool) or isinstance(value, float):
            msg = f"decimal rejects Boolean and Float types; got {type(value).__name__}"
            raise CanonicalValueError(msg)

        if isinstance(value, int):
            return str(value)

        if isinstance(value, Decimal):
            if not value.is_finite():
                raise CanonicalValueError(
                    f"decimal rejects non-finite Decimal: {value}"
                )
            dec_val = value
        elif isinstance(value, str):
            cleaned = normalize_digits(value.strip())
            if not DECIMAL_REGEX.match(cleaned):
                raise CanonicalValueError(f"decimal text format invalid: '{value}'")
            try:
                dec_val = Decimal(cleaned)
            except InvalidOperation as exc:
                raise CanonicalValueError(f"Invalid decimal string: '{value}'") from exc
            if not dec_val.is_finite():
                raise CanonicalValueError(
                    f"decimal rejects non-finite Decimal string: '{value}'"
                )
        else:
            raise CanonicalValueError(
                f"Unsupported type for decimal: {type(value).__name__}"
            )

        if dec_val == 0:
            return "0"

        # Format without exponent and strip trailing fractional zeros
        s = format(dec_val, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        if s in ("-0", "0"):
            return "0"
        return s

    raise CanonicalValueError(f"Unknown type tag '{type_tag}'")


@dataclass(frozen=True, slots=True)
class SourceHashResult:
    """Result of canonical source row serialization and SHA-256 digest calculation."""

    canonical_bytes: bytes
    canonical_json: str
    source_hash: str
    sheet_name: str
    field_triples: tuple[tuple[str, str, Any], ...]


@dataclass(frozen=True, slots=True)
class SheetSnapshotHashResult:
    """Result of canonical snapshot serialization and SHA-256 calculation."""

    canonical_bytes: bytes
    canonical_json: str
    snapshot_hash: str
    sheet_name: str
    row_count: int


def compute_source_hash(
    sheet_name: str, source_row: Mapping[str, Any]
) -> SourceHashResult:
    """Compute the deterministic canonical source_hash for a single row.

    Validates that the source_row mapping contains exactly the approved raw fields
    for the sheet (no missing, extra, derived or technical ID fields).
    """
    sheet_contract = RAW_CONTRACT_REGISTRY.get_sheet_contract(sheet_name)
    expected_fields = {col.field_name for col in sheet_contract.raw_columns}
    provided_fields = set(source_row.keys())

    if provided_fields != expected_fields:
        missing = expected_fields - provided_fields
        extra = provided_fields - expected_fields
        errors: list[str] = []
        if missing:
            errors.append(f"missing required raw fields: {sorted(missing)}")
        if extra:
            errors.append(f"unauthorized/extra fields: {sorted(extra)}")
        msg = f"Invalid source_row mapping for sheet '{sheet_name}': " + "; ".join(
            errors
        )
        raise CanonicalMappingError(msg)

    field_triples: list[list[Any]] = []
    triples_tuple: list[tuple[str, str, Any]] = []

    for col in sheet_contract.raw_columns:
        if (
            col.field_name == "date_raw"
            and sheet_name in TRANSACTIONAL_SHEETS_WITH_JALALI_DATE
        ):
            tag = TypeTag.JALALI_DATE
        elif col.value_kind == ValueKind.RAW_TEXT:
            tag = TypeTag.RAW_TEXT
        elif col.value_kind == ValueKind.INTEGER_TOMAN:
            tag = TypeTag.INTEGER_TOMAN
        elif col.value_kind == ValueKind.DECIMAL:
            tag = TypeTag.DECIMAL
        else:
            raise CanonicalValueError(
                f"Unsupported column value kind '{col.value_kind}' for hashing"
            )

        raw_val = source_row[col.field_name]
        canon_val = canonicalize_value(tag, raw_val)

        field_triples.append([col.field_name, tag.value, canon_val])
        triples_tuple.append((col.field_name, tag.value, canon_val))

    payload = [
        SOURCE_HASH_VERSION,
        RAW_SOURCE_CONTRACT_VERSION,
        sheet_name,
        field_triples,
    ]

    canonical_json = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    canonical_bytes = canonical_json.encode("utf-8")
    source_hash = hashlib.sha256(canonical_bytes).hexdigest()

    return SourceHashResult(
        canonical_bytes=canonical_bytes,
        canonical_json=canonical_json,
        source_hash=source_hash,
        sheet_name=sheet_name,
        field_triples=tuple(triples_tuple),
    )


def compute_sheet_snapshot_hash(
    sheet_name: str,
    row_snapshots: (
        Iterable[tuple[str | uuid.UUID, str]] | Mapping[str | uuid.UUID, str]
    ),
) -> SheetSnapshotHashResult:
    """Compute the deterministic canonical sheet_snapshot_hash for a sheet.

    Accepts an iterable or mapping of (record_id / party_id, source_hash) pairs.
    Sorts pairs deterministically by canonical UUIDv7 bytes.
    """
    if sheet_name not in RAW_CONTRACT_REGISTRY.sheets:
        raise UnknownSheetError(f"Unknown or unapproved sheet '{sheet_name}'")

    pairs_iterable: Iterable[tuple[str | uuid.UUID, str]]
    if isinstance(row_snapshots, Mapping):
        pairs_iterable = row_snapshots.items()
    else:
        pairs_iterable = row_snapshots

    seen_uuids: set[bytes] = set()
    normalized_pairs: list[tuple[bytes, str, str]] = []

    for raw_id, source_hash in pairs_iterable:
        if isinstance(raw_id, uuid.UUID):
            parsed_uuid = raw_id
        elif isinstance(raw_id, str):
            try:
                parsed_uuid = uuid.UUID(raw_id.strip())
            except (ValueError, AttributeError) as exc:
                raise InvalidUUIDError(f"Malformed UUID string: '{raw_id}'") from exc
        else:
            raise InvalidUUIDError(
                f"Expected UUID or str for record ID, got {type(raw_id).__name__}"
            )

        if parsed_uuid.version != 7:
            msg = (
                f"UUID must be version 7, got version "
                f"{parsed_uuid.version} ('{parsed_uuid}')"
            )
            raise InvalidUUIDError(msg)

        if not isinstance(source_hash, str) or not HEX_DIGEST_64_REGEX.match(
            source_hash
        ):
            raise InvalidHashError(
                f"Invalid source_hash '{source_hash}'. "
                "Must be a 64-character lowercase hex SHA-256 string."
            )

        uuid_bytes = parsed_uuid.bytes
        if uuid_bytes in seen_uuids:
            raise DuplicateRecordIdError(
                f"Duplicate record ID encountered: '{parsed_uuid}'"
            )
        seen_uuids.add(uuid_bytes)

        canon_uuid_str = str(parsed_uuid).lower()
        normalized_pairs.append((uuid_bytes, canon_uuid_str, source_hash))

    # Sort pairs by UUID binary bytes for guaranteed platform-independent sorting
    normalized_pairs.sort(key=lambda item: item[0])

    pairs_payload = [
        [canon_uuid_str, s_hash] for _, canon_uuid_str, s_hash in normalized_pairs
    ]

    payload = [
        SHEET_SNAPSHOT_HASH_VERSION,
        RAW_SOURCE_CONTRACT_VERSION,
        sheet_name,
        pairs_payload,
    ]

    canonical_json = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    canonical_bytes = canonical_json.encode("utf-8")
    snapshot_hash = hashlib.sha256(canonical_bytes).hexdigest()

    return SheetSnapshotHashResult(
        canonical_bytes=canonical_bytes,
        canonical_json=canonical_json,
        snapshot_hash=snapshot_hash,
        sheet_name=sheet_name,
        row_count=len(normalized_pairs),
    )
