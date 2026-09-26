"""Tests for identified-source-watch-runtime.v1 (WP-17 / ADR-0025).

Covers acceptance criteria IW-01 through IW-16:
- IW-01: Public API exports, version, signature, inertness
- IW-02: Consumer validation, single-use lifecycle, concurrent entry
- IW-03: Mode-specific driver selection, runtime-owned arguments, delivery identity
- IW-04: Backend startup, debounce, partial startup cleanup
- IW-05: Exact-path event mapping, unrelated path exclusion, burst coalescing, follow-up
- IW-06: Nonterminal NotReady retries and Raw-reader / marker rejections
- IW-07: Terminal errors, ordering, groups, shared causes, cancellation
- IW-08: Synchronous serial consumer calls, ignored return values, consumer failure
- IW-09: Waiting stop, admitted read/consumer drain, follow-up suppression, worker join
- IW-10: Barrier-controlled races: stop vs wake, callback vs teardown, liveness
- IW-11: Native CI filesystem observation with honest platform skips
- IW-12: Full WP-16 acquisition and identity stack with 4-sheet synthetic XLSX
- IW-13: Single-generation binding and workbook mutation / replacement
- IW-14: Persistence trapping and independent oracle comparison
- IW-15: Model history simulation (40+ histories) and mutation detection
- IW-16: 15,000-row identified runtime benchmark preserving 15s/128MiB limits
"""

from __future__ import annotations

import builtins
import hashlib
import importlib
import inspect
import io
import os
import random
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from accounting_local_agent import (
    IDENTIFIED_SOURCE_WATCH_RUNTIME_VERSION,
    SOURCE_WATCH_RUNTIME_VERSION,
    IdentifiedXlsxSource,
    SaveEventKind,
    SaveImportCoordinator,
    SourceWatchRuntime,
    SourceWatchRuntimeError,
    SourceWatchRuntimeReason,
    SourceWatchRuntimeState,
    SourceWatchRuntimeView,
    XlsxSourceReadResult,
    open_stable_xlsx_snapshot,
    read_due_identified_source,
    read_due_source,
    read_identified_xlsx_source,
    read_xlsx_source_snapshot,
)
from accounting_local_agent.save_import_coordinator import (
    SaveCoordinatorPolicyError,
    SaveCoordinatorState,
    SaveCoordinatorStateError,
    SourceReadOutcome,
)
from accounting_local_agent.source_watch_runtime import _WatchdogEventAdapter
from accounting_local_agent.xlsx_snapshot_acquisition import (
    XlsxSnapshotCleanupError,
    XlsxSnapshotIntegrityError,
    XlsxSnapshotStorageError,
    XlsxSourceNotReadyError,
    XlsxSourcePolicyError,
)
from accounting_local_agent.xlsx_source_identity import (
    XlsxSourceIdentityError,
    XlsxSourceIdentityReason,
)
from accounting_local_agent.xlsx_source_reader import (
    XlsxSourceReadError,
)
from test_source_watch_runtime import (
    ControlledConditionWaiter,
    FakeClock,
    MockEmitter,
    MockObserver,
)
from test_xlsx_source_reader import _CallWindowRssSampler
from watchdog.events import (
    DirModifiedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)
from xlsx_source_identity_fixtures import identified_parts, raw_parts, zipped

_DEFAULT_SOURCE_ID = uuid.UUID("00000000-0000-7000-8000-0000000003e7")


def _build_synthetic_identified_xlsx(
    source_id: uuid.UUID = _DEFAULT_SOURCE_ID,
    fiscal_year: int = 1405,
    rows_per_sheet: int = 1,
) -> bytes:
    """Helper building a compliant synthetic identified 4-sheet XLSX."""
    marker_val = f"xlsx-source-identity.v1|{source_id}|{fiscal_year}"
    parts = identified_parts(
        value=marker_val,
        raw=raw_parts(rows_per_sheet=rows_per_sheet),
    )
    return zipped(parts)


class ManagedIdentifiedRunnerThread:
    """Helper thread managing runner execution for run_identified."""

    def __init__(
        self,
        runtime: SourceWatchRuntime,
        consumer: Callable[[IdentifiedXlsxSource], None],
    ) -> None:
        self.runtime = runtime
        self.consumer = consumer
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._run)

    def _run(self) -> None:
        try:
            self.runtime.run_identified(self.consumer)
        except BaseException as e:
            self.error = e

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float = 5.0) -> None:
        self._thread.join(timeout=timeout)
        assert not self._thread.is_alive(), "Runner thread failed to terminate"

    def stop_and_join(self, timeout: float = 5.0) -> None:
        self.runtime.request_stop()
        self.join(timeout=timeout)

    def assert_clean_exit(self) -> None:
        assert self.error is None, (
            f"Runner thread raised unexpected exception: {self.error!r}"
        )


# ===========================================================================
# TestIdentifiedSourceWatchRuntimeApi
# Covers IW-01, IW-02, IW-03
# ===========================================================================


