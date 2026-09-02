"""Tests for source-watch-runtime.v1 (WP-08 / ADR-0011).

Covers acceptance criteria WR-01 through WR-14:
- WR-01: Public API exports, version, views, invariant checks, lexical validation
- WR-02: Captured factory checks, single observer allocation, no allocation on init
- WR-03: Table-driven event adapter tests (created/modified/deleted/moved/temp)
- WR-04: Callback boundary fault handling, malformed event surfacing, loop wakeup
- WR-05: Initial hint delivery after start, preexisting file reading
- WR-06: Fake monotonic clock and controlled waiter testing (deadlines, 1s idle cap)
- WR-07: Lost-wake prevention barriers, stop vs cycle admission, fault vs consumer
- WR-08: Four-phase blocking (Acquisition, Reader, Cleanup, Consumer) with 2,000 burst
- WR-09: Unchanged driver error handling: NotReady, rejection without/with follow-up
- WR-10: Synchronous delivery on run thread, stop draining at all lifecycle stages
- WR-11: Concurrent run calls, single winner, losers receive INVALID_TRANSITION
- WR-12: Observer factory, schedule, and partial start failure teardown
- WR-13: Dispatcher, emitter, and empty-set liveness failure detection
- WR-14: Multi-failure preservation and ExceptionGroups
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from accounting_local_agent import (
    SOURCE_WATCH_RUNTIME_VERSION,
    SaveEventKind,
    SourceWatchRuntime,
    SourceWatchRuntimeError,
    SourceWatchRuntimeReason,
    SourceWatchRuntimeState,
    SourceWatchRuntimeView,
    XlsxSourceReadResult,
)
from accounting_local_agent.save_import_coordinator import (
    read_due_source,
)
from accounting_local_agent.source_watch_runtime import _WatchdogEventAdapter
from accounting_local_agent.xlsx_snapshot_acquisition import (
    open_stable_xlsx_snapshot,
)
from accounting_local_agent.xlsx_source_reader import (
    XlsxSourceReadError,
    read_xlsx_source_snapshot,
)
from test_xlsx_source_reader import (
    SyntheticXlsxBuilder,
    _make_uuid7,
    _sample_business_parties_row_data,
    _sample_buy_sell_row_data,
    _sample_inventory_movements_row_data,
    _sample_receipts_payments_row_data,
)
from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEvent,
)
from watchdog.observers.api import BaseObserver, EventEmitter


class FakeClock:
    """Deterministic monotonic clock source in nanoseconds."""

    def __init__(self, start_ns: int = 1_000_000_000) -> None:
        self._current_ns = start_ns

    def __call__(self) -> int:
        return self._current_ns

    def advance_ns(self, delta_ns: int) -> int:
        if delta_ns < 0:
            raise ValueError("Cannot advance fake clock backward")
        self._current_ns += delta_ns
        return self._current_ns

    def advance_seconds(self, seconds: float) -> int:
        return self.advance_ns(int(seconds * 1_000_000_000))

    def set_ns(self, new_ns: int) -> None:
        self._current_ns = new_ns


class ManagedRunnerThread:
    """Helper thread managing runner execution and error propagation."""

    def __init__(
        self,
        runtime: SourceWatchRuntime,
        consumer: Callable[[XlsxSourceReadResult], None],
    ) -> None:
        self.runtime = runtime
        self.consumer = consumer
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._run)

    def _run(self) -> None:
        try:
            self.runtime.run(self.consumer)
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


class ControlledConditionWaiter:
    """Helper for deterministic condition wait synchronization and ack assertions."""

    def __init__(self, condition: threading.Condition) -> None:
        self.condition = condition
        self.orig_wait = condition.wait
        self.waits: list[float | None] = []
        self._arrived = threading.Event()
        self._lock = threading.Lock()

    def hooked_wait(self, timeout: float | None = None) -> bool:
        with self._lock:
            self.waits.append(timeout)
        self._arrived.set()
        return self.orig_wait(timeout)

    def wait_for_ack(self, timeout: float = 5.0) -> float | None:
        """Wait until runtime loop enters wait and returns recorded wait timeout."""
        assert self._arrived.wait(timeout=timeout), "Runtime failed to arrive in wait"
        self._arrived.clear()
        with self._lock:
            return self.waits[-1]


def _build_minimal_valid_four_sheet_xlsx() -> bytes:
    """Helper building a minimal compliant 4-sheet synthetic XLSX."""
    builder = SyntheticXlsxBuilder()
    u1 = _make_uuid7(b"sheet1_row2_uuid")
    builder.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u1, 2)])
    u2 = _make_uuid7(b"sheet2_row2_uuid")
    builder.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u2, 2)])
    u3 = _make_uuid7(b"sheet3_row2_uuid")
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u3, 2)])
    u4 = _make_uuid7(b"sheet4_row2_uuid")
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u4, 2)])
    return builder.build_bytes()


class MockEmitter(EventEmitter):
    """Mock emitter thread for deterministic testing."""

    def __init__(self) -> None:
        super().__init__(cast(Any, None), cast(Any, None))
        self.daemon = True
        self.stopped = False
        self._stop_event = threading.Event()

    def run(self) -> None:
        self._stop_event.wait()

    def stop(self) -> None:
        self.stopped = True
        self._stop_event.set()


class MockObserver(BaseObserver):
    """Mock watchdog BaseObserver implementation for deterministic unit testing."""

    def __init__(self) -> None:
        super().__init__(MockEmitter, timeout=1.0)
        self.daemon = True
        self.scheduled_handlers: list[tuple[Any, str, bool]] = []
        self.stopped = False
        self._stop_event = threading.Event()
        self._mock_emitters: set[MockEmitter] = {MockEmitter()}

    @property
    def emitters(self) -> set[MockEmitter]:  # type: ignore[override]
        return self._mock_emitters

    def schedule(
        self,
        event_handler: Any,
        path: str,
        *,
        recursive: bool = False,
        event_filter: list[type[FileSystemEvent]] | None = None,
    ) -> Any:
        self.scheduled_handlers.append((event_handler, path, recursive))
        return "mock_watch"

    def start(self) -> None:
        for em in self._mock_emitters:
            if not em.is_alive():
                em.start()
        super().start()

    def run(self) -> None:
        self._stop_event.wait()

    def stop(self) -> None:
        self.stopped = True
        self._stop_event.set()
        for em in self._mock_emitters:
            em.stop()


# ---------------------------------------------------------------------------
# WR-01: Public API Exports, Views, Configuration & Validation Boundaries
# ---------------------------------------------------------------------------


def test_wr01_public_api_exports_and_version() -> None:
    """WR-01: Public API exports, version string, enums and typed error reasons."""
    assert SOURCE_WATCH_RUNTIME_VERSION == "source-watch-runtime.v1"

    # Verify Enum values
    assert SourceWatchRuntimeState.NEW.value == "new"
    assert SourceWatchRuntimeState.RUNNING.value == "running"
    assert SourceWatchRuntimeState.STOPPING.value == "stopping"
    assert SourceWatchRuntimeState.STOPPED.value == "stopped"
    assert SourceWatchRuntimeState.FAILED.value == "failed"

    assert SourceWatchRuntimeReason.INVALID_POLICY.value == "invalid_policy"
    assert SourceWatchRuntimeReason.INVALID_TRANSITION.value == "invalid_transition"
    assert (
        SourceWatchRuntimeReason.OBSERVER_START_FAILED.value == "observer_start_failed"
    )
    assert (
        SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED.value == "event_delivery_failed"
    )
    assert (
        SourceWatchRuntimeReason.OBSERVER_STOPPED_UNEXPECTEDLY.value
        == "observer_stopped_unexpectedly"
    )
    assert SourceWatchRuntimeReason.SOURCE_READ_FAILED.value == "source_read_failed"
    assert SourceWatchRuntimeReason.CONSUMER_FAILED.value == "consumer_failed"
    assert SourceWatchRuntimeReason.SHUTDOWN_FAILED.value == "shutdown_failed"

    # Verify error representation
    err = SourceWatchRuntimeError(SourceWatchRuntimeReason.INVALID_POLICY)
    assert err.reason == SourceWatchRuntimeReason.INVALID_POLICY
    assert str(err) == "[invalid_policy] Invalid configuration or policy"
    assert "INVALID_POLICY" in repr(err)


def test_wr01_source_watch_runtime_view_invariants() -> None:
    """WR-01: SourceWatchRuntimeView construction invariants."""
    # 1. Valid views
    v1 = SourceWatchRuntimeView(
        SOURCE_WATCH_RUNTIME_VERSION, SourceWatchRuntimeState.NEW, False
    )
    assert v1.state == SourceWatchRuntimeState.NEW
    assert v1.stop_requested is False

    v2 = SourceWatchRuntimeView(
        SOURCE_WATCH_RUNTIME_VERSION, SourceWatchRuntimeState.RUNNING, False
    )
    assert v2.state == SourceWatchRuntimeState.RUNNING

    v3 = SourceWatchRuntimeView(
        SOURCE_WATCH_RUNTIME_VERSION, SourceWatchRuntimeState.STOPPING, True
    )
    assert v3.state == SourceWatchRuntimeState.STOPPING

    v4 = SourceWatchRuntimeView(
        SOURCE_WATCH_RUNTIME_VERSION, SourceWatchRuntimeState.STOPPED, True
    )
    assert v4.state == SourceWatchRuntimeState.STOPPED

    v5 = SourceWatchRuntimeView(
        SOURCE_WATCH_RUNTIME_VERSION, SourceWatchRuntimeState.FAILED, False
    )
    assert v5.state == SourceWatchRuntimeState.FAILED

    v6 = SourceWatchRuntimeView(
        SOURCE_WATCH_RUNTIME_VERSION, SourceWatchRuntimeState.FAILED, True
    )
    assert v6.state == SourceWatchRuntimeState.FAILED

    # 2. Invariant violations
    with pytest.raises(SourceWatchRuntimeError, match="Invalid runtime version"):
        SourceWatchRuntimeView("bad_version", SourceWatchRuntimeState.NEW, False)

    with pytest.raises(SourceWatchRuntimeError, match="Invalid runtime state"):
        SourceWatchRuntimeView(SOURCE_WATCH_RUNTIME_VERSION, "new", False)  # type: ignore[arg-type]

    with pytest.raises(
        SourceWatchRuntimeError, match="Invalid stop_requested boolean flag"
    ):
        SourceWatchRuntimeView(
            SOURCE_WATCH_RUNTIME_VERSION,
            SourceWatchRuntimeState.NEW,
            0,  # type: ignore[arg-type]
        )

    with pytest.raises(SourceWatchRuntimeError, match="stop_requested must be False"):
        SourceWatchRuntimeView(
            SOURCE_WATCH_RUNTIME_VERSION, SourceWatchRuntimeState.NEW, True
        )

    with pytest.raises(SourceWatchRuntimeError, match="stop_requested must be False"):
        SourceWatchRuntimeView(
            SOURCE_WATCH_RUNTIME_VERSION, SourceWatchRuntimeState.RUNNING, True
        )

    with pytest.raises(SourceWatchRuntimeError, match="stop_requested must be True"):
        SourceWatchRuntimeView(
            SOURCE_WATCH_RUNTIME_VERSION, SourceWatchRuntimeState.STOPPING, False
        )

    with pytest.raises(SourceWatchRuntimeError, match="stop_requested must be True"):
        SourceWatchRuntimeView(
            SOURCE_WATCH_RUNTIME_VERSION, SourceWatchRuntimeState.STOPPED, False
        )


def test_wr01_safe_repr_no_path_leakage(tmp_path: Path) -> None:
    """WR-01: Representations do not leak sensitive file paths or data."""
    secret_dir = tmp_path / "super_secret_folder_99"
    secret_src = secret_dir / "confidential_accounting.xlsx"
    snap_root = tmp_path / "snapshots"

    runtime = SourceWatchRuntime(
        secret_src, snapshot_root=snap_root, observation_interval_seconds=0.1
    )
    view = runtime.view()
    err = SourceWatchRuntimeError(SourceWatchRuntimeReason.INVALID_POLICY)

    assert "super_secret_folder_99" not in repr(runtime)
    assert "confidential_accounting.xlsx" not in repr(runtime)
    assert "super_secret_folder_99" not in repr(view)
    assert "confidential_accounting.xlsx" not in repr(view)
    assert "super_secret_folder_99" not in repr(err)


def test_wr01_constructor_lexical_validation_and_containment(
    tmp_path: Path,
) -> None:
    """WR-01: Lexical parameter validation, path containment, and zero I/O on init."""
    src = tmp_path / "valid_parent" / "target.xlsx"
    snap_root = tmp_path / "snapshots"

    # 1. Valid initialization (zero I/O: paths need not exist yet)
    runtime = SourceWatchRuntime(
        src, snapshot_root=snap_root, observation_interval_seconds=0.05
    )
    assert runtime.source_path == src
    assert runtime.snapshot_root == snap_root
    assert runtime.observation_interval_seconds == 0.05
    assert runtime.view().state == SourceWatchRuntimeState.NEW

    # 2. Non-Path types
    with pytest.raises(
        SourceWatchRuntimeError, match="source_path must be a Path instance"
    ):
        SourceWatchRuntime(
            str(src),  # type: ignore[arg-type]
            snapshot_root=snap_root,
            observation_interval_seconds=0.05,
        )

    with pytest.raises(
        SourceWatchRuntimeError, match="snapshot_root must be a Path instance"
    ):
        SourceWatchRuntime(
            src,
            snapshot_root=str(snap_root),  # type: ignore[arg-type]
            observation_interval_seconds=0.05,
        )

    # 3. Relative segments ('..' or '.')
    with pytest.raises(
        SourceWatchRuntimeError, match="source_path cannot contain relative segments"
    ):
        SourceWatchRuntime(
            tmp_path / "parent" / ".." / "target.xlsx",
            snapshot_root=snap_root,
            observation_interval_seconds=0.05,
        )

    with pytest.raises(
        SourceWatchRuntimeError, match="snapshot_root cannot contain relative segments"
    ):
        SourceWatchRuntime(
            src,
            snapshot_root=tmp_path / "intermediate" / ".." / "snapshots",
            observation_interval_seconds=0.05,
        )

    # 4. Relative paths
    with pytest.raises(SourceWatchRuntimeError, match="source_path must be absolute"):
        SourceWatchRuntime(
            Path("relative/target.xlsx"),
            snapshot_root=snap_root,
            observation_interval_seconds=0.05,
        )

    with pytest.raises(SourceWatchRuntimeError, match="snapshot_root must be absolute"):
        SourceWatchRuntime(
            src,
            snapshot_root=Path("relative/snapshots"),
            observation_interval_seconds=0.05,
        )

    # 5. Invalid source extension & lock file
    with pytest.raises(
        SourceWatchRuntimeError, match="source_path must have .xlsx extension"
    ):
        SourceWatchRuntime(
            tmp_path / "target.xls",
            snapshot_root=snap_root,
            observation_interval_seconds=0.05,
        )

    with pytest.raises(SourceWatchRuntimeError, match="cannot be an Excel lock file"):
        SourceWatchRuntime(
            tmp_path / "~$target.xlsx",
            snapshot_root=snap_root,
            observation_interval_seconds=0.05,
        )

    # 6. Invalid observation interval
    with pytest.raises(
        SourceWatchRuntimeError, match="observation_interval_seconds must be"
    ):
        SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=cast(Any, True),
        )

    with pytest.raises(
        SourceWatchRuntimeError, match="observation_interval_seconds must be"
    ):
        SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=0.0,
        )

    with pytest.raises(
        SourceWatchRuntimeError, match="observation_interval_seconds must be"
    ):
        SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=-1.0,
        )

    with pytest.raises(
        SourceWatchRuntimeError, match="observation_interval_seconds must be"
    ):
        SourceWatchRuntime(
            src,
            snapshot_root=snap_root,
            observation_interval_seconds=float("nan"),
        )

    # 7. Snapshot root containment boundaries
    with pytest.raises(SourceWatchRuntimeError, match="cannot be equal to or inside"):
        SourceWatchRuntime(
            src,
            snapshot_root=src.parent,
            observation_interval_seconds=0.05,
        )

    with pytest.raises(SourceWatchRuntimeError, match="cannot be equal to or inside"):
        SourceWatchRuntime(
            src,
            snapshot_root=src.parent / "nested_snapshots",
            observation_interval_seconds=0.05,
        )


def test_wr01_consumer_validation_and_invalid_transition(
    tmp_path: Path,
) -> None:
    """WR-01: Non-callable consumer rejection and invalid run transitions."""
    src = tmp_path / "watch_dir" / "target.xlsx"
    snap_root = tmp_path / "snapshots"
    runtime = SourceWatchRuntime(
        src, snapshot_root=snap_root, observation_interval_seconds=0.05
    )

    # 1. Non-callable consumer
    with pytest.raises(SourceWatchRuntimeError, match="consumer must be callable"):
        runtime.run(cast(Any, "not_a_function"))
    assert runtime.view().state == SourceWatchRuntimeState.NEW

    # 2. Stop before run -> state is STOPPED
    runtime.request_stop()
    assert runtime.view().state == SourceWatchRuntimeState.STOPPED

    # 3. Calling run() on STOPPED instance
    with pytest.raises(
        SourceWatchRuntimeError, match="can only be called on a runtime in new state"
    ):
        runtime.run(lambda res: None)


# ---------------------------------------------------------------------------
# WR-02: Captured Observer Factory and Zero Allocation on Construction
# ---------------------------------------------------------------------------


def test_wr02_captured_observer_factory_and_no_alloc_on_init(
    tmp_path: Path,
) -> None:
    """WR-02: Factory proves 1 observer, exact source parent and recursive=False."""
    src = tmp_path / "watch_dir" / "workbook.xlsx"
    snap_root = tmp_path / "snapshots"

    factory_calls = 0
    created_mock: MockObserver | None = None

    def mock_factory() -> MockObserver:
        nonlocal factory_calls, created_mock
        factory_calls += 1
        created_mock = MockObserver()
        return created_mock

    # 1. Construction does not call factory
    runtime = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.05,
        _observer_factory=mock_factory,
    )
    assert factory_calls == 0

    # 2. Stop before run does not call factory
    runtime.request_stop()
    assert factory_calls == 0
    assert runtime.view().state == SourceWatchRuntimeState.STOPPED

    # 3. New instance run calls factory once with exact parent and recursive=False
    runtime2 = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.05,
        _observer_factory=mock_factory,
    )
    runtime2.request_stop()  # set stop so run exits after start
    runtime2._state = SourceWatchRuntimeState.NEW
    runtime2._stop_requested = True

    runtime2.run(lambda r: None)
    assert factory_calls == 1
    assert created_mock is not None
    assert len(created_mock.scheduled_handlers) == 1
    _handler, path, recursive = created_mock.scheduled_handlers[0]
    assert path == str(src.parent)
    assert recursive is False
    assert created_mock.stopped is True


# ---------------------------------------------------------------------------
# WR-03: Table-Driven Watchdog Event Adapter Mapping
# ---------------------------------------------------------------------------


def test_wr03_watchdog_event_adapter_table_driven_mapping(
    tmp_path: Path,
) -> None:
    """WR-03: Event adapter maps created/modified/deleted/moved without I/O."""
    src = tmp_path / "watch_dir" / "workbook.xlsx"

    received_events: list[tuple[SaveEventKind, Path, Path | None]] = []

    def mock_notify(kind: SaveEventKind, p: Path, dest: Path | None) -> None:
        received_events.append((kind, p, dest))

    faults: list[BaseException] = []
    adapter = _WatchdogEventAdapter(mock_notify, lambda e: faults.append(e))

    # 1. Mutating file events for exact target (via dispatch and on_any_event)
    adapter.dispatch(FileCreatedEvent(str(src)))
    assert received_events[-1] == (SaveEventKind.CREATED, src, None)

    adapter.dispatch(FileModifiedEvent(str(src)))
    assert received_events[-1] == (SaveEventKind.MODIFIED, src, None)

    adapter.dispatch(FileDeletedEvent(str(src)))
    assert received_events[-1] == (SaveEventKind.DELETED, src, None)

    other_src = tmp_path / "watch_dir" / "temp.xlsx"
    adapter.dispatch(FileMovedEvent(str(other_src), str(src)))
    assert received_events[-1] == (
        SaveEventKind.MOVED,
        other_src,
        cast(Path | None, src),
    )

    # 2. Directory events ignored
    count_before = len(received_events)
    adapter.dispatch(DirCreatedEvent(str(src.parent)))
    adapter.dispatch(DirModifiedEvent(str(src.parent)))
    adapter.dispatch(DirDeletedEvent(str(src.parent)))
    adapter.dispatch(DirMovedEvent(str(src.parent), str(src.parent / "moved")))
    assert len(received_events) == count_before

    # 3. Known read-only events ignored
    class _CustomReadOnlyEvent(FileSystemEvent):
        event_type = "opened"

    class _CustomClosedEvent(FileSystemEvent):
        event_type = "closed"

    adapter.dispatch(_CustomReadOnlyEvent(str(src)))
    adapter.dispatch(_CustomClosedEvent(str(src)))
    assert len(received_events) == count_before

    # 4. Unknown event kinds ignored without AttributeError or fault
    class _CustomFutureEvent(FileSystemEvent):
        event_type = "future_event"

    adapter.dispatch(_CustomFutureEvent(str(src)))
    assert len(received_events) == count_before
    assert len(faults) == 0


# ---------------------------------------------------------------------------
# WR-04: Callback Boundary Fault Handling
# ---------------------------------------------------------------------------


def test_wr04_callback_boundary_fault_handling(tmp_path: Path) -> None:
    """WR-04: Malformed mutating event closes admission and surfaces fault."""
    src = tmp_path / "watch_dir" / "workbook.xlsx"
    snap_root = tmp_path / "snapshots"

    mock_obs = MockObserver()
    runtime = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.05,
        _observer_factory=lambda: mock_obs,
    )

    faults: list[BaseException] = []
    adapter = _WatchdogEventAdapter(
        runtime._on_adapter_event, lambda e: faults.append(e)
    )

    # 1. Event with invalid path type in mutating event
    class _MalformedMutatingEvent(FileSystemEvent):
        event_type = "modified"

    malformed_event = _MalformedMutatingEvent(cast(Any, None))
    adapter.dispatch(malformed_event)
    assert len(faults) == 1
    assert isinstance(faults[0], SourceWatchRuntimeError)
    assert faults[0].reason == SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED

    # 2. Event with non-boolean is_directory
    faults.clear()
    malformed_dir_event = _MalformedMutatingEvent(str(src))
    malformed_dir_event.is_directory = cast(Any, "not_a_bool")
    adapter.dispatch(malformed_dir_event)
    assert len(faults) == 1
    assert isinstance(faults[0], SourceWatchRuntimeError)
    assert faults[0].reason == SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED


# ---------------------------------------------------------------------------
# WR-05: Initial Hint Delivery and Preexisting File Reading
# ---------------------------------------------------------------------------


def test_wr05_initial_hint_and_preexisting_file_read(
    tmp_path: Path,
) -> None:
    """WR-05: Start enqueues logical MODIFIED notice and reads preexisting file."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock = FakeClock()
    results: list[XlsxSourceReadResult] = []

    mock_obs = MockObserver()
    runtime = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs,
        _time_source=clock,
    )

    def test_consumer(res: XlsxSourceReadResult) -> None:
        results.append(res)
        runtime.request_stop()

    runner = ManagedRunnerThread(runtime, test_consumer)
    runner.start()

    try:
        time.sleep(0.05)
        # Advance clock to satisfy 2s debounce
        clock.advance_seconds(3.0)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()

        runner.join(timeout=5.0)
        runner.assert_clean_exit()
        assert len(results) == 1
        assert runtime.view().state == SourceWatchRuntimeState.STOPPED
    finally:
        runtime.request_stop()
        mock_obs.stop()


