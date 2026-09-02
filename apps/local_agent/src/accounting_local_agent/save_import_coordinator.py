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
            raise SaveCoordinatorPolicyError(
                f"Invalid coordinator version: {self.version!r}, "
                f"expected {SAVE_IMPORT_COORDINATOR_VERSION!r}"
            )
        if not isinstance(self.state, SaveCoordinatorState):
            raise SaveCoordinatorPolicyError(f"Invalid state: {self.state!r}")
        if not isinstance(self.pending, bool):
            raise SaveCoordinatorPolicyError(
                f"pending must be a bool, got {type(self.pending)}"
            )

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
                or not isinstance(self.next_due_ns, int)
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
        raise SaveCoordinatorPolicyError(
            f"{name} must be a Path instance, got {type(path)}"
        )
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
        raise SaveCoordinatorPolicyError(
            f"{name} must be a Path instance, got {type(path)}"
        )
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

        if not isinstance(t, int) or t < 0:
            raise SaveCoordinatorStateError(
                f"Clock source returned invalid time: {t!r}"
            )
        if t < self._last_clock_ns:
            raise SaveCoordinatorStateError(
                f"Monotonic clock went backwards: {t} < {self._last_clock_ns}"
            )
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
            try:
                kind = SaveEventKind(kind)
            except (ValueError, TypeError) as exc:
                raise SaveCoordinatorPolicyError(
                    f"Invalid event kind: {kind!r}"
                ) from exc

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
                    f"destination_path must be None for {kind.value} events"
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
                    self._state = SaveCoordinatorState.RUNNING
                    attempt = SourceReadAttempt(
                        coordinator_id=self._coordinator_id,
                        token_id=uuid.uuid4().hex,
                        started_at_ns=now,
                    )
                    self._active_attempt = attempt
                    self._next_due_ns = None
                    self._pending_followup = False
                    return attempt
            return None

    def finish(self, attempt: SourceReadAttempt, outcome: SourceReadOutcome) -> None:
        """Complete an active attempt with a specific outcome."""
        if not isinstance(outcome, SourceReadOutcome):
            try:
                outcome = SourceReadOutcome(outcome)
            except (ValueError, TypeError) as exc:
                raise SaveCoordinatorPolicyError(
                    f"Invalid finish outcome: {outcome!r}"
                ) from exc

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
    try:
        with coordinator._lock:
            if coordinator._active_attempt is attempt:
                coordinator._active_attempt = None
                coordinator._state = SaveCoordinatorState.FAULTED
                coordinator._next_due_ns = None
                coordinator._pending_followup = True
    except Exception:
        pass


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
            f"coordinator must be a SaveImportCoordinator instance, "
            f"got {type(coordinator)}"
        )

    attempt = coordinator.take_due()
    if attempt is None:
        return None

    outcome: SourceReadOutcome = SourceReadOutcome.FAULTED
    token_finished = False

    try:
        try:
            with open_stable_xlsx_snapshot(
                coordinator.source_path,
                snapshot_root,
                observation_interval_seconds,
            ) as snapshot:
                result = read_xlsx_source_snapshot(snapshot.snapshot_path)
        except XlsxSourceNotReadyError:
            outcome = SourceReadOutcome.SOURCE_NOT_READY
            raise
        except XlsxSourceReadError:
            outcome = SourceReadOutcome.READER_REJECTED
            raise
        except BaseException:
            outcome = SourceReadOutcome.FAULTED
            raise
        else:
            outcome = SourceReadOutcome.SUCCESS
            return result
    except BaseException as orig_exc:
        try:
            coordinator.finish(attempt, outcome)
            token_finished = True
        except Exception as finish_exc:
            _guarded_force_fault(coordinator, attempt)
            raise orig_exc from finish_exc
        raise orig_exc
    finally:
        if not token_finished:
            try:
                coordinator.finish(attempt, outcome)
            except Exception:
                _guarded_force_fault(coordinator, attempt)
                raise
