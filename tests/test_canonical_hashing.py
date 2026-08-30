"""Deterministic Golden Vector and property tests for canonical hashing.

Verifies ADR-0006 compliance for source_hash and sheet_snapshot_hash:
- exact UTF-8 byte representation and SHA-256 digests;
- literal Golden Vectors for all four approved sheets and snapshot configurations;
- rejection of Float, Boolean, NaN, Infinity, exponent and grouping;
- row-order and mapping-order independence;
- snapshot UUIDv7 binary sorting and duplicate/malformed validation;
- every canonical raw field mutation sensitivity across all 4 sheets;
- arbitrary snapshot permutation invariance via property tests.
"""

from __future__ import annotations

import random
import uuid
from decimal import Decimal

import pytest
from accounting_contracts.canonical_hashing import (
    SHEET_SNAPSHOT_HASH_VERSION,
    SOURCE_HASH_VERSION,
    CanonicalMappingError,
    CanonicalValueError,
    DuplicateRecordIdError,
    InvalidHashError,
    InvalidUUIDError,
    TypeTag,
    canonicalize_value,
    compute_sheet_snapshot_hash,
    compute_source_hash,
)
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
    RAW_SOURCE_CONTRACT_VERSION,
    UnknownSheetError,
    ValueKind,
)
from hypothesis import given
from hypothesis import strategies as st


def test_version_constants() -> None:
    """Verify version constants matching ADR-0006 specification."""
    assert SOURCE_HASH_VERSION == "source-hash.v1"
    assert SHEET_SNAPSHOT_HASH_VERSION == "sheet-snapshot-hash.v1"
    assert RAW_SOURCE_CONTRACT_VERSION == "raw-source-contract.v1"


# --- Literal Golden Vectors ---


def test_golden_vector_buy_sell_row() -> None:
    """Verify exact literal JSON bytes and SHA-256 for 'خرید-فروش' row."""
    row = {
        "date_raw": "1403/05/15",
        "party_name_raw": "  بازرگانی احمدی  ",
        "transaction_type_raw": "خرید",
        "item_name_raw": "طلای آبشده",
        "quantity_raw": "12.3400",
        "unit_price_toman_raw": "1500000",
        "discount_toman_raw": "0",
        "notes_raw": None,
    }

    result = compute_source_hash("خرید-فروش", row)

    expected_json = (
        '["source-hash.v1","raw-source-contract.v1","خرید-فروش",'
        '[["date_raw","jalali_date","1403-05-15"],'
        '["party_name_raw","raw_text","  بازرگانی احمدی  "],'
        '["transaction_type_raw","raw_text","خرید"],'
        '["item_name_raw","raw_text","طلای آبشده"],'
        '["quantity_raw","decimal","12.34"],'
        '["unit_price_toman_raw","integer_toman","1500000"],'
        '["discount_toman_raw","integer_toman","0"],'
        '["notes_raw","raw_text",null]]]'
    )
    expected_bytes = expected_json.encode("utf-8")
    expected_hash = "a7ca691f9d50e0e6f2a47c4c3d098b6f8969899a331a23e0a00a9ddd431acf3e"

    assert result.canonical_json == expected_json
    assert result.canonical_bytes == expected_bytes
    assert result.source_hash == expected_hash


def test_golden_vector_receipts_payments_row() -> None:
    """Verify exact literal JSON bytes and SHA-256 for 'دریافت-پرداخت' row."""
    row = {
        "date_raw": "۱۴۰۳-۰۱-۰۱",
        "party_name_raw": "همکار نمونه",
        "entry_type_raw": "RS",
        "amount_toman_raw": "50000000",
        "notes_raw": "تسویه فاکتور ۱۲۳",
        "account_code_raw": "101",
        "customer_flag_raw": None,
    }

    result = compute_source_hash("دریافت-پرداخت", row)

    expected_json = (
        '["source-hash.v1","raw-source-contract.v1","دریافت-پرداخت",'
        '[["date_raw","jalali_date","1403-01-01"],'
        '["party_name_raw","raw_text","همکار نمونه"],'
        '["entry_type_raw","raw_text","RS"],'
        '["amount_toman_raw","integer_toman","50000000"],'
        '["notes_raw","raw_text","تسویه فاکتور ۱۲۳"],'
        '["account_code_raw","raw_text","101"],'
        '["customer_flag_raw","raw_text",null]]]'
    )
    expected_bytes = expected_json.encode("utf-8")
    expected_hash = "5114c76528e109ebc9c91a75ce11680e5603492d621d8521da22926e02e5185f"

    assert result.canonical_json == expected_json
    assert result.canonical_bytes == expected_bytes
    assert result.source_hash == expected_hash