# ---------------------------------------------------------------------------
# WR-06: Fake Monotonic Clock and Controlled Waiter Testing
# ---------------------------------------------------------------------------


def test_wr06_fake_clock_deadline_boundaries_and_liveness_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-06: Controlled waiter verifies exact deadline triggers read; 0 I/O before."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock = FakeClock(start_ns=1_000_000_000)
    results: list[XlsxSourceReadResult] = []
    read_due_attempts = 0

    orig_read_due = read_due_source

    def counting_read_due(*args: Any, **kwargs: Any) -> Any:
        nonlocal read_due_attempts
        read_due_attempts += 1
        return orig_read_due(*args, **kwargs)

    monkeypatch.setattr(
        "accounting_local_agent.source_watch_runtime.read_due_source",
        counting_read_due,
    )

    mock_obs = MockObserver()
    runtime = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs,
        _time_source=clock,
    )

    waiter = ControlledConditionWaiter(runtime._condition)
    monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)

    runner = ManagedRunnerThread(runtime, lambda r: results.append(r))
    runner.start()

    try:
        # Initial wait ack: hint at t=1.0s, deadline at t=3.0s (2.0s debounce)
        # Runtime evaluates (3.0 - 1.0) = 2.0s, capped at 1.0s max idle/wait cap
        ack_timeout = waiter.wait_for_ack()
        assert ack_timeout == 1.0
        assert read_due_attempts == 0
        assert len(results) == 0

        # 1. Spurious wakes without clock advance: 0 read attempts, 0 I/O
        for _ in range(5):
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            ack_timeout = waiter.wait_for_ack()
            assert ack_timeout == 1.0
            assert read_due_attempts == 0
            assert len(results) == 0

        # 2. Advance clock to deadline - 1ns (now_ns = 2_999_999_999)
        clock.advance_ns(1_999_999_999)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()
        # 1 ns remaining before deadline: wait_s is min(max(0.0001, 1e-9), 1.0) = 0.0001
        ack_timeout = waiter.wait_for_ack()
        assert ack_timeout == 0.0001
        assert read_due_attempts == 0
        assert len(results) == 0

        # 3. Advance clock by 1ns -> reaches exact 2.0s deadline
        clock.advance_ns(1)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()
        # Read cycle is admitted, read_due_source executes, then enters IDLE (1.0s wait)
        ack_timeout = waiter.wait_for_ack()
        assert ack_timeout == 1.0
        assert read_due_attempts == 1
        assert len(results) == 1

        # 4. Latest-event debounce reset: fresh notice at t=3.5s
        clock.set_ns(3_500_000_000)
        runtime._on_adapter_event(SaveEventKind.MODIFIED, src, None)
        # Advance to t=5.499999999s (before 5.5s deadline)
        clock.set_ns(5_499_999_999)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()
        # 1 ns remaining before deadline
        ack_timeout = waiter.wait_for_ack()
        assert ack_timeout == 0.0001
        assert read_due_attempts == 1
        assert len(results) == 1

        # 5. Advance to t=5.500s (exact deadline)
        clock.set_ns(5_500_000_000)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()
        # Second read cycle admitted, read_due_source executes, then enters IDLE
        ack_timeout = waiter.wait_for_ack()
        assert ack_timeout == 1.0
        assert read_due_attempts == 2
        assert len(results) == 2

        runtime.request_stop()
        runner.join(timeout=5.0)
        runner.assert_clean_exit()
    finally:
        runtime.request_stop()
        runner.join(timeout=5.0)
        mock_obs.stop()


