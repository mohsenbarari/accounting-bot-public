"""Streaming read-only XLSX source reader and physical row tracker.

Implements ADR-0008 and WP-05 for reading physical Excel .xlsx packages using
standard zipfile and streaming lxml, extracting literal source inputs from the
four approved sheets, excluding formulas/caches, evaluating row activity,
validating headers and UUIDv7 identifiers, and returning a validated source
workbook snapshot with separate physical row locations.
"""

from __future__ import annotations

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

from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
    CellClassification,
    RawColumnContract,
    RawSheetContract,
    ValueKind,
    is_valid_excel_column,
)
from accounting_contracts.source_change_plan import (
    DuplicateIdentityError,
    SourceRowInput,
    SourceSheetInput,
    ValidatedSourceWorkbookSnapshot,
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

_VALID_WORKBOOK_CONTENT_TYPES = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.main+xml",
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

_TAG_WORKSHEET = (
    f"{{{_NS_SPREADSHEETML_TRANS}}}worksheet",
    f"{{{_NS_SPREADSHEETML_STRICT}}}worksheet",
)
_TAG_SHEET_DATA = (
    f"{{{_NS_SPREADSHEETML_TRANS}}}sheetData",
    f"{{{_NS_SPREADSHEETML_STRICT}}}sheetData",
)
_TAG_ROW = (
    f"{{{_NS_SPREADSHEETML_TRANS}}}row",
    f"{{{_NS_SPREADSHEETML_STRICT}}}row",
)
_TAG_C = (
    f"{{{_NS_SPREADSHEETML_TRANS}}}c",
    f"{{{_NS_SPREADSHEETML_STRICT}}}c",
)
_TAG_F = (
    f"{{{_NS_SPREADSHEETML_TRANS}}}f",
    f"{{{_NS_SPREADSHEETML_STRICT}}}f",
)
_TAG_SI = (
    f"{{{_NS_SPREADSHEETML_TRANS}}}si",
    f"{{{_NS_SPREADSHEETML_STRICT}}}si",
)
_TAG_T = (
    f"{{{_NS_SPREADSHEETML_TRANS}}}t",
    f"{{{_NS_SPREADSHEETML_STRICT}}}t",
)
_TAG_R = (
    f"{{{_NS_SPREADSHEETML_TRANS}}}r",
    f"{{{_NS_SPREADSHEETML_STRICT}}}r",
)

# Standard regexes
_CELL_REF_STRICT_REGEX = re.compile(r"^([A-Za-z]{1,3})([1-9][0-9]*)$")
_OOXML_ESCAPE_REGEX = re.compile(r"_x([0-9a-fA-F]{4})_")
_NUMERIC_XML_STRICT_REGEX = re.compile(
    r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"
)
_SST_INDEX_STRICT_REGEX = re.compile(r"^[0-9]+$")

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
REASON_PACKAGE_MISSING_WORKSHEET_PART = "XLSX_PACKAGE_MISSING_WORKSHEET_PART"
REASON_PACKAGE_MISSING_SHARED_STRINGS_PART = "XLSX_PACKAGE_MISSING_SHARED_STRINGS_PART"

REASON_STRUCTURE_MALFORMED_XML = "XLSX_STRUCTURE_MALFORMED_XML"
REASON_STRUCTURE_INVALID_ROOT = "XLSX_STRUCTURE_INVALID_ROOT"
REASON_STRUCTURE_MISSING_SHEET_DECLARATION = "XLSX_STRUCTURE_MISSING_SHEET_DECLARATION"
REASON_STRUCTURE_DUPLICATE_SHEET_DECLARATION = (
    "XLSX_STRUCTURE_DUPLICATE_SHEET_DECLARATION"
)
REASON_STRUCTURE_MISSING_APPROVED_SHEETS = "XLSX_STRUCTURE_MISSING_APPROVED_SHEETS"
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
REASON_IDENTITY_NON_CANONICAL_UUID = "XLSX_IDENTITY_NON_CANONICAL_UUID"
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
        if cell_ref:
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
                physical_row_number=self.physical_row_number
                if isinstance(self.physical_row_number, int)
                and not isinstance(self.physical_row_number, bool)
                else None,
            )


