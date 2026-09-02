"""Save debounce, coalescing, and source-read coordinator.

Implements save-import-coordinator.v1 under ADR-0010 and WP-07.
Coordinates notifications about one exact configured source XLSX file,
providing a two-second quiet period, coalescing of burst notifications,
single-attempt token reservation, and synchronized snapshot acquisition + reader driver.
"""

from __future__ import annotations

import dataclasses
import enum
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from accounting_local_agent.xlsx_snapshot_acquisition import (
    XlsxSourceNotReadyError,
    open_stable_xlsx_snapshot,
)
from accounting_local_agent.xlsx_source_reader import (
    XlsxSourceReadError,
    read_xlsx_source_snapshot,
)

if TYPE_CHECKING:
    from accounting_local_agent.xlsx_source_reader import XlsxSourceReadResult

SAVE_IMPORT_COORDINATOR_VERSION: str = "save-import-coordinator.v1"
SAVE_DEBOUNCE_NS: int = 2_000_000_000


class SaveEventKind(enum.StrEnum):
    """File notification event kinds."""

    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    MOVED = "moved"
    OPENED = "opened"
    CLOSED = "closed"
    ACCESSED = "accessed"


class SaveCoordinatorState(enum.StrEnum):
    """Lifecycle states of the Save coordinator."""

    IDLE = "idle"
    WAITING = "waiting"
    RUNNING = "running"
    FAULTED = "faulted"


class SourceReadOutcome(enum.StrEnum):
    """Outcomes of a source read attempt."""

    SUCCESS = "success"
    SOURCE_NOT_READY = "source_not_ready"
    READER_REJECTED = "reader_rejected"
    FAULTED = "faulted"


class SaveCoordinatorError(Exception):
    """Base error for save import coordinator operations."""

    def __init__(self, reason: str = "", message: str = "") -> None:
        self.reason = reason
        self._message = message
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        if self.reason and self._message:
            return f"[{self.reason}] {self._message}"
        if self.reason:
            return f"[{self.reason}]"
        return self._message or self.__class__.__name__

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(reason={self.reason!r})"


class SaveCoordinatorPolicyError(SaveCoordinatorError):
    """Raised when policy, path validation, or argument constraints are violated."""

    def __init__(self, message: str = "") -> None:
        super().__init__(reason="invalid_policy", message=message)


class SaveCoordinatorStateError(SaveCoordinatorError):
    """Raised when an invalid state transition, token, or time anomaly occurs."""

    def __init__(self, message: str = "") -> None:
        super().__init__(reason="invalid_transition", message=message)