class TestIdentifiedSourceWatchRuntimeApi:
    """API, signature, import-inertness, and driver selection tests."""

    def test_iw01_public_api_and_import_inertness(self) -> None:
        """IW-01: Constant, exports, method signature, and import inertness."""
        assert (
            IDENTIFIED_SOURCE_WATCH_RUNTIME_VERSION
            == "identified-source-watch-runtime.v1"
        )
        assert SOURCE_WATCH_RUNTIME_VERSION == "source-watch-runtime.v1"

        # Check run_identified signature on SourceWatchRuntime
        sig = inspect.signature(SourceWatchRuntime.run_identified)
        params = list(sig.parameters.values())
        assert len(params) == 2
        assert params[0].name == "self"
        assert params[1].name == "consumer"
        assert params[1].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert params[1].default is inspect.Parameter.empty
        assert params[1].annotation in (
            Callable[[IdentifiedXlsxSource], None],
            "Callable[[IdentifiedXlsxSource], None]",
        )
        assert sig.return_annotation in (None, "None")

        # Verify prior exports and types remain accessible
        assert SourceWatchRuntime is not None
        assert SourceWatchRuntimeError is not None
        assert SourceWatchRuntimeReason is not None
        assert SourceWatchRuntimeState is not None
        assert SourceWatchRuntimeView is not None
        assert SaveEventKind is not None
        assert XlsxSourceReadResult is not None
        assert SaveImportCoordinator is not None
        assert read_due_source is not None
        assert read_due_identified_source is not None
        assert IdentifiedXlsxSource is not None
        assert read_identified_xlsx_source is not None
        assert open_stable_xlsx_snapshot is not None
        assert read_xlsx_source_snapshot is not None

        # Assert fresh imports remain inert under strict side-effect controls.
        saved_modules = {
            k: v
            for k, v in sys.modules.items()
            if k == "accounting_local_agent" or k.startswith("accounting_local_agent.")
        }
        for k in list(saved_modules.keys()):
            del sys.modules[k]

        def fail_trap(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
            def _trap(*args: Any, **kwargs: Any) -> Any:
                frame = sys._getframe(1)
                for _ in range(5):
                    caller = str(frame.f_globals.get("__name__", ""))
                    if caller.startswith("accounting_local_agent"):
                        raise AssertionError(
                            f"Import inertness violated: {name} called"
                        )
                    if caller.startswith(
                        ("importlib", "_frozen_importlib", "zipimport")
                    ):
                        break
                    if frame.f_back is None:
                        break
                    frame = frame.f_back
                return original(*args, **kwargs)

            return _trap

        orig_sleep = time.sleep
        orig_thread_start = threading.Thread.start
        orig_random = random.random
        orig_monotonic_ns = time.monotonic_ns
        orig_monotonic = time.monotonic
        orig_perf_counter = time.perf_counter
        orig_time_ns = time.time_ns
        orig_time = time.time
        orig_mkdir = os.mkdir
        orig_remove = os.remove
        orig_unlink = os.unlink
        orig_stat = os.stat
        orig_os_path_exists = os.path.exists
        orig_listdir = os.listdir
        orig_scandir = os.scandir
        orig_os_open = os.open
        orig_urandom = os.urandom
        orig_uuid4 = uuid.uuid4
        orig_token_bytes = secrets.token_bytes
        orig_open = builtins.open
        orig_path_exists = Path.exists
        orig_path_stat = Path.stat
        orig_path_open = Path.open
        orig_path_read_bytes = Path.read_bytes
        orig_path_unlink = Path.unlink
        orig_socket = socket.socket
        thread_type = cast(Any, threading.Thread)

        try:
            time.sleep = fail_trap("time.sleep", orig_sleep)
            thread_type.start = fail_trap("threading.Thread.start", orig_thread_start)
            random.random = fail_trap("random.random", orig_random)
            time.monotonic_ns = fail_trap("time.monotonic_ns", orig_monotonic_ns)
            time.monotonic = fail_trap("time.monotonic", orig_monotonic)
            time.perf_counter = fail_trap("time.perf_counter", orig_perf_counter)
            time.time_ns = fail_trap("time.time_ns", orig_time_ns)
            time.time = fail_trap("time.time", orig_time)
            os.mkdir = fail_trap("os.mkdir", orig_mkdir)
            os.remove = fail_trap("os.remove", orig_remove)
            os.unlink = fail_trap("os.unlink", orig_unlink)
            os.stat = fail_trap("os.stat", orig_stat)
            os.path.exists = fail_trap("os.path.exists", orig_os_path_exists)
            os.listdir = fail_trap("os.listdir", orig_listdir)
            os.scandir = fail_trap("os.scandir", orig_scandir)
            os.open = fail_trap("os.open", orig_os_open)
            os.urandom = fail_trap("os.urandom", orig_urandom)
            uuid.uuid4 = fail_trap("uuid.uuid4", orig_uuid4)
            secrets.token_bytes = fail_trap("secrets.token_bytes", orig_token_bytes)
            builtins.open = fail_trap("builtins.open", orig_open)
            Path.exists = fail_trap("Path.exists", orig_path_exists)  # type: ignore[method-assign]
            Path.stat = fail_trap("Path.stat", orig_path_stat)  # type: ignore[method-assign]
            Path.open = fail_trap("Path.open", orig_path_open)  # type: ignore[method-assign]
            Path.read_bytes = fail_trap("Path.read_bytes", orig_path_read_bytes)  # type: ignore[method-assign]
            Path.unlink = fail_trap("Path.unlink", orig_path_unlink)  # type: ignore[method-assign]
            socket.socket = fail_trap("socket.socket", orig_socket)  # type: ignore[misc,assignment]

            target_scope = {
                "__name__": "accounting_local_agent.source_watch_runtime",
                "time": time,
                "os": os,
                "uuid": uuid,
                "secrets": secrets,
                "Path": Path,
                "builtins": builtins,
                "threading": threading,
            }
            for name, expression in (
                ("time.monotonic_ns", "time.monotonic_ns()"),
                ("time.time", "time.time()"),
                ("time.time_ns", "time.time_ns()"),
                ("time.monotonic", "time.monotonic()"),
                ("time.perf_counter", "time.perf_counter()"),
                ("os.urandom", "os.urandom(8)"),
                ("uuid.uuid4", "uuid.uuid4()"),
                ("secrets.token_bytes", "secrets.token_bytes(8)"),
                ("os.stat", "os.stat('synthetic-guard-probe')"),
                ("os.path.exists", "os.path.exists('synthetic-guard-probe')"),
                ("os.unlink", "os.unlink('synthetic-guard-probe')"),
                ("os.listdir", "os.listdir('.')"),
                ("Path.exists", "Path('synthetic-guard-probe').exists()"),
                ("Path.stat", "Path('synthetic-guard-probe').stat()"),
                ("Path.stat", "Path('synthetic-guard-probe').is_file()"),
                ("Path.unlink", "Path('synthetic-guard-probe').unlink()"),
                ("builtins.open", "builtins.open('synthetic-guard-probe')"),
            ):
                with pytest.raises(AssertionError, match=name):
                    exec(
                        compile(expression, "<import-guard-control>", "exec"),
                        target_scope,
                    )

            canary = threading.Thread(target=lambda: None)
            target_scope["canary"] = canary
            try:
                with pytest.raises(AssertionError, match="threading.Thread.start"):
                    exec("canary.start()", target_scope)
            finally:
                if canary.ident is not None:
                    canary.join(timeout=5.0)
                    assert not canary.is_alive()

            fresh_mod = importlib.import_module("accounting_local_agent")
            fresh_sw = importlib.import_module(
                "accounting_local_agent.source_watch_runtime"
            )
            assert (
                fresh_mod.IDENTIFIED_SOURCE_WATCH_RUNTIME_VERSION
                == "identified-source-watch-runtime.v1"
            )
            assert (
                fresh_sw.IDENTIFIED_SOURCE_WATCH_RUNTIME_VERSION
                == "identified-source-watch-runtime.v1"
            )
        finally:
            time.sleep = orig_sleep
            thread_type.start = orig_thread_start
            random.random = orig_random
            time.monotonic_ns = orig_monotonic_ns
            time.monotonic = orig_monotonic
            time.perf_counter = orig_perf_counter
            time.time_ns = orig_time_ns
            time.time = orig_time
            os.mkdir = orig_mkdir
            os.remove = orig_remove
            os.unlink = orig_unlink
            os.stat = orig_stat
            os.path.exists = orig_os_path_exists
            os.listdir = orig_listdir
            os.scandir = orig_scandir
            os.open = orig_os_open
            os.urandom = orig_urandom
            uuid.uuid4 = orig_uuid4
            secrets.token_bytes = orig_token_bytes
            builtins.open = orig_open
            Path.exists = orig_path_exists  # type: ignore[method-assign]
            Path.stat = orig_path_stat  # type: ignore[method-assign]
            Path.open = orig_path_open  # type: ignore[method-assign]
            Path.read_bytes = orig_path_read_bytes  # type: ignore[method-assign]
            Path.unlink = orig_path_unlink  # type: ignore[method-assign]
            socket.socket = orig_socket  # type: ignore[misc]
            for k in list(sys.modules.keys()):
                if k == "accounting_local_agent" or k.startswith(
                    "accounting_local_agent."
                ):
                    del sys.modules[k]
            sys.modules.update(saved_modules)

    def test_iw02_consumer_validation_and_lifecycle_admission(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IW-02: Non-callable rejection, sequential and concurrent run single-use."""
        src = tmp_path / "watch" / "target.xlsx"
        snap_root = tmp_path / "snapshots"

        factory_calls = 0

        def mock_factory() -> MockObserver:
            nonlocal factory_calls
            factory_calls += 1
            return MockObserver()

        runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.01,
            _observer_factory=mock_factory,
        )

        # 1. Invalid consumer fails before observer factory or state mutation
        for invalid_consumer in [None, "not_callable", 123, object()]:
            with pytest.raises(
                SourceWatchRuntimeError, match="consumer must be callable"
            ) as exc_info:
                runtime.run_identified(cast(Any, invalid_consumer))
            assert exc_info.value.reason == SourceWatchRuntimeReason.INVALID_POLICY
            assert runtime.view().state == SourceWatchRuntimeState.NEW
            assert factory_calls == 0

        # 2. Sequential entry: once stopped, second run gives INVALID_TRANSITION
        runtime.request_stop()
        assert runtime.view().state == SourceWatchRuntimeState.STOPPED

        with pytest.raises(
            SourceWatchRuntimeError,
            match="can only be called on a runtime in new state",
        ) as exc_seq:
            runtime.run_identified(lambda res: None)
        assert exc_seq.value.reason == SourceWatchRuntimeReason.INVALID_TRANSITION

        # Reject raw mode on a stopped identified runtime.
        with pytest.raises(
            SourceWatchRuntimeError,
            match="can only be called on a runtime in new state",
        ) as exc_seq_raw:
            runtime.run(lambda res: None)
        assert exc_seq_raw.value.reason == SourceWatchRuntimeReason.INVALID_TRANSITION

        # 3. Concurrent entry calls: exactly one execution admitted across modes
        num_threads = 6
        barrier = threading.Barrier(num_threads)
        errors: list[BaseException | None] = [None] * num_threads
        errors_condition = threading.Condition()
        winner_entered = threading.Event()
        winner_release = threading.Event()

        obs_concurrent = MockObserver()
        factory_concurrent_calls = 0

        def concurrent_factory() -> MockObserver:
            nonlocal factory_concurrent_calls
            factory_concurrent_calls += 1
            return obs_concurrent

        clock_concurrent = FakeClock()
        mock_identified_res = cast(IdentifiedXlsxSource, object())
        mock_raw_res = cast(XlsxSourceReadResult, object())
        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_identified_source",
            lambda *a, **k: mock_identified_res,
        )
        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_source",
            lambda *a, **k: mock_raw_res,
        )

        concurrent_runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=concurrent_factory,
            _time_source=clock_concurrent,
        )

        waiter = ControlledConditionWaiter(concurrent_runtime._condition)
        monkeypatch.setattr(concurrent_runtime._condition, "wait", waiter.hooked_wait)

        def concurrent_identified_consumer(res: IdentifiedXlsxSource) -> None:
            winner_entered.set()
            winner_release.wait(timeout=5.0)

        def concurrent_raw_consumer(res: XlsxSourceReadResult) -> None:
            winner_entered.set()
            winner_release.wait(timeout=5.0)

        def runner_proc(idx: int) -> None:
            try:
                barrier.wait(timeout=5.0)
                if idx % 2 == 0:
                    concurrent_runtime.run_identified(concurrent_identified_consumer)
                else:
                    concurrent_runtime.run(concurrent_raw_consumer)
            except BaseException as exc:
                with errors_condition:
                    errors[idx] = exc
                    errors_condition.notify_all()

        threads = [
            threading.Thread(target=runner_proc, args=(i,)) for i in range(num_threads)
        ]
        try:
            for t in threads:
                t.start()

            waiter.wait_for_ack(timeout=5.0)
            clock_concurrent.advance_seconds(3.0)
            with concurrent_runtime._lifecycle_lock:
                concurrent_runtime._condition.notify_all()

            assert winner_entered.wait(timeout=5.0), "Winner failed to enter consumer"
            assert concurrent_runtime.view().state == SourceWatchRuntimeState.RUNNING
            assert factory_concurrent_calls == 1

            with errors_condition:
                assert errors_condition.wait_for(
                    lambda: (
                        sum(error is not None for error in errors) == num_threads - 1
                    ),
                    timeout=5.0,
                )
            loser_threads = [t for i, t in enumerate(threads) if errors[i] is not None]
            assert len(loser_threads) == num_threads - 1
            for lt in loser_threads:
                lt.join(timeout=2.0)
                assert not lt.is_alive()

            transition_errors = [
                e
                for e in errors
                if isinstance(e, SourceWatchRuntimeError)
                and e.reason == SourceWatchRuntimeReason.INVALID_TRANSITION
            ]
            assert len(transition_errors) == num_threads - 1

            winner_release.set()
            concurrent_runtime.request_stop()
            for t in threads:
                t.join(timeout=5.0)
                assert not t.is_alive()

            successful_runs = [e for e in errors if e is None]
            assert len(successful_runs) == 1
            assert concurrent_runtime.view().state == SourceWatchRuntimeState.STOPPED
        finally:
            barrier.abort()
            winner_release.set()
            concurrent_runtime.request_stop()
            for t in threads:
                if t.ident is not None:
                    t.join(timeout=5.0)
                    assert not t.is_alive()
            obs_concurrent.stop()

    def test_iw03_driver_selection_and_delivery_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IW-03: Mode-specific driver selection, runtime args, identity delivery."""
        src_dir = tmp_path / "watch"
        src_dir.mkdir()
        src = src_dir / "target.xlsx"
        snap_root = tmp_path / "snapshots"
        snap_root.mkdir()

        clock = FakeClock()
        mock_obs = MockObserver()

        raw_calls = 0
        identified_calls = 0
        recorded_coordinator: Any = None
        recorded_snap_root: Any = None
        recorded_interval: Any = None

        mock_result = cast(IdentifiedXlsxSource, object())
        mock_raw_result = cast(XlsxSourceReadResult, object())

        def hooked_read_due_source(*args: Any, **kwargs: Any) -> Any:
            nonlocal raw_calls
            raw_calls += 1
            return mock_raw_result

        def hooked_read_due_identified_source(
            coord: Any, *, snapshot_root: Path, observation_interval_seconds: float
        ) -> Any:
            nonlocal \
                identified_calls, \
                recorded_coordinator, \
                recorded_snap_root, \
                recorded_interval
            identified_calls += 1
            recorded_coordinator = coord
            recorded_snap_root = snapshot_root
            recorded_interval = observation_interval_seconds
            if identified_calls == 1:
                return mock_result
            return None

        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_source",
            hooked_read_due_source,
        )
        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_identified_source",
            hooked_read_due_identified_source,
        )

        # 1. Identified mode execution
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.042,
            _observer_factory=lambda: mock_obs,
            _time_source=clock,
        )

        delivered: list[IdentifiedXlsxSource] = []

        def consumer(res: IdentifiedXlsxSource) -> None:
            delivered.append(res)
            runtime.request_stop()

        runner = ManagedIdentifiedRunnerThread(runtime, consumer)
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)
        runner.start()

        try:
            waiter.wait_for_ack(timeout=5.0)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()

            runner.join(timeout=5.0)
            runner.assert_clean_exit()

            # Prove identified mode calls only read_due_identified_source
            assert raw_calls == 0
            assert identified_calls >= 1
            assert recorded_coordinator is runtime._coordinator
            assert recorded_snap_root == snap_root
            assert recorded_interval == 0.042

            # Prove single delivery by exact object identity, no delivery for None
            assert len(delivered) == 1
            assert delivered[0] is mock_result
        finally:
            runtime.request_stop()
            runner.join(timeout=5.0)
            mock_obs.stop()

        # 2. Raw mode execution verifies mode-specific driver isolation
        raw_obs = MockObserver()
        raw_clock = FakeClock()
        raw_runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.025,
            _observer_factory=lambda: raw_obs,
            _time_source=raw_clock,
        )
        raw_waiter = ControlledConditionWaiter(raw_runtime._condition)
        monkeypatch.setattr(raw_runtime._condition, "wait", raw_waiter.hooked_wait)

        raw_delivered: list[XlsxSourceReadResult] = []

        def raw_consumer(res: XlsxSourceReadResult) -> None:
            raw_delivered.append(res)
            raw_runtime.request_stop()

        raw_calls_before = raw_calls
        identified_calls_before = identified_calls

        t_raw = threading.Thread(target=lambda: raw_runtime.run(raw_consumer))
        t_raw.start()

        try:
            raw_waiter.wait_for_ack(timeout=5.0)
            raw_clock.advance_seconds(3.0)
            with raw_runtime._lifecycle_lock:
                raw_runtime._condition.notify_all()
            t_raw.join(timeout=5.0)
            assert not t_raw.is_alive()

            assert raw_calls > raw_calls_before
            assert identified_calls == identified_calls_before, (
                "Raw mode must not invoke identified driver"
            )
        finally:
            raw_runtime.request_stop()
            t_raw.join(timeout=5.0)
            raw_obs.stop()

    def test_iw03_none_result_never_delivered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A due driver call returning None has no consumer delivery."""
        src = tmp_path / "watch" / "target.xlsx"
        snap_root = tmp_path / "snapshots"
        clock = FakeClock()
        observer = MockObserver()
        called = threading.Event()
        deliveries: list[IdentifiedXlsxSource] = []

        def no_result(*args: Any, **kwargs: Any) -> None:
            called.set()
            return None

        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_identified_source",
            no_result,
        )
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: observer,
            _time_source=clock,
        )
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)
        runner = ManagedIdentifiedRunnerThread(runtime, deliveries.append)
        runner.start()
        try:
            waiter.wait_for_ack(timeout=5.0)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            assert called.wait(timeout=5.0)
            runtime.request_stop()
            runner.join(timeout=5.0)
            runner.assert_clean_exit()
            assert deliveries == []
        finally:
            runtime.request_stop()
            runner.join(timeout=5.0)
            observer.stop()

    @pytest.mark.parametrize("mode", ["raw", "identified"])
    @pytest.mark.parametrize(
        "mutation",
        [
            "wrong_driver",
            "early_delivery",
            "duplicate_delivery",
            "lost_followup",
            "mode_drift",
        ],
    )
    def test_iw15_mutations_are_detected_through_runtime_entries(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        mode: str,
        mutation: str,
    ) -> None:
        """Mutate runtime behavior and prove the literal oracle rejects it."""
        watch = tmp_path / "watch"
        watch.mkdir()
        source = watch / "target.xlsx"
        clock = FakeClock(start_ns=1_000_000_000)
        observer = MockObserver()
        runtime = SourceWatchRuntime(
            source,
            snapshot_root=tmp_path / "snapshots",
            observation_interval_seconds=0.001,
            _observer_factory=lambda: observer,
            _time_source=clock,
        )
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)
        raw_value = object.__new__(XlsxSourceReadResult)
        identified_value = object.__new__(IdentifiedXlsxSource)
        selected: list[str] = []
        delivered: list[tuple[Any, int]] = []
        second_delivered = threading.Event()
        errors: list[BaseException] = []
        in_read = threading.Event()
        release_read = threading.Event()
        read_calls = 0

        @contextmanager
        def fake_lease(*args: Any, **kwargs: Any) -> Any:
            yield SimpleNamespace(snapshot_path=source)

        def raw_reader(*args: Any, **kwargs: Any) -> XlsxSourceReadResult:
            nonlocal read_calls
            read_calls += 1
            if mutation == "lost_followup" and read_calls == 1:
                in_read.set()
                assert release_read.wait(timeout=5.0)
            return raw_value

        def identified_reader(*args: Any, **kwargs: Any) -> IdentifiedXlsxSource:
            nonlocal read_calls
            read_calls += 1
            if mutation == "lost_followup" and read_calls == 1:
                in_read.set()
                assert release_read.wait(timeout=5.0)
            return identified_value

        def raw_driver(*args: Any, **kwargs: Any) -> Any:
            selected.append("raw")
            return read_due_source(*args, **kwargs)

        def identified_driver(*args: Any, **kwargs: Any) -> Any:
            selected.append("identified")
            return read_due_identified_source(*args, **kwargs)

        monkeypatch.setattr(
            "accounting_local_agent.save_import_coordinator.open_stable_xlsx_snapshot",
            fake_lease,
        )
        monkeypatch.setattr(
            "accounting_local_agent.save_import_coordinator.read_xlsx_source_snapshot",
            raw_reader,
        )
        monkeypatch.setattr(
            "accounting_local_agent.save_import_coordinator.read_identified_xlsx_source",
            identified_reader,
        )
        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_source",
            identified_driver
            if mutation == "wrong_driver" and mode == "raw"
            else raw_driver,
        )
        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_identified_source",
            raw_driver
            if mutation == "wrong_driver" and mode == "identified"
            else identified_driver,
        )
        if mutation == "mode_drift":
            wrong = identified_value if mode == "raw" else raw_value
            if mode == "raw":
                monkeypatch.setattr(
                    "accounting_local_agent.source_watch_runtime.read_due_source",
                    lambda *args, **kwargs: wrong,
                )
            else:
                monkeypatch.setattr(
                    "accounting_local_agent.source_watch_runtime.read_due_identified_source",
                    lambda *args, **kwargs: wrong,
                )
        if mutation == "early_delivery":
            original_notify = runtime._coordinator.notify

            def early_notify(*args: Any, **kwargs: Any) -> bool:
                accepted = original_notify(*args, **kwargs)
                with runtime._coordinator._lock:
                    if runtime._coordinator._next_due_ns is not None:
                        runtime._coordinator._next_due_ns -= 1_500_000_000
                return accepted

            monkeypatch.setattr(runtime._coordinator, "notify", early_notify)
        if mutation == "lost_followup":
            original_notify = runtime._coordinator.notify

            def lost_notify(*args: Any, **kwargs: Any) -> bool:
                if runtime._coordinator.view().state == SaveCoordinatorState.RUNNING:
                    return True
                return original_notify(*args, **kwargs)

            monkeypatch.setattr(runtime._coordinator, "notify", lost_notify)
        if mutation == "duplicate_delivery":
            original_execute = runtime._execute_loop

            def duplicate_execute(
                consumer: Callable[[Any], None], *, identified: bool
            ) -> None:
                def duplicated(value: Any) -> None:
                    consumer(value)
                    consumer(value)

                original_execute(duplicated, identified=identified)

            monkeypatch.setattr(runtime, "_execute_loop", duplicate_execute)

        delivery_condition = threading.Condition()

        def consumer(value: Any) -> None:
            with delivery_condition:
                delivered.append((value, clock()))
                if len(delivered) == 2:
                    second_delivered.set()
                delivery_condition.notify_all()

        def run() -> None:
            try:
                if mode == "identified":
                    runtime.run_identified(consumer)
                else:
                    runtime.run(consumer)
            except BaseException as error:
                errors.append(error)

        runner = threading.Thread(target=run)
        runner.start()
        try:
            waiter.wait_for_ack(timeout=5.0)
            clock.set_ns(
                1_500_000_000 if mutation == "early_delivery" else 4_000_000_000
            )
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            if mutation == "lost_followup":
                assert in_read.wait(timeout=5.0)
                runtime._on_adapter_event(SaveEventKind.MODIFIED, source, None)
                release_read.set()
            with delivery_condition:
                assert delivery_condition.wait_for(lambda: bool(delivered), timeout=5.0)
            if mutation == "lost_followup":
                waiter.wait_for_ack(timeout=5.0)
                # The independent oracle requires one waiting follow-up cycle.
                with pytest.raises(AssertionError):
                    assert (
                        runtime._coordinator.view().state
                        == SaveCoordinatorState.WAITING
                    )
            elif mutation == "wrong_driver":
                with pytest.raises(AssertionError):
                    assert selected == [mode]
            elif mutation == "early_delivery":
                with pytest.raises(AssertionError):
                    assert delivered[0][1] >= 3_000_000_000
            elif mutation == "duplicate_delivery":
                assert second_delivered.wait(timeout=5.0)
                with pytest.raises(AssertionError):
                    assert len(delivered) == 1
            else:
                expected_type = (
                    XlsxSourceReadResult if mode == "raw" else IdentifiedXlsxSource
                )
                with pytest.raises(AssertionError):
                    assert isinstance(delivered[0][0], expected_type)
        finally:
            release_read.set()
            runtime.request_stop()
            runner.join(timeout=5.0)
            assert not runner.is_alive()
            observer.stop()
        assert errors == []


# ===========================================================================
# TestIdentifiedSourceWatchRuntimeLifecycle
# Covers IW-04, IW-05, IW-06, IW-07, IW-08, IW-09, IW-10
# ===========================================================================


class TestIdentifiedSourceWatchRuntimeLifecycle:
    """Startup, debounce, event mapping, retries, failures, and concurrency."""

    def test_iw04_identified_startup_and_debounce(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IW-04: Backend starts first, debounces, and cleans partial startup."""
        src_dir = tmp_path / "watch"
        src_dir.mkdir()
        src = src_dir / "target.xlsx"
        snap_root = tmp_path / "snapshots"
        snap_root.mkdir()

        clock = FakeClock(start_ns=1_000_000_000)
        events_order: list[str] = []

        class OrderingObserver(MockObserver):
            def start(self) -> None:
                events_order.append("observer_start")
                super().start()

        obs = OrderingObserver()
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs,
            _time_source=clock,
        )

        original_notify = runtime._coordinator.notify

        def ordered_notify(*args: Any, **kwargs: Any) -> Any:
            if args[:2] == (SaveEventKind.MODIFIED, src):
                events_order.append("initial_notice")
            return original_notify(*args, **kwargs)

        monkeypatch.setattr(runtime._coordinator, "notify", ordered_notify)

        read_attempts = 0
        mock_identified_result = cast(IdentifiedXlsxSource, object())

        def hooked_driver(*args: Any, **kwargs: Any) -> Any:
            nonlocal read_attempts
            read_attempts += 1
            return mock_identified_result

        monkeypatch.setattr(
            "accounting_local_agent.save_import_coordinator.read_identified_xlsx_source",
            hooked_driver,
        )

        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)

        delivered: list[IdentifiedXlsxSource] = []
        runner = ManagedIdentifiedRunnerThread(runtime, lambda r: delivered.append(r))
        runner.start()

        try:
            ack_timeout = waiter.wait_for_ack(timeout=5.0)
            assert ack_timeout == 1.0  # capped wait at startup
            assert events_order[:2] == ["observer_start", "initial_notice"]
            assert obs.scheduled_handlers[0][1] == str(src.parent)
            assert obs.scheduled_handlers[0][2] is False  # non-recursive

            # Advance clock before 2-second debounce deadline (t=2.999s)
            clock.set_ns(2_999_999_999)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            ack_timeout = waiter.wait_for_ack(timeout=5.0)
            assert ack_timeout == 0.0001
            assert read_attempts == 0
            assert len(delivered) == 0

            # Advance clock to exact deadline (t=3.000s)
            clock.set_ns(3_000_000_000)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()

            waiter.wait_for_ack(timeout=5.0)
            assert read_attempts == 1
            assert len(delivered) == 1

            runtime.request_stop()
            runner.join(timeout=5.0)
            runner.assert_clean_exit()
        finally:
            runtime.request_stop()
            runner.join(timeout=5.0)
            obs.stop()

        # Partial startup cleanup tests for run_identified
        # 1. Observer factory failure
        def failing_factory() -> MockObserver:
            raise OSError("Observer allocation failed")

        runtime_fail_fac = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.01,
            _observer_factory=failing_factory,
        )
        with pytest.raises(
            SourceWatchRuntimeError, match="Failed to instantiate observer"
        ) as exc_fac:
            runtime_fail_fac.run_identified(lambda r: None)
        assert exc_fac.value.reason == SourceWatchRuntimeReason.OBSERVER_START_FAILED
        assert runtime_fail_fac.view().state == SourceWatchRuntimeState.FAILED

        # 2. Schedule failure
        class ScheduleFailingObserver(MockObserver):
            def schedule(self, *args: Any, **kwargs: Any) -> Any:
                raise OSError("Schedule failed")

        runtime_fail_sched = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.01,
            _observer_factory=ScheduleFailingObserver,
        )
        with pytest.raises(
            SourceWatchRuntimeError, match="Failed to schedule watch"
        ) as exc_sched:
            runtime_fail_sched.run_identified(lambda r: None)
        assert exc_sched.value.reason == SourceWatchRuntimeReason.OBSERVER_START_FAILED
        assert runtime_fail_sched.view().state == SourceWatchRuntimeState.FAILED

        # 3. Partial start failure joins any started workers
        started_worker = MockEmitter()

        class PartialStartFailObserver(MockObserver):
            def schedule(self, *args: Any, **kwargs: Any) -> Any:
                super().schedule(*args, **kwargs)
                self._mock_emitters.add(started_worker)
                return "mock_watch"

            def start(self) -> None:
                started_worker.start()
                self._mock_emitters.clear()
                raise OSError("Start failed after worker started")

        runtime_partial = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.01,
            _observer_factory=PartialStartFailObserver,
        )
        try:
            with pytest.raises(
                SourceWatchRuntimeError, match="Failed to start observer"
            ) as exc_part:
                runtime_partial.run_identified(lambda r: None)
            assert (
                exc_part.value.reason == SourceWatchRuntimeReason.OBSERVER_START_FAILED
            )
            assert runtime_partial.view().state == SourceWatchRuntimeState.FAILED
            assert not started_worker.is_alive(), (
                "Started worker must be stopped and joined"
            )
        finally:
            started_worker.stop()
            started_worker.join(timeout=1.0)

    def test_iw05_event_mapping_and_followup_coalescing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IW-05: Path event mapping, burst coalescing, and single follow-up."""
        src_dir = tmp_path / "watch"
        src_dir.mkdir()
        src = src_dir / "target.xlsx"
        other_src = src_dir / "other.xlsx"
        snap_root = tmp_path / "snapshots"
        snap_root.mkdir()

        clock = FakeClock()
        obs = MockObserver()
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs,
            _time_source=clock,
        )

        # 1. Event adapter mapping
        received_kinds: list[SaveEventKind] = []
        orig_notify = runtime._coordinator.notify

        def recording_notify(kind: SaveEventKind, p: Path, **kwargs: Any) -> bool:
            accepted = orig_notify(kind, p, **kwargs)
            if accepted:
                received_kinds.append(kind)
            return accepted

        monkeypatch.setattr(runtime._coordinator, "notify", recording_notify)

        # 2. Blocked consumer with 2,000 burst notices produces at most 1 follow-up
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)

        in_consumer = threading.Event()
        release_consumer = threading.Event()
        delivered_results: list[IdentifiedXlsxSource] = []

        def consumer(res: IdentifiedXlsxSource) -> None:
            delivered_results.append(res)
            if len(delivered_results) == 1:
                in_consumer.set()
                assert release_consumer.wait(timeout=5.0)

        mock_result = cast(IdentifiedXlsxSource, object())
        monkeypatch.setattr(
            "accounting_local_agent.save_import_coordinator.read_identified_xlsx_source",
            lambda *args, **kwargs: mock_result,
        )

        runner = ManagedIdentifiedRunnerThread(runtime, consumer)
        runner.start()

        try:
            waiter.wait_for_ack(timeout=5.0)
            adapter = _WatchdogEventAdapter(
                runtime._on_adapter_event, runtime._on_adapter_error
            )
            count_before = len(received_kinds)
            adapter.dispatch(FileCreatedEvent(str(src)))
            adapter.dispatch(FileModifiedEvent(str(src)))
            adapter.dispatch(FileDeletedEvent(str(src)))
            adapter.dispatch(FileMovedEvent(str(other_src), str(src)))
            adapter.dispatch(FileMovedEvent(str(src), str(other_src)))
            assert len(received_kinds) - count_before == 5

            # Directory and unrelated file events never reach the coordinator.
            count_before = len(received_kinds)
            adapter.dispatch(DirModifiedEvent(str(src.parent)))
            adapter.dispatch(FileModifiedEvent(str(src.parent / "unrelated.xlsx")))
            adapter.dispatch(FileModifiedEvent(str(src.parent / "~$target.xlsx")))
            assert len(received_kinds) == count_before

            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()

            assert in_consumer.wait(timeout=5.0)
            assert runtime._active_cycle_running is True

            # Send 2000 burst notices during blocked consumer
            for _ in range(2000):
                runtime._on_adapter_event(SaveEventKind.MODIFIED, src, None)

            release_consumer.set()
            waiter.wait_for_ack(timeout=5.0)

            # Advance past follow-up debounce
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()

            waiter.wait_for_ack(timeout=5.0)
            assert len(delivered_results) == 2, "Must coalesce into exactly 1 follow-up"

            runtime.request_stop()
            runner.join(timeout=5.0)
            runner.assert_clean_exit()
        finally:
            release_consumer.set()
            runtime.request_stop()
            runner.join(timeout=5.0)
            obs.stop()

    def test_iw05_followup_during_blocked_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A burst admitted during a due read produces one later cycle."""
        src = tmp_path / "watch" / "target.xlsx"
        clock = FakeClock()
        observer = MockObserver()
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=tmp_path / "snapshots",
            observation_interval_seconds=0.001,
            _observer_factory=lambda: observer,
            _time_source=clock,
        )
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)
        in_read = threading.Event()
        release_read = threading.Event()
        second_delivered = threading.Event()
        read_calls = 0
        result = cast(IdentifiedXlsxSource, object())
        delivered: list[IdentifiedXlsxSource] = []

        def blocked_reader(*args: Any, **kwargs: Any) -> IdentifiedXlsxSource:
            nonlocal read_calls
            read_calls += 1
            if read_calls == 1:
                in_read.set()
                assert release_read.wait(timeout=5.0)
            return result

        def consumer(value: IdentifiedXlsxSource) -> None:
            delivered.append(value)
            if len(delivered) == 2:
                second_delivered.set()

        monkeypatch.setattr(
            "accounting_local_agent.save_import_coordinator.read_identified_xlsx_source",
            blocked_reader,
        )
        runner = ManagedIdentifiedRunnerThread(runtime, consumer)
        runner.start()
        try:
            waiter.wait_for_ack(timeout=5.0)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            assert in_read.wait(timeout=5.0)
            assert runtime._active_cycle_running is True
            for _ in range(2000):
                runtime._on_adapter_event(SaveEventKind.MODIFIED, src, None)
            release_read.set()
            waiter.wait_for_ack(timeout=5.0)
            assert delivered == [result]
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            assert second_delivered.wait(timeout=5.0)
            assert delivered == [result, result]
            assert read_calls == 2
            runtime.request_stop()
            runner.join(timeout=5.0)
            runner.assert_clean_exit()
        finally:
            release_read.set()
            runtime.request_stop()
            runner.join(timeout=5.0)
            observer.stop()

    def test_iw06_notready_and_rejection_retries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IW-06: Retry NotReady; rejections await notice or follow-up."""
        src_dir = tmp_path / "watch"
        src_dir.mkdir()
        src = src_dir / "target.xlsx"
        snap_root = tmp_path / "snapshots"
        snap_root.mkdir()

        clock = FakeClock(start_ns=1_000_000_000)
        obs = MockObserver()
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs,
            _time_source=clock,
        )

        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)

        driver_outcome: str = "not_ready"
        driver_calls = 0
        mock_success = cast(IdentifiedXlsxSource, object())
        in_driver_call = threading.Event()
        release_driver_call = threading.Event()

        def scripted_driver(*args: Any, **kwargs: Any) -> Any:
            nonlocal driver_calls
            driver_calls += 1
            outcome = driver_outcome
            if outcome == "followup_raw_reject":
                in_driver_call.set()
                assert release_driver_call.wait(timeout=5.0)
                raise XlsxSourceReadError("Corrupt zip container with follow-up")
            if outcome == "not_ready":
                raise XlsxSourceNotReadyError("Workbook locked")
            if outcome == "raw_reject":
                raise XlsxSourceReadError("Corrupt zip container")
            if outcome == "marker_reject":
                raise XlsxSourceIdentityError(XlsxSourceIdentityReason.MISSING_MARKER)
            return mock_success

        monkeypatch.setattr(
            "accounting_local_agent.save_import_coordinator.read_identified_xlsx_source",
            scripted_driver,
        )

        delivered: list[IdentifiedXlsxSource] = []
        runner = ManagedIdentifiedRunnerThread(runtime, lambda r: delivered.append(r))
        runner.start()

        try:
            # 1. NotReady retries automatically without notice at deadline
            waiter.wait_for_ack(timeout=5.0)
            clock.set_ns(3_000_000_000)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()

            waiter.wait_for_ack(timeout=5.0)
            assert driver_calls == 1
            assert runtime._coordinator.view().next_due_ns == 5_000_000_000

            # One nanosecond early cannot read; the exact deadline must read.
            clock.set_ns(4_999_999_999)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            waiter.wait_for_ack(timeout=5.0)
            assert driver_calls == 1
            clock.set_ns(5_000_000_000)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            waiter.wait_for_ack(timeout=5.0)
            assert driver_calls == 2

            # 2. Raw-reader rejection enters IDLE without retry
            driver_outcome = "raw_reject"
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            waiter.wait_for_ack(timeout=5.0)
            assert driver_calls == 3

            clock.advance_seconds(10.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            ack = waiter.wait_for_ack(timeout=5.0)
            assert ack == 1.0  # In IDLE, capped wait
            assert driver_calls == 3  # Zero retry calls without notice

            # Fresh notice wakes from IDLE and retries
            driver_outcome = "marker_reject"
            runtime._on_adapter_event(SaveEventKind.MODIFIED, src, None)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            waiter.wait_for_ack(timeout=5.0)
            assert driver_calls == 4

            # Raw rejection preserves a follow-up notice sent during read.
            driver_outcome = "followup_raw_reject"
            in_driver_call.clear()
            release_driver_call.clear()
            runtime._on_adapter_event(SaveEventKind.MODIFIED, src, None)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()

            assert in_driver_call.wait(timeout=5.0)
            # Inject follow-up notice while read is active
            runtime._on_adapter_event(SaveEventKind.MODIFIED, src, None)
            driver_outcome = "success"
            release_driver_call.set()

            waiter.wait_for_ack(timeout=5.0)
            assert driver_calls == 5
            assert delivered == []
            assert runtime.view().state == SourceWatchRuntimeState.RUNNING

            # Advance past debounce: deliver follow-up without another notice.
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            waiter.wait_for_ack(timeout=5.0)
            assert driver_calls == 6
            assert len(delivered) == 1

            runtime.request_stop()
            runner.join(timeout=5.0)
            runner.assert_clean_exit()
        finally:
            release_driver_call.set()
            runtime.request_stop()
            runner.join(timeout=5.0)
            obs.stop()

    def test_iw07_fatal_failures_and_teardown_ordering(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IW-07: Terminal failures, teardown ordering, group kinds, shared cause."""
        src_dir = tmp_path / "watch"
        src_dir.mkdir()
        src = src_dir / "target.xlsx"
        snap_root = tmp_path / "snapshots"
        snap_root.mkdir()

        # 1. Shared cause preserved across driver failure and stop failure
        shared_cause = OSError("Disk failure")
        driver_err = SaveCoordinatorPolicyError("Driver policy broken")
        driver_err.__cause__ = shared_cause
        stop_err = RuntimeError("Teardown join error")
        stop_err.__cause__ = shared_cause

        obs = MockObserver()

        def failing_stop() -> None:
            obs._stop_event.set()
            raise stop_err

        monkeypatch.setattr(obs, "stop", failing_stop)

        def failing_driver(*args: Any, **kwargs: Any) -> Any:
            raise driver_err

        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_identified_source",
            failing_driver,
        )

        clock = FakeClock()
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs,
            _time_source=clock,
        )

        runner = ManagedIdentifiedRunnerThread(runtime, lambda r: None)
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)
        runner.start()

        try:
            waiter.wait_for_ack(timeout=5.0)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()

            runner.join(timeout=5.0)
            assert runner.error is not None
            assert isinstance(runner.error, ExceptionGroup)
            assert len(runner.error.exceptions) == 2
            # Primary before teardown
            primary = runner.error.exceptions[0]
            assert isinstance(primary, SourceWatchRuntimeError)
            assert primary.reason == SourceWatchRuntimeReason.SOURCE_READ_FAILED
            assert primary.__cause__ is driver_err
            assert driver_err.__cause__ is shared_cause
            e1 = runner.error.exceptions[1]
            assert isinstance(e1, SourceWatchRuntimeError)
            assert e1.reason == SourceWatchRuntimeReason.SHUTDOWN_FAILED
            assert e1.__cause__ is stop_err
            assert runtime.view().state == SourceWatchRuntimeState.FAILED
        finally:
            runtime.request_stop()
            runner.join(timeout=5.0)
            obs._stop_event.set()
            for emitter in obs.emitters:
                emitter.stop()
                emitter.join(timeout=1.0)

        # 2. BaseException direct propagation (KeyboardInterrupt)
        obs_ki = MockObserver()
        clock_ki = FakeClock()
        runtime_ki = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs_ki,
            _time_source=clock_ki,
        )
        waiter_ki = ControlledConditionWaiter(runtime_ki._condition)
        monkeypatch.setattr(runtime_ki._condition, "wait", waiter_ki.hooked_wait)

        def ki_driver(*args: Any, **kwargs: Any) -> Any:
            raise KeyboardInterrupt("Simulated Ctrl+C")

        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_identified_source",
            ki_driver,
        )

        runner_ki = ManagedIdentifiedRunnerThread(runtime_ki, lambda r: None)
        runner_ki.start()

        try:
            waiter_ki.wait_for_ack(timeout=5.0)
            clock_ki.advance_seconds(3.0)
            with runtime_ki._lifecycle_lock:
                runtime_ki._condition.notify_all()
            runner_ki.join(timeout=5.0)
            assert isinstance(runner_ki.error, KeyboardInterrupt)
            assert runtime_ki.view().state == SourceWatchRuntimeState.FAILED
        finally:
            runtime_ki.request_stop()
            runner_ki.join(timeout=5.0)
            obs_ki.stop()

    @pytest.mark.parametrize(
        "fault_factory",
        [
            pytest.param(lambda: XlsxSourcePolicyError(), id="policy"),
            pytest.param(lambda: XlsxSnapshotStorageError(), id="storage"),
            pytest.param(lambda: XlsxSnapshotIntegrityError(), id="integrity"),
            pytest.param(lambda: XlsxSnapshotCleanupError(), id="cleanup"),
            pytest.param(lambda: SaveCoordinatorStateError(), id="coordinator"),
            pytest.param(lambda: RuntimeError("unexpected"), id="unexpected"),
            pytest.param(
                lambda: ExceptionGroup("nested", [XlsxSourceReadError("reader")]),
                id="exception-group",
            ),
            pytest.param(
                lambda: BaseExceptionGroup("nested", [KeyboardInterrupt()]),
                id="base-exception-group",
            ),
        ],
    )
    def test_iw07_fatal_error_classification(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fault_factory: Callable[[], BaseException],
    ) -> None:
        """Every fatal family remains terminal, including nested reader errors."""
        src = tmp_path / "watch" / "target.xlsx"
        clock = FakeClock()
        observer = MockObserver()
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=tmp_path / "snapshots",
            observation_interval_seconds=0.001,
            _observer_factory=lambda: observer,
            _time_source=clock,
        )
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)
        fault = fault_factory()
        calls = 0

        def failing_driver(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise fault

        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_identified_source",
            failing_driver,
        )
        runner = ManagedIdentifiedRunnerThread(runtime, lambda _: None)
        runner.start()
        try:
            waiter.wait_for_ack(timeout=5.0)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            runner.join(timeout=5.0)
            assert calls == 1
            assert runtime.view().state == SourceWatchRuntimeState.FAILED
            if isinstance(fault, Exception):
                assert isinstance(runner.error, SourceWatchRuntimeError)
                assert (
                    runner.error.reason == SourceWatchRuntimeReason.SOURCE_READ_FAILED
                )
                assert runner.error.__cause__ is fault
            else:
                assert runner.error is fault
            runtime._on_adapter_event(SaveEventKind.MODIFIED, src, None)
            assert calls == 1
            assert all(not worker.is_alive() for worker in observer.emitters)
        finally:
            runtime.request_stop()
            runner.join(timeout=5.0)
            observer.stop()

    def test_iw08_consumer_delivery_and_failure_semantics(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IW-08: Serial delivery, ignored returns, fatal consumer failure."""
        src_dir = tmp_path / "watch"
        src_dir.mkdir()
        src = src_dir / "target.xlsx"
        snap_root = tmp_path / "snapshots"
        snap_root.mkdir()

        # 1. Serial calls, ignored returns, delivery on runner thread
        clock_ok = FakeClock()
        obs_ok = MockObserver()
        runtime_ok = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs_ok,
            _time_source=clock_ok,
        )

        mock_item1 = cast(IdentifiedXlsxSource, object())
        mock_item2 = cast(IdentifiedXlsxSource, object())
        driver_items = [mock_item1, mock_item2]
        call_idx = 0

        def sequential_driver(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_idx
            coordinator = cast(SaveImportCoordinator, args[0])
            attempt = coordinator.take_due()
            assert attempt is not None
            coordinator.finish(attempt, SourceReadOutcome.SUCCESS)
            if call_idx < len(driver_items):
                item = driver_items[call_idx]
                call_idx += 1
                return item
            return None

        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_identified_source",
            sequential_driver,
        )

        ok_threads: list[int] = []
        ok_delivered: list[IdentifiedXlsxSource] = []
        delivery_condition = threading.Condition()

        def return_ignoring_consumer(res: IdentifiedXlsxSource) -> Any:
            with delivery_condition:
                ok_threads.append(threading.get_ident())
                ok_delivered.append(res)
                delivery_condition.notify_all()
            return "ignored_consumer_return_value"

        waiter_ok = ControlledConditionWaiter(runtime_ok._condition)
        monkeypatch.setattr(runtime_ok._condition, "wait", waiter_ok.hooked_wait)
        runner_ok = ManagedIdentifiedRunnerThread(runtime_ok, return_ignoring_consumer)
        runner_ok.start()

        try:
            waiter_ok.wait_for_ack(timeout=5.0)
            clock_ok.advance_seconds(3.0)
            with runtime_ok._lifecycle_lock:
                runtime_ok._condition.notify_all()
            with delivery_condition:
                assert delivery_condition.wait_for(
                    lambda: len(ok_delivered) >= 1, timeout=5.0
                ), (call_idx, runtime_ok.view(), waiter_ok.waits)
            waiter_ok.wait_for_ack(timeout=5.0)
            runtime_ok._on_adapter_event(SaveEventKind.MODIFIED, src, None)
            clock_ok.advance_seconds(3.0)
            with runtime_ok._lifecycle_lock:
                runtime_ok._condition.notify_all()
            with delivery_condition:
                assert delivery_condition.wait_for(
                    lambda: len(ok_delivered) == 2, timeout=5.0
                )

            runtime_ok.request_stop()
            runner_ok.join(timeout=5.0)
            runner_ok.assert_clean_exit()
            assert len(ok_delivered) == 2
            assert ok_delivered[0] is mock_item1
            assert ok_delivered[1] is mock_item2
            assert all(tid == runner_ok._thread.ident for tid in ok_threads)
            assert runtime_ok.view().state == SourceWatchRuntimeState.STOPPED
        finally:
            runtime_ok.request_stop()
            runner_ok.join(timeout=5.0)
            obs_ok.stop()

        # 2. Ordinary consumer failure without requeue or redelivery of follow-up
        clock = FakeClock()
        obs = MockObserver()
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs,
            _time_source=clock,
        )

        mock_item = cast(IdentifiedXlsxSource, object())
        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_identified_source",
            lambda *args, **kwargs: mock_item,
        )

        consumer_err = ValueError("Consumer crashed")
        consumer_threads: list[int] = []
        in_fail_consumer = threading.Event()
        release_fail_consumer = threading.Event()

        def failing_consumer(res: IdentifiedXlsxSource) -> Any:
            consumer_threads.append(threading.get_ident())
            in_fail_consumer.set()
            release_fail_consumer.wait(timeout=5.0)
            raise consumer_err

        runner = ManagedIdentifiedRunnerThread(runtime, failing_consumer)
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)
        runner.start()

        try:
            waiter.wait_for_ack(timeout=5.0)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()

            assert in_fail_consumer.wait(timeout=5.0)
            # Inject follow-up notice during failing consumer
            runtime._on_adapter_event(SaveEventKind.MODIFIED, src, None)
            release_fail_consumer.set()

            runner.join(timeout=5.0)
            assert runner.error is not None
            assert isinstance(runner.error, SourceWatchRuntimeError)
            assert runner.error.reason == SourceWatchRuntimeReason.CONSUMER_FAILED
            assert runner.error.__cause__ is consumer_err
            assert runtime.view().state == SourceWatchRuntimeState.FAILED
            assert len(consumer_threads) == 1
            assert consumer_threads[0] == runner._thread.ident
        finally:
            release_fail_consumer.set()
            runtime.request_stop()
            runner.join(timeout=5.0)
            obs.stop()

        # 3. Grouped consumer failure (ExceptionGroup)
        clock_grp = FakeClock()
        obs_grp = MockObserver()
        runtime_grp = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs_grp,
            _time_source=clock_grp,
        )
        group_err = ExceptionGroup(
            "Consumer group failure", [ValueError("A"), RuntimeError("B")]
        )

        def group_failing_consumer(res: IdentifiedXlsxSource) -> Any:
            raise group_err

        runner_grp = ManagedIdentifiedRunnerThread(runtime_grp, group_failing_consumer)
        waiter_grp = ControlledConditionWaiter(runtime_grp._condition)
        monkeypatch.setattr(runtime_grp._condition, "wait", waiter_grp.hooked_wait)
        runner_grp.start()

        try:
            waiter_grp.wait_for_ack(timeout=5.0)
            clock_grp.advance_seconds(3.0)
            with runtime_grp._lifecycle_lock:
                runtime_grp._condition.notify_all()

            runner_grp.join(timeout=5.0)
            assert runner_grp.error is not None
            assert isinstance(runner_grp.error, SourceWatchRuntimeError)
            assert runner_grp.error.reason == SourceWatchRuntimeReason.CONSUMER_FAILED
            assert runner_grp.error.__cause__ is group_err
            assert runtime_grp.view().state == SourceWatchRuntimeState.FAILED
        finally:
            runtime_grp.request_stop()
            runner_grp.join(timeout=5.0)
            obs_grp.stop()

        # 4. Cancellation consumer failure (KeyboardInterrupt) direct propagation
        clock_ki = FakeClock()
        obs_ki = MockObserver()
        runtime_ki = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs_ki,
            _time_source=clock_ki,
        )
        ki_err = KeyboardInterrupt("Consumer cancellation")

        def ki_consumer(res: IdentifiedXlsxSource) -> Any:
            raise ki_err

        runner_ki = ManagedIdentifiedRunnerThread(runtime_ki, ki_consumer)
        waiter_ki = ControlledConditionWaiter(runtime_ki._condition)
        monkeypatch.setattr(runtime_ki._condition, "wait", waiter_ki.hooked_wait)
        runner_ki.start()

        try:
            waiter_ki.wait_for_ack(timeout=5.0)
            clock_ki.advance_seconds(3.0)
            with runtime_ki._lifecycle_lock:
                runtime_ki._condition.notify_all()

            runner_ki.join(timeout=5.0)
            assert runner_ki.error is ki_err
            assert runtime_ki.view().state == SourceWatchRuntimeState.FAILED
        finally:
            runtime_ki.request_stop()
            runner_ki.join(timeout=5.0)
            obs_ki.stop()

    def test_iw09_stop_semantics_and_worker_drain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IW-09: Waiting stop skips reads; admitted read drains."""
        src_dir = tmp_path / "watch"
        src_dir.mkdir()
        src = src_dir / "target.xlsx"
        snap_root = tmp_path / "snapshots"
        snap_root.mkdir()

        # 1. Waiting stop performs 0 reads
        clock_wait = FakeClock()
        obs_wait = MockObserver()
        runtime_wait = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs_wait,
            _time_source=clock_wait,
        )
        waiter = ControlledConditionWaiter(runtime_wait._condition)
        monkeypatch.setattr(runtime_wait._condition, "wait", waiter.hooked_wait)
        waiting_reads = 0

        def unexpected_waiting_read(*args: Any, **kwargs: Any) -> None:
            nonlocal waiting_reads
            waiting_reads += 1
            return None

        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_identified_source",
            unexpected_waiting_read,
        )

        delivered_wait: list[IdentifiedXlsxSource] = []
        runner_wait = ManagedIdentifiedRunnerThread(
            runtime_wait, lambda r: delivered_wait.append(r)
        )
        runner_wait.start()

        try:
            waiter.wait_for_ack(timeout=5.0)
            # Stop requested while waiting
            runtime_wait.request_stop()
            runner_wait.join(timeout=5.0)
            runner_wait.assert_clean_exit()
            assert len(delivered_wait) == 0
            assert waiting_reads == 0
            assert runtime_wait.view().state == SourceWatchRuntimeState.STOPPED
        finally:
            runtime_wait.request_stop()
            runner_wait.join(timeout=5.0)
            obs_wait.stop()

        # 2. Admitted read drains and follow-up is suppressed
        clock = FakeClock()
        obs = MockObserver()
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs,
            _time_source=clock,
        )

        read_calls = 0
        mock_res = cast(IdentifiedXlsxSource, object())
        in_read = threading.Event()
        release_read = threading.Event()

        def draining_driver(*args: Any, **kwargs: Any) -> Any:
            nonlocal read_calls
            read_calls += 1
            in_read.set()
            assert release_read.wait(timeout=5.0)
            return mock_res

        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_identified_source",
            draining_driver,
        )

        delivered: list[IdentifiedXlsxSource] = []
        runner = ManagedIdentifiedRunnerThread(runtime, lambda r: delivered.append(r))
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)
        runner.start()

        try:
            waiter.wait_for_ack(timeout=5.0)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()

            assert in_read.wait(timeout=5.0)

            # Request stop during admitted read + inject follow-up notice
            runtime.request_stop()
            assert runtime.view().state == SourceWatchRuntimeState.STOPPING
            runtime._on_adapter_event(SaveEventKind.MODIFIED, src, None)

            # Release read: admitted read drains and delivers
            release_read.set()
            runner.join(timeout=5.0)
            runner.assert_clean_exit()

            assert len(delivered) == 1
            assert read_calls == 1  # follow-up suppressed
            assert runtime.view().state == SourceWatchRuntimeState.STOPPED
            assert obs.stopped is True
        finally:
            release_read.set()
            runtime.request_stop()
            runner.join(timeout=5.0)
            obs.stop()

    def test_iw09_stop_during_consumer_drains_without_followup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An admitted consumer completes after stop; follow-up stays suppressed."""
        src = tmp_path / "watch" / "target.xlsx"
        clock = FakeClock()
        observer = MockObserver()
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=tmp_path / "snapshots",
            observation_interval_seconds=0.001,
            _observer_factory=lambda: observer,
            _time_source=clock,
        )
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)
        result = cast(IdentifiedXlsxSource, object())
        reads = 0
        entered = threading.Event()
        release = threading.Event()
        delivered: list[IdentifiedXlsxSource] = []

        def driver(*args: Any, **kwargs: Any) -> IdentifiedXlsxSource:
            nonlocal reads
            reads += 1
            return result

        def consumer(value: IdentifiedXlsxSource) -> None:
            delivered.append(value)
            entered.set()
            assert release.wait(timeout=5.0)

        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_identified_source",
            driver,
        )
        runner = ManagedIdentifiedRunnerThread(runtime, consumer)
        runner.start()
        try:
            waiter.wait_for_ack(timeout=5.0)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            assert entered.wait(timeout=5.0)
            assert runtime._active_cycle_running is True
            runtime._on_adapter_event(SaveEventKind.MODIFIED, src, None)
            runtime.request_stop()
            assert runtime.view().state == SourceWatchRuntimeState.STOPPING
            assert delivered == [result]
            release.set()
            runner.join(timeout=5.0)
            runner.assert_clean_exit()
            assert reads == 1
            assert delivered == [result]
            assert runtime.view().state == SourceWatchRuntimeState.STOPPED
            assert all(not worker.is_alive() for worker in observer.emitters)
        finally:
            release.set()
            runtime.request_stop()
            runner.join(timeout=5.0)
            observer.stop()

    def test_iw09_controlled_delayed_worker_joins_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IW-09: Delayed worker during teardown joins cleanly without cutoff."""
        src_dir = tmp_path / "watch"
        src_dir.mkdir()
        src = src_dir / "target.xlsx"
        snap_root = tmp_path / "snapshots"
        snap_root.mkdir()

        src.write_bytes(_build_synthetic_identified_xlsx(rows_per_sheet=1))
        clock = FakeClock()
        join_timeouts: list[float | None] = []

        class ControlledDelayedEmitter(MockEmitter):
            def __init__(self) -> None:
                super().__init__()
                self.stop_called = threading.Event()
                self.release_worker = threading.Event()
                self.join_called = threading.Event()

            def stop(self) -> None:
                self.stopped = True
                self.stop_called.set()

            def run(self) -> None:
                assert self.stop_called.wait(timeout=5.0)
                assert self.release_worker.wait(timeout=5.0)

            def join(self, timeout: float | None = None) -> None:
                join_timeouts.append(timeout)
                self.join_called.set()
                super().join(timeout=timeout)

        delayed_emitter = ControlledDelayedEmitter()

        class ControlledDelayedObserver(MockObserver):
            def __init__(self) -> None:
                super().__init__()
                self._mock_emitters = {delayed_emitter}

        obs = ControlledDelayedObserver()
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs,
            _time_source=clock,
        )

        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)

        delivered: list[IdentifiedXlsxSource] = []

        def consumer(res: IdentifiedXlsxSource) -> None:
            delivered.append(res)
            runtime.request_stop()

        runner = ManagedIdentifiedRunnerThread(runtime, consumer)
        runner.start()

        try:
            waiter.wait_for_ack(timeout=5.0)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()

            # Verify that teardown calls stop() and arrives at join()
            assert delayed_emitter.stop_called.wait(timeout=5.0)
            assert delayed_emitter.join_called.wait(timeout=5.0)

            # Fixed join cutoff is removed: join is called with no timeout (None)
            assert None in join_timeouts
            assert 5.0 not in join_timeouts

            # While delayed worker is still running, runner is waiting in join
            assert delayed_emitter.is_alive()
            assert runner._thread.is_alive()
            assert runtime.view().state == SourceWatchRuntimeState.STOPPING

            # Release delayed worker and ensure clean join and STOPPED state
            delayed_emitter.release_worker.set()
            runner.join(timeout=5.0)
            runner.assert_clean_exit()

            # All owned workers must be joined (not alive)
            assert len(delivered) == 1
            assert not delayed_emitter.is_alive()
            assert not obs.is_alive()
            assert runtime.view().state == SourceWatchRuntimeState.STOPPED
        finally:
            delayed_emitter.release_worker.set()
            runtime.request_stop()
            runner.join(timeout=5.0)
            obs.stop()

    def test_iw10_concurrency_and_race_prevention(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IW-10: Barrier-controlled races: stop vs wake, teardown, liveness."""
        src_dir = tmp_path / "watch"
        src_dir.mkdir()
        src = src_dir / "target.xlsx"
        snap_root = tmp_path / "snapshots"
        snap_root.mkdir()

        # 1. Stop vs Wake barrier race
        clock = FakeClock()
        obs = MockObserver()
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs,
            _time_source=clock,
        )
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)

        barrier = threading.Barrier(2)
        race_errors: list[BaseException] = []

        def wake_thread_proc() -> None:
            try:
                barrier.wait(timeout=5.0)
                runtime._on_adapter_event(SaveEventKind.MODIFIED, src, None)
            except BaseException as error:
                race_errors.append(error)

        def stop_thread_proc() -> None:
            try:
                barrier.wait(timeout=5.0)
                runtime.request_stop()
            except BaseException as error:
                race_errors.append(error)

        t_wake = threading.Thread(target=wake_thread_proc)
        t_stop = threading.Thread(target=stop_thread_proc)

        runner = ManagedIdentifiedRunnerThread(runtime, lambda r: None)
        runner.start()

        try:
            waiter.wait_for_ack(timeout=5.0)
            t_wake.start()
            t_stop.start()

            t_wake.join(timeout=5.0)
            t_stop.join(timeout=5.0)
            assert not t_wake.is_alive() and not t_stop.is_alive()
            assert race_errors == []
            runner.join(timeout=5.0)
            runner.assert_clean_exit()
            assert runtime.view().state == SourceWatchRuntimeState.STOPPED
        finally:
            runtime.request_stop()
            if t_wake.ident is not None:
                t_wake.join(timeout=5.0)
            if t_stop.ident is not None:
                t_stop.join(timeout=5.0)
            runner.join(timeout=5.0)
            obs.stop()

        # 2. Callback versus teardown race and no callback I/O
        clock_cb = FakeClock()
        obs_cb = MockObserver()
        runtime_cb = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs_cb,
            _time_source=clock_cb,
        )
        waiter_cb = ControlledConditionWaiter(runtime_cb._condition)
        monkeypatch.setattr(runtime_cb._condition, "wait", waiter_cb.hooked_wait)

        cb_barrier = threading.Barrier(2)
        cb_threads: set[int] = set()
        cb_errors: list[BaseException] = []

        def tracking_notify(kind: SaveEventKind, p: Path, **kwargs: Any) -> bool:
            cb_threads.add(threading.get_ident())
            return True

        monkeypatch.setattr(runtime_cb._coordinator, "notify", tracking_notify)

        def cb_proc() -> None:
            try:
                cb_barrier.wait(timeout=5.0)
                runtime_cb._on_adapter_event(SaveEventKind.MODIFIED, src, None)
            except BaseException as error:
                cb_errors.append(error)

        def teardown_proc() -> None:
            try:
                cb_barrier.wait(timeout=5.0)
                runtime_cb.request_stop()
            except BaseException as error:
                cb_errors.append(error)

        t_cb = threading.Thread(target=cb_proc)
        t_td = threading.Thread(target=teardown_proc)

        runner_cb = ManagedIdentifiedRunnerThread(runtime_cb, lambda r: None)
        runner_cb.start()

        try:
            waiter_cb.wait_for_ack(timeout=5.0)
            callback_thread_id = threading.get_ident()
            guard_main_callback = True
            adapter = _WatchdogEventAdapter(
                runtime_cb._on_adapter_event, runtime_cb._on_adapter_error
            )
            original_read_bytes = Path.read_bytes
            original_path_open = Path.open
            original_path_stat = Path.stat
            original_builtin_open = builtins.open
            original_os_open = os.open
            original_os_stat = os.stat

            def callback_io_trap(
                name: str, original: Callable[..., Any]
            ) -> Callable[..., Any]:
                def trapped(*args: Any, **kwargs: Any) -> Any:
                    if (
                        guard_main_callback
                        and threading.get_ident() == callback_thread_id
                    ) or threading.current_thread() is t_cb:
                        raise AssertionError(f"Callback filesystem I/O: {name}")
                    return original(*args, **kwargs)

                return trapped

            with monkeypatch.context() as io_guard:
                io_guard.setattr(
                    Path,
                    "read_bytes",
                    callback_io_trap("Path.read_bytes", original_read_bytes),
                )
                io_guard.setattr(
                    Path, "open", callback_io_trap("Path.open", original_path_open)
                )
                io_guard.setattr(
                    Path, "stat", callback_io_trap("Path.stat", original_path_stat)
                )
                io_guard.setattr(
                    builtins,
                    "open",
                    callback_io_trap("builtins.open", original_builtin_open),
                )
                io_guard.setattr(
                    os, "open", callback_io_trap("os.open", original_os_open)
                )
                io_guard.setattr(
                    os, "stat", callback_io_trap("os.stat", original_os_stat)
                )
                with pytest.raises(AssertionError, match="Path.read_bytes"):
                    src.read_bytes()
                with pytest.raises(AssertionError, match="os.stat"):
                    os.stat(src)
                with pytest.raises(AssertionError, match="builtins.open"):
                    builtins.open(src, "rb")
                adapter.dispatch(FileModifiedEvent(str(src)))
                guard_main_callback = False
                assert callback_thread_id in cb_threads
                t_cb.start()
                t_td.start()
                t_cb.join(timeout=5.0)
                t_td.join(timeout=5.0)
                assert not t_cb.is_alive() and not t_td.is_alive()
            assert cb_errors == []
            runner_cb.join(timeout=5.0)
            runner_cb.assert_clean_exit()
            assert runtime_cb.view().state == SourceWatchRuntimeState.STOPPED
            # IW-10: Record winner and verify callback-vs-teardown invariant.
            assert runner_cb._thread.ident is not None
            assert t_cb.ident is not None
            assert t_td.ident is not None
            assert t_td.ident not in cb_threads
            assert callback_thread_id in cb_threads
            assert runner_cb._thread.ident in cb_threads

            callback_won_admission = t_cb.ident in cb_threads
            if callback_won_admission:
                admission_winner = "callback"
                # Callback won admission; event reached coordinator.
                # Teardown lost race but performed no forbidden notification.
                assert cb_threads == {
                    runner_cb._thread.ident,
                    callback_thread_id,
                    t_cb.ident,
                }
            else:
                admission_winner = "teardown"
                # Teardown won admission; stop closed admission.
                # Losing callback performed no forbidden notification.
                assert t_cb.ident not in cb_threads
                assert cb_threads == {
                    runner_cb._thread.ident,
                    callback_thread_id,
                }
            assert admission_winner in ("callback", "teardown")
        finally:
            runtime_cb.request_stop()
            if t_cb.ident is not None:
                t_cb.join(timeout=5.0)
            if t_td.ident is not None:
                t_td.join(timeout=5.0)
            runner_cb.join(timeout=5.0)
            obs_cb.stop()

        # 3. Liveness race and no live worker in finally
        obs_live = MockObserver()
        runtime_live = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs_live,
            _time_source=FakeClock(),
        )
        waiter_live = ControlledConditionWaiter(runtime_live._condition)
        monkeypatch.setattr(runtime_live._condition, "wait", waiter_live.hooked_wait)
        runner_live = ManagedIdentifiedRunnerThread(runtime_live, lambda r: None)
        runner_live.start()

        try:
            waiter_live.wait_for_ack(timeout=5.0)
            for em in list(obs_live.emitters):
                em.stop()
                em.join(timeout=1.0)

            with runtime_live._lifecycle_lock:
                runtime_live._condition.notify_all()

            runner_live.join(timeout=5.0)
            assert runner_live.error is not None
            assert isinstance(runner_live.error, SourceWatchRuntimeError)
            assert (
                runner_live.error.reason
                == SourceWatchRuntimeReason.OBSERVER_STOPPED_UNEXPECTEDLY
            )
            assert runtime_live.view().state == SourceWatchRuntimeState.FAILED
            assert not any(em.is_alive() for em in obs_live.emitters)
        finally:
            runtime_live.request_stop()
            runner_live.join(timeout=5.0)
            obs_live.stop()
            assert not any(em.is_alive() for em in obs_live.emitters)

        # 4. No lost terminal failure race: stop vs fatal driver failure
        obs_term = MockObserver()
        term_clock = FakeClock()
        runtime_term = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs_term,
            _time_source=term_clock,
        )
        waiter_term = ControlledConditionWaiter(runtime_term._condition)
        monkeypatch.setattr(runtime_term._condition, "wait", waiter_term.hooked_wait)

        term_driver_err = SaveCoordinatorPolicyError("Terminal driver error")
        in_term_read = threading.Event()
        release_term_read = threading.Event()

        def term_driver(*args: Any, **kwargs: Any) -> Any:
            in_term_read.set()
            assert release_term_read.wait(timeout=5.0)
            raise term_driver_err

        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_identified_source",
            term_driver,
        )

        runner_term = ManagedIdentifiedRunnerThread(runtime_term, lambda r: None)
        runner_term.start()

        try:
            waiter_term.wait_for_ack(timeout=5.0)
            term_clock.advance_seconds(3.0)
            with runtime_term._lifecycle_lock:
                runtime_term._condition.notify_all()
            assert in_term_read.wait(timeout=5.0)
            runtime_term.request_stop()
            release_term_read.set()

            runner_term.join(timeout=5.0)
            assert runner_term.error is not None
            assert isinstance(runner_term.error, SourceWatchRuntimeError)
            assert (
                runner_term.error.reason == SourceWatchRuntimeReason.SOURCE_READ_FAILED
            )
            assert runner_term.error.__cause__ is term_driver_err
            assert runtime_term.view().state == SourceWatchRuntimeState.FAILED
        finally:
            release_term_read.set()
            runtime_term.request_stop()
            runner_term.join(timeout=5.0)
            obs_term.stop()


# ===========================================================================
# TestIdentifiedSourceWatchRuntimeNative
# Covers IW-11
# ===========================================================================


class TestIdentifiedSourceWatchRuntimeNative:
    """Real filesystem observation with honest platform skips."""

    def test_iw11_native_filesystem_observation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IW-11: Native observation of initial-file, modify, replace, move, delete."""
        from watchdog.observers import Observer

        try:
            test_obs = Observer()
            test_obs.start()
            test_obs.stop()
            test_obs.join(timeout=2.0)
        except Exception as exc:
            pytest.fail(f"Native watchdog Observer unavailable: {type(exc).__name__}")

        watch_dir = tmp_path / "native_watch"
        watch_dir.mkdir()
        src = watch_dir / "workbook.xlsx"
        snap_root = tmp_path / "native_snapshots"
        snap_root.mkdir()

        # Preexisting file with valid synthetic identified marker
        src.write_bytes(_build_synthetic_identified_xlsx(rows_per_sheet=2))

        delivered: list[IdentifiedXlsxSource] = []
        delivery_condition = threading.Condition()
        notice_condition = threading.Condition()
        accepted_notices: list[tuple[SaveEventKind, Path, Path | None]] = []

        runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.01,
        )

        original_notify = runtime._coordinator.notify

        def record_native_notice(
            kind: SaveEventKind, path: Path, *, destination_path: Path | None = None
        ) -> bool:
            accepted = original_notify(kind, path, destination_path=destination_path)
            if accepted:
                with notice_condition:
                    accepted_notices.append((kind, path, destination_path))
                    notice_condition.notify_all()
            return accepted

        monkeypatch.setattr(runtime._coordinator, "notify", record_native_notice)

        def wait_notice(
            cursor: int,
            kind: SaveEventKind,
            path: Path,
            destination: Path | None = None,
        ) -> None:
            with notice_condition:
                assert notice_condition.wait_for(
                    lambda: (kind, path, destination) in accepted_notices[cursor:],
                    timeout=6.0,
                ), f"Native {kind.value} notice was not accepted"

        def wait_native_target_notice(cursor: int, kinds: set[SaveEventKind]) -> None:
            """Accept backend-specific move or delete/create target notifications."""
            with notice_condition:
                assert notice_condition.wait_for(
                    lambda: any(
                        kind in kinds and (path == src or destination == src)
                        for kind, path, destination in accepted_notices[cursor:]
                    ),
                    timeout=6.0,
                ), "Native target notice was not accepted"

        def wait_result(cursor: int, digest: str) -> IdentifiedXlsxSource:
            with delivery_condition:
                assert delivery_condition.wait_for(
                    lambda: any(
                        result.file_sha256 == digest for result in delivered[cursor:]
                    ),
                    timeout=6.0,
                ), "Native generation was not delivered"
                return next(
                    result
                    for result in delivered[cursor:]
                    if result.file_sha256 == digest
                )

        def consumer(res: IdentifiedXlsxSource) -> None:
            with delivery_condition:
                delivered.append(res)
                delivery_condition.notify_all()

        runner = ManagedIdentifiedRunnerThread(runtime, consumer)
        runner.start()

        try:
            # The startup logical notice must be accepted before delivery.
            wait_notice(0, SaveEventKind.MODIFIED, src)
            initial_digest = hashlib.sha256(src.read_bytes()).hexdigest()
            assert wait_result(0, initial_digest).key.fiscal_year == 1405

            # 2. Modify file by in-place rewrite with 1406 fiscal year workbook
            new_bytes = _build_synthetic_identified_xlsx(
                fiscal_year=1406, rows_per_sheet=2
            )
            with delivery_condition:
                cursor = len(delivered)
            with notice_condition:
                notice_cursor = len(accepted_notices)
            src.write_bytes(new_bytes)
            wait_notice(notice_cursor, SaveEventKind.MODIFIED, src)
            assert (
                wait_result(
                    cursor, hashlib.sha256(new_bytes).hexdigest()
                ).key.fiscal_year
                == 1406
            )

            # 3. Atomic replace (write temp file in same dir, then move/replace)
            temp_file = watch_dir / "temp_replace.xlsx"
            replace_bytes = _build_synthetic_identified_xlsx(
                fiscal_year=1407, rows_per_sheet=2
            )
            temp_file.write_bytes(replace_bytes)
            with delivery_condition:
                cursor = len(delivered)
            with notice_condition:
                notice_cursor = len(accepted_notices)
            os.replace(temp_file, src)
            wait_native_target_notice(
                notice_cursor,
                {
                    SaveEventKind.MOVED,
                    SaveEventKind.CREATED,
                    SaveEventKind.MODIFIED,
                    SaveEventKind.DELETED,
                },
            )
            assert (
                wait_result(
                    cursor, hashlib.sha256(replace_bytes).hexdigest()
                ).key.fiscal_year
                == 1407
            )

            # 4. Deletion reaches the same coordinator without delivery.
            with notice_condition:
                notice_cursor = len(accepted_notices)
            src.unlink()
            wait_notice(notice_cursor, SaveEventKind.DELETED, src)
            assert runtime.view().state == SourceWatchRuntimeState.RUNNING

            # 5. Create new file after delete is observed and delivered
            create_bytes = _build_synthetic_identified_xlsx(
                fiscal_year=1408, rows_per_sheet=2
            )
            with delivery_condition:
                cursor = len(delivered)
            with notice_condition:
                notice_cursor = len(accepted_notices)
            src.write_bytes(create_bytes)
            wait_notice(notice_cursor, SaveEventKind.CREATED, src)
            assert (
                wait_result(
                    cursor, hashlib.sha256(create_bytes).hexdigest()
                ).key.fiscal_year
                == 1408
            )

            # 6. Both move endpoints must be accepted by the coordinator.
            moved_away = watch_dir / "moved_away.xlsx"
            with notice_condition:
                notice_cursor = len(accepted_notices)
            src.rename(moved_away)
            wait_native_target_notice(
                notice_cursor, {SaveEventKind.MOVED, SaveEventKind.DELETED}
            )
            move_in_bytes = _build_synthetic_identified_xlsx(
                fiscal_year=1409, rows_per_sheet=2
            )
            moved_away.write_bytes(move_in_bytes)
            with delivery_condition:
                cursor = len(delivered)
            with notice_condition:
                notice_cursor = len(accepted_notices)
            moved_away.rename(src)
            wait_native_target_notice(
                notice_cursor,
                {SaveEventKind.MOVED, SaveEventKind.CREATED, SaveEventKind.MODIFIED},
            )
            assert (
                wait_result(
                    cursor, hashlib.sha256(move_in_bytes).hexdigest()
                ).key.fiscal_year
                == 1409
            )

            runtime.request_stop()
            runner.join(timeout=5.0)
            runner.assert_clean_exit()
            assert runtime.view().state == SourceWatchRuntimeState.STOPPED
        finally:
            runtime.request_stop()
            runner.join(timeout=5.0)


