"""Lossless, versioned Raw row bytes; no persistence or change-planning policy."""

from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal, localcontext
from enum import StrEnum
from types import MappingProxyType
from typing import Any, NoReturn

from accounting_contracts.canonical_hashing import SOURCE_HASH_VERSION
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
    RAW_SOURCE_CONTRACT_VERSION,
    ContractError,
)
from accounting_contracts.source_change_plan import ValidatedSourceRow

SOURCE_RAW_CODEC_VERSION: str = "source-raw-codec.v1"

_INTEGER = re.compile(r"(?:0|-?[1-9][0-9]*)")
_COEFFICIENT = re.compile(r"(?:0|[1-9][0-9]*)")


class SourceRawCodecReason(StrEnum):
    """Fixed classifications independent of the supplied Raw data."""

    INVALID_INPUT = "invalid_input"
    INVALID_PAYLOAD = "invalid_payload"


class SourceRawCodecError(ContractError):
    """Safe public message; an underlying diagnostic cause may contain data."""

    def __init__(self, reason: SourceRawCodecReason) -> None:
        if type(reason) is not SourceRawCodecReason:
            raise TypeError("Invalid Raw codec reason.")
        self.reason = reason
        super().__init__(
            {
                SourceRawCodecReason.INVALID_INPUT: "Invalid Raw codec input.",
                SourceRawCodecReason.INVALID_PAYLOAD: "Invalid Raw row payload.",
            }[reason]
        )


def _encode_scalar(value: object) -> list[Any]:
    if value is None:
        return ["null", None]
    if type(value) is str:
        return ["text", value]
    if type(value) is int:
        return ["int", str(value)]
    if type(value) is Decimal and value.is_finite():
        parts = value.as_tuple()
        return [
            "decimal",
            [str(parts.sign), "".join(map(str, parts.digits)), str(parts.exponent)],
        ]
    raise ValueError("Unsupported Raw scalar.")


def _encode_row(row: ValidatedSourceRow) -> bytes:
    fields = [
        [column.field_name, _encode_scalar(row.raw_values[column.field_name])]
        for column in RAW_CONTRACT_REGISTRY.sheets[row.sheet_name].raw_columns
    ]
    tree = [
        SOURCE_RAW_CODEC_VERSION,
        RAW_SOURCE_CONTRACT_VERSION,
        SOURCE_HASH_VERSION,
        row.sheet_name,
        row.canonical_uuid,
        row.source_hash,
        fields,
    ]
    return json.dumps(
        tree, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _array(value: object, length: int) -> list[Any]:
    if not isinstance(value, list) or len(value) != length:
        raise ValueError("Invalid Raw payload array.")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, str) or _INTEGER.fullmatch(value) is None:
        raise ValueError("Invalid Raw integer spelling.")
    return int(value)


def _decode_scalar(value: object) -> str | int | Decimal | None:
    tag, raw = _array(value, 2)
    if tag == "null" and raw is None:
        return None
    if tag == "text" and isinstance(raw, str):
        return raw
    if tag == "int":
        return _integer(raw)
    if tag == "decimal":
        sign, coefficient, exponent = _array(raw, 3)
        if (
            sign not in ("0", "1")
            or not isinstance(coefficient, str)
            or _COEFFICIENT.fullmatch(coefficient) is None
        ):
            raise ValueError("Invalid Raw decimal tuple.")
        parts = (int(sign), tuple(map(int, coefficient)), _integer(exponent))
        result = Decimal(parts)
        if result.as_tuple() != parts:
            raise ValueError("Noncanonical Raw decimal tuple.")
        return result
    raise ValueError("Invalid Raw scalar tag or value.")


def _reject_number(value: str) -> NoReturn:
    raise ValueError("JSON numeric tokens are not supported.")


def _reject_object(value: list[tuple[str, Any]]) -> NoReturn:
    raise ValueError("JSON objects are not supported.")


def _decode_row(tree: object) -> ValidatedSourceRow:
    codec, raw_version, hash_version, sheet, canonical_uuid, source_hash, fields = (
        _array(tree, 7)
    )
    if not all(
        isinstance(item, str)
        for item in (
            codec,
            raw_version,
            hash_version,
            sheet,
            canonical_uuid,
            source_hash,
        )
    ):
        raise ValueError("Invalid Raw envelope metadata.")
    if (codec, raw_version, hash_version) != (
        SOURCE_RAW_CODEC_VERSION,
        RAW_SOURCE_CONTRACT_VERSION,
        SOURCE_HASH_VERSION,
    ):
        raise ValueError("Unsupported Raw envelope version.")
    if sheet not in RAW_CONTRACT_REGISTRY.sheets:
        raise ValueError("Unknown Raw sheet.")
    columns = RAW_CONTRACT_REGISTRY.sheets[sheet].raw_columns
    entries = _array(fields, len(columns))
    values: dict[str, Any] = {}
    for column, entry in zip(columns, entries, strict=True):
        name, value = _array(entry, 2)
        if name != column.field_name:
            raise ValueError("Invalid Raw field order or membership.")
        values[column.field_name] = _decode_scalar(value)
    return ValidatedSourceRow(
        stable_id=uuid.UUID(canonical_uuid),
        canonical_uuid=canonical_uuid,
        sheet_name=sheet,
        raw_values=MappingProxyType(values),
        source_hash=source_hash,
    )


def encode_source_raw_row(row: ValidatedSourceRow) -> bytes:
    """Encode a validated row, preserving exact supported scalar representations."""
    if not isinstance(row, ValidatedSourceRow):
        raise SourceRawCodecError(SourceRawCodecReason.INVALID_INPUT)
    try:
        with localcontext():
            return _encode_row(row)
    except SourceRawCodecError:
        raise
    except Exception as exc:
        raise SourceRawCodecError(SourceRawCodecReason.INVALID_INPUT) from exc


def decode_source_raw_row(payload: bytes) -> ValidatedSourceRow:
    """Validate the exact wire format and rebuild immutable Raw using WP-04."""
    if type(payload) is not bytes:
        raise SourceRawCodecError(SourceRawCodecReason.INVALID_INPUT)
    try:
        with localcontext():
            tree = json.loads(
                payload.decode("utf-8"),
                parse_int=_reject_number,
                parse_float=_reject_number,
                parse_constant=_reject_number,
                object_pairs_hook=_reject_object,
            )
            row = _decode_row(tree)
            if _encode_row(row) != payload:
                raise ValueError("Noncanonical Raw payload bytes.")
            return row
    except SourceRawCodecError:
        raise
    except Exception as exc:
        raise SourceRawCodecError(SourceRawCodecReason.INVALID_PAYLOAD) from exc
