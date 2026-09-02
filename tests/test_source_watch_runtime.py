"""Tests for source-watch-runtime.v1 (WP-08 / ADR-0011).

Covers acceptance criteria WR-01 through WR-14:
- WR-01: Public API exports, version, views, invariant checks, lexical validation
- WR-02: Captured factory checks, single observer allocation, no allocation on init
- WR-03: Table-driven event adapter tests (created/modified/deleted/moved/temp)
- WR-04: Callback boundary fault handling, malformed event surfacing, loop wakeup
- WR-05: Initial hint delivery after start, preexisting file reading
- WR-06: Fake monotonic clock and controlled waiter testing (deadlines, 1s idle cap)
- WR-07: Lost-wake prevention barriers, stop vs cycle admission races
- WR-08: 2,000 burst notices coalescing with bounded state, responsiveness
- WR-09: Unchanged WP-07 driver error handling: NotReady retry, reader rejection
- WR-10: Synchronous delivery on run thread, stop draining admitted read, async fault
- WR-11: Concurrent run calls, single observer, stop-before-run, consumer calling stop
- WR-12: Partial startup failure teardown, missing parent directory safety
- WR-13: Dispatcher and emitter liveness failure detection
- WR-14: Multi-failure preservation (ExceptionGroup/BaseExceptionGroup), cancellation
"""

from __future__ import annotations

import threading
import time
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
from accounting_local_agent.source_watch_runtime import _WatchdogEventAdapter
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

    run_thread = threading.Thread(target=lambda: runtime.run(test_consumer))
    run_thread.start()

    try:
        time.sleep(0.05)
        # Advance clock to satisfy 2s debounce
        clock.advance_seconds(3.0)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()

        run_thread.join(timeout=5.0)
        assert not run_thread.is_alive()
        assert len(results) == 1
        assert runtime.view().state == SourceWatchRuntimeState.STOPPED
    finally:
        runtime.request_stop()
        run_thread.join(timeout=1.0)
        mock_obs.stop()


# ---------------------------------------------------------------------------
# WR-06: Fake Monotonic Clock and Deadline Boundaries
# ---------------------------------------------------------------------------