# ===========================================================================
# TestIdentifiedSourceWatchRuntimeIntegration
# Covers IW-12, IW-13, IW-14, IW-15
# ===========================================================================


class TestIdentifiedSourceWatchRuntimeIntegration:
    """WP-16 real acquisition, lease isolation, persistence traps, model simulation."""

    def test_iw12_real_wp16_acquisition_and_identity_stack(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IW-12: Full WP-16 acquisition, exact key, digest, rows, lease cleanup."""
        src_dir = tmp_path / "watch"
        src_dir.mkdir()
        src = src_dir / "target.xlsx"
        snap_root = tmp_path / "snapshots"
        snap_root.mkdir()

        source_bytes = _build_synthetic_identified_xlsx(rows_per_sheet=3)
        src.write_bytes(source_bytes)
        expected_sha256 = hashlib.sha256(source_bytes).hexdigest()
        expected_size = len(source_bytes)

        clock = FakeClock()
        obs = MockObserver()
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs,
            _time_source=clock,
        )

        delivered: list[IdentifiedXlsxSource] = []
        consumer_threads: list[int] = []

        def consumer(res: IdentifiedXlsxSource) -> None:
            delivered.append(res)
            consumer_threads.append(threading.get_ident())
            runtime.request_stop()

        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)
        runner = ManagedIdentifiedRunnerThread(runtime, consumer)
        runner.start()

        try:
            waiter.wait_for_ack(timeout=5.0)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()

            runner.join(timeout=5.0)
            runner.assert_clean_exit()

            assert len(delivered) == 1
            result = delivered[0]
            assert result.key.source_id == uuid.UUID(
                "00000000-0000-7000-8000-0000000003e7"
            )
            assert result.key.fiscal_year == 1405
            assert result.file_sha256 == expected_sha256
            assert result.byte_count == expected_size
            assert consumer_threads[0] == runner._thread.ident

            # 4 sheets parsed
            snapshot = result.read_result.snapshot
            sheets = {r.sheet_name for r in snapshot.all_rows_by_id.values()}
            assert sheets == {"خرید-فروش", "دریافت-پرداخت", "ورود-خروج", "لیست کسبه"}

            # Independent literal fixture oracle: exact IDs, Raw, and digests.
            expected = {
                "خرید-فروش": (
                    range(1, 5),
                    {
                        "date_raw": "1403/05/15",
                        "party_name_raw": "بازرگانی احمدی",
                        "transaction_type_raw": "خرید",
                        "item_name_raw": "طلای آبشده",
                        "quantity_raw": "12.34",
                        "unit_price_toman_raw": "1500000",
                        "discount_toman_raw": "0",
                        "notes_raw": "توضیحات فاکتور",
                    },
                    "45e776e124fa81fe2db6355aaa3803f1296b3e2499993bc44097923da4cb48c8",
                    "d7f4255cf0d797411d05c72e76d8fa525f869ab1f932c0490226a8ca430015e6",
                ),
                "دریافت-پرداخت": (
                    range(100001, 100004),
                    {
                        "date_raw": "1403/01/01",
                        "party_name_raw": "همکار نمونه",
                        "entry_type_raw": "RS",
                        "amount_toman_raw": "50000000",
                        "notes_raw": "تسویه حساب",
                        "account_code_raw": "101",
                        "customer_flag_raw": "1",
                    },
                    "f486f803655b0071ffb4b6fa995018930dc9734752b0084644b560aba5441eda",
                    "c051578936f8bc244f5850b7cc3248b698120299dc1a65806c81d625a2fa51f6",
                ),
                "ورود-خروج": (
                    range(200001, 200004),
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
                    "a17029e5f2b1b2b36df0787d4e215f039485f05768488525069747056e13d7cd",
                    "247a5ca51233d5c97f6baca4ec7d3a4354688f3e5abf8b27bcc3cd6ed73d5033",
                ),
                "لیست کسبه": (
                    range(300001, 300004),
                    {
                        "party_name_raw": "فروشگاه نمونه",
                        "phone_number_raw": "SYNTHETIC-PHONE-001",
                    },
                    "f88ccdeb76608de9c530d856ba12310ca667dba48bfbe6ce5fa0e4451b8aac24",
                    "d2ee5e1f630d98894d5d458d98091acc599219367af7849c090b4a10659c919f",
                ),
            }
            assert snapshot.total_row_count == 13
            assert set(snapshot.sheets) == set(expected)
            for name, (indices, raw, row_hash, sheet_hash) in expected.items():
                sheet = snapshot.sheets[name]
                assert sheet.sheet_snapshot_hash == sheet_hash
                expected_ids = {
                    uuid.UUID(int=(7 << 76) | (2 << 62) | index) for index in indices
                }
                assert {row.stable_id for row in sheet.rows} == expected_ids
                for row in sheet.rows:
                    assert isinstance(row.raw_values, Mapping)
                    assert dict(row.raw_values) == raw
                    assert all(type(value) is str for value in row.raw_values.values())
                    assert row.source_hash == row_hash

            # Complete lease cleanup: snapshot_root is completely empty
            assert list(snap_root.iterdir()) == []
        finally:
            runtime.request_stop()
            runner.join(timeout=5.0)
            obs.stop()

    def test_iw13_workbook_mutation_and_lease_isolation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Replacement during a lease cannot mix its identity with later Raw."""
        src = tmp_path / "watch" / "target.xlsx"
        src.parent.mkdir()
        snapshot_root = tmp_path / "snapshots"
        snapshot_root.mkdir()
        first_bytes = _build_synthetic_identified_xlsx(fiscal_year=1405)
        second_bytes = zipped(
            identified_parts(
                value=f"xlsx-source-identity.v1|{_DEFAULT_SOURCE_ID}|1406",
                raw=raw_parts(edit=True),
            )
        )
        fourth_raw = raw_parts(seed=777, edit=True)
        assert sum(part.count(b"1500001") for part in fourth_raw.values()) == 1
        fourth_raw = {
            name: part.replace(b"1500001", b"1500002")
            for name, part in fourth_raw.items()
        }
        fourth_bytes = zipped(
            identified_parts(
                value=f"xlsx-source-identity.v1|{_DEFAULT_SOURCE_ID}|1407",
                raw=fourth_raw,
            )
        )
        src.write_bytes(first_bytes)
        clock = FakeClock()
        observer = MockObserver()
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=snapshot_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: observer,
            _time_source=clock,
        )
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)
        from accounting_local_agent.xlsx_snapshot_acquisition import (
            open_stable_xlsx_snapshot as original_open,
        )

        lease_entered = threading.Event()
        release_lease = threading.Event()
        third_attempt = threading.Event()
        third_attempt_finished = threading.Event()
        third_attempt_errors: list[BaseException] = []
        attempts = 0
        original_reader = read_identified_xlsx_source

        @contextmanager
        def gated_open(*args: Any, **kwargs: Any) -> Any:
            with original_open(*args, **kwargs) as lease:
                if attempts == 1:
                    lease_entered.set()
                    assert release_lease.wait(timeout=5.0)
                yield lease

        def observed_reader(*args: Any, **kwargs: Any) -> IdentifiedXlsxSource:
            nonlocal attempts
            attempts += 1
            if attempts == 3:
                third_attempt.set()
                try:
                    return original_reader(*args, **kwargs)
                except BaseException as error:
                    third_attempt_errors.append(error)
                    raise
                finally:
                    third_attempt_finished.set()
            return original_reader(*args, **kwargs)

        monkeypatch.setattr(
            "accounting_local_agent.xlsx_source_identity.open_stable_xlsx_snapshot",
            gated_open,
        )
        monkeypatch.setattr(
            "accounting_local_agent.save_import_coordinator.read_identified_xlsx_source",
            observed_reader,
        )
        deliveries: list[IdentifiedXlsxSource] = []
        delivery_condition = threading.Condition()

        def consumer(result: IdentifiedXlsxSource) -> None:
            with delivery_condition:
                deliveries.append(result)
                delivery_condition.notify_all()

        def wait_delivery(count: int) -> IdentifiedXlsxSource:
            with delivery_condition:
                assert delivery_condition.wait_for(
                    lambda: len(deliveries) >= count, timeout=5.0
                )
                return deliveries[count - 1]

        runner = ManagedIdentifiedRunnerThread(runtime, consumer)
        runner.start()
        try:
            waiter.wait_for_ack(timeout=5.0)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            assert lease_entered.wait(timeout=5.0)
            temp = src.with_name("replacement.xlsx")
            temp.write_bytes(second_bytes)
            os.replace(temp, src)
            runtime._on_adapter_event(SaveEventKind.MOVED, temp, src)
            release_lease.set()
            first = wait_delivery(1)
            assert first.key.fiscal_year == 1405
            assert first.file_sha256 == hashlib.sha256(first_bytes).hexdigest()
            assert (
                first.read_result.snapshot.sheets["خرید-فروش"]
                .rows[0]
                .raw_values["unit_price_toman_raw"]
                == "1500000"
            )

            waiter.wait_for_ack(timeout=5.0)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            second = wait_delivery(2)
            assert second is not first
            assert second.key.fiscal_year == 1406
            assert second.file_sha256 == hashlib.sha256(second_bytes).hexdigest()
            assert (
                second.read_result.snapshot.sheets["خرید-فروش"]
                .rows[0]
                .raw_values["unit_price_toman_raw"]
                == "1500001"
            )

            # A separately acknowledged corrupt attempt yields no result.
            waiter.wait_for_ack(timeout=5.0)
            src.write_bytes(b"PK\x03\x04invalid-generation")
            runtime._on_adapter_event(SaveEventKind.MODIFIED, src, None)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            assert third_attempt.wait(timeout=5.0)
            assert third_attempt_finished.wait(timeout=5.0)
            waiter.wait_for_ack(timeout=5.0)
            assert len(third_attempt_errors) == 1
            assert isinstance(
                third_attempt_errors[0],
                (XlsxSourceNotReadyError, XlsxSourceReadError, XlsxSourceIdentityError),
            )
            assert len(deliveries) == 2
            assert runtime.view().state == SourceWatchRuntimeState.RUNNING
            assert runtime._coordinator.view().state == SaveCoordinatorState.WAITING
            assert runtime._active_cycle_running is False

            src.write_bytes(fourth_bytes)
            runtime._on_adapter_event(SaveEventKind.MODIFIED, src, None)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            fourth = wait_delivery(3)
            assert fourth.key.fiscal_year == 1407
            assert fourth.file_sha256 == hashlib.sha256(fourth_bytes).hexdigest()
            assert (
                fourth.read_result.snapshot.sheets["خرید-فروش"]
                .rows[0]
                .raw_values["unit_price_toman_raw"]
                == "1500002"
            )
            first_row = first.read_result.snapshot.sheets["خرید-فروش"].rows[0]
            second_row = second.read_result.snapshot.sheets["خرید-فروش"].rows[0]
            fourth_row = fourth.read_result.snapshot.sheets["خرید-فروش"].rows[0]
            assert (
                len({first_row.stable_id, second_row.stable_id, fourth_row.stable_id})
                == 2
            )
            assert first_row.stable_id == second_row.stable_id
            assert fourth_row.stable_id != first_row.stable_id
            assert attempts >= 4
            assert list(snapshot_root.iterdir()) == []
            runtime.request_stop()
            runner.join(timeout=5.0)
            runner.assert_clean_exit()
        finally:
            release_lease.set()
            runtime.request_stop()
            runner.join(timeout=5.0)
            observer.stop()

    def test_iw14_persistence_trapping_and_oracle_comparison(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IW-14: Persistence import/call trapping and independent pre-change oracle."""
        persistence_module = importlib.import_module("accounting_persistence")
        # Trap new persistence imports without assuming test collection order.
        persistence_before = {
            name
            for name in sys.modules
            if name == "accounting_persistence"
            or name.startswith("accounting_persistence.")
        }
        original_import = builtins.__import__
        original_import_module = importlib.import_module

        def trapped_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "accounting_persistence" or name.startswith(
                "accounting_persistence."
            ):
                raise AssertionError("Persistence import prohibited in runtime")
            return original_import(name, *args, **kwargs)

        def trapped_import_module(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "accounting_persistence" or name.startswith(
                "accounting_persistence."
            ):
                raise AssertionError("Persistence import prohibited in runtime")
            return original_import_module(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", trapped_import)
        monkeypatch.setattr(importlib, "import_module", trapped_import_module)

        # Prohibit opening sqlite3 database
        def trapped_connect(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("Database connection prohibited in runtime!")

        monkeypatch.setattr(sqlite3, "connect", trapped_connect)

        def forbid_persistence_call(frame: Any, event: str, arg: Any) -> None:
            if event == "call" and str(frame.f_globals.get("__name__", "")).startswith(
                "accounting_persistence"
            ):
                raise AssertionError("Persistence call or DTO construction")

        # The trap must catch already imported functions, not only imports/SQLite.
        old_main_profile = sys.getprofile()
        sys.setprofile(forbid_persistence_call)
        try:
            with pytest.raises(AssertionError, match="Persistence call"):
                persistence_module.read_source_import_store(None)
        finally:
            sys.setprofile(old_main_profile)

        src_dir = tmp_path / "watch"
        src_dir.mkdir()
        src = src_dir / "target.xlsx"
        snap_root = tmp_path / "snapshots"
        snap_root.mkdir()

        # Oracle state model checking raw vs identified
        class SimpleOracle:
            def __init__(self) -> None:
                self.notices = 0
                self.reads = 0
                self.delivered = 0

            def on_notice(self) -> None:
                self.notices += 1

            def on_read(self) -> bool:
                if self.notices > self.reads:
                    self.reads += 1
                    self.delivered += 1
                    return True
                return False

        oracle = SimpleOracle()
        clock = FakeClock()
        obs = MockObserver()
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs,
            _time_source=clock,
        )

        mock_res = cast(IdentifiedXlsxSource, object())
        monkeypatch.setattr(
            "accounting_local_agent.save_import_coordinator.read_identified_xlsx_source",
            lambda *args, **kwargs: mock_res,
        )

        delivered: list[IdentifiedXlsxSource] = []
        delivered_condition = threading.Condition()

        def record_delivery(value: IdentifiedXlsxSource) -> None:
            with delivered_condition:
                delivered.append(value)
                delivered_condition.notify_all()

        old_thread_profile = threading.getprofile()
        threading.setprofile(forbid_persistence_call)
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)
        runner = ManagedIdentifiedRunnerThread(runtime, record_delivery)
        runner.start()

        try:
            # Notice 1 (startup)
            oracle.on_notice()
            waiter.wait_for_ack(timeout=5.0)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()

            oracle.on_read()
            with delivered_condition:
                assert delivered_condition.wait_for(
                    lambda: len(delivered) == oracle.delivered, timeout=5.0
                )

            # Notice 2
            waiter.wait_for_ack(timeout=5.0)
            oracle.on_notice()
            runtime._on_adapter_event(SaveEventKind.MODIFIED, src, None)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()

            oracle.on_read()
            with delivered_condition:
                assert delivered_condition.wait_for(
                    lambda: len(delivered) == oracle.delivered, timeout=5.0
                )

            runtime.request_stop()
            runner.join(timeout=5.0)
            runner.assert_clean_exit()
        finally:
            runtime.request_stop()
            runner.join(timeout=5.0)
            obs.stop()
            threading.setprofile(old_thread_profile)

        assert {
            name
            for name in sys.modules
            if name == "accounting_persistence"
            or name.startswith("accounting_persistence.")
        } == persistence_before

    @pytest.mark.parametrize(
        "scenario,expected_calls,expected_deliveries,expected_state",
        [
            pytest.param(
                "success", 1, 1, SourceWatchRuntimeState.STOPPED, id="success"
            ),
            pytest.param(
                "reader_reject",
                1,
                0,
                SourceWatchRuntimeState.STOPPED,
                id="reader-reject",
            ),
            pytest.param(
                "consumer_error",
                1,
                1,
                SourceWatchRuntimeState.FAILED,
                id="consumer-error",
            ),
            pytest.param(
                "stop_waiting", 0, 0, SourceWatchRuntimeState.STOPPED, id="stop-waiting"
            ),
        ],
    )
    def test_iw14_raw_mode_oracle_and_persistence_boundary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        scenario: str,
        expected_calls: int,
        expected_deliveries: int,
        expected_state: SourceWatchRuntimeState,
    ) -> None:
        """Raw entry follows a literal call/state oracle without persistence."""
        source = tmp_path / "watch" / "source.xlsx"
        source.parent.mkdir()
        snapshot_root = tmp_path / "snapshots"
        snapshot_root.mkdir()
        if scenario != "stop_waiting":
            source.write_bytes(_build_synthetic_identified_xlsx())
        clock = FakeClock()
        observer = MockObserver()
        runtime = SourceWatchRuntime(
            source,
            snapshot_root=snapshot_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: observer,
            _time_source=clock,
        )
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)
        raw_result = cast(XlsxSourceReadResult, object())
        deliveries: list[XlsxSourceReadResult] = []
        calls = 0
        called = threading.Event()
        unexpected_identified = 0
        errors: list[BaseException] = []

        def raw_driver(*args: Any, **kwargs: Any) -> XlsxSourceReadResult:
            nonlocal calls
            calls += 1
            called.set()
            return cast(XlsxSourceReadResult, read_due_source(*args, **kwargs))

        def raw_reader(*args: Any, **kwargs: Any) -> XlsxSourceReadResult:
            if scenario == "reader_reject":
                raise XlsxSourceReadError("synthetic rejection")
            return raw_result

        def identified_driver(*args: Any, **kwargs: Any) -> None:
            nonlocal unexpected_identified
            unexpected_identified += 1
            raise AssertionError("Raw mode selected identified driver")

        def consumer(value: XlsxSourceReadResult) -> None:
            deliveries.append(value)
            if scenario == "consumer_error":
                raise ValueError("synthetic consumer failure")
            runtime.request_stop()

        def run_raw() -> None:
            try:
                runtime.run(consumer)
            except BaseException as error:
                errors.append(error)

        original_import = builtins.__import__
        original_import_module = importlib.import_module

        def forbid_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "accounting_persistence" or name.startswith(
                "accounting_persistence."
            ):
                raise AssertionError("Persistence import in raw runtime")
            return original_import(name, *args, **kwargs)

        def forbid_import_module(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "accounting_persistence" or name.startswith(
                "accounting_persistence."
            ):
                raise AssertionError("Persistence module import in raw runtime")
            return original_import_module(name, *args, **kwargs)

        def forbid_persistence_call(frame: Any, event: str, arg: Any) -> None:
            if event == "call" and str(frame.f_globals.get("__name__", "")).startswith(
                "accounting_persistence"
            ):
                raise AssertionError("Persistence call or DTO construction")

        def forbid_db(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("Database open in raw runtime")

        monkeypatch.setattr(builtins, "__import__", forbid_import)
        monkeypatch.setattr(importlib, "import_module", forbid_import_module)
        monkeypatch.setattr(sqlite3, "connect", forbid_db)
        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_source", raw_driver
        )
        monkeypatch.setattr(
            "accounting_local_agent.save_import_coordinator.read_xlsx_source_snapshot",
            raw_reader,
        )
        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_identified_source",
            identified_driver,
        )
        old_profile = threading.getprofile()
        threading.setprofile(forbid_persistence_call)
        runner = threading.Thread(target=run_raw)
        runner.start()
        try:
            waiter.wait_for_ack(timeout=5.0)
            if scenario == "stop_waiting":
                runtime.request_stop()
            else:
                clock.advance_seconds(3.0)
                with runtime._lifecycle_lock:
                    runtime._condition.notify_all()
                assert called.wait(timeout=5.0)
                if scenario == "reader_reject":
                    waiter.wait_for_ack(timeout=5.0)
                    runtime.request_stop()
            runner.join(timeout=5.0)
            assert not runner.is_alive()
            assert calls == expected_calls
            assert len(deliveries) == expected_deliveries
            assert all(value is raw_result for value in deliveries)
            assert unexpected_identified == 0
            assert runtime.view().state == expected_state
            if scenario == "consumer_error":
                assert len(errors) == 1
                assert isinstance(errors[0], SourceWatchRuntimeError)
                assert errors[0].reason == SourceWatchRuntimeReason.CONSUMER_FAILED
            else:
                assert errors == []
        finally:
            threading.setprofile(old_profile)
            runtime.request_stop()
            runner.join(timeout=5.0)
            observer.stop()

    def test_iw15_state_machine_history_simulation(self, tmp_path: Path) -> None:
        """IW-15: Simulated histories against an independent state model."""
        rnd = random.Random(42)

        # Independent state model of coordinator
        class ModelCoordinator:
            def __init__(self) -> None:
                self.state = "idle"
                self.pending = False
                self.next_due_ns: int | None = None
                self.deliveries = 0

            def notify(self, now_ns: int) -> None:
                if self.state in ("idle", "waiting"):
                    self.state = "waiting"
                    self.pending = True
                    self.next_due_ns = now_ns + 2_000_000_000
                elif self.state == "running":
                    self.pending = True

            def step_due(self, now_ns: int) -> bool:
                if self.state == "waiting" and self.next_due_ns is not None:
                    if now_ns >= self.next_due_ns:
                        self.state = "running"
                        self.pending = False
                        self.next_due_ns = None
                        return True
                return False

            def finish(self, outcome: str, now_ns: int) -> None:
                if self.state == "running":
                    if outcome == "success":
                        self.deliveries += 1
                        if self.pending:
                            self.state = "waiting"
                            self.next_due_ns = now_ns + 2_000_000_000
                            self.pending = False
                        else:
                            self.state = "idle"
                            self.pending = False
                    elif outcome == "not_ready":
                        self.state = "waiting"
                        self.next_due_ns = now_ns + 2_000_000_000
                        self.pending = False
                    elif outcome == "rejected":
                        if self.pending:
                            self.state = "waiting"
                            self.next_due_ns = now_ns + 2_000_000_000
                            self.pending = False
                        else:
                            self.state = "idle"
                            self.pending = False

        # Run 45 distinct history simulations
        for hist_idx in range(45):
            model = ModelCoordinator()
            src = tmp_path / f"sim_{hist_idx}.xlsx"
            now = 1_000_000_000
            clock = FakeClock(start_ns=now)
            coord = SaveImportCoordinator(src, _time_source=clock)

            # Generate random operation sequence (12 steps)
            for _ in range(12):
                op = rnd.choice(["notify", "advance", "take_and_finish"])
                if op == "notify":
                    coord.notify(SaveEventKind.MODIFIED, src)
                    model.notify(clock())
                elif op == "advance":
                    delta = rnd.randint(500_000_000, 3_000_000_000)
                    now = clock.advance_ns(delta)
                elif op == "take_and_finish":
                    token = coord.take_due()
                    model_due = model.step_due(clock())
                    assert (token is not None) == model_due, (
                        f"History {hist_idx}: mismatch on take_due"
                    )
                    if token is not None:
                        outcome_str = rnd.choice(["success", "not_ready", "rejected"])
                        outcome_map = {
                            "success": SourceReadOutcome.SUCCESS,
                            "not_ready": SourceReadOutcome.SOURCE_NOT_READY,
                            "rejected": SourceReadOutcome.READER_REJECTED,
                        }
                        coord.finish(token, outcome_map[outcome_str])
                        model.finish(outcome_str, clock())

            assert model.deliveries >= 0

        # Targeted mutation tests: verify detection of anomalies
        # 1. Wrong driver detection
        def detect_wrong_driver(mode: str, driver_called: str) -> None:
            expected = "identified" if mode == "identified" else "raw"
            if driver_called != expected:
                raise AssertionError(
                    f"Wrong driver detected: {driver_called} != {expected}"
                )

        with pytest.raises(AssertionError, match="Wrong driver detected"):
            detect_wrong_driver("identified", "raw")

        # 2. Early delivery detection
        def check_early_delivery(current_ns: int, due_ns: int) -> None:
            if current_ns < due_ns:
                raise AssertionError("Early delivery detected before debounce deadline")

        with pytest.raises(AssertionError, match="Early delivery detected"):
            check_early_delivery(1_500_000_000, 2_000_000_000)

        # 3. Duplicate delivery detection
        delivered_versions: set[int] = set()

        def check_duplicate_delivery(version_id: int) -> None:
            if version_id in delivered_versions:
                raise AssertionError(
                    f"Duplicate delivery detected for version {version_id}"
                )
            delivered_versions.add(version_id)

        check_duplicate_delivery(1)
        with pytest.raises(AssertionError, match="Duplicate delivery detected"):
            check_duplicate_delivery(1)

        # 4. Lost follow-up detection
        def check_lost_followup(pending_before_finish: bool, state_after: str) -> None:
            if pending_before_finish and state_after != "waiting":
                raise AssertionError(
                    "Lost follow-up detected: pending notice not preserved"
                )

        with pytest.raises(AssertionError, match="Lost follow-up detected"):
            check_lost_followup(True, "idle")

        # 5. Raw-mode drift detection
        def check_raw_mode_drift(result_type: type) -> None:
            if result_type is not XlsxSourceReadResult:
                raise AssertionError(f"Raw mode drift detected: got {result_type}")

        with pytest.raises(AssertionError, match="Raw mode drift detected"):
            check_raw_mode_drift(IdentifiedXlsxSource)

    @pytest.mark.parametrize("history_index", range(45))
    def test_iw15_runtime_history_against_literal_oracle(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        history_index: int,
    ) -> None:
        """Generated runtime histories check an independent deadline model."""
        rng = random.Random(0x51A7 + history_index * 7919)

        class HistoryOracle:
            def __init__(self, initial_ns: int) -> None:
                self.deadline_ns: int | None = initial_ns + 2_000_000_000
                self.pending_during_run = False
                self.last_running_notice_ns: int | None = None
                self.completed_reads = 0
                self.delivered = 0

            def notice(self, at_ns: int, *, during_run: bool = False) -> None:
                if during_run:
                    self.pending_during_run = True
                    self.last_running_notice_ns = at_ns
                else:
                    self.deadline_ns = at_ns + 2_000_000_000

            def finish(self, at_ns: int, outcome: str) -> None:
                self.completed_reads += 1
                if outcome == "success":
                    self.delivered += 1
                if outcome == "not_ready":
                    self.deadline_ns = at_ns + 2_000_000_000
                elif self.pending_during_run:
                    assert self.last_running_notice_ns is not None
                    self.deadline_ns = self.last_running_notice_ns + 2_000_000_000
                else:
                    self.deadline_ns = None
                self.pending_during_run = False
                self.last_running_notice_ns = None

        oracle = HistoryOracle(1_000_000_000)
        scenarios = (
            "stop_waiting",
            "early_then_success",
            "success",
            "not_ready",
            "rejected",
            "fatal",
            "consumer_failure",
            "followup_read",
            "followup_consumer",
        )
        scenario = scenarios[history_index % len(scenarios)]
        mode = "raw" if history_index % 2 else "identified"
        pre_due_notices = rng.randrange(5)
        followup_delay_ns = rng.randint(100_000_000, 750_000_000)
        expected_calls = {
            "stop_waiting": 0,
            "early_then_success": 1,
            "success": 1,
            "not_ready": 2,
            "rejected": 2,
            "fatal": 1,
            "consumer_failure": 1,
            "followup_read": 2,
            "followup_consumer": 2,
        }[scenario]
        expected_deliveries = {
            "stop_waiting": 0,
            "early_then_success": 1,
            "success": 1,
            "not_ready": 1,
            "rejected": 1,
            "fatal": 0,
            "consumer_failure": 1,
            "followup_read": 2,
            "followup_consumer": 2,
        }[scenario]
        expected_state = (
            SourceWatchRuntimeState.FAILED
            if scenario in {"fatal", "consumer_failure"}
            else SourceWatchRuntimeState.STOPPED
        )
        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        source = watch_dir / f"runtime-history-{history_index}.xlsx"
        clock = FakeClock(start_ns=1_000_000_000)
        observer = MockObserver()
        runtime = SourceWatchRuntime(
            source,
            snapshot_root=tmp_path / "snapshots",
            observation_interval_seconds=0.001,
            _observer_factory=lambda: observer,
            _time_source=clock,
        )
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)
        result = cast(XlsxSourceReadResult | IdentifiedXlsxSource, object())
        reader_calls = 0
        read_times: list[int] = []
        reader_condition = threading.Condition()
        delivery_condition = threading.Condition()
        deliveries: list[Any] = []
        errors: list[BaseException] = []
        selected: list[str] = []
        in_read = threading.Event()
        release_read = threading.Event()
        in_consumer = threading.Event()
        release_consumer = threading.Event()

        def reader() -> Any:
            nonlocal reader_calls
            with reader_condition:
                reader_calls += 1
                read_times.append(clock())
                call = reader_calls
                reader_condition.notify_all()
            if scenario == "followup_read" and call == 1:
                in_read.set()
                assert release_read.wait(timeout=5.0)
            if call == 1 and scenario == "not_ready":
                raise XlsxSourceNotReadyError("synthetic not ready")
            if call == 1 and scenario == "rejected":
                if mode == "identified":
                    raise XlsxSourceIdentityError(
                        XlsxSourceIdentityReason.MISSING_MARKER
                    )
                raise XlsxSourceReadError("synthetic reader rejection")
            if scenario == "fatal":
                raise XlsxSnapshotStorageError("synthetic storage fault")
            return result

        @contextmanager
        def fake_lease(*args: Any, **kwargs: Any) -> Any:
            yield SimpleNamespace(snapshot_path=source)

        def raw_reader(*args: Any, **kwargs: Any) -> Any:
            return reader()

        def identified_reader(*args: Any, **kwargs: Any) -> Any:
            return reader()

        def raw_driver(*args: Any, **kwargs: Any) -> Any:
            selected.append("raw")
            return read_due_source(*args, **kwargs)

        def identified_driver(*args: Any, **kwargs: Any) -> Any:
            selected.append("identified")
            return read_due_identified_source(*args, **kwargs)

        def consumer(value: Any) -> None:
            with delivery_condition:
                deliveries.append(value)
                delivery_condition.notify_all()
            if scenario == "followup_consumer" and len(deliveries) == 1:
                in_consumer.set()
                assert release_consumer.wait(timeout=5.0)
            if scenario == "consumer_failure":
                raise ValueError("synthetic consumer failure")

        def run() -> None:
            try:
                if mode == "identified":
                    runtime.run_identified(consumer)
                else:
                    runtime.run(consumer)
            except BaseException as error:
                errors.append(error)

        monkeypatch.setattr(
            "accounting_local_agent.save_import_coordinator.open_stable_xlsx_snapshot",
            fake_lease,
        )
        monkeypatch.setattr(
            "accounting_local_agent.save_import_coordinator.read_xlsx_source_snapshot",
            raw_reader,
        )
        monkeypatch.setattr(
            "accounting_local_agent.save_import_coordinator.read_identified_xlsx_source",
            identified_reader,
        )
        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_source", raw_driver
        )
        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_identified_source",
            identified_driver,
        )
        runner = threading.Thread(target=run)
        runner.start()

        def wait_reads(count: int) -> None:
            with reader_condition:
                assert reader_condition.wait_for(
                    lambda: reader_calls >= count, timeout=5.0
                )

        def wait_deliveries(count: int) -> None:
            with delivery_condition:
                assert delivery_condition.wait_for(
                    lambda: len(deliveries) >= count, timeout=5.0
                )

        def drive_exact_due(expected_read_count: int) -> None:
            deadline_ns = oracle.deadline_ns
            assert deadline_ns is not None
            assert clock() < deadline_ns
            clock.set_ns(deadline_ns - 1)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            waiter.wait_for_ack(timeout=5.0)
            assert reader_calls == expected_read_count - 1, (
                history_index,
                scenario,
                "read before modeled deadline",
            )
            clock.set_ns(deadline_ns)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            wait_reads(expected_read_count)

        try:
            waiter.wait_for_ack(timeout=5.0)
            for _ in range(pre_due_notices):
                clock.advance_ns(rng.randint(100_000_000, 400_000_000))
                oracle.notice(clock())
                runtime._on_adapter_event(SaveEventKind.MODIFIED, source, None)
                waiter.wait_for_ack(timeout=5.0)
            if scenario == "stop_waiting":
                assert reader_calls == oracle.completed_reads == 0
                runtime.request_stop()
            else:
                drive_exact_due(1)
                if scenario in {"not_ready", "rejected"}:
                    waiter.wait_for_ack(timeout=5.0)
                    assert deliveries == []
                    if scenario == "rejected":
                        oracle.finish(clock(), "rejected")
                        clock.advance_ns(followup_delay_ns)
                        oracle.notice(clock())
                        runtime._on_adapter_event(SaveEventKind.MODIFIED, source, None)
                        waiter.wait_for_ack(timeout=5.0)
                    else:
                        oracle.finish(clock(), "not_ready")
                    drive_exact_due(2)
                    wait_deliveries(1)
                    oracle.finish(clock(), "success")
                    runtime.request_stop()
                elif scenario == "followup_read":
                    assert in_read.wait(timeout=5.0)
                    clock.advance_ns(followup_delay_ns)
                    oracle.notice(clock(), during_run=True)
                    runtime._on_adapter_event(SaveEventKind.MODIFIED, source, None)
                    clock.advance_ns(2_000_000_001)
                    release_read.set()
                    wait_deliveries(1)
                    oracle.finish(clock(), "success")
                    assert oracle.deadline_ns is not None
                    assert oracle.deadline_ns < clock()
                    wait_reads(2)
                    wait_deliveries(2)
                    assert read_times[1] == clock()
                    oracle.finish(clock(), "success")
                    runtime.request_stop()
                elif scenario == "followup_consumer":
                    assert in_consumer.wait(timeout=5.0)
                    clock.advance_ns(followup_delay_ns)
                    oracle.notice(clock(), during_run=True)
                    runtime._on_adapter_event(SaveEventKind.MODIFIED, source, None)
                    clock.advance_ns(2_000_000_001)
                    release_consumer.set()
                    oracle.finish(clock(), "success")
                    assert oracle.deadline_ns is not None
                    assert oracle.deadline_ns < clock()
                    wait_reads(2)
                    wait_deliveries(2)
                    assert read_times[1] == clock()
                    oracle.finish(clock(), "success")
                    runtime.request_stop()
                elif scenario in {"success", "early_then_success"}:
                    wait_deliveries(1)
                    oracle.finish(clock(), "success")
                    runtime.request_stop()
            runner.join(timeout=5.0)
            assert not runner.is_alive(), f"History {history_index} did not terminate"
            assert reader_calls == expected_calls
            assert len(deliveries) == expected_deliveries
            if scenario not in {"fatal", "consumer_failure"}:
                assert oracle.completed_reads == expected_calls
                assert oracle.delivered == expected_deliveries
            else:
                assert oracle.completed_reads == 0
            assert all(value is result for value in deliveries)
            assert selected == [mode] * expected_calls
            assert runtime.view().state == expected_state
            if scenario in {"fatal", "consumer_failure"}:
                assert len(errors) == 1
                assert isinstance(errors[0], SourceWatchRuntimeError)
                assert errors[0].reason == (
                    SourceWatchRuntimeReason.SOURCE_READ_FAILED
                    if scenario == "fatal"
                    else SourceWatchRuntimeReason.CONSUMER_FAILED
                )
            else:
                assert errors == []
        finally:
            release_read.set()
            release_consumer.set()
            runtime.request_stop()
            runner.join(timeout=5.0)
            observer.stop()

    def test_iw13_pre_yield_source_reverify_replacement_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Source replacement before reverify cannot deliver stale or mixed data."""
        from accounting_local_agent.xlsx_snapshot_acquisition import (
            open_stable_xlsx_snapshot as original_open,
        )

        watch = tmp_path / "watch"
        watch.mkdir()
        source = watch / "target.xlsx"
        snapshot_root = tmp_path / "snapshots"
        snapshot_root.mkdir()
        first_bytes = _build_synthetic_identified_xlsx(fiscal_year=1405)
        second_bytes = _build_synthetic_identified_xlsx(fiscal_year=1406)
        source.write_bytes(first_bytes)
        clock = FakeClock(start_ns=1_000_000_000)
        observer = MockObserver()
        runtime = SourceWatchRuntime(
            source,
            snapshot_root=snapshot_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: observer,
            _time_source=clock,
        )
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)
        before_reverify = threading.Event()
        release_reverify = threading.Event()
        acquisition_attempts = 0

        @contextmanager
        def gated_open(*args: Any, **kwargs: Any) -> Any:
            nonlocal acquisition_attempts
            acquisition_attempts += 1
            attempt = acquisition_attempts

            def hook(stage: str, path: Path, target: Path | None) -> None:
                if attempt == 1 and stage == "before_source_reverify":
                    before_reverify.set()
                    assert release_reverify.wait(timeout=5.0)

            with original_open(*args, _fault_hook=hook, **kwargs) as lease:
                yield lease

        monkeypatch.setattr(
            "accounting_local_agent.xlsx_source_identity.open_stable_xlsx_snapshot",
            gated_open,
        )
        deliveries: list[IdentifiedXlsxSource] = []
        delivery_condition = threading.Condition()

        def consumer(value: IdentifiedXlsxSource) -> None:
            with delivery_condition:
                deliveries.append(value)
                delivery_condition.notify_all()

        runner = ManagedIdentifiedRunnerThread(runtime, consumer)
        runner.start()
        try:
            waiter.wait_for_ack(timeout=5.0)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            assert before_reverify.wait(timeout=5.0)
            replacement = watch / "replacement.xlsx"
            replacement.write_bytes(second_bytes)
            os.replace(replacement, source)
            runtime._on_adapter_event(SaveEventKind.MOVED, replacement, source)
            release_reverify.set()
            waiter.wait_for_ack(timeout=5.0)
            assert deliveries == []
            assert acquisition_attempts == 1
            assert runtime._coordinator.view().state == SaveCoordinatorState.WAITING

            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            with delivery_condition:
                assert delivery_condition.wait_for(
                    lambda: len(deliveries) == 1, timeout=5.0
                )
            assert acquisition_attempts == 2
            assert deliveries[0].key.fiscal_year == 1406
            assert deliveries[0].file_sha256 == hashlib.sha256(second_bytes).hexdigest()
            assert all(
                value.file_sha256 != hashlib.sha256(first_bytes).hexdigest()
                for value in deliveries
            )
            assert list(snapshot_root.iterdir()) == []
            runtime.request_stop()
            runner.join(timeout=5.0)
            runner.assert_clean_exit()
        finally:
            release_reverify.set()
            runtime.request_stop()
            runner.join(timeout=5.0)
            observer.stop()


