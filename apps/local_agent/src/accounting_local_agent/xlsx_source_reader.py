"""Streaming read-only XLSX source reader and physical row tracker.

Implements ADR-0008 and WP-05 for reading physical Excel .xlsx packages using
standard zipfile and streaming lxml, extracting literal source inputs from the
four approved sheets, excluding formulas/caches, evaluating row activity,
validating headers and UUIDv7 identifiers, and returning a validated source
workbook snapshot with separate physical row locations.
"""

from __future__ import annotations

import bisect
import posixpath
import re
import uuid
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from accounting_contracts.canonical_date import (
    CanonicalDateError,
    InvalidDateError,
    parse_canonical_jalali_date,
)
from accounting_contracts.canonical_hashing import (
    CanonicalValueError,
    TypeTag,
    canonicalize_value,
)
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
    RawColumnContract,
    RawSheetContract,
    ValueKind,
)
from accounting_contracts.source_change_plan import (
    DuplicateIdentityError,
    InvalidIdentityError,
    SourceChangePlanError,
    SourceRowInput,
    SourceSheetInput,
    ValidatedSourceWorkbookSnapshot,
    _parse_and_validate_uuid7,
    build_source_workbook_snapshot,
)
from lxml import etree  # type: ignore[import-untyped]

XLSX_SOURCE_READER_VERSION: str = "xlsx-source-reader.v1"
MIN_PHYSICAL_ROW: int = 2
MAX_PHYSICAL_ROW: int = 1_048_576
MAX_EXCEL_COLUMN: int = 16_384

# XML Namespaces
_NS_SPREADSHEETML_TRANS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_SPREADSHEETML_STRICT = "http://purl.oclc.org/ooxml/spreadsheetml/main"
_NS_REL_PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_REL_OFFICE_TRANS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_NS_REL_OFFICE_STRICT = "http://purl.oclc.org/ooxml/officeDocument/relationships"
_NS_CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"

_VALID_SPREADSHEETML_NAMESPACES = (
    _NS_SPREADSHEETML_TRANS,
    _NS_SPREADSHEETML_STRICT,
)

_VALID_WORKBOOK_CONTENT_TYPES = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.main+xml",
)
_VALID_WORKSHEET_CONTENT_TYPES = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
)
_FORBIDDEN_WORKBOOK_CONTENT_TYPES = (
    "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
    "application/vnd.ms-excel.sheet.binary.macroEnabled.main",
)

_REL_TYPE_OFFICE_DOC = (
    f"{_NS_REL_OFFICE_TRANS}/officeDocument",
    f"{_NS_REL_OFFICE_STRICT}/officeDocument",
)
_REL_TYPE_WORKSHEET = (
    f"{_NS_REL_OFFICE_TRANS}/worksheet",
    f"{_NS_REL_OFFICE_STRICT}/worksheet",
)
_REL_TYPE_SHARED_STRINGS = (
    f"{_NS_REL_OFFICE_TRANS}/sharedStrings",
    f"{_NS_REL_OFFICE_STRICT}/sharedStrings",
)

_TAG_WORKSHEET = tuple(f"{{{ns}}}worksheet" for ns in _VALID_SPREADSHEETML_NAMESPACES)
_TAG_SHEET_DATA = tuple(f"{{{ns}}}sheetData" for ns in _VALID_SPREADSHEETML_NAMESPACES)
_TAG_ROW = tuple(f"{{{ns}}}row" for ns in _VALID_SPREADSHEETML_NAMESPACES)
_TAG_C = tuple(f"{{{ns}}}c" for ns in _VALID_SPREADSHEETML_NAMESPACES)
_TAG_F = tuple(f"{{{ns}}}f" for ns in _VALID_SPREADSHEETML_NAMESPACES)
_TAG_V = tuple(f"{{{ns}}}v" for ns in _VALID_SPREADSHEETML_NAMESPACES)
_TAG_IS = tuple(f"{{{ns}}}is" for ns in _VALID_SPREADSHEETML_NAMESPACES)
_TAG_SI = tuple(f"{{{ns}}}si" for ns in _VALID_SPREADSHEETML_NAMESPACES)
_TAG_T = tuple(f"{{{ns}}}t" for ns in _VALID_SPREADSHEETML_NAMESPACES)
_TAG_R = tuple(f"{{{ns}}}r" for ns in _VALID_SPREADSHEETML_NAMESPACES)

_TAG_T_SET = set(_TAG_T) | {"t"}
_TAG_R_SET = set(_TAG_R) | {"r"}
_TAG_IGNORE_IS = {
    f"{{{ns}}}{name}"
    for ns in _VALID_SPREADSHEETML_NAMESPACES
    for name in ("rPh", "phoneticPr")
} | {"rPh", "phoneticPr"}
_TAG_IGNORE_R = {
    f"{{{ns}}}{name}"
    for ns in _VALID_SPREADSHEETML_NAMESPACES
    for name in ("rPr", "rPh", "phoneticPr")
} | {"rPr", "rPh", "phoneticPr"}

# Strict fullmatch regexes (R5)
_CELL_REF_STRICT_REGEX = re.compile(r"^[A-Za-z]{1,3}[1-9][0-9]*$")
_ROW_NUM_STRICT_REGEX = re.compile(r"^[1-9][0-9]*$")
_OOXML_ESCAPE_REGEX = re.compile(r"_x([0-9a-fA-F]{4})_")
_NUMERIC_XML_STRICT_REGEX = re.compile(
    r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"
)
_SST_INDEX_STRICT_REGEX = re.compile(r"^(?:0|[1-9][0-9]*)$")

# --- Machine-Readable Reason Codes (R7) ---
REASON_PACKAGE_NOT_FOUND = "XLSX_PACKAGE_NOT_FOUND"
REASON_PACKAGE_NOT_FILE = "XLSX_PACKAGE_NOT_FILE"
REASON_PACKAGE_CORRUPT_ZIP = "XLSX_PACKAGE_CORRUPT_ZIP"
REASON_PACKAGE_DUPLICATE_ZIP_ENTRY = "XLSX_PACKAGE_DUPLICATE_ZIP_ENTRY"
REASON_PACKAGE_MISSING_CONTENT_TYPES = "XLSX_PACKAGE_MISSING_CONTENT_TYPES"
REASON_PACKAGE_INVALID_CONTENT_TYPES = "XLSX_PACKAGE_INVALID_CONTENT_TYPES"
REASON_PACKAGE_FORBIDDEN_CONTENT_TYPE = "XLSX_PACKAGE_FORBIDDEN_CONTENT_TYPE"
REASON_PACKAGE_MISSING_ROOT_RELS = "XLSX_PACKAGE_MISSING_ROOT_RELS"
REASON_PACKAGE_MALFORMED_RELS = "XLSX_PACKAGE_MALFORMED_RELS"
REASON_PACKAGE_DUPLICATE_REL_ID = "XLSX_PACKAGE_DUPLICATE_REL_ID"
REASON_PACKAGE_INVALID_REL_TARGET = "XLSX_PACKAGE_INVALID_REL_TARGET"
REASON_PACKAGE_TARGET_ESCAPES_ROOT = "XLSX_PACKAGE_TARGET_ESCAPES_ROOT"
REASON_PACKAGE_MISSING_WORKBOOK = "XLSX_PACKAGE_MISSING_WORKBOOK"
REASON_PACKAGE_AMBIGUOUS_WORKBOOK = "XLSX_PACKAGE_AMBIGUOUS_WORKBOOK"
REASON_PACKAGE_MISSING_WORKSHEET_PART = "XLSX_PACKAGE_MISSING_WORKSHEET_PART"
REASON_PACKAGE_MISSING_SHARED_STRINGS_PART = "XLSX_PACKAGE_MISSING_SHARED_STRINGS_PART"
REASON_PACKAGE_AMBIGUOUS_SHARED_STRINGS = "XLSX_PACKAGE_AMBIGUOUS_SHARED_STRINGS"