def test_golden_vector_inventory_movements_row() -> None:
    """Verify exact literal JSON bytes and SHA-256 for 'ورود-خروج' row."""
    row = {
        "date_raw": "1403/12/29",
        "party_name_raw": "کارگاه زرگری",
        "movement_type_raw": "ورود",
        "item_name_raw": "شمش طلا",
        "quantity_raw": "100.50",
        "purity_raw": "750",
        "notes_raw": "تحویل کارگاه",
        "customer_flag_raw": "1",
    }

    result = compute_source_hash("ورود-خروج", row)

    expected_json = (
        '["source-hash.v1","raw-source-contract.v1","ورود-خروج",'
        '[["date_raw","jalali_date","1403-12-29"],'
        '["party_name_raw","raw_text","کارگاه زرگری"],'
        '["movement_type_raw","raw_text","ورود"],'
        '["item_name_raw","raw_text","شمش طلا"],'
        '["quantity_raw","decimal","100.5"],'
        '["purity_raw","decimal","750"],'
        '["notes_raw","raw_text","تحویل کارگاه"],'
        '["customer_flag_raw","raw_text","1"]]]'
    )
    expected_bytes = expected_json.encode("utf-8")
    expected_hash = "d5df8e9bdb0a4170ae5faff3db31a1a6794c6f6677ea419b06886b774a7d70bc"

    assert result.canonical_json == expected_json
    assert result.canonical_bytes == expected_bytes
    assert result.source_hash == expected_hash


def test_golden_vector_business_parties_row() -> None:
    """Verify exact literal JSON bytes and SHA-256 for 'لیست کسبه' row."""
    row = {
        "party_name_raw": "فروشگاه نمونه",
        "phone_number_raw": "SYNTHETIC-PHONE-001",
    }

    result = compute_source_hash("لیست کسبه", row)

    expected_json = (
        '["source-hash.v1","raw-source-contract.v1","لیست کسبه",'
        '[["party_name_raw","raw_text","فروشگاه نمونه"],'
        '["phone_number_raw","raw_text","SYNTHETIC-PHONE-001"]]]'
    )
    expected_bytes = expected_json.encode("utf-8")
    expected_hash = "f88ccdeb76608de9c530d856ba12310ca667dba48bfbe6ce5fa0e4451b8aac24"

    assert result.canonical_json == expected_json
    assert result.canonical_bytes == expected_bytes
    assert result.source_hash == expected_hash


def test_golden_vector_empty_snapshot() -> None:
    """Verify exact literal JSON bytes and SHA-256 digest for empty snapshot."""
    result = compute_sheet_snapshot_hash("خرید-فروش", [])

    expected_json = '["sheet-snapshot-hash.v1","raw-source-contract.v1","خرید-فروش",[]]'
    expected_bytes = expected_json.encode("utf-8")
    expected_hash = "2242738449d1fcbff0e143afb283962275acb2ae68cbc6070815ee1e9b263236"

    assert result.canonical_json == expected_json
    assert result.canonical_bytes == expected_bytes
    assert result.snapshot_hash == expected_hash
    assert result.row_count == 0


def test_golden_vector_two_row_snapshot() -> None:
    """Verify exact literal JSON bytes and digest for 2-row snapshot."""
    pairs = [
        ("0191a3f0-0002-7000-8000-000000000002", "b" * 64),
        ("0191a3f0-0001-7000-8000-000000000001", "a" * 64),
    ]
    result = compute_sheet_snapshot_hash("خرید-فروش", pairs)

    expected_json = (
        '["sheet-snapshot-hash.v1","raw-source-contract.v1","خرید-فروش",'
        '[["0191a3f0-0001-7000-8000-000000000001","'
        + "a" * 64
        + '"],["0191a3f0-0002-7000-8000-000000000002","'
        + "b" * 64
        + '"]]]'
    )
    expected_bytes = expected_json.encode("utf-8")
    expected_hash = "1129324609650841e273cef5deb09aa8a66f164ff097c4f102497af101451d1d"

    assert result.canonical_json == expected_json
    assert result.canonical_bytes == expected_bytes
    assert result.snapshot_hash == expected_hash
    assert result.row_count == 2


# --- Field and Mapping Rules ---


def test_mapping_order_independence() -> None:
    """Verify source_row dictionary key insertion order does not affect source_hash."""
    row_forward = {
        "party_name_raw": "فروشگاه نمونه",
        "phone_number_raw": "SYNTHETIC-PHONE-001",
    }
    row_backward = {
        "phone_number_raw": "SYNTHETIC-PHONE-001",
        "party_name_raw": "فروشگاه نمونه",
    }
    res_forward = compute_source_hash("لیست کسبه", row_forward)
    res_backward = compute_source_hash("لیست کسبه", row_backward)

    assert res_forward.canonical_bytes == res_backward.canonical_bytes
    assert res_forward.source_hash == res_backward.source_hash


