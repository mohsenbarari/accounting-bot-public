"""XI protocol, negative, composition and scale evidence on synthetic packages."""

from __future__ import annotations

import inspect
import io
import json
import subprocess
import sys
import time
import uuid
import zipfile
from dataclasses import FrozenInstanceError, fields, replace
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import accounting_local_agent as public
import accounting_local_agent.xlsx_source_identity as identity
import pytest
from accounting_contracts import (
    SourceBindingKey,
    SourceSheetInput,
    build_source_workbook_snapshot,
    evaluate_source_fiscal_evidence,
)
from accounting_local_agent import (
    IdentifiedXlsxSource,
    XlsxPackageError,
    XlsxSourceIdentityError,
    XlsxSourceIdentityReason,
    XlsxSourceNotReadyError,
    read_identified_xlsx_source,
    read_xlsx_source_snapshot,
)
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from xlsx_source_identity_fixtures import (
    FMTID,
    MARKER,
    PART,
    SHEETS,
    TRANSITIONAL,
    VALUE,
    identified_parts,
    property_xml,
    raw_parts,
    uid,
    zipped,
)

PUBLIC_NAMES = (
    "XLSX_SOURCE_IDENTITY_VERSION",
    "XLSX_SOURCE_IDENTITY_PROPERTY_NAME",
    "XLSX_SOURCE_IDENTITY_MAX_METADATA_BYTES",
    "XlsxSourceIdentityReason",
    "XlsxSourceIdentityError",
    "IdentifiedXlsxSource",
    "read_identified_xlsx_source",
)


def read_parts(tmp_path: Path, parts: dict[str, bytes]) -> IdentifiedXlsxSource:
    source, root = tmp_path / "SYNTHETIC-source.xlsx", tmp_path / "leases"
    root.mkdir(exist_ok=True)
    data = zipped(parts)
    source.write_bytes(data)
    try:
        result = read_identified_xlsx_source(
            source, snapshot_root=root, observation_interval_seconds=0.001
        )
    finally:
        assert source.read_bytes() == data
        assert list(root.iterdir()) == []
    assert result.file_sha256 == sha256(data).hexdigest()
    assert result.byte_count == len(data)
    return result