REASON_STRUCTURE_MALFORMED_XML = "XLSX_STRUCTURE_MALFORMED_XML"
REASON_STRUCTURE_INVALID_ROOT = "XLSX_STRUCTURE_INVALID_ROOT"
REASON_STRUCTURE_MISSING_SHEET_DECLARATION = "XLSX_STRUCTURE_MISSING_SHEET_DECLARATION"
REASON_STRUCTURE_DUPLICATE_SHEET_DECLARATION = (
    "XLSX_STRUCTURE_DUPLICATE_SHEET_DECLARATION"
)
REASON_STRUCTURE_MISSING_APPROVED_SHEETS = "XLSX_STRUCTURE_MISSING_APPROVED_SHEETS"
REASON_STRUCTURE_AMBIGUOUS_SHEETS = "XLSX_STRUCTURE_AMBIGUOUS_SHEETS"
REASON_STRUCTURE_DUPLICATE_WORKSHEET_PART = "XLSX_STRUCTURE_DUPLICATE_WORKSHEET_PART"
REASON_STRUCTURE_INVALID_WORKSHEET_REL_TYPE = (
    "XLSX_STRUCTURE_INVALID_WORKSHEET_REL_TYPE"
)
REASON_STRUCTURE_INVALID_SHEET_HIERARCHY = "XLSX_STRUCTURE_INVALID_SHEET_HIERARCHY"
REASON_STRUCTURE_INVALID_ROW_NUMBER = "XLSX_STRUCTURE_INVALID_ROW_NUMBER"
REASON_STRUCTURE_ROW_OUT_OF_BOUNDS = "XLSX_STRUCTURE_ROW_OUT_OF_BOUNDS"
REASON_STRUCTURE_DUPLICATE_ROW = "XLSX_STRUCTURE_DUPLICATE_ROW"
REASON_STRUCTURE_INVALID_CELL_REF = "XLSX_STRUCTURE_INVALID_CELL_REF"
REASON_STRUCTURE_CELL_ROW_MISMATCH = "XLSX_STRUCTURE_CELL_ROW_MISMATCH"
REASON_STRUCTURE_DUPLICATE_CELL_REF = "XLSX_STRUCTURE_DUPLICATE_CELL_REF"
REASON_STRUCTURE_INVALID_VERSION = "XLSX_STRUCTURE_INVALID_VERSION"
REASON_STRUCTURE_INVALID_SNAPSHOT_TYPE = "XLSX_STRUCTURE_INVALID_SNAPSHOT_TYPE"
REASON_STRUCTURE_LOCATION_IDENTITY_MISMATCH = (
    "XLSX_STRUCTURE_LOCATION_IDENTITY_MISMATCH"
)
REASON_STRUCTURE_LOCATION_SHEET_MISMATCH = "XLSX_STRUCTURE_LOCATION_SHEET_MISMATCH"
REASON_STRUCTURE_DUPLICATE_LOCATION_ROW = "XLSX_STRUCTURE_DUPLICATE_LOCATION_ROW"
REASON_STRUCTURE_UNKNOWN_LOCATION_SHEET = "XLSX_STRUCTURE_UNKNOWN_LOCATION_SHEET"

REASON_HEADER_MISSING_ROW = "XLSX_HEADER_MISSING_ROW"
REASON_HEADER_TEXT_MISMATCH = "XLSX_HEADER_TEXT_MISMATCH"
REASON_HEADER_FORMULA_BACKED = "XLSX_HEADER_FORMULA_BACKED"

REASON_CELL_INCOMPATIBLE_CHILDREN = "XLSX_CELL_INCOMPATIBLE_CHILDREN"
REASON_CELL_UNKNOWN_TYPE = "XLSX_CELL_UNKNOWN_TYPE"
REASON_CELL_BOOLEAN_REJECTED = "XLSX_CELL_BOOLEAN_REJECTED"
REASON_CELL_ERROR_REJECTED = "XLSX_CELL_ERROR_REJECTED"
REASON_CELL_DATE_TYPE_REJECTED = "XLSX_CELL_DATE_TYPE_REJECTED"
REASON_CELL_NUMERIC_XML_IN_TEXT_FIELD = "XLSX_CELL_NUMERIC_XML_IN_TEXT_FIELD"
REASON_CELL_INVALID_NUMERIC_LEXEME = "XLSX_CELL_INVALID_NUMERIC_LEXEME"
REASON_CELL_INVALID_SST_INDEX = "XLSX_CELL_INVALID_SST_INDEX"
REASON_CELL_SST_INDEX_OUT_OF_RANGE = "XLSX_CELL_SST_INDEX_OUT_OF_RANGE"
REASON_CELL_UNPAIRED_SURROGATE = "XLSX_CELL_UNPAIRED_SURROGATE"

REASON_FORMULA_COVERAGE_MISSING_REF = "XLSX_FORMULA_COVERAGE_MISSING_REF"
REASON_FORMULA_COVERAGE_MISSING_ANCHOR = "XLSX_FORMULA_COVERAGE_MISSING_ANCHOR"
REASON_FORMULA_COVERAGE_INVALID_RANGE = "XLSX_FORMULA_COVERAGE_INVALID_RANGE"
REASON_FORMULA_COVERAGE_REVERSED_RANGE = "XLSX_FORMULA_COVERAGE_REVERSED_RANGE"
REASON_FORMULA_COVERAGE_ANCHOR_OUTSIDE_RANGE = (
    "XLSX_FORMULA_COVERAGE_ANCHOR_OUTSIDE_RANGE"
)

REASON_IDENTITY_ACTIVE_ROW_MISSING_UUID = "XLSX_IDENTITY_ACTIVE_ROW_MISSING_UUID"
REASON_IDENTITY_MALFORMED_UUID = "XLSX_IDENTITY_MALFORMED_UUID"
REASON_IDENTITY_NON_V7_UUID = "XLSX_IDENTITY_NON_V7_UUID"
REASON_IDENTITY_DUPLICATE_UUID = "XLSX_IDENTITY_DUPLICATE_UUID"


class XlsxSourceReadError(Exception):
    """Base exception for all XLSX source reading and structural failures."""

    def __init__(
        self,
        reason: str,
        *,
        sheet_name: str | None = None,
        cell_ref: str | None = None,
        physical_row_number: int | None = None,
    ) -> None:
        self.reason = reason
        self.sheet_name = sheet_name
        self.cell_ref = cell_ref
        self.physical_row_number = physical_row_number

        msg_parts = [f"XLSX read error: {reason}"]
        if sheet_name:
            msg_parts.append(f"sheet='{sheet_name}'")
        # Only format cell if cell_ref is a strictly valid coordinate (R7)
        if cell_ref and _CELL_REF_STRICT_REGEX.fullmatch(cell_ref):
            msg_parts.append(f"cell='{cell_ref}'")
        if physical_row_number is not None:
            msg_parts.append(f"row={physical_row_number}")
        super().__init__(" ".join(msg_parts))


class XlsxPackageError(XlsxSourceReadError):
    """Raised when the XLSX archive or OPC package structure is invalid."""


class XlsxStructureError(XlsxSourceReadError):
    """Raised when sheet declarations, XML syntax, or cell coordinates are malformed."""


class XlsxHeaderError(XlsxSourceReadError):
    """Raised when row 1 headers do not match the mandatory registry contract."""


class XlsxCellError(XlsxSourceReadError):
    """Raised when a cell value encoding or shared string index is invalid."""


class XlsxFormulaCoverageError(XlsxSourceReadError):
    """Raised when formula array or data-table coverage metadata is invalid."""


class XlsxIdentityError(XlsxSourceReadError):
    """Raised when an active row has a missing, malformed, or non-v7 UUID."""


@dataclass(frozen=True, slots=True)
class SourceRowLocation:
    """Physical location of an active source row within the source workbook."""

    sheet_name: str
    physical_row_number: int

    def __post_init__(self) -> None:
        if self.sheet_name not in RAW_CONTRACT_REGISTRY.sheets:
            raise XlsxStructureError(
                REASON_STRUCTURE_UNKNOWN_LOCATION_SHEET,
                sheet_name=self.sheet_name,
            )
        if (
            isinstance(self.physical_row_number, bool)
            or not isinstance(self.physical_row_number, int)
            or not (MIN_PHYSICAL_ROW <= self.physical_row_number <= MAX_PHYSICAL_ROW)
        ):
            raise XlsxStructureError(
                REASON_STRUCTURE_ROW_OUT_OF_BOUNDS,
                sheet_name=self.sheet_name,
                physical_row_number=(
                    self.physical_row_number
                    if isinstance(self.physical_row_number, int)
                    and not isinstance(self.physical_row_number, bool)
                    else None
                ),
            )


@dataclass(frozen=True, slots=True)
class XlsxSourceReadResult:
    """Immutable result of read_xlsx_source_snapshot."""

    snapshot: ValidatedSourceWorkbookSnapshot
    locations_by_uuid: MappingProxyType[uuid.UUID, SourceRowLocation]
    version: str = XLSX_SOURCE_READER_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, ValidatedSourceWorkbookSnapshot):
            raise XlsxStructureError(REASON_STRUCTURE_INVALID_SNAPSHOT_TYPE)

        if (
            not isinstance(self.version, str)
            or self.version != XLSX_SOURCE_READER_VERSION
        ):
            raise XlsxStructureError(REASON_STRUCTURE_INVALID_VERSION)

        if isinstance(self.locations_by_uuid, (Mapping, MappingProxyType)):
            object.__setattr__(
                self,
                "locations_by_uuid",
                MappingProxyType(dict(self.locations_by_uuid)),
            )
        else:
            raise XlsxStructureError(REASON_STRUCTURE_INVALID_SNAPSHOT_TYPE)

        snapshot_uuids = set(self.snapshot.all_rows_by_id.keys())
        location_uuids = set(self.locations_by_uuid.keys())

        if snapshot_uuids != location_uuids:
            raise XlsxStructureError(REASON_STRUCTURE_LOCATION_IDENTITY_MISMATCH)

        seen_locations: set[tuple[str, int]] = set()
        for u, loc in self.locations_by_uuid.items():
            if not isinstance(loc, SourceRowLocation):
                raise XlsxStructureError(REASON_STRUCTURE_INVALID_SNAPSHOT_TYPE)

            row = self.snapshot.all_rows_by_id[u]
            if loc.sheet_name != row.sheet_name:
                raise XlsxStructureError(
                    REASON_STRUCTURE_LOCATION_SHEET_MISMATCH,
                    sheet_name=loc.sheet_name,
                )

            loc_key = (loc.sheet_name, loc.physical_row_number)
            if loc_key in seen_locations:
                raise XlsxStructureError(
                    REASON_STRUCTURE_DUPLICATE_LOCATION_ROW,
                    sheet_name=loc.sheet_name,
                    physical_row_number=loc.physical_row_number,
                )
            seen_locations.add(loc_key)