def test_wr06_fake_clock_deadline_boundaries_and_liveness_wait(
    tmp_path: Path,
) -> None:
    """WR-06: Controlled waiter verifies exact deadline triggers read."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    clock = FakeClock(start_ns=1_000_000_000)
    results: list[XlsxSourceReadResult] = []

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
        runtime.request_stop()

    run_thread = threading.Thread(target=lambda: runtime.run(consumer))
    run_thread.start()

    try:
        time.sleep(0.05)
        # 1. Advance clock by 1.999999999s (1ns before 2.0s debounce)
        clock.advance_ns(1_999_999_999)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()
        time.sleep(0.05)
        assert len(results) == 0, "Must not read before deadline"

        # 2. Advance exactly 1ns (reaches 2.0s deadline)
        clock.advance_ns(1)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()

        run_thread.join(timeout=5.0)
        assert not run_thread.is_alive()
        assert len(results) == 1
    finally:
        runtime.request_stop()
        run_thread.join(timeout=1.0)
        mock_obs.stop()


# ---------------------------------------------------------------------------
# WR-07: Lost-Wake Prevention and Stop Admission Races
# ---------------------------------------------------------------------------


def test_wr07_barrier_predicates_and_stop_race_resolution(
    tmp_path: Path,
) -> None:
    """WR-07: Predicate re-checking prevents lost wakes; stop race resolved."""
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

    run_thread = threading.Thread(target=lambda: runtime.run(lambda r: None))
    run_thread.start()

    try:
        time.sleep(0.05)
        # Request stop concurrent with condition wait
        runtime.request_stop()
        run_thread.join(timeout=5.0)
        assert not run_thread.is_alive()
        assert runtime.view().state == SourceWatchRuntimeState.STOPPED
    finally:
        runtime.request_stop()
        run_thread.join(timeout=1.0)
        mock_obs.stop()


# ---------------------------------------------------------------------------
# WR-08: Burst Coalescing and Responsiveness During Blocking
# ---------------------------------------------------------------------------


def test_wr08_burst_coalescing_and_responsiveness_during_blocking(
    tmp_path: Path,
) -> None:
    """WR-08: 2000 burst notices coalesce; notices responsive during consumer."""
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

    consumer_entered = threading.Event()
    consumer_release = threading.Event()
    results: list[XlsxSourceReadResult] = []

    def blocking_consumer(res: XlsxSourceReadResult) -> None:
        results.append(res)
        consumer_entered.set()
        consumer_release.wait(timeout=5.0)

    run_thread = threading.Thread(target=lambda: runtime.run(blocking_consumer))
    run_thread.start()

    try:
        time.sleep(0.05)
        # 1. Advance clock to trigger 1st read
        clock.advance_seconds(3.0)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()

        assert consumer_entered.wait(timeout=5.0)

        # 2. While consumer is running, send 2000 burst notices
        for _i in range(2000):
            runtime._on_adapter_event(SaveEventKind.MODIFIED, src, None)

        # 3. Release consumer and advance clock for follow-up
        consumer_release.set()
        time.sleep(0.05)
        clock.advance_seconds(3.0)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()
        time.sleep(0.05)

        runtime.request_stop()
        run_thread.join(timeout=5.0)
        assert not run_thread.is_alive()
        # Exactly 2 deliveries: initial + 1 coalesced follow-up
        assert len(results) == 2
    finally:
        consumer_release.set()
        runtime.request_stop()
        run_thread.join(timeout=1.0)
        mock_obs.stop()


# ---------------------------------------------------------------------------
# WR-09: Driver Error Handling (Not Ready Retry & Reader Rejection)
# ---------------------------------------------------------------------------


def test_wr09_driver_error_handling_and_retry_preservation(
    tmp_path: Path,
) -> None:
    """WR-09: Direct NotReady retries; direct reader rejection waits."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
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

    results: list[XlsxSourceReadResult] = []

    def consumer(r: XlsxSourceReadResult) -> None:
        results.append(r)
        runtime.request_stop()

    run_thread = threading.Thread(target=lambda: runtime.run(consumer))
    run_thread.start()

    try:
        time.sleep(0.05)
        # 1. 1st attempt at 2s fails with XlsxSourceNotReadyError (file missing)
        clock.advance_seconds(3.0)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()
        time.sleep(0.05)
        assert len(results) == 0

        # 2. Create valid file now
        src.write_bytes(_build_minimal_valid_four_sheet_xlsx())

        # 3. Advance clock to coordinator retry time (+2s = 4s) without fresh notice
        clock.advance_seconds(3.0)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()

        run_thread.join(timeout=5.0)
        assert not run_thread.is_alive()
        assert len(results) == 1
    finally:
        runtime.request_stop()
        run_thread.join(timeout=1.0)
        mock_obs.stop()


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

    run_thread = threading.Thread(target=lambda: runtime.run(consumer))
    run_thread.start()

    try:
        time.sleep(0.05)
        # Advance clock to due deadline
        clock.advance_seconds(3.0)
        with runtime._lifecycle_lock:
            runtime._condition.notify_all()

        run_thread.join(timeout=5.0)
        assert not run_thread.is_alive()
        assert len(results) == 1
        assert delivery_thread_id == run_thread.ident
        assert runtime.view().state == SourceWatchRuntimeState.STOPPED
    finally:
        runtime.request_stop()
        run_thread.join(timeout=1.0)
        mock_obs.stop()


# ---------------------------------------------------------------------------
# WR-11: Concurrent Run Calls and Consumer Calling Stop
# ---------------------------------------------------------------------------


def test_wr11_concurrent_run_calls_and_lifecycle_races(tmp_path: Path) -> None:
    """WR-11: Single run() succeeds on contention; consumer stop exits clean."""
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

    def consumer(r: XlsxSourceReadResult) -> None:
        pass

    raised_error: BaseException | None = None

    def runner() -> None:
        nonlocal raised_error
        try:
            runtime.run(consumer)
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
# WR-14: Multi-Failure Preservation and Exception Groups
# ---------------------------------------------------------------------------


