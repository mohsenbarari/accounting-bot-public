"""Deterministic and property-based tests for raw input contracts registry.

Verifies the four approved Excel sheet boundaries, column classifications,
formula/cached-value exclusions, invariant validations and immutability.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from accounting_contracts.raw_input_contracts import (
    BUSINESS_PARTIES_CONTRACT,
    BUY_SELL_CONTRACT,
    INVENTORY_MOVEMENTS_CONTRACT,
    RAW_CONTRACT_REGISTRY,
    RAW_SOURCE_CONTRACT_VERSION,
    RECEIPTS_PAYMENTS_CONTRACT,
    CellClassification,
    ColumnRole,
    ContractValidationError,
    RawColumnContract,
    RawContractRegistry,
    RawSheetContract,
    UnknownSheetError,
    ValueKind,
    classify_cell,
    get_raw_contract_registry,
    get_sheet_contract,
)
from hypothesis import given
from hypothesis import strategies as st


def test_contract_version() -> None:
    """Verify standard contract version constant."""
    assert RAW_SOURCE_CONTRACT_VERSION == "raw-source-contract.v1"
    for sheet in RAW_CONTRACT_REGISTRY.list_sheet_contracts():
        assert sheet.contract_version == RAW_SOURCE_CONTRACT_VERSION


def test_registry_approved_sheets() -> None:
    """Verify registry contains exactly the four approved sheet names."""
    expected_sheets = ("خرید-فروش", "دریافت-پرداخت", "ورود-خروج", "لیست کسبه")
    assert RAW_CONTRACT_REGISTRY.list_sheet_names() == expected_sheets
    assert len(RAW_CONTRACT_REGISTRY.list_sheet_contracts()) == 4
    assert get_raw_contract_registry() is RAW_CONTRACT_REGISTRY


def test_buy_sell_contract_specification() -> None:
    """Verify exact specifications of 'خرید-فروش' sheet contract."""
    contract = get_sheet_contract("خرید-فروش")
    assert contract is BUY_SELL_CONTRACT
    assert contract.sheet_name == "خرید-فروش"

    # Stable ID
    assert contract.stable_id_column.column_letter == "Z"
    assert contract.stable_id_column.field_name == "record_id"
    assert contract.stable_id_column.role == ColumnRole.STABLE_ID
    assert contract.stable_id_column.value_kind == ValueKind.UUID7
    assert contract.stable_id_column.required_header == "record_id"

    # Raw Columns
    raw_col_letters = tuple(c.column_letter for c in contract.raw_columns)
    assert raw_col_letters == ("B", "C", "D", "E", "F", "G", "H", "J")

    raw_fields = tuple(c.field_name for c in contract.raw_columns)
    assert raw_fields == (
        "date_raw",
        "party_name_raw",
        "transaction_type_raw",
        "item_name_raw",
        "quantity_raw",
        "unit_price_toman_raw",
        "discount_toman_raw",
        "notes_raw",
    )

    # Required Persian Headers
    expected_headers = {
        "B": "تاریخ",
        "C": "نام",
        "D": "شرح",
        "E": "کالا",
        "F": "مقدار",
        "G": "فی",
        "H": "تخفیف",
        "J": "توضیحات",
    }
    assert contract.required_headers_by_column == expected_headers

    # Activity Columns
    assert contract.activity_columns == ("C", "D", "E", "F", "G", "H", "J")
    for act in contract.activity_columns:
        assert contract.is_activity_column(act)
    assert not contract.is_activity_column("B")
    assert not contract.is_activity_column("Z")

    # Derived Columns
    derived_letters = tuple(c.column_letter for c in contract.derived_columns)
    assert derived_letters == ("A", "I")
    col_a = contract.get_column("A")
    assert col_a is not None
    assert col_a.field_name == "row_number"
    col_i = contract.get_column("I")
    assert col_i is not None
    assert col_i.field_name == "total_amount"

    # Specific Value Kinds
    col_f = contract.get_raw_column_by_field("quantity_raw")
    assert col_f is not None and col_f.value_kind == ValueKind.DECIMAL
    col_g = contract.get_raw_column_by_field("unit_price_toman_raw")
    assert col_g is not None and col_g.value_kind == ValueKind.INTEGER_TOMAN
    col_h = contract.get_raw_column_by_field("discount_toman_raw")
    assert col_h is not None and col_h.value_kind == ValueKind.INTEGER_TOMAN


def test_receipts_payments_contract_specification() -> None:
    """Verify exact specifications of 'دریافت-پرداخت' sheet contract."""
    contract = get_sheet_contract("دریافت-پرداخت")
    assert contract is RECEIPTS_PAYMENTS_CONTRACT
    assert contract.sheet_name == "دریافت-پرداخت"

    # Stable ID
    assert contract.stable_id_column.column_letter == "P"
    assert contract.stable_id_column.field_name == "record_id"
    assert contract.stable_id_column.role == ColumnRole.STABLE_ID
    assert contract.stable_id_column.value_kind == ValueKind.UUID7

    # Raw Columns
    raw_col_letters = tuple(c.column_letter for c in contract.raw_columns)
    assert raw_col_letters == ("B", "C", "D", "E", "F", "G", "H")

    raw_fields = tuple(c.field_name for c in contract.raw_columns)
    assert raw_fields == (
        "date_raw",
        "party_name_raw",
        "entry_type_raw",
        "amount_toman_raw",
        "notes_raw",
        "account_code_raw",
        "customer_flag_raw",
    )

    # Required Persian Headers (G and H are auxiliary without mandatory header)
    expected_headers = {
        "B": "تاریخ",
        "C": "نام",
        "D": "شرح",
        "E": "مبلغ",
        "F": "توضیحات",
    }
    assert contract.required_headers_by_column == expected_headers
    col_g = contract.get_raw_column_by_field("account_code_raw")
    assert col_g is not None and col_g.required_header is None
    col_h = contract.get_raw_column_by_field("customer_flag_raw")
    assert col_h is not None and col_h.required_header is None

    # Activity Columns
    assert contract.activity_columns == ("C", "D", "E", "F", "G", "H")
    assert not contract.is_activity_column("B")

    # Derived Columns
    derived_letters = tuple(c.column_letter for c in contract.derived_columns)
    assert derived_letters == ("A",)

    # Specific Value Kinds
    col_e = contract.get_raw_column_by_field("amount_toman_raw")
    assert col_e is not None and col_e.value_kind == ValueKind.INTEGER_TOMAN


def test_inventory_movements_contract_specification() -> None:
    """Verify exact specifications of 'ورود-خروج' sheet contract."""
    contract = get_sheet_contract("ورود-خروج")
    assert contract is INVENTORY_MOVEMENTS_CONTRACT
    assert contract.sheet_name == "ورود-خروج"

    # Stable ID
    assert contract.stable_id_column.column_letter == "P"
    assert contract.stable_id_column.field_name == "record_id"
    assert contract.stable_id_column.role == ColumnRole.STABLE_ID
    assert contract.stable_id_column.value_kind == ValueKind.UUID7

    # Raw Columns
    raw_col_letters = tuple(c.column_letter for c in contract.raw_columns)
    assert raw_col_letters == ("B", "C", "D", "E", "F", "G", "I", "K")

    raw_fields = tuple(c.field_name for c in contract.raw_columns)
    assert raw_fields == (
        "date_raw",
        "party_name_raw",
        "movement_type_raw",
        "item_name_raw",
        "quantity_raw",
        "purity_raw",
        "notes_raw",
        "customer_flag_raw",
    )

    # Required Persian Headers (K is auxiliary without mandatory header)
    expected_headers = {
        "B": "تاریخ",
        "C": "نام",
        "D": "شرح",
        "E": "کالا",
        "F": "مقدار",
        "G": "عیار",
        "I": "توضیحات",
    }
    assert contract.required_headers_by_column == expected_headers
    col_k = contract.get_raw_column_by_field("customer_flag_raw")
    assert col_k is not None and col_k.required_header is None

    # Activity Columns
    assert contract.activity_columns == ("C", "D", "E", "F", "G", "I", "K")

    # Derived Columns
    derived_letters = tuple(c.column_letter for c in contract.derived_columns)
    assert derived_letters == ("A", "H", "J")
    col_h = contract.get_column("H")
    assert col_h is not None and col_h.field_name == "weight_750"
    col_j = contract.get_column("J")
    assert col_j is not None and col_j.field_name == "invoice_number"

    # Specific Value Kinds
    col_f = contract.get_raw_column_by_field("quantity_raw")
    assert col_f is not None and col_f.value_kind == ValueKind.DECIMAL
    col_g = contract.get_raw_column_by_field("purity_raw")
    assert col_g is not None and col_g.value_kind == ValueKind.DECIMAL


def test_business_parties_contract_specification() -> None:
    """Verify exact specifications of 'لیست کسبه' sheet contract."""
    contract = get_sheet_contract("لیست کسبه")
    assert contract is BUSINESS_PARTIES_CONTRACT
    assert contract.sheet_name == "لیست کسبه"

    # Stable ID
    assert contract.stable_id_column.column_letter == "D"
    assert contract.stable_id_column.field_name == "party_id"
    assert contract.stable_id_column.role == ColumnRole.STABLE_ID
    assert contract.stable_id_column.value_kind == ValueKind.UUID7

    # Raw Columns
    raw_col_letters = tuple(c.column_letter for c in contract.raw_columns)
    assert raw_col_letters == ("B", "C")
    raw_fields = tuple(c.field_name for c in contract.raw_columns)
    assert raw_fields == ("party_name_raw", "phone_number_raw")

    # Required Persian Headers
    expected_headers = {
        "B": "نام",
        "C": "شماره تماس",
    }
    assert contract.required_headers_by_column == expected_headers

    # Activity Columns
    assert contract.activity_columns == ("B", "C")

    # Derived Columns
    derived_letters = tuple(c.column_letter for c in contract.derived_columns)
    assert derived_letters == ("A",)


def test_no_float_kind_in_any_contract() -> None:
    """Verify that ValueKind has no Float and all columns use approved kinds."""
    approved_kinds = {
        ValueKind.RAW_TEXT,
        ValueKind.INTEGER_TOMAN,
        ValueKind.DECIMAL,
        ValueKind.UUID7,
    }
    assert set(ValueKind) == approved_kinds

    for sheet in RAW_CONTRACT_REGISTRY.list_sheet_contracts():
        assert sheet.stable_id_column.value_kind in approved_kinds
        for col in sheet.raw_columns:
            assert col.value_kind in approved_kinds
        for col in sheet.derived_columns:
            assert col.value_kind in approved_kinds


def test_cell_classification_rules() -> None:
    """Verify pure cell classification logic across different columns and flags."""
    sheet = "خرید-فروش"

    # 1. Literal raw input candidates
    assert (
        classify_cell(sheet, "B", has_formula=False)
        == CellClassification.RAW_INPUT_CANDIDATE
    )
    assert (
        classify_cell(sheet, "F", has_formula=False)
        == CellClassification.RAW_INPUT_CANDIDATE
    )
    assert (
        classify_cell(sheet, "G", has_formula=False)
        == CellClassification.RAW_INPUT_CANDIDATE
    )
    assert (
        classify_cell(sheet, "H", has_formula=False)
        == CellClassification.RAW_INPUT_CANDIDATE
    )

    # 2. Stable ID column
    assert classify_cell(sheet, "Z", has_formula=False) == CellClassification.STABLE_ID

    # 3. Formula cell in ANY column is always FORMULA_EXCLUDED
    # Critical requirement: F/G/H in خرید-فروش, E in دریافت-پرداخت, F in ورود-خروج
    assert (
        classify_cell("خرید-فروش", "F", has_formula=True)
        == CellClassification.FORMULA_EXCLUDED
    )
    assert (
        classify_cell("خرید-فروش", "G", has_formula=True)
        == CellClassification.FORMULA_EXCLUDED
    )
    assert (
        classify_cell("خرید-فروش", "H", has_formula=True)
        == CellClassification.FORMULA_EXCLUDED
    )
    assert (
        classify_cell("دریافت-پرداخت", "E", has_formula=True)
        == CellClassification.FORMULA_EXCLUDED
    )
    assert (
        classify_cell("ورود-خروج", "F", has_formula=True)
        == CellClassification.FORMULA_EXCLUDED
    )
    assert (
        classify_cell("خرید-فروش", "Z", has_formula=True)
        == CellClassification.FORMULA_EXCLUDED
    )
    assert (
        classify_cell("خرید-فروش", "A", has_formula=True)
        == CellClassification.FORMULA_EXCLUDED
    )
    assert (
        classify_cell("خرید-فروش", "M", has_formula=True)
        == CellClassification.FORMULA_EXCLUDED
    )

    # 4. Known derived columns
    assert (
        classify_cell("خرید-فروش", "A", has_formula=False)
        == CellClassification.KNOWN_DERIVED_EXCLUDED
    )
    assert (
        classify_cell("خرید-فروش", "I", has_formula=False)
        == CellClassification.KNOWN_DERIVED_EXCLUDED
    )
    assert (
        classify_cell("ورود-خروج", "H", has_formula=False)
        == CellClassification.KNOWN_DERIVED_EXCLUDED
    )
    assert (
        classify_cell("ورود-خروج", "J", has_formula=False)
        == CellClassification.KNOWN_DERIVED_EXCLUDED
    )

    # 5. Unlisted columns
    assert (
        classify_cell("خرید-فروش", "K", has_formula=False)
        == CellClassification.UNLISTED_EXCLUDED
    )
    assert (
        classify_cell("خرید-فروش", "L", has_formula=False)
        == CellClassification.UNLISTED_EXCLUDED
    )
    assert (
        classify_cell("لیست کسبه", "E", has_formula=False)
        == CellClassification.UNLISTED_EXCLUDED
    )
    assert (
        classify_cell("لیست کسبه", "AA", has_formula=False)
        == CellClassification.UNLISTED_EXCLUDED
    )


def test_unknown_sheet_lookup_fails() -> None:
    """Verify that unknown sheet lookups fail explicitly with UnknownSheetError."""
    with pytest.raises(UnknownSheetError) as exc_info:
        get_sheet_contract("نامعتبر")
    assert "نامعتبر" in str(exc_info.value)

    with pytest.raises(UnknownSheetError):
        classify_cell("Sheet1", "B", has_formula=False)


def test_immutability_and_frozen_contracts() -> None:
    """Verify contract objects cannot be mutated through their public attributes."""
    contract = get_sheet_contract("خرید-فروش")
    with pytest.raises((FrozenInstanceError, AttributeError)):
        contract.sheet_name = "NewName"  # type: ignore[misc]

    col = contract.stable_id_column
    with pytest.raises((FrozenInstanceError, AttributeError)):
        col.column_letter = "Y"  # type: ignore[misc]


def test_contract_validation_rejects_invalid_definitions() -> None:
    """Verify structural invariant validations reject invalid contract combinations."""
    valid_stable_id = RawColumnContract(
        column_letter="Z",
        field_name="record_id",
        role=ColumnRole.STABLE_ID,
        value_kind=ValueKind.UUID7,
    )
    valid_raw = RawColumnContract(
        column_letter="B",
        field_name="date_raw",
        role=ColumnRole.LITERAL_RAW_INPUT,
        value_kind=ValueKind.RAW_TEXT,
    )

    # 1. Invalid column address
    with pytest.raises(ContractValidationError):
        RawColumnContract(
            column_letter="123",
            field_name="test",
            role=ColumnRole.LITERAL_RAW_INPUT,
            value_kind=ValueKind.RAW_TEXT,
        )

    # 2. Empty sheet name
    with pytest.raises(ContractValidationError):
        RawSheetContract(
            sheet_name="",
            stable_id_column=valid_stable_id,
            raw_columns=(valid_raw,),
            activity_columns=("B",),
            derived_columns=(),
        )

    # 3. Stable ID role mismatch
    with pytest.raises(ContractValidationError):
        RawSheetContract(
            sheet_name="Test",
            stable_id_column=RawColumnContract(
                column_letter="Z",
                field_name="record_id",
                role=ColumnRole.LITERAL_RAW_INPUT,
                value_kind=ValueKind.UUID7,
            ),
            raw_columns=(valid_raw,),
            activity_columns=("B",),
            derived_columns=(),
        )

    # 4. Stable ID value kind mismatch
    with pytest.raises(ContractValidationError):
        RawSheetContract(
            sheet_name="Test",
            stable_id_column=RawColumnContract(
                column_letter="Z",
                field_name="record_id",
                role=ColumnRole.STABLE_ID,
                value_kind=ValueKind.RAW_TEXT,
            ),
            raw_columns=(valid_raw,),
            activity_columns=("B",),
            derived_columns=(),
        )

    # 5. Duplicate raw column letters
    with pytest.raises(ContractValidationError):
        RawSheetContract(
            sheet_name="Test",
            stable_id_column=valid_stable_id,
            raw_columns=(
                valid_raw,
                RawColumnContract(
                    column_letter="B",
                    field_name="other_field",
                    role=ColumnRole.LITERAL_RAW_INPUT,
                    value_kind=ValueKind.RAW_TEXT,
                ),
            ),
            activity_columns=("B",),
            derived_columns=(),
        )

    # 6. Overlap between raw and stable ID
    with pytest.raises(ContractValidationError):
        RawSheetContract(
            sheet_name="Test",
            stable_id_column=valid_stable_id,
            raw_columns=(
                RawColumnContract(
                    column_letter="Z",
                    field_name="raw_z",
                    role=ColumnRole.LITERAL_RAW_INPUT,
                    value_kind=ValueKind.RAW_TEXT,
                ),
            ),
            activity_columns=("Z",),
            derived_columns=(),
        )

    # 7. Activity column not in raw columns
    with pytest.raises(ContractValidationError):
        RawSheetContract(
            sheet_name="Test",
            stable_id_column=valid_stable_id,
            raw_columns=(valid_raw,),
            activity_columns=("C",),
            derived_columns=(),
        )

    # 8. Empty activity columns
    with pytest.raises(ContractValidationError):
        RawSheetContract(
            sheet_name="Test",
            stable_id_column=valid_stable_id,
            raw_columns=(valid_raw,),
            activity_columns=(),
            derived_columns=(),
        )

    # 9. Registry key mismatch
    with pytest.raises(ContractValidationError):
        RawContractRegistry(sheets={"DifferentName": BUY_SELL_CONTRACT})


@given(
    col=st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=1, max_size=3),
    has_formula=st.booleans(),
    sheet_name=st.sampled_from(
        ["خرید-فروش", "دریافت-پرداخت", "ورود-خروج", "لیست کسبه"]
    ),
)
def test_property_classification_invariants(
    col: str, has_formula: bool, sheet_name: str
) -> None:
    """Hypothesis property test verifying classification consistency."""
    contract = get_sheet_contract(sheet_name)
    classification = classify_cell(sheet_name, col, has_formula=has_formula)

    if has_formula:
        assert classification == CellClassification.FORMULA_EXCLUDED
    elif col == contract.stable_id_column.column_letter:
        assert classification == CellClassification.STABLE_ID
    elif contract.is_whitelisted_column(col):
        assert classification == CellClassification.RAW_INPUT_CANDIDATE
    elif any(c.column_letter == col for c in contract.derived_columns):
        assert classification == CellClassification.KNOWN_DERIVED_EXCLUDED
    else:
        assert classification == CellClassification.UNLISTED_EXCLUDED