def test_xi01_exports_signature_and_guarded_fresh_import(tmp_path: Path) -> None:
    assert tuple(identity.__all__) == PUBLIC_NAMES
    for name in PUBLIC_NAMES:
        assert name in public.__all__
        assert getattr(public, name) is getattr(identity, name)
    assert identity.XLSX_SOURCE_IDENTITY_VERSION == "xlsx-source-identity.v1"
    assert identity.XLSX_SOURCE_IDENTITY_PROPERTY_NAME == "AccountingBot.SourceIdentity"
    assert type(identity.XLSX_SOURCE_IDENTITY_MAX_METADATA_BYTES) is int
    assert identity.XLSX_SOURCE_IDENTITY_MAX_METADATA_BYTES == 1_048_576
    assert list(inspect.signature(read_identified_xlsx_source).parameters) == [
        "source_path",
        "snapshot_root",
        "observation_interval_seconds",
    ]
    for arg in ("snapshot_root", "observation_interval_seconds"):
        assert inspect.signature(read_identified_xlsx_source).parameters[arg].kind is (
            inspect.Parameter.KEYWORD_ONLY
        )
    assert [f.name for f in fields(IdentifiedXlsxSource)] == [
        "key",
        "read_result",
        "file_sha256",
        "byte_count",
        "version",
    ]
    probe = Path(__file__).with_name("xlsx_source_identity_import_probe.py")
    for mode, status in (("normal", 0), ("inject_write", 73)):
        canary = tmp_path / "SYNTHETIC-canary"
        outcome = subprocess.run(
            [sys.executable, str(probe), mode, str(canary)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert outcome.returncode == status, outcome.stdout + outcome.stderr
        assert "IMPORT_ENTERED" in outcome.stdout
        assert (
            "PROBE_OK" if status == 0 else "IMPORT_REJECTED_BY_GUARD"
        ) in outcome.stdout
        assert not canary.exists()


@pytest.mark.parametrize("strict", [False, True], ids=["transitional", "strict"])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_xi02_literal_valid_marker_and_raw(
    tmp_path: Path, strict: bool, encoding: str
) -> None:
    raw = raw_parts(strict=strict)
    parts = identified_parts(
        strict=strict,
        raw=raw,
        part="metadata/source-v1.xml",
        target="/metadata/source-v1.xml",
        metadata=property_xml(
            strict=strict, prefix="custom", value_prefix="text", encoding=encoding
        ),
    )
    # Independently permute relationship order, not merely ZIP member order.
    rel = ET.fromstring(parts["_rels/.rels"])
    rel[:] = list(reversed(list(rel)))
    parts["_rels/.rels"] = ET.tostring(rel, encoding=encoding)
    result = read_parts(tmp_path, dict(reversed(list(parts.items()))))
    assert result.key.source_id == uuid.UUID("00000000-0000-7000-8000-0000000003e7")
    assert result.key.fiscal_year == 1405
    reference = tmp_path / "raw-only.xlsx"
    reference.write_bytes(zipped(raw))
    expected = read_xlsx_source_snapshot(reference)
    assert result.read_result == expected
    # Literal Raw oracle, separate from both XLSX parsers and fixture factories.
    buy = {
        "date_raw": "1403/05/15",
        "party_name_raw": "بازرگانی احمدی",
        "transaction_type_raw": "خرید",
        "item_name_raw": "طلای آبشده",
        "quantity_raw": "12.34",
        "unit_price_toman_raw": "1500000",
        "discount_toman_raw": "0",
        "notes_raw": "توضیحات فاکتور",
    }
    literal = build_source_workbook_snapshot(
        [
            SourceSheetInput(SHEETS[0], [(uid(1), buy), (uid(2), buy)]),
            SourceSheetInput(
                SHEETS[1],
                [
                    (
                        uid(100001),
                        {
                            "date_raw": "1403/01/01",
                            "party_name_raw": "همکار نمونه",
                            "entry_type_raw": "RS",
                            "amount_toman_raw": "50000000",
                            "notes_raw": "تسویه حساب",
                            "account_code_raw": "101",
                            "customer_flag_raw": "1",
                        },
                    )
                ],
            ),
            SourceSheetInput(
                SHEETS[2],
                [
                    (
                        uid(200001),
                        {
                            "date_raw": "1403/12/29",
                            "party_name_raw": "کارگاه زرگری",
                            "movement_type_raw": "ورود",
                            "item_name_raw": "شمش طلا",
                            "quantity_raw": "100.5",
                            "purity_raw": "750",
                            "notes_raw": "تحویل شمش",
                            "customer_flag_raw": "1",
                        },
                    )
                ],
            ),
            SourceSheetInput(
                SHEETS[3],
                [
                    (
                        uid(300001),
                        {
                            "party_name_raw": "فروشگاه نمونه",
                            "phone_number_raw": "SYNTHETIC-PHONE-001",
                        },
                    )
                ],
            ),
        ]
    )
    assert result.read_result.snapshot == literal
    assert set(result.read_result.snapshot.all_rows_by_id) == {
        uid(1),
        uid(2),
        uid(100001),
        uid(200001),
        uid(300001),
    }


@pytest.mark.parametrize(
    "case", ["no-relation", "orphan", "no-property", "empty", "undated", "mixed"]
)
def test_xi03_missing_marker_never_guesses(tmp_path: Path, case: str) -> None:
    parts = (
        raw_parts(rows_per_sheet=0)
        if case == "empty"
        else raw_parts(undated=case == "undated", mixed=case == "mixed")
    )
    raw_path = tmp_path / "markerless.xlsx"
    raw_path.write_bytes(zipped(parts))
    if case == "mixed":
        standalone = read_xlsx_source_snapshot(raw_path)
        assert evaluate_source_fiscal_evidence(standalone.snapshot).observed_years == (
            1403,
            1404,
        )
    assert read_xlsx_source_snapshot(raw_path).snapshot.total_row_count == (
        0 if case == "empty" else 5
    )
    if case == "orphan":
        parts[PART] = property_xml()
    elif case == "no-property":
        parts = identified_parts(raw=parts, metadata=property_xml(name="Unrelated"))
    with pytest.raises(XlsxSourceIdentityError) as caught:
        read_parts(tmp_path, parts)
    assert caught.value.reason is XlsxSourceIdentityReason.MISSING_MARKER


@pytest.mark.parametrize(
    "case",
    [
        "relationships",
        "external-duplicate",
        "invalid-duplicate",
        "relation-id",
        "properties",
        "case-property",
        "numeric-pid",
        "override",
        "zip-duplicate",
        "rels-alias",
        "types-alias",
        "property-alias",
    ],
)
def test_xi04_duplicate_candidates(tmp_path: Path, case: str) -> None:
    parts = identified_parts()
    expected: Any = XlsxSourceIdentityReason.INVALID_MARKER
    if case in {
        "relationships",
        "external-duplicate",
        "invalid-duplicate",
        "relation-id",
    }:
        root = ET.fromstring(parts["_rels/.rels"])
        duplicate = ET.fromstring(ET.tostring(root[-1]))
        duplicate.set("Id", "identity" if case == "relation-id" else "other")
        if case == "external-duplicate":
            duplicate.set("TargetMode", "External")
        if case == "invalid-duplicate":
            duplicate.set("Target", "../outside")
        root.append(duplicate)
        parts["_rels/.rels"] = ET.tostring(root)
        expected = (
            "XLSX_PACKAGE_DUPLICATE_REL_ID"
            if case == "relation-id"
            else XlsxSourceIdentityReason.AMBIGUOUS_MARKER
        )
    elif case in {"properties", "case-property", "numeric-pid"}:
        root = ET.fromstring(parts[PART])
        duplicate = ET.fromstring(ET.tostring(root[0]))
        duplicate.set("pid", "02" if case == "numeric-pid" else "3")
        if case == "case-property":
            duplicate.set("name", MARKER.lower())
        root.append(duplicate)
        parts[PART] = ET.tostring(root)
        if case != "numeric-pid":
            expected = XlsxSourceIdentityReason.AMBIGUOUS_MARKER
    elif case == "override":
        root = ET.fromstring(parts["[Content_Types].xml"])
        root.append(ET.fromstring(ET.tostring(root[-1])))
        parts["[Content_Types].xml"] = ET.tostring(root)
    elif case.endswith("alias"):
        path = {
            "rels-alias": "_rels/.rels",
            "types-alias": "[Content_Types].xml",
            "property-alias": PART,
        }[case]
        parts[path.upper()] = parts[path]
        expected = "XLSX_PACKAGE_DUPLICATE_ZIP_ENTRY"
    if case == "zip-duplicate":
        source, leases = tmp_path / "duplicate.xlsx", tmp_path / "leases"
        leases.mkdir()
        with pytest.warns(UserWarning, match="Duplicate name"):
            source.write_bytes(zipped([*parts.items(), (PART, parts[PART])]))
        with pytest.raises(XlsxPackageError) as package_error:
            read_identified_xlsx_source(
                source, snapshot_root=leases, observation_interval_seconds=0.001
            )
        assert package_error.value.reason == "XLSX_PACKAGE_DUPLICATE_ZIP_ENTRY"
        assert list(leases.iterdir()) == []
    elif isinstance(expected, XlsxSourceIdentityReason):
        with pytest.raises(XlsxSourceIdentityError) as caught:
            read_parts(tmp_path, parts)
        assert caught.value.reason is expected
    else:
        with pytest.raises(XlsxPackageError) as caught_package:
            read_parts(tmp_path, parts)
        assert caught_package.value.reason == expected


@pytest.mark.parametrize(
    "value",
    [
        VALUE.replace(".v1|", ".v2|"),
        VALUE.replace("|", "/", 1),
        VALUE + "|x",
        VALUE.replace("0000000003e7", "0000000003E7"),
        VALUE.replace("-7000-", "-4000-"),
        VALUE.replace("-8000-", "-c000-"),
        VALUE.replace("-8000-", "-0000-"),
        VALUE.replace("1405", "۱۴۰۵"),
        " " + VALUE,
        VALUE + "\n",
        VALUE.replace("1405", "0000"),
        VALUE.replace("1405", "9378"),
        VALUE.replace("1405", "-001"),
        VALUE.replace("1405", "14050"),
        VALUE.replace("identity", "_x0069_dentity"),
        VALUE.replace("3e7", "ggg"),
    ],
)
def test_xi05_invalid_wire_values(tmp_path: Path, value: str) -> None:
    with pytest.raises(XlsxSourceIdentityError) as caught:
        read_parts(tmp_path, identified_parts(value=value))
    assert caught.value.reason is XlsxSourceIdentityReason.INVALID_MARKER


@pytest.mark.parametrize("year", [1, 1404, 1405, 9377])
def test_xi05_declared_year_boundaries(tmp_path: Path, year: int) -> None:
    result = read_parts(tmp_path, identified_parts(value=VALUE[:-4] + f"{year:04d}"))
    assert result.key == SourceBindingKey(uid(999), year)


@pytest.mark.parametrize(
    "case",
    [
        "name-case",
        "fmtid",
        "pid-absent",
        "pid-zero",
        "pid-negative",
        "pid-large",
        "pid-long",
        "name-empty",
        "type",
        "value-namespace",
        "root-namespace",
        "family-mismatch",
        "nested",
        "alternate",
        "link",
        "empty-link",
        "wrapper",
    ],
)
def test_xi05_invalid_property_profile(tmp_path: Path, case: str) -> None:
    parts = identified_parts()
    root = ET.fromstring(parts[PART])
    prop, value = root[0], root[0][0]
    if case == "name-case":
        prop.set("name", MARKER.lower())
    elif case == "fmtid":
        prop.set("fmtid", "invalid")
    elif case == "pid-absent":
        del prop.attrib["pid"]
    elif case.startswith("pid-"):
        prop.set(
            "pid",
            {
                "pid-zero": "0",
                "pid-negative": "-2",
                "pid-large": "2147483648",
                "pid-long": "9" * 5000,
            }[case],
        )
    elif case == "name-empty":
        prop.set("name", "")
    elif case == "type":
        value.tag = f"{{{TRANSITIONAL[2]}}}i4"
    elif case == "value-namespace":
        value.tag = "{urn:synthetic-invalid}lpwstr"
    elif case == "root-namespace":
        root.tag = "{urn:synthetic-invalid}Properties"
    elif case == "family-mismatch":
        parts[PART] = property_xml(strict=True)
    elif case == "nested":
        ET.SubElement(value, "nested").text = "invalid"
    elif case == "alternate":
        prop.append(ET.fromstring(ET.tostring(value)))
    elif case in {"link", "empty-link"}:
        prop.set("linkTarget", "A1" if case == "link" else "")
    elif case == "wrapper":
        root.remove(prop)
        ET.SubElement(root, "wrapper").append(prop)
    if case != "family-mismatch":
        parts[PART] = ET.tostring(root)
    with pytest.raises(XlsxSourceIdentityError) as caught:
        read_parts(tmp_path, parts)
    assert caught.value.reason is XlsxSourceIdentityReason.INVALID_MARKER


def test_xi05_unrelated_values_links_and_comments_ignored(tmp_path: Path) -> None:
    extra = (
        f'<p:property fmtid="{FMTID}" pid="0003" name="Other" linkTarget="A1">'
        "<v:unknown><nested/></v:unknown></p:property>"
    )
    metadata = property_xml(extra_properties=extra, reverse=True).replace(
        b"<v:lpwstr>", b"<!-- independent comment --><?synthetic test?><v:lpwstr>"
    )
    result = read_parts(tmp_path, identified_parts(metadata=metadata))
    assert result.key == SourceBindingKey(uid(999), 1405)


@pytest.mark.parametrize(
    "target",
    [
        "https://example.invalid/a",
        "../custom.xml",
        "docProps/../custom.xml",
        "//docProps/custom.xml",
        "docProps//custom.xml",
        "docProps/custom.xml/",
        "docProps/./custom.xml",
        "docProps\\custom.xml",
        "docProps/%63ustom.xml",
        "docProps/custom.xml?q",
        "docProps/custom.xml#f",
        "",
        "missing.xml",
    ],
)
def test_xi06_invalid_targets(tmp_path: Path, target: str) -> None:
    with pytest.raises(XlsxSourceIdentityError) as caught:
        read_parts(tmp_path, identified_parts(target=target))
    assert caught.value.reason is XlsxSourceIdentityReason.INVALID_MARKER


@pytest.mark.parametrize(
    "case",
    [
        "external",
        "target-mode-case",
        "missing-override",
        "wrong-type",
        "override-case",
        "missing-member",
        "missing-rels",
        "missing-types",
    ],
)
def test_xi06_package_profile(tmp_path: Path, case: str) -> None:
    parts = identified_parts()
    if case in {"external", "target-mode-case"}:
        root = ET.fromstring(parts["_rels/.rels"])
        root[-1].set("TargetMode", "External" if case == "external" else "internal")
        parts["_rels/.rels"] = ET.tostring(root)
    elif case in {"missing-override", "wrong-type", "override-case"}:
        root = ET.fromstring(parts["[Content_Types].xml"])
        if case == "missing-override":
            root.remove(root[-1])
        else:
            root[-1].set(
                "ContentType" if case == "wrong-type" else "PartName",
                "wrong" if case == "wrong-type" else "/DOCPROPS/CUSTOM.XML",
            )
        parts["[Content_Types].xml"] = ET.tostring(root)
    else:
        del parts[
            {
                "missing-member": PART,
                "missing-rels": "_rels/.rels",
                "missing-types": "[Content_Types].xml",
            }[case]
        ]
    if case == "missing-types":
        # Acquisition detects this before yielding; its accepted taxonomy wins.
        with pytest.raises(XlsxSourceNotReadyError):
            read_parts(tmp_path, parts)
        with zipfile.ZipFile(io.BytesIO(zipped(parts))) as zf:
            with pytest.raises(XlsxPackageError) as metadata_error:
                identity._read_key(zf)
            assert metadata_error.value.reason == "XLSX_PACKAGE_MISSING_CONTENT_TYPES"
    elif case == "missing-rels":
        with pytest.raises(XlsxPackageError) as caught:
            read_parts(tmp_path, parts)
        assert caught.value.reason == (
            "XLSX_PACKAGE_MISSING_ROOT_RELS"
            if case == "missing-rels"
            else "XLSX_PACKAGE_MISSING_CONTENT_TYPES"
        )
    else:
        with pytest.raises(XlsxSourceIdentityError) as invalid:
            read_parts(tmp_path, parts)
        assert invalid.value.reason is XlsxSourceIdentityReason.INVALID_MARKER


@pytest.mark.parametrize("member", ["_rels/.rels", "[Content_Types].xml", PART])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_xi06_unsafe_xml_rejected(tmp_path: Path, member: str, encoding: str) -> None:
    parts = identified_parts()
    text = parts[member].decode("utf-8")
    if text.startswith("<?xml"):
        text = text.split("?>", 1)[1]
    parts[member] = (
        f'<?xml version="1.0" encoding="{encoding}"?>'
        '<!DOCTYPE root [<!ENTITY leak SYSTEM "https://example.invalid/secret">]>'
        + text
    ).encode(encoding)
    with pytest.raises(XlsxSourceIdentityError) as caught:
        read_parts(tmp_path, parts)
    assert caught.value.reason is XlsxSourceIdentityReason.INVALID_MARKER


@pytest.mark.parametrize("member", ["_rels/.rels", "[Content_Types].xml", PART])
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_xi06_metadata_size_boundary(tmp_path: Path, member: str, delta: int) -> None:
    parts = identified_parts()
    size = 1_048_576 + delta
    parts[member] += b" " * (size - len(parts[member]))
    assert len(parts[member]) == size
    if delta <= 0:
        assert read_parts(tmp_path, parts).key == SourceBindingKey(uid(999), 1405)
    else:
        with pytest.raises(XlsxSourceIdentityError) as caught:
            read_parts(tmp_path, parts)
        assert caught.value.reason is XlsxSourceIdentityReason.METADATA_LIMIT_EXCEEDED


@pytest.mark.parametrize("member", ["_rels/.rels", "[Content_Types].xml", PART])
def test_xi06_actual_decompression_is_bounded(member: str) -> None:
    parts = identified_parts()
    parts[member] += b" " * (1_048_600 - len(parts[member]))
    calls = []
    with zipfile.ZipFile(io.BytesIO(zipped(parts))) as archive:

        class ActualStream:
            def __init__(self) -> None:
                self.stream = archive.open(member)

            def read(self, size: int = -1) -> bytes:
                calls.append(size)
                assert size == 1_048_577
                return self.stream.read(size)

            def close(self) -> None:
                self.stream.close()

        stream = ActualStream()

        class FalseHeader:
            def getinfo(self, name: str) -> zipfile.ZipInfo:
                info = zipfile.ZipInfo(name)
                info.file_size = 1
                return info

            def open(self, info: zipfile.ZipInfo, mode: str) -> Any:
                return stream

        fake: Any = FalseHeader()
        with pytest.raises(XlsxSourceIdentityError) as caught:
            identity._metadata_xml(fake, member)
        assert caught.value.reason is XlsxSourceIdentityReason.METADATA_LIMIT_EXCEEDED
        assert calls == [1_048_577]
        assert stream.stream.closed


@pytest.mark.parametrize("member", ["_rels/.rels", "[Content_Types].xml", PART])
def test_xi06_malformed_xml_has_fixed_private_error(
    tmp_path: Path, member: str
) -> None:
    parts = identified_parts()
    marker = b"SYNTHETIC-PRIVATE-CONTENT"
    parts[member] = b"<broken>" + marker
    with pytest.raises(XlsxSourceIdentityError) as caught:
        read_parts(tmp_path, parts)
    assert caught.value.reason is XlsxSourceIdentityReason.INVALID_MARKER
    assert caught.value.__cause__ is not None
    public_text = str(caught.value) + repr(caught.value) + str(caught.value.args)
    assert marker.decode() not in public_text and str(tmp_path) not in public_text


class ForeignReason(StrEnum):
    INVALID_INPUT = "invalid_input"


@pytest.mark.parametrize(
    "reason",
    ["invalid_input", "SYNTHETIC-SECRET", ForeignReason.INVALID_INPUT, None, 1],
)
def test_xi12_error_enum_is_exact_and_sanitized(reason: Any) -> None:
    with pytest.raises(XlsxSourceIdentityError) as caught:
        XlsxSourceIdentityError(reason)
    assert caught.value.reason is XlsxSourceIdentityReason.INVALID_INPUT
    for valid in XlsxSourceIdentityReason:
        error = XlsxSourceIdentityError(valid)
        assert error.reason is valid
        assert error.reason_code == "XLSX_SOURCE_IDENTITY_" + valid.name
        assert error.args == (f"XLSX source identity error: {error.reason_code}",)
        assert "SYNTHETIC-SECRET" not in repr(error)


def test_xi12_result_invariants_and_private_repr(tmp_path: Path) -> None:
    result = read_parts(tmp_path, identified_parts())
    assert result.version == "xlsx-source-identity.v1"
    assert "read_result" not in repr(result) and "بازرگانی" not in repr(result)
    assert not hasattr(result, "__dict__")
    duplicate = IdentifiedXlsxSource(
        result.key, result.read_result, result.file_sha256, result.byte_count
    )
    assert duplicate.key is result.key and duplicate.read_result is result.read_result
    dynamic: Any = result
    with pytest.raises(FrozenInstanceError):
        dynamic.key = SourceBindingKey(uid(42), 1405)
    for change in (
        {"key": "SYNTHETIC-SECRET"},
        {"read_result": object()},
        {"file_sha256": "A" * 64},
        {"file_sha256": "x" * 63},
        {"byte_count": True},
        {"byte_count": 0},
        {"byte_count": -1},
        {"byte_count": 1.5},
    ):
        with pytest.raises(XlsxSourceIdentityError) as caught:
            replace(result, **change)
        assert caught.value.reason is XlsxSourceIdentityReason.INVALID_INPUT
        assert "SYNTHETIC-SECRET" not in str(caught.value) + repr(caught.value)
    with pytest.raises((ValueError, TypeError)):
        replace(result, version="forged")
    with pytest.raises(TypeError):
        injected: dict[str, Any] = {"snapshot_path": "forged"}
        replace(result, **injected)


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    strict=st.booleans(),
    reverse=st.booleans(),
    index=st.integers(1, 10000),
    year=st.integers(1, 9377),
    part=st.sampled_from([PART, "meta/key.xml", "identity/source_1.xml"]),
    prefix=st.sampled_from(["p", "properties", "custom"]),
)
def test_xi13_independent_marker_property_oracle(
    tmp_path: Path,
    strict: bool,
    reverse: bool,
    index: int,
    year: int,
    part: str,
    prefix: str,
) -> None:
    key = SourceBindingKey(uid(index), year)
    value = f"xlsx-source-identity.v1|{key.source_id}|{year:04d}"
    extra = (
        f'<{prefix}:property pid="3" name="Other"><v:i4>7</v:i4></{prefix}:property>'
    )
    raw = raw_parts(strict=strict)
    parts = identified_parts(
        raw=raw,
        strict=strict,
        part=part,
        metadata=property_xml(
            value, strict=strict, prefix=prefix, reverse=reverse, extra_properties=extra
        ),
    )
    rel = ET.fromstring(parts["_rels/.rels"])
    if reverse:
        rel[:] = list(reversed(list(rel)))
    parts["_rels/.rels"] = ET.tostring(rel)
    first = read_parts(tmp_path, parts)
    assert first.key == key
    changed = SourceBindingKey(uid(index + 1), year)
    parts[part] = property_xml(
        f"xlsx-source-identity.v1|{changed.source_id}|{year:04d}", strict=strict
    )
    second = read_parts(tmp_path, parts)
    assert second.key == changed
    assert second.key != first.key
    assert second.read_result == first.read_result
    assert second.file_sha256 != first.file_sha256


def test_xi14_combined_15000_row_benchmark(tmp_path: Path) -> None:
    started = time.perf_counter()
    data = zipped(identified_parts(raw=raw_parts(rows_per_sheet=3750, extra_buy=False)))
    (tmp_path / "benchmark.xlsx").write_bytes(data)
    fixture_seconds = time.perf_counter() - started
    probe = Path(__file__).with_name("xlsx_source_identity_benchmark.py")
    result = subprocess.run(
        [sys.executable, str(probe), str(tmp_path), str(fixture_seconds)],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["rows"] == 15000
    assert evidence["seconds"] < 15.0
    assert evidence["peak_rss_mib"] < 128.0
    print(json.dumps(evidence, sort_keys=True))