# ---------------------------------------------------------------------------
# WR-07: Lost-Wake Prevention, Stop Races, and Fault vs Admission
# ---------------------------------------------------------------------------


def test_wr07_case_a_stop_while_waiting_intermediate_and_final_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-07 Case A: Stop while waiting: STOPPING/STOPPED, STOPPED after join."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock_a = FakeClock()
    mock_obs_a = MockObserver()
    runtime_a = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs_a,
        _time_source=clock_a,
    )

    arrived_in_wait = threading.Event()
    orig_wait_a = runtime_a._condition.wait

    def hooked_wait_a(timeout: float | None = None) -> bool:
        # Signals arrival right before calling orig_wait, which releases _lifecycle_lock
        # upon entering condition wait, avoiding circular lock holding
        arrived_in_wait.set()
        return orig_wait_a(timeout)

    monkeypatch.setattr(runtime_a._condition, "wait", hooked_wait_a)

    delivered_a: list[XlsxSourceReadResult] = []
    runner_a = ManagedRunnerThread(runtime_a, lambda r: delivered_a.append(r))
    runner_a.start()

    try:
        assert arrived_in_wait.wait(timeout=5.0)
        runtime_a.request_stop()
        assert runtime_a.view().state in (
            SourceWatchRuntimeState.STOPPING,
            SourceWatchRuntimeState.STOPPED,
        )

        runner_a.join(timeout=5.0)
        runner_a.assert_clean_exit()
        assert runtime_a.view().state == SourceWatchRuntimeState.STOPPED
        assert len(delivered_a) == 0
    finally:
        runtime_a.request_stop()
        runner_a.join(timeout=5.0)
        mock_obs_a.stop()