def _get_secure_xml_parser() -> etree.XMLParser:
    """Construct a secure lxml parser rejecting DTDs, external entities and network."""
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        dtd_validation=False,
        load_dtd=False,
        attribute_defaults=False,
        recover=False,
        huge_tree=False,
        strip_cdata=False,
    )


def _secure_iterparse(
    stream: Any,
    *,
    events: tuple[str, ...] = ("end",),
    tag: str | tuple[str, ...] | None = None,
) -> Any:
    """Stream XML securely rejecting DTDs, external entities and network."""
    return etree.iterparse(
        stream,
        events=events,
        tag=tag,
        load_dtd=False,
        dtd_validation=False,
        attribute_defaults=False,
        resolve_entities=False,
        no_network=True,
        recover=False,
        huge_tree=False,
        strip_cdata=False,
    )


def _decode_ooxml_escapes(text: str) -> str:
    """Decode single-pass OpenXML _xHHHH_ character escapes safely."""
    if "_x" not in text:
        return text

    def _replace_match(match: re.Match[str]) -> str:
        hex_code = match.group(1)
        code_point = int(hex_code, 16)
        return chr(code_point)

    decoded = _OOXML_ESCAPE_REGEX.sub(_replace_match, text)
    try:
        return decoded.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeDecodeError as e:
        raise XlsxCellError(REASON_CELL_UNPAIRED_SURROGATE) from e
    except Exception:
        return decoded


def _extract_t_or_v_leaf_text(
    elem: etree._Element,
    *,
    sheet_name: str | None = None,
    cell_ref: str | None = None,
    physical_row_number: int | None = None,
) -> str:
    """Extract text from leaf, preserving comments and rejecting bad children."""
    if len(elem) == 0:
        return elem.text or ""

    parts = [elem.text or ""]
    for ch in elem:
        if ch.tag is etree.Comment:
            if ch.tail:
                parts.append(ch.tail)
            continue
        raise XlsxCellError(
            REASON_CELL_UNKNOWN_TYPE,
            sheet_name=sheet_name,
            cell_ref=cell_ref,
            physical_row_number=physical_row_number,
        )
    return "".join(parts)


def _extract_text_from_si_or_is(
    elem: etree._Element,
    *,
    sheet_name: str | None = None,
    cell_ref: str | None = None,
    physical_row_number: int | None = None,
) -> str:
    """Extract plain text from an <si> or <is> element (R2, R4-01).

    Rejects unknown child tags, decodes escaped fragments, and handles phonetic tags.
    """
    text_fragments: list[str] = []
    for child in elem:
        c_tag = child.tag
        if not isinstance(c_tag, str):
            continue

        if c_tag in _TAG_T_SET:
            raw_t = _extract_t_or_v_leaf_text(
                child,
                sheet_name=sheet_name,
                cell_ref=cell_ref,
                physical_row_number=physical_row_number,
            )
            text_fragments.append(_decode_ooxml_escapes(raw_t))
        elif c_tag in _TAG_R_SET:
            for r_child in child:
                rc_tag = r_child.tag
                if not isinstance(rc_tag, str):
                    continue
                if rc_tag in _TAG_T_SET:
                    raw_t = _extract_t_or_v_leaf_text(
                        r_child,
                        sheet_name=sheet_name,
                        cell_ref=cell_ref,
                        physical_row_number=physical_row_number,
                    )
                    text_fragments.append(_decode_ooxml_escapes(raw_t))
                elif rc_tag in _TAG_IGNORE_R:
                    continue
                else:
                    raise XlsxCellError(
                        REASON_CELL_UNKNOWN_TYPE,
                        sheet_name=sheet_name,
                        cell_ref=cell_ref,
                        physical_row_number=physical_row_number,
                    )
        elif c_tag in _TAG_IGNORE_IS:
            continue
        else:
            raise XlsxCellError(
                REASON_CELL_UNKNOWN_TYPE,
                sheet_name=sheet_name,
                cell_ref=cell_ref,
                physical_row_number=physical_row_number,
            )

    return "".join(text_fragments)


@lru_cache(maxsize=16384)
def _cached_parse_canonical_jalali_date(date_str: str) -> Any:
    return parse_canonical_jalali_date(date_str)


@lru_cache(maxsize=16384)
def _parse_col_and_num(col_letter: str) -> int:
    col_num = 0
    for char in col_letter:
        col_num = col_num * 26 + (ord(char) - ord("A") + 1)
    return col_num


_CELL_REF_PARSER_REGEX = re.compile(r"^([A-Z]{1,3})([1-9][0-9]*)$")


def _parse_cell_ref(ref: str) -> tuple[str, int, int]:
    """Parse cell coordinate reference strictly using fast string slicing (R5)."""
    col_letter = ref.rstrip("0123456789")
    row_part = ref[len(col_letter) :]

    if (
        not col_letter
        or not row_part
        or not col_letter.isalpha()
        or not col_letter.isupper()
        or not row_part.isdigit()
        or row_part.startswith("0")
    ):
        raise XlsxStructureError(REASON_STRUCTURE_INVALID_CELL_REF, cell_ref=ref)

    if not (1 <= len(col_letter) <= 3):
        raise XlsxStructureError(REASON_STRUCTURE_INVALID_CELL_REF, cell_ref=ref)

    col_num = _parse_col_and_num(col_letter)
    if col_num < 1 or col_num > 16384:
        raise XlsxStructureError(REASON_STRUCTURE_INVALID_CELL_REF, cell_ref=ref)

    row_num = int(row_part)
    if row_num > MAX_PHYSICAL_ROW:
        raise XlsxStructureError(
            REASON_STRUCTURE_ROW_OUT_OF_BOUNDS,
            cell_ref=ref,
            physical_row_number=row_num,
        )

    return col_letter, col_num, row_num


def _parse_range_ref(ref: str) -> tuple[int, int, int, int]:
    """Parse A1 cell or rectangle into (min_col, min_row, max_col, max_row).

    Rejects reversed ranges like 'K2:H2' or 'A10:A2' (R6).
    """
    if ":" in ref:
        parts = ref.split(":")
        if len(parts) != 2:
            raise XlsxFormulaCoverageError(REASON_FORMULA_COVERAGE_INVALID_RANGE)
        _, c1, r1 = _parse_cell_ref(parts[0])
        _, c2, r2 = _parse_cell_ref(parts[1])
        if c1 > c2 or r1 > r2:
            raise XlsxFormulaCoverageError(REASON_FORMULA_COVERAGE_REVERSED_RANGE)
        return c1, r1, c2, r2
    else:
        _, c, r = _parse_cell_ref(ref)
        return c, r, c, r


def _resolve_package_target(base_part_path: str, target: str) -> str:
    """Resolve an internal OPC target path relative to base part directory safely."""
    if ":" in target or "#" in target or "?" in target:
        raise XlsxPackageError(REASON_PACKAGE_INVALID_REL_TARGET)

    if target.startswith("/"):
        norm = posixpath.normpath(target.lstrip("/"))
    else:
        base_dir = posixpath.dirname(base_part_path)
        norm = posixpath.normpath(posixpath.join(base_dir, target))

    if norm.startswith("..") or norm.startswith("/"):
        raise XlsxPackageError(REASON_PACKAGE_TARGET_ESCAPES_ROOT)

    return norm


def _parse_relationships_file(
    zf: zipfile.ZipFile, rels_path: str, base_part_path: str
) -> dict[str, tuple[str, str]]:
    """Parse an OPC .rels file checking Id uniqueness across ALL relationships (R2)."""
    if rels_path not in zf.namelist():
        return {}

    rels_map: dict[str, tuple[str, str]] = {}
    seen_ids: set[str] = set()
    parser = _get_secure_xml_parser()

    with zf.open(rels_path, "r") as f:
        try:
            tree = etree.parse(f, parser=parser)
        except Exception as e:
            raise XlsxPackageError(REASON_PACKAGE_MALFORMED_RELS) from e

    if tree.docinfo.doctype:
        raise XlsxPackageError(REASON_PACKAGE_MALFORMED_RELS)

    root = tree.getroot()
    root_tag = root.tag
    if not (
        root_tag == "Relationships" or root_tag == f"{{{_NS_REL_PACKAGE}}}Relationships"
    ):
        raise XlsxPackageError(REASON_PACKAGE_MALFORMED_RELS)

    for rel_elem in root:
        tag = rel_elem.tag
        if tag == "Relationship" or tag == f"{{{_NS_REL_PACKAGE}}}Relationship":
            r_id = rel_elem.get("Id")
            if not r_id:
                raise XlsxPackageError(REASON_PACKAGE_MALFORMED_RELS)

            # Validate Id uniqueness across ALL relationships in this rels part (R2)
            if r_id in seen_ids:
                raise XlsxPackageError(REASON_PACKAGE_DUPLICATE_REL_ID)
            seen_ids.add(r_id)

            r_type = rel_elem.get("Type") or ""
            r_target = rel_elem.get("Target") or ""
            target_mode = (rel_elem.get("TargetMode") or "").strip()

            if target_mode.lower() == "external":
                continue

            try:
                resolved = _resolve_package_target(base_part_path, r_target)
            except XlsxPackageError:
                continue
            rels_map[r_id] = (r_type, resolved)

    return rels_map


