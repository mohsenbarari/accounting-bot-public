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

_REL_TYPE_OFFICE_DOC = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
    "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument",
)
_REL_TYPE_WORKSHEET = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
    "http://purl.oclc.org/ooxml/officeDocument/relationships/worksheet",
)
_REL_TYPE_SHARED_STRINGS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings",
    "http://purl.oclc.org/ooxml/officeDocument/relationships/sharedStrings",
)

_TAG_ROW = (
    f"{{{_NS_SPREADSHEETML_TRANS}}}row",
    f"{{{_NS_SPREADSHEETML_STRICT}}}row",
    "row",
)
_TAG_F = (
    f"{{{_NS_SPREADSHEETML_TRANS}}}f",
    f"{{{_NS_SPREADSHEETML_STRICT}}}f",
    "f",
)
_TAG_SI = (
    f"{{{_NS_SPREADSHEETML_TRANS}}}si",
    f"{{{_NS_SPREADSHEETML_STRICT}}}si",
    "si",
)

_CELL_REF_REGEX = re.compile(r"^([A-Za-z]{1,3})([1-9][0-9]*)$")
_OOXML_ESCAPE_REGEX = re.compile(r"_x([0-9a-fA-F]{4})_")


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
            msg = f"Unknown sheet '{self.sheet_name}' in SourceRowLocation"
            raise XlsxStructureError(msg, sheet_name=self.sheet_name)
        if (
            isinstance(self.physical_row_number, bool)
            or not isinstance(self.physical_row_number, int)
            or not (MIN_PHYSICAL_ROW <= self.physical_row_number <= MAX_PHYSICAL_ROW)
        ):
            msg = (
                f"physical_row_number must be an integer between {MIN_PHYSICAL_ROW} "
                f"and {MAX_PHYSICAL_ROW}, got {self.physical_row_number!r}"
            )
            raise XlsxStructureError(
                msg,
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
            msg = (
                f"version must be '{XLSX_SOURCE_READER_VERSION}', got {self.version!r}"
            )
            raise XlsxStructureError(msg)

        if not isinstance(self.snapshot, ValidatedSourceWorkbookSnapshot):
            msg = (
                "snapshot must be ValidatedSourceWorkbookSnapshot, "
                f"got {type(self.snapshot).__name__}"
            )
            raise XlsxStructureError(msg)

        if isinstance(self.locations_by_uuid, (Mapping, MappingProxyType)):
            loc_dict = dict(self.locations_by_uuid)
        else:
            msg = (
                "locations_by_uuid must be a Mapping, "
                f"got {type(self.locations_by_uuid).__name__}"
            )
            raise XlsxStructureError(msg)

        # Invariant 1: Exact match with snapshot identities
        snapshot_uuids = set(self.snapshot.all_rows_by_id.keys())
        loc_uuids = set(loc_dict.keys())
        if loc_uuids != snapshot_uuids:
            msg = "locations_by_uuid keys must match snapshot row identities exactly"
            raise XlsxStructureError(msg)

        # Invariant 2: Location sheet matches snapshot row sheet
        sheet_rows_seen: dict[str, set[int]] = {
            s: set() for s in RAW_CONTRACT_REGISTRY.sheets
        }
        for u, loc in loc_dict.items():
            if not isinstance(loc, SourceRowLocation):
                msg = f"locations_by_uuid values must be SourceRowLocation, got {loc!r}"
                raise XlsxStructureError(msg)
            expected_sheet = self.snapshot.all_rows_by_id[u].sheet_name
            if loc.sheet_name != expected_sheet:
                msg = (
                    f"Location sheet '{loc.sheet_name}' does not match "
                    f"snapshot row home sheet '{expected_sheet}' for UUID {u}"
                )
                raise XlsxStructureError(msg, sheet_name=loc.sheet_name)
            if loc.physical_row_number in sheet_rows_seen[loc.sheet_name]:
                msg = (
                    f"Duplicate physical row number {loc.physical_row_number} "
                    f"in sheet '{loc.sheet_name}'"
                )
                raise XlsxStructureError(
                    msg,
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
    """Decode single-pass OpenXML _xHHHH_ character escapes."""
    if "_x" not in text:
        return text

    def _replace_match(match: re.Match[str]) -> str:
        hex_code = match.group(1)
        code_point = int(hex_code, 16)
        return chr(code_point)

    decoded = _OOXML_ESCAPE_REGEX.sub(_replace_match, text)
    try:
        return decoded.encode("utf-16", "surrogatepass").decode("utf-16")
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
    match = _CELL_REF_REGEX.match(ref)
    if not match:
        msg = f"Invalid cell coordinate reference format: {ref!r}"
        raise XlsxStructureError(msg, cell_ref=ref)

    col_letter = match.group(1).upper()
    row_num = int(match.group(2))

    if not is_valid_excel_column(col_letter):
        msg = f"Invalid Excel column letter '{col_letter}' in cell '{ref}'"
        raise XlsxStructureError(msg, cell_ref=ref)

    if not (1 <= row_num <= MAX_PHYSICAL_ROW):
        msg = f"Excel row number {row_num} out of bounds (1..{MAX_PHYSICAL_ROW})"
        raise XlsxStructureError(msg, cell_ref=ref, physical_row_number=row_num)

    col_num = _parse_col_and_num(col_letter)
    return col_letter, col_num, row_num


def _parse_range_ref(ref: str) -> tuple[int, int, int, int]:
    """Parse A1 cell or rectangle into (min_col, min_row, max_col, max_row)."""
    if ":" in ref:
        parts = ref.split(":")
        if len(parts) != 2:
            msg = f"Invalid formula coverage range format: {ref!r}"
            raise XlsxFormulaCoverageError(msg)
        _, c1, r1 = _parse_cell_ref(parts[0])
        _, c2, r2 = _parse_cell_ref(parts[1])
        min_c, max_c = min(c1, c2), max(c1, c2)
        min_r, max_r = min(r1, r2), max(r1, r2)
        return min_c, min_r, max_c, max_r
    else:
        _, c, r = _parse_cell_ref(ref)
        return c, r, c, r


def _resolve_package_target(base_part_path: str, target: str) -> str:
    """Resolve an internal OPC target path relative to base part directory safely."""
    if ":" in target or "#" in target or "?" in target:
        msg = f"External or URI target not allowed in internal workbook: {target!r}"
        raise XlsxPackageError(msg)

    if target.startswith("/"):
        norm = posixpath.normpath(target.lstrip("/"))
    else:
        base_dir = posixpath.dirname(base_part_path)
        norm = posixpath.normpath(posixpath.join(base_dir, target))

    if norm.startswith("..") or norm.startswith("/"):
        msg = f"Target path escapes package root: {target!r}"
        raise XlsxPackageError(msg)

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
            msg = f"Malformed XML in relationships part '{rels_path}'"
            raise XlsxPackageError(msg) from e

    root = tree.getroot()
    for rel_elem in root.iter():
        tag = rel_elem.tag
        if tag == "Relationship" or tag.endswith("}Relationship"):
            r_id = rel_elem.get("Id")
            r_type = rel_elem.get("Type") or ""
            r_target = rel_elem.get("Target") or ""
            target_mode = rel_elem.get("TargetMode") or ""

            if target_mode.lower() == "external":
                continue

            if r_id and r_target:
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
        msg = f"Missing shared strings part '{shared_strings_path}'"
        raise XlsxPackageError(msg)

    strings: list[str] = []

    with zf.open(shared_strings_path, "r") as stream:
        context = _secure_iterparse(stream, events=("end",), tag=_TAG_SI)
        for _, elem in context:
            text_fragments: list[str] = []
            for child in elem.iter():
                c_tag = child.tag
                if c_tag == "t" or c_tag.endswith("}t"):
                    text_fragments.append(child.text or "")

            full_raw_text = "".join(text_fragments)
            decoded_text = _decode_ooxml_escapes(full_raw_text)
            strings.append(decoded_text)

            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]

    return strings


def _discover_array_formula_coverage(
    zf: zipfile.ZipFile, worksheet_path: str, sheet_name: str
) -> list[tuple[int, int, int, int]]:
    """Pass 1: Discover array and data-table formula coverage bounding boxes."""
    coverage_ranges: list[tuple[int, int, int, int]] = []

    with zf.open(worksheet_path, "r") as stream:
        context = _secure_iterparse(stream, events=("end",), tag=_TAG_F)
        for _, elem in context:
            f_type = (elem.get("t") or "").strip().lower()
            if f_type in ("array", "datatable"):
                ref = elem.get("ref")
                if not ref:
                    msg = (
                        f"Array/data-table formula missing required 'ref' "
                        f"attribute in sheet '{sheet_name}'"
                    )
                    raise XlsxFormulaCoverageError(msg, sheet_name=sheet_name)

                parent = elem.getparent()
                anchor_ref = parent.get("r") if parent is not None else None
                if not anchor_ref:
                    msg = (
                        "Array formula cell missing 'r' coordinate attribute "
                        f"in sheet '{sheet_name}'"
                    )
                    raise XlsxFormulaCoverageError(msg, sheet_name=sheet_name)

                try:
                    _, a_col, a_row = _parse_cell_ref(anchor_ref)
                    min_c, min_r, max_c, max_r = _parse_range_ref(ref)
                except XlsxSourceReadError as e:
                    msg = (
                        f"Invalid formula coverage range '{ref}' "
                        f"in sheet '{sheet_name}': {e.reason}"
                    )
                    raise XlsxFormulaCoverageError(msg, sheet_name=sheet_name) from e

                if not (min_c <= a_col <= max_c and min_r <= a_row <= max_r):
                    msg = (
                        f"Array formula coverage range '{ref}' does not "
                        f"contain anchor cell '{anchor_ref}' in '{sheet_name}'"
                    )
                    raise XlsxFormulaCoverageError(
                        msg, sheet_name=sheet_name, cell_ref=anchor_ref
                    )

                coverage_ranges.append((min_c, min_r, max_c, max_r))

            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]

    return coverage_ranges