def test_wr07_case_a2_intermediate_stopping_state_held_in_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-07 Case A2: Intermediate STOPPING state assertion by holding teardown."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock_a2 = FakeClock()
    mock_obs_a2 = MockObserver()
    runtime_a2 = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs_a2,
        _time_source=clock_a2,
    )

    arrived_in_wait_a2 = threading.Event()
    teardown_entered_a2 = threading.Event()
    teardown_release_a2 = threading.Event()

    orig_wait_a2 = runtime_a2._condition.wait

    def hooked_wait_a2(timeout: float | None = None) -> bool:
        arrived_in_wait_a2.set()
        return orig_wait_a2(timeout)

    def holding_stop_a2() -> None:
        teardown_entered_a2.set()
        teardown_release_a2.wait(timeout=5.0)
        mock_obs_a2._stop_event.set()

    monkeypatch.setattr(runtime_a2._condition, "wait", hooked_wait_a2)
    monkeypatch.setattr(mock_obs_a2, "stop", holding_stop_a2)

    runner_a2 = ManagedRunnerThread(runtime_a2, lambda r: None)
    runner_a2.start()

    try:
        assert arrived_in_wait_a2.wait(timeout=5.0)
        runtime_a2.request_stop()
        assert teardown_entered_a2.wait(timeout=5.0)
        assert runtime_a2.view().state == SourceWatchRuntimeState.STOPPING
        teardown_release_a2.set()

        runner_a2.join(timeout=5.0)
        runner_a2.assert_clean_exit()
        assert runtime_a2.view().state == SourceWatchRuntimeState.STOPPED
    finally:
        teardown_release_a2.set()
        runtime_a2.request_stop()
        runner_a2.join(timeout=5.0)
        mock_obs_a2.stop()


def test_wr07_case_b_stop_vs_cycle_admission_drains_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-07 Case B: Stop vs Cycle Admission: Cycle admission wins and drains."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock_b = FakeClock()
    mock_obs_b = MockObserver()
    runtime_b = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs_b,
        _time_source=clock_b,
    )

    in_cycle_admission = threading.Event()
    release_cycle_admission = threading.Event()

    def hooked_read_due_b(*args: Any, **kwargs: Any) -> Any:
        in_cycle_admission.set()
        release_cycle_admission.wait(timeout=5.0)
        return read_due_source(*args, **kwargs)

    monkeypatch.setattr(
        "accounting_local_agent.source_watch_runtime.read_due_source",
        hooked_read_due_b,
    )

    delivered_b: list[XlsxSourceReadResult] = []
    runner_b = ManagedRunnerThread(runtime_b, lambda r: delivered_b.append(r))
    runner_b.start()

    try:
        time.sleep(0.05)
        clock_b.advance_seconds(3.0)
        with runtime_b._lifecycle_lock:
            runtime_b._condition.notify_all()

        assert in_cycle_admission.wait(timeout=5.0)
        assert runtime_b._active_cycle_running is True

        runtime_b.request_stop()
        assert runtime_b.view().state == SourceWatchRuntimeState.STOPPING

        release_cycle_admission.set()

        runner_b.join(timeout=5.0)
        runner_b.assert_clean_exit()
        assert len(delivered_b) == 1
        assert runtime_b.view().state == SourceWatchRuntimeState.STOPPED
    finally:
        release_cycle_admission.set()
        runtime_b.request_stop()
        runner_b.join(timeout=5.0)
        mock_obs_b.stop()


def test_wr07_case_c_fault_before_consumer_admission_suppresses_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-07 Case C: Fault before consumer admission suppresses unadmitted consumer."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock_c = FakeClock()
    mock_obs_c = MockObserver()
    runtime_c = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs_c,
        _time_source=clock_c,
    )

    in_read_c = threading.Event()
    release_read_c = threading.Event()

    def hooked_read_due_c(*args: Any, **kwargs: Any) -> Any:
        res = read_due_source(*args, **kwargs)
        in_read_c.set()
        release_read_c.wait(timeout=5.0)
        return res

    monkeypatch.setattr(
        "accounting_local_agent.source_watch_runtime.read_due_source",
        hooked_read_due_c,
    )

    delivered_c: list[XlsxSourceReadResult] = []
    runner_c = ManagedRunnerThread(runtime_c, lambda r: delivered_c.append(r))
    runner_c.start()

    try:
        time.sleep(0.05)
        clock_c.advance_seconds(3.0)
        with runtime_c._lifecycle_lock:
            runtime_c._condition.notify_all()

        assert in_read_c.wait(timeout=5.0)
        runtime_c._on_adapter_error(OSError("Injected adapter failure"))
        release_read_c.set()

        runner_c.join(timeout=5.0)
        assert len(delivered_c) == 0, "Fault must suppress unadmitted consumer"
        assert runner_c.error is not None
        assert isinstance(runner_c.error, SourceWatchRuntimeError)
        assert runner_c.error.reason == SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED
        assert runtime_c.view().state == SourceWatchRuntimeState.FAILED
    finally:
        release_read_c.set()
        runtime_c.request_stop()
        runner_c.join(timeout=5.0)
        mock_obs_c.stop()


def test_wr07_case_d_notice_between_predicate_inspection_and_wait_lost_wake_prevention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-07 Case D: Notice injected between predicate inspection and condition wait."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock_d = FakeClock(start_ns=1_000_000_000)
    mock_obs_d = MockObserver()
    runtime_d = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs_d,
        _time_source=clock_d,
    )

    arrived_before_wait_d = threading.Event()
    release_wait_d = threading.Event()
    notice_injected_d = False

    orig_wait_d = runtime_d._condition.wait

    def hooked_wait_d(timeout: float | None = None) -> bool:
        nonlocal notice_injected_d
        if not notice_injected_d:
            notice_injected_d = True
            arrived_before_wait_d.set()
            release_wait_d.wait(timeout=5.0)
        return orig_wait_d(timeout)

    monkeypatch.setattr(runtime_d._condition, "wait", hooked_wait_d)

    delivered_d: list[XlsxSourceReadResult] = []
    runner_d = ManagedRunnerThread(runtime_d, lambda r: delivered_d.append(r))
    runner_d.start()

    try:
        assert arrived_before_wait_d.wait(timeout=5.0)
        clock_d.advance_seconds(5.0)
        runtime_d._on_adapter_event(SaveEventKind.MODIFIED, src, None)
        release_wait_d.set()

        time.sleep(0.05)
        clock_d.advance_seconds(3.0)
        with runtime_d._lifecycle_lock:
            runtime_d._condition.notify_all()
        time.sleep(0.05)

        runtime_d.request_stop()
        runner_d.join(timeout=5.0)
        runner_d.assert_clean_exit()
        assert len(delivered_d) >= 1
    finally:
        release_wait_d.set()
        runtime_d.request_stop()
        runner_d.join(timeout=5.0)
        mock_obs_d.stop()


# ---------------------------------------------------------------------------
# WR-08: Four-Phase Separate Blocking with 2,000 Burst Notices Coalescing
# ---------------------------------------------------------------------------


