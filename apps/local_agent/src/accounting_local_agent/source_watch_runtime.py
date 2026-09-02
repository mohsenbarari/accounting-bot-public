"""Managed source watcher and serial read runtime.

Connects watchdog filesystem events to SaveImportCoordinator and coordinates
serial snapshot acquisition and reading under ADR-0011 and WP-08.
"""

from __future__ import annotations

import dataclasses
import enum
import math
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

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
    FileSystemEventHandler,
)
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from accounting_local_agent.save_import_coordinator import (
    SaveCoordinatorState,
    SaveEventKind,
    SaveImportCoordinator,
    read_due_source,
)
from accounting_local_agent.xlsx_snapshot_acquisition import (
    XlsxSourceNotReadyError,
)
from accounting_local_agent.xlsx_source_reader import (
    XlsxSourceReadError,
    XlsxSourceReadResult,
)

SOURCE_WATCH_RUNTIME_VERSION = "source-watch-runtime.v1"

_IGNORED_EVENT_TYPES = frozenset({"opened", "closed", "closed_no_write", "accessed"})


class SourceWatchRuntimeState(enum.Enum):
    """Lifecycle states of the source watch runtime."""

    NEW = "new"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class SourceWatchRuntimeReason(enum.Enum):
    """Typed reasons for source watch runtime errors."""

    INVALID_POLICY = "invalid_policy"
    INVALID_TRANSITION = "invalid_transition"
    OBSERVER_START_FAILED = "observer_start_failed"
    EVENT_DELIVERY_FAILED = "event_delivery_failed"
    OBSERVER_STOPPED_UNEXPECTEDLY = "observer_stopped_unexpectedly"
    SOURCE_READ_FAILED = "source_read_failed"
    CONSUMER_FAILED = "consumer_failed"
    SHUTDOWN_FAILED = "shutdown_failed"


_DEFAULT_MESSAGES: dict[SourceWatchRuntimeReason, str] = {
    SourceWatchRuntimeReason.INVALID_POLICY: "Invalid configuration or policy",
    SourceWatchRuntimeReason.INVALID_TRANSITION: "Invalid lifecycle transition",
    SourceWatchRuntimeReason.OBSERVER_START_FAILED: "Observer failed to start",
    SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED: "Event delivery callback failed",
    SourceWatchRuntimeReason.OBSERVER_STOPPED_UNEXPECTEDLY: (
        "Observer stopped unexpectedly"
    ),
    SourceWatchRuntimeReason.SOURCE_READ_FAILED: "Source read driver failed",
    SourceWatchRuntimeReason.CONSUMER_FAILED: "Consumer callback failed",
    SourceWatchRuntimeReason.SHUTDOWN_FAILED: "Shutdown or teardown failed",
}


class SourceWatchRuntimeError(Exception):
    """Typed error raised by SourceWatchRuntime."""

    def __init__(
        self,
        reason: SourceWatchRuntimeReason,
        message: str | None = None,
    ) -> None:
        if not isinstance(reason, SourceWatchRuntimeReason):
            raise ValueError("reason must be a SourceWatchRuntimeReason instance")
        self.reason = reason
        text = message or _DEFAULT_MESSAGES.get(reason, "Source watch runtime error")
        super().__init__(f"[{reason.value}] {text}")

    def __repr__(self) -> str:
        return f"SourceWatchRuntimeError({self.reason!r})"


@dataclasses.dataclass(frozen=True, slots=True)
class SourceWatchRuntimeView:
    """Frozen, path-free view of runtime state and stop request flag."""

    version: str
    state: SourceWatchRuntimeState
    stop_requested: bool

    def __post_init__(self) -> None:
        if self.version != SOURCE_WATCH_RUNTIME_VERSION:
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "Invalid runtime version",
            )
        if not isinstance(self.state, SourceWatchRuntimeState):
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "Invalid runtime state",
            )
        if type(self.stop_requested) is not bool:
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "Invalid stop_requested boolean flag",
            )
        if (
            self.state
            in (
                SourceWatchRuntimeState.NEW,
                SourceWatchRuntimeState.RUNNING,
            )
            and self.stop_requested
        ):
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "stop_requested must be False for new or running state",
            )
        if (
            self.state
            in (
                SourceWatchRuntimeState.STOPPING,
                SourceWatchRuntimeState.STOPPED,
            )
            and not self.stop_requested
        ):
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "stop_requested must be True for stopping or stopped state",
            )

    def __repr__(self) -> str:
        return (
            f"SourceWatchRuntimeView(version={self.version!r}, "
            f"state={self.state!r}, stop_requested={self.stop_requested!r})"
        )


