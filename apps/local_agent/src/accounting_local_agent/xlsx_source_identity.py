"""Read the annual source key and Raw from one owned XLSX lease (ADR-0015)."""

from __future__ import annotations

import re
import uuid
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from accounting_contracts import SourceBindingInputError, SourceBindingKey
from lxml import etree  # type: ignore[import-untyped]

from accounting_local_agent import xlsx_source_reader as reader
from accounting_local_agent.xlsx_snapshot_acquisition import (
    XlsxSnapshotStorageError,
    open_stable_xlsx_snapshot,
)
from accounting_local_agent.xlsx_source_reader import (
    XlsxPackageError,
    XlsxSourceReadError,
    XlsxSourceReadResult,
)

XLSX_SOURCE_IDENTITY_VERSION = "xlsx-source-identity.v1"
XLSX_SOURCE_IDENTITY_PROPERTY_NAME = "AccountingBot.SourceIdentity"
XLSX_SOURCE_IDENTITY_MAX_METADATA_BYTES = 1_048_576

__all__ = [
    "XLSX_SOURCE_IDENTITY_VERSION",
    "XLSX_SOURCE_IDENTITY_PROPERTY_NAME",
    "XLSX_SOURCE_IDENTITY_MAX_METADATA_BYTES",
    "XlsxSourceIdentityReason",
    "XlsxSourceIdentityError",
    "IdentifiedXlsxSource",
    "read_identified_xlsx_source",
]

_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
_FMTID = "{d5cdd505-2e9c-101b-9397-08002b2cf9ae}"
_FAMILIES = {
    (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
        "custom-properties"
    ): (
        "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties",
        "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
    ),
    "http://purl.oclc.org/ooxml/officeDocument/relationships/customProperties": (
        "http://purl.oclc.org/ooxml/officeDocument/customProperties",
        "http://purl.oclc.org/ooxml/officeDocument/docPropsVTypes",
    ),
}
_PATH = re.compile(r"/?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*")
_VALUE = re.compile(
    r"xlsx-source-identity\.v1\|"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"\|([0-9]{4})"
)


class XlsxSourceIdentityReason(StrEnum):
    INVALID_INPUT = "invalid_input"
    MISSING_MARKER = "missing_marker"
    AMBIGUOUS_MARKER = "ambiguous_marker"
    INVALID_MARKER = "invalid_marker"
    METADATA_LIMIT_EXCEEDED = "metadata_limit_exceeded"


class XlsxSourceIdentityError(XlsxSourceReadError):
    """Fixed public messages; diagnostic causes retain their original objects."""

    def __init__(self, reason: XlsxSourceIdentityReason) -> None:
        if type(reason) is not XlsxSourceIdentityReason:
            raise XlsxSourceIdentityError(XlsxSourceIdentityReason.INVALID_INPUT)
        super().__init__(reason)
        self.reason = reason
        self.reason_code = "XLSX_SOURCE_IDENTITY_" + reason.name
        self.args = (f"XLSX source identity error: {self.reason_code}",)


@dataclass(frozen=True, slots=True)
class IdentifiedXlsxSource:
    """Data representation, not authentication or import/commit authorization."""

    key: SourceBindingKey
    read_result: XlsxSourceReadResult = field(repr=False)
    file_sha256: str
    byte_count: int
    version: str = field(default=XLSX_SOURCE_IDENTITY_VERSION, init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.key, SourceBindingKey)
            or not isinstance(self.read_result, XlsxSourceReadResult)
            or type(self.file_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.file_sha256) is None
            or type(self.byte_count) is not int
            or self.byte_count <= 0
        ):
            raise XlsxSourceIdentityError(XlsxSourceIdentityReason.INVALID_INPUT)


class _Closable(Protocol):
    def close(self) -> None: ...