def test_wr08_burst_coalescing_and_responsiveness_during_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-08: Four-phase blocking (Acq, Reader, Cleanup, Consumer) with 2000 burst."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    # -----------------------------------------------------------------------
    # 1. Blocking in Consumer phase + 2000 burst
    # -----------------------------------------------------------------------
    clock1 = FakeClock()
    mock_obs1 = MockObserver()
    runtime1 = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs1,
        _time_source=clock1,
    )

    in_consumer = threading.Event()
    release_consumer = threading.Event()
    results1: list[XlsxSourceReadResult] = []

    def blocking_consumer(res: XlsxSourceReadResult) -> None:
        results1.append(res)
        if len(results1) == 1:
            in_consumer.set()
            release_consumer.wait(timeout=5.0)

    runner1 = ManagedRunnerThread(runtime1, blocking_consumer)
    runner1.start()

    try:
        time.sleep(0.05)
        clock1.advance_seconds(3.0)
        with runtime1._lifecycle_lock:
            runtime1._condition.notify_all()

        assert in_consumer.wait(timeout=5.0)
        # While consumer blocks, assert active cycle is running (preventing 2nd cycle)
        assert runtime1._active_cycle_running is True

        # Send 2000 burst notices
        for _ in range(2000):
            runtime1._on_adapter_event(SaveEventKind.MODIFIED, src, None)

        # Release consumer and advance clock
        release_consumer.set()
        time.sleep(0.05)
        clock1.advance_seconds(3.0)
        with runtime1._lifecycle_lock:
            runtime1._condition.notify_all()
        time.sleep(0.05)

        runtime1.request_stop()
        runner1.join(timeout=5.0)
        runner1.assert_clean_exit()
        assert len(results1) == 2
    finally:
        release_consumer.set()
        runtime1.request_stop()
        mock_obs1.stop()

    # -----------------------------------------------------------------------
    # 2. Blocking in Acquisition phase + 2000 burst
    # -----------------------------------------------------------------------
    clock2 = FakeClock()
    mock_obs2 = MockObserver()
    runtime2 = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs2,
        _time_source=clock2,
    )

    in_acq = threading.Event()
    release_acq = threading.Event()
    orig_open_snap = open_stable_xlsx_snapshot

    def hooked_open_snap(*args: Any, **kwargs: Any) -> Any:
        in_acq.set()
        release_acq.wait(timeout=5.0)
        return orig_open_snap(*args, **kwargs)

    monkeypatch.setattr(
        "accounting_local_agent.save_import_coordinator.open_stable_xlsx_snapshot",
        hooked_open_snap,
    )

    results2: list[XlsxSourceReadResult] = []
    runner2 = ManagedRunnerThread(runtime2, lambda r: results2.append(r))
    runner2.start()

    try:
        time.sleep(0.05)
        clock2.advance_seconds(3.0)
        with runtime2._lifecycle_lock:
            runtime2._condition.notify_all()

        assert in_acq.wait(timeout=5.0)
        for _ in range(2000):
            runtime2._on_adapter_event(SaveEventKind.MODIFIED, src, None)

        release_acq.set()
        time.sleep(0.05)
        clock2.advance_seconds(3.0)
        with runtime2._lifecycle_lock:
            runtime2._condition.notify_all()
        time.sleep(0.05)

        runtime2.request_stop()
        runner2.join(timeout=5.0)
        runner2.assert_clean_exit()
        assert len(results2) == 2
    finally:
        release_acq.set()
        runtime2.request_stop()
        mock_obs2.stop()

    # -----------------------------------------------------------------------
    # 3. Blocking in Reader parsing phase + 2000 burst
    # -----------------------------------------------------------------------
    clock3 = FakeClock()
    mock_obs3 = MockObserver()
    runtime3 = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs3,
        _time_source=clock3,
    )

    in_reader = threading.Event()
    release_reader = threading.Event()
    orig_read_snap = read_xlsx_source_snapshot

    def hooked_read_snap(*args: Any, **kwargs: Any) -> Any:
        in_reader.set()
        release_reader.wait(timeout=5.0)
        return orig_read_snap(*args, **kwargs)

    monkeypatch.setattr(
        "accounting_local_agent.save_import_coordinator.read_xlsx_source_snapshot",
        hooked_read_snap,
    )

    results3: list[XlsxSourceReadResult] = []
    runner3 = ManagedRunnerThread(runtime3, lambda r: results3.append(r))
    runner3.start()

    try:
        time.sleep(0.05)
        clock3.advance_seconds(3.0)
        with runtime3._lifecycle_lock:
            runtime3._condition.notify_all()

        assert in_reader.wait(timeout=5.0)
        for _ in range(2000):
            runtime3._on_adapter_event(SaveEventKind.MODIFIED, src, None)

        release_reader.set()
        time.sleep(0.05)
        clock3.advance_seconds(3.0)
        with runtime3._lifecycle_lock:
            runtime3._condition.notify_all()
        time.sleep(0.05)

        runtime3.request_stop()
        runner3.join(timeout=5.0)
        runner3.assert_clean_exit()
        assert len(results3) == 2
    finally:
        release_reader.set()
        runtime3.request_stop()
        mock_obs3.stop()

    # -----------------------------------------------------------------------
    # 4. Blocking in Lease Exit / Cleanup phase + 2000 burst
    # -----------------------------------------------------------------------
    clock4 = FakeClock()
    mock_obs4 = MockObserver()
    runtime4 = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs4,
        _time_source=clock4,
    )

    in_cleanup = threading.Event()
    release_cleanup = threading.Event()

    @contextmanager
    def hooked_open_snap_cleanup(*args: Any, **kwargs: Any) -> Iterator[Any]:
        with orig_open_snap(*args, **kwargs) as snap_lease:
            yield snap_lease
            in_cleanup.set()
            release_cleanup.wait(timeout=5.0)

    monkeypatch.setattr(
        "accounting_local_agent.save_import_coordinator.open_stable_xlsx_snapshot",
        hooked_open_snap_cleanup,
    )

    results4: list[XlsxSourceReadResult] = []
    runner4 = ManagedRunnerThread(runtime4, lambda r: results4.append(r))
    runner4.start()

    try:
        time.sleep(0.05)
        clock4.advance_seconds(3.0)
        with runtime4._lifecycle_lock:
            runtime4._condition.notify_all()

        assert in_cleanup.wait(timeout=5.0)
        for _ in range(2000):
            runtime4._on_adapter_event(SaveEventKind.MODIFIED, src, None)

        release_cleanup.set()
        time.sleep(0.05)
        clock4.advance_seconds(3.0)
        with runtime4._lifecycle_lock:
            runtime4._condition.notify_all()
        time.sleep(0.05)

        runtime4.request_stop()
        runner4.join(timeout=5.0)
        runner4.assert_clean_exit()
        assert len(results4) == 2
    finally:
        release_cleanup.set()
        runtime4.request_stop()
        mock_obs4.stop()


# ---------------------------------------------------------------------------
# WR-09: Driver Error Handling (NotReady Retry & Reader Rejection)
# ---------------------------------------------------------------------------


def test_wr09_case_1_notready_retries_automatically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-09 Case 1: Direct NotReady retries automatically."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock1 = FakeClock(start_ns=1_000_000_000)
    mock_obs1 = MockObserver()
    runtime1 = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs1,
        _time_source=clock1,
    )

    waiter1 = ControlledConditionWaiter(runtime1._condition)
    monkeypatch.setattr(runtime1._condition, "wait", waiter1.hooked_wait)

    results1: list[XlsxSourceReadResult] = []
    runner1 = ManagedRunnerThread(runtime1, lambda r: results1.append(r))
    runner1.start()

    try:
        waiter1.wait_for_ack()
        clock1.advance_seconds(3.0)
        with runtime1._lifecycle_lock:
            runtime1._condition.notify_all()
        waiter1.wait_for_ack()
        assert len(results1) == 0

        src.write_bytes(_build_minimal_valid_four_sheet_xlsx())

        clock1.advance_seconds(3.0)
        with runtime1._lifecycle_lock:
            runtime1._condition.notify_all()
        waiter1.wait_for_ack()

        runtime1.request_stop()
        runner1.join(timeout=5.0)
        runner1.assert_clean_exit()
        assert len(results1) == 1
    finally:
        runtime1.request_stop()
        runner1.join(timeout=5.0)
        mock_obs1.stop()


def test_wr09_case_2_reader_rejection_enters_idle_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-09 Case 2: Reader rejection without follow-up enters IDLE (0 retry I/O)."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock2 = FakeClock(start_ns=1_000_000_000)
    mock_obs2 = MockObserver()
    runtime2 = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs2,
        _time_source=clock2,
    )

    waiter2 = ControlledConditionWaiter(runtime2._condition)
    monkeypatch.setattr(runtime2._condition, "wait", waiter2.hooked_wait)

    read_count = 0

    def failing_read_snap(path: Path) -> Any:
        nonlocal read_count
        read_count += 1
        raise XlsxSourceReadError("XLSX_CORRUPT_ZIP_CONTAINER")

    monkeypatch.setattr(
        "accounting_local_agent.save_import_coordinator.read_xlsx_source_snapshot",
        failing_read_snap,
    )

    results2: list[XlsxSourceReadResult] = []
    runner2 = ManagedRunnerThread(runtime2, lambda r: results2.append(r))
    runner2.start()

    try:
        waiter2.wait_for_ack()
        clock2.advance_seconds(3.0)
        with runtime2._lifecycle_lock:
            runtime2._condition.notify_all()
        ack_timeout = waiter2.wait_for_ack()
        assert ack_timeout == 1.0
        assert read_count == 1
        assert len(results2) == 0

        clock2.advance_seconds(10.0)
        with runtime2._lifecycle_lock:
            runtime2._condition.notify_all()
        ack_timeout = waiter2.wait_for_ack()
        assert ack_timeout == 1.0
        assert read_count == 1
        assert len(results2) == 0

        runtime2.request_stop()
        runner2.join(timeout=5.0)
        runner2.assert_clean_exit()
    finally:
        runtime2.request_stop()
        runner2.join(timeout=5.0)
        mock_obs2.stop()