def _parse_shared_strings_table(
    zf: zipfile.ZipFile,
    shared_strings_path: str,
    needed_indices: set[int] | None = None,
) -> dict[int, str]:
    """Parse sharedStrings.xml streaming <si> elements (R3, R6)."""
    if shared_strings_path not in zf.namelist():
        raise XlsxPackageError(REASON_PACKAGE_MISSING_SHARED_STRINGS_PART)

    strings_map: dict[int, str] = {}
    current_idx = 0

    with zf.open(shared_strings_path, "r") as stream:
        try:
            context = _secure_iterparse(stream, events=("start", "end"))
            root_checked = False
            for event, elem in context:
                tree = elem.getroottree()
                if tree is not None and tree.docinfo.doctype:
                    raise XlsxStructureError(REASON_STRUCTURE_MALFORMED_XML)

                if event == "start":
                    if not root_checked:
                        root_checked = True
                        if elem.tag not in tuple(
                            f"{{{ns}}}sst" for ns in _VALID_SPREADSHEETML_NAMESPACES
                        ):
                            raise XlsxStructureError(REASON_STRUCTURE_INVALID_ROOT)
                    elif elem.getparent() is not None and elem.getparent().tag in tuple(
                        f"{{{ns}}}sst" for ns in _VALID_SPREADSHEETML_NAMESPACES
                    ):
                        q = etree.QName(elem)
                        if (
                            q.namespace not in _VALID_SPREADSHEETML_NAMESPACES
                            or q.localname != "si"
                        ):
                            raise XlsxStructureError(
                                REASON_STRUCTURE_INVALID_SHEET_HIERARCHY
                            )
                    continue

                # event == "end"
                if elem.tag in _TAG_SI:
                    parent = elem.getparent()
                    if parent is None or parent.tag not in tuple(
                        f"{{{ns}}}sst" for ns in _VALID_SPREADSHEETML_NAMESPACES
                    ):
                        raise XlsxStructureError(
                            REASON_STRUCTURE_INVALID_SHEET_HIERARCHY
                        )

                    # Decode string only if needed_indices is None or needed (R3, R4-03)
                    if needed_indices is None or current_idx in needed_indices:
                        strings_map[current_idx] = _extract_text_from_si_or_is(elem)

                    current_idx += 1

                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]
        except XlsxSourceReadError:
            raise
        except Exception as e:
            raise XlsxStructureError(REASON_STRUCTURE_MALFORMED_XML) from e

    return strings_map


def _discover_sheet_metadata_and_needed_sst(
    zf: zipfile.ZipFile,
    worksheet_path: str,
    sheet_name: str,
    sheet_contract: RawSheetContract,
    has_sst: bool,
) -> tuple[dict[int, list[tuple[int, int]]], set[int]]:
    """Pass 1: Discover formula coverage and candidate SST indices (R4-03, R6).

    Returns (covered_by_col, needed_sst_indices).
    """
    raw_covered_by_col: dict[int, list[tuple[int, int]]] = {}
    candidate_header_sst: list[tuple[int, int]] = []
    candidate_data_sst: list[tuple[int, bool, list[tuple[int, int, bool]]]] = []

    raw_col_by_letter = {c.column_letter: c for c in sheet_contract.raw_columns}
    stable_id_col = sheet_contract.stable_id_column.column_letter
    activity_cols = set(sheet_contract.activity_columns)
    req_headers = dict(sheet_contract.required_headers_by_column)
    if sheet_contract.stable_id_column.required_header is not None:
        req_headers[stable_id_col] = sheet_contract.stable_id_column.required_header
    retained_cols = set(raw_col_by_letter.keys()) | {stable_id_col}
    sst_rx = _SST_INDEX_STRICT_REGEX

    with zf.open(worksheet_path, "r") as stream:
        try:
            context = _secure_iterparse(stream, events=("end",))
            root_checked = False
            for _, elem in context:
                tree = elem.getroottree()
                if not root_checked:
                    root_checked = True
                    if tree is not None and tree.docinfo.doctype:
                        raise XlsxStructureError(
                            REASON_STRUCTURE_MALFORMED_XML, sheet_name=sheet_name
                        )
                    root = tree.getroot() if tree is not None else None
                    if root is None or root.tag not in _TAG_WORKSHEET:
                        raise XlsxStructureError(
                            REASON_STRUCTURE_INVALID_ROOT, sheet_name=sheet_name
                        )

                if elem.tag in _TAG_ROW:
                    parent = elem.getparent()
                    if (
                        parent is None
                        or parent.tag not in _TAG_SHEET_DATA
                        or parent.getparent() is None
                        or parent.getparent().tag not in _TAG_WORKSHEET
                        or parent.getparent() != elem.getroottree().getroot()
                    ):
                        elem.clear()
                        while elem.getprevious() is not None:
                            del elem.getparent()[0]
                        continue

                    row_r = elem.get("r")
                    if not row_r or not _ROW_NUM_STRICT_REGEX.fullmatch(row_r):
                        elem.clear()
                        while elem.getprevious() is not None:
                            del elem.getparent()[0]
                        continue

                    physical_row_num = int(row_r)

                    has_literal_act = False
                    row_sst_candidates: list[tuple[int, int, bool]] = []

                    for c_elem in elem:
                        if c_elem.tag not in _TAG_C:
                            continue

                        has_children = len(c_elem) > 0
                        f_elem = c_elem.find("{*}f") if has_children else None
                        if f_elem is not None:
                            f_type = (f_elem.get("t") or "").strip().lower()
                            if f_type in ("array", "datatable"):
                                ref = f_elem.get("ref")
                                if not ref:
                                    raise XlsxFormulaCoverageError(
                                        REASON_FORMULA_COVERAGE_MISSING_REF,
                                        sheet_name=sheet_name,
                                    )

                                anchor_ref = c_elem.get("r")
                                if not anchor_ref:
                                    raise XlsxFormulaCoverageError(
                                        REASON_FORMULA_COVERAGE_MISSING_ANCHOR,
                                        sheet_name=sheet_name,
                                    )

                                try:
                                    _, a_col, a_row = _parse_cell_ref(anchor_ref)
                                    min_c, min_r, max_c, max_r = _parse_range_ref(ref)
                                except XlsxFormulaCoverageError:
                                    raise
                                except XlsxSourceReadError as e:
                                    raise XlsxFormulaCoverageError(
                                        REASON_FORMULA_COVERAGE_INVALID_RANGE,
                                        sheet_name=sheet_name,
                                    ) from e

                                if not (
                                    min_c <= a_col <= max_c and min_r <= a_row <= max_r
                                ):
                                    raise XlsxFormulaCoverageError(
                                        REASON_FORMULA_COVERAGE_ANCHOR_OUTSIDE_RANGE,
                                        sheet_name=sheet_name,
                                        cell_ref=anchor_ref,
                                    )

                                for c in range(min_c, max_c + 1):
                                    if c not in raw_covered_by_col:
                                        raw_covered_by_col[c] = []
                                    raw_covered_by_col[c].append((min_r, max_r))

                        if has_sst and f_elem is None:
                            cell_ref = c_elem.get("r")
                            if cell_ref:
                                col_let = cell_ref.rstrip("0123456789")
                                row_part = cell_ref[len(col_let) :]
                                if (
                                    col_let
                                    and row_part
                                    and row_part.isdigit()
                                    and not row_part.startswith("0")
                                ):
                                    col_num = _parse_col_and_num(col_let)
                                    c_row = int(row_part)
                                    if c_row == physical_row_num:
                                        if physical_row_num == 1:
                                            if (
                                                col_let in req_headers
                                                and c_elem.get("t") == "s"
                                            ):
                                                v_el = (
                                                    c_elem.find("{*}v")
                                                    if has_children
                                                    else None
                                                )
                                                if v_el is not None:
                                                    v_text = _extract_t_or_v_leaf_text(
                                                        v_el
                                                    )
                                                    if sst_rx.fullmatch(v_text):
                                                        candidate_header_sst.append(
                                                            (col_num, int(v_text))
                                                        )
                                        else:
                                            is_act = col_let in activity_cols
                                            is_retained = col_let in retained_cols
                                            c_t = (c_elem.get("t") or "").strip()
                                            if is_act:
                                                if c_t in ("", "n", "str"):
                                                    v_el = (
                                                        c_elem.find("{*}v")
                                                        if has_children
                                                        else None
                                                    )
                                                    if (
                                                        v_el is not None
                                                        and (v_el.text or "") != ""
                                                    ):
                                                        has_literal_act = True
                                                elif c_t == "inlineStr":
                                                    is_el = (
                                                        c_elem.find("{*}is")
                                                        if has_children
                                                        else None
                                                    )
                                                    if is_el is not None:
                                                        has_literal_act = True

                                            if is_retained and c_t == "s":
                                                v_el = (
                                                    c_elem.find("{*}v")
                                                    if has_children
                                                    else None
                                                )
                                                if v_el is not None:
                                                    v_text = _extract_t_or_v_leaf_text(
                                                        v_el
                                                    )
                                                    if sst_rx.fullmatch(v_text):
                                                        row_sst_candidates.append(
                                                            (
                                                                col_num,
                                                                int(v_text),
                                                                is_act,
                                                            )
                                                        )

                    if (
                        has_sst
                        and physical_row_num >= 2
                        and (has_literal_act or row_sst_candidates)
                    ):
                        candidate_data_sst.append(
                            (physical_row_num, has_literal_act, row_sst_candidates)
                        )

                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]
        except XlsxSourceReadError:
            raise
        except Exception as e:
            raise XlsxStructureError(
                REASON_STRUCTURE_MALFORMED_XML, sheet_name=sheet_name
            ) from e

    merged_covered_by_col: dict[int, list[tuple[int, int]]] = {}
    for col_n, intervals in raw_covered_by_col.items():
        intervals.sort(key=lambda item: item[0])
        merged: list[tuple[int, int]] = []
        for start, end in intervals:
            if not merged or start > merged[-1][1] + 1:
                merged.append((start, end))
            else:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        merged_covered_by_col[col_n] = merged

    needed_sst_indices: set[int] = set()
    if has_sst:
        for col_num, s_idx in candidate_header_sst:
            if not _is_cell_covered(merged_covered_by_col, col_num, 1):
                needed_sst_indices.add(s_idx)

        for row_num, has_lit_act, cand_list in candidate_data_sst:
            has_valid_act = has_lit_act
            if not has_valid_act:
                for col_num, _s_idx, is_act in cand_list:
                    if is_act and not _is_cell_covered(
                        merged_covered_by_col, col_num, row_num
                    ):
                        has_valid_act = True
                        break
            if has_valid_act:
                for col_num, s_idx, _is_act in cand_list:
                    if not _is_cell_covered(merged_covered_by_col, col_num, row_num):
                        needed_sst_indices.add(s_idx)

    return merged_covered_by_col, needed_sst_indices


