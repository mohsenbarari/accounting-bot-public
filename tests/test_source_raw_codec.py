"""Independent RC-01..14 grammar, preservation, failure and purity evidence."""

from __future__ import annotations

import inspect
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import FrozenInstanceError
from decimal import (
    ROUND_DOWN,
    ROUND_UP,
    Context,
    Decimal,
    Inexact,
    InvalidOperation,
    localcontext,
)
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

import accounting_contracts as contracts
import accounting_contracts.source_raw_codec as codec
import pytest
from accounting_contracts import (
    ContractError,
    SourceRawCodecError,
    SourceRawCodecReason,
    ValidatedSourceRow,
    compute_source_hash,
    decode_source_raw_row,
    encode_source_raw_row,
)
from hypothesis import given, settings
from hypothesis import strategies as st
from source_binding_import_probe import (
    ForbiddenSideEffect,
    deny_side_effects,
    forbidden,
)
from source_raw_codec_support import (
    FIELDS,
    RAW,
    SHEETS,
    make_row,
    row_view,
    scalar_view,
    uid,
    wire,
    wire_tree,
)

# Complete reviewed literals. Source hashes were obtained from the accepted WP-03
# implementation on the literal RAW fixtures, not from the codec under test.
GOLDEN_JSON = (
    '["source-raw-codec.v1","raw-source-contract.v1","source-hash.v1","خرید-فروش",'
    '"00000000-0000-7000-8000-000000000001",'
    '"e34d223d75899764388a92fea57e127cf4e322eb23a0027df0f3c949eeb7e0b2",'
    '[["date_raw",["text"," ۱۴۰۵/۱/۱ "]],["party_name_raw",["text","RAW-GOLDEN-A"]],'
    '["transaction_type_raw",["text","خرید"]],["item_name_raw",["text","SYNTHETIC"]],'
    '["quantity_raw",["decimal",["0","100","-2"]]],'
    '["unit_price_toman_raw",["int","100"]],["discount_toman_raw",["null",null]],'
    '["notes_raw",["text",""]]]]',
    '["source-raw-codec.v1","raw-source-contract.v1","source-hash.v1","دریافت-پرداخت",'
    '"00000000-0000-7000-8000-000000000002",'
    '"4f813e106f60bb6590de4c3c7cfbc4dab4410d17b0abf489be9ba497cb89c2a6",'
    '[["date_raw",["text","1405-01-01"]],["party_name_raw",["text","RAW-GOLDEN-B"]],'
    '["entry_type_raw",["text","C"]],["amount_toman_raw",["decimal",["1","0","-2"]]],'
    '["notes_raw",["null",null]],["account_code_raw",["text"," 001 "]],'
    '["customer_flag_raw",["text",""]]]]',
    '["source-raw-codec.v1","raw-source-contract.v1","source-hash.v1","ورود-خروج",'
    '"00000000-0000-7000-8000-000000000003",'
    '"34ba814ae3dbcc2906e3cd4b2db032b575d993077f42d96a1021f1ac9ce16154",'
    '[["date_raw",["null",null]],["party_name_raw",["text","RAW-GOLDEN-C"]],'
    '["movement_type_raw",["text","ورود"]],["item_name_raw",["text","SYNTHETIC"]],'
    '["quantity_raw",["decimal",["0","1","3"]]],["purity_raw",["text","0.7500"]],'
    '["notes_raw",["text"," "]],["customer_flag_raw",["null",null]]]]',
    '["source-raw-codec.v1","raw-source-contract.v1","source-hash.v1","لیست کسبه",'
    '"00000000-0000-7000-8000-000000000004",'
    '"24a570547ce4cb527c3d4ec16e830ab1b5c5f97ff0426c5a014a3833bd28666a",'
    '[["party_name_raw",["text","RAW-GOLDEN-D"]],'
    '["phone_number_raw",["text","SYNTHETIC-PHONE"]]]]',
)


