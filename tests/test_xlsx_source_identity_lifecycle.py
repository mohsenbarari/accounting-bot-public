"""XI-07..11/15: provenance, complete exit, independent failures and composition."""

from __future__ import annotations

import io
import os
import socket
import struct
import threading
import uuid
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import accounting_contracts.source_change_plan as planner
import accounting_local_agent.xlsx_source_identity as identity
import accounting_local_agent.xlsx_source_reader as reader
import pytest
from accounting_contracts import (
    SourceBindingDisposition,
    SourceBindingInputError,
    SourceBindingKey,
    SourceBindingRecord,
    SourceBindingRegistry,
    SourceBindingState,
    evaluate_source_fiscal_evidence,
    evaluate_source_requiredness,
    plan_source_changes,
    resolve_source_binding,
)
from accounting_local_agent import (
    StableXlsxSnapshot,
    XlsxPackageError,
    XlsxSnapshotCleanupError,
    XlsxSnapshotIntegrityError,
    XlsxSnapshotStorageError,
    XlsxSourceIdentityError,
    XlsxSourceIdentityReason,
    XlsxSourceNotReadyError,
    XlsxSourcePolicyError,
    XlsxSourceReadError,
    XlsxStructureError,
    open_stable_xlsx_snapshot,
    read_identified_xlsx_source,
)
from test_source_binding import _prior
from test_xlsx_source_identity import read_parts
from xlsx_source_identity_fixtures import (
    VALUE,
    identified_parts,
    raw_parts,
    uid,
    zipped,
)


def paths(tmp_path: Path) -> tuple[Path, Path, bytes]:
    source, leases = tmp_path / "SYNTHETIC-source.xlsx", tmp_path / "leases"
    data = zipped(identified_parts())
    source.write_bytes(data)
    leases.mkdir()
    return source, leases, data