def _zip_error(error: BaseException) -> BaseException:
    converted: BaseException
    if isinstance(error, zipfile.BadZipFile):
        converted = XlsxPackageError(reader.REASON_PACKAGE_CORRUPT_ZIP)
    elif isinstance(error, OSError):
        converted = XlsxSnapshotStorageError()
    else:
        return error
    converted.__cause__ = error
    return converted


def _zip_call[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except (zipfile.BadZipFile, OSError) as error:
        raise _zip_error(error) from error


@contextmanager
def _closing[T: _Closable](resource: T) -> Iterator[T]:
    # A normal `with ZipFile` would mask an independent parsing failure if
    # close also failed. Apply this to member streams as well as the archive.
    try:
        yield resource
    except BaseException as error:
        try:
            resource.close()
        except BaseException as close_error:
            raise BaseExceptionGroup(
                "XLSX reading and closing failed", [error, _zip_error(close_error)]
            ) from None
        raise
    else:
        _zip_call(resource.close)


def _invalid() -> XlsxSourceIdentityError:
    return XlsxSourceIdentityError(XlsxSourceIdentityReason.INVALID_MARKER)


def _metadata_xml(zf: zipfile.ZipFile, name: str) -> etree._Element:
    info = zf.getinfo(name)
    limit = XLSX_SOURCE_IDENTITY_MAX_METADATA_BYTES
    if info.file_size > limit:
        raise XlsxSourceIdentityError(XlsxSourceIdentityReason.METADATA_LIMIT_EXCEEDED)
    with _closing(_zip_call(lambda: zf.open(info, "r"))) as stream:
        data = _zip_call(lambda: stream.read(limit + 1))
        if len(data) > limit:
            raise XlsxSourceIdentityError(
                XlsxSourceIdentityReason.METADATA_LIMIT_EXCEEDED
            )
        parser = etree.XMLParser(
            resolve_entities=False,
            load_dtd=False,
            dtd_validation=False,
            attribute_defaults=False,
            no_network=True,
            recover=False,
            huge_tree=False,
            remove_comments=True,
            remove_pis=True,
        )
        try:
            root = etree.fromstring(data, parser=parser)
        except etree.XMLSyntaxError as error:
            raise _invalid() from error
        if root.getroottree().docinfo.doctype:
            raise _invalid()
        return root


def _check_aliases(names: set[str], selected: str) -> None:
    if any(name != selected and name.lower() == selected.lower() for name in names):
        raise XlsxPackageError(reader.REASON_PACKAGE_DUPLICATE_ZIP_ENTRY)


def _select_property_part(
    zf: zipfile.ZipFile, names: set[str]
) -> tuple[str, tuple[str, str]]:
    for name, reason in (
        ("_rels/.rels", reader.REASON_PACKAGE_MISSING_ROOT_RELS),
        ("[Content_Types].xml", reader.REASON_PACKAGE_MISSING_CONTENT_TYPES),
    ):
        _check_aliases(names, name)
        if name not in names:
            raise XlsxPackageError(reason)
    root = _metadata_xml(zf, "_rels/.rels")
    if root.tag != f"{{{_RELS}}}Relationships":
        raise _invalid()
    candidates = []
    ids = set()
    for relation in root:
        if relation.tag != f"{{{_RELS}}}Relationship" or not relation.get("Id"):
            raise _invalid()
        if relation.get("Id") in ids:
            raise XlsxPackageError(reader.REASON_PACKAGE_DUPLICATE_REL_ID)
        ids.add(relation.get("Id"))
        if relation.get("Type") in _FAMILIES:
            candidates.append(relation)
    if not candidates:
        raise XlsxSourceIdentityError(XlsxSourceIdentityReason.MISSING_MARKER)
    if len(candidates) != 1:
        raise XlsxSourceIdentityError(XlsxSourceIdentityReason.AMBIGUOUS_MARKER)
    selected = candidates[0]
    target = selected.get("Target", "")
    if (
        selected.get("TargetMode", "Internal") != "Internal"
        or _PATH.fullmatch(target) is None
        or any(segment in {".", ".."} for segment in target.split("/"))
    ):
        raise _invalid()
    part = target.removeprefix("/")
    _check_aliases(names, part)
    if part not in names:
        raise _invalid()
    content_types = _metadata_xml(zf, "[Content_Types].xml")
    if content_types.tag != f"{{{_TYPES}}}Types":
        raise _invalid()
    overrides = [
        node
        for node in content_types
        if node.tag == f"{{{_TYPES}}}Override" and node.get("PartName") == "/" + part
    ]
    if len(overrides) != 1 or overrides[0].get("ContentType") != _CONTENT_TYPE:
        raise _invalid()
    return part, _FAMILIES[selected.get("Type", "")]


def _parse_marker(value: str) -> SourceBindingKey:
    match = _VALUE.fullmatch(value)
    if match is None:
        raise _invalid()
    try:
        return SourceBindingKey(uuid.UUID(match[1]), int(match[2]))
    except (ValueError, SourceBindingInputError) as error:
        raise _invalid() from error


def _read_key(zf: zipfile.ZipFile) -> SourceBindingKey:
    entries = zf.namelist()
    names = set(entries)
    if len(names) != len(entries):
        raise XlsxPackageError(reader.REASON_PACKAGE_DUPLICATE_ZIP_ENTRY)
    part, (properties_ns, values_ns) = _select_property_part(zf, names)
    root = _metadata_xml(zf, part)
    if root.tag != f"{{{properties_ns}}}Properties":
        raise _invalid()
    ids: set[int] = set()
    candidates = []
    for prop in root:
        if prop.tag != f"{{{properties_ns}}}property" or not prop.get("name"):
            raise _invalid()
        pid = prop.get("pid", "")
        if re.fullmatch(r"[0-9]+", pid) is None:
            raise _invalid()
        digits = pid.lstrip("0")
        if not digits or len(digits) > 10:
            raise _invalid()
        number = int(digits)
        if not 2 <= number <= 2147483647 or number in ids:
            raise _invalid()
        ids.add(number)
        if prop.get("name", "").lower() == XLSX_SOURCE_IDENTITY_PROPERTY_NAME.lower():
            candidates.append(prop)
    if not candidates:
        raise XlsxSourceIdentityError(XlsxSourceIdentityReason.MISSING_MARKER)
    if len(candidates) != 1:
        raise XlsxSourceIdentityError(XlsxSourceIdentityReason.AMBIGUOUS_MARKER)
    prop = candidates[0]
    if (
        prop.get("name") != XLSX_SOURCE_IDENTITY_PROPERTY_NAME
        or prop.get("fmtid", "").lower() != _FMTID
        or any(etree.QName(attr).localname == "linkTarget" for attr in prop.attrib)
        or len(prop) != 1
        or (prop.text or "").strip()
    ):
        raise _invalid()
    value = prop[0]
    if (
        value.tag != f"{{{values_ns}}}lpwstr"
        or len(value)
        or (value.tail or "").strip()
    ):
        raise _invalid()
    return _parse_marker(value.text or "")


def read_identified_xlsx_source(
    source_path: Path,
    *,
    snapshot_root: Path,
    observation_interval_seconds: float,
) -> IdentifiedXlsxSource:
    """Acquire, read and finish cleanup before delivering identity with Raw."""
    with open_stable_xlsx_snapshot(
        source_path, snapshot_root, observation_interval_seconds
    ) as lease:
        with _closing(
            _zip_call(lambda: zipfile.ZipFile(lease.snapshot_path, "r"))
        ) as zf:
            key = _read_key(zf)
            try:
                raw_result = reader._read_xlsx_from_zip(zf)
            except zipfile.BadZipFile as error:
                # Match the standalone Reader's corruption boundary even though
                # this adapter deliberately reuses its open-package helper.
                raise _zip_error(error) from error
            result = IdentifiedXlsxSource(
                key, raw_result, lease.file_sha256, lease.byte_count
            )
    return result