def _is_cell_covered(
    covered_by_col: dict[int, list[tuple[int, int]]],
    col_num: int,
    row_num: int,
) -> bool:
    """Fast O(log K) interval lookup checking if (col_num, row_num) is covered."""
    intervals = covered_by_col.get(col_num)
    if not intervals:
        return False

    # Binary search for interval containing row_num
    idx = bisect.bisect_right(intervals, (row_num, MAX_PHYSICAL_ROW + 1))
    if idx > 0:
        min_r, max_r = intervals[idx - 1]
        if min_r <= row_num <= max_r:
            return True
    return False


def _decode_cell_literal_value(
    c_elem: etree._Element,
    col_letter: str,
    physical_row_num: int,
    sheet_name: str,
    field_contract: RawColumnContract | None,
    is_stable_id: bool,
    shared_strings_map: dict[int, str],
) -> Any:
    """Decode physical cell literal XML value into exact Python type or Decimal."""
    cell_type = (c_elem.get("t") or "").strip()
    cell_ref = c_elem.get("r") or f"{col_letter}{physical_row_num}"

    # Verify that cell children are structurally valid and not conflicting (R1, R4-01)
    v_count = 0
    is_count = 0
    f_count = 0
    for ch in c_elem:
        tag = ch.tag
        if not isinstance(tag, str):
            continue
        if tag.startswith("{"):
            idx = tag.find("}")
            ns = tag[1:idx]
            local = tag[idx + 1 :]
        else:
            ns = _NS_SPREADSHEETML_TRANS
            local = tag

        if ns not in _VALID_SPREADSHEETML_NAMESPACES:
            raise XlsxStructureError(
                REASON_STRUCTURE_INVALID_SHEET_HIERARCHY,
                sheet_name=sheet_name,
                cell_ref=cell_ref,
                physical_row_number=physical_row_num,
            )
        if local == "v":
            v_count += 1
        elif local == "is":
            is_count += 1
        elif local == "f":
            f_count += 1
        elif local == "extLst":
            pass
        else:
            raise XlsxCellError(
                REASON_CELL_UNKNOWN_TYPE,
                sheet_name=sheet_name,
                cell_ref=cell_ref,
                physical_row_number=physical_row_num,
            )

    if v_count > 1 or is_count > 1 or f_count > 1 or (v_count > 0 and is_count > 0):
        raise XlsxCellError(
            REASON_CELL_INCOMPATIBLE_CHILDREN,
            sheet_name=sheet_name,
            cell_ref=cell_ref,
            physical_row_number=physical_row_num,
        )

    # Validate cell type against known OpenXML types (R5)
    valid_types = ("s", "inlineStr", "str", "n", "b", "e", "d", "")
    if cell_type not in valid_types:
        raise XlsxCellError(
            REASON_CELL_UNKNOWN_TYPE,
            sheet_name=sheet_name,
            cell_ref=cell_ref,
            physical_row_number=physical_row_num,
        )

    # Reject forbidden cell types in retained columns (R3, R5)
    if cell_type == "b":
        raise XlsxCellError(
            REASON_CELL_BOOLEAN_REJECTED,
            sheet_name=sheet_name,
            cell_ref=cell_ref,
            physical_row_number=physical_row_num,
        )
    if cell_type == "e":
        raise XlsxCellError(
            REASON_CELL_ERROR_REJECTED,
            sheet_name=sheet_name,
            cell_ref=cell_ref,
            physical_row_number=physical_row_num,
        )
    if cell_type == "d":
        raise XlsxCellError(
            REASON_CELL_DATE_TYPE_REJECTED,
            sheet_name=sheet_name,
            cell_ref=cell_ref,
            physical_row_number=physical_row_num,
        )

    # 1. Shared string (t="s")
    if cell_type == "s":
        v_elem = c_elem.find("{*}v")
        if v_elem is None:
            raise XlsxCellError(
                REASON_CELL_INVALID_SST_INDEX,
                sheet_name=sheet_name,
                cell_ref=cell_ref,
                physical_row_number=physical_row_num,
            )
        v_text = _extract_t_or_v_leaf_text(
            v_elem,
            sheet_name=sheet_name,
            cell_ref=cell_ref,
            physical_row_number=physical_row_num,
        )
        if not _SST_INDEX_STRICT_REGEX.fullmatch(v_text):
            raise XlsxCellError(
                REASON_CELL_INVALID_SST_INDEX,
                sheet_name=sheet_name,
                cell_ref=cell_ref,
                physical_row_number=physical_row_num,
            )
        s_idx = int(v_text)
        if s_idx not in shared_strings_map:
            raise XlsxCellError(
                REASON_CELL_SST_INDEX_OUT_OF_RANGE,
                sheet_name=sheet_name,
                cell_ref=cell_ref,
                physical_row_number=physical_row_num,
            )
        return shared_strings_map[s_idx]

    # 2. Inline string (t="inlineStr")
    if cell_type == "inlineStr":
        is_elem = c_elem.find("{*}is")
        if is_elem is None:
            return None
        return _extract_text_from_si_or_is(
            is_elem,
            sheet_name=sheet_name,
            cell_ref=cell_ref,
            physical_row_number=physical_row_num,
        )

    # 3. Direct string (t="str")
    if cell_type == "str":
        v_elem = c_elem.find("{*}v")
        if v_elem is None:
            return None
        raw_val = _extract_t_or_v_leaf_text(
            v_elem,
            sheet_name=sheet_name,
            cell_ref=cell_ref,
            physical_row_number=physical_row_num,
        )
        return _decode_ooxml_escapes(raw_val)

    # 4. Numeric XML (t="n" or omitted)
    v_elem = c_elem.find("{*}v")
    if v_elem is None:
        return None
    raw_num_str = _extract_t_or_v_leaf_text(
        v_elem,
        sheet_name=sheet_name,
        cell_ref=cell_ref,
        physical_row_number=physical_row_num,
    )
    if raw_num_str == "" and not any(isinstance(ch, etree._Comment) for ch in v_elem):
        return None

    # Enforce strict finite ASCII numeric grammar using fullmatch (R5)
    if not _NUMERIC_XML_STRICT_REGEX.fullmatch(raw_num_str):
        raise XlsxCellError(
            REASON_CELL_INVALID_NUMERIC_LEXEME,
            sheet_name=sheet_name,
            cell_ref=cell_ref,
            physical_row_number=physical_row_num,
        )

    # If this column is stable_id -> numeric XML is forbidden
    if is_stable_id:
        raise XlsxCellError(
            REASON_CELL_NUMERIC_XML_IN_TEXT_FIELD,
            sheet_name=sheet_name,
            cell_ref=cell_ref,
            physical_row_number=physical_row_num,
        )

    # If this column is stable_id -> numeric XML is forbidden
    if is_stable_id:
        raise XlsxCellError(
            REASON_CELL_NUMERIC_XML_IN_TEXT_FIELD,
            sheet_name=sheet_name,
            cell_ref=cell_ref,
            physical_row_number=physical_row_num,
        )

    # If this is date_raw -> numeric XML is forbidden
    if field_contract is not None and field_contract.field_name == "date_raw":
        raise XlsxCellError(
            REASON_CELL_NUMERIC_XML_IN_TEXT_FIELD,
            sheet_name=sheet_name,
            cell_ref=cell_ref,
            physical_row_number=physical_row_num,
        )

    try:
        return Decimal(raw_num_str)
    except (InvalidOperation, ValueError) as e:
        raise XlsxCellError(
            REASON_CELL_INVALID_NUMERIC_LEXEME,
            sheet_name=sheet_name,
            cell_ref=cell_ref,
            physical_row_number=physical_row_num,
        ) from e