def assert_payload_rejected(payload: bytes) -> SourceRawCodecError:
    with pytest.raises(SourceRawCodecError) as caught:
        decode_source_raw_row(payload)
    assert caught.value.reason is SourceRawCodecReason.INVALID_PAYLOAD
    return caught.value


def test_rc01_public_api_and_valid_row_subclass() -> None:
    names = (
        "SOURCE_RAW_CODEC_VERSION",
        "SourceRawCodecReason",
        "SourceRawCodecError",
        "encode_source_raw_row",
        "decode_source_raw_row",
    )
    assert codec.SOURCE_RAW_CODEC_VERSION == "source-raw-codec.v1"
    for name in names:
        assert name in contracts.__all__
        assert getattr(contracts, name) is getattr(codec, name)
    assert tuple(inspect.signature(encode_source_raw_row).parameters) == ("row",)
    assert tuple(inspect.signature(decode_source_raw_row).parameters) == ("payload",)
    assert inspect.signature(encode_source_raw_row).return_annotation == "bytes"
    assert (
        inspect.signature(decode_source_raw_row).return_annotation
        == "ValidatedSourceRow"
    )
    assert issubclass(SourceRawCodecError, ContractError)
    assert [reason.value for reason in SourceRawCodecReason] == [
        "invalid_input",
        "invalid_payload",
    ]

    class RowSubclass(ValidatedSourceRow):
        pass

    original = make_row()
    child = RowSubclass(
        original.stable_id,
        original.canonical_uuid,
        original.sheet_name,
        original.raw_values,
        original.source_hash,
    )
    assert encode_source_raw_row(child) == GOLDEN_JSON[0].encode()
    assert row_view(decode_source_raw_row(encode_source_raw_row(child))) == row_view(
        original
    )


@pytest.mark.parametrize("mode,exit_code", [("normal", 0), ("inject_write", 73)])
def test_rc01_inert_import_with_negative_control(
    tmp_path: Path, mode: str, exit_code: int
) -> None:
    canary = tmp_path / "SYNTHETIC-canary.txt"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("source_raw_codec_import_probe.py")),
            mode,
            str(canary),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert "IMPORT_ENTERED" in result.stdout and not canary.exists()
    expected = "PROBE_OK" if mode == "normal" else "IMPORT_REJECTED_BY_GUARD"
    assert expected in result.stdout


@pytest.mark.parametrize("sheet", range(4))
def test_rc02_literal_complete_golden_vectors(sheet: int) -> None:
    expected = GOLDEN_JSON[sheet].encode("utf-8")
    row = make_row(sheet)
    tree = json.loads(expected)
    assert tree[5] == compute_source_hash(SHEETS[sheet], RAW[sheet]).source_hash
    assert [entry[0] for entry in tree[6]] == list(FIELDS[sheet])
    assert encode_source_raw_row(row) == expected
    restored = decode_source_raw_row(expected)
    assert row_view(restored) == row_view(row)
    assert type(restored) is ValidatedSourceRow and restored is not row
    assert encode_source_raw_row(restored) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        " \t\r\n",
        "۰۱۲٣٤",
        "é",
        "e\u0301",
        "الف\u200cب",
        'quote" slash/ back\\',
        "\0\x01\b\f\t\n\r",
        "😀",
        "\u2028\u2029",
    ],
)
def test_rc03_text_and_null_are_preserved(value: str | None) -> None:
    row = make_row(3, {"party_name_raw": value, "phone_number_raw": value})
    payload = encode_source_raw_row(row)
    assert payload == wire(wire_tree(row))
    assert row_view(decode_source_raw_row(payload)) == row_view(row)


@pytest.mark.parametrize("value", [None, " ۱۴۰۵/۱/۱ ", "١٤٠٥-١-١", "1405/01/01"])
def test_rc03_original_date_text_is_preserved(value: str | None) -> None:
    row = make_row(0, {"date_raw": value})
    assert row_view(decode_source_raw_row(encode_source_raw_row(row))) == row_view(row)