class _WatchdogEventAdapter(FileSystemEventHandler):
    """Adapts native watchdog filesystem events to coordinator notifications."""

    def __init__(
        self,
        notify_fn: Callable[[SaveEventKind, Path, Path | None], None],
        fault_fn: Callable[[BaseException], None],
    ) -> None:
        super().__init__()
        self._notify_fn = notify_fn
        self._fault_fn = fault_fn

    def dispatch(self, event: FileSystemEvent) -> None:
        """Override watchdog dispatch to prevent unhandled exceptions escaping."""
        try:
            self.on_any_event(event)
        except BaseException as exc:
            self._fault_fn(exc)

    def on_any_event(self, event: FileSystemEvent) -> None:
        try:
            # 1. Check is_directory field
            is_dir = getattr(event, "is_directory", False)
            if type(is_dir) is not bool:
                event_type = getattr(event, "event_type", None)
                if event_type in ("created", "modified", "deleted", "moved"):
                    raise SourceWatchRuntimeError(
                        SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED,
                        "Mutating event has non-boolean is_directory field",
                    )
                return

            if is_dir or isinstance(
                event,
                (
                    DirCreatedEvent,
                    DirModifiedEvent,
                    DirDeletedEvent,
                    DirMovedEvent,
                ),
            ):
                return

            # 2. Ignore known read-only event types
            event_type = getattr(event, "event_type", None)
            if not isinstance(event_type, str):
                return
            if event_type in _IGNORED_EVENT_TYPES:
                return

            # 3. Decode paths without filesystem I/O
            src_raw = getattr(event, "src_path", None)
            if src_raw is None or not isinstance(src_raw, (str, bytes)):
                # Mutating event with invalid/missing src_path: fail visibly
                if event_type in ("created", "modified", "deleted", "moved"):
                    raise SourceWatchRuntimeError(
                        SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED,
                        "Mutating event has invalid source path",
                    )
                return

            src_path = Path(
                os.fsdecode(src_raw) if isinstance(src_raw, bytes) else str(src_raw)
            )

            dest_path: Path | None = None
            if event_type == "moved" or isinstance(event, FileMovedEvent):
                dest_raw = getattr(event, "dest_path", None)
                if dest_raw is None or not isinstance(dest_raw, (str, bytes)):
                    raise SourceWatchRuntimeError(
                        SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED,
                        "Move event has invalid destination path",
                    )
                dest_path = Path(
                    os.fsdecode(dest_raw)
                    if isinstance(dest_raw, bytes)
                    else str(dest_raw)
                )

            # 4. Map mutating event types
            kind: SaveEventKind | None = None
            if event_type == "created" or isinstance(event, FileCreatedEvent):
                kind = SaveEventKind.CREATED
            elif event_type == "modified" or isinstance(event, FileModifiedEvent):
                kind = SaveEventKind.MODIFIED
            elif event_type == "deleted" or isinstance(event, FileDeletedEvent):
                kind = SaveEventKind.DELETED
            elif event_type == "moved" or isinstance(event, FileMovedEvent):
                kind = SaveEventKind.MOVED
            else:
                # Unknown event type: ignore without fault
                return

            self._notify_fn(kind, src_path, dest_path)
        except BaseException as exc:
            self._fault_fn(exc)