def _decode_row_column_value(
    row_c_elems: dict[str, etree._Element],
    col_letter: str,
    *,
    is_id: bool,
    physical_row_num: int,
    covered_by_col: dict[int, list[tuple[int, int]]],
    sheet_contract: RawSheetContract,
    sheet_name: str,
    raw_col_by_letter: dict[str, RawColumnContract],
    shared_strings_map: dict[int, str],
) -> Any:
    """Decode a specific column from raw row cell elements after classification."""
    c_el = row_c_elems.get(col_letter)
    if c_el is None:
        return None
    f_el = c_el.find("{*}f") if len(c_el) > 0 else None
    if f_el is not None:
        return None
    if covered_by_col:
        col_num = _parse_col_and_num(col_letter)
        if _is_cell_covered(covered_by_col, col_num, physical_row_num):
            return None

    if not is_id and col_letter not in raw_col_by_letter:
        return None

    field = raw_col_by_letter.get(col_letter)
    return _decode_cell_literal_value(
        c_el,
        col_letter,
        physical_row_num,
        sheet_name,
        field,
        is_id,
        shared_strings_map,
    )


def _read_sheet_snapshot_and_locations(
    zf: zipfile.ZipFile,
    worksheet_path: str,
    sheet_name: str,
    sheet_contract: RawSheetContract,
    shared_strings_map: dict[int, str],
    covered_by_col: dict[int, list[tuple[int, int]]],
) -> tuple[list[SourceRowInput], dict[uuid.UUID, SourceRowLocation]]:
    """Pass 3: Parse worksheet rows, check headers, decode literals (R4-01..R4-04)."""
    raw_col_by_letter = {c.column_letter: c for c in sheet_contract.raw_columns}
    stable_id_col = sheet_contract.stable_id_column.column_letter
    activity_cols = tuple(sheet_contract.activity_columns)
    req_headers = dict(sheet_contract.required_headers_by_column)
    if sheet_contract.stable_id_column.required_header is not None:
        req_headers[stable_id_col] = sheet_contract.stable_id_column.required_header

    col_validation_specs: list[tuple[RawColumnContract, TypeTag]] = []
    for col in sheet_contract.raw_columns:
        if col.field_name == "date_raw":
            tag = TypeTag.JALALI_DATE
        elif col.value_kind == ValueKind.INTEGER_TOMAN:
            tag = TypeTag.INTEGER_TOMAN
        elif col.value_kind == ValueKind.DECIMAL:
            tag = TypeTag.DECIMAL
        else:
            tag = TypeTag.RAW_TEXT
        col_validation_specs.append((col, tag))

    rows_inputs: list[SourceRowInput] = []
    locations: dict[uuid.UUID, SourceRowLocation] = {}
    seen_physical_rows: set[int] = set()
    header_checked = False

    with zf.open(worksheet_path, "r") as stream:
        try:
            context = _secure_iterparse(stream, events=("end",))
            doctype_checked = False

            for _, elem in context:
                tree = elem.getroottree()
                if not doctype_checked:
                    doctype_checked = True
                    if tree is not None and tree.docinfo.doctype:
                        raise XlsxStructureError(
                            REASON_STRUCTURE_MALFORMED_XML, sheet_name=sheet_name
                        )
                    root = tree.getroot() if tree is not None else None
                    if root is None or root.tag not in _TAG_WORKSHEET:
                        raise XlsxStructureError(
                            REASON_STRUCTURE_INVALID_ROOT, sheet_name=sheet_name
                        )

                # Check for non-row child inside sheetData (R1, R4-01)
                parent = elem.getparent()
                if (
                    parent is not None
                    and parent.tag in _TAG_SHEET_DATA
                    and elem.tag not in _TAG_ROW
                ):
                    raise XlsxStructureError(
                        REASON_STRUCTURE_INVALID_SHEET_HIERARCHY,
                        sheet_name=sheet_name,
                    )

                if elem.tag in _TAG_SHEET_DATA:
                    if parent is None or parent.tag not in _TAG_WORKSHEET:
                        raise XlsxStructureError(
                            REASON_STRUCTURE_INVALID_SHEET_HIERARCHY,
                            sheet_name=sheet_name,
                        )
                    continue

                if elem.tag in _TAG_ROW:
                    if parent is None or parent.tag not in _TAG_SHEET_DATA:
                        elem.clear()
                        while elem.getprevious() is not None:
                            del elem.getparent()[0]
                        continue

                    grandparent = parent.getparent()
                    if (
                        grandparent is None
                        or grandparent.tag not in _TAG_WORKSHEET
                        or grandparent != elem.getroottree().getroot()
                    ):
                        # Nested in extension (e.g. extLst/ext/worksheet) -> ignore
                        elem.clear()
                        while elem.getprevious() is not None:
                            del elem.getparent()[0]
                        continue

                    row_r = elem.get("r")
                    if not row_r or not _ROW_NUM_STRICT_REGEX.fullmatch(row_r):
                        raise XlsxStructureError(
                            REASON_STRUCTURE_INVALID_ROW_NUMBER,
                            sheet_name=sheet_name,
                        )

                    try:
                        physical_row_num = int(row_r)
                    except ValueError as e:
                        raise XlsxStructureError(
                            REASON_STRUCTURE_INVALID_ROW_NUMBER,
                            sheet_name=sheet_name,
                        ) from e

                    if not (1 <= physical_row_num <= MAX_PHYSICAL_ROW):
                        raise XlsxStructureError(
                            REASON_STRUCTURE_ROW_OUT_OF_BOUNDS,
                            sheet_name=sheet_name,
                            physical_row_number=physical_row_num,
                        )

                    if physical_row_num in seen_physical_rows:
                        raise XlsxStructureError(
                            REASON_STRUCTURE_DUPLICATE_ROW,
                            sheet_name=sheet_name,
                            physical_row_number=physical_row_num,
                        )
                    seen_physical_rows.add(physical_row_num)

                    # Collect cell elements in row and validate hierarchy (R1)
                    row_c_elems: dict[str, etree._Element] = {}

                    for c_elem in elem:
                        if c_elem.tag not in _TAG_C:
                            raise XlsxStructureError(
                                REASON_STRUCTURE_INVALID_SHEET_HIERARCHY,
                                sheet_name=sheet_name,
                                physical_row_number=physical_row_num,
                            )

                        cell_ref = c_elem.get("r")
                        if not cell_ref:
                            raise XlsxStructureError(
                                REASON_STRUCTURE_INVALID_CELL_REF,
                                sheet_name=sheet_name,
                                physical_row_number=physical_row_num,
                            )

                        col_let, _, c_row = _parse_cell_ref(cell_ref)
                        if c_row != physical_row_num:
                            raise XlsxStructureError(
                                REASON_STRUCTURE_CELL_ROW_MISMATCH,
                                sheet_name=sheet_name,
                                cell_ref=cell_ref,
                                physical_row_number=physical_row_num,
                            )

                        if col_let in row_c_elems:
                            raise XlsxStructureError(
                                REASON_STRUCTURE_DUPLICATE_CELL_REF,
                                sheet_name=sheet_name,
                                cell_ref=cell_ref,
                                physical_row_number=physical_row_num,
                            )
                        row_c_elems[col_let] = c_elem

                    # Process Row 1: Headers (R4-02)
                    if physical_row_num == 1:
                        header_checked = True
                        for req_col, req_h in req_headers.items():
                            c_el = row_c_elems.get(req_col)
                            if c_el is None:
                                raise XlsxHeaderError(
                                    REASON_HEADER_TEXT_MISMATCH,
                                    sheet_name=sheet_name,
                                    cell_ref=f"{req_col}1",
                                    physical_row_number=1,
                                )

                            col_num = _parse_col_and_num(req_col)
                            if (len(c_el) > 0 and c_el.find("{*}f") is not None) or (
                                covered_by_col
                                and _is_cell_covered(covered_by_col, col_num, 1)
                            ):
                                raise XlsxHeaderError(
                                    REASON_HEADER_FORMULA_BACKED,
                                    sheet_name=sheet_name,
                                    cell_ref=f"{req_col}1",
                                    physical_row_number=1,
                                )

                            h_val = _decode_cell_literal_value(
                                c_el,
                                req_col,
                                1,
                                sheet_name,
                                None,
                                False,
                                shared_strings_map,
                            )
                            if h_val != req_h:
                                raise XlsxHeaderError(
                                    REASON_HEADER_TEXT_MISMATCH,
                                    sheet_name=sheet_name,
                                    cell_ref=f"{req_col}1",
                                    physical_row_number=1,
                                )

                    # Process Row >= 2: Data Rows
                    elif physical_row_num >= 2:
                        if not header_checked:
                            raise XlsxHeaderError(
                                REASON_HEADER_MISSING_ROW,
                                sheet_name=sheet_name,
                                physical_row_number=1,
                            )

                        # Check row activity from candidate literal cells (R3, R5-01)
                        decoded_cells: dict[str, Any] = {}
                        has_activity = False
                        for act_col in activity_cols:
                            act_val = _decode_row_column_value(
                                row_c_elems,
                                act_col,
                                is_id=False,
                                physical_row_num=physical_row_num,
                                covered_by_col=covered_by_col,
                                sheet_contract=sheet_contract,
                                sheet_name=sheet_name,
                                raw_col_by_letter=raw_col_by_letter,
                                shared_strings_map=shared_strings_map,
                            )
                            decoded_cells[act_col] = act_val
                            if act_val is not None:
                                if isinstance(act_val, str):
                                    if act_val.strip() != "":
                                        has_activity = True
                                        break
                                elif isinstance(act_val, (int, Decimal)):
                                    has_activity = True
                                    break
                                else:
                                    has_activity = True
                                    break

                        # Active rows: validate UUIDv7 and extract raw fields
                        if has_activity:
                            id_val = _decode_row_column_value(
                                row_c_elems,
                                stable_id_col,
                                is_id=True,
                                physical_row_num=physical_row_num,
                                covered_by_col=covered_by_col,
                                sheet_contract=sheet_contract,
                                sheet_name=sheet_name,
                                raw_col_by_letter=raw_col_by_letter,
                                shared_strings_map=shared_strings_map,
                            )
                            if (
                                not id_val
                                or not isinstance(id_val, str)
                                or not id_val.strip()
                            ):
                                raise XlsxIdentityError(
                                    REASON_IDENTITY_ACTIVE_ROW_MISSING_UUID,
                                    sheet_name=sheet_name,
                                    cell_ref=f"{stable_id_col}{physical_row_num}",
                                    physical_row_number=physical_row_num,
                                )

                            try:
                                parsed_uuid = _parse_and_validate_uuid7(id_val.strip())
                            except InvalidIdentityError as exc:
                                raise XlsxIdentityError(
                                    REASON_IDENTITY_MALFORMED_UUID,
                                    sheet_name=sheet_name,
                                    cell_ref=f"{stable_id_col}{physical_row_num}",
                                    physical_row_number=physical_row_num,
                                ) from exc

                            if parsed_uuid in locations:
                                raise XlsxIdentityError(
                                    REASON_IDENTITY_DUPLICATE_UUID,
                                    sheet_name=sheet_name,
                                    cell_ref=f"{stable_id_col}{physical_row_num}",
                                    physical_row_number=physical_row_num,
                                )

                            row_raw_values: dict[str, Any] = {}
                            for col, type_tag in col_validation_specs:
                                col_let = col.column_letter
                                if col_let in decoded_cells:
                                    val = decoded_cells[col_let]
                                else:
                                    val = _decode_row_column_value(
                                        row_c_elems,
                                        col_let,
                                        is_id=False,
                                        physical_row_num=physical_row_num,
                                        covered_by_col=covered_by_col,
                                        sheet_contract=sheet_contract,
                                        sheet_name=sheet_name,
                                        raw_col_by_letter=raw_col_by_letter,
                                        shared_strings_map=shared_strings_map,
                                    )
                                    decoded_cells[col_let] = val

                                if val is not None:
                                    if type_tag == TypeTag.RAW_TEXT:
                                        if isinstance(val, bool) or not isinstance(
                                            val, str
                                        ):
                                            raise XlsxCellError(
                                                REASON_CELL_INVALID_NUMERIC_LEXEME,
                                                sheet_name=sheet_name,
                                                cell_ref=f"{col_let}{physical_row_num}",
                                                physical_row_number=physical_row_num,
                                            )
                                    elif type_tag == TypeTag.JALALI_DATE:
                                        if isinstance(val, bool) or not isinstance(
                                            val, str
                                        ):
                                            raise XlsxCellError(
                                                REASON_CELL_INVALID_NUMERIC_LEXEME,
                                                sheet_name=sheet_name,
                                                cell_ref=f"{col_let}{physical_row_num}",
                                                physical_row_number=physical_row_num,
                                            )
                                        try:
                                            _cached_parse_canonical_jalali_date(val)
                                        except (
                                            InvalidDateError,
                                            CanonicalDateError,
                                        ) as exc:
                                            raise XlsxCellError(
                                                REASON_CELL_INVALID_NUMERIC_LEXEME,
                                                sheet_name=sheet_name,
                                                cell_ref=f"{col_let}{physical_row_num}",
                                                physical_row_number=physical_row_num,
                                            ) from exc
                                    else:
                                        try:
                                            canonicalize_value(type_tag, val)
                                        except (
                                            CanonicalValueError,
                                            CanonicalDateError,
                                            InvalidDateError,
                                            InvalidOperation,
                                            ValueError,
                                        ) as exc:
                                            raise XlsxCellError(
                                                REASON_CELL_INVALID_NUMERIC_LEXEME,
                                                sheet_name=sheet_name,
                                                cell_ref=f"{col_let}{physical_row_num}",
                                                physical_row_number=physical_row_num,
                                            ) from exc

                                row_raw_values[col.field_name] = val

                            rows_inputs.append(
                                SourceRowInput(parsed_uuid, row_raw_values)
                            )
                            locations[parsed_uuid] = SourceRowLocation(
                                sheet_name, physical_row_num
                            )

                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]
        except XlsxSourceReadError:
            raise
        except Exception as e:
            raise XlsxStructureError(
                REASON_STRUCTURE_MALFORMED_XML, sheet_name=sheet_name
            ) from e

    if not header_checked:
        raise XlsxHeaderError(REASON_HEADER_MISSING_ROW, sheet_name=sheet_name)

    return rows_inputs, locations