@pytest.mark.parametrize("value", ["", " \t"])
def test_rc03_blank_date_remains_an_upstream_contract_error(value: str) -> None:
    tree = json.loads(GOLDEN_JSON[0])
    tree[6][0][1] = ["text", value]
    error = assert_payload_rejected(wire(tree))
    assert isinstance(error.__cause__, contracts.InvalidDateError)


@pytest.mark.parametrize(
    "value",
    [
        0,
        -12,
        10**250,
        " ۰۰۱.۰۰ ",
        Decimal("1.00"),
        Decimal("-0.00"),
        Decimal("0E+9"),
        Decimal("1E+3"),
        Decimal("-1.2500"),
        Decimal("12345678901234567890123456789.000"),
    ],
)
def test_rc04_exact_numeric_types_and_tuples(value: Any) -> None:
    row = make_row(0, {"quantity_raw": value})
    restored = decode_source_raw_row(encode_source_raw_row(row))
    assert scalar_view(restored.raw_values["quantity_raw"]) == scalar_view(value)
    assert row_view(restored) == row_view(row)


@pytest.mark.parametrize(
    "value", [Decimal("100.00"), Decimal("-0.00"), " ۰۰۱ ", 10**250, -500]
)
def test_rc04_integral_decimal_toman(value: Any) -> None:
    row = make_row(1, {"amount_toman_raw": value})
    assert row_view(decode_source_raw_row(encode_source_raw_row(row))) == row_view(row)


def context_view(context: Context) -> tuple[Any, ...]:
    return (
        context.prec,
        context.rounding,
        context.Emin,
        context.Emax,
        context.capitals,
        context.clamp,
        dict(context.traps),
        dict(context.flags),
    )


@pytest.mark.parametrize(
    "precision,rounding,capitals", [(2, ROUND_UP, 0), (50, ROUND_DOWN, 1)]
)
@pytest.mark.parametrize("trap", [False, True])
def test_rc04_context_settings_flags_and_failure_preserved(
    precision: int,
    rounding: str,
    capitals: int,
    trap: bool,
) -> None:
    row = make_row(
        0,
        {
            "quantity_raw": Decimal("-1234567890123456789.000"),
            "unit_price_toman_raw": Decimal("100.00"),
        },
    )
    expected = wire(wire_tree(row))
    bad = wire_tree(row)
    bad[6][4][1] = ["decimal", ["0", "1", "999999999999999999999999999999"]]
    with localcontext() as context:
        context.prec, context.rounding, context.capitals = precision, rounding, capitals
        context.traps[InvalidOperation] = trap
        context.flags[Inexact] = True
        before = context_view(context)
        assert encode_source_raw_row(row) == expected
        assert row_view(decode_source_raw_row(expected)) == row_view(row)
        assert_payload_rejected(wire(bad))
        assert context_view(context) == before


class TextSubclass(str):
    pass


class IntSubclass(int):
    pass


class DecimalSubclass(Decimal):
    pass


class BytesSubclass(bytes):
    pass


@pytest.mark.parametrize(
    "value", [TextSubclass("1"), IntSubclass(1), DecimalSubclass("1.00")]
)
def test_rc05_scalar_subclasses_rejected_only_at_codec(value: Any) -> None:
    row = make_row(0, {"quantity_raw": value})
    assert type(row.raw_values["quantity_raw"]) is type(value)
    before = row_view(row)
    with pytest.raises(SourceRawCodecError) as caught:
        encode_source_raw_row(row)
    assert caught.value.reason is SourceRawCodecReason.INVALID_INPUT
    assert row_view(row) == before


@pytest.mark.parametrize("value", [None, False, 1, 1.0, "row", {}, [], object()])
def test_rc05_invalid_encoder_root(value: Any) -> None:
    with pytest.raises(SourceRawCodecError) as caught:
        encode_source_raw_row(value)
    assert caught.value.reason is SourceRawCodecReason.INVALID_INPUT