def test_wr09_case_3_reader_rejection_with_followup_notice_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-09 Case 3: Reader rejection WITH follow-up notice sent during read."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock3 = FakeClock(start_ns=1_000_000_000)
    mock_obs3 = MockObserver()
    runtime3 = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs3,
        _time_source=clock3,
    )

    waiter3 = ControlledConditionWaiter(runtime3._condition)
    monkeypatch.setattr(runtime3._condition, "wait", waiter3.hooked_wait)

    in_read3 = threading.Event()
    release_read3 = threading.Event()
    attempt3 = 0

    def hooked_read_snap3(path: Path) -> Any:
        nonlocal attempt3
        attempt3 += 1
        if attempt3 == 1:
            in_read3.set()
            release_read3.wait(timeout=5.0)
            raise XlsxSourceReadError("XLSX_CORRUPT_ZIP_CONTAINER")
        return read_xlsx_source_snapshot(path)

    monkeypatch.setattr(
        "accounting_local_agent.save_import_coordinator.read_xlsx_source_snapshot",
        hooked_read_snap3,
    )

    results3: list[XlsxSourceReadResult] = []
    runner3 = ManagedRunnerThread(runtime3, lambda r: results3.append(r))
    runner3.start()

    try:
        waiter3.wait_for_ack()
        clock3.advance_seconds(3.0)
        with runtime3._lifecycle_lock:
            runtime3._condition.notify_all()

        assert in_read3.wait(timeout=5.0)
        runtime3._on_adapter_event(SaveEventKind.MODIFIED, src, None)

        release_read3.set()
        waiter3.wait_for_ack()
        assert attempt3 == 1
        assert len(results3) == 0

        clock3.advance_seconds(3.0)
        with runtime3._lifecycle_lock:
            runtime3._condition.notify_all()
        waiter3.wait_for_ack()
        assert attempt3 == 2
        assert len(results3) == 1

        runtime3.request_stop()
        runner3.join(timeout=5.0)
        runner3.assert_clean_exit()
    finally:
        release_read3.set()
        runtime3.request_stop()
        runner3.join(timeout=5.0)
        mock_obs3.stop()


# ---------------------------------------------------------------------------
# WR-10: Synchronous Delivery on Run Thread and Stop Draining
# ---------------------------------------------------------------------------


def test_wr10_consumer_delivery_and_stop_draining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-10: Stop during admitted read allows that result to deliver."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock = FakeClock()
    mock_obs = MockObserver()
    runtime = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs,
        _time_source=clock,
    )

    delivery_thread_id: int | None = None
    results: list[XlsxSourceReadResult] = []

    orig_open = open_stable_xlsx_snapshot

    def hook_open(*args: Any, **kwargs: Any) -> Any:
        # Request stop while read cycle is active
        runtime.request_stop()
        return orig_open(*args, **kwargs)

    monkeypatch.setattr(
        "accounting_local_agent.save_import_coordinator.open_stable_xlsx_snapshot",
        hook_open,
    )

    def consumer(res: XlsxSourceReadResult) -> None:
        nonlocal delivery_thread_id
        delivery_thread_id = threading.get_ident()
        results.append(res)

    runner = ManagedRunnerThread(runtime, consumer)
    runner.start()

    try:
        time.sleep(0.05)
        clock.advance_seconds(3.0)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()

        runner.join(timeout=5.0)
        runner.assert_clean_exit()
        assert len(results) == 1
        assert delivery_thread_id == runner._thread.ident
        assert runtime.view().state == SourceWatchRuntimeState.STOPPED
    finally:
        runtime.request_stop()
        runner.join(timeout=5.0)
        mock_obs.stop()


# ---------------------------------------------------------------------------
# WR-11: Concurrent Run Calls and Stop at All Lifecycle Stages
# ---------------------------------------------------------------------------


def test_wr11_concurrent_run_calls_single_winner_losers_invalid_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-11 Part 1: Concurrent runs produce 1 winner; losers get INVALID_TRANSITION."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock = FakeClock()
    factory_calls = 0
    mock_obs = MockObserver()

    def counting_factory() -> MockObserver:
        nonlocal factory_calls
        factory_calls += 1
        return mock_obs

    runtime = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=counting_factory,
        _time_source=clock,
    )

    waiter = ControlledConditionWaiter(runtime._condition)
    monkeypatch.setattr(runtime._condition, "wait", waiter.hooked_wait)

    winner_entered = threading.Event()
    winner_release = threading.Event()

    def holding_consumer(res: XlsxSourceReadResult) -> None:
        winner_entered.set()
        winner_release.wait(timeout=5.0)

    num_threads = 5
    barrier = threading.Barrier(num_threads)
    errors: list[BaseException | None] = [None] * num_threads

    def runner(idx: int) -> None:
        try:
            barrier.wait(timeout=5.0)
            runtime.run(holding_consumer)
        except BaseException as e:
            errors[idx] = e

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()

    try:
        waiter.wait_for_ack()

        clock.advance_seconds(3.0)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()

        assert winner_entered.wait(timeout=5.0), "Winner failed to enter consumer"

        assert runtime.view().state == SourceWatchRuntimeState.RUNNING
        assert factory_calls == 1

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
        runtime.request_stop()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive()

        successful_runs = [e for e in errors if e is None]
        assert len(successful_runs) == 1
        assert runtime.view().state == SourceWatchRuntimeState.STOPPED
    finally:
        winner_release.set()
        runtime.request_stop()
        for t in threads:
            t.join(timeout=2.0)
        mock_obs.stop()


def test_wr11_stop_requested_during_startup_before_run_loop(
    tmp_path: Path,
) -> None:
    """WR-11 Part 2: Stop requested during startup before run loop commences."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    factory_entered = threading.Event()
    factory_release = threading.Event()
    mock_obs_startup = MockObserver()

    def startup_holding_factory() -> MockObserver:
        factory_entered.set()
        factory_release.wait(timeout=5.0)
        return mock_obs_startup

    runtime_startup = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=startup_holding_factory,
    )

    runner_startup = ManagedRunnerThread(runtime_startup, lambda r: None)
    runner_startup.start()

    try:
        assert factory_entered.wait(timeout=5.0), "Startup factory entry timeout"
        runtime_startup.request_stop()
        factory_release.set()

        runner_startup.join(timeout=5.0)
        runner_startup.assert_clean_exit()
        assert runtime_startup.view().state == SourceWatchRuntimeState.STOPPED
    finally:
        factory_release.set()
        runtime_startup.request_stop()
        runner_startup.join(timeout=2.0)
        mock_obs_startup.stop()


# ---------------------------------------------------------------------------
# WR-12: Partial Startup Failure Teardown
# ---------------------------------------------------------------------------


def test_wr12_partial_startup_failure_teardown(tmp_path: Path) -> None:
    """WR-12: Startup failure stops and joins any started threads."""
    src = tmp_path / "non_existent_dir" / "workbook.xlsx"
    snap_root = tmp_path / "snapshots"

    # 1. Observer factory failure
    def crashing_factory() -> MockObserver:
        raise OSError("Injected observer allocation failure")

    runtime = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.05,
        _observer_factory=crashing_factory,
    )

    with pytest.raises(
        SourceWatchRuntimeError, match="Failed to instantiate observer"
    ) as exc_info:
        runtime.run(lambda r: None)

    assert exc_info.value.reason == SourceWatchRuntimeReason.OBSERVER_START_FAILED
    assert isinstance(exc_info.value.__cause__, OSError)
    assert runtime.view().state == SourceWatchRuntimeState.FAILED

    # 2. Schedule failure
    class ScheduleFailingObserver(MockObserver):
        def schedule(self, *args: Any, **kwargs: Any) -> Any:
            raise OSError("Injected schedule failure")

    runtime_sched = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.05,
        _observer_factory=ScheduleFailingObserver,
    )

    with pytest.raises(
        SourceWatchRuntimeError, match="Failed to schedule watch"
    ) as exc_sched:
        runtime_sched.run(lambda r: None)

    assert exc_sched.value.reason == SourceWatchRuntimeReason.OBSERVER_START_FAILED
    assert runtime_sched.view().state == SourceWatchRuntimeState.FAILED

    # 3. Partial start with worker thread started before failure
    started_worker = MockEmitter()

    class PartialFailingObserver(MockObserver):
        def schedule(self, *args: Any, **kwargs: Any) -> Any:
            super().schedule(*args, **kwargs)
            self._mock_emitters.add(started_worker)
            return "mock_watch"

        def start(self) -> None:
            started_worker.start()
            self._mock_emitters.clear()
            raise OSError("Injected start failure after worker started")

    runtime_partial = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.05,
        _observer_factory=PartialFailingObserver,
    )

    try:
        with pytest.raises(
            SourceWatchRuntimeError, match="Failed to start observer"
        ) as exc_info2:
            runtime_partial.run(lambda r: None)

        assert exc_info2.value.reason == SourceWatchRuntimeReason.OBSERVER_START_FAILED
        assert runtime_partial.view().state == SourceWatchRuntimeState.FAILED
        assert not started_worker.is_alive()
    finally:
        started_worker.stop()
        started_worker.join(timeout=1.0)


# ---------------------------------------------------------------------------
# WR-13: Dispatcher, Emitter, and Empty-Set Liveness Failures
# ---------------------------------------------------------------------------


def test_wr13_emitter_death_detected_at_loop_boundary(tmp_path: Path) -> None:
    """WR-13 Case 1: Unexpected emitter death is detected at loop boundary."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock = FakeClock()
    mock_obs = MockObserver()
    runtime = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs,
        _time_source=clock,
    )

    runner = ManagedRunnerThread(runtime, lambda r: None)
    runner.start()

    try:
        time.sleep(0.05)
        for em in mock_obs.emitters:
            em.stop()
            em.join(timeout=1.0)

        with runtime._lifecycle_lock:
            runtime._condition.notify_all()

        runner.join(timeout=5.0)
        assert runner.error is not None
        assert isinstance(runner.error, SourceWatchRuntimeError)
        assert (
            runner.error.reason
            == SourceWatchRuntimeReason.OBSERVER_STOPPED_UNEXPECTEDLY
        )
        assert runtime.view().state == SourceWatchRuntimeState.FAILED
    finally:
        runtime.request_stop()
        runner.join(timeout=5.0)
        mock_obs.stop()