def test_missing_or_extra_fields_rejected() -> None:
    """Verify missing required raw fields or unauthorized extra fields are rejected."""
    # 1. Missing field
    with pytest.raises(CanonicalMappingError) as exc_info:
        compute_source_hash("لیست کسبه", {"party_name_raw": "فروشگاه"})
    assert "missing required raw fields" in str(exc_info.value)

    # 2. Extra unlisted field
    with pytest.raises(CanonicalMappingError) as exc_info:
        compute_source_hash(
            "لیست کسبه",
            {
                "party_name_raw": "فروشگاه",
                "phone_number_raw": "SYNTHETIC-PHONE-001",
                "extra_field": "unauthorized",
            },
        )
    assert "unauthorized/extra fields" in str(exc_info.value)

    # 3. Technical ID field must NOT be in source_row mapping
    with pytest.raises(CanonicalMappingError) as exc_info:
        compute_source_hash(
            "لیست کسبه",
            {
                "party_name_raw": "فروشگاه",
                "phone_number_raw": "SYNTHETIC-PHONE-001",
                "party_id": "0191a3f0-0001-7000-8000-000000000001",
            },
        )
    assert "party_id" in str(exc_info.value)

    # 4. Unknown sheet lookup
    with pytest.raises(UnknownSheetError):
        compute_source_hash("unknown_sheet", {})


def test_null_vs_empty_text_distinction() -> None:
    """Verify that None (null) and empty string produce distinct canonical hashes."""
    row_none = {
        "party_name_raw": None,
        "phone_number_raw": None,
    }
    row_empty = {
        "party_name_raw": "",
        "phone_number_raw": "",
    }
    res_none = compute_source_hash("لیست کسبه", row_none)
    res_empty = compute_source_hash("لیست کسبه", row_empty)

    assert res_none.canonical_json != res_empty.canonical_json
    assert res_none.source_hash != res_empty.source_hash


def test_raw_text_whitespace_and_unicode_preservation() -> None:
    """Verify raw_text retains whitespace and distinct Unicode code points."""
    row_trimmed = {"party_name_raw": "بازرگانی", "phone_number_raw": None}
    row_spaced = {"party_name_raw": " بازرگانی ", "phone_number_raw": None}
    row_arabic_kaf = {"party_name_raw": "بازرگانى", "phone_number_raw": None}

    h_trimmed = compute_source_hash("لیست کسبه", row_trimmed).source_hash
    h_spaced = compute_source_hash("لیست کسبه", row_spaced).source_hash
    h_arabic = compute_source_hash("لیست کسبه", row_arabic_kaf).source_hash

    assert h_trimmed != h_spaced
    assert h_trimmed != h_arabic


# --- Number Normalization and Rejection ---


def test_invalid_type_tag_raises_canonical_value_error() -> None:
    """Verify that invalid type tag raises CanonicalValueError even if value is None."""
    with pytest.raises(CanonicalValueError):
        canonicalize_value("invalid_type_tag", None)

    with pytest.raises(CanonicalValueError):
        canonicalize_value("unknown", "sample_value")

    with pytest.raises(CanonicalValueError):
        canonicalize_value(12345, None)  # type: ignore[arg-type]