def flatten(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [leaf for item in error.exceptions for leaf in flatten(item)]
    return [error]


def has_cause(error: BaseException, wanted: BaseException) -> bool:
    pending, visited = [error], set()
    while pending:
        node = pending.pop()
        if node is wanted:
            return True
        if id(node) in visited:
            continue
        visited.add(id(node))
        if isinstance(node, BaseExceptionGroup):
            pending.extend(node.exceptions)
        if node.__cause__ is not None:
            pending.append(node.__cause__)
    return False


def replace_zip(monkeypatch: pytest.MonkeyPatch, factory: Any) -> None:
    # Replace this module's seam, not zipfile globally (WP-06 also uses it).
    monkeypatch.setattr(
        identity,
        "zipfile",
        SimpleNamespace(ZipFile=factory, BadZipFile=zipfile.BadZipFile),
    )


def test_xi07_one_leased_zip_keeps_generation_a_when_live_path_becomes_b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, leases, a = paths(tmp_path)
    b = zipped(
        identified_parts(value=VALUE[:-4] + "1406", raw=raw_parts(seed=400, edit=True))
    )
    seen: list[zipfile.ZipFile] = []
    snapshots = []
    original_open = io.open

    def guard_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(file, (str, Path)) and Path(file) == source:
            raise AssertionError("Live source parsed after acquisition")
        return original_open(file, *args, **kwargs)

    @contextmanager
    def lease(*args: Any, **kwargs: Any) -> Iterator[StableXlsxSnapshot]:
        with open_stable_xlsx_snapshot(*args, **kwargs) as snap:
            snapshots.append(snap)
            replacement = tmp_path / "generation-b.xlsx"
            replacement.write_bytes(b)
            os.replace(replacement, source)
            with monkeypatch.context() as patch:
                patch.setattr(io, "open", guard_open)
                yield snap

    class TrackingZip(zipfile.ZipFile):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            assert Path(args[0]) == snapshots[0].snapshot_path
            seen.append(self)

    marker_zip, raw_zip = [], []
    original_key, original_raw = identity._read_key, reader._read_xlsx_from_zip

    def key(zf: zipfile.ZipFile) -> SourceBindingKey:
        marker_zip.append(zf)
        return original_key(zf)

    def raw(zf: zipfile.ZipFile) -> Any:
        raw_zip.append(zf)
        return original_raw(zf)

    monkeypatch.setattr(identity, "open_stable_xlsx_snapshot", lease)
    monkeypatch.setattr(identity, "_read_key", key)
    monkeypatch.setattr(reader, "_read_xlsx_from_zip", raw)
    replace_zip(monkeypatch, TrackingZip)
    result = read_identified_xlsx_source(
        source, snapshot_root=leases, observation_interval_seconds=0.001
    )
    assert len(seen) == 1 and marker_zip == raw_zip == seen
    assert seen[0].fp is None
    assert result.key == SourceBindingKey(uid(999), 1405)
    assert set(result.read_result.snapshot.all_rows_by_id) == {
        uid(1),
        uid(2),
        uid(100001),
        uid(200001),
        uid(300001),
    }
    assert result.file_sha256 == sha256(a).hexdigest() and result.byte_count == len(a)
    assert source.read_bytes() == b and list(leases.iterdir()) == []


@pytest.mark.parametrize("stage", ["between-marker-and-raw", "after-parsing"])
@pytest.mark.parametrize("operation", ["tamper", "replace"])
def test_xi08_lease_mutation_never_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, operation: str
) -> None:
    source, leases, before = paths(tmp_path)
    changed = zipped(identified_parts(value=VALUE[:-4] + "1406"))
    snapshots = []

    @contextmanager
    def lease(*args: Any, **kwargs: Any) -> Iterator[StableXlsxSnapshot]:
        with open_stable_xlsx_snapshot(*args, **kwargs) as snap:
            snapshots.append(snap)
            yield snap

    def mutate(zf: zipfile.ZipFile) -> None:
        target = snapshots[0].snapshot_path
        if operation == "replace":
            # Release the native ZIP handle at this injected seam so replacement
            # is possible on Windows; WP-06's independent lease still owns it.
            zf.close()
            replacement = tmp_path / "replacement.xlsx"
            replacement.write_bytes(changed)
            os.replace(replacement, target)
        else:
            target.write_bytes(changed)

    original_key, original_raw = identity._read_key, reader._read_xlsx_from_zip

    def key(zf: zipfile.ZipFile) -> SourceBindingKey:
        result = original_key(zf)
        if stage == "between-marker-and-raw":
            mutate(zf)
        return result

    def raw(zf: zipfile.ZipFile) -> Any:
        result = original_raw(zf)
        if stage == "after-parsing":
            mutate(zf)
        return result

    monkeypatch.setattr(identity, "open_stable_xlsx_snapshot", lease)
    monkeypatch.setattr(identity, "_read_key", key)
    monkeypatch.setattr(reader, "_read_xlsx_from_zip", raw)
    with pytest.raises(BaseException) as caught:
        read_identified_xlsx_source(
            source, snapshot_root=leases, observation_interval_seconds=0.001
        )
    assert any(
        isinstance(error, XlsxSnapshotIntegrityError) for error in flatten(caught.value)
    )
    assert source.read_bytes() == before
    # Foreign replacements belong to the injector and must survive quarantine.
    if operation == "replace":
        assert snapshots[0].snapshot_path.read_bytes() == changed


@pytest.mark.parametrize("stage", ["zip-close", "lease-exit"])
def test_xi08_delivery_waits_for_exit_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    source, leases, _ = paths(tmp_path)
    entered, release, delivered = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    errors, results = [], []

    def gate() -> None:
        entered.set()
        assert release.wait(5.0)

    class GatedZip(zipfile.ZipFile):
        def close(self) -> None:
            active = self.fp is not None
            super().close()
            if active and stage == "zip-close":
                gate()

    @contextmanager
    def lease(*args: Any, **kwargs: Any) -> Iterator[StableXlsxSnapshot]:
        with open_stable_xlsx_snapshot(*args, **kwargs) as snap:
            yield snap
        if stage == "lease-exit":
            assert list(leases.iterdir()) == []
            gate()

    def run() -> None:
        try:
            results.append(
                read_identified_xlsx_source(
                    source, snapshot_root=leases, observation_interval_seconds=0.001
                )
            )
            delivered.set()
        except BaseException as error:
            errors.append(error)

    replace_zip(monkeypatch, GatedZip)
    monkeypatch.setattr(identity, "open_stable_xlsx_snapshot", lease)
    runner = threading.Thread(target=run)
    runner.start()
    try:
        assert entered.wait(5.0)
        assert not delivered.is_set() and results == []
        release.set()
        assert delivered.wait(5.0)
    finally:
        release.set()
        runner.join(5.0)
    assert not runner.is_alive() and not errors
    assert len(results) == 1 and list(leases.iterdir()) == []