class SourceWatchRuntime:
    """Managed source watcher and serial read runtime."""

    def __init__(
        self,
        source_path: Path,
        *,
        snapshot_root: Path,
        observation_interval_seconds: float,
        _observer_factory: Any = None,
        _time_source: Any = None,
    ) -> None:
        # 1. Type validation
        if type(source_path) is not Path and not isinstance(source_path, Path):
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "source_path must be a Path instance",
            )
        if type(snapshot_root) is not Path and not isinstance(snapshot_root, Path):
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "snapshot_root must be a Path instance",
            )
        if type(observation_interval_seconds) is bool or not isinstance(
            observation_interval_seconds, (int, float)
        ):
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "observation_interval_seconds must be a float or int",
            )
        if (
            not math.isfinite(observation_interval_seconds)
            or observation_interval_seconds <= 0.0
        ):
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "observation_interval_seconds must be positive and finite",
            )

        # 2. Relative segments ('..' or '.') check BEFORE normalization
        if ".." in source_path.parts or "." in source_path.parts:
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "source_path cannot contain relative segments ('..' or '.')",
            )
        if ".." in snapshot_root.parts or "." in snapshot_root.parts:
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "snapshot_root cannot contain relative segments ('..' or '.')",
            )

        # 3. Lexical source path checks
        if not source_path.is_absolute():
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "source_path must be absolute",
            )
        if source_path.suffix.lower() != ".xlsx":
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "source_path must have .xlsx extension",
            )
        if source_path.name.startswith("~$"):
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "source_path cannot be an Excel lock file (~$)",
            )

        # 4. Lexical snapshot root checks
        if not snapshot_root.is_absolute():
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "snapshot_root must be absolute",
            )

        # 5. Containment check: snapshot_root cannot be equal to or inside source parent
        norm_src_parent = os.path.normcase(os.path.normpath(str(source_path.parent)))
        norm_snap_root = os.path.normcase(os.path.normpath(str(snapshot_root)))
        src_parts = Path(norm_src_parent).parts
        snap_parts = Path(norm_snap_root).parts

        if (
            norm_snap_root == norm_src_parent
            or snap_parts[: len(src_parts)] == src_parts
        ):
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "snapshot_root cannot be equal to or inside source parent directory",
            )

        # 6. Initialization without filesystem I/O
        self._source_path = source_path
        self._snapshot_root = snapshot_root
        self._observation_interval_seconds = float(observation_interval_seconds)
        self._observer_factory = _observer_factory or Observer
        self._time_source = _time_source or time.monotonic_ns

        try:
            self._coordinator = SaveImportCoordinator(
                source_path,
                _time_source=self._time_source,
            )
        except Exception as exc:
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "Failed to initialize coordinator",
            ) from exc

        self._state = SourceWatchRuntimeState.NEW
        self._stop_requested = False
        self._lifecycle_lock = threading.Lock()
        self._condition = threading.Condition(self._lifecycle_lock)

        self._admission_open = False
        self._async_error: BaseException | None = None
        self._observer: BaseObserver | None = None
        self._expected_workers: list[threading.Thread] = []
        self._active_cycle_running = False

    @property
    def source_path(self) -> Path:
        """Configured absolute source path."""
        return self._source_path

    @property
    def snapshot_root(self) -> Path:
        """Configured absolute snapshot storage root."""
        return self._snapshot_root

    @property
    def observation_interval_seconds(self) -> float:
        """Configured snapshot observation interval in seconds."""
        return self._observation_interval_seconds

    def view(self) -> SourceWatchRuntimeView:
        """Return a frozen, path-free view of current runtime state."""
        with self._lifecycle_lock:
            return SourceWatchRuntimeView(
                version=SOURCE_WATCH_RUNTIME_VERSION,
                state=self._state,
                stop_requested=self._stop_requested,
            )

    def request_stop(self) -> None:
        """Request runtime shutdown in a thread-safe and non-blocking manner."""
        with self._lifecycle_lock:
            if self._stop_requested and self._state in (
                SourceWatchRuntimeState.STOPPING,
                SourceWatchRuntimeState.STOPPED,
                SourceWatchRuntimeState.FAILED,
            ):
                return
            self._stop_requested = True
            self._admission_open = False
            if self._state == SourceWatchRuntimeState.NEW:
                self._state = SourceWatchRuntimeState.STOPPED
            elif self._state == SourceWatchRuntimeState.RUNNING:
                self._state = SourceWatchRuntimeState.STOPPING
            self._condition.notify_all()

    def _on_adapter_event(
        self,
        kind: SaveEventKind,
        src_path: Path,
        dest_path: Path | None,
    ) -> None:
        """Adapter callback delivering adapted filesystem events to coordinator."""
        with self._lifecycle_lock:
            if not self._admission_open:
                return
            if self._async_error is not None:
                return
            try:
                accepted = self._coordinator.notify(
                    kind, src_path, destination_path=dest_path
                )
            except BaseException as exc:
                self._async_error = exc
                self._admission_open = False
                self._condition.notify_all()
                return

            if accepted:
                self._condition.notify_all()

    def _on_adapter_error(self, exc: BaseException) -> None:
        """Adapter callback surfacing asynchronous decoding or delivery faults."""
        with self._lifecycle_lock:
            if not self._admission_open:
                return
            self._admission_open = False
            if self._async_error is None:
                self._async_error = exc
            self._condition.notify_all()

    def _check_liveness_locked(self) -> None:
        """Check liveness of active observer and emitters under lifecycle lock."""
        if self._observer is None or self._stop_requested:
            return
        if self._async_error is not None:
            return

        if (
            isinstance(self._observer, threading.Thread)
            and not self._observer.is_alive()
        ):
            self._async_error = SourceWatchRuntimeError(
                SourceWatchRuntimeReason.OBSERVER_STOPPED_UNEXPECTEDLY,
                "Observer dispatcher thread stopped unexpectedly",
            )
            self._admission_open = False
            return

        current_emitters = getattr(self._observer, "emitters", None)
        if (
            current_emitters is not None
            and not current_emitters
            and self._expected_workers
        ):
            self._async_error = SourceWatchRuntimeError(
                SourceWatchRuntimeReason.OBSERVER_STOPPED_UNEXPECTEDLY,
                "Observer emitters missing or stopped unexpectedly",
            )
            self._admission_open = False
            return

        for w in self._expected_workers:
            if w is not self._observer and not w.is_alive():
                self._async_error = SourceWatchRuntimeError(
                    SourceWatchRuntimeReason.OBSERVER_STOPPED_UNEXPECTEDLY,
                    "Observer worker thread stopped unexpectedly",
                )
                self._admission_open = False
                return

    def run(self, consumer: Callable[[XlsxSourceReadResult], None]) -> None:
        """Execute the source watch runtime loop on the calling thread.

        Blocks until stopped or failed, delivering successful Reader results
        serially to the consumer callback.
        """
        if not callable(consumer):
            raise SourceWatchRuntimeError(
                SourceWatchRuntimeReason.INVALID_POLICY,
                "consumer must be callable",
            )

        with self._lifecycle_lock:
            if self._state != SourceWatchRuntimeState.NEW:
                raise SourceWatchRuntimeError(
                    SourceWatchRuntimeReason.INVALID_TRANSITION,
                    "run() can only be called on a runtime in new state",
                )
            self._state = SourceWatchRuntimeState.RUNNING
            self._admission_open = True

        start_error: BaseException | None = None
        run_error: BaseException | None = None
        teardown_errors: list[BaseException] = []

        try:
            # 1. Allocate observer
            try:
                observer = self._observer_factory()
                self._observer = observer
                if isinstance(observer, threading.Thread):
                    if observer not in self._expected_workers:
                        self._expected_workers.append(observer)
            except BaseException as exc:
                if isinstance(exc, Exception):
                    wrap_err = SourceWatchRuntimeError(
                        SourceWatchRuntimeReason.OBSERVER_START_FAILED,
                        "Failed to instantiate observer",
                    )
                    wrap_err.__cause__ = exc
                    start_error = wrap_err
                    raise wrap_err from exc
                start_error = exc
                raise

            # 2. Schedule source parent directory
            try:
                adapter = _WatchdogEventAdapter(
                    self._on_adapter_event,
                    self._on_adapter_error,
                )
                observer.schedule(
                    adapter,
                    str(self._source_path.parent),
                    recursive=False,
                )
                emitters = getattr(observer, "emitters", None)
                if emitters is not None:
                    for em in emitters:
                        if (
                            isinstance(em, threading.Thread)
                            and em not in self._expected_workers
                        ):
                            self._expected_workers.append(em)
            except BaseException as exc:
                if isinstance(exc, Exception):
                    wrap_err = SourceWatchRuntimeError(
                        SourceWatchRuntimeReason.OBSERVER_START_FAILED,
                        "Failed to schedule watch on source parent",
                    )
                    wrap_err.__cause__ = exc
                    start_error = wrap_err
                    raise wrap_err from exc
                start_error = exc
                raise

            # 3. Start observer and capture any newly added emitters
            try:
                observer.start()
                emitters = getattr(observer, "emitters", None)
                if emitters is not None:
                    for em in emitters:
                        if (
                            isinstance(em, threading.Thread)
                            and em not in self._expected_workers
                        ):
                            self._expected_workers.append(em)
            except BaseException as exc:
                emitters = getattr(observer, "emitters", None)
                if emitters is not None:
                    for em in emitters:
                        if (
                            isinstance(em, threading.Thread)
                            and em not in self._expected_workers
                        ):
                            self._expected_workers.append(em)
                if isinstance(exc, Exception):
                    wrap_err = SourceWatchRuntimeError(
                        SourceWatchRuntimeReason.OBSERVER_START_FAILED,
                        "Failed to start observer",
                    )
                    wrap_err.__cause__ = exc
                    start_error = wrap_err
                    raise wrap_err from exc
                start_error = exc
                raise

            # 4. Check liveness and enqueue initial logical MODIFIED notice
            with self._lifecycle_lock:
                self._check_liveness_locked()
                if self._async_error is not None or self._stop_requested:
                    return
                try:
                    self._coordinator.notify(
                        SaveEventKind.MODIFIED,
                        self._source_path,
                    )
                except BaseException as exc:
                    if isinstance(exc, Exception):
                        wrap_err = SourceWatchRuntimeError(
                            SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED,
                            "Initial notification to coordinator failed",
                        )
                        wrap_err.__cause__ = exc
                        start_error = wrap_err
                        raise wrap_err from exc
                    start_error = exc
                    raise
                self._condition.notify_all()

            # 5. Main serial execution loop
            while True:
                with self._lifecycle_lock:
                    if self._async_error is not None:
                        break
                    if self._stop_requested and not self._active_cycle_running:
                        break

                    self._check_liveness_locked()
                    if self._async_error is not None:
                        break

                    coord_view = self._coordinator.view()
                    if coord_view.state == SaveCoordinatorState.FAULTED:
                        run_error = SourceWatchRuntimeError(
                            SourceWatchRuntimeReason.SOURCE_READ_FAILED,
                            "Coordinator entered faulted state",
                        )
                        break

                    now_ns = self._time_source()
                    if (
                        coord_view.state == SaveCoordinatorState.WAITING
                        and coord_view.next_due_ns is not None
                    ):
                        if now_ns >= coord_view.next_due_ns:
                            # Work cycle admitted
                            self._active_cycle_running = True
                        else:
                            rem_s = (coord_view.next_due_ns - now_ns) / 1_000_000_000.0
                            wait_s = min(max(0.0001, rem_s), 1.0)
                            self._condition.wait(wait_s)
                            continue
                    else:
                        # IDLE: wait up to 1.0s for events or liveness
                        self._condition.wait(1.0)
                        continue

                # Execute read_due_source OUTSIDE lifecycle lock
                read_res: XlsxSourceReadResult | None = None
                read_failed = False
                try:
                    read_res = read_due_source(
                        self._coordinator,
                        snapshot_root=self._snapshot_root,
                        observation_interval_seconds=self._observation_interval_seconds,
                    )
                except (XlsxSourceNotReadyError, XlsxSourceReadError):
                    # Handled coordinator retry/idle state; continue loop
                    with self._lifecycle_lock:
                        self._active_cycle_running = False
                    continue
                except BaseException as exc:
                    if isinstance(exc, Exception):
                        wrap_err = SourceWatchRuntimeError(
                            SourceWatchRuntimeReason.SOURCE_READ_FAILED,
                        )
                        wrap_err.__cause__ = exc
                        run_error = wrap_err
                    else:
                        run_error = exc
                    read_failed = True

                if read_failed:
                    with self._lifecycle_lock:
                        self._active_cycle_running = False
                    break

                # Deliver result if read succeeded and not None
                if read_res is not None:
                    should_deliver = True
                    with self._lifecycle_lock:
                        if self._async_error is not None:
                            should_deliver = False

                    if should_deliver:
                        try:
                            consumer(read_res)
                        except BaseException as c_exc:
                            if isinstance(c_exc, Exception):
                                wrap_err = SourceWatchRuntimeError(
                                    SourceWatchRuntimeReason.CONSUMER_FAILED,
                                )
                                wrap_err.__cause__ = c_exc
                                run_error = wrap_err
                            else:
                                run_error = c_exc
                            with self._lifecycle_lock:
                                self._active_cycle_running = False
                            break

                with self._lifecycle_lock:
                    self._active_cycle_running = False

        except BaseException as loop_exc:
            if start_error is None and run_error is None:
                if isinstance(loop_exc, SourceWatchRuntimeError):
                    run_error = loop_exc
                elif isinstance(loop_exc, Exception):
                    wrap_err = SourceWatchRuntimeError(
                        SourceWatchRuntimeReason.SOURCE_READ_FAILED
                    )
                    wrap_err.__cause__ = loop_exc
                    run_error = wrap_err
                else:
                    run_error = loop_exc

        finally:
            # 6. Teardown: stop event admission and observer, join all threads
            with self._lifecycle_lock:
                self._admission_open = False
                self._stop_requested = True
                if self._state == SourceWatchRuntimeState.RUNNING:
                    self._state = SourceWatchRuntimeState.STOPPING

            # 6a. Stop observer
            if self._observer is not None:
                try:
                    self._observer.stop()
                except BaseException as s_exc:
                    if isinstance(s_exc, Exception):
                        wrap_err = SourceWatchRuntimeError(
                            SourceWatchRuntimeReason.SHUTDOWN_FAILED,
                            "Failed to stop observer",
                        )
                        wrap_err.__cause__ = s_exc
                        teardown_errors.append(wrap_err)
                    else:
                        teardown_errors.append(s_exc)

            # 6b. Stop all individual expected workers if still alive and stoppable
            for w in self._expected_workers:
                if w is not self._observer and hasattr(w, "stop") and callable(w.stop):
                    if w.is_alive():
                        try:
                            w.stop()
                        except BaseException as w_exc:
                            if isinstance(w_exc, Exception):
                                wrap_err = SourceWatchRuntimeError(
                                    SourceWatchRuntimeReason.SHUTDOWN_FAILED,
                                    "Failed to stop worker thread",
                                )
                                wrap_err.__cause__ = w_exc
                                teardown_errors.append(wrap_err)
                            else:
                                teardown_errors.append(w_exc)

            # 6c. Join all expected workers in deterministic order
            for t in self._expected_workers:
                if (
                    getattr(t, "ident", None) is not None
                    and t.is_alive()
                    and t is not threading.current_thread()
                ):
                    try:
                        t.join(timeout=5.0)
                        if t.is_alive():
                            teardown_errors.append(
                                SourceWatchRuntimeError(
                                    SourceWatchRuntimeReason.SHUTDOWN_FAILED,
                                    "Owned thread failed to join within timeout",
                                )
                            )
                    except BaseException as j_exc:
                        if isinstance(j_exc, Exception):
                            wrap_err = SourceWatchRuntimeError(
                                SourceWatchRuntimeReason.SHUTDOWN_FAILED,
                                "Thread join failed",
                            )
                            wrap_err.__cause__ = j_exc
                            teardown_errors.append(wrap_err)
                        else:
                            teardown_errors.append(j_exc)

            # 7. Collect all errors in deterministic order:
            # run_error, async_error, teardown_errors
            ordered_errors: list[BaseException] = []
            if start_error is not None:
                ordered_errors.append(start_error)
            elif run_error is not None:
                ordered_errors.append(run_error)

            with self._lifecycle_lock:
                async_err = self._async_error

            if async_err is not None:
                final_async_err: BaseException
                if isinstance(async_err, SourceWatchRuntimeError):
                    final_async_err = async_err
                elif isinstance(async_err, Exception):
                    wrap_async = SourceWatchRuntimeError(
                        SourceWatchRuntimeReason.EVENT_DELIVERY_FAILED,
                    )
                    wrap_async.__cause__ = async_err
                    final_async_err = wrap_async
                else:
                    final_async_err = async_err

                if not any(
                    e is final_async_err or e is async_err for e in ordered_errors
                ):
                    ordered_errors.append(final_async_err)

            for te in teardown_errors:
                if not any(e is te for e in ordered_errors):
                    ordered_errors.append(te)

            # Update final terminal state
            with self._lifecycle_lock:
                if ordered_errors:
                    self._state = SourceWatchRuntimeState.FAILED
                else:
                    self._state = SourceWatchRuntimeState.STOPPED

            # Raise single error or ExceptionGroup/BaseExceptionGroup
            if ordered_errors:
                if len(ordered_errors) == 1:
                    raise ordered_errors[0]

                if any(
                    isinstance(e, BaseException) and not isinstance(e, Exception)
                    for e in ordered_errors
                ):
                    raise BaseExceptionGroup(
                        "source_watch_runtime encountered multiple failures",
                        ordered_errors,
                    )
                std_excs = [e for e in ordered_errors if isinstance(e, Exception)]
                raise ExceptionGroup(
                    "source_watch_runtime encountered multiple failures",
                    std_excs,
                )

    def __repr__(self) -> str:
        with self._lifecycle_lock:
            st = self._state
            sr = self._stop_requested
        return (
            f"SourceWatchRuntime(state={st!r}, stop_requested={sr!r}, "
            f"version={SOURCE_WATCH_RUNTIME_VERSION!r})"
        )
