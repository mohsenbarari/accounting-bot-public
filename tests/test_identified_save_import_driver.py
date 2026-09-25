"""WP-16: exact API, independent scheduling oracle, and real synthetic XLSX evidence."""

from __future__ import annotations

import ast
import builtins
import inspect
import json
import os
import subprocess
import sys
import threading
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import accounting_local_agent as public
import accounting_local_agent.save_import_coordinator as driver
import accounting_local_agent.xlsx_snapshot_acquisition as acquisition
import accounting_local_agent.xlsx_source_identity as identity
import pytest
from accounting_local_agent import (
    SaveCoordinatorState as State,
)
from accounting_local_agent import (
    SaveEventKind,
    SaveImportCoordinator,
    StableXlsxSnapshot,
    XlsxSnapshotCleanupError,
    XlsxSnapshotIntegrityError,
    XlsxSnapshotStorageError,
    XlsxSourceIdentityError,
    XlsxSourceIdentityReason,
    XlsxSourceNotReadyError,
    XlsxSourcePolicyError,
    XlsxSourceReadError,
    read_due_identified_source,
)
from accounting_local_agent import (
    SourceReadOutcome as Outcome,
)
from hypothesis import given, settings
from hypothesis import strategies as st
from test_save_import_coordinator import FakeClock
from test_xlsx_source_identity_lifecycle import replace_zip
from xlsx_source_identity_fixtures import (
    SHEETS,
    VALUE,
    identified_parts,
    raw_parts,
    uid,
    zipped,
)

DEBOUNCE = 2_000_000_000
PREVIOUS_EXPORTS = [
    "DEFAULT_COPY_CHUNK_SIZE",
    "IdentifiedXlsxSource",
    "SAVE_DEBOUNCE_NS",
    "SAVE_IMPORT_COORDINATOR_VERSION",
    "SOURCE_WATCH_RUNTIME_VERSION",
    "SaveCoordinatorError",
    "SaveCoordinatorPolicyError",
    "SaveCoordinatorState",
    "SaveCoordinatorStateError",
    "SaveCoordinatorView",
    "SaveEventKind",
    "SaveImportCoordinator",
    "SourceReadAttempt",
    "SourceReadOutcome",
    "SourceRowLocation",
    "SourceWatchRuntime",
    "SourceWatchRuntimeError",
    "SourceWatchRuntimeReason",
    "SourceWatchRuntimeState",
    "SourceWatchRuntimeView",
    "StableXlsxSnapshot",
    "XLSX_SNAPSHOT_ACQUISITION_VERSION",
    "XLSX_SOURCE_IDENTITY_MAX_METADATA_BYTES",
    "XLSX_SOURCE_IDENTITY_PROPERTY_NAME",
    "XLSX_SOURCE_IDENTITY_VERSION",
    "XLSX_SOURCE_READER_VERSION",
    "XlsxCellError",
    "XlsxFormulaCoverageError",
    "XlsxHeaderError",
    "XlsxIdentityError",
    "XlsxPackageError",
    "XlsxSnapshotAcquisitionError",
    "XlsxSnapshotAcquisitionReason",
    "XlsxSnapshotCleanupError",
    "XlsxSnapshotIntegrityError",
    "XlsxSnapshotStorageError",
    "XlsxSourceIdentityError",
    "XlsxSourceIdentityReason",
    "XlsxSourceNotReadyError",
    "XlsxSourcePolicyError",
    "XlsxSourceReadError",
    "XlsxSourceReadResult",
    "XlsxStructureError",
    "open_stable_xlsx_snapshot",
    "read_due_source",
    "read_identified_xlsx_source",
    "read_xlsx_source_snapshot",
]


def coordinator(
    tmp_path: Path, *, due: bool = True
) -> tuple[SaveImportCoordinator, FakeClock, Path]:
    clock = FakeClock()
    target = tmp_path / "sources" / "SYNTHETIC-source.xlsx"
    target.parent.mkdir(exist_ok=True)
    result = SaveImportCoordinator(target, _time_source=clock)
    if due:
        assert result.notify(SaveEventKind.MODIFIED, target)
        clock.advance_ns(DEBOUNCE)
    return result, clock, target


def call(c: SaveImportCoordinator, root: Path) -> Any:
    return read_due_identified_source(
        c, snapshot_root=root, observation_interval_seconds=0.001
    )


def fail(error: BaseException) -> Any:
    def raise_error(*args: Any, **kwargs: Any) -> Any:
        raise error

    return raise_error


def _contains_exception(error: BaseException, expected: BaseException) -> bool:
    """Inspect a typed boundary failure without equating shared causes."""
    if error is expected:
        return True
    if isinstance(error, BaseExceptionGroup) and any(
        _contains_exception(member, expected) for member in error.exceptions
    ):
        return True
    return error.__cause__ is not None and _contains_exception(
        error.__cause__, expected
    )