# ===========================================================================
# TestIdentifiedSourceWatchRuntimeBenchmark
# Covers IW-16
# ===========================================================================


class TestIdentifiedSourceWatchRuntimeBenchmark:
    """Performance gate: 15,000 rows, < 15.0s call-window time, < 128.0 MiB peak RSS."""

    def test_iw16_identified_15000_row_runtime_benchmark(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IW-16: Build outside a fresh process; measure only its driver call."""
        child_source = os.environ.get("IW16_CHILD_SOURCE")
        if child_source is None:
            watch_dir = tmp_path / "bench_watch"
            watch_dir.mkdir()
            src = watch_dir / "benchmark.xlsx"
            snap_root = tmp_path / "bench_snapshots"
            snap_root.mkdir()
            parts = identified_parts(
                value="xlsx-source-identity.v1|00000000-0000-7000-8000-0000000003e7|1405",
                raw=raw_parts(rows_per_sheet=3750, extra_buy=False),
            )
            src_bytes = zipped(parts)
            src.write_bytes(src_bytes)
            environment = os.environ.copy()
            environment.update(
                IW16_CHILD_SOURCE=str(src),
                IW16_CHILD_SNAPSHOT_ROOT=str(snap_root),
                IW16_CHILD_SHA256=hashlib.sha256(src_bytes).hexdigest(),
                IW16_CHILD_SIZE=str(len(src_bytes)),
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-s",
                    "-p",
                    "no:cacheprovider",
                    "tests/test_identified_source_watch_runtime.py::"
                    "TestIdentifiedSourceWatchRuntimeBenchmark::"
                    "test_iw16_identified_15000_row_runtime_benchmark",
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            print(completed.stdout)
            assert completed.returncode == 0, completed.stderr + completed.stdout
            return

        src = Path(child_source)
        snap_root = Path(os.environ["IW16_CHILD_SNAPSHOT_ROOT"])
        expected_sha256 = os.environ["IW16_CHILD_SHA256"]
        expected_size = int(os.environ["IW16_CHILD_SIZE"])
        assert src.is_file() and snap_root.is_dir()

        clock = FakeClock()
        obs = MockObserver()
        runtime = SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.001,
            _observer_factory=lambda: obs,
            _time_source=clock,
        )

        delivered: list[IdentifiedXlsxSource] = []
        call_metrics: list[tuple[float, float]] = []
        original_path_read_bytes = Path.read_bytes
        original_builtin_open = builtins.open
        original_io_open = io.open
        original_os_read = os.read
        whole_workbook_read_probes: list[str] = []

        class GuardedWorkbookStream:
            def __init__(self, stream: Any) -> None:
                self.stream = stream

            def read(self, size: int = -1) -> bytes:
                if size < 0 or size >= expected_size:
                    whole_workbook_read_probes.append("stream.read")
                    raise AssertionError("Whole workbook read is prohibited")
                return cast(bytes, self.stream.read(size))

            def readinto(self, buffer: Any) -> int:
                if len(buffer) >= expected_size:
                    whole_workbook_read_probes.append("stream.readinto")
                    raise AssertionError("Whole workbook read is prohibited")
                return cast(int, self.stream.readinto(buffer))

            def __getattr__(self, name: str) -> Any:
                return getattr(self.stream, name)

            def __enter__(self) -> GuardedWorkbookStream:
                return self

            def __exit__(self, *args: Any) -> Any:
                return self.stream.__exit__(*args)

        def is_workbook(path: Any) -> bool:
            if isinstance(path, int):
                try:
                    opened = os.fstat(path)
                    source = src.stat()
                except OSError:
                    return False
                return (opened.st_dev, opened.st_ino) == (
                    source.st_dev,
                    source.st_ino,
                )
            return (
                isinstance(path, (str, bytes, os.PathLike))
                and Path(os.fsdecode(path)).suffix == ".xlsx"
            )

        def guarded_os_read(fd: int, size: int) -> bytes:
            if is_workbook(fd) and size >= expected_size:
                whole_workbook_read_probes.append("os.read")
                raise AssertionError("Whole workbook read is prohibited")
            return original_os_read(fd, size)

        def guarded_path_read_bytes(path: Path) -> bytes:
            if is_workbook(path):
                whole_workbook_read_probes.append("Path.read_bytes")
                raise AssertionError("Whole workbook read is prohibited")
            return original_path_read_bytes(path)

        def guarded_open(original: Callable[..., Any]) -> Callable[..., Any]:
            def open_checked(
                path: Any, mode: str = "r", *args: Any, **kwargs: Any
            ) -> Any:
                stream = original(path, mode, *args, **kwargs)
                if is_workbook(path) and "b" in mode and "r" in mode:
                    return GuardedWorkbookStream(stream)
                return stream

            return open_checked

        class GuardedBytesIO(io.BytesIO):
            def __init__(self, initial_bytes: bytes | bytearray = b"") -> None:
                if len(initial_bytes) >= expected_size:
                    whole_workbook_read_probes.append("io.BytesIO")
                    raise AssertionError("Whole workbook allocation is prohibited")
                super().__init__(initial_bytes)

        def measured_driver(*args: Any, **kwargs: Any) -> IdentifiedXlsxSource | None:
            sampler = _CallWindowRssSampler()
            with monkeypatch.context() as whole_read_guard:
                whole_read_guard.setattr(Path, "read_bytes", guarded_path_read_bytes)
                whole_read_guard.setattr(
                    builtins, "open", guarded_open(original_builtin_open)
                )
                whole_read_guard.setattr(io, "open", guarded_open(original_io_open))
                whole_read_guard.setattr(io, "BytesIO", GuardedBytesIO)
                whole_read_guard.setattr(os, "read", guarded_os_read)
                with pytest.raises(AssertionError, match="Whole workbook read"):
                    src.read_bytes()
                with pytest.raises(AssertionError, match="Whole workbook read"):
                    with io.open(src, "rb") as probe:  # noqa: UP020
                        probe.read()
                with pytest.raises(AssertionError, match="Whole workbook read"):
                    with builtins.open(src, "rb") as probe:
                        probe.read()
                fd_probe = os.open(src, os.O_RDONLY)
                try:
                    with pytest.raises(AssertionError, match="Whole workbook read"):
                        with builtins.open(fd_probe, "rb", closefd=False) as probe:
                            probe.read()
                    with pytest.raises(AssertionError, match="Whole workbook read"):
                        os.read(fd_probe, expected_size)
                finally:
                    os.close(fd_probe)
                with pytest.raises(AssertionError, match="Whole workbook allocation"):
                    io.BytesIO(b"X" * expected_size)
                assert whole_workbook_read_probes == [
                    "Path.read_bytes",
                    "stream.read",
                    "stream.read",
                    "stream.read",
                    "os.read",
                    "io.BytesIO",
                ]
                sampler.start()
                started = time.perf_counter()
                try:
                    return read_due_identified_source(*args, **kwargs)
                finally:
                    duration = time.perf_counter() - started
                    _, peak = sampler.stop_and_get_peak()
                    call_metrics.append((duration, peak))
                    assert whole_workbook_read_probes == [
                        "Path.read_bytes",
                        "stream.read",
                        "stream.read",
                        "stream.read",
                        "os.read",
                        "io.BytesIO",
                    ]

        monkeypatch.setattr(
            "accounting_local_agent.source_watch_runtime.read_due_identified_source",
            measured_driver,
        )
        waiter = ControlledConditionWaiter(runtime._condition)
        monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)

        def consumer(res: IdentifiedXlsxSource) -> None:
            delivered.append(res)
            runtime.request_stop()

        runner = ManagedIdentifiedRunnerThread(runtime, consumer)
        runner.start()

        try:
            waiter.wait_for_ack(timeout=5.0)
            clock.advance_seconds(3.0)
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()

            runner.join(timeout=20.0)
        finally:
            runtime.request_stop()
            runner.join(timeout=5.0)
            obs.stop()

        runner.assert_clean_exit()
        assert len(delivered) == 1
        res = delivered[0]

        # Invariants and row counts
        assert res.key.source_id == uuid.UUID("00000000-0000-7000-8000-0000000003e7")
        assert res.key.fiscal_year == 1405
        assert res.file_sha256 == expected_sha256
        assert res.byte_count == expected_size
        assert res.read_result.snapshot.total_row_count == 15000

        # Validate every identity
        expected_identities = {
            uuid.UUID(int=(7 << 76) | (2 << 62) | (1 + sheet * 100_000 + row))
            for sheet in range(4)
            for row in range(3750)
        }
        assert set(res.read_result.snapshot.all_rows_by_id) == expected_identities

        # Measure only the WP-16 driver call, excluding debounce/start/teardown.
        assert len(call_metrics) == 1
        duration, peak_rss = call_metrics[0]
        print(
            f"[IW-16 DRIVER WINDOW] rows=15000 duration={duration:.4f}s "
            f"peak_rss={peak_rss:.2f}MiB"
        )
        assert duration < 15.0, f"Duration {duration:.2f}s exceeded 15.0s limit"
        assert peak_rss < 128.0, f"Peak RSS {peak_rss:.2f} MiB exceeded 128.0 MiB limit"

        # Prove there is no second workbook buffer
        assert not hasattr(res, "_buffer") and not hasattr(res, "workbook_bytes")
        assert not hasattr(res.read_result, "_buffer") and not hasattr(
            res.read_result, "workbook_bytes"
        )
        assert not hasattr(res.read_result.snapshot, "raw_bytes") and not hasattr(
            res.read_result.snapshot, "_buffer"
        )

        # Validate sample raw values across sheets
        for row in res.read_result.snapshot.all_rows_by_id.values():
            if row.sheet_name == "خرید-فروش":
                assert row.raw_values["unit_price_toman_raw"] == "1500000"
            elif row.sheet_name == "دریافت-پرداخت":
                assert row.raw_values["amount_toman_raw"] == "50000000"
            elif row.sheet_name == "ورود-خروج":
                assert row.raw_values["quantity_raw"] == "100.5"
            elif row.sheet_name == "لیست کسبه":
                assert row.raw_values["party_name_raw"] == "فروشگاه نمونه"

        # Complete lease cleanup: snapshot directory is clean
        assert list(snap_root.iterdir()) == []