def test_wr14_multi_failure_preservation_and_exception_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WR-14: BaseExceptions preserved raw; multi-failures grouped in order."""
    src_dir = tmp_path / "watch_dir"
    src_dir.mkdir()
    src = src_dir / "workbook.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    # -----------------------------------------------------------------------
    # 1. Raw BaseException (KeyboardInterrupt) in factory, consumer, and stop
    # -----------------------------------------------------------------------
    # 1a. KeyboardInterrupt in factory raises raw KeyboardInterrupt directly
    runtime_ki_fac = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.05,
        _observer_factory=lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        runtime_ki_fac.run(lambda r: None)
    assert runtime_ki_fac.view().state == SourceWatchRuntimeState.FAILED

    # 1b. KeyboardInterrupt in consumer raises raw KeyboardInterrupt directly
    clock_ki = FakeClock()
    mock_obs_ki = MockObserver()
    runtime_ki_cons = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs_ki,
        _time_source=clock_ki,
    )

    def ki_consumer(r: XlsxSourceReadResult) -> None:
        raise KeyboardInterrupt("Simulated Ctrl+C in consumer")

    ki_err: BaseException | None = None

    def ki_runner() -> None:
        nonlocal ki_err
        try:
            runtime_ki_cons.run(ki_consumer)
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
    # 2. Multiple simultaneous standard Exception failures -> ExceptionGroup
    # -----------------------------------------------------------------------
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

    # Injected consumer failure
    def crashing_consumer(r: XlsxSourceReadResult) -> None:
        consumer_entered.set()
        consumer_release.wait(timeout=5.0)
        raise ValueError("Injected consumer failure")

    # Injected teardown stop failure
    def failing_stop() -> None:
        mock_obs_multi._stop_event.set()
        raise RuntimeError("Injected observer stop failure")

    monkeypatch.setattr(mock_obs_multi, "stop", failing_stop)

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
        # Advance clock to due deadline -> consumer enters
        clock_multi.advance_seconds(3.0)
        with runtime_multi._lifecycle_lock:
            runtime_multi._condition.notify_all()

        assert consumer_entered.wait(timeout=5.0)

        # While consumer is running, inject async adapter error
        async_err = OSError("Injected async background error")
        runtime_multi._on_adapter_error(async_err)

        # Release consumer to crash
        consumer_release.set()
        run_thread_multi.join(timeout=5.0)
        assert not run_thread_multi.is_alive()

        assert captured_group is not None
        assert isinstance(captured_group, ExceptionGroup)
        assert len(captured_group.exceptions) == 3

        # Order must be: run_error (consumer), async_error, teardown_error (stop)
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
            assert not em.is_alive()

    # -----------------------------------------------------------------------
    # 3. Mixed BaseExceptionGroup (KeyboardInterrupt in consumer + Stop error)
    # -----------------------------------------------------------------------
    clock_mixed = FakeClock()
    mock_obs_mixed = MockObserver()
    runtime_mixed = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.001,
        _observer_factory=lambda: mock_obs_mixed,
        _time_source=clock_mixed,
    )

    def failing_stop_mixed() -> None:
        mock_obs_mixed._stop_event.set()
        raise RuntimeError("Injected observer stop failure in mixed")

    monkeypatch.setattr(mock_obs_mixed, "stop", failing_stop_mixed)

    captured_mixed_group: BaseException | None = None

    def mixed_runner() -> None:
        nonlocal captured_mixed_group
        try:
            runtime_mixed.run(ki_consumer)
        except BaseException as e:
            captured_mixed_group = e

    run_thread_mixed = threading.Thread(target=mixed_runner)
    run_thread_mixed.start()

    try:
        time.sleep(0.05)
        clock_mixed.advance_seconds(3.0)
        with runtime_mixed._lifecycle_lock:
            runtime_mixed._condition.notify_all()
        run_thread_mixed.join(timeout=5.0)
        assert not run_thread_mixed.is_alive()

        assert captured_mixed_group is not None
        assert isinstance(captured_mixed_group, BaseExceptionGroup)
        assert len(captured_mixed_group.exceptions) == 2

        # 1st is raw KeyboardInterrupt, 2nd is SourceWatchRuntimeError(SHUTDOWN_FAILED)
        m0 = captured_mixed_group.exceptions[0]
        m1 = captured_mixed_group.exceptions[1]

        assert isinstance(m0, KeyboardInterrupt)
        assert isinstance(m1, SourceWatchRuntimeError)
        assert m1.reason == SourceWatchRuntimeReason.SHUTDOWN_FAILED
        assert isinstance(m1.__cause__, RuntimeError)

        assert runtime_mixed.view().state == SourceWatchRuntimeState.FAILED
    finally:
        mock_obs_mixed._stop_event.set()
        for em in mock_obs_mixed.emitters:
            em.stop()
            em.join(timeout=1.0)
            assert not em.is_alive()