@dataclass(frozen=True, slots=True)
class XlsxSourceReadResult:
    """Immutable result of a complete XLSX source extraction."""

    snapshot: ValidatedSourceWorkbookSnapshot
    locations_by_uuid: MappingProxyType[uuid.UUID, SourceRowLocation]
    version: str = XLSX_SOURCE_READER_VERSION

    def __post_init__(self) -> None:
        if self.version != XLSX_SOURCE_READER_VERSION:
            raise XlsxStructureError(REASON_STRUCTURE_INVALID_VERSION)

        if not isinstance(self.snapshot, ValidatedSourceWorkbookSnapshot):
            raise XlsxStructureError(REASON_STRUCTURE_INVALID_SNAPSHOT_TYPE)

        if isinstance(self.locations_by_uuid, (Mapping, MappingProxyType)):
            loc_dict = dict(self.locations_by_uuid)
        else:
            raise XlsxStructureError(REASON_STRUCTURE_LOCATION_IDENTITY_MISMATCH)

        # Invariant 1: Exact match with snapshot identities
        snapshot_uuids = set(self.snapshot.all_rows_by_id.keys())
        loc_uuids = set(loc_dict.keys())
        if loc_uuids != snapshot_uuids:
            raise XlsxStructureError(REASON_STRUCTURE_LOCATION_IDENTITY_MISMATCH)

        # Invariant 2: Location sheet matches snapshot row sheet
        sheet_rows_seen: dict[str, set[int]] = {
            s: set() for s in RAW_CONTRACT_REGISTRY.sheets
        }
        for u, loc in loc_dict.items():
            if not isinstance(loc, SourceRowLocation):
                raise XlsxStructureError(REASON_STRUCTURE_LOCATION_IDENTITY_MISMATCH)
            expected_sheet = self.snapshot.all_rows_by_id[u].sheet_name
            if loc.sheet_name != expected_sheet:
                raise XlsxStructureError(
                    REASON_STRUCTURE_LOCATION_SHEET_MISMATCH,
                    sheet_name=loc.sheet_name,
                )
            if loc.physical_row_number in sheet_rows_seen[loc.sheet_name]:
                raise XlsxStructureError(
                    REASON_STRUCTURE_DUPLICATE_LOCATION_ROW,
                    sheet_name=loc.sheet_name,
                    physical_row_number=loc.physical_row_number,
                )
            sheet_rows_seen[loc.sheet_name].add(loc.physical_row_number)

        # Invariant 3: Ordering by UUID bytes
        sorted_keys = tuple(sorted(loc_dict.keys(), key=lambda k: k.bytes))
        if tuple(loc_dict.keys()) != sorted_keys:
            loc_dict = {k: loc_dict[k] for k in sorted_keys}

        object.__setattr__(self, "locations_by_uuid", MappingProxyType(loc_dict))