@pytest.mark.parametrize(
    "stage", ["acquisition", "identity", "raw", "zip-open", "zip-close"]
)
@pytest.mark.parametrize("cancel", [False, True])
def test_xi09_single_failure_identity_and_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, cancel: bool
) -> None:
    source, leases, before = paths(tmp_path)
    cause = OSError("SYNTHETIC diagnostic cause")
    failure: BaseException = (
        KeyboardInterrupt("synthetic cancellation")
        if cancel
        else (
            XlsxSourceIdentityError(XlsxSourceIdentityReason.INVALID_MARKER)
            if stage == "identity"
            else XlsxSourceReadError("SYNTHETIC_TEST_FAILURE")
        )
    )
    failure.__cause__ = cause

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise failure

    @contextmanager
    def failing_lease(*args: Any, **kwargs: Any) -> Iterator[StableXlsxSnapshot]:
        raise failure
        yield  # pragma: no cover

    class CloseFailure(zipfile.ZipFile):
        def close(self) -> None:
            active = self.fp is not None
            super().close()
            if active:
                raise failure

    if stage == "acquisition":
        monkeypatch.setattr(identity, "open_stable_xlsx_snapshot", failing_lease)
    elif stage == "identity":
        monkeypatch.setattr(identity, "_read_key", fail)
    elif stage == "raw":
        monkeypatch.setattr(reader, "_read_xlsx_from_zip", fail)
    else:
        replace_zip(monkeypatch, fail if stage == "zip-open" else CloseFailure)
    with pytest.raises(BaseException) as caught:
        read_identified_xlsx_source(
            source, snapshot_root=leases, observation_interval_seconds=0.001
        )
    assert caught.value is failure and caught.value.__cause__ is cause
    assert source.read_bytes() == before and list(leases.iterdir()) == []


@pytest.mark.parametrize("stage", ["open", "close"])
@pytest.mark.parametrize("kind", ["io", "corrupt"])
def test_xi09_zip_failures_have_fixed_typed_wrappers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, kind: str
) -> None:
    source, leases, _ = paths(tmp_path)
    failure = (
        OSError("SYNTHETIC-PRIVATE-PATH")
        if kind == "io"
        else zipfile.BadZipFile("SYNTHETIC-PRIVATE-PATH")
    )

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise failure

    class BadClose(zipfile.ZipFile):
        def close(self) -> None:
            active = self.fp is not None
            super().close()
            if active:
                raise failure

    replace_zip(monkeypatch, fail if stage == "open" else BadClose)
    with pytest.raises(
        XlsxSnapshotStorageError if kind == "io" else XlsxPackageError
    ) as caught:
        read_identified_xlsx_source(
            source, snapshot_root=leases, observation_interval_seconds=0.001
        )
    assert caught.value.__cause__ is failure
    assert "SYNTHETIC-PRIVATE-PATH" not in str(caught.value) + repr(caught.value) + str(
        caught.value.args
    )
    assert list(leases.iterdir()) == []