def _decode_cell_literal_value(
    c_elem: etree._Element,
    col_letter: str,
    physical_row_num: int,
    sheet_name: str,
    raw_col_by_letter: dict[str, RawColumnContract],
    is_stable_id: bool,
    shared_strings: list[str],
) -> Any:
    """Decode physical cell literal XML value into exact Python type or Decimal."""
    cell_type = (c_elem.get("t") or "").strip()
    cell_ref = c_elem.get("r") or f"{col_letter}{physical_row_num}"
    is_raw_col = col_letter in raw_col_by_letter

    # Check for forbidden cell types in retained input
    if cell_type == "b":
        if is_raw_col or is_stable_id:
            msg = f"Boolean cell 'b' rejected in retained column '{col_letter}'"
            raise XlsxCellError(
                msg,
                sheet_name=sheet_name,
                cell_ref=cell_ref,
                physical_row_number=physical_row_num,
            )
        return None

    if cell_type == "e":
        if is_raw_col or is_stable_id:
            msg = f"Error cell 'e' rejected in retained column '{col_letter}'"
            raise XlsxCellError(
                msg,
                sheet_name=sheet_name,
                cell_ref=cell_ref,
                physical_row_number=physical_row_num,
            )
        return None

    if cell_type == "d":
        if is_raw_col or is_stable_id:
            msg = (
                f"Date cell type 'd' rejected in column '{col_letter}' "
                "(text date required)"
            )
            raise XlsxCellError(
                msg,
                sheet_name=sheet_name,
                cell_ref=cell_ref,
                physical_row_number=physical_row_num,
            )
        return None

    # Shared string (t="s")
    if cell_type == "s":
        v_elem = c_elem.find("{*}v")
        if v_elem is None or v_elem.text is None:
            return None
        v_str = v_elem.text.strip()
        if not v_str:
            return None
        try:
            s_idx = int(v_str)
        except ValueError as e:
            msg = f"Invalid shared string index {v_str!r}"
            raise XlsxCellError(
                msg,
                sheet_name=sheet_name,
                cell_ref=cell_ref,
                physical_row_number=physical_row_num,
            ) from e
        if s_idx < 0 or s_idx >= len(shared_strings):
            msg = (
                f"Shared string index {s_idx} out of range "
                f"(total: {len(shared_strings)})"
            )
            raise XlsxCellError(
                msg,
                sheet_name=sheet_name,
                cell_ref=cell_ref,
                physical_row_number=physical_row_num,
            )
        return shared_strings[s_idx]

    # Inline string (t="inlineStr")
    if cell_type == "inlineStr":
        is_elem = c_elem.find("{*}is")
        if is_elem is None:
            return None
        fragments: list[str] = []
        for child in is_elem.iter():
            tag = child.tag
            if tag == "t" or tag.endswith("}t"):
                fragments.append(child.text or "")
        return _decode_ooxml_escapes("".join(fragments))

    # Direct string (t="str")
    if cell_type == "str":
        v_elem = c_elem.find("{*}v")
        if v_elem is None or v_elem.text is None:
            return None
        return _decode_ooxml_escapes(v_elem.text)

    # Numeric XML (t="n" or omitted)
    v_elem = c_elem.find("{*}v")
    if v_elem is None or v_elem.text is None:
        return None
    raw_num_str = v_elem.text.strip()
    if not raw_num_str:
        return None

    # If this column is stable_id -> numeric XML is forbidden
    if is_stable_id:
        msg = (
            f"Numeric XML not allowed for stable_id column '{col_letter}' "
            "(must be text UUID)"
        )
        raise XlsxCellError(
            msg,
            sheet_name=sheet_name,
            cell_ref=cell_ref,
            physical_row_number=physical_row_num,
        )

    # If this column is a raw column, check its expected value kind
    if is_raw_col:
        field_contract = raw_col_by_letter[col_letter]
        if field_contract.field_name == "date_raw":
            msg = (
                f"Numeric XML not allowed for date_raw column '{col_letter}' "
                "(must be Jalali text date)"
            )
            raise XlsxCellError(
                msg,
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
                msg = (
                    f"Invalid numeric lexeme {raw_num_str!r} for "
                    f"Decimal column '{col_letter}'"
                )
                raise XlsxCellError(
                    msg,
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

    coverage_ranges = _discover_array_formula_coverage(zf, worksheet_path, sheet_name)

    rows_inputs: list[SourceRowInput] = []
    locations: dict[uuid.UUID, SourceRowLocation] = {}

    seen_physical_rows: set[int] = set()
    header_checked = False

    with zf.open(worksheet_path, "r") as stream:
        context = _secure_iterparse(stream, events=("end",), tag=_TAG_ROW)
        for _, elem in context:
            row_r = elem.get("r")
            if not row_r:
                msg = f"Row container missing 'r' attribute in sheet '{sheet_name}'"
                raise XlsxStructureError(msg, sheet_name=sheet_name)

            try:
                physical_row_num = int(row_r)
            except ValueError as e:
                msg = f"Invalid row number {row_r!r} in sheet '{sheet_name}'"
                raise XlsxStructureError(msg, sheet_name=sheet_name) from e

            if not (1 <= physical_row_num <= MAX_PHYSICAL_ROW):
                msg = (
                    f"Row number {physical_row_num} out of Excel bounds "
                    f"(1..{MAX_PHYSICAL_ROW}) in sheet '{sheet_name}'"
                )
                raise XlsxStructureError(
                    msg,
                    sheet_name=sheet_name,
                    physical_row_number=physical_row_num,
                )

            if physical_row_num in seen_physical_rows:
                msg = (
                    f"Duplicate row container for row {physical_row_num} "
                    f"in sheet '{sheet_name}'"
                )
                raise XlsxStructureError(
                    msg,
                    sheet_name=sheet_name,
                    physical_row_number=physical_row_num,
                )
            seen_physical_rows.add(physical_row_num)

            # Parse cell elements in this row
            row_cells_by_col: dict[str, Any] = {}
            row_formula_cols: set[str] = set()
            seen_cell_cols: set[str] = set()

            for c_elem in elem:
                c_tag = c_elem.tag
                if c_tag == "c" or c_tag.endswith("}c"):
                    cell_ref = c_elem.get("r")
                    if not cell_ref:
                        msg = (
                            "Cell element missing 'r' attribute "
                            f"in sheet '{sheet_name}'"
                        )
                        raise XlsxStructureError(
                            msg,
                            sheet_name=sheet_name,
                            physical_row_number=physical_row_num,
                        )

                    col_letter, col_num, cell_row = _parse_cell_ref(cell_ref)
                    if cell_row != physical_row_num:
                        msg = (
                            f"Cell '{cell_ref}' coordinate row {cell_row} "
                            f"does not match row container {physical_row_num} "
                            f"in sheet '{sheet_name}'"
                        )
                        raise XlsxStructureError(
                            msg,
                            sheet_name=sheet_name,
                            cell_ref=cell_ref,
                            physical_row_number=physical_row_num,
                        )

                    if col_letter in seen_cell_cols:
                        msg = (
                            f"Duplicate cell coordinate '{cell_ref}' "
                            f"in sheet '{sheet_name}'"
                        )
                        raise XlsxStructureError(
                            msg,
                            sheet_name=sheet_name,
                            cell_ref=cell_ref,
                            physical_row_number=physical_row_num,
                        )
                    seen_cell_cols.add(col_letter)

                    # Check formula element presence
                    f_elem = c_elem.find("{*}f")
                    is_formula = f_elem is not None

                    # Check array/dataTable coverage
                    if not is_formula and coverage_ranges:
                        for min_c, min_r, max_c, max_r in coverage_ranges:
                            if (
                                min_c <= col_num <= max_c
                                and min_r <= physical_row_num <= max_r
                            ):
                                is_formula = True
                                break

                    if is_formula:
                        row_formula_cols.add(col_letter)
                        row_cells_by_col[col_letter] = None
                    else:
                        val = _decode_cell_literal_value(
                            c_elem,
                            col_letter,
                            physical_row_num,
                            sheet_name,
                            raw_col_by_letter,
                            col_letter == stable_id_col,
                            shared_strings,
                        )
                        row_cells_by_col[col_letter] = val

            # Process Row 1: Headers
            if physical_row_num == 1:
                header_checked = True
                for req_col, expected_header in req_headers.items():
                    if req_col in row_formula_cols:
                        msg = (
                            f"Required header column '{req_col}' has formula "
                            f"in sheet '{sheet_name}'"
                        )
                        raise XlsxHeaderError(
                            msg,
                            sheet_name=sheet_name,
                            cell_ref=f"{req_col}1",
                            physical_row_number=1,
                        )
                    actual_header = row_cells_by_col.get(req_col)
                    if (
                        actual_header is None
                        or not isinstance(actual_header, str)
                        or actual_header != expected_header
                    ):
                        msg = (
                            f"Header mismatch in '{sheet_name}' at '{req_col}': "
                            f"expected {expected_header!r}, got {actual_header!r}"
                        )
                        raise XlsxHeaderError(
                            msg,
                            sheet_name=sheet_name,
                            cell_ref=f"{req_col}1",
                            physical_row_number=1,
                        )

                # Check stable_id required header
                if req_id_header:
                    if stable_id_col in row_formula_cols:
                        msg = (
                            "Required technical ID header column "
                            f"'{stable_id_col}' contains a formula "
                            f"in sheet '{sheet_name}'"
                        )
                        raise XlsxHeaderError(
                            msg,
                            sheet_name=sheet_name,
                            cell_ref=f"{stable_id_col}1",
                            physical_row_number=1,
                        )
                    actual_id_header = row_cells_by_col.get(stable_id_col)
                    if (
                        actual_id_header is None
                        or not isinstance(actual_id_header, str)
                        or actual_id_header != req_id_header
                    ):
                        msg = (
                            f"Technical ID header mismatch in '{sheet_name}' "
                            f"at '{stable_id_col}': expected {req_id_header!r}, "
                            f"got {actual_id_header!r}"
                        )
                        raise XlsxHeaderError(
                            msg,
                            sheet_name=sheet_name,
                            cell_ref=f"{stable_id_col}1",
                            physical_row_number=1,
                        )

            # Process Rows >= 2: Data Rows
            elif physical_row_num >= MIN_PHYSICAL_ROW:
                is_active = False
                for act_col in activity_cols:
                    val = row_cells_by_col.get(act_col)
                    if val is not None:
                        if isinstance(val, (int, Decimal)):
                            is_active = True
                            break
                        if isinstance(val, str) and val.strip() != "":
                            is_active = True
                            break

                if is_active:
                    id_val = row_cells_by_col.get(stable_id_col)

                    if (
                        id_val is None
                        or not isinstance(id_val, str)
                        or not id_val.strip()
                    ):
                        msg = (
                            f"Active row {physical_row_num} missing UUIDv7 "
                            f"in column '{stable_id_col}' in sheet '{sheet_name}'"
                        )
                        raise XlsxIdentityError(
                            msg,
                            sheet_name=sheet_name,
                            cell_ref=f"{stable_id_col}{physical_row_num}",
                            physical_row_number=physical_row_num,
                        )

                    id_clean = id_val.strip()
                    try:
                        parsed_uuid = uuid.UUID(id_clean)
                    except ValueError as e:
                        msg = (
                            f"Malformed UUID '{id_clean}' in row "
                            f"{physical_row_num} in sheet '{sheet_name}'"
                        )
                        raise XlsxIdentityError(
                            msg,
                            sheet_name=sheet_name,
                            cell_ref=f"{stable_id_col}{physical_row_num}",
                            physical_row_number=physical_row_num,
                        ) from e

                    if parsed_uuid.version != 7:
                        msg = (
                            f"UUID '{id_clean}' is version {parsed_uuid.version}, "
                            f"must be version 7 in sheet '{sheet_name}'"
                        )
                        raise XlsxIdentityError(
                            msg,
                            sheet_name=sheet_name,
                            cell_ref=f"{stable_id_col}{physical_row_num}",
                            physical_row_number=physical_row_num,
                        )

                    if str(parsed_uuid).lower() != id_clean.lower():
                        msg = (
                            f"Non-canonical UUID representation '{id_clean}' "
                            f"in sheet '{sheet_name}'"
                        )
                        raise XlsxIdentityError(
                            msg,
                            sheet_name=sheet_name,
                            cell_ref=f"{stable_id_col}{physical_row_num}",
                            physical_row_number=physical_row_num,
                        )

                    if parsed_uuid in locations:
                        msg = (
                            f"Duplicate UUIDv7 '{parsed_uuid}' detected in row "
                            f"{physical_row_num} in sheet '{sheet_name}'"
                        )
                        raise DuplicateIdentityError(msg)

                    raw_dict: dict[str, Any] = {}
                    for raw_col in sheet_contract.raw_columns:
                        raw_dict[raw_col.field_name] = row_cells_by_col.get(
                            raw_col.column_letter
                        )

                    rows_inputs.append(
                        SourceRowInput(stable_id=parsed_uuid, source_values=raw_dict)
                    )
                    locations[parsed_uuid] = SourceRowLocation(
                        sheet_name=sheet_name,
                        physical_row_number=physical_row_num,
                    )

            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]

    if not header_checked:
        msg = f"Missing row 1 headers in sheet '{sheet_name}'"
        raise XlsxHeaderError(msg, sheet_name=sheet_name, physical_row_number=1)

    return rows_inputs, locations


def _read_xlsx_from_zip(zf: zipfile.ZipFile) -> XlsxSourceReadResult:
    """Core extraction algorithm from an open ZipFile archive."""
    # Step 1: Parse root package relationships (_rels/.rels)
    root_rels = _parse_relationships_file(zf, "_rels/.rels", "")
    workbook_path: str | None = None

    for _, (r_type, r_target) in root_rels.items():
        if r_type in _REL_TYPE_OFFICE_DOC or r_type.endswith("/officeDocument"):
            workbook_path = r_target
            break

    if not workbook_path or workbook_path not in zf.namelist():
        msg = "Cannot locate workbook part in package relationships"
        raise XlsxPackageError(msg)

    # Step 2: Parse workbook relationships (e.g. xl/_rels/workbook.xml.rels)
    wb_dir = posixpath.dirname(workbook_path)
    wb_filename = posixpath.basename(workbook_path)
    wb_rels_path = posixpath.join(wb_dir, "_rels", f"{wb_filename}.rels")

    wb_rels = _parse_relationships_file(zf, wb_rels_path, workbook_path)

    # Find shared strings part if present
    shared_strings_path: str | None = None
    for _, (r_type, r_target) in wb_rels.items():
        if r_type in _REL_TYPE_SHARED_STRINGS or r_type.endswith("/sharedStrings"):
            shared_strings_path = r_target
            break

    shared_strings: list[str] = []
    if shared_strings_path:
        shared_strings = _parse_shared_strings_table(zf, shared_strings_path)

    # Step 3: Parse workbook.xml to locate sheet declarations
    parser = _get_secure_xml_parser()
    with zf.open(workbook_path, "r") as f:
        try:
            wb_tree = etree.parse(f, parser=parser)
        except Exception as e:
            msg = f"Malformed XML in workbook part '{workbook_path}'"
            raise XlsxStructureError(msg) from e

    wb_root = wb_tree.getroot()
    declared_sheets: dict[str, str] = {}  # sheet_name -> rId

    for sheet_elem in wb_root.iter():
        tag = sheet_elem.tag
        if tag == "sheet" or tag.endswith("}sheet"):
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
                msg = f"Sheet '{s_name}' declaration missing relationship ID"
                raise XlsxStructureError(msg, sheet_name=s_name)

            if s_name in declared_sheets:
                msg = f"Duplicate sheet declaration for '{s_name}' in workbook"
                raise XlsxStructureError(msg, sheet_name=s_name)

            declared_sheets[s_name] = r_id

    # Step 4: Verify exactly all 4 approved sheets are declared
    approved_sheets = tuple(RAW_CONTRACT_REGISTRY.sheets.keys())
    missing_approved = [s for s in approved_sheets if s not in declared_sheets]
    if missing_approved:
        msg = f"Missing required approved sheet(s) in workbook: {missing_approved}"
        raise XlsxStructureError(msg)

    # Map each approved sheet to worksheet XML part path
    sheet_parts: dict[str, str] = {}
    seen_part_paths: set[str] = set()

    for s_name in approved_sheets:
        r_id = declared_sheets[s_name]
        if r_id not in wb_rels:
            msg = (
                f"Relationship ID '{r_id}' for sheet '{s_name}' "
                "not found in workbook relationships"
            )
            raise XlsxStructureError(msg, sheet_name=s_name)

        r_type, ws_path = wb_rels[r_id]
        if not (r_type in _REL_TYPE_WORKSHEET or r_type.endswith("/worksheet")):
            msg = (
                f"Relationship for sheet '{s_name}' has invalid type '{r_type}', "
                "expected worksheet"
            )
            raise XlsxStructureError(msg, sheet_name=s_name)

        if ws_path not in zf.namelist():
            msg = (
                f"Worksheet part '{ws_path}' for sheet '{s_name}' not found in archive"
            )
            raise XlsxPackageError(msg, sheet_name=s_name)

        if ws_path in seen_part_paths:
            msg = f"Multiple source sheets point to the same worksheet part '{ws_path}'"
            raise XlsxStructureError(msg, sheet_name=s_name)

        seen_part_paths.add(ws_path)
        sheet_parts[s_name] = ws_path

    # Step 5: Read and parse each approved sheet
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
                msg = (
                    f"Duplicate UUIDv7 '{u}' detected across sheets: "
                    f"'{all_locations[u].sheet_name}' vs '{loc.sheet_name}'"
                )
                raise DuplicateIdentityError(msg)
            all_locations[u] = loc

    # Step 6: Construct WP-04 snapshot
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
        msg = f"XLSX source file does not exist: {file_path.name}"
        raise XlsxPackageError(msg)

    if not file_path.is_file():
        msg = f"XLSX source path is not a regular file: {file_path.name}"
        raise XlsxPackageError(msg)

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            return _read_xlsx_from_zip(zf)
    except zipfile.BadZipFile as e:
        msg = "Corrupted or non-ZIP XLSX archive"
        raise XlsxPackageError(msg) from e
    except XlsxSourceReadError:
        raise
    except Exception as e:
        msg = f"Unexpected error reading XLSX package: {type(e).__name__}"
        raise XlsxPackageError(msg) from e