def _get_secure_xml_parser() -> etree.XMLParser:
    """Create a securely configured lxml XMLParser rejecting DTDs and entities."""
    return etree.XMLParser(
        load_dtd=False,
        dtd_validation=False,
        attribute_defaults=False,
        resolve_entities=False,
        no_network=True,
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
        if 0xD800 <= code_point <= 0xDFFF:
            # Preserve surrogate code point as chr
            return chr(code_point)
        return chr(code_point)

    decoded = _OOXML_ESCAPE_REGEX.sub(_replace_match, text)
    try:
        # Re-encode UTF-16 surrogate pairs into single code points
        return decoded.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeDecodeError as e:
        raise XlsxCellError(REASON_CELL_UNPAIRED_SURROGATE) from e
    except Exception:
        return decoded


@lru_cache(maxsize=16384)
def _parse_col_and_num(col_letter: str) -> int:
    col_num = 0
    for char in col_letter:
        col_num = col_num * 26 + (ord(char) - ord("A") + 1)
    return col_num


def _parse_cell_ref(ref: str) -> tuple[str, int, int]:
    """Parse cell coordinate reference into (col_letter, col_number, row_number)."""
    match = _CELL_REF_STRICT_REGEX.match(ref)
    if not match:
        raise XlsxStructureError(REASON_STRUCTURE_INVALID_CELL_REF, cell_ref=ref)

    col_letter = match.group(1).upper()
    row_num = int(match.group(2))

    if not is_valid_excel_column(col_letter):
        raise XlsxStructureError(REASON_STRUCTURE_INVALID_CELL_REF, cell_ref=ref)

    if not (1 <= row_num <= MAX_PHYSICAL_ROW):
        raise XlsxStructureError(
            REASON_STRUCTURE_ROW_OUT_OF_BOUNDS,
            cell_ref=ref,
            physical_row_number=row_num,
        )

    col_num = _parse_col_and_num(col_letter)
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
    """Parse an OPC .rels file returning dict of rId -> (rel_type, resolved_target)."""
    if rels_path not in zf.namelist():
        return {}

    rels_map: dict[str, tuple[str, str]] = {}
    parser = _get_secure_xml_parser()
    with zf.open(rels_path, "r") as f:
        try:
            tree = etree.parse(f, parser=parser)
        except Exception as e:
            raise XlsxPackageError(REASON_PACKAGE_MALFORMED_RELS) from e

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
            if r_id in rels_map:
                raise XlsxPackageError(REASON_PACKAGE_DUPLICATE_REL_ID)

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
    zf: zipfile.ZipFile, shared_strings_path: str
) -> list[str]:
    """Parse sharedStrings.xml streaming <si> elements and return strings list."""
    if shared_strings_path not in zf.namelist():
        raise XlsxPackageError(REASON_PACKAGE_MISSING_SHARED_STRINGS_PART)

    strings: list[str] = []

    with zf.open(shared_strings_path, "r") as stream:
        try:
            context = _secure_iterparse(stream, events=("end",), tag=_TAG_SI)
            for _, elem in context:
                text_fragments: list[str] = []
                for child in elem:
                    c_tag = child.tag
                    # Direct <t> child of <si>
                    if c_tag in _TAG_T:
                        raw_t = child.text or ""
                        text_fragments.append(_decode_ooxml_escapes(raw_t))
                    # Rich text <r> container: extract <t>, ignore <rPh> (R4)
                    elif c_tag in _TAG_R:
                        for r_child in child:
                            if r_child.tag in _TAG_T:
                                raw_t = r_child.text or ""
                                text_fragments.append(_decode_ooxml_escapes(raw_t))

                full_text = "".join(text_fragments)
                strings.append(full_text)

                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]
        except XlsxSourceReadError:
            raise
        except Exception as e:
            raise XlsxStructureError(REASON_STRUCTURE_MALFORMED_XML) from e

    return strings


def _discover_array_formula_coverage(
    zf: zipfile.ZipFile, worksheet_path: str, sheet_name: str
) -> dict[int, list[tuple[int, int]]]:
    """Pass 1: Discover array/data-table formula coverage bounding boxes.

    Returns compact dictionary mapping col_num -> list of (min_row, max_row) (R6).
    """
    covered_by_col: dict[int, list[tuple[int, int]]] = {}

    with zf.open(worksheet_path, "r") as stream:
        try:
            context = _secure_iterparse(stream, events=("end",), tag=_TAG_F)
            for _, elem in context:
                f_type = (elem.get("t") or "").strip().lower()
                if f_type in ("array", "datatable"):
                    ref = elem.get("ref")
                    if not ref:
                        raise XlsxFormulaCoverageError(
                            REASON_FORMULA_COVERAGE_MISSING_REF,
                            sheet_name=sheet_name,
                        )

                    parent = elem.getparent()
                    anchor_ref = parent.get("r") if parent is not None else None
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

                    # Anchor MUST be top-left cell (R6)
                    if not (a_col == min_c and a_row == min_r):
                        raise XlsxFormulaCoverageError(
                            REASON_FORMULA_COVERAGE_ANCHOR_OUTSIDE_RANGE,
                            sheet_name=sheet_name,
                            cell_ref=anchor_ref,
                        )

                    for c in range(min_c, max_c + 1):
                        if c not in covered_by_col:
                            covered_by_col[c] = []
                        covered_by_col[c].append((min_r, max_r))

                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]
        except XlsxSourceReadError:
            raise
        except Exception as e:
            raise XlsxStructureError(
                REASON_STRUCTURE_MALFORMED_XML, sheet_name=sheet_name
            ) from e

    return covered_by_col