def _parse_content_types_file(zf: zipfile.ZipFile) -> dict[str, str]:
    """Parse [Content_Types].xml checking required workbook and worksheet types (R2)."""
    if "[Content_Types].xml" not in zf.namelist():
        raise XlsxPackageError(REASON_PACKAGE_MISSING_CONTENT_TYPES)

    content_types: dict[str, str] = {}
    parser = _get_secure_xml_parser()

    with zf.open("[Content_Types].xml", "r") as f:
        try:
            tree = etree.parse(f, parser=parser)
        except Exception as e:
            raise XlsxPackageError(REASON_PACKAGE_INVALID_CONTENT_TYPES) from e

    if tree.docinfo.doctype:
        raise XlsxPackageError(REASON_PACKAGE_INVALID_CONTENT_TYPES)

    root = tree.getroot()
    root_tag = root.tag
    if not (root_tag == "Types" or root_tag == f"{{{_NS_CONTENT_TYPES}}}Types"):
        raise XlsxPackageError(REASON_PACKAGE_INVALID_CONTENT_TYPES)

    for elem in root:
        tag = elem.tag
        if tag == "Override" or tag == f"{{{_NS_CONTENT_TYPES}}}Override":
            part_name = (elem.get("PartName") or "").lstrip("/")
            ct = elem.get("ContentType") or ""
            if ct in _FORBIDDEN_WORKBOOK_CONTENT_TYPES:
                raise XlsxPackageError(REASON_PACKAGE_FORBIDDEN_CONTENT_TYPE)
            content_types[part_name] = ct

    return content_types