class TestIdentifiedSaveImportDriverApi:
    def test_id01_public_api_and_import_inertness(self, tmp_path: Path) -> None:
        assert (
            public.IDENTIFIED_SAVE_IMPORT_DRIVER_VERSION
            == "identified-save-import-driver.v1"
        )
        assert public.read_due_identified_source is driver.read_due_identified_source
        assert {
            "IDENTIFIED_SAVE_IMPORT_DRIVER_VERSION",
            "read_due_identified_source",
        } <= set(public.__all__)
        assert set(public.__all__) == set(PREVIOUS_EXPORTS) | {
            "IDENTIFIED_SAVE_IMPORT_DRIVER_VERSION",
            "read_due_identified_source",
            "IDENTIFIED_SOURCE_WATCH_RUNTIME_VERSION",
        }
        sig = inspect.signature(read_due_identified_source)
        assert list(sig.parameters) == [
            "coordinator",
            "snapshot_root",
            "observation_interval_seconds",
        ]
        assert (
            sig.parameters["coordinator"].kind
            is inspect.Parameter.POSITIONAL_OR_KEYWORD
        )
        for name in ("snapshot_root", "observation_interval_seconds"):
            assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
            assert sig.parameters[name].default is inspect.Parameter.empty
        assert public.SAVE_IMPORT_COORDINATOR_VERSION == "save-import-coordinator.v1"
        assert public.SOURCE_WATCH_RUNTIME_VERSION == "source-watch-runtime.v1"
        assert public.XLSX_SOURCE_IDENTITY_VERSION == "xlsx-source-identity.v1"
        assert list(inspect.signature(driver.read_due_source).parameters) == list(
            sig.parameters
        )
        # The existing loader probe is parameterized in a fresh interpreter;
        # its canary verifies that the target is executed under the side-effect guard.
        probe = """
import importlib
import sys
import xlsx_source_identity_import_probe as p
p.TARGET = 'accounting_local_agent.save_import_coordinator'
p.PUBLIC_NAMES = (
    'IDENTIFIED_SAVE_IMPORT_DRIVER_VERSION', 'read_due_identified_source',
)
assert 'accounting_local_agent' not in sys.modules
assert p.TARGET not in sys.modules
sys.meta_path.insert(0, p.ProbeFinder())
try:
    module = importlib.import_module(p.TARGET)
except p.ForbiddenSideEffect:
    assert p.TARGET not in sys.modules
    assert 'accounting_local_agent' not in sys.modules
    print('IMPORT_REJECTED_BY_GUARD', flush=True)
    raise SystemExit(73)
package = sys.modules['accounting_local_agent']
for name in p.PUBLIC_NAMES:
    assert getattr(package, name) is getattr(module, name)
assert module.IDENTIFIED_SAVE_IMPORT_DRIVER_VERSION == (
    'identified-save-import-driver.v1'
)
print('PROBE_OK', flush=True)
"""
        env = dict(
            os.environ,
            PYTHONPATH=os.pathsep.join(
                [str(Path(__file__).parent), os.environ.get("PYTHONPATH", "")]
            ),
        )
        for mode, code in (("normal", 0), ("inject_write", 73)):
            canary = tmp_path / "SYNTHETIC-canary"
            result = subprocess.run(
                [sys.executable, "-c", probe, mode, str(canary)],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == code, result.stdout + result.stderr
            assert "IMPORT_ENTERED" in result.stdout
            assert (
                "PROBE_OK" if code == 0 else "IMPORT_REJECTED_BY_GUARD"
            ) in result.stdout
            assert not canary.exists()

    @pytest.mark.parametrize(
        "kind", ["none", "foreign", "subclass", "not-due", "take-failure"]
    )
    def test_id02_invalid_and_not_due_admission(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        c, _, _ = coordinator(tmp_path, due=False)
        reader = Mock(side_effect=AssertionError("unexpected I/O"))
        monkeypatch.setattr(driver, "read_identified_xlsx_source", reader)

        class Foreign:
            def __getattribute__(self, name: str) -> Any:
                raise AssertionError("foreign descriptor accessed")

        class Subclass(SaveImportCoordinator):
            def take_due(self) -> Any:
                raise AssertionError("subclass accessed")

        if kind in {"none", "foreign", "subclass"}:
            bad: Any = {
                "none": None,
                "foreign": Foreign(),
                "subclass": object.__new__(Subclass),
            }[kind]
            with pytest.raises(
                TypeError, match=r"^Invalid identified save import driver input\.$"
            ):
                call(bad, tmp_path)
        elif kind == "take-failure":
            error = RuntimeError("synthetic admission failure")
            finish = Mock()
            monkeypatch.setattr(SaveImportCoordinator, "take_due", fail(error))
            monkeypatch.setattr(SaveImportCoordinator, "finish", finish)
            with pytest.raises(RuntimeError) as caught:
                call(c, tmp_path)
            assert caught.value is error
            finish.assert_not_called()
        else:
            before = c.view()
            assert call(c, tmp_path) is None
            assert c.view() == before
        reader.assert_not_called()


class TestIdentifiedSaveImportDriverLifecycle:
    def test_id03_successful_call_provenance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        c, _, target = coordinator(tmp_path)
        result = object()
        reader = Mock(return_value=result)
        original = SaveImportCoordinator.take_due
        take = Mock(side_effect=lambda obj: original(obj))
        monkeypatch.setattr(SaveImportCoordinator, "take_due", lambda obj: take(obj))
        monkeypatch.setattr(driver, "read_identified_xlsx_source", reader)
        root = tmp_path / "snapshot-policy"
        assert call(c, root) is result
        take.assert_called_once_with(c)
        reader.assert_called_once_with(
            target, snapshot_root=root, observation_interval_seconds=0.001
        )
        assert reader.call_args.kwargs["snapshot_root"] is root
        assert c.view().state is State.IDLE

    @pytest.mark.parametrize(
        "stage",
        ["member-close", "zip-close", "lease-verify", "lease-cleanup", "finish"],
    )
    @pytest.mark.parametrize(
        "broken", [False, True], ids=["ordered-success", "boundary-failure"]
    )
    def test_id04_success_boundary_ordering(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, broken: bool
    ) -> None:
        c, _, source = coordinator(tmp_path)
        source.write_bytes(zipped(identified_parts()))
        leases = tmp_path / "leases"
        leases.mkdir()
        entered, release = threading.Event(), threading.Event()
        results: list[Any] = []
        errors: list[BaseException] = []
        finishes: list[Outcome] = []
        boundary: BaseException = (
            OSError("synthetic cleanup failure")
            if stage == "lease-cleanup"
            else RuntimeError("synthetic boundary failure")
        )
        stopped = False

        def gate() -> None:
            nonlocal stopped
            if stopped:
                return
            stopped = True
            entered.set()
            assert release.wait(5.0)
            if broken:
                raise boundary

        class TrackedZip(zipfile.ZipFile):
            def open(self, *args: Any, **kwargs: Any) -> Any:
                member = super().open(*args, **kwargs)
                close = member.close

                def closed() -> None:
                    close()
                    if stage == "member-close":
                        gate()

                monkeypatch.setattr(member, "close", closed)
                return member

            def close(self) -> None:
                active = self.fp is not None
                super().close()
                if active and stage == "zip-close":
                    gate()

        real_verify = acquisition._stream_hash_leased_snapshot

        def verify(*args: Any, **kwargs: Any) -> tuple[str, int]:
            if stage == "lease-verify":
                gate()
            return real_verify(*args, **kwargs)

        real_rmdir = Path.rmdir

        def rmdir(path: Path) -> None:
            if stage == "lease-cleanup" and path.name.startswith(".qdir-"):
                gate()
            real_rmdir(path)

        original_finish = SaveImportCoordinator.finish

        def finish(obj: SaveImportCoordinator, attempt: Any, outcome: Outcome) -> None:
            if outcome is Outcome.SUCCESS:
                assert not list(leases.iterdir())
                if stage == "finish":
                    gate()
            finishes.append(outcome)
            original_finish(obj, attempt, outcome)

        replace_zip(monkeypatch, TrackedZip)
        monkeypatch.setattr(acquisition, "_stream_hash_leased_snapshot", verify)
        monkeypatch.setattr(Path, "rmdir", rmdir)
        monkeypatch.setattr(SaveImportCoordinator, "finish", finish)

        def run() -> None:
            try:
                results.append(call(c, leases))
            except BaseException as exc:
                errors.append(exc)

        runner = threading.Thread(target=run)
        runner.start()
        try:
            assert entered.wait(5.0)
            assert c.view().state is State.RUNNING
            assert results == [] and finishes == []
        finally:
            release.set()
            runner.join(5.0)
        assert not runner.is_alive()
        if broken and stage == "lease-cleanup":
            # A real rmdir failure retains only this owned, empty test lease.
            remaining = list(leases.iterdir())
            assert len(remaining) == 1 and remaining[0].name.startswith("acq-")
            assert not list(remaining[0].iterdir())
            remaining[0].rmdir()
        assert not list(leases.iterdir())
        if broken:
            assert not results and len(errors) == 1
            if stage in {"lease-verify", "lease-cleanup"}:
                expected_error = (
                    XlsxSnapshotIntegrityError
                    if stage == "lease-verify"
                    else XlsxSnapshotCleanupError
                )
                assert isinstance(errors[0], expected_error)
                assert _contains_exception(errors[0], boundary)
            else:
                assert errors == [boundary]
            assert c.view().state is State.FAULTED
            assert finishes == ([] if stage == "finish" else [Outcome.FAULTED])
        else:
            assert len(results) == 1 and not errors
            assert finishes == [Outcome.SUCCESS] and c.view().state is State.IDLE

    @pytest.mark.parametrize("boundary", ["lease-verify", "lease-cleanup"])
    def test_id04_real_lease_failure_precedes_finish_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
    ) -> None:
        c, _, source = coordinator(tmp_path)
        source.write_bytes(zipped(identified_parts()))
        leases = tmp_path / "leases"
        leases.mkdir()
        lease_error = OSError("synthetic lease cleanup failure")
        finish_error = RuntimeError("synthetic coordinator finish failure")
        finish_outcomes: list[Outcome] = []

        def verify(*args: Any, **kwargs: Any) -> tuple[str, int]:
            raise lease_error

        real_rmdir = Path.rmdir

        def rmdir(path: Path) -> None:
            if path.name.startswith(".qdir-"):
                raise lease_error
            real_rmdir(path)

        real_finish = SaveImportCoordinator.finish

        def finish(obj: SaveImportCoordinator, attempt: Any, outcome: Outcome) -> None:
            finish_outcomes.append(outcome)
            if outcome is Outcome.FAULTED:
                raise finish_error
            real_finish(obj, attempt, outcome)

        if boundary == "lease-verify":
            monkeypatch.setattr(acquisition, "_stream_hash_leased_snapshot", verify)
        else:
            monkeypatch.setattr(Path, "rmdir", rmdir)
        monkeypatch.setattr(SaveImportCoordinator, "finish", finish)

        with pytest.raises(ExceptionGroup) as caught:
            call(c, leases)
        primary, bookkeeping = caught.value.exceptions
        assert isinstance(
            primary,
            XlsxSnapshotIntegrityError
            if boundary == "lease-verify"
            else XlsxSnapshotCleanupError,
        )
        assert _contains_exception(primary, lease_error)
        assert bookkeeping is finish_error
        assert finish_outcomes == [Outcome.FAULTED]
        assert c.view().state is State.FAULTED
        if boundary == "lease-cleanup":
            remaining = list(leases.iterdir())
            assert len(remaining) == 1 and remaining[0].name.startswith("acq-")
            assert not list(remaining[0].iterdir())
            remaining[0].rmdir()
        assert not list(leases.iterdir())


class TestIdentifiedSaveImportDriverFailures:
    @pytest.mark.parametrize(
        "boundary", ["before_observation", "during_copy_chunk", "before_zip_validation"]
    )
    def test_id05_real_acquisition_boundaries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
    ) -> None:
        c, clock, target = coordinator(tmp_path)
        content = zipped(identified_parts())
        target.write_bytes(content)
        leases = tmp_path / "leases"
        leases.mkdir()
        error = XlsxSourceNotReadyError()
        hits = []
        acquire = public.open_stable_xlsx_snapshot

        def hook(stage: str, *args: Any) -> None:
            if stage == boundary:
                hits.append(stage)
                raise error

        @contextmanager
        def lease(*args: Any, **kwargs: Any) -> Iterator[StableXlsxSnapshot]:
            with acquire(*args, **kwargs, _fault_hook=hook) as snapshot:
                yield snapshot

        monkeypatch.setattr(identity, "open_stable_xlsx_snapshot", lease)
        with pytest.raises(XlsxSourceNotReadyError) as caught:
            call(c, leases)
        assert caught.value is error and hits == [boundary]
        assert c.view().state is State.WAITING
        assert c.view().next_due_ns == clock() + DEBOUNCE
        assert not list(leases.iterdir()) and target.read_bytes() == content
        monkeypatch.setattr(identity, "open_stable_xlsx_snapshot", acquire)
        clock.advance_ns(DEBOUNCE)
        assert call(c, leases).file_sha256 == sha256(content).hexdigest()
        assert not list(leases.iterdir())

    @pytest.mark.parametrize("boundary", ["observation", "copy", "container"])
    @pytest.mark.parametrize("followup", [False, True])
    def test_id05_source_not_ready_transition(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        boundary: str,
        followup: bool,
    ) -> None:
        c, clock, target = coordinator(tmp_path)
        error = XlsxSourceNotReadyError()
        # WP-06's boundary classification is tested upstream; all direct
        # not-ready boundary errors must preserve the same scheduler contract.
        error.__cause__ = OSError("synthetic " + boundary)

        def read(*args: Any, **kwargs: Any) -> Any:
            clock.advance_ns(123)
            if followup:
                assert c.notify(SaveEventKind.MODIFIED, target)
            raise error

        monkeypatch.setattr(driver, "read_identified_xlsx_source", read)
        with pytest.raises(XlsxSourceNotReadyError) as caught:
            call(c, tmp_path)
        assert caught.value is error
        assert c.view().state is State.WAITING
        assert c.view().next_due_ns == clock() + DEBOUNCE
        replacement = Mock(return_value=object())
        monkeypatch.setattr(driver, "read_identified_xlsx_source", replacement)
        clock.advance_ns(DEBOUNCE - 1)
        assert call(c, tmp_path) is None
        replacement.assert_not_called()
        clock.advance_ns(1)
        assert call(c, tmp_path) is replacement.return_value
        replacement.assert_called_once()

    @pytest.mark.parametrize("identity_error", [False, True], ids=["raw", "marker"])
    @pytest.mark.parametrize("followup", [False, True])
    def test_id06_reader_rejection_transition(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        identity_error: bool,
        followup: bool,
    ) -> None:
        c, clock, target = coordinator(tmp_path)
        error = (
            XlsxSourceIdentityError(XlsxSourceIdentityReason.MISSING_MARKER)
            if identity_error
            else XlsxSourceReadError("synthetic rejection")
        )

        def read(*args: Any, **kwargs: Any) -> Any:
            if followup:
                assert c.notify(SaveEventKind.MODIFIED, target)
            raise error

        monkeypatch.setattr(driver, "read_identified_xlsx_source", read)
        with pytest.raises(XlsxSourceReadError) as caught:
            call(c, tmp_path)
        assert caught.value is error
        assert c.view().state is (State.WAITING if followup else State.IDLE)
        assert c.view().next_due_ns == (clock() + DEBOUNCE if followup else None)
        assert not c.notify(SaveEventKind.MODIFIED, target.with_name("unrelated.xlsx"))
        replacement = Mock(return_value=object())
        monkeypatch.setattr(driver, "read_identified_xlsx_source", replacement)
        clock.advance_ns(DEBOUNCE)
        if not followup:
            assert call(c, tmp_path) is None
            replacement.assert_not_called()
            assert c.notify(SaveEventKind.MODIFIED, target)
            clock.advance_ns(DEBOUNCE)
        assert call(c, tmp_path) is replacement.return_value
        replacement.assert_called_once()

    @pytest.mark.parametrize(
        "error_type",
        [
            XlsxSourcePolicyError,
            XlsxSnapshotStorageError,
            XlsxSnapshotIntegrityError,
            XlsxSnapshotCleanupError,
            RuntimeError,
        ],
    )
    def test_id07_fatal_failure_transition(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        error_type: type[Exception],
    ) -> None:
        c, clock, target = coordinator(tmp_path)
        error = error_type()
        monkeypatch.setattr(driver, "read_identified_xlsx_source", fail(error))
        with pytest.raises(error_type) as caught:
            call(c, tmp_path)
        assert caught.value is error
        assert c.view().state is State.FAULTED and c.view().next_due_ns is None
        c.notify(SaveEventKind.MODIFIED, target)
        clock.advance_ns(DEBOUNCE)
        assert call(c, tmp_path) is None
        c.resume_after_fault()
        assert c.view().state is State.WAITING
        assert c.view().next_due_ns == clock() + DEBOUNCE

    @pytest.mark.parametrize(
        "primary_kind",
        ["none", "ordinary", "group", "keyboard", "system", "base-group"],
    )
    @pytest.mark.parametrize(
        "finish_failure,guard_failure", [(False, False), (True, False), (True, True)]
    )
    def test_id08_ordered_failure_preservation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        primary_kind: str,
        finish_failure: bool,
        guard_failure: bool,
    ) -> None:
        c, _, _ = coordinator(tmp_path)
        cause = OSError("shared synthetic cause")
        nested = [
            XlsxSourceReadError("synthetic rejection"),
            XlsxSnapshotCleanupError(),
        ]
        primary: BaseException | None = {
            "none": None,
            "ordinary": RuntimeError(),
            "group": ExceptionGroup("read then cleanup", nested),
            "keyboard": KeyboardInterrupt(),
            "system": SystemExit(),
            "base-group": BaseExceptionGroup(
                "cancel then cleanup", [KeyboardInterrupt(), nested[1]]
            ),
        }[primary_kind]
        later, guard = RuntimeError("finish"), RuntimeError("guard")
        for error in (primary, later, guard):
            if error is not None:
                error.__cause__ = cause
        monkeypatch.setattr(
            driver,
            "read_identified_xlsx_source",
            fail(primary) if primary is not None else Mock(return_value=object()),
        )
        if finish_failure:
            monkeypatch.setattr(SaveImportCoordinator, "finish", fail(later))
        if guard_failure:
            original_guard = driver._guarded_force_fault

            def broken_guard(*args: Any) -> None:
                original_guard(*args)
                raise guard

            monkeypatch.setattr(driver, "_guarded_force_fault", broken_guard)
        expected = [
            e
            for e in (
                primary,
                later if finish_failure else None,
                guard if guard_failure else None,
            )
            if e is not None
        ]
        if not expected:
            assert call(c, tmp_path) is not None
            return
        with pytest.raises(BaseException) as caught:
            call(c, tmp_path)
        if len(expected) == 1:
            assert caught.value is expected[0]
        else:
            assert isinstance(caught.value, BaseExceptionGroup)
            assert caught.value.exceptions == tuple(expected)
            assert type(caught.value) is (
                ExceptionGroup
                if all(isinstance(e, Exception) for e in expected)
                else BaseExceptionGroup
            )
        assert all(e.__cause__ is cause for e in expected)
        assert c.view().state is State.FAULTED