@pytest.mark.parametrize("cancel", [False, True])
def test_xi09_independent_read_close_integrity_cleanup_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    source, leases, before = paths(tmp_path)
    shared = OSError("SYNTHETIC shared diagnostic cause")
    run_error: BaseException = KeyboardInterrupt("run") if cancel else ValueError("run")
    close_error: BaseException = (
        SystemExit("close") if cancel else RuntimeError("close")
    )
    run_error.__cause__ = close_error.__cause__ = shared
    cleanup_cause = OSError("SYNTHETIC rmdir blocked")
    integrity_cause = OSError("SYNTHETIC integrity read blocked")
    original_rmdir = Path.rmdir
    cleanup_attempts = []

    def rmdir(path: Path) -> None:
        if path.is_relative_to(leases):
            cleanup_attempts.append(path)
            raise cleanup_cause
        original_rmdir(path)

    def failed_raw(zf: zipfile.ZipFile) -> Any:
        raise run_error

    def fault(stage: str, first: Path | None, second: Path | None) -> None:
        if stage == "before_lease_reverify":
            raise integrity_cause

    @contextmanager
    def lease(*args: Any, **kwargs: Any) -> Iterator[StableXlsxSnapshot]:
        with open_stable_xlsx_snapshot(*args, **kwargs, _fault_hook=fault) as snap:
            yield snap

    class FailedClose(zipfile.ZipFile):
        def close(self) -> None:
            active = self.fp is not None
            super().close()
            if active:
                raise close_error

    replace_zip(monkeypatch, FailedClose)
    monkeypatch.setattr(identity, "open_stable_xlsx_snapshot", lease)
    monkeypatch.setattr(reader, "_read_xlsx_from_zip", failed_raw)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "rmdir", rmdir)
        with pytest.raises(BaseExceptionGroup) as caught:
            read_identified_xlsx_source(
                source, snapshot_root=leases, observation_interval_seconds=0.001
            )
    leaves = flatten(caught.value)
    assert leaves[:2] == [run_error, close_error]
    assert leaves[0] is run_error and leaves[1] is close_error
    assert len(leaves) == 4
    assert isinstance(leaves[2], XlsxSnapshotIntegrityError)
    assert has_cause(leaves[2], integrity_cause)
    assert isinstance(leaves[3], XlsxSnapshotCleanupError)
    assert run_error.__cause__ is close_error.__cause__ is shared
    assert leaves[3].__cause__ is not None
    assert has_cause(leaves[3], cleanup_cause)
    assert cleanup_attempts
    assert isinstance(caught.value, ExceptionGroup) is (not cancel)
    assert source.read_bytes() == before
    # The injected filesystem failure can leave owned empty directories; reclaim
    # only those exact empty synthetic directories after the guard is removed.
    for directory in leases.iterdir():
        if directory.is_dir() and not list(directory.iterdir()):
            directory.rmdir()


def test_xi09_member_read_and_close_preserve_both_errors() -> None:
    read_error, close_error = OSError("synthetic read"), OSError("synthetic close")

    class Member:
        closed = False

        def read(self, size: int) -> bytes:
            raise read_error

        def close(self) -> None:
            self.closed = True
            raise close_error

    stream = Member()
    fake: Any = SimpleNamespace(
        getinfo=lambda _: SimpleNamespace(file_size=1), open=lambda *args: stream
    )
    with pytest.raises(ExceptionGroup) as caught:
        identity._metadata_xml(fake, "synthetic")
    leaves = flatten(caught.value)
    assert len(leaves) == 2 and all(
        isinstance(e, XlsxSnapshotStorageError) for e in leaves
    )
    assert leaves[0].__cause__ is read_error and leaves[1].__cause__ is close_error
    assert stream.closed


