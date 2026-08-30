"""Authoritative raw Excel input contracts registry.

Defines the frozen, versioned structural boundary for the four approved Excel
input sheets ('خرید-فروش', 'دریافت-پرداخت', 'ورود-خروج', 'لیست کسبه') according
to Roadmap sections 5.1, 5.2, 5.4, 5.5 and O-03/O-25/O-26.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

RAW_SOURCE_CONTRACT_VERSION: str = "raw-source-contract.v1"
MAX_EXCEL_COLUMN_INDEX: int = 16384  # Excel column 'XFD'


def excel_column_to_number(column_letter: str) -> int:
    """Convert uppercase Excel column string to 1-based index."""
    col_num = 0
    for char in column_letter:
        if not ("A" <= char <= "Z"):
            return 0
        col_num = col_num * 26 + (ord(char) - ord("A") + 1)
    return col_num


def is_valid_excel_column(column_letter: str) -> bool:
    """Check if a column letter is a valid Excel column address (A through XFD)."""
    if not (1 <= len(column_letter) <= 3):
        return False
    if not column_letter.isalpha() or not column_letter.isupper():
        return False
    col_num = excel_column_to_number(column_letter)
    return 1 <= col_num <= MAX_EXCEL_COLUMN_INDEX


class ColumnRole(StrEnum):
    """Classification role of a column in an Excel workbook sheet."""

    LITERAL_RAW_INPUT = "literal_raw_input"
    STABLE_ID = "stable_id"
    KNOWN_DERIVED = "known_derived"


class ValueKind(StrEnum):
    """Expected downstream value kind without Float."""

    RAW_TEXT = "raw_text"
    INTEGER_TOMAN = "integer_toman"
    DECIMAL = "decimal"
    UUID7 = "uuid7"


class CellClassification(StrEnum):
    """Classification of an individual cell for extraction and filtering."""

    RAW_INPUT_CANDIDATE = "raw_input_candidate"
    STABLE_ID = "stable_id"
    FORMULA_EXCLUDED = "formula_excluded"
    KNOWN_DERIVED_EXCLUDED = "known_derived_excluded"
    UNLISTED_EXCLUDED = "unlisted_excluded"


class ContractError(Exception):
    """Base exception for raw input contract errors."""


class UnknownSheetError(ContractError):
    """Raised when an unapproved sheet name is requested."""


class ContractValidationError(ContractError):
    """Raised when a sheet or registry contract violates structural invariants."""


@dataclass(frozen=True, slots=True)
class RawColumnContract:
    """Contract definition for a single column in an Excel sheet."""

    column_letter: str
    field_name: str
    role: ColumnRole
    value_kind: ValueKind
    required_header: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "column_letter", self.column_letter.strip().upper())
        if not is_valid_excel_column(self.column_letter):
            msg = (
                f"Invalid Excel column address '{self.column_letter}'. "
                "Must be a valid uppercase Excel column between 'A' and 'XFD'."
            )
            raise ContractValidationError(msg)


@dataclass(frozen=True, slots=True)
class RawSheetContract:
    """Immutable contract defining the raw boundary of an Excel sheet."""

    sheet_name: str
    stable_id_column: RawColumnContract
    raw_columns: tuple[RawColumnContract, ...]
    activity_columns: tuple[str, ...]
    derived_columns: tuple[RawColumnContract, ...]
    contract_version: str = RAW_SOURCE_CONTRACT_VERSION

    def __post_init__(self) -> None:
        self._validate_invariants()

    def _validate_invariants(self) -> None:
        if not self.sheet_name.strip():
            raise ContractValidationError("Sheet name cannot be empty")

        if self.stable_id_column.role != ColumnRole.STABLE_ID:
            msg = (
                f"Stable ID column '{self.stable_id_column.column_letter}' "
                "must have role STABLE_ID"
            )
            raise ContractValidationError(msg)
        if self.stable_id_column.value_kind != ValueKind.UUID7:
            msg = (
                f"Stable ID column '{self.stable_id_column.column_letter}' "
                "must have value kind UUID7"
            )
            raise ContractValidationError(msg)

        all_fields: dict[str, str] = {
            self.stable_id_column.field_name: (
                f"stable_id '{self.stable_id_column.column_letter}'"
            )
        }

        raw_letters = set()
        for col in self.raw_columns:
            if col.role != ColumnRole.LITERAL_RAW_INPUT:
                msg = (
                    f"Raw column '{col.column_letter}' must have role LITERAL_RAW_INPUT"
                )
                raise ContractValidationError(msg)
            if col.column_letter in raw_letters:
                msg = (
                    f"Duplicate raw column letter '{col.column_letter}' "
                    f"in sheet '{self.sheet_name}'"
                )
                raise ContractValidationError(msg)
            if col.field_name in all_fields:
                msg = (
                    f"Duplicate field name '{col.field_name}' in raw column "
                    f"'{col.column_letter}' collides with {all_fields[col.field_name]} "
                    f"in sheet '{self.sheet_name}'"
                )
                raise ContractValidationError(msg)
            raw_letters.add(col.column_letter)
            all_fields[col.field_name] = f"raw column '{col.column_letter}'"

        derived_letters = set()
        for col in self.derived_columns:
            if col.role != ColumnRole.KNOWN_DERIVED:
                msg = (
                    f"Derived column '{col.column_letter}' must have role KNOWN_DERIVED"
                )
                raise ContractValidationError(msg)
            if col.column_letter in derived_letters:
                msg = (
                    f"Duplicate derived column letter '{col.column_letter}' "
                    f"in sheet '{self.sheet_name}'"
                )
                raise ContractValidationError(msg)
            if col.field_name in all_fields:
                msg = (
                    f"Duplicate field name '{col.field_name}' in derived column "
                    f"'{col.column_letter}' collides with {all_fields[col.field_name]} "
                    f"in sheet '{self.sheet_name}'"
                )
                raise ContractValidationError(msg)
            derived_letters.add(col.column_letter)
            all_fields[col.field_name] = f"derived column '{col.column_letter}'"

        if self.stable_id_column.column_letter in raw_letters:
            msg = (
                f"Stable ID column '{self.stable_id_column.column_letter}' "
                "overlaps with raw columns"
            )
            raise ContractValidationError(msg)
        if self.stable_id_column.column_letter in derived_letters:
            msg = (
                f"Stable ID column '{self.stable_id_column.column_letter}' "
                "overlaps with derived columns"
            )
            raise ContractValidationError(msg)
        overlap = raw_letters.intersection(derived_letters)
        if overlap:
            raise ContractValidationError(
                f"Raw columns overlap with derived columns: {overlap}"
            )

        if not self.activity_columns:
            raise ContractValidationError(
                f"Activity columns subset cannot be empty for sheet '{self.sheet_name}'"
            )

        normalized_activity = tuple(
            col.strip().upper() for col in self.activity_columns
        )
        for act_col in normalized_activity:
            if act_col not in raw_letters:
                msg = (
                    f"Activity column '{act_col}' is not in raw input "
                    f"columns of sheet '{self.sheet_name}'"
                )
                raise ContractValidationError(msg)

    def get_column(self, column_letter: str) -> RawColumnContract | None:
        """Lookup a column contract by its letter."""
        normalized = column_letter.strip().upper()
        if self.stable_id_column.column_letter == normalized:
            return self.stable_id_column
        for col in self.raw_columns:
            if col.column_letter == normalized:
                return col
        for col in self.derived_columns:
            if col.column_letter == normalized:
                return col
        return None

    def get_raw_column_by_field(self, field_name: str) -> RawColumnContract | None:
        """Lookup a literal raw column contract by its field name."""
        for col in self.raw_columns:
            if col.field_name == field_name:
                return col
        return None

    def is_whitelisted_column(self, column_letter: str) -> bool:
        """Check whether a column letter is part of the raw input whitelist."""
        normalized = column_letter.strip().upper()
        return any(col.column_letter == normalized for col in self.raw_columns)

    def is_activity_column(self, column_letter: str) -> bool:
        """Check whether a column letter is an activity-triggering input column."""
        normalized = column_letter.strip().upper()
        return normalized in (col.strip().upper() for col in self.activity_columns)

    @property
    def required_headers_by_column(self) -> dict[str, str]:
        """Mapping of column letters to their required Persian header strings."""
        return {
            col.column_letter: col.required_header
            for col in self.raw_columns
            if col.required_header is not None
        }

    def classify_cell(
        self, column_letter: str, *, has_formula: bool = False
    ) -> CellClassification:
        """Classify a cell based on column location and formula presence.

        Does not evaluate or parse the cell value.
        """
        normalized = column_letter.strip().upper()

        if has_formula:
            return CellClassification.FORMULA_EXCLUDED

        if normalized == self.stable_id_column.column_letter:
            return CellClassification.STABLE_ID

        if self.is_whitelisted_column(normalized):
            return CellClassification.RAW_INPUT_CANDIDATE

        if any(col.column_letter == normalized for col in self.derived_columns):
            return CellClassification.KNOWN_DERIVED_EXCLUDED

        return CellClassification.UNLISTED_EXCLUDED


@dataclass(frozen=True, slots=True)
class RawContractRegistry:
    """Deeply immutable registry containing all approved raw sheet contracts."""

    sheets: Mapping[str, RawSheetContract]

    def __init__(self, sheets: Mapping[str, RawSheetContract]) -> None:
        defensive_copy = dict(sheets)
        object.__setattr__(self, "sheets", MappingProxyType(defensive_copy))
        self.validate()

    def validate(self) -> None:
        """Validate all sheet contracts and registry completeness."""
        if not self.sheets:
            raise ContractValidationError("Registry contains no sheet contracts")

        for sheet_name, sheet_contract in self.sheets.items():
            if sheet_name != sheet_contract.sheet_name:
                msg = (
                    f"Registry key '{sheet_name}' does not match "
                    f"sheet contract name '{sheet_contract.sheet_name}'"
                )
                raise ContractValidationError(msg)
            sheet_contract._validate_invariants()

    def get_sheet_contract(self, sheet_name: str) -> RawSheetContract:
        """Retrieve a sheet contract by exact sheet name.

        Raises UnknownSheetError if the sheet is not approved.
        """
        contract = self.sheets.get(sheet_name)
        if contract is None:
            raise UnknownSheetError(
                f"Unknown or unapproved sheet '{sheet_name}'. "
                f"Approved sheets: {list(self.sheets.keys())}"
            )
        return contract

    def list_sheet_names(self) -> tuple[str, ...]:
        """Return the tuple of approved sheet names in canonical order."""
        return tuple(self.sheets.keys())

    def list_sheet_contracts(self) -> tuple[RawSheetContract, ...]:
        """Return the tuple of approved sheet contracts in canonical order."""
        return tuple(self.sheets.values())

    def classify_cell(
        self, sheet_name: str, column_letter: str, *, has_formula: bool = False
    ) -> CellClassification:
        """Classify a cell in a given sheet by column address and formula presence."""
        sheet_contract = self.get_sheet_contract(sheet_name)
        return sheet_contract.classify_cell(column_letter, has_formula=has_formula)


# --- Normative v1 Sheet Contract Definitions ---

BUY_SELL_CONTRACT = RawSheetContract(
    sheet_name="خرید-فروش",
    stable_id_column=RawColumnContract(
        column_letter="Z",
        field_name="record_id",
        role=ColumnRole.STABLE_ID,
        value_kind=ValueKind.UUID7,
        required_header="record_id",
        description="Stable UUIDv7 identifier for buy/sell transaction rows",
    ),
    raw_columns=(
        RawColumnContract(
            column_letter="B",
            field_name="date_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header="تاریخ",
            description="Raw Jalali transaction date",
        ),
        RawColumnContract(
            column_letter="C",
            field_name="party_name_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header="نام",
            description="Raw party name",
        ),
        RawColumnContract(
            column_letter="D",
            field_name="transaction_type_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header="شرح",
            description="Transaction type description (buy/sell)",
        ),
        RawColumnContract(
            column_letter="E",
            field_name="item_name_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header="کالا",
            description="Raw commodity item name",
        ),
        RawColumnContract(
            column_letter="F",
            field_name="quantity_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.DECIMAL,
            required_header="مقدار",
            description="Raw item quantity or weight",
        ),
        RawColumnContract(
            column_letter="G",
            field_name="unit_price_toman_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.INTEGER_TOMAN,
            required_header="فی",
            description="Unit price in Toman integer",
        ),
        RawColumnContract(
            column_letter="H",
            field_name="discount_toman_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.INTEGER_TOMAN,
            required_header="تخفیف",
            description="Discount in Toman integer",
        ),
        RawColumnContract(
            column_letter="J",
            field_name="notes_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header="توضیحات",
            description="Raw notes and description",
        ),
    ),
    activity_columns=("C", "D", "E", "F", "G", "H", "J"),
    derived_columns=(
        RawColumnContract(
            column_letter="A",
            field_name="row_number",
            role=ColumnRole.KNOWN_DERIVED,
            value_kind=ValueKind.RAW_TEXT,
            description="Visual row sequence number excluded from Raw Immutable",
        ),
        RawColumnContract(
            column_letter="I",
            field_name="total_amount",
            role=ColumnRole.KNOWN_DERIVED,
            value_kind=ValueKind.INTEGER_TOMAN,
            description=(
                "Calculated total amount formula column excluded from Raw Immutable"
            ),
        ),
    ),
)

RECEIPTS_PAYMENTS_CONTRACT = RawSheetContract(
    sheet_name="دریافت-پرداخت",
    stable_id_column=RawColumnContract(
        column_letter="P",
        field_name="record_id",
        role=ColumnRole.STABLE_ID,
        value_kind=ValueKind.UUID7,
        required_header="record_id",
        description="Stable UUIDv7 identifier for monetary receipt/payment rows",
    ),
    raw_columns=(
        RawColumnContract(
            column_letter="B",
            field_name="date_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header="تاریخ",
            description="Raw Jalali transaction date",
        ),
        RawColumnContract(
            column_letter="C",
            field_name="party_name_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header="نام",
            description="Raw party name",
        ),
        RawColumnContract(
            column_letter="D",
            field_name="entry_type_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header="شرح",
            description="Monetary transaction code (C/D/RS/H/HA/HS)",
        ),
        RawColumnContract(
            column_letter="E",
            field_name="amount_toman_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.INTEGER_TOMAN,
            required_header="مبلغ",
            description="Monetary amount in Toman integer",
        ),
        RawColumnContract(
            column_letter="F",
            field_name="notes_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header="توضیحات",
            description="Raw notes, destination account or counterpart name",
        ),
        RawColumnContract(
            column_letter="G",
            field_name="account_code_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header=None,
            description="Auxiliary raw account code column without mandatory header",
        ),
        RawColumnContract(
            column_letter="H",
            field_name="customer_flag_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header=None,
            description="Auxiliary raw customer flag column without mandatory header",
        ),
    ),
    activity_columns=("C", "D", "E", "F", "G", "H"),
    derived_columns=(
        RawColumnContract(
            column_letter="A",
            field_name="row_number",
            role=ColumnRole.KNOWN_DERIVED,
            value_kind=ValueKind.RAW_TEXT,
            description="Visual row sequence number excluded from Raw Immutable",
        ),
    ),
)

INVENTORY_MOVEMENTS_CONTRACT = RawSheetContract(
    sheet_name="ورود-خروج",
    stable_id_column=RawColumnContract(
        column_letter="P",
        field_name="record_id",
        role=ColumnRole.STABLE_ID,
        value_kind=ValueKind.UUID7,
        required_header="record_id",
        description=("Stable UUIDv7 identifier for commodity inventory movement rows"),
    ),
    raw_columns=(
        RawColumnContract(
            column_letter="B",
            field_name="date_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header="تاریخ",
            description="Raw Jalali movement date",
        ),
        RawColumnContract(
            column_letter="C",
            field_name="party_name_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header="نام",
            description="Raw party name",
        ),
        RawColumnContract(
            column_letter="D",
            field_name="movement_type_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header="شرح",
            description="Movement description / direction",
        ),
        RawColumnContract(
            column_letter="E",
            field_name="item_name_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header="کالا",
            description="Raw commodity item name",
        ),
        RawColumnContract(
            column_letter="F",
            field_name="quantity_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.DECIMAL,
            required_header="مقدار",
            description="Raw quantity or weight",
        ),
        RawColumnContract(
            column_letter="G",
            field_name="purity_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.DECIMAL,
            required_header="عیار",
            description="Raw gold purity (e.g. 750)",
        ),
        RawColumnContract(
            column_letter="I",
            field_name="notes_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header="توضیحات",
            description="Raw notes and description",
        ),
        RawColumnContract(
            column_letter="K",
            field_name="customer_flag_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header=None,
            description="Auxiliary raw customer flag column without mandatory header",
        ),
    ),
    activity_columns=("C", "D", "E", "F", "G", "I", "K"),
    derived_columns=(
        RawColumnContract(
            column_letter="A",
            field_name="row_number",
            role=ColumnRole.KNOWN_DERIVED,
            value_kind=ValueKind.RAW_TEXT,
            description="Visual row sequence number excluded from Raw Immutable",
        ),
        RawColumnContract(
            column_letter="H",
            field_name="weight_750",
            role=ColumnRole.KNOWN_DERIVED,
            value_kind=ValueKind.DECIMAL,
            description="Derived 750 weight formula column excluded from Raw Immutable",
        ),
        RawColumnContract(
            column_letter="J",
            field_name="invoice_number",
            role=ColumnRole.KNOWN_DERIVED,
            value_kind=ValueKind.RAW_TEXT,
            description="Invoice reference column excluded from Raw Immutable",
        ),
    ),
)

BUSINESS_PARTIES_CONTRACT = RawSheetContract(
    sheet_name="لیست کسبه",
    stable_id_column=RawColumnContract(
        column_letter="D",
        field_name="party_id",
        role=ColumnRole.STABLE_ID,
        value_kind=ValueKind.UUID7,
        required_header="party_id",
        description="Stable UUIDv7 identifier for registered business parties",
    ),
    raw_columns=(
        RawColumnContract(
            column_letter="B",
            field_name="party_name_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header="نام",
            description="Raw party name in merchant master list",
        ),
        RawColumnContract(
            column_letter="C",
            field_name="phone_number_raw",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
            required_header="شماره تماس",
            description="Raw phone number in merchant master list",
        ),
    ),
    activity_columns=("B", "C"),
    derived_columns=(
        RawColumnContract(
            column_letter="A",
            field_name="row_number",
            role=ColumnRole.KNOWN_DERIVED,
            value_kind=ValueKind.RAW_TEXT,
            description="Visual row sequence number excluded from Raw Immutable",
        ),
    ),
)

RAW_CONTRACT_REGISTRY: RawContractRegistry = RawContractRegistry(
    sheets={
        "خرید-فروش": BUY_SELL_CONTRACT,
        "دریافت-پرداخت": RECEIPTS_PAYMENTS_CONTRACT,
        "ورود-خروج": INVENTORY_MOVEMENTS_CONTRACT,
        "لیست کسبه": BUSINESS_PARTIES_CONTRACT,
    }
)


def get_raw_contract_registry() -> RawContractRegistry:
    """Return the authoritative raw contract registry singleton."""
    return RAW_CONTRACT_REGISTRY


def get_sheet_contract(sheet_name: str) -> RawSheetContract:
    """Retrieve a sheet contract by exact sheet name."""
    return RAW_CONTRACT_REGISTRY.get_sheet_contract(sheet_name)


def classify_cell(
    sheet_name: str, column_letter: str, *, has_formula: bool = False
) -> CellClassification:
    """Classify a cell in an approved sheet by column address and formula presence."""
    return RAW_CONTRACT_REGISTRY.classify_cell(
        sheet_name, column_letter, has_formula=has_formula
    )