@pytest.mark.parametrize(
    "value",
    [None, True, 1, "bytes", bytearray(b"[]"), memoryview(b"[]"), BytesSubclass(b"[]")],
)
def test_rc05_invalid_decoder_root(value: Any) -> None:
    with pytest.raises(SourceRawCodecError) as caught:
        decode_source_raw_row(value)
    assert caught.value.reason is SourceRawCodecReason.INVALID_INPUT


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"[",
        b"[]",
        b"null",
        b"false",
        b'"text"',
        b"{}",
        b'{"a":"x","a":"y"}',
        b"[\xff]",
        b"\xef\xbb\xbf[]",
        b"[] []",
    ],
)
def test_rc05_malformed_json_and_root_shape(payload: bytes) -> None:
    assert_payload_rejected(payload)


@pytest.mark.parametrize(
    "token",
    [b"1", b"-0", b"1.0", b"1e20", b"NaN", b"Infinity", b"-Infinity", b"9" * 5000],
)
def test_rc05_numeric_tokens_fail_during_parsing(
    token: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_contract(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Numeric token reached row reconstruction")

    monkeypatch.setattr(codec, "_decode_row", no_contract)
    payload = GOLDEN_JSON[0].encode().replace(b'"source-raw-codec.v1"', token, 1)
    error = assert_payload_rejected(payload)
    assert type(error.__cause__) is ValueError
    assert str(error.__cause__) == "JSON numeric tokens are not supported."


@pytest.mark.parametrize(
    "index,value",
    [
        (0, "source-raw-codec.v2"),
        (1, "raw-source-contract.v2"),
        (2, "source-hash.v2"),
        (3, "unknown"),
        (3, None),
        (4, "not-a-uuid"),
        (4, str(uid(1)).replace("7000", "4000")),
        (4, str(uid(1)).replace("8000", "0000")),
        (4, str(uid(1)).replace("-", "")),
        (5, "0" * 64),
        (5, "a" * 63),
        (5, "F" * 64),
        (5, True),
    ],
)
def test_rc06_envelope_uuid_and_hash_rejection(index: int, value: Any) -> None:
    tree = json.loads(GOLDEN_JSON[0])
    tree[index] = value
    assert_payload_rejected(wire(tree))


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "extra",
        "duplicate",
        "reordered",
        "derived",
        "bad_pair",
        "bad_name",
        "bad_envelope",
    ],
)
def test_rc06_exact_whitelist_and_shape(case: str) -> None:
    tree = json.loads(GOLDEN_JSON[0])
    if case == "missing":
        tree[6].pop()
    elif case == "extra":
        tree[6].append(["foreign", ["null", None]])
    elif case == "duplicate":
        tree[6][1] = tree[6][0]
    elif case == "reordered":
        tree[6][0], tree[6][1] = tree[6][1], tree[6][0]
    elif case == "derived":
        tree[6][7][0] = "total_amount"
    elif case == "bad_pair":
        tree[6][0].append(None)
    elif case == "bad_name":
        tree[6][0][0] = ["date_raw"]
    else:
        tree.append(None)
    assert_payload_rejected(wire(tree))


@pytest.mark.parametrize(
    "tagged",
    [
        ["int", "00"],
        ["int", "+100"],
        ["int", "-0"],
        ["int", "１００"],
        ["int", "100\n"],
        ["int", "1e2"],
        ["int", None],
        ["null", ""],
        ["text", []],
        ["text", True],
        ["unknown", "1"],
        [[], "1"],
        ["text"],
        ["decimal", ["0", "0100", "-2"]],
        ["decimal", ["0", "100", "-02"]],
        ["decimal", ["0", "100", "+0"]],
        ["decimal", ["0", "100", "-0"]],
        ["decimal", ["0", "100", "０"]],
        ["decimal", ["0", "", "0"]],
        ["decimal", ["0", "NaN", "0"]],
        ["decimal", ["2", "100", "-2"]],
        ["decimal", [False, "100", "-2"]],
        ["decimal", ["0", "00", "-2"]],
        ["decimal", ["0", "100"]],
    ],
)
def test_rc06_bad_tags_and_scalar_grammar(tagged: Any) -> None:
    tree = json.loads(GOLDEN_JSON[0])
    tree[6][4][1] = tagged
    # Several malformed tuples still denote 1.00, so the stored hash already
    # matches their intended value. Only strict representation checks reject them.
    assert_payload_rejected(wire(tree))