def test_xi09_cleanup_failure_alone_prevents_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, leases, before = paths(tmp_path)
    error = OSError("SYNTHETIC cleanup failure")
    original = Path.rmdir

    def fail(path: Path) -> None:
        if path.is_relative_to(leases):
            raise error
        original(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rmdir", fail)
        with pytest.raises(XlsxSnapshotCleanupError) as caught:
            read_identified_xlsx_source(
                source, snapshot_root=leases, observation_interval_seconds=0.001
            )
    assert caught.value.__cause__ is not None
    assert has_cause(caught.value, error)
    assert source.read_bytes() == before
    for directory in leases.iterdir():
        assert directory.is_dir() and not list(directory.iterdir())
        directory.rmdir()


@pytest.mark.parametrize("corruption", ["crc", "local-header"])
def test_xi09_corrupt_raw_member_retains_package_taxonomy(
    tmp_path: Path, corruption: str
) -> None:
    source, leases, before = paths(tmp_path)
    data = bytearray(before)
    # Mutate only the central-directory CRC of one Raw worksheet, keeping the
    # container and identity metadata valid for the actual WP-06 acquisition.
    offset = data.index(b"PK\x01\x02")
    while data[offset : offset + 4] == b"PK\x01\x02":
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", data, offset + 28)
        name = bytes(data[offset + 46 : offset + 46 + name_len]).decode()
        if name == "xl/worksheets/sheet1.xml":
            (crc,) = struct.unpack_from("<I", data, offset + 16)
            struct.pack_into("<I", data, offset + 16, crc ^ 1)
            break
        offset += 46 + name_len + extra_len + comment_len
    else:
        raise AssertionError("Synthetic worksheet not found")
    if corruption == "local-header":
        data = bytearray(before)
        with zipfile.ZipFile(io.BytesIO(before)) as zf:
            local = zf.getinfo("xl/worksheets/sheet1.xml").header_offset
        data[local + 30] = ord("y")
    source.write_bytes(data)
    # WP-05 wraps CRC failures encountered inside its XML pass as structure
    # errors; a failure opening the member retains the outer package taxonomy.
    with pytest.raises(
        XlsxPackageError if corruption == "local-header" else XlsxStructureError
    ) as caught:
        read_identified_xlsx_source(
            source, snapshot_root=leases, observation_interval_seconds=0.001
        )
    assert caught.value.reason == (
        "XLSX_PACKAGE_CORRUPT_ZIP"
        if corruption == "local-header"
        else "XLSX_STRUCTURE_MALFORMED_XML"
    )
    assert isinstance(caught.value.__cause__, zipfile.BadZipFile)
    assert source.read_bytes() == data and list(leases.iterdir()) == []


def test_xi10_raw_invariance_and_one_edit_with_planner_oracle(tmp_path: Path) -> None:
    first = read_parts(tmp_path, identified_parts())
    prior = _prior(first.read_result.snapshot, 7)
    expected_ids = [uid(1), uid(2), uid(100001), uid(200001), uid(300001)]
    unchanged = [(key, "unchanged", None) for key in expected_ids]
    for reorder, formula in ((True, 0), (False, 1), (True, 2)):
        parts = identified_parts(raw=raw_parts(reorder=reorder, formula=formula))
        parts["docProps/custom.xml"] = parts["docProps/custom.xml"].replace(
            b"</p:Properties>",
            b'<p:property pid="3" name="Other"><v:i4>999</v:i4>'
            b"</p:property></p:Properties>",
        )
        result = read_parts(tmp_path, parts)
        assert result.key == first.key
        assert result.read_result.snapshot == first.read_result.snapshot
        assert result.file_sha256 != first.file_sha256
        if reorder:
            assert result.read_result.locations_by_uuid[uid(1)].physical_row_number == 3
            assert result.read_result.locations_by_uuid[uid(2)].physical_row_number == 2
        plan = plan_source_changes(result.read_result.snapshot, prior)
        assert [
            (i.stable_id, i.action.value, i.planned_revision) for i in plan.items
        ] == unchanged
    edited = read_parts(tmp_path, identified_parts(raw=raw_parts(edit=True)))
    plan = plan_source_changes(edited.read_result.snapshot, prior)
    assert [(i.stable_id, i.action.value, i.planned_revision) for i in plan.items] == [
        (uid(1), "edit", 8),
        *unchanged[1:],
    ]


def test_xi11_explicit_binding_requiredness_fiscal_and_planner_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = read_parts(
        tmp_path, identified_parts(value=VALUE[:-4] + "1404", raw=raw_parts(seed=400))
    )
    value_b = f"xlsx-source-identity.v1|{uid(998)}|1405"
    b = read_parts(
        tmp_path,
        identified_parts(value=value_b, raw=raw_parts(undated=True, mixed=True)),
    )
    prior_a, prior_b = (
        _prior(a.read_result.snapshot, 3),
        _prior(b.read_result.snapshot, 7),
    )
    registry = SourceBindingRegistry(
        [
            SourceBindingRecord(
                a.key, SourceBindingState.ARCHIVED, prior_a, a.file_sha256
            ),
            SourceBindingRecord(b.key, SourceBindingState.ACTIVE, prior_b, None),
        ]
    )
    selected = resolve_source_binding(b.key, registry)
    assert selected.prior_registry is prior_b
    required = evaluate_source_requiredness(b.read_result.snapshot)
    fiscal = evaluate_source_fiscal_evidence(b.read_result.snapshot)
    assert required.snapshot is b.read_result.snapshot
    assert [(issue.stable_id, issue.field_name) for issue in required.issues] == [
        (uid(1), "date_raw")
    ]
    assert fiscal.snapshot is b.read_result.snapshot and fiscal.observed_years == (
        1403,
        1404,
    )
    assert fiscal.undated_row_count == 1
    assert b.key.fiscal_year == 1405
    empty = read_parts(
        tmp_path, identified_parts(value=value_b, raw=raw_parts(rows_per_sheet=0))
    )
    resolution = resolve_source_binding(empty.key, registry)
    assert resolution.prior_registry is prior_b
    plan = plan_source_changes(empty.read_result.snapshot, prior_b)
    assert [
        (item.stable_id, item.action.value, item.planned_revision)
        for item in plan.items
    ] == [(uid(i), "void", 8) for i in (1, 2, 100001, 200001, 300001)]
    assert not set(prior_a.identities) & {item.stable_id for item in plan.items}
    unknown = read_parts(
        tmp_path, identified_parts(value=f"xlsx-source-identity.v1|{uid(997)}|1405")
    )
    calls = []

    def forbidden_planner(*args: Any) -> Any:
        calls.append(args)
        raise AssertionError("Planner must not run")

    with monkeypatch.context() as patch:
        patch.setattr(planner, "plan_source_changes", forbidden_planner)
        for result, expected in (
            (a, SourceBindingDisposition.ARCHIVED),
            (unknown, SourceBindingDisposition.UNREGISTERED),
        ):
            found = resolve_source_binding(result.key, registry)
            assert found.disposition is expected and found.prior_registry is None
            if found.disposition is SourceBindingDisposition.ACTIVE:
                assert found.prior_registry is not None
                planner.plan_source_changes(
                    result.read_result.snapshot, found.prior_registry
                )
    assert not calls
    mismatch = read_parts(
        tmp_path, identified_parts(value=f"xlsx-source-identity.v1|{uid(998)}|1406")
    )
    with pytest.raises(SourceBindingInputError):
        resolve_source_binding(mismatch.key, registry)
    assert registry.records and a.key.fiscal_year == 1404


def test_xi15_delegated_policy_and_missing_source(tmp_path: Path) -> None:
    source, leases, _ = paths(tmp_path)
    with pytest.raises(XlsxSourcePolicyError):
        read_identified_xlsx_source(
            source, snapshot_root=leases, observation_interval_seconds=True
        )
    with pytest.raises(XlsxSourceNotReadyError):
        read_identified_xlsx_source(
            tmp_path / "missing.xlsx",
            snapshot_root=leases,
            observation_interval_seconds=0.001,
        )
    assert list(leases.iterdir()) == []


def test_xi15_source_read_only_and_forbidden_downstream_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, leases, before = paths(tmp_path)
    unrelated = tmp_path / "SYNTHETIC-unrelated"
    unrelated.write_bytes(b"SYNTHETIC-PRESERVE")

    class Forbidden(RuntimeError):
        pass

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise Forbidden("source identity guard")

    original_open = io.open

    def protected_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if (
            isinstance(file, (str, Path))
            and Path(file) in {source, unrelated}
            and any(flag in mode for flag in "wax+")
        ):
            raise Forbidden("source identity guard")
        return original_open(file, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        import accounting_contracts.source_binding as binding
        import accounting_contracts.source_fiscal_evidence as fiscal
        import accounting_contracts.source_requiredness as requiredness
        import accounting_local_agent.source_watch_runtime as runtime

        patch.setattr(io, "open", protected_open)
        patch.setattr(socket, "socket", forbidden)
        patch.setattr(socket, "create_connection", forbidden)
        patch.setattr(zipfile.ZipFile, "extract", forbidden)
        patch.setattr(zipfile.ZipFile, "extractall", forbidden)
        patch.setattr(planner, "plan_source_changes", forbidden)
        patch.setattr(binding, "resolve_source_binding", forbidden)
        patch.setattr(requiredness, "evaluate_source_requiredness", forbidden)
        patch.setattr(fiscal, "evaluate_source_fiscal_evidence", forbidden)
        patch.setattr(runtime.SourceWatchRuntime, "run", forbidden)
        if hasattr(uuid, "uuid7"):
            patch.setattr(uuid, "uuid7", forbidden)
        # Source-key generation is forbidden even though WP-06 still creates
        # independent private lease identifiers through its own uuid module.
        patch.setattr(
            identity,
            "uuid",
            SimpleNamespace(
                UUID=uuid.UUID, uuid1=forbidden, uuid4=forbidden, uuid7=forbidden
            ),
        )
        with pytest.raises(Forbidden, match="source identity guard"):
            source.write_bytes(b"SYNTHETIC canary")
        with pytest.raises(Forbidden, match="source identity guard"):
            socket.create_connection(("example.invalid", 443))
        result = read_identified_xlsx_source(
            source, snapshot_root=leases, observation_interval_seconds=0.001
        )
        assert result.key == SourceBindingKey(uid(999), 1405)
    assert (
        source.read_bytes() == before
        and unrelated.read_bytes() == b"SYNTHETIC-PRESERVE"
    )
    assert list(leases.iterdir()) == []
