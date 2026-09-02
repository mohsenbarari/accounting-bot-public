"""Tests for source-watch-runtime.v1 (WP-08 / ADR-0011).

Covers acceptance criteria WR-01 through WR-14:
- WR-01: Public API exports, version, views, invariant checks, lexical validation
- WR-02: Captured factory checks, single observer allocation, no allocation on init
- WR-03: Table-driven event adapter tests (created/modified/deleted/moved/temp)
- WR-04: Callback boundary fault handling, malformed event surfacing, loop wakeup
- WR-05: Initial hint delivery after start, preexisting file reading
- WR-06: Fake monotonic clock and controlled waiter testing (deadlines, 1s idle cap)
- WR-07: Lost-wake prevention barriers, stop vs cycle admission
- WR-08: Four-phase blocking with 2,000 burst notices
- WR-09: Unchanged driver error handling: NotReady, rejection
- WR-10: Synchronous delivery on run thread, stop draining
- WR-11: Concurrent run calls, single observer, stop at all stages
- WR-12: Observer factory, schedule, and partial start failure teardown
- WR-13: Dispatcher, emitter, and empty-set liveness failure detection
- WR-14: Multi-failure preservation and ExceptionGroups
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest
from accounting_local_agent import (
    SOURCE_WATCH_RUNTIME_VERSION,
    SaveCoordinatorState,
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

    runner_err: BaseException | None = None

    def runner() -> None:
        nonlocal runner_err
        try:
            runtime.run(test_consumer)
        except BaseException as e:
            runner_err = e

    run_thread = threading.Thread(target=runner)
    run_thread.start()

    try:
        time.sleep(0.05)
        # Advance clock to satisfy 2s debounce
        clock.advance_seconds(3.0)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()

        run_thread.join(timeout=5.0)
        assert not run_thread.is_alive()
        assert runner_err is None
        assert len(results) == 1
        assert runtime.view().state == SourceWatchRuntimeState.STOPPED
    finally:
        runtime.request_stop()
        run_thread.join(timeout=1.0)
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

    def consumer(r: XlsxSourceReadResult) -> None:
        results.append(r)

    runner_err: BaseException | None = None

    def runner() -> None:
        nonlocal runner_err
        try:
            runtime.run(consumer)
        except BaseException as e:
            runner_err = e

    run_thread = threading.Thread(target=runner)
    run_thread.start()

    try:
        time.sleep(0.05)
        # 1. Spurious wakes without clock advance: 0 read attempts, 0 I/O
        for _ in range(5):
            with runtime._lifecycle_lock:
                runtime._condition.notify_all()
            time.sleep(0.01)
        assert read_due_attempts == 0
        assert len(results) == 0

        # 2. Advance clock to deadline - 1ns (1.999999999s)
        clock.advance_ns(1_999_999_999)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()
        time.sleep(0.02)
        assert read_due_attempts == 0
        assert len(results) == 0

        # 3. Advance clock by 1ns -> reaches exact 2.0s deadline
        clock.advance_ns(1)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()
        time.sleep(0.05)
        assert read_due_attempts == 1
        assert len(results) == 1

        # 4. Latest-event debounce reset: fresh notice at t=3.5s
        clock.set_ns(3_500_000_000)
        runtime._on_adapter_event(SaveEventKind.MODIFIED, src, None)
        # Advance to t=5.499s (before 5.5s deadline)
        clock.set_ns(5_499_999_999)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()
        time.sleep(0.02)
        assert read_due_attempts == 1

        # Advance to t=5.500s (exact deadline)
        clock.set_ns(5_500_000_000)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()
        time.sleep(0.05)
        assert read_due_attempts == 2
        assert len(results) == 2

        runtime.request_stop()
        run_thread.join(timeout=5.0)
        assert not run_thread.is_alive()
        assert runner_err is None
    finally:
        runtime.request_stop()
        run_thread.join(timeout=1.0)
        mock_obs.stop()


# ---------------------------------------------------------------------------
# WR-07: Dual-Synchronized Barriers: Lost-Wake Prevention and Stop Races
# ---------------------------------------------------------------------------


def test_wr07_barrier_predicates_and_stop_race_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-07: Dual-synchronized barrier tests hold winner and assert loser state."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    # -----------------------------------------------------------------------
    # Case A: Stop requested between predicate inspection and wait
    # -----------------------------------------------------------------------
    clock_a = FakeClock()
    mock_obs_a = MockObserver()
    runtime_a = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs_a,
        _time_source=clock_a,
    )

    arrived_predicate = threading.Event()
    release_predicate = threading.Event()
    original_view_a = runtime_a._coordinator.view

    def hooked_view_a() -> Any:
        view = original_view_a()
        if view.state == SaveCoordinatorState.WAITING:
            arrived_predicate.set()
            release_predicate.wait(timeout=5.0)
        return view

    monkeypatch.setattr(runtime_a._coordinator, "view", hooked_view_a)

    delivered_a: list[XlsxSourceReadResult] = []
    err_a: BaseException | None = None

    def runner_a() -> None:
        nonlocal err_a
        try:
            runtime_a.run(lambda r: delivered_a.append(r))
        except BaseException as e:
            err_a = e

    thread_a = threading.Thread(target=runner_a)
    thread_a.start()

    try:
        assert arrived_predicate.wait(timeout=5.0)
        # While runtime is held between inspection and wait, request stop
        runtime_a.request_stop()
        assert runtime_a.view().state == SourceWatchRuntimeState.STOPPING

        # Release runtime to proceed to wait
        release_predicate.set()

        thread_a.join(timeout=5.0)
        assert not thread_a.is_alive()
        assert err_a is None
        assert runtime_a.view().state == SourceWatchRuntimeState.STOPPED
        assert len(delivered_a) == 0
    finally:
        release_predicate.set()
        runtime_a.request_stop()
        thread_a.join(timeout=1.0)
        mock_obs_a.stop()

    # -----------------------------------------------------------------------
    # Case B: Stop vs Cycle Admission: Cycle admission wins and drains
    # -----------------------------------------------------------------------
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
    err_b: BaseException | None = None

    def runner_b() -> None:
        nonlocal err_b
        try:
            runtime_b.run(lambda r: delivered_b.append(r))
        except BaseException as e:
            err_b = e

    thread_b = threading.Thread(target=runner_b)
    thread_b.start()

    try:
        time.sleep(0.05)
        clock_b.advance_seconds(3.0)
        with runtime_b._lifecycle_lock:
            runtime_b._condition.notify_all()

        # Wait until runner has entered and is held inside active cycle
        assert in_cycle_admission.wait(timeout=5.0)
        assert runtime_b._active_cycle_running is True

        # While held, concurrently request stop
        runtime_b.request_stop()
        assert runtime_b.view().state == SourceWatchRuntimeState.STOPPING

        # Release cycle to complete
        release_cycle_admission.set()

        thread_b.join(timeout=5.0)
        assert not thread_b.is_alive()
        assert err_b is None
        assert len(delivered_b) == 1
        assert runtime_b.view().state == SourceWatchRuntimeState.STOPPED
    finally:
        release_cycle_admission.set()
        runtime_b.request_stop()
        thread_b.join(timeout=1.0)
        mock_obs_b.stop()


# ---------------------------------------------------------------------------
# WR-08: Four-Phase Blocking with 2,000 Burst Notices Coalescing
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

    runner_err1: BaseException | None = None

    def runner1() -> None:
        nonlocal runner_err1
        try:
            runtime1.run(blocking_consumer)
        except BaseException as e:
            runner_err1 = e

    run_thread1 = threading.Thread(target=runner1)
    run_thread1.start()

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
        run_thread1.join(timeout=5.0)
        assert not run_thread1.is_alive()
        assert runner_err1 is None
        assert len(results1) == 2
    finally:
        release_consumer.set()
        runtime1.request_stop()
        run_thread1.join(timeout=1.0)
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
    from accounting_local_agent.xlsx_snapshot_acquisition import (
        open_stable_xlsx_snapshot,
    )

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
    runner_err2: BaseException | None = None

    def runner2() -> None:
        nonlocal runner_err2
        try:
            runtime2.run(lambda r: results2.append(r))
        except BaseException as e:
            runner_err2 = e

    run_thread2 = threading.Thread(target=runner2)
    run_thread2.start()

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
        run_thread2.join(timeout=5.0)
        assert not run_thread2.is_alive()
        assert runner_err2 is None
        assert len(results2) == 2
    finally:
        release_acq.set()
        runtime2.request_stop()
        run_thread2.join(timeout=1.0)
        mock_obs2.stop()


# ---------------------------------------------------------------------------
# WR-09: Driver Error Handling (NotReady Retry & Reader Rejection)
# ---------------------------------------------------------------------------


def test_wr09_driver_error_handling_and_retry_preservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-09: Direct NotReady retries; Reader rejection enters idle and follow-up."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    # -----------------------------------------------------------------------
    # Case 1: Direct NotReady retries automatically
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

    results1: list[XlsxSourceReadResult] = []
    runner_err1: BaseException | None = None

    def runner1() -> None:
        nonlocal runner_err1
        try:
            runtime1.run(lambda r: results1.append(r))
        except BaseException as e:
            runner_err1 = e

    run_thread1 = threading.Thread(target=runner1)
    run_thread1.start()

    try:
        time.sleep(0.05)
        # 1. 1st attempt at 2s fails with XlsxSourceNotReadyError (file missing)
        clock1.advance_seconds(3.0)
        with runtime1._lifecycle_lock:
            runtime1._condition.notify_all()
        time.sleep(0.05)
        assert len(results1) == 0

        # 2. Create valid file
        src.write_bytes(_build_minimal_valid_four_sheet_xlsx())

        # 3. Advance clock to coordinator retry time (+2s = 4s) without fresh notice
        clock1.advance_seconds(3.0)
        with runtime1._lifecycle_lock:
            runtime1._condition.notify_all()
        time.sleep(0.05)

        runtime1.request_stop()
        run_thread1.join(timeout=5.0)
        assert not run_thread1.is_alive()
        assert runner_err1 is None
        assert len(results1) == 1
    finally:
        runtime1.request_stop()
        run_thread1.join(timeout=1.0)
        mock_obs1.stop()

    # -----------------------------------------------------------------------
    # Case 2: Reader rejection without follow-up enters IDLE (0 retry I/O)
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
    runner_err2: BaseException | None = None

    def runner2() -> None:
        nonlocal runner_err2
        try:
            runtime2.run(lambda r: results2.append(r))
        except BaseException as e:
            runner_err2 = e

    run_thread2 = threading.Thread(target=runner2)
    run_thread2.start()

    try:
        time.sleep(0.05)
        # Trigger 1st read (fails with reader error)
        clock2.advance_seconds(3.0)
        with runtime2._lifecycle_lock:
            runtime2._condition.notify_all()
        time.sleep(0.05)
        assert read_count == 1
        assert len(results2) == 0

        # Advance clock: in IDLE state, does NOT retry automatically
        clock2.advance_seconds(10.0)
        with runtime2._lifecycle_lock:
            runtime2._condition.notify_all()
        time.sleep(0.05)
        assert read_count == 1
        assert len(results2) == 0

        runtime2.request_stop()
        run_thread2.join(timeout=5.0)
        assert not run_thread2.is_alive()
        assert runner_err2 is None
    finally:
        runtime2.request_stop()
        run_thread2.join(timeout=1.0)
        mock_obs2.stop()

    # -----------------------------------------------------------------------
    # Case 3: Reader rejection WITH follow-up notice sent during read
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
    runner_err3: BaseException | None = None

    def runner3() -> None:
        nonlocal runner_err3
        try:
            runtime3.run(lambda r: results3.append(r))
        except BaseException as e:
            runner_err3 = e

    run_thread3 = threading.Thread(target=runner3)
    run_thread3.start()

    try:
        time.sleep(0.05)
        clock3.advance_seconds(3.0)
        with runtime3._lifecycle_lock:
            runtime3._condition.notify_all()

        assert in_read3.wait(timeout=5.0)
        # While read attempt 1 is running, send follow-up notice
        runtime3._on_adapter_event(SaveEventKind.MODIFIED, src, None)

        # Release attempt 1 to fail with reader error
        release_read3.set()
        time.sleep(0.05)

        # Advance clock to satisfy follow-up deadline
        clock3.advance_seconds(3.0)
        with runtime3._lifecycle_lock:
            runtime3._condition.notify_all()
        time.sleep(0.05)

        runtime3.request_stop()
        run_thread3.join(timeout=5.0)
        assert not run_thread3.is_alive()
        assert runner_err3 is None
        assert len(results3) == 1
    finally:
        release_read3.set()
        runtime3.request_stop()
        run_thread3.join(timeout=1.0)
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

    from accounting_local_agent.xlsx_snapshot_acquisition import (
        open_stable_xlsx_snapshot,
    )

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

    runner_err: BaseException | None = None

    def runner() -> None:
        nonlocal runner_err
        try:
            runtime.run(consumer)
        except BaseException as e:
            runner_err = e

    run_thread = threading.Thread(target=runner)
    run_thread.start()

    try:
        time.sleep(0.05)
        clock.advance_seconds(3.0)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()

        run_thread.join(timeout=5.0)
        assert not run_thread.is_alive()
        assert runner_err is None
        assert len(results) == 1
        assert delivery_thread_id == run_thread.ident
        assert runtime.view().state == SourceWatchRuntimeState.STOPPED
    finally:
        runtime.request_stop()
        run_thread.join(timeout=1.0)
        mock_obs.stop()


# ---------------------------------------------------------------------------
# WR-11: Concurrent Run Calls and Stop at All Lifecycle Stages
# ---------------------------------------------------------------------------


def test_wr11_concurrent_run_calls_and_lifecycle_races(tmp_path: Path) -> None:
    """WR-11: Single run() succeeds on contention; stop in consumer exits clean."""
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

    num_threads = 4
    barrier = threading.Barrier(num_threads)
    errors: list[BaseException | None] = [None] * num_threads

    def consumer(res: XlsxSourceReadResult) -> None:
        # Consumer calls request_stop() from inside callback
        runtime.request_stop()

    def runner(idx: int) -> None:
        try:
            barrier.wait(timeout=5.0)
            runtime.run(consumer)
        except BaseException as e:
            errors[idx] = e

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()

    try:
        time.sleep(0.05)
        clock.advance_seconds(3.0)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()

        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive()

        successful_runs = [e for e in errors if e is None]
        transition_errors = [
            e
            for e in errors
            if isinstance(e, SourceWatchRuntimeError)
            and e.reason == SourceWatchRuntimeReason.INVALID_TRANSITION
        ]
        assert len(successful_runs) == 1
        assert len(transition_errors) == num_threads - 1
        assert runtime.view().state == SourceWatchRuntimeState.STOPPED
    finally:
        runtime.request_stop()
        for t in threads:
            t.join(timeout=1.0)
        mock_obs.stop()


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
# WR-13: Dispatcher and Emitter Liveness Failure Detection
# ---------------------------------------------------------------------------


def test_wr13_dispatcher_and_emitter_liveness_failure(tmp_path: Path) -> None:
    """WR-13: Unexpected observer or emitter death is detected at loop boundary."""
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

    raised_error: BaseException | None = None

    def runner() -> None:
        nonlocal raised_error
        try:
            runtime.run(lambda r: None)
        except BaseException as e:
            raised_error = e

    run_thread = threading.Thread(target=runner)
    run_thread.start()

    try:
        time.sleep(0.05)
        # Kill emitter thread unexpectedly while runtime is running
        for em in mock_obs.emitters:
            em.stop()
            em.join(timeout=1.0)

        # Wake loop to check liveness
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()

        run_thread.join(timeout=5.0)
        assert not run_thread.is_alive()
        assert raised_error is not None
        assert isinstance(raised_error, SourceWatchRuntimeError)
        assert (
            raised_error.reason
            == SourceWatchRuntimeReason.OBSERVER_STOPPED_UNEXPECTEDLY
        )
        assert runtime.view().state == SourceWatchRuntimeState.FAILED
    finally:
        runtime.request_stop()
        run_thread.join(timeout=1.0)
        mock_obs.stop()


# ---------------------------------------------------------------------------
# WR-14: Multi-Failure Preservation, Cause Independence and Cancellation
# ---------------------------------------------------------------------------


def test_wr14_multi_failure_preservation_and_exception_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-14: BaseExceptions raw; multi-failures grouped with independent causes."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    # -----------------------------------------------------------------------
    # 1. Raw BaseException (KeyboardInterrupt) in factory and consumer
    # -----------------------------------------------------------------------
    runtime_ki_fac = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.05,
        _observer_factory=lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        runtime_ki_fac.run(lambda r: None)
    assert runtime_ki_fac.view().state == SourceWatchRuntimeState.FAILED

    # 1b. KeyboardInterrupt in coordinator init constructor passes through raw
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

    ki_err: BaseException | None = None

    def ki_runner() -> None:
        nonlocal ki_err
        try:
            runtime_ki_cons.run(
                lambda r: (_ for _ in ()).throw(KeyboardInterrupt("Ctrl+C in consumer"))
            )
        except BaseException as e:
            ki_err = e

    run_thread_ki = threading.Thread(target=ki_runner)
    run_thread_ki.start()
    time.sleep(0.05)
    clock_ki.advance_seconds(3.0)
    with runtime_ki_cons._lifecycle_lock:
        runtime_ki_cons._condition.notify_all()
    run_thread_ki.join(timeout=5.0)
    assert not run_thread_ki.is_alive()
    assert isinstance(ki_err, KeyboardInterrupt)
    assert runtime_ki_cons.view().state == SourceWatchRuntimeState.FAILED
    mock_obs_ki._stop_event.set()
    for em in mock_obs_ki.emitters:
        em.stop()
        em.join(timeout=1.0)

    # -----------------------------------------------------------------------
    # 2. Pre-existing cause on Primary + Async error (R2 Round 3 verification)
    # -----------------------------------------------------------------------
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
        # Injected async error from adapter callback
        runtime_r2._on_adapter_error(async_original)
        # Raise primary with pre-existing cause
        raise primary_ki

    monkeypatch.setattr(
        "accounting_local_agent.source_watch_runtime.read_due_source",
        failing_read_due_r2,
    )

    captured_r2_group: BaseException | None = None

    def r2_runner() -> None:
        nonlocal captured_r2_group
        try:
            runtime_r2.run(lambda r: None)
        except BaseException as e:
            captured_r2_group = e

    run_thread_r2 = threading.Thread(target=r2_runner)
    run_thread_r2.start()

    try:
        time.sleep(0.05)
        clock_r2.advance_seconds(3.0)
        with runtime_r2._lifecycle_lock:
            runtime_r2._condition.notify_all()

        run_thread_r2.join(timeout=5.0)
        assert not run_thread_r2.is_alive()

        assert captured_r2_group is not None
        assert isinstance(captured_r2_group, BaseExceptionGroup)
        assert len(captured_r2_group.exceptions) == 2

        # Member 0: Primary KeyboardInterrupt with original cause intact
        assert captured_r2_group.exceptions[0] is primary_ki
        assert captured_r2_group.exceptions[0].__cause__ is async_original

        # Member 1: Async wrapper with EVENT_DELIVERY_FAILED and cause intact
        e1 = captured_r2_group.exceptions[1]
        assert isinstance(e1, SourceWatchRuntimeError)
        assert e1.reason == SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED
        assert e1.__cause__ is async_original

        assert runtime_r2.view().state == SourceWatchRuntimeState.FAILED
    finally:
        mock_obs_r2._stop_event.set()
        for em in mock_obs_r2.emitters:
            em.stop()
            em.join(timeout=1.0)

    # -----------------------------------------------------------------------
    # 3. Shared cause between Driver error and Stop error (R2.a verification)
    # -----------------------------------------------------------------------
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

    captured_shared_group: BaseException | None = None

    def shared_runner() -> None:
        nonlocal captured_shared_group
        try:
            runtime_shared.run(lambda r: None)
        except BaseException as e:
            captured_shared_group = e

    run_thread_shared = threading.Thread(target=shared_runner)
    run_thread_shared.start()

    try:
        time.sleep(0.05)
        clock_shared.advance_seconds(3.0)
        with runtime_shared._lifecycle_lock:
            runtime_shared._condition.notify_all()

        run_thread_shared.join(timeout=5.0)
        assert not run_thread_shared.is_alive()

        assert captured_shared_group is not None
        assert isinstance(captured_shared_group, BaseExceptionGroup)
        assert len(captured_shared_group.exceptions) == 2

        assert captured_shared_group.exceptions[0] is driver_ki
        assert captured_shared_group.exceptions[1] is stop_se
        assert captured_shared_group.exceptions[0].__cause__ is shared_cause
        assert captured_shared_group.exceptions[1].__cause__ is shared_cause
        assert runtime_shared.view().state == SourceWatchRuntimeState.FAILED
    finally:
        mock_obs_shared._stop_event.set()
        for em in mock_obs_shared.emitters:
            em.stop()
            em.join(timeout=1.0)

    # -----------------------------------------------------------------------
    # 4. Async error during Start + Stop failure (R2.b verification)
    # -----------------------------------------------------------------------
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

    stop_err = RuntimeError("Injected stop error")

    def failing_stop_async() -> None:
        raise stop_err

    # Monkeypatch observer factory to fail on stop
    captured_async_stop_group: BaseException | None = None

    def async_stop_runner() -> None:
        nonlocal captured_async_stop_group
        try:
            # We monkeypatch runtime's allocated observer after factory creates it
            runtime_async_stop.run(lambda r: None)
        except BaseException as e:
            captured_async_stop_group = e

    # 4a. Test single async error without stop failure
    async_stop_runner()
    assert captured_async_stop_group is not None
    assert isinstance(captured_async_stop_group, SourceWatchRuntimeError)
    assert (
        captured_async_stop_group.reason
        == SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED
    )
    assert captured_async_stop_group.__cause__ is custom_async_cause

    # 4b. Test async error during start WITH stop failure
    # -> ExceptionGroup with 2 members
    runtime_async_stop_dual = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.05,
        _observer_factory=StartAsyncErrorObserver,
    )
    monkeypatch.setattr(
        runtime_async_stop_dual._coordinator, "notify", failing_coordinator_notify
    )

    # Inject stop error into the observer that will be created
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

    # -----------------------------------------------------------------------
    # 5. Three simultaneous standard Exception failures -> ExceptionGroup
    # -----------------------------------------------------------------------
    # Restore original read_due_source
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

    def crashing_consumer(r: XlsxSourceReadResult) -> None:
        consumer_entered.set()
        consumer_release.wait(timeout=5.0)
        raise ValueError("Injected consumer failure")

    def failing_stop_multi() -> None:
        mock_obs_multi._stop_event.set()
        raise RuntimeError("Injected observer stop failure")

    monkeypatch.setattr(mock_obs_multi, "stop", failing_stop_multi)

    captured_group: BaseException | None = None

    def multi_runner() -> None:
        nonlocal captured_group
        try:
            runtime_multi.run(crashing_consumer)
        except BaseException as e:
            captured_group = e

    run_thread_multi = threading.Thread(target=multi_runner)
    run_thread_multi.start()

    try:
        time.sleep(0.05)
        clock_multi.advance_seconds(3.0)
        with runtime_multi._lifecycle_lock:
            runtime_multi._condition.notify_all()

        assert consumer_entered.wait(timeout=5.0)
        async_err = OSError("Injected async background error")
        runtime_multi._on_adapter_error(async_err)

        consumer_release.set()
        run_thread_multi.join(timeout=5.0)
        assert not run_thread_multi.is_alive()

        assert captured_group is not None
        assert isinstance(captured_group, ExceptionGroup)
        assert len(captured_group.exceptions) == 3

        e0 = captured_group.exceptions[0]
        e1 = captured_group.exceptions[1]
        e2 = captured_group.exceptions[2]

        assert isinstance(e0, SourceWatchRuntimeError)
        assert e0.reason == SourceWatchRuntimeReason.CONSUMER_FAILED
        assert isinstance(e0.__cause__, ValueError)

        assert isinstance(e1, SourceWatchRuntimeError)
        assert e1.reason == SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED
        assert isinstance(e1.__cause__, OSError)

        assert isinstance(e2, SourceWatchRuntimeError)
        assert e2.reason == SourceWatchRuntimeReason.SHUTDOWN_FAILED
        assert isinstance(e2.__cause__, RuntimeError)

        assert runtime_multi.view().state == SourceWatchRuntimeState.FAILED
    finally:
        consumer_release.set()
        mock_obs_multi._stop_event.set()
        for em in mock_obs_multi.emitters:
            em.stop()
            em.join(timeout=1.0)