@pytest.mark.parametrize(
    "tagged,semantic",
    [
        (["int", "01"], 1),
        (["int", "+1"], 1),
        (["int", "-0"], 0),
        (["int", " 1 "], 1),
        (["int", "۱"], 1),
        (["decimal", ["0", "0100", "-2"]], Decimal("1.00")),
        (["decimal", ["0", "100", "-02"]], Decimal("1.00")),
        (["decimal", ["0", "1", "+0"]], Decimal("1")),
        (["decimal", ["0", "1", "-0"]], Decimal("1")),
        (["decimal", ["0", "1", "۰"]], Decimal("1")),
    ],
)
def test_rc06_noncanonical_scalars_with_matching_semantic_hash(
    tagged: Any,
    semantic: Any,
) -> None:
    row = make_row(0, {"quantity_raw": semantic})
    tree = wire_tree(row)
    tree[6][4][1] = tagged
    assert tree[5] == compute_source_hash(SHEETS[0], row.raw_values).source_hash
    error = assert_payload_rejected(wire(tree))
    assert "Forged/mismatched source_hash" not in str(error.__cause__)


@pytest.mark.parametrize("style", ["uppercase", "braces", "urn"])
def test_rc06_noncanonical_uuid_spellings(style: str) -> None:
    row = make_row(number=255)
    tree = wire_tree(row)
    tree[4] = {
        "uppercase": row.canonical_uuid.upper(),
        "braces": "{" + row.canonical_uuid + "}",
        "urn": "urn:uuid:" + row.canonical_uuid,
    }[style]
    assert tree[4] != row.canonical_uuid
    assert_payload_rejected(wire(tree))


@pytest.mark.parametrize(
    "field_index,value",
    [(0, ["int", "1"]), (1, ["int", "1"]), (5, ["decimal", ["0", "15", "-1"]])],
)
def test_rc06_field_type_rules_remain_upstream(field_index: int, value: Any) -> None:
    tree = json.loads(GOLDEN_JSON[0])
    tree[6][field_index][1] = value
    error = assert_payload_rejected(wire(tree))
    assert isinstance(error.__cause__, ContractError)
    assert "Forged/mismatched source_hash" not in str(error.__cause__)


@pytest.mark.parametrize(
    "case", ["space", "newline", "unicode_escape", "slash_escape", "surrogate"]
)
def test_rc07_noncanonical_byte_spellings(case: str) -> None:
    payload = GOLDEN_JSON[0].encode()
    if case == "space":
        payload = b" " + payload
    elif case == "newline":
        payload += b"\n"
    elif case == "unicode_escape":
        payload = json.dumps(
            json.loads(payload), ensure_ascii=True, separators=(",", ":")
        ).encode()
    elif case == "slash_escape":
        payload = payload.replace(b"/", b"\\/")
    else:
        payload = payload.replace(b"RAW-GOLDEN-A", b"\\ud800")
    assert_payload_rejected(payload)


def test_rc07_permutation_and_deep_immutability() -> None:
    row = make_row()
    permuted = ValidatedSourceRow(
        row.stable_id,
        row.canonical_uuid,
        row.sheet_name,
        MappingProxyType(dict(reversed(list(row.raw_values.items())))),
        row.source_hash,
    )
    before = row_view(row)
    assert encode_source_raw_row(permuted) == encode_source_raw_row(row)
    decoded = decode_source_raw_row(encode_source_raw_row(row))
    assert list(decoded.raw_values) == list(FIELDS[0])
    with pytest.raises(TypeError):
        decoded.raw_values["quantity_raw"] = 2  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        decoded.source_hash = "0" * 64  # type: ignore[misc]
    assert row_view(row) == before