def test_number_canonicalization_and_type_rejections() -> None:
    """Verify integer and decimal canonical values and strict type rejections."""
    # 1. integer_toman
    assert canonicalize_value(TypeTag.INTEGER_TOMAN, 0) == "0"
    assert canonicalize_value(TypeTag.INTEGER_TOMAN, -0) == "0"
    assert canonicalize_value(TypeTag.INTEGER_TOMAN, 150000) == "150000"
    assert canonicalize_value(TypeTag.INTEGER_TOMAN, -150000) == "-150000"
    assert canonicalize_value(TypeTag.INTEGER_TOMAN, Decimal("150000")) == "150000"
    assert canonicalize_value(TypeTag.INTEGER_TOMAN, "  ۱۵۰۰۰۰  ") == "150000"

    # Rejections for integer_toman
    with pytest.raises(CanonicalValueError):
        canonicalize_value(TypeTag.INTEGER_TOMAN, True)
    with pytest.raises(CanonicalValueError):
        canonicalize_value(TypeTag.INTEGER_TOMAN, False)
    with pytest.raises(CanonicalValueError):
        canonicalize_value(TypeTag.INTEGER_TOMAN, 150000.0)  # Float rejected!
    with pytest.raises(CanonicalValueError):
        canonicalize_value(TypeTag.INTEGER_TOMAN, Decimal("150.5"))  # Non-integral
    with pytest.raises(CanonicalValueError):
        canonicalize_value(TypeTag.INTEGER_TOMAN, "150,000")  # Grouping separator
    with pytest.raises(CanonicalValueError):
        canonicalize_value(TypeTag.INTEGER_TOMAN, "1e5")  # Exponent

    # 2. decimal
    assert canonicalize_value(TypeTag.DECIMAL, Decimal("12.3400")) == "12.34"
    assert canonicalize_value(TypeTag.DECIMAL, Decimal("-0.00")) == "0"
    assert canonicalize_value(TypeTag.DECIMAL, Decimal("100.00")) == "100"
    assert canonicalize_value(TypeTag.DECIMAL, 50) == "50"
    assert canonicalize_value(TypeTag.DECIMAL, "  ۵۰.۷۵۰  ") == "50.75"

    # Exact large Decimal precision without scientific notation
    large_dec = Decimal("0.000000000000000001")
    assert canonicalize_value(TypeTag.DECIMAL, large_dec) == "0.000000000000000001"

    # Rejections for decimal
    with pytest.raises(CanonicalValueError):
        canonicalize_value(TypeTag.DECIMAL, True)
    with pytest.raises(CanonicalValueError):
        canonicalize_value(TypeTag.DECIMAL, 12.34)  # Float rejected!
    with pytest.raises(CanonicalValueError):
        canonicalize_value(TypeTag.DECIMAL, Decimal("NaN"))
    with pytest.raises(CanonicalValueError):
        canonicalize_value(TypeTag.DECIMAL, Decimal("Infinity"))
    with pytest.raises(CanonicalValueError):
        canonicalize_value(TypeTag.DECIMAL, "1,000.5")  # Grouping separator
    with pytest.raises(CanonicalValueError):
        canonicalize_value(TypeTag.DECIMAL, "1e-4")  # Exponent


def test_every_raw_field_mutation_changes_source_hash() -> None:
    """Verify mutating any single raw column across all 4 sheets changes source_hash."""
    base_rows: dict[str, dict[str, object]] = {
        "خرید-فروش": {
            "date_raw": "1403/05/15",
            "party_name_raw": "بازرگانی احمدی",
            "transaction_type_raw": "خرید",
            "item_name_raw": "طلای آبشده",
            "quantity_raw": "12.34",
            "unit_price_toman_raw": "1500000",
            "discount_toman_raw": "0",
            "notes_raw": "توضیحات اولیه",
        },
        "دریافت-پرداخت": {
            "date_raw": "1403/01/01",
            "party_name_raw": "همکار نمونه",
            "entry_type_raw": "RS",
            "amount_toman_raw": "50000000",
            "notes_raw": "تسویه اولیه",
            "account_code_raw": "101",
            "customer_flag_raw": "1",
        },
        "ورود-خروج": {
            "date_raw": "1403/12/29",
            "party_name_raw": "کارگاه زرگری",
            "movement_type_raw": "ورود",
            "item_name_raw": "شمش طلا",
            "quantity_raw": "100.5",
            "purity_raw": "750",
            "notes_raw": "تحویل کارگاه",
            "customer_flag_raw": "1",
        },
        "لیست کسبه": {
            "party_name_raw": "فروشگاه نمونه",
            "phone_number_raw": "SYNTHETIC-PHONE-001",
        },
    }

    for sheet_name, base_row in base_rows.items():
        base_res = compute_source_hash(sheet_name, base_row)
        sheet_contract = RAW_CONTRACT_REGISTRY.get_sheet_contract(sheet_name)

        for col in sheet_contract.raw_columns:
            mutated_row = dict(base_row)
            if col.field_name == "date_raw":
                mutated_row["date_raw"] = "1403/05/20"
            elif col.value_kind == ValueKind.RAW_TEXT:
                curr_val = base_row[col.field_name]
                mutated_row[col.field_name] = f"{curr_val}_MUTATED"
            elif col.value_kind == ValueKind.INTEGER_TOMAN:
                mutated_row[col.field_name] = "999999"
            elif col.value_kind == ValueKind.DECIMAL:
                mutated_row[col.field_name] = "888.88"

            mutated_res = compute_source_hash(sheet_name, mutated_row)
            assert mutated_res.source_hash != base_res.source_hash, (
                f"Mutation of field '{col.field_name}' in sheet '{sheet_name}' "
                "did not change source_hash!"
            )
            assert mutated_res.canonical_bytes != base_res.canonical_bytes