def _decode_cell_literal_value(
    c_elem: etree._Element,
    col_letter: str,
    physical_row_num: int,
    sheet_name: str,
    field_contract: RawColumnContract | None,
    is_stable_id: bool,
    shared_strings: list[str],
) -> Any:
    """Decode physical cell literal XML value into exact Python type or Decimal."""
    cell_type = (c_elem.get("t") or "").strip()
    cell_ref = c_elem.get("r") or f"{col_letter}{physical_row_num}"

    # Verify that cell children are structurally valid and not conflicting (R1)
    # Collect child element tags
    child_localnames = [
        etree.QName(ch).localname for ch in c_elem if isinstance(ch.tag, str)
    ]
    if "v" in child_localnames and "is" in child_localnames:
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
            return None
        v_text = v_elem.text
        if v_text is None or v_text == "":
            return None
        if not _SST_INDEX_STRICT_REGEX.match(v_text):
            raise XlsxCellError(
                REASON_CELL_INVALID_SST_INDEX,
                sheet_name=sheet_name,
                cell_ref=cell_ref,
                physical_row_number=physical_row_num,
            )
        s_idx = int(v_text)
        if s_idx < 0 or s_idx >= len(shared_strings):
            raise XlsxCellError(
                REASON_CELL_SST_INDEX_OUT_OF_RANGE,
                sheet_name=sheet_name,
                cell_ref=cell_ref,
                physical_row_number=physical_row_num,
            )
        return shared_strings[s_idx]

    # 2. Inline string (t="inlineStr")
    if cell_type == "inlineStr":
        is_elem = c_elem.find("{*}is")
        if is_elem is None:
            return None
        fragments: list[str] = []
        for child in is_elem:
            c_tag = child.tag
            if c_tag in _TAG_T:
                fragments.append(_decode_ooxml_escapes(child.text or ""))
            elif c_tag in _TAG_R:
                for r_child in child:
                    if r_child.tag in _TAG_T:
                        fragments.append(_decode_ooxml_escapes(r_child.text or ""))
        return "".join(fragments)

    # 3. Direct string (t="str")
    if cell_type == "str":
        v_elem = c_elem.find("{*}v")
        if v_elem is None:
            return None
        # <v/> or <v></v> is explicit empty string "" (R4)
        raw_val = v_elem.text if v_elem.text is not None else ""
        return _decode_ooxml_escapes(raw_val)

    # 4. Numeric XML (t="n" or omitted)
    v_elem = c_elem.find("{*}v")
    if v_elem is None or v_elem.text is None:
        return None
    raw_num_str = v_elem.text
    if raw_num_str == "":
        return None

    # Enforce strict finite ASCII numeric grammar (R5)
    if not _NUMERIC_XML_STRICT_REGEX.match(raw_num_str):
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

    # If this is a raw column
    if field_contract is not None:
        if field_contract.field_name == "date_raw":
            raise XlsxCellError(
                REASON_CELL_NUMERIC_XML_IN_TEXT_FIELD,
                sheet_name=sheet_name,
                cell_ref=cell_ref,
                physical_row_number=physical_row_num,
            )
        if field_contract.value_kind in (
            ValueKind.INTEGER_TOMAN,
            ValueKind.DECIMAL,
        ):
            try:
                return Decimal(raw_num_str)
            except InvalidOperation as e:
                raise XlsxCellError(
                    REASON_CELL_INVALID_NUMERIC_LEXEME,
                    sheet_name=sheet_name,
                    cell_ref=cell_ref,
                    physical_row_number=physical_row_num,
                ) from e
        if field_contract.value_kind == ValueKind.RAW_TEXT:
            return raw_num_str

    try:
        return Decimal(raw_num_str)
    except InvalidOperation:
        return raw_num_str


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
    shared_strings: list[str],
) -> Any:
    """Decode a specific column from raw row cell elements after classification."""
    c_el = row_c_elems.get(col_letter)
    if c_el is None:
        return None
    f_el = c_el.find("{*}f")
    is_f = f_el is not None
    c_num = _parse_col_and_num(col_letter)
    if not is_f and c_num in covered_by_col:
        for min_r, max_r in covered_by_col[c_num]:
            if min_r <= physical_row_num <= max_r:
                is_f = True
                break
    cls = sheet_contract.classify_cell(col_letter, has_formula=is_f)
    if cls == CellClassification.FORMULA_EXCLUDED or is_f:
        return None
    if cls in (
        CellClassification.RAW_INPUT_CANDIDATE,
        CellClassification.STABLE_ID,
    ):
        return _decode_cell_literal_value(
            c_el,
            col_letter,
            physical_row_num,
            sheet_name,
            raw_col_by_letter.get(col_letter),
            is_id,
            shared_strings,
        )
    return None