@settings(max_examples=60, deadline=None)
@given(
    sheet=st.integers(0, 3),
    number=st.integers(1, 100000),
    text=st.text(alphabet=st.characters(exclude_categories=["Cs"]), max_size=40),
    numeric=st.one_of(
        st.none(),
        st.integers(-(10**40), 10**40),
        st.sampled_from(
            [" ۰۰۱.۰۰ ", "-0.000", Decimal("-0E+4"), Decimal("1.000"), Decimal("1E-7")]
        ),
        st.tuples(
            st.integers(0, 1),
            st.lists(st.integers(0, 9), min_size=1, max_size=30),
            st.integers(-15, 15),
        ).map(lambda t: Decimal((t[0], tuple(t[1]), t[2]))),
    ),
    permutation=st.permutations(range(8)),
)
def test_rc10_generated_rows_against_scalar_and_wire_oracles(
    sheet: int,
    number: int,
    text: str,
    numeric: Any,
    permutation: list[int],
) -> None:
    changes = {"party_name_raw": text}
    if sheet in (0, 2):
        changes["quantity_raw"] = numeric
    row = make_row(sheet, changes, number)
    order = [index for index in permutation if index < len(FIELDS[sheet])]
    raw = MappingProxyType(
        {FIELDS[sheet][i]: row.raw_values[FIELDS[sheet][i]] for i in order}
    )
    permuted = ValidatedSourceRow(
        row.stable_id, row.canonical_uuid, row.sheet_name, raw, row.source_hash
    )
    payload = encode_source_raw_row(permuted)
    assert payload == wire(wire_tree(row))
    assert row_view(decode_source_raw_row(payload)) == row_view(row)
    assert encode_source_raw_row(decode_source_raw_row(payload)) == payload


@settings(max_examples=50, deadline=None)
@given(
    tag=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", max_size=12).filter(
        lambda tag: tag not in {"null", "text", "int", "decimal"}
    )
)
def test_rc10_generated_invalid_tags(tag: str) -> None:
    tree = json.loads(GOLDEN_JSON[0])
    tree[6][0][1][0] = tag
    assert_payload_rejected(wire(tree))


@pytest.mark.parametrize(
    "reason,message",
    [
        (SourceRawCodecReason.INVALID_INPUT, "Invalid Raw codec input."),
        (SourceRawCodecReason.INVALID_PAYLOAD, "Invalid Raw row payload."),
    ],
)
def test_rc11_exact_safe_public_error(
    reason: SourceRawCodecReason, message: str
) -> None:
    error = SourceRawCodecError(reason)
    assert error.reason is reason and str(error) == message and error.args == (message,)
    assert repr(error) == f"SourceRawCodecError({message!r})"


@pytest.mark.parametrize("value", ["invalid_input", "invalid_payload", None])
def test_rc11_error_reason_is_strict(value: Any) -> None:
    with pytest.raises(TypeError, match="^Invalid Raw codec reason\\.$"):
        SourceRawCodecError(value)


def test_rc11_foreign_enum_and_payload_markers() -> None:
    class Foreign(StrEnum):
        INVALID_INPUT = "invalid_input"

    with pytest.raises(TypeError, match="^Invalid Raw codec reason\\.$"):
        SourceRawCodecError(Foreign.INVALID_INPUT)  # type: ignore[arg-type]
    marker = "SYNTHETIC-RAW-MARKER-C:/private/path"
    row = make_row(0, {"party_name_raw": marker})
    tree = wire_tree(row)
    tree[5] = "b" * 64
    error = assert_payload_rejected(wire(tree))
    for text in (str(error), repr(error), repr(error.args)):
        for secret in (marker, row.canonical_uuid, row.source_hash, "b" * 64):
            assert secret not in text