def test_wr13_dispatcher_death_detected_while_emitters_remain_alive(
    tmp_path: Path,
) -> None:
    """WR-13 Case 2: Dispatcher death detected while emitters remain alive."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock2 = FakeClock()
    mock_obs2 = MockObserver()
    runtime2 = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs2,
        _time_source=clock2,
    )

    runner2 = ManagedRunnerThread(runtime2, lambda r: None)
    runner2.start()

    try:
        time.sleep(0.05)
        mock_obs2._stop_event.set()
        mock_obs2.join(timeout=1.0)
        assert not mock_obs2.is_alive(), "Dispatcher thread must be dead"
        assert len(mock_obs2.emitters) > 0
        for em in mock_obs2.emitters:
            assert em.is_alive(), "Emitter threads must remain alive"

        with runtime2._lifecycle_lock:
            runtime2._condition.notify_all()

        runner2.join(timeout=5.0)
        assert runner2.error is not None
        assert isinstance(runner2.error, SourceWatchRuntimeError)
        assert (
            runner2.error.reason
            == SourceWatchRuntimeReason.OBSERVER_STOPPED_UNEXPECTEDLY
        )
        assert runtime2.view().state == SourceWatchRuntimeState.FAILED
    finally:
        runtime2.request_stop()
        runner2.join(timeout=5.0)
        mock_obs2.stop()
        for em in mock_obs2.emitters:
            em.join(timeout=1.0)


def test_wr13_empty_emitters_set_detected_at_loop_boundary(tmp_path: Path) -> None:
    """WR-13 Case 3: Empty emitters set is detected at loop boundary."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock3 = FakeClock()
    mock_obs3 = MockObserver()
    runtime3 = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs3,
        _time_source=clock3,
    )

    runner3 = ManagedRunnerThread(runtime3, lambda r: None)
    runner3.start()

    try:
        time.sleep(0.05)
        mock_obs3._mock_emitters.clear()
        with runtime3._lifecycle_lock:
            runtime3._condition.notify_all()

        runner3.join(timeout=5.0)
        assert runner3.error is not None
        assert isinstance(runner3.error, SourceWatchRuntimeError)
        assert (
            runner3.error.reason
            == SourceWatchRuntimeReason.OBSERVER_STOPPED_UNEXPECTEDLY
        )
        assert runtime3.view().state == SourceWatchRuntimeState.FAILED
    finally:
        runtime3.request_stop()
        runner3.join(timeout=5.0)
        mock_obs3.stop()
        for em in mock_obs3.emitters:
            em.join(timeout=1.0)


# ---------------------------------------------------------------------------
# WR-14: Multi-Failure Preservation, Cause Independence and Cancellation
# ---------------------------------------------------------------------------


def test_wr14_raw_base_exception_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-14 Case 1: Raw BaseException (KeyboardInterrupt) passes through."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    # 1a. KeyboardInterrupt in observer factory
    runtime_ki_fac = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.05,
        _observer_factory=lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        runtime_ki_fac.run(lambda r: None)
    assert runtime_ki_fac.view().state == SourceWatchRuntimeState.FAILED

    # 1b. KeyboardInterrupt in coordinator init constructor
    monkeypatch.setattr(
        "accounting_local_agent.source_watch_runtime.SaveImportCoordinator",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        SourceWatchRuntime(
            src, snapshot_root=snap_root, observation_interval_seconds=0.05
        )
    monkeypatch.undo()

    # 1c. KeyboardInterrupt in consumer
    clock_ki = FakeClock()
    mock_obs_ki = MockObserver()
    runtime_ki_cons = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs_ki,
        _time_source=clock_ki,
    )

    runner_ki = ManagedRunnerThread(
        runtime_ki_cons,
        lambda r: (_ for _ in ()).throw(KeyboardInterrupt("Ctrl+C in consumer")),
    )
    runner_ki.start()
    try:
        time.sleep(0.05)
        clock_ki.advance_seconds(3.0)
        with runtime_ki_cons._lifecycle_lock:
            runtime_ki_cons._condition.notify_all()
        runner_ki.join(timeout=5.0)
        assert isinstance(runner_ki.error, KeyboardInterrupt)
        assert runtime_ki_cons.view().state == SourceWatchRuntimeState.FAILED
    finally:
        runtime_ki_cons.request_stop()
        runner_ki.join(timeout=5.0)
        mock_obs_ki._stop_event.set()
        for em in mock_obs_ki.emitters:
            em.stop()
            em.join(timeout=1.0)


def test_wr14_primary_cause_preserved_with_async_delivery_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-14 Case 2: Pre-existing cause + Async error grouped in BaseExceptionGroup."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock_r2 = FakeClock()
    mock_obs_r2 = MockObserver()
    runtime_r2 = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs_r2,
        _time_source=clock_r2,
    )

    async_original = OSError("Mock async adapter OS failure")
    primary_ki = KeyboardInterrupt("Primary driver cancellation")
    primary_ki.__cause__ = async_original

    def failing_read_due_r2(*args: Any, **kwargs: Any) -> Any:
        runtime_r2._on_adapter_error(async_original)
        raise primary_ki

    monkeypatch.setattr(
        "accounting_local_agent.source_watch_runtime.read_due_source",
        failing_read_due_r2,
    )

    runner_r2 = ManagedRunnerThread(runtime_r2, lambda r: None)
    runner_r2.start()

    try:
        time.sleep(0.05)
        clock_r2.advance_seconds(3.0)
        with runtime_r2._lifecycle_lock:
            runtime_r2._condition.notify_all()

        runner_r2.join(timeout=5.0)
        captured_r2_group = runner_r2.error

        assert captured_r2_group is not None
        assert isinstance(captured_r2_group, BaseExceptionGroup)
        assert len(captured_r2_group.exceptions) == 2

        assert captured_r2_group.exceptions[0] is primary_ki
        assert captured_r2_group.exceptions[0].__cause__ is async_original

        e1 = captured_r2_group.exceptions[1]
        assert isinstance(e1, SourceWatchRuntimeError)
        assert e1.reason == SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED
        assert e1.__cause__ is async_original

        assert runtime_r2.view().state == SourceWatchRuntimeState.FAILED
    finally:
        runtime_r2.request_stop()
        runner_r2.join(timeout=5.0)
        mock_obs_r2._stop_event.set()
        for em in mock_obs_r2.emitters:
            em.stop()
            em.join(timeout=1.0)


def test_wr14_shared_cause_preserved_across_driver_and_stop_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-14 Case 3: Shared cause between Driver error and Stop error preserved."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock_shared = FakeClock()
    mock_obs_shared = MockObserver()
    runtime_shared = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs_shared,
        _time_source=clock_shared,
    )

    shared_cause = OSError("Shared mock OS cause")
    driver_ki = KeyboardInterrupt("Driver cancellation")
    driver_ki.__cause__ = shared_cause
    stop_se = SystemExit("Stop exit failure")
    stop_se.__cause__ = shared_cause

    def failing_read_due_shared(*args: Any, **kwargs: Any) -> Any:
        raise driver_ki

    monkeypatch.setattr(
        "accounting_local_agent.source_watch_runtime.read_due_source",
        failing_read_due_shared,
    )

    def failing_stop_shared() -> None:
        mock_obs_shared._stop_event.set()
        raise stop_se

    monkeypatch.setattr(mock_obs_shared, "stop", failing_stop_shared)

    runner_shared = ManagedRunnerThread(runtime_shared, lambda r: None)
    runner_shared.start()

    try:
        time.sleep(0.05)
        clock_shared.advance_seconds(3.0)
        with runtime_shared._lifecycle_lock:
            runtime_shared._condition.notify_all()

        runner_shared.join(timeout=5.0)
        captured_shared_group = runner_shared.error

        assert captured_shared_group is not None
        assert isinstance(captured_shared_group, BaseExceptionGroup)
        assert len(captured_shared_group.exceptions) == 2

        assert captured_shared_group.exceptions[0] is driver_ki
        assert captured_shared_group.exceptions[1] is stop_se
        assert captured_shared_group.exceptions[0].__cause__ is shared_cause
        assert captured_shared_group.exceptions[1].__cause__ is shared_cause
        assert runtime_shared.view().state == SourceWatchRuntimeState.FAILED
    finally:
        runtime_shared.request_stop()
        runner_shared.join(timeout=5.0)
        mock_obs_shared._stop_event.set()
        for em in mock_obs_shared.emitters:
            em.stop()
            em.join(timeout=1.0)