class SourceReadAttempt:
    """Immutable opaque capability token representing one active attempt reservation."""

    __slots__ = ("_coordinator_id", "_started_at_ns", "_token_id")
    _coordinator_id: str
    _started_at_ns: int
    _token_id: str

    def __init__(self, coordinator_id: str, token_id: str, started_at_ns: int) -> None:
        if not isinstance(coordinator_id, str) or not isinstance(token_id, str):
            raise SaveCoordinatorPolicyError("Token identifiers must be strings")
        if type(started_at_ns) is not int or started_at_ns < 0:
            raise SaveCoordinatorPolicyError("started_at_ns must be a non-negative int")
        object.__setattr__(self, "_coordinator_id", coordinator_id)
        object.__setattr__(self, "_token_id", token_id)
        object.__setattr__(self, "_started_at_ns", started_at_ns)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("SourceReadAttempt is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("SourceReadAttempt is immutable")

    def __copy__(self) -> SourceReadAttempt:
        return self

    def __deepcopy__(self, memo: Any) -> SourceReadAttempt:
        return self

    def __repr__(self) -> str:
        return f"<SourceReadAttempt token={self._token_id[:8]}>"


@dataclasses.dataclass(frozen=True, slots=True)
class SaveCoordinatorView:
    """Immutable, path-free view of coordinator scheduling state."""

    version: str
    state: SaveCoordinatorState
    pending: bool
    next_due_ns: int | None

    def __post_init__(self) -> None:
        if self.version != SAVE_IMPORT_COORDINATOR_VERSION:
            raise SaveCoordinatorPolicyError("Invalid coordinator version")
        if not isinstance(self.state, SaveCoordinatorState):
            raise SaveCoordinatorPolicyError("Invalid coordinator state")
        if type(self.pending) is not bool:
            raise SaveCoordinatorPolicyError("pending must be a boolean")

        if self.state == SaveCoordinatorState.IDLE:
            if self.pending is not False:
                raise SaveCoordinatorPolicyError("IDLE state must have pending=False")
            if self.next_due_ns is not None:
                raise SaveCoordinatorPolicyError(
                    "IDLE state must have next_due_ns=None"
                )
        elif self.state == SaveCoordinatorState.WAITING:
            if self.pending is not True:
                raise SaveCoordinatorPolicyError("WAITING state must have pending=True")
            if (
                self.next_due_ns is None
                or type(self.next_due_ns) is not int
                or self.next_due_ns < 0
            ):
                raise SaveCoordinatorPolicyError(
                    "WAITING state must have non-negative int next_due_ns"
                )
        elif self.state == SaveCoordinatorState.RUNNING:
            if self.next_due_ns is not None:
                raise SaveCoordinatorPolicyError(
                    "RUNNING state must have next_due_ns=None"
                )
        elif self.state == SaveCoordinatorState.FAULTED:
            if self.pending is not True:
                raise SaveCoordinatorPolicyError("FAULTED state must have pending=True")
            if self.next_due_ns is not None:
                raise SaveCoordinatorPolicyError(
                    "FAULTED state must have next_due_ns=None"
                )

    def __repr__(self) -> str:
        return (
            f"SaveCoordinatorView(version={self.version!r}, "
            f"state={self.state.value!r}, "
            f"pending={self.pending}, "
            f"next_due_ns={self.next_due_ns})"
        )


def _validate_and_normalize_configured_path(
    path: Any, *, name: str = "source_path"
) -> tuple[Path, str]:
    if not isinstance(path, Path):
        raise SaveCoordinatorPolicyError(f"{name} must be a Path instance")
    raw_str = str(path)
    if not path.is_absolute():
        raise SaveCoordinatorPolicyError(f"{name} must be an absolute path")

    norm_str = os.path.normpath(raw_str)
    if ".." in path.parts or ".." in Path(norm_str).parts or norm_str.endswith(".."):
        raise SaveCoordinatorPolicyError(
            f"{name} contains unresolved parent directory components"
        )

    if path.suffix.lower() != ".xlsx":
        raise SaveCoordinatorPolicyError(f"{name} must have a .xlsx extension")

    if path.name.startswith("~$"):
        raise SaveCoordinatorPolicyError(
            f"{name} must not be an Excel temporary lock file"
        )

    canonical_key = os.path.normcase(norm_str)
    return Path(norm_str), canonical_key


def _validate_notice_path(path: Any, *, name: str) -> str:
    if not isinstance(path, Path):
        raise SaveCoordinatorPolicyError(f"{name} must be a Path instance")
    raw_str = str(path)
    if not path.is_absolute():
        raise SaveCoordinatorPolicyError(f"{name} must be an absolute path")

    norm_str = os.path.normpath(raw_str)
    if ".." in path.parts or ".." in Path(norm_str).parts or norm_str.endswith(".."):
        raise SaveCoordinatorPolicyError(
            f"{name} contains unresolved parent directory components"
        )

    return os.path.normcase(norm_str)


class SaveImportCoordinator:
    """Deterministic, thread-safe coordinator for Save notifications and attempts."""

    def __init__(
        self,
        source_path: Path,
        *,
        _time_source: Callable[[], int] | None = None,
    ) -> None:
        self._source_path, self._canonical_source_key = (
            _validate_and_normalize_configured_path(source_path, name="source_path")
        )
        self._coordinator_id: str = uuid.uuid4().hex
        self._lock: threading.Lock = threading.Lock()
        self._state: SaveCoordinatorState = SaveCoordinatorState.IDLE
        self._latest_notice_ns: int | None = None
        self._next_due_ns: int | None = None
        self._pending_followup: bool = False
        self._active_attempt: SourceReadAttempt | None = None
        self._time_source: Callable[[], int] = (
            _time_source if _time_source is not None else time.monotonic_ns
        )
        self._last_clock_ns: int = 0

    @property
    def source_path(self) -> Path:
        """The configured source XLSX path."""
        return self._source_path

    def _get_time_ns(self) -> int:
        try:
            t = self._time_source()
        except Exception as exc:
            raise SaveCoordinatorStateError("Clock source failed") from exc

        if type(t) is not int or t < 0:
            raise SaveCoordinatorStateError("Clock source returned invalid time")
        if t < self._last_clock_ns:
            raise SaveCoordinatorStateError("Monotonic clock went backwards")
        self._last_clock_ns = t
        return t

    def notify(
        self,
        kind: SaveEventKind,
        source_path: Path,
        *,
        destination_path: Path | None = None,
        is_directory: bool = False,
    ) -> bool:
        """Process a filesystem event notification.

        Returns True if the notice matched the configured source path and
        affected state, False if the notice was ignored or unrelated.
        """
        if not isinstance(kind, SaveEventKind):
            raise SaveCoordinatorPolicyError("Invalid event kind")

        if type(is_directory) is not bool:
            raise SaveCoordinatorPolicyError("is_directory must be a boolean")

        src_key = _validate_notice_path(source_path, name="source_path")

        dst_key: str | None = None
        if kind == SaveEventKind.MOVED:
            if destination_path is None:
                raise SaveCoordinatorPolicyError(
                    "destination_path is required for MOVED events"
                )
            dst_key = _validate_notice_path(destination_path, name="destination_path")
        else:
            if destination_path is not None:
                raise SaveCoordinatorPolicyError(
                    "destination_path must be None for non-MOVED events"
                )

        if is_directory:
            return False
        if kind in (
            SaveEventKind.OPENED,
            SaveEventKind.CLOSED,
            SaveEventKind.ACCESSED,
        ):
            return False

        matched = src_key == self._canonical_source_key
        if not matched and dst_key is not None:
            matched = dst_key == self._canonical_source_key

        if not matched:
            return False

        with self._lock:
            now = self._get_time_ns()
            if self._state == SaveCoordinatorState.IDLE:
                self._state = SaveCoordinatorState.WAITING
                self._latest_notice_ns = now
                self._next_due_ns = now + SAVE_DEBOUNCE_NS
                self._pending_followup = False
            elif self._state == SaveCoordinatorState.WAITING:
                self._latest_notice_ns = now
                self._next_due_ns = now + SAVE_DEBOUNCE_NS
            elif self._state == SaveCoordinatorState.RUNNING:
                self._latest_notice_ns = now
                self._pending_followup = True
            elif self._state == SaveCoordinatorState.FAULTED:
                self._latest_notice_ns = now
                self._pending_followup = True

            return True

    def take_due(self) -> SourceReadAttempt | None:
        """Atomically reserve an attempt if work is due.

        Returns an opaque SourceReadAttempt capability token if due, or None.
        """
        with self._lock:
            now = self._get_time_ns()
            if self._state == SaveCoordinatorState.WAITING:
                if self._next_due_ns is not None and now >= self._next_due_ns:
                    token_id = uuid.uuid4().hex
                    attempt = SourceReadAttempt(
                        coordinator_id=self._coordinator_id,
                        token_id=token_id,
                        started_at_ns=now,
                    )
                    self._state = SaveCoordinatorState.RUNNING
                    self._active_attempt = attempt
                    self._next_due_ns = None
                    self._pending_followup = False
                    return attempt
            return None

    def finish(self, attempt: SourceReadAttempt, outcome: SourceReadOutcome) -> None:
        """Complete an active attempt with a specific outcome."""
        if not isinstance(outcome, SourceReadOutcome):
            raise SaveCoordinatorPolicyError("Invalid finish outcome")

        with self._lock:
            if not isinstance(attempt, SourceReadAttempt):
                raise SaveCoordinatorStateError(
                    "attempt must be a SourceReadAttempt instance"
                )

            if (
                self._state != SaveCoordinatorState.RUNNING
                or self._active_attempt is not attempt
            ):
                raise SaveCoordinatorStateError(
                    "Attempt token is not active or coordinator is not running"
                )

            now = self._get_time_ns()
            self._active_attempt = None

            if outcome == SourceReadOutcome.SUCCESS:
                if self._pending_followup:
                    self._state = SaveCoordinatorState.WAITING
                    latest = (
                        self._latest_notice_ns
                        if self._latest_notice_ns is not None
                        else now
                    )
                    self._next_due_ns = latest + SAVE_DEBOUNCE_NS
                    self._pending_followup = False
                else:
                    self._state = SaveCoordinatorState.IDLE
                    self._next_due_ns = None
                    self._latest_notice_ns = None
            elif outcome == SourceReadOutcome.SOURCE_NOT_READY:
                self._state = SaveCoordinatorState.WAITING
                latest = (
                    self._latest_notice_ns
                    if self._latest_notice_ns is not None
                    else now
                )
                self._next_due_ns = max(
                    now + SAVE_DEBOUNCE_NS, latest + SAVE_DEBOUNCE_NS
                )
                self._pending_followup = False
            elif outcome == SourceReadOutcome.READER_REJECTED:
                if self._pending_followup:
                    self._state = SaveCoordinatorState.WAITING
                    latest = (
                        self._latest_notice_ns
                        if self._latest_notice_ns is not None
                        else now
                    )
                    self._next_due_ns = latest + SAVE_DEBOUNCE_NS
                    self._pending_followup = False
                else:
                    self._state = SaveCoordinatorState.IDLE
                    self._next_due_ns = None
                    self._latest_notice_ns = None
            elif outcome == SourceReadOutcome.FAULTED:
                self._state = SaveCoordinatorState.FAULTED
                self._next_due_ns = None
                self._pending_followup = True

    def resume_after_fault(self) -> None:
        """Explicitly resume coordination after being in FAULTED state."""
        with self._lock:
            if self._state != SaveCoordinatorState.FAULTED:
                raise SaveCoordinatorStateError(
                    "Cannot resume when not in FAULTED state"
                )
            if self._active_attempt is not None:
                raise SaveCoordinatorStateError("Cannot resume with active attempt")

            now = self._get_time_ns()
            self._state = SaveCoordinatorState.WAITING
            self._next_due_ns = now + SAVE_DEBOUNCE_NS
            self._pending_followup = False

    def view(self) -> SaveCoordinatorView:
        """Return an immutable snapshot view of current coordinator state."""
        with self._lock:
            pending: bool
            if self._state == SaveCoordinatorState.IDLE:
                pending = False
            elif self._state in (
                SaveCoordinatorState.WAITING,
                SaveCoordinatorState.FAULTED,
            ):
                pending = True
            elif self._state == SaveCoordinatorState.RUNNING:
                pending = self._pending_followup

            return SaveCoordinatorView(
                version=SAVE_IMPORT_COORDINATOR_VERSION,
                state=self._state,
                pending=pending,
                next_due_ns=self._next_due_ns,
            )

    def __repr__(self) -> str:
        v = self.view()
        return (
            f"SaveImportCoordinator(version={v.version!r}, "
            f"state={v.state.value!r}, pending={v.pending})"
        )


def _guarded_force_fault(
    coordinator: SaveImportCoordinator,
    attempt: SourceReadAttempt,
) -> None:
    """Token-scoped failure guard releasing active attempt to FAULTED."""
    with coordinator._lock:
        if coordinator._active_attempt is attempt:
            coordinator._active_attempt = None
            coordinator._state = SaveCoordinatorState.FAULTED
            coordinator._next_due_ns = None
            coordinator._pending_followup = True


def read_due_source(
    coordinator: SaveImportCoordinator,
    *,
    snapshot_root: Path,
    observation_interval_seconds: float,
) -> XlsxSourceReadResult | None:
    """Reserve and execute one snapshot acquisition and source-read attempt.

    Returns XlsxSourceReadResult on complete success
    (acquisition + reading + lease cleanup). Returns None when no work is due.
    Re-raises errors with original causes and updates coordinator state.
    """
    if not isinstance(coordinator, SaveImportCoordinator):
        raise SaveCoordinatorPolicyError(
            "coordinator must be a SaveImportCoordinator instance"
        )

    attempt = coordinator.take_due()
    if attempt is None:
        return None

    work_exc: BaseException | None = None
    result: XlsxSourceReadResult | None = None
    outcome: SourceReadOutcome = SourceReadOutcome.FAULTED

    try:
        with open_stable_xlsx_snapshot(
            coordinator.source_path,
            snapshot_root,
            observation_interval_seconds,
        ) as snapshot:
            result = read_xlsx_source_snapshot(snapshot.snapshot_path)
        outcome = SourceReadOutcome.SUCCESS
    except XlsxSourceNotReadyError as exc:
        outcome = SourceReadOutcome.SOURCE_NOT_READY
        work_exc = exc
    except XlsxSourceReadError as exc:
        outcome = SourceReadOutcome.READER_REJECTED
        work_exc = exc
    except BaseException as exc:
        outcome = SourceReadOutcome.FAULTED
        work_exc = exc

    finish_exc: BaseException | None = None
    guard_exc: BaseException | None = None
    try:
        coordinator.finish(attempt, outcome)
    except BaseException as exc:
        finish_exc = exc
        try:
            _guarded_force_fault(coordinator, attempt)
        except BaseException as g_exc:
            guard_exc = g_exc

    all_excs: list[BaseException] = [
        e for e in (work_exc, finish_exc, guard_exc) if e is not None
    ]

    if not all_excs:
        return result

    if len(all_excs) == 1:
        raise all_excs[0]

    if any(
        isinstance(e, BaseException) and not isinstance(e, Exception) for e in all_excs
    ):
        raise BaseExceptionGroup(
            "read_due_source encountered multiple failures",
            all_excs,
        )
    std_excs = [e for e in all_excs if isinstance(e, Exception)]
    raise ExceptionGroup(
        "read_due_source encountered multiple failures",
        std_excs,
    )