def _read_xlsx_from_zip(zf: zipfile.ZipFile) -> XlsxSourceReadResult:
    """Stream literal data from opened zip package validating profile."""
    # Check for duplicate ZIP entry names (R2)
    seen_zip_names: set[str] = set()
    for name in zf.namelist():
        if name in seen_zip_names:
            raise XlsxPackageError(REASON_PACKAGE_DUPLICATE_ZIP_ENTRY)
        seen_zip_names.add(name)

    # Step 1: Parse and validate [Content_Types].xml (R2)
    content_types_by_part = _parse_content_types_file(zf)

    # Step 2: Parse root relationships _rels/.rels (R2, R4)
    if "_rels/.rels" not in zf.namelist():
        raise XlsxPackageError(REASON_PACKAGE_MISSING_ROOT_RELS)

    root_rels = _parse_relationships_file(zf, "_rels/.rels", "")
    office_doc_targets = [
        target
        for r_type, target in root_rels.values()
        if r_type in _REL_TYPE_OFFICE_DOC
    ]
    if len(office_doc_targets) == 0:
        raise XlsxPackageError(REASON_PACKAGE_MISSING_WORKBOOK)
    if len(office_doc_targets) > 1:
        raise XlsxPackageError(REASON_PACKAGE_AMBIGUOUS_WORKBOOK)

    workbook_part_path = office_doc_targets[0]

    # Verify workbook ContentType in [Content_Types].xml (R2)
    wb_ct = content_types_by_part.get(workbook_part_path)
    if not wb_ct or wb_ct not in _VALID_WORKBOOK_CONTENT_TYPES:
        raise XlsxPackageError(REASON_PACKAGE_MISSING_WORKBOOK)

    # Step 3: Parse workbook relationships xl/_rels/workbook.xml.rels (R2, R4)
    wb_dir = posixpath.dirname(workbook_part_path)
    wb_rels_path = (
        posixpath.join(
            wb_dir, "_rels", f"{posixpath.basename(workbook_part_path)}.rels"
        )
        if wb_dir
        else f"_rels/{posixpath.basename(workbook_part_path)}.rels"
    )

    wb_rels = _parse_relationships_file(zf, wb_rels_path, workbook_part_path)

    # Validate at most one sharedStrings relationship (R4)
    sst_targets = [
        target
        for r_type, target in wb_rels.values()
        if r_type in _REL_TYPE_SHARED_STRINGS
    ]
    if len(sst_targets) > 1:
        raise XlsxPackageError(REASON_PACKAGE_AMBIGUOUS_SHARED_STRINGS)
    shared_strings_path = sst_targets[0] if sst_targets else None

    # Step 4: Parse xl/workbook.xml to resolve declared sheets (R2, R4)
    if workbook_part_path not in zf.namelist():
        raise XlsxPackageError(REASON_PACKAGE_MISSING_WORKBOOK)

    parser = _get_secure_xml_parser()
    with zf.open(workbook_part_path, "r") as f:
        try:
            wb_tree = etree.parse(f, parser=parser)
        except Exception as e:
            raise XlsxStructureError(REASON_STRUCTURE_MALFORMED_XML) from e

    if wb_tree.docinfo.doctype:
        raise XlsxStructureError(REASON_STRUCTURE_MALFORMED_XML)

    wb_root = wb_tree.getroot()
    if wb_root.tag not in tuple(
        f"{{{ns}}}workbook" for ns in _VALID_SPREADSHEETML_NAMESPACES
    ):
        raise XlsxStructureError(REASON_STRUCTURE_INVALID_ROOT)

    # Exactly one <sheets> element in workbook.xml (R4)
    sheets_containers = [
        ch
        for ch in wb_root
        if ch.tag in tuple(f"{{{ns}}}sheets" for ns in _VALID_SPREADSHEETML_NAMESPACES)
    ]
    if len(sheets_containers) != 1:
        raise XlsxStructureError(REASON_STRUCTURE_AMBIGUOUS_SHEETS)

    sheets_elem = sheets_containers[0]

    sheet_r_ids: dict[str, str] = {}
    seen_sheet_names: set[str] = set()

    for child in sheets_elem:
        if child.tag in tuple(
            f"{{{ns}}}sheet" for ns in _VALID_SPREADSHEETML_NAMESPACES
        ):
            name = child.get("name")
            if not name:
                continue

            if name in seen_sheet_names:
                raise XlsxStructureError(
                    REASON_STRUCTURE_DUPLICATE_SHEET_DECLARATION,
                    sheet_name=name,
                )
            seen_sheet_names.add(name)

            r_id = None
            for attr_k, attr_v in child.attrib.items():
                if attr_k == "id" or attr_k.endswith("}id"):
                    r_id = attr_v
                    break

            if r_id:
                sheet_r_ids[name] = r_id

    # Step 5: Verify that exactly all 4 approved sheets are declared (R2)
    approved_sheets = tuple(RAW_CONTRACT_REGISTRY.sheets.keys())
    missing_approved = [s for s in approved_sheets if s not in sheet_r_ids]
    if missing_approved:
        raise XlsxStructureError(REASON_STRUCTURE_MISSING_APPROVED_SHEETS)

    sheet_parts: dict[str, str] = {}
    seen_part_paths: set[str] = set()

    for s_name in approved_sheets:
        r_id = sheet_r_ids[s_name]
        if r_id not in wb_rels:
            raise XlsxStructureError(
                REASON_STRUCTURE_MISSING_SHEET_DECLARATION,
                sheet_name=s_name,
            )

        r_type, ws_path = wb_rels[r_id]
        if r_type not in _REL_TYPE_WORKSHEET:
            raise XlsxStructureError(
                REASON_STRUCTURE_INVALID_WORKSHEET_REL_TYPE,
                sheet_name=s_name,
            )

        if ws_path not in zf.namelist():
            raise XlsxPackageError(
                REASON_PACKAGE_MISSING_WORKSHEET_PART, sheet_name=s_name
            )

        # Verify worksheet ContentType in [Content_Types].xml (R2)
        ws_ct = content_types_by_part.get(ws_path)
        if not ws_ct or ws_ct not in _VALID_WORKSHEET_CONTENT_TYPES:
            raise XlsxPackageError(
                REASON_PACKAGE_MISSING_WORKSHEET_PART, sheet_name=s_name
            )

        if ws_path in seen_part_paths:
            raise XlsxStructureError(
                REASON_STRUCTURE_DUPLICATE_WORKSHEET_PART,
                sheet_name=s_name,
            )

        seen_part_paths.add(ws_path)
        sheet_parts[s_name] = ws_path

    # Step 6: Pass 1 on sheets -> collect formula coverage and needed SST (R4-03, R6)
    all_needed_sst_indices: set[int] = set()
    formula_coverage_by_sheet: dict[str, dict[int, list[tuple[int, int]]]] = {}
    has_sst = bool(shared_strings_path)

    for s_name in approved_sheets:
        ws_path = sheet_parts[s_name]
        sheet_contract = RAW_CONTRACT_REGISTRY.sheets[s_name]
        cov, needed_sst = _discover_sheet_metadata_and_needed_sst(
            zf, ws_path, s_name, sheet_contract, has_sst
        )
        formula_coverage_by_sheet[s_name] = cov
        if has_sst:
            all_needed_sst_indices.update(needed_sst)

    # Step 7: Parse SST if present, decoding only needed indices (R4-03, R6)
    shared_strings_map: dict[int, str] = {}
    if shared_strings_path:
        shared_strings_map = _parse_shared_strings_table(
            zf, shared_strings_path, needed_indices=all_needed_sst_indices
        )

    # Step 8: Pass 2 on all approved sheets -> read rows and locations (R4-01..R4-04)
    all_sheet_inputs: list[SourceSheetInput] = []
    all_locations: dict[uuid.UUID, SourceRowLocation] = {}

    for s_name in approved_sheets:
        ws_path = sheet_parts[s_name]
        sheet_contract = RAW_CONTRACT_REGISTRY.sheets[s_name]
        cov = formula_coverage_by_sheet[s_name]
        s_rows, s_locations = _read_sheet_snapshot_and_locations(
            zf, ws_path, s_name, sheet_contract, shared_strings_map, cov
        )
        all_sheet_inputs.append(SourceSheetInput(sheet_name=s_name, rows=s_rows))

        for u, loc in s_locations.items():
            if u in all_locations:
                raise XlsxIdentityError(
                    REASON_IDENTITY_DUPLICATE_UUID,
                    sheet_name=loc.sheet_name,
                    cell_ref=(
                        f"{sheet_contract.stable_id_column.column_letter}"
                        f"{loc.physical_row_number}"
                    ),
                    physical_row_number=loc.physical_row_number,
                )
            all_locations[u] = loc

    # Step 9: Construct WP-04 snapshot with exception mapping (R4-04)
    try:
        snapshot = build_source_workbook_snapshot(all_sheet_inputs)
    except DuplicateIdentityError as exc:
        raise XlsxIdentityError(REASON_IDENTITY_DUPLICATE_UUID) from exc
    except InvalidIdentityError as exc:
        raise XlsxIdentityError(REASON_IDENTITY_MALFORMED_UUID) from exc
    except (CanonicalValueError, InvalidDateError) as exc:
        raise XlsxCellError(REASON_CELL_INVALID_NUMERIC_LEXEME) from exc
    except SourceChangePlanError as exc:
        raise XlsxIdentityError(REASON_IDENTITY_MALFORMED_UUID) from exc

    sorted_locs = dict(sorted(all_locations.items(), key=lambda item: item[0].bytes))

    return XlsxSourceReadResult(
        snapshot=snapshot,
        locations_by_uuid=MappingProxyType(sorted_locs),
        version=XLSX_SOURCE_READER_VERSION,
    )


def read_xlsx_source_snapshot(path: Path | str) -> XlsxSourceReadResult:
    """Read a stable XLSX file snapshot and return validated snapshot and locations."""
    file_path = Path(path)
    if not file_path.exists():
        raise XlsxPackageError(REASON_PACKAGE_NOT_FOUND)

    if not file_path.is_file():
        raise XlsxPackageError(REASON_PACKAGE_NOT_FILE)

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            return _read_xlsx_from_zip(zf)
    except zipfile.BadZipFile as e:
        raise XlsxPackageError(REASON_PACKAGE_CORRUPT_ZIP) from e
    except XlsxSourceReadError:
        raise