def test_wr14_async_error_during_start_with_teardown_failure_grouping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-14 Case 4: Async error during Start + Stop failure produces ExceptionGroup."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    class StartAsyncErrorObserver(MockObserver):
        def start(self) -> None:
            super().start()
            if self.scheduled_handlers:
                handler = self.scheduled_handlers[0][0]
                handler.on_any_event(FileModifiedEvent(str(src)))

    runtime_async_stop = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.05,
        _observer_factory=StartAsyncErrorObserver,
    )

    custom_async_cause = RuntimeError("Custom coordinator callback marker")

    def failing_coordinator_notify(*args: Any, **kwargs: Any) -> Any:
        raise custom_async_cause

    monkeypatch.setattr(
        runtime_async_stop._coordinator, "notify", failing_coordinator_notify
    )

    captured_async_stop_group: BaseException | None = None
    try:
        runtime_async_stop.run(lambda r: None)
    except BaseException as e:
        captured_async_stop_group = e

    assert captured_async_stop_group is not None
    assert isinstance(captured_async_stop_group, SourceWatchRuntimeError)
    assert (
        captured_async_stop_group.reason
        == SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED
    )
    assert captured_async_stop_group.__cause__ is custom_async_cause

    runtime_async_stop_dual = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.05,
        _observer_factory=StartAsyncErrorObserver,
    )
    monkeypatch.setattr(
        runtime_async_stop_dual._coordinator, "notify", failing_coordinator_notify
    )

    stop_fail_err = RuntimeError("Observer stop injected failure")

    class DualFailObserver(StartAsyncErrorObserver):
        def stop(self) -> None:
            super().stop()
            raise stop_fail_err

    runtime_async_stop_dual._observer_factory = DualFailObserver

    captured_dual_group: BaseException | None = None
    try:
        runtime_async_stop_dual.run(lambda r: None)
    except BaseException as e:
        captured_dual_group = e

    assert captured_dual_group is not None
    assert isinstance(captured_dual_group, ExceptionGroup)
    assert len(captured_dual_group.exceptions) == 2
    assert isinstance(captured_dual_group.exceptions[0], SourceWatchRuntimeError)
    assert (
        captured_dual_group.exceptions[0].reason
        == SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED
    )
    assert captured_dual_group.exceptions[0].__cause__ is custom_async_cause
    assert isinstance(captured_dual_group.exceptions[1], SourceWatchRuntimeError)
    assert (
        captured_dual_group.exceptions[1].reason
        == SourceWatchRuntimeReason.SHUTDOWN_FAILED
    )
    assert captured_dual_group.exceptions[1].__cause__ is stop_fail_err


def test_wr14_three_simultaneous_standard_exceptions_cause_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-14 Case 5: 3 standard Exception failures assert exact cause identities."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    from accounting_local_agent.save_import_coordinator import read_due_source

    monkeypatch.setattr(
        "accounting_local_agent.source_watch_runtime.read_due_source",
        read_due_source,
    )

    clock_multi = FakeClock()
    mock_obs_multi = MockObserver()
    runtime_multi = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs_multi,
        _time_source=clock_multi,
    )

    consumer_entered = threading.Event()
    consumer_release = threading.Event()

    consumer_err = ValueError("Injected consumer failure")
    stop_multi_err = RuntimeError("Injected observer stop failure")
    async_err = OSError("Injected async background error")

    def crashing_consumer(r: XlsxSourceReadResult) -> None:
        consumer_entered.set()
        consumer_release.wait(timeout=5.0)
        raise consumer_err

    def failing_stop_multi() -> None:
        mock_obs_multi._stop_event.set()
        raise stop_multi_err

    monkeypatch.setattr(mock_obs_multi, "stop", failing_stop_multi)

    runner_multi = ManagedRunnerThread(runtime_multi, crashing_consumer)
    runner_multi.start()

    try:
        time.sleep(0.05)
        clock_multi.advance_seconds(3.0)
        with runtime_multi._lifecycle_lock:
            runtime_multi._condition.notify_all()

        assert consumer_entered.wait(timeout=5.0)
        runtime_multi._on_adapter_error(async_err)

        consumer_release.set()
        runner_multi.join(timeout=5.0)
        captured_group = runner_multi.error

        assert captured_group is not None
        assert isinstance(captured_group, ExceptionGroup)
        assert len(captured_group.exceptions) == 3

        e0 = captured_group.exceptions[0]
        e1 = captured_group.exceptions[1]
        e2 = captured_group.exceptions[2]

        assert isinstance(e0, SourceWatchRuntimeError)
        assert e0.reason == SourceWatchRuntimeReason.CONSUMER_FAILED
        assert e0.__cause__ is consumer_err

        assert isinstance(e1, SourceWatchRuntimeError)
        assert e1.reason == SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED
        assert e1.__cause__ is async_err

        assert isinstance(e2, SourceWatchRuntimeError)
        assert e2.reason == SourceWatchRuntimeReason.SHUTDOWN_FAILED
        assert e2.__cause__ is stop_multi_err

        assert runtime_multi.view().state == SourceWatchRuntimeState.FAILED
    finally:
        consumer_release.set()
        runtime_multi.request_stop()
        runner_multi.join(timeout=5.0)
        mock_obs_multi._stop_event.set()
        for em in mock_obs_multi.emitters:
            em.stop()
            em.join(timeout=1.0)


def test_wr14_worker_thread_join_failure_wrapped_in_shutdown_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-14 Case 6: Worker thread join failure in teardown wraps in SHUTDOWN_FAILED."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock_join = FakeClock()
    mock_obs_join = MockObserver()
    join_failing_emitter = MockEmitter()
    join_os_err = RuntimeError("Injected worker thread join failure")

    def failing_join(*args: Any, **kwargs: Any) -> None:
        raise join_os_err

    monkeypatch.setattr(join_failing_emitter, "join", failing_join)
    mock_obs_join._mock_emitters.add(join_failing_emitter)

    runtime_join = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs_join,
        _time_source=clock_join,
    )

    runner_join = ManagedRunnerThread(runtime_join, lambda r: None)
    runner_join.start()

    try:
        time.sleep(0.05)
        clock_join.advance_seconds(3.0)
        with runtime_join._lifecycle_lock:
            runtime_join._condition.notify_all()

        monkeypatch.setattr(join_failing_emitter, "is_alive", lambda: True)

        runtime_join.request_stop()
        runner_join.join(timeout=5.0)
        assert runner_join.error is not None
        assert isinstance(runner_join.error, SourceWatchRuntimeError)
        assert runner_join.error.reason == SourceWatchRuntimeReason.SHUTDOWN_FAILED
        assert runner_join.error.__cause__ is join_os_err
        assert runtime_join.view().state == SourceWatchRuntimeState.FAILED
    finally:
        monkeypatch.setattr(join_failing_emitter, "is_alive", lambda: False)
        runtime_join.request_stop()
        runner_join.join(timeout=5.0)
        mock_obs_join._stop_event.set()
        for em in mock_obs_join.emitters:
            em.stop()
            if em is not join_failing_emitter:
                em.join(timeout=1.0)


def test_wr14_mixed_cancellation_and_normal_stop_failure_base_exception_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-14 Case 7: Mixed Cancellation + Normal Stop produces BaseExceptionGroup."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock_mixed = FakeClock()
    mock_obs_mixed = MockObserver()
    mixed_ki = KeyboardInterrupt("Primary driver cancellation")
    mixed_stop_err = RuntimeError("Normal observer stop exception")

    def failing_read_due_mixed(*args: Any, **kwargs: Any) -> Any:
        raise mixed_ki

    monkeypatch.setattr(
        "accounting_local_agent.source_watch_runtime.read_due_source",
        failing_read_due_mixed,
    )

    def failing_stop_mixed() -> None:
        mock_obs_mixed._stop_event.set()
        raise mixed_stop_err

    monkeypatch.setattr(mock_obs_mixed, "stop", failing_stop_mixed)

    runtime_mixed = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs_mixed,
        _time_source=clock_mixed,
    )

    runner_mixed = ManagedRunnerThread(runtime_mixed, lambda r: None)
    runner_mixed.start()

    try:
        time.sleep(0.05)
        clock_mixed.advance_seconds(3.0)
        with runtime_mixed._lifecycle_lock:
            runtime_mixed._condition.notify_all()

        runner_mixed.join(timeout=5.0)
        captured_mixed_group = runner_mixed.error

        assert captured_mixed_group is not None
        assert isinstance(captured_mixed_group, BaseExceptionGroup)
        assert len(captured_mixed_group.exceptions) == 2

        assert captured_mixed_group.exceptions[0] is mixed_ki

        m1 = captured_mixed_group.exceptions[1]
        assert isinstance(m1, SourceWatchRuntimeError)
        assert m1.reason == SourceWatchRuntimeReason.SHUTDOWN_FAILED
        assert m1.__cause__ is mixed_stop_err

        assert runtime_mixed.view().state == SourceWatchRuntimeState.FAILED
    finally:
        runtime_mixed.request_stop()
        runner_mixed.join(timeout=5.0)
        mock_obs_mixed._stop_event.set()
        for em in mock_obs_mixed.emitters:
            em.stop()
            em.join(timeout=1.0)