def _read_sheet_snapshot_and_locations(
    zf: zipfile.ZipFile,
    worksheet_path: str,
    sheet_name: str,
    sheet_contract: RawSheetContract,
    shared_strings: list[str],
) -> tuple[list[SourceRowInput], dict[uuid.UUID, SourceRowLocation]]:
    """Pass 2: Parse worksheet rows, check headers, decode literals, check activity."""
    raw_col_by_letter = {c.column_letter: c for c in sheet_contract.raw_columns}
    stable_id_col = sheet_contract.stable_id_column.column_letter
    activity_cols = sheet_contract.activity_columns
    req_headers = sheet_contract.required_headers_by_column
    req_id_header = sheet_contract.stable_id_column.required_header

    # Pass 1: Compact covered ranges dictionary
    covered_by_col = _discover_array_formula_coverage(zf, worksheet_path, sheet_name)

    rows_inputs: list[SourceRowInput] = []
    locations: dict[uuid.UUID, SourceRowLocation] = {}

    seen_physical_rows: set[int] = set()
    header_checked = False

    with zf.open(worksheet_path, "r") as stream:
        try:
            # Stream <row> elements safely
            context = _secure_iterparse(stream, events=("end",), tag=_TAG_ROW)
            for _, elem in context:
                # Verify that parent of row is sheetData and root is worksheet (R1)
                parent = elem.getparent()
                if (
                    parent is None
                    or parent.tag not in _TAG_SHEET_DATA
                    or parent.getparent() is None
                    or parent.getparent().tag not in _TAG_WORKSHEET
                ):
                    # Discard rows outside sheetData (R1)
                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]
                    continue

                row_r = elem.get("r")
                if not row_r:
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

                # Collect cell elements in this row
                row_c_elems: dict[str, etree._Element] = {}

                for c_elem in elem:
                    if c_elem.tag not in _TAG_C:
                        continue

                    cell_ref = c_elem.get("r")
                    if not cell_ref:
                        raise XlsxStructureError(
                            REASON_STRUCTURE_INVALID_CELL_REF,
                            sheet_name=sheet_name,
                            physical_row_number=physical_row_num,
                        )

                    col_letter, col_num, cell_row = _parse_cell_ref(cell_ref)
                    if cell_row != physical_row_num:
                        raise XlsxStructureError(
                            REASON_STRUCTURE_CELL_ROW_MISMATCH,
                            sheet_name=sheet_name,
                            cell_ref=cell_ref,
                            physical_row_number=physical_row_num,
                        )

                    if col_letter in row_c_elems:
                        raise XlsxStructureError(
                            REASON_STRUCTURE_DUPLICATE_CELL_REF,
                            sheet_name=sheet_name,
                            cell_ref=cell_ref,
                            physical_row_number=physical_row_num,
                        )
                    row_c_elems[col_letter] = c_elem

                # Process Row 1: Headers
                if physical_row_num == 1:
                    header_checked = True
                    for req_col, expected_header in req_headers.items():
                        c_el = row_c_elems.get(req_col)
                        if c_el is not None and c_el.find("{*}f") is not None:
                            raise XlsxHeaderError(
                                REASON_HEADER_FORMULA_BACKED,
                                sheet_name=sheet_name,
                                cell_ref=f"{req_col}1",
                                physical_row_number=1,
                            )
                        actual_header = _decode_row_column_value(
                            row_c_elems,
                            req_col,
                            is_id=False,
                            physical_row_num=1,
                            covered_by_col=covered_by_col,
                            sheet_contract=sheet_contract,
                            sheet_name=sheet_name,
                            raw_col_by_letter=raw_col_by_letter,
                            shared_strings=shared_strings,
                        )
                        if (
                            actual_header is None
                            or not isinstance(actual_header, str)
                            or actual_header != expected_header
                        ):
                            raise XlsxHeaderError(
                                REASON_HEADER_TEXT_MISMATCH,
                                sheet_name=sheet_name,
                                cell_ref=f"{req_col}1",
                                physical_row_number=1,
                            )

                    # Check stable_id required header if mandated
                    if req_id_header:
                        c_el = row_c_elems.get(stable_id_col)
                        if c_el is not None and c_el.find("{*}f") is not None:
                            raise XlsxHeaderError(
                                REASON_HEADER_FORMULA_BACKED,
                                sheet_name=sheet_name,
                                cell_ref=f"{stable_id_col}1",
                                physical_row_number=1,
                            )
                        actual_id_header = _decode_row_column_value(
                            row_c_elems,
                            stable_id_col,
                            is_id=True,
                            physical_row_num=1,
                            covered_by_col=covered_by_col,
                            sheet_contract=sheet_contract,
                            sheet_name=sheet_name,
                            raw_col_by_letter=raw_col_by_letter,
                            shared_strings=shared_strings,
                        )
                        if (
                            actual_id_header is None
                            or not isinstance(actual_id_header, str)
                            or actual_id_header != req_id_header
                        ):
                            raise XlsxHeaderError(
                                REASON_HEADER_TEXT_MISMATCH,
                                sheet_name=sheet_name,
                                cell_ref=f"{stable_id_col}1",
                                physical_row_number=1,
                            )

                # Process Rows >= 2: Data Rows
                elif physical_row_num >= MIN_PHYSICAL_ROW:
                    # Step A: Evaluate activity ONLY on activity_columns (R3)
                    is_active = False
                    decoded_activity_vals: dict[str, Any] = {}
                    for act_col in activity_cols:
                        val = _decode_row_column_value(
                            row_c_elems,
                            act_col,
                            is_id=False,
                            physical_row_num=physical_row_num,
                            covered_by_col=covered_by_col,
                            sheet_contract=sheet_contract,
                            sheet_name=sheet_name,
                            raw_col_by_letter=raw_col_by_letter,
                            shared_strings=shared_strings,
                        )
                        decoded_activity_vals[act_col] = val
                        if val is not None:
                            if isinstance(val, (int, Decimal)):
                                is_active = True
                            elif isinstance(val, str) and val.strip() != "":
                                is_active = True

                    if is_active:
                        # Step B: Decode technical ID column
                        id_val = _decode_row_column_value(
                            row_c_elems,
                            stable_id_col,
                            is_id=True,
                            physical_row_num=physical_row_num,
                            covered_by_col=covered_by_col,
                            sheet_contract=sheet_contract,
                            sheet_name=sheet_name,
                            raw_col_by_letter=raw_col_by_letter,
                            shared_strings=shared_strings,
                        )

                        if (
                            id_val is None
                            or not isinstance(id_val, str)
                            or not id_val.strip()
                        ):
                            raise XlsxIdentityError(
                                REASON_IDENTITY_ACTIVE_ROW_MISSING_UUID,
                                sheet_name=sheet_name,
                                cell_ref=f"{stable_id_col}{physical_row_num}",
                                physical_row_number=physical_row_num,
                            )

                        id_clean = id_val.strip()
                        try:
                            parsed_uuid = uuid.UUID(id_clean)
                        except ValueError as e:
                            raise XlsxIdentityError(
                                REASON_IDENTITY_MALFORMED_UUID,
                                sheet_name=sheet_name,
                                cell_ref=f"{stable_id_col}{physical_row_num}",
                                physical_row_number=physical_row_num,
                            ) from e

                        if parsed_uuid.version != 7:
                            raise XlsxIdentityError(
                                REASON_IDENTITY_NON_V7_UUID,
                                sheet_name=sheet_name,
                                cell_ref=f"{stable_id_col}{physical_row_num}",
                                physical_row_number=physical_row_num,
                            )

                        if str(parsed_uuid).lower() != id_clean.lower():
                            raise XlsxIdentityError(
                                REASON_IDENTITY_NON_CANONICAL_UUID,
                                sheet_name=sheet_name,
                                cell_ref=f"{stable_id_col}{physical_row_num}",
                                physical_row_number=physical_row_num,
                            )

                        if parsed_uuid in locations:
                            raise DuplicateIdentityError(REASON_IDENTITY_DUPLICATE_UUID)

                        raw_dict: dict[str, Any] = {}
                        for raw_col in sheet_contract.raw_columns:
                            c_let = raw_col.column_letter
                            if c_let in decoded_activity_vals:
                                raw_dict[raw_col.field_name] = decoded_activity_vals[
                                    c_let
                                ]
                            else:
                                raw_dict[raw_col.field_name] = _decode_row_column_value(
                                    row_c_elems,
                                    c_let,
                                    is_id=False,
                                    physical_row_num=physical_row_num,
                                    covered_by_col=covered_by_col,
                                    sheet_contract=sheet_contract,
                                    sheet_name=sheet_name,
                                    raw_col_by_letter=raw_col_by_letter,
                                    shared_strings=shared_strings,
                                )

                        rows_inputs.append(
                            SourceRowInput(
                                stable_id=parsed_uuid, source_values=raw_dict
                            )
                        )
                        locations[parsed_uuid] = SourceRowLocation(
                            sheet_name=sheet_name,
                            physical_row_number=physical_row_num,
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
        raise XlsxHeaderError(
            REASON_HEADER_MISSING_ROW,
            sheet_name=sheet_name,
            physical_row_number=1,
        )

    return rows_inputs, locations


def _validate_package_content_types(
    zf: zipfile.ZipFile,
) -> tuple[str, str | None]:
    """Validate [Content_Types].xml and return workbook & shared strings paths."""
    if "[Content_Types].xml" not in zf.namelist():
        raise XlsxPackageError(REASON_PACKAGE_MISSING_CONTENT_TYPES)

    parser = _get_secure_xml_parser()
    with zf.open("[Content_Types].xml", "r") as f:
        try:
            ct_tree = etree.parse(f, parser=parser)
        except Exception as e:
            raise XlsxPackageError(REASON_PACKAGE_INVALID_CONTENT_TYPES) from e

    root = ct_tree.getroot()
    root_tag = root.tag
    if not (root_tag == "Types" or root_tag == f"{{{_NS_CONTENT_TYPES}}}Types"):
        raise XlsxPackageError(REASON_PACKAGE_INVALID_CONTENT_TYPES)

    workbook_part: str | None = None
    shared_strings_part: str | None = None

    for elem in root:
        localname = etree.QName(elem).localname
        if localname == "Override":
            part_name = elem.get("PartName") or ""
            content_type = (elem.get("ContentType") or "").strip()

            if content_type in _FORBIDDEN_WORKBOOK_CONTENT_TYPES:
                raise XlsxPackageError(REASON_PACKAGE_FORBIDDEN_CONTENT_TYPE)

            if content_type in _VALID_WORKBOOK_CONTENT_TYPES:
                workbook_part = part_name.lstrip("/")

            if content_type.endswith("sharedStrings+xml"):
                shared_strings_part = part_name.lstrip("/")

    return workbook_part or "", shared_strings_part


def _read_xlsx_from_zip(zf: zipfile.ZipFile) -> XlsxSourceReadResult:
    """Core extraction algorithm from an open ZipFile archive."""
    # Step 0: Check duplicate ZIP member names (R2)
    names = [info.filename for info in zf.infolist()]
    if len(names) != len(set(names)):
        raise XlsxPackageError(REASON_PACKAGE_DUPLICATE_ZIP_ENTRY)

    # Step 1: Validate [Content_Types].xml
    ct_wb_part, ct_sst_part = _validate_package_content_types(zf)

    # Step 2: Parse root package relationships (_rels/.rels)
    if "_rels/.rels" not in zf.namelist():
        raise XlsxPackageError(REASON_PACKAGE_MISSING_ROOT_RELS)

    root_rels = _parse_relationships_file(zf, "_rels/.rels", "")
    workbook_path: str | None = None

    for _, (r_type, r_target) in root_rels.items():
        if r_type in _REL_TYPE_OFFICE_DOC:
            workbook_path = r_target
            break

    if not workbook_path or workbook_path not in zf.namelist():
        raise XlsxPackageError(REASON_PACKAGE_MISSING_WORKBOOK)

    if ct_wb_part and workbook_path != ct_wb_part:
        # If Content_Types declared a different workbook part, ensure matching
        pass

    # Step 3: Parse workbook relationships (e.g. xl/_rels/workbook.xml.rels)
    wb_dir = posixpath.dirname(workbook_path)
    wb_filename = posixpath.basename(workbook_path)
    wb_rels_path = posixpath.join(wb_dir, "_rels", f"{wb_filename}.rels")

    wb_rels = _parse_relationships_file(zf, wb_rels_path, workbook_path)

    # Find shared strings part if present
    shared_strings_path: str | None = None
    for _, (r_type, r_target) in wb_rels.items():
        if r_type in _REL_TYPE_SHARED_STRINGS:
            shared_strings_path = r_target
            break

    if not shared_strings_path and ct_sst_part:
        shared_strings_path = ct_sst_part

    shared_strings: list[str] = []
    if shared_strings_path:
        shared_strings = _parse_shared_strings_table(zf, shared_strings_path)

    # Step 4: Parse workbook.xml to locate sheet declarations
    parser = _get_secure_xml_parser()
    with zf.open(workbook_path, "r") as f:
        try:
            wb_tree = etree.parse(f, parser=parser)
        except Exception as e:
            raise XlsxStructureError(REASON_STRUCTURE_MALFORMED_XML) from e

    wb_root = wb_tree.getroot()
    declared_sheets: dict[str, str] = {}  # sheet_name -> rId

    for sheet_elem in wb_root.iter():
        localname = etree.QName(sheet_elem).localname
        if localname == "sheet":
            s_name = sheet_elem.get("name")
            if not s_name:
                continue

            r_id = (
                sheet_elem.get(f"{{{_NS_REL_OFFICE_TRANS}}}id")
                or sheet_elem.get(f"{{{_NS_REL_OFFICE_STRICT}}}id")
                or sheet_elem.get("id")
            )
            if not r_id:
                for k, v in sheet_elem.attrib.items():
                    if k == "id" or k.endswith("}id"):
                        r_id = v
                        break

            if not r_id:
                raise XlsxStructureError(
                    REASON_STRUCTURE_MISSING_SHEET_DECLARATION,
                    sheet_name=s_name,
                )

            if s_name in declared_sheets:
                raise XlsxStructureError(
                    REASON_STRUCTURE_DUPLICATE_SHEET_DECLARATION,
                    sheet_name=s_name,
                )

            declared_sheets[s_name] = r_id

    # Step 5: Verify exactly all 4 approved sheets are declared
    approved_sheets = tuple(RAW_CONTRACT_REGISTRY.sheets.keys())
    missing_approved = [s for s in approved_sheets if s not in declared_sheets]
    if missing_approved:
        raise XlsxStructureError(REASON_STRUCTURE_MISSING_APPROVED_SHEETS)

    # Map each approved sheet to worksheet XML part path
    sheet_parts: dict[str, str] = {}
    seen_part_paths: set[str] = set()

    for s_name in approved_sheets:
        r_id = declared_sheets[s_name]
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

        if ws_path in seen_part_paths:
            raise XlsxStructureError(
                REASON_STRUCTURE_DUPLICATE_WORKSHEET_PART,
                sheet_name=s_name,
            )

        seen_part_paths.add(ws_path)
        sheet_parts[s_name] = ws_path

    # Step 6: Read and parse each approved sheet
    all_sheet_inputs: list[SourceSheetInput] = []
    all_locations: dict[uuid.UUID, SourceRowLocation] = {}

    for s_name in approved_sheets:
        ws_path = sheet_parts[s_name]
        sheet_contract = RAW_CONTRACT_REGISTRY.sheets[s_name]
        s_rows, s_locations = _read_sheet_snapshot_and_locations(
            zf, ws_path, s_name, sheet_contract, shared_strings
        )
        all_sheet_inputs.append(SourceSheetInput(sheet_name=s_name, rows=s_rows))

        for u, loc in s_locations.items():
            if u in all_locations:
                raise DuplicateIdentityError(REASON_IDENTITY_DUPLICATE_UUID)
            all_locations[u] = loc

    # Step 7: Construct WP-04 snapshot
    snapshot = build_source_workbook_snapshot(all_sheet_inputs)

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
    except Exception as e:
        raise XlsxPackageError(REASON_PACKAGE_CORRUPT_ZIP) from e