@pytest.mark.parametrize("entry", ["encode", "decode"])
@pytest.mark.parametrize(
    "failure", [ValueError("SYNTHETIC-diagnostic"), KeyboardInterrupt(), SystemExit(17)]
)
def test_rc11_exact_causes_cancellation_and_context(
    entry: str,
    failure: BaseException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = make_row()
    payload = GOLDEN_JSON[0].encode()

    def fail(*args: Any, **kwargs: Any) -> Any:
        from decimal import getcontext

        getcontext().flags[InvalidOperation] = True
        raise failure

    monkeypatch.setattr(
        codec, "_encode_row" if entry == "encode" else "_decode_row", fail
    )
    with localcontext() as context:
        before = context_view(context)
        with pytest.raises(BaseException) as caught:
            if entry == "encode":
                encode_source_raw_row(row)
            else:
                decode_source_raw_row(payload)
        assert context_view(context) == before
    if isinstance(failure, Exception):
        assert isinstance(caught.value, SourceRawCodecError)
        assert caught.value.__cause__ is failure
        expected = (
            SourceRawCodecReason.INVALID_INPUT
            if entry == "encode"
            else SourceRawCodecReason.INVALID_PAYLOAD
        )
        assert caught.value.reason is expected
    else:
        assert caught.value is failure


def test_rc11_preclassified_error_not_wrapped_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = SourceRawCodecError(SourceRawCodecReason.INVALID_PAYLOAD)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise failure

    monkeypatch.setattr(codec, "_decode_row", fail)
    with pytest.raises(SourceRawCodecError) as caught:
        decode_source_raw_row(GOLDEN_JSON[0].encode())
    assert caught.value is failure and caught.value.__cause__ is None


@pytest.mark.parametrize("case", ["decimal-tuple", "raw-text", "source-hash"])
def test_rc12_semantic_mutation_oracles(case: str) -> None:
    row = make_row(0, {"quantity_raw": Decimal("-0.00")})
    if case == "source-hash":
        tree = wire_tree(row)
        tree[5] = "0" * 64
        rejected = False
        try:
            decode_source_raw_row(wire(tree))
        except SourceRawCodecError as error:
            rejected = error.reason is SourceRawCodecReason.INVALID_PAYLOAD
        assert rejected is True
    else:
        tagged = dict(json.loads(encode_source_raw_row(row))[6])
        if case == "decimal-tuple":
            assert tagged["quantity_raw"] == ["decimal", ["1", "0", "-2"]]
        else:
            assert tagged["date_raw"] == ["text", " ۱۴۰۵/۱/۱ "]


def test_rc12_call_purity_and_injected_write_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = make_row(3, {"party_name_raw": None})
    payload = wire(wire_tree(row))
    before = row_view(row)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    for name in (
        "plan_source_changes",
        "project_source_prior",
        "evaluate_source_requiredness",
        "evaluate_source_fiscal_evidence",
    ):
        function = getattr(contracts, name)
        monkeypatch.setattr(sys.modules[function.__module__], name, forbidden)
        monkeypatch.setattr(contracts, name, forbidden)
    with deny_side_effects():
        assert encode_source_raw_row(row) == payload
        assert row_view(decode_source_raw_row(payload)) == before
    original = codec._encode_scalar
    canary = tmp_path / "SYNTHETIC-side-effect.txt"

    def inject(value: object) -> list[Any]:
        canary.write_text("SYNTHETIC")
        return original(value)

    monkeypatch.setattr(codec, "_encode_scalar", inject)
    with deny_side_effects(), pytest.raises(SourceRawCodecError) as caught:
        encode_source_raw_row(row)
    assert isinstance(caught.value.__cause__, ForbiddenSideEffect)
    assert not canary.exists() and row_view(row) == before


def test_rc13_complete_15000_row_replay_benchmark() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("source_raw_codec_benchmark.py")),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads(result.stdout)
    assert record["rows_checked"] == 15000 and record["planner_items_checked"] == 15000
    assert record["max_caller_payloads_retained"] == 1
    assert all(
        record[key] >= 0
        for key in (
            "fixture_seconds",
            "encode_seconds",
            "decode_seconds",
            "planner_seconds",
            "peak_rss_mib",
        )
    )
    print(json.dumps(record))