class TestIdentifiedSaveImportDriverConcurrency:
    def test_id09_concurrent_attempt_ownership(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        c, clock, target = coordinator(tmp_path)
        barrier = threading.Barrier(3)
        reading, release, loser_done = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        results: list[Any] = []
        errors: list[BaseException] = []
        returned = object()
        calls: list[Path] = []

        def read(path: Path, **kwargs: Any) -> Any:
            calls.append(path)
            reading.set()
            assert release.wait(5.0)
            return returned

        monkeypatch.setattr(driver, "read_identified_xlsx_source", read)

        def run() -> None:
            try:
                barrier.wait(5.0)
                result = call(c, tmp_path)
                results.append(result)
                if result is None:
                    loser_done.set()
            except BaseException as exc:
                errors.append(exc)

        runners = [threading.Thread(target=run) for _ in range(2)]
        for runner in runners:
            runner.start()
        try:
            barrier.wait(5.0)
            assert reading.wait(5.0) and loser_done.wait(5.0)
            assert c.view().state is State.RUNNING
            assert calls == [target] and results == [None]
            for _ in range(2000):
                assert c.notify(SaveEventKind.MODIFIED, target)
        finally:
            release.set()
            barrier.abort()
            for runner in runners:
                runner.join(5.0)
        assert not errors and all(not runner.is_alive() for runner in runners)
        assert results == [None, returned]
        assert c.view().state is State.WAITING
        clock.advance_ns(DEBOUNCE)
        assert call(c, tmp_path) is returned
        assert call(c, tmp_path) is None and calls == [target, target]


# This table is a literal oracle for the fixture XML, independent of the WP-05
# decoder and of the product's canonical hashing helpers.  The final element of
# each field tuple is the ADR-0006 canonical value, not a second decoded value.
ID10_LITERAL_ROWS = (
    (
        "خرید-فروش",
        42,
        (
            ("date_raw", "1403/05/15", "jalali_date", "1403-05-15"),
            ("party_name_raw", "بازرگانی احمدی", "raw_text", "بازرگانی احمدی"),
            ("transaction_type_raw", "خرید", "raw_text", "خرید"),
            ("item_name_raw", "طلای آبشده", "raw_text", "طلای آبشده"),
            ("quantity_raw", "12.34", "decimal", "12.34"),
            ("unit_price_toman_raw", "1500000", "integer_toman", "1500000"),
            ("discount_toman_raw", "0", "integer_toman", "0"),
            ("notes_raw", "توضیحات فاکتور", "raw_text", "توضیحات فاکتور"),
        ),
    ),
    (
        "دریافت-پرداخت",
        100_042,
        (
            ("date_raw", "1403/01/01", "jalali_date", "1403-01-01"),
            ("party_name_raw", "همکار نمونه", "raw_text", "همکار نمونه"),
            ("entry_type_raw", "RS", "raw_text", "RS"),
            ("amount_toman_raw", "50000000", "integer_toman", "50000000"),
            ("notes_raw", "تسویه حساب", "raw_text", "تسویه حساب"),
            ("account_code_raw", "101", "raw_text", "101"),
            ("customer_flag_raw", "1", "raw_text", "1"),
        ),
    ),
    (
        "ورود-خروج",
        200_042,
        (
            ("date_raw", "1403/12/29", "jalali_date", "1403-12-29"),
            ("party_name_raw", "کارگاه زرگری", "raw_text", "کارگاه زرگری"),
            ("movement_type_raw", "ورود", "raw_text", "ورود"),
            ("item_name_raw", "شمش طلا", "raw_text", "شمش طلا"),
            ("quantity_raw", "100.5", "decimal", "100.5"),
            ("purity_raw", "750", "decimal", "750"),
            ("notes_raw", "تحویل شمش", "raw_text", "تحویل شمش"),
            ("customer_flag_raw", "1", "raw_text", "1"),
        ),
    ),
    (
        "لیست کسبه",
        300_042,
        (
            ("party_name_raw", "فروشگاه نمونه", "raw_text", "فروشگاه نمونه"),
            (
                "phone_number_raw",
                "SYNTHETIC-PHONE-001",
                "raw_text",
                "SYNTHETIC-PHONE-001",
            ),
        ),
    ),
)


def _id10_adr_digest(payload: list[Any]) -> str:
    """Independent ADR-0006 JSON wire bytes for this fixed synthetic fixture."""
    return sha256(
        json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


class TestIdentifiedSaveImportDriverIntegration:
    @pytest.mark.parametrize("empty", [False, True])
    def test_id10_real_synthetic_identity_stack(
        self, tmp_path: Path, empty: bool
    ) -> None:
        c, _, source = coordinator(tmp_path)
        raw = raw_parts(seed=42, rows_per_sheet=0 if empty else 1, extra_buy=False)
        content = zipped(identified_parts(raw=raw))
        source.write_bytes(content)
        leases = tmp_path / "leases"
        leases.mkdir()
        result = call(c, leases)
        assert result.key.source_id == uid(999) and result.key.fiscal_year == 1405
        assert result.file_sha256 == sha256(
            content
        ).hexdigest() and result.byte_count == len(content)
        assert tuple(sheet for sheet, _, _ in ID10_LITERAL_ROWS) == SHEETS
        snapshot = result.read_result.snapshot
        rows = snapshot.all_rows_by_id
        expected_ids = (
            set() if empty else {uid(index) for _, index, _ in ID10_LITERAL_ROWS}
        )
        assert set(rows) == expected_ids
        assert set(result.read_result.locations_by_uuid) == expected_ids
        assert set(snapshot.sheets) == set(SHEETS)
        assert snapshot.total_row_count == (0 if empty else 4)
        for sheet_name, index, fields in ID10_LITERAL_ROWS:
            expected_pairs: list[list[str]] = []
            sheet = snapshot.sheets[sheet_name]
            assert len(sheet.rows) == (0 if empty else 1)
            if not empty:
                record_id = uid(index)
                expected_raw = {name: raw_value for name, raw_value, _, _ in fields}
                expected_hash = _id10_adr_digest(
                    [
                        "source-hash.v1",
                        "raw-source-contract.v1",
                        sheet_name,
                        [[name, tag, canonical] for name, _, tag, canonical in fields],
                    ]
                )
                actual = rows[record_id]
                assert sheet.rows[0] is actual
                assert actual.stable_id == record_id
                assert actual.canonical_uuid == str(record_id)
                assert actual.sheet_name == sheet_name
                assert list(actual.raw_values) == list(expected_raw)
                assert dict(actual.raw_values) == expected_raw
                assert all(
                    type(actual.raw_values[name]) is str for name in expected_raw
                )
                assert actual.source_hash == expected_hash
                location = result.read_result.locations_by_uuid[record_id]
                assert location.sheet_name == sheet_name
                assert location.physical_row_number == 2
                expected_pairs.append([str(record_id), expected_hash])
            assert sheet.sheet_snapshot_hash == _id10_adr_digest(
                [
                    "sheet-snapshot-hash.v1",
                    "raw-source-contract.v1",
                    sheet_name,
                    expected_pairs,
                ]
            )
        assert source.read_bytes() == content and not list(leases.iterdir())
        assert c.view().state is State.IDLE

    def test_id11_single_generation_binding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        c, clock, source = coordinator(tmp_path)
        first = zipped(identified_parts(raw=raw_parts(seed=1)))
        second = zipped(
            identified_parts(
                value=VALUE[:-4] + "1406", raw=raw_parts(seed=400, edit=True)
            )
        )
        source.write_bytes(first)
        leases = tmp_path / "leases"
        leases.mkdir()
        acquire = public.open_stable_xlsx_snapshot

        @contextmanager
        def lease(*args: Any, **kwargs: Any) -> Iterator[StableXlsxSnapshot]:
            with acquire(*args, **kwargs) as snap:
                replacement = tmp_path / "replacement.xlsx"
                replacement.write_bytes(second)
                os.replace(replacement, source)
                yield snap

        monkeypatch.setattr(identity, "open_stable_xlsx_snapshot", lease)
        a = call(c, leases)
        assert a.key.fiscal_year == 1405 and a.file_sha256 == sha256(first).hexdigest()
        assert (
            uid(1) in a.read_result.snapshot.all_rows_by_id
            and uid(400) not in a.read_result.snapshot.all_rows_by_id
        )
        assert not list(leases.iterdir())
        assert c.notify(SaveEventKind.MODIFIED, source)
        clock.advance_ns(DEBOUNCE)
        b = call(c, leases)
        assert (
            b is not a
            and b.key.fiscal_year == 1406
            and b.file_sha256 == sha256(second).hexdigest()
        )
        assert (
            uid(400) in b.read_result.snapshot.all_rows_by_id
            and uid(1) not in b.read_result.snapshot.all_rows_by_id
        )
        assert b.byte_count == len(second) and a.byte_count == len(first)
        assert source.read_bytes() == second and not list(leases.iterdir())

    @pytest.mark.parametrize(
        "boundary", ["observation", "copy", "source-reverification"]
    )
    def test_id11_replacement_at_acquisition_boundary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
    ) -> None:
        c, clock, source = coordinator(tmp_path)
        first = zipped(identified_parts(raw=raw_parts(seed=1)))
        second = zipped(
            identified_parts(
                value=VALUE[:-4] + "1406", raw=raw_parts(seed=400, edit=True)
            )
        )
        source.write_bytes(first)
        replacement = tmp_path / "SYNTHETIC-replacement.xlsx"
        replacement.write_bytes(second)
        leases = tmp_path / "leases"
        leases.mkdir()
        replaced = threading.Event()

        def replace_live_file() -> None:
            assert not replaced.is_set()
            if boundary == "copy":
                # Windows may prevent renaming a path held open by the copy
                # handle.  In-place mutation is the accepted native race for
                # this boundary and still changes the actual source bytes.
                source.write_bytes(second)
                replacement.unlink()
            else:
                os.replace(replacement, source)
            replaced.set()

        if boundary == "observation":
            real_observe = acquisition._get_path_observation
            observations = 0

            def observe(path: Path) -> Any:
                nonlocal observations
                if path == source:
                    observations += 1
                    if observations == 2:
                        replace_live_file()
                return real_observe(path)

            monkeypatch.setattr(acquisition, "_get_path_observation", observe)
        elif boundary == "copy":
            real_fsync = os.fsync

            def fsync(fd: int) -> None:
                real_fsync(fd)
                if not replaced.is_set():
                    # This fsync is inside the open source/destination copy scope.
                    replace_live_file()

            monkeypatch.setattr(os, "fsync", fsync)
        else:
            real_reverify = acquisition._stream_hash_source

            def reverify(*args: Any, **kwargs: Any) -> tuple[str, int]:
                if not replaced.is_set():
                    replace_live_file()
                return real_reverify(*args, **kwargs)

            monkeypatch.setattr(acquisition, "_stream_hash_source", reverify)

        with pytest.raises(XlsxSourceNotReadyError):
            call(c, leases)
        assert replaced.is_set()
        assert source.read_bytes() == second
        assert not list(leases.iterdir())
        assert c.view().state is State.WAITING

        clock.advance_ns(DEBOUNCE)
        accepted = call(c, leases)
        assert accepted is not None
        assert accepted.key.source_id == uid(999)
        assert accepted.key.fiscal_year == 1406
        assert accepted.file_sha256 == sha256(second).hexdigest()
        assert accepted.byte_count == len(second)
        rows = accepted.read_result.snapshot.all_rows_by_id
        assert uid(400) in rows and uid(1) not in rows
        assert rows[uid(400)].raw_values["unit_price_toman_raw"] == "1500001"
        assert c.view().state is State.IDLE
        assert not list(leases.iterdir())

    def test_id12_architecture_boundary_preservation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import accounting_local_agent.source_watch_runtime as runtime

        driver_tree = ast.parse(inspect.getsource(driver))
        for node in ast.walk(driver_tree):
            if isinstance(node, ast.Import):
                assert all(
                    name.name != "accounting_persistence"
                    and not name.name.startswith("accounting_persistence.")
                    for name in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                assert node.module is None or (
                    node.module != "accounting_persistence"
                    and not node.module.startswith("accounting_persistence.")
                )

        # The guard must precede the first driver import; the current process
        # already imported the driver during test collection.
        import_probe = """
import builtins
import importlib
import sys

assert 'accounting_local_agent.save_import_coordinator' not in sys.modules
original = builtins.__import__

class RejectPersistence:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'accounting_persistence' or fullname.startswith(
            'accounting_persistence.'
        ):
            raise AssertionError('persistence import crossed the local-agent boundary')
        return None

def guarded_import(name, *args, **kwargs):
    if name == 'accounting_persistence' or name.startswith('accounting_persistence.'):
        raise AssertionError('persistence import crossed the local-agent boundary')
    return original(name, *args, **kwargs)

builtins.__import__ = guarded_import
sys.meta_path.insert(0, RejectPersistence())
try:
    guarded_import('accounting_persistence')
except AssertionError:
    pass
else:
    raise AssertionError('persistence guard canary did not fire')
importlib.import_module('accounting_local_agent.save_import_coordinator')
print('PERSISTENCE_IMPORT_GUARD_OK')
"""
        project_root = Path(__file__).resolve().parents[1]
        pythonpath = [
            project_root / "apps/local_agent/src",
            project_root / "packages/contracts/src",
            project_root / "packages/domain/src",
        ]
        probe_env = dict(
            os.environ,
            PYTHONPATH=os.pathsep.join(
                [*(str(path) for path in pythonpath), os.environ.get("PYTHONPATH", "")]
            ),
        )
        probe = subprocess.run(
            [sys.executable, "-c", import_probe],
            env=probe_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert probe.returncode == 0, probe.stdout + probe.stderr
        assert probe.stdout.strip() == "PERSISTENCE_IMPORT_GUARD_OK"

        c, clock, source = coordinator(tmp_path)
        source.write_bytes(zipped(identified_parts()))
        leases = tmp_path / "leases"
        leases.mkdir()
        original = builtins.__import__

        def guard(name: str, *args: Any, **kwargs: Any) -> Any:
            assert not name.startswith("accounting_persistence")
            return original(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", guard)
        assert call(c, leases) is not None
        assert c.notify(SaveEventKind.MODIFIED, source)
        clock.advance_ns(DEBOUNCE)
        forbidden = Mock(
            side_effect=AssertionError("legacy driver called identified reader")
        )
        monkeypatch.setattr(driver, "read_identified_xlsx_source", forbidden)
        raw = driver.read_due_source(
            c, snapshot_root=leases, observation_interval_seconds=0.001
        )
        assert raw is not None and raw.snapshot.total_row_count == 5
        forbidden.assert_not_called()
        assert vars(runtime)["read_due_source"] is driver.read_due_source
        forbidden_identified = Mock(
            side_effect=AssertionError("raw execution path called identified driver")
        )
        monkeypatch.setattr(runtime, "read_due_identified_source", forbidden_identified)
        monkeypatch.setattr(driver, "read_due_identified_source", forbidden_identified)

        from test_source_watch_runtime import (
            ControlledConditionWaiter,
            ManagedRunnerThread,
            MockObserver,
        )

        observer = MockObserver()
        watch = runtime.SourceWatchRuntime(
            source,
            snapshot_root=leases,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: observer,
            _time_source=clock,
        )
        waiter = ControlledConditionWaiter(watch._condition)
        monkeypatch.setattr(watch._condition, "wait", waiter.hooked_wait)
        legacy = Mock(wraps=driver.read_due_source)
        monkeypatch.setattr(runtime, "read_due_source", legacy)
        delivered = threading.Event()
        delivered_values = []

        def consume(value: Any) -> None:
            delivered_values.append(value)
            delivered.set()

        runner = ManagedRunnerThread(watch, consume)
        runner.start()
        try:
            waiter.wait_for_ack(5.0)
            with watch._condition:
                clock.advance_ns(DEBOUNCE)
                watch._condition.notify_all()
            assert delivered.wait(5.0)
        finally:
            runner.stop_and_join()
        runner.assert_clean_exit()
        assert len(delivered_values) == 1 and delivered_values[0] == raw
        assert legacy.call_count >= 1
        forbidden.assert_not_called()
        forbidden_identified.assert_not_called()
        assert not observer.is_alive() and all(
            not em.is_alive() for em in observer.emitters
        )


class TestIdentifiedSaveImportDriverModel:
    @settings(max_examples=60, deadline=None)
    @given(
        st.lists(
            st.tuples(
                st.sampled_from(
                    [
                        "notice",
                        "unrelated",
                        "advance",
                        "success",
                        "notready",
                        "reject",
                        "fatal",
                        "resume",
                    ]
                ),
                st.booleans(),
                st.integers(min_value=0, max_value=DEBOUNCE + 1),
            ),
            min_size=1,
            max_size=40,
        )
    )
    def test_id13_independent_history_model(
        self, history: list[tuple[str, bool, int]]
    ) -> None:
        # Literal independent scheduler: no product transition helper is used.
        target = Path.cwd() / "SYNTHETIC-history.xlsx"
        clock = FakeClock()
        c = SaveImportCoordinator(target, _time_source=clock)
        state, deadline = "idle", None
        calls = 0
        expected_calls = 0
        # Every generated history exercises all outcomes before its random tail.
        # This avoids a mostly idle corpus accidentally passing the state oracle.
        prefix = [
            ("notice", False, 0),
            ("success", False, 0),
            ("advance", False, DEBOUNCE),
            ("success", True, 0),
            ("advance", False, DEBOUNCE),
            ("notready", False, 0),
            ("advance", False, DEBOUNCE),
            ("reject", True, 0),
            ("advance", False, DEBOUNCE),
            ("reject", False, 0),
            ("notice", False, 0),
            ("advance", False, DEBOUNCE),
            ("fatal", True, 0),
            ("unrelated", False, 0),
            ("advance", False, DEBOUNCE),
            ("success", False, 0),
            ("resume", False, 0),
            ("advance", False, DEBOUNCE),
            ("success", False, 0),
        ]
        for action, followup, delta in prefix + history:
            if action == "notice":
                c.notify(SaveEventKind.MODIFIED, target)
                if state != "faulted":
                    state, deadline = "waiting", clock() + DEBOUNCE
            elif action == "unrelated":
                assert not c.notify(
                    SaveEventKind.MODIFIED, target.with_name("unrelated.xlsx")
                )
            elif action == "advance":
                clock.advance_ns(delta)
            elif action == "resume":
                if state == "faulted":
                    c.resume_after_fault()
                    state, deadline = "waiting", clock() + DEBOUNCE
            else:
                ready = (
                    state == "waiting" and deadline is not None and clock() >= deadline
                )
                error = {
                    "success": None,
                    "notready": XlsxSourceNotReadyError(),
                    "reject": XlsxSourceReadError("synthetic rejection"),
                    "fatal": RuntimeError(),
                }[action]
                returned = object()

                def read(
                    *args: Any,
                    followup: bool = followup,
                    error: BaseException | None = error,
                    returned: object = returned,
                    **kwargs: Any,
                ) -> Any:
                    nonlocal calls
                    calls += 1
                    assert c.view().state is State.RUNNING
                    # A second overlapping admission cannot obtain ownership.
                    assert call(c, target.parent) is None
                    if followup:
                        assert c.notify(SaveEventKind.MODIFIED, target)
                    if error is not None:
                        raise error
                    return returned

                with pytest.MonkeyPatch.context() as patch:
                    patch.setattr(driver, "read_identified_xlsx_source", read)
                    if ready and error is not None:
                        with pytest.raises(type(error)) as caught:
                            call(c, target.parent)
                        assert caught.value is error
                    else:
                        assert call(c, target.parent) is (returned if ready else None)
                if ready:
                    expected_calls += 1
                    if action == "fatal":
                        state, deadline = "faulted", None
                    elif action == "notready" or followup:
                        state, deadline = "waiting", clock() + DEBOUNCE
                    else:
                        state, deadline = "idle", None
            assert calls == expected_calls
            assert c.view().state.value == state and c.view().next_due_ns == deadline

    @pytest.mark.parametrize(
        "mutation",
        ["early-success", "duplicate-read", "lost-followup", "wrong-classification"],
    )
    def test_id13_targeted_mutations_are_detected(
        self, tmp_path: Path, mutation: str
    ) -> None:
        original = inspect.getsource(driver.read_due_identified_source)
        needle = "        result = read_identified_xlsx_source("
        changes = {
            "early-success": (
                needle,
                "        coordinator.finish(attempt, SourceReadOutcome.SUCCESS)\n"
                + needle,
            ),
            "duplicate-read": (
                needle,
                "        read_identified_xlsx_source(coordinator.source_path,"
                " snapshot_root=snapshot_root, observation_interval_seconds=o"
                "bservation_interval_seconds)\n" + needle,
            ),
            "lost-followup": (
                "        coordinator.finish(attempt, outcome)",
                "        coordinator._pending_followup = False\n        coordi"
                "nator.finish(attempt, outcome)",
            ),
            "wrong-classification": (
                "outcome = SourceReadOutcome.SOURCE_NOT_READY",
                "outcome = SourceReadOutcome.READER_REJECTED",
            ),
        }
        old, new = changes[mutation]
        assert original.count(old) == 1

        def oracle(text: str) -> None:
            c, clock, target = coordinator(tmp_path)
            namespace = dict(vars(driver))
            returned = object()
            calls = []
            read_states = []
            error = XlsxSourceNotReadyError()

            def read(*args: Any, **kwargs: Any) -> Any:
                calls.append(args)
                read_states.append(c.view().state)
                if mutation == "wrong-classification":
                    # Not-ready must retry even with no follow-up notice.
                    raise error
                c.notify(SaveEventKind.MODIFIED, target)
                return returned

            namespace["read_identified_xlsx_source"] = read
            exec(compile(text, "synthetic-driver-mutation", "exec"), namespace)
            result = None
            caught = None
            try:
                result = namespace["read_due_identified_source"](
                    c, snapshot_root=tmp_path, observation_interval_seconds=0.001
                )
            except BaseException as exc:
                caught = exc
            assert len(calls) == 1 and read_states == [State.RUNNING]
            assert caught is (error if mutation == "wrong-classification" else None)
            assert result is (None if mutation == "wrong-classification" else returned)
            assert (
                c.view().state is State.WAITING
                and c.view().next_due_ns == clock() + DEBOUNCE
            )

        oracle(original)
        with pytest.raises(AssertionError):
            oracle(original.replace(old, new, 1))


class TestIdentifiedSaveImportDriverBenchmark:
    def test_id14_identified_15000_row_benchmark(self, tmp_path: Path) -> None:
        start = time.perf_counter()
        (tmp_path / "benchmark.xlsx").write_bytes(
            zipped(
                identified_parts(raw=raw_parts(rows_per_sheet=3750, extra_buy=False))
            )
        )
        fixture_seconds = time.perf_counter() - start
        # Reuse the existing RSS sampler and independent 15,000-row oracle;
        # only its entry point is wrapped to exercise the due driver.
        code = """
import xlsx_source_identity_benchmark as benchmark
from accounting_local_agent import (
    SaveImportCoordinator, SaveEventKind, read_due_identified_source,
)
def due(source, **kwargs):
    now = [1_000_000_000]
    coordinator = SaveImportCoordinator(source, _time_source=lambda: now[0])
    assert coordinator.notify(SaveEventKind.MODIFIED, source)
    now[0] += 2_000_000_000
    return read_due_identified_source(coordinator, **kwargs)
benchmark.read_identified_xlsx_source = due
benchmark.main()
"""
        env = dict(
            os.environ,
            PYTHONPATH=os.pathsep.join(
                [str(Path(__file__).parent), os.environ.get("PYTHONPATH", "")]
            ),
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path), str(fixture_seconds)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        metrics = json.loads(result.stdout.strip().splitlines()[-1])
        assert (
            metrics["rows"] == 15000
            and metrics["seconds"] < 15.0
            and metrics["peak_rss_mib"] < 128.0
        )
        print(json.dumps(metrics, sort_keys=True))

    def test_id15_registered_regression_gates(self) -> None:
        # Full regression and platform gates are executed by the controller,
        # outside this test. Assert the accepted runtime/legacy API remains intact.
        assert public.read_due_source is driver.read_due_source
        assert public.SAVE_DEBOUNCE_NS == DEBOUNCE
        assert list(inspect.signature(public.SourceWatchRuntime.run).parameters) == [
            "self",
            "consumer",
        ]
        assert list(
            inspect.signature(public.read_identified_xlsx_source).parameters
        ) == ["source_path", "snapshot_root", "observation_interval_seconds"]