# --- Snapshot Hashing Rules ---


def test_snapshot_permutation_invariance_and_sensitivity() -> None:
    """Verify snapshot hashing is permutation-invariant and sensitive to edits."""
    id1 = "0191a3f0-0001-7000-8000-000000000001"
    id2 = "0191a3f0-0002-7000-8000-000000000002"
    h1 = "a" * 64
    h2 = "b" * 64

    # Permutations have identical snapshot hash
    res_1 = compute_sheet_snapshot_hash("خرید-فروش", [(id1, h1), (id2, h2)])
    res_2 = compute_sheet_snapshot_hash("خرید-فروش", [(id2, h2), (id1, h1)])
    assert res_1.snapshot_hash == res_2.snapshot_hash

    # Editing a single hash changes the snapshot hash
    res_edited = compute_sheet_snapshot_hash(
        "خرید-فروش", [(id1, "c" + "a" * 63), (id2, h2)]
    )
    assert res_1.snapshot_hash != res_edited.snapshot_hash

    # Deleting a row changes the snapshot hash
    res_deleted = compute_sheet_snapshot_hash("خرید-فروش", [(id1, h1)])
    assert res_1.snapshot_hash != res_deleted.snapshot_hash


def test_snapshot_uuid_and_hash_validations() -> None:
    """Verify non-v7 UUIDs, duplicates, and malformed hashes are rejected."""
    valid_v7 = "0191a3f0-0001-7000-8000-000000000001"
    valid_hash = "a" * 64

    # 1. Non-v7 UUID (v4)
    v4_id = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    with pytest.raises(InvalidUUIDError) as exc:
        compute_sheet_snapshot_hash("خرید-فروش", [(v4_id, valid_hash)])
    assert "version 7" in str(exc.value)

    # 2. Malformed UUID string
    with pytest.raises(InvalidUUIDError):
        compute_sheet_snapshot_hash("خرید-فروش", [("not-a-uuid", valid_hash)])

    # 3. Duplicate UUID in snapshot
    with pytest.raises(DuplicateRecordIdError) as exc_dup:
        compute_sheet_snapshot_hash(
            "خرید-فروش", [(valid_v7, valid_hash), (valid_v7, "b" * 64)]
        )
    assert "Duplicate record ID" in str(exc_dup.value)

    # 4. Uppercase hash rejected (must be lowercase)
    with pytest.raises(InvalidHashError):
        compute_sheet_snapshot_hash("خرید-فروش", [(valid_v7, valid_hash.upper())])

    # 5. Invalid hash length
    with pytest.raises(InvalidHashError):
        compute_sheet_snapshot_hash("خرید-فروش", [(valid_v7, "a" * 63)])


# --- Hypothesis Property Tests ---


@given(
    name=st.text(min_size=1, max_size=50),
    phone=st.text(min_size=1, max_size=20),
)
def test_property_source_hash_determinism(name: str, phone: str) -> None:
    """Hypothesis test verifying source_hash produces identical results repeatedly."""
    row = {"party_name_raw": name, "phone_number_raw": phone}
    res1 = compute_source_hash("لیست کسبه", row)
    res2 = compute_source_hash("لیست کسبه", row)
    assert res1.source_hash == res2.source_hash
    assert res1.canonical_bytes == res2.canonical_bytes


def _make_uuid7(b: bytes) -> uuid.UUID:
    b_arr = bytearray(b)
    b_arr[6] = (b_arr[6] & 0x0F) | 0x70
    b_arr[8] = (b_arr[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(b_arr))


st_uuid7 = st.binary(min_size=16, max_size=16).map(_make_uuid7)


@given(
    pairs=st.lists(
        st.tuples(
            st_uuid7,
            st.text(
                alphabet="0123456789abcdef",
                min_size=64,
                max_size=64,
            ),
        ),
        min_size=1,
        max_size=10,
        unique_by=lambda item: item[0],
    ),
    seed=st.integers(min_value=0, max_value=1000),
)
def test_property_snapshot_arbitrary_permutation_invariance(
    pairs: list[tuple[uuid.UUID, str]], seed: int
) -> None:
    """Hypothesis test: arbitrary pair permutations yield identical snapshot hash."""
    shuffled = list(pairs)
    random.Random(seed).shuffle(shuffled)

    res1 = compute_sheet_snapshot_hash("خرید-فروش", pairs)
    res2 = compute_sheet_snapshot_hash("خرید-فروش", shuffled)

    assert res1.snapshot_hash == res2.snapshot_hash
    assert res1.canonical_bytes == res2.canonical_bytes
