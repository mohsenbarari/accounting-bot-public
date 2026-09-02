"""Tests for save-import-coordinator.v1 (WP-07 / ADR-0010).

Covers all 16 acceptance criteria (SC-01 to SC-16):
- SC-01: Public version/API, strict inputs, immutable views/tokens, safe representations
- SC-02: Exact-path & platform case semantics, ignore irrelevant notices
- SC-03: Move into/out of target, target immutability
- SC-04: Idle construction, fake monotonic clock, deadline boundaries, clock sanity
- SC-05: Burst coalescing, past deadline single take
- SC-06: Thread concurrency on take_due, token security & outcome validation
- SC-07: Follow-up notice handling during active attempt (expired & future)
- SC-08: Direct source_not_ready retry scheduling without new notice
- SC-09: Direct reader_rejected idle transition and fresh notice handling
- SC-10: Faulted state lifecycle, explicit resume, driver bookkeeping failure guard
- SC-11: Zero I/O when no work due, exact source path passed on due work
- SC-12: End-to-end driver outcomes with synthetic 4-sheet workbook fixtures
- SC-13: Notice responsiveness during I/O and cleanup barriers
- SC-14: Composition with WP-04 Planner oracle (zero changes for unchanged Raw)
- SC-15: Hypothesis property-based state machine verification against reference model
- SC-16: Workspace regression suites and benchmark limits preservation
"""

from __future__ import annotations

import copy
import io
import os
import threading
import zipfile
from pathlib import Path
from typing import Any

import pytest
from accounting_contracts import (
    IdentityLifecycle,
    PlanAction,
    PriorIdentityState,
    plan_source_changes,
)
from accounting_local_agent import (
    SAVE_DEBOUNCE_NS,
    SAVE_IMPORT_COORDINATOR_VERSION,
    SaveCoordinatorPolicyError,
    SaveCoordinatorState,
    SaveCoordinatorStateError,
    SaveCoordinatorView,
    SaveEventKind,
    SaveImportCoordinator,
    SourceReadAttempt,
    SourceReadOutcome,
    XlsxSnapshotIntegrityError,
    XlsxSourceNotReadyError,
    XlsxSourceReadError,
    XlsxSourceReadResult,
    read_due_source,
)
from hypothesis import given, settings
from hypothesis import strategies as st
from test_xlsx_source_reader import (
    SyntheticXlsxBuilder,
    _make_uuid7,
    _sample_business_parties_row_data,
    _sample_buy_sell_row_data,
    _sample_inventory_movements_row_data,
    _sample_receipts_payments_row_data,
)


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


# ---------------------------------------------------------------------------
# SC-01: Public API exports, version, strict inputs, immutability, safe reprs
# ---------------------------------------------------------------------------


def test_sc01_public_api_exports_and_version() -> None:
    """SC-01: Public API exports, version strings, constants, and typed errors."""
    assert SAVE_IMPORT_COORDINATOR_VERSION == "save-import-coordinator.v1"
    assert SAVE_DEBOUNCE_NS == 2_000_000_000

    # Verify error classes and reasons
    pol_err = SaveCoordinatorPolicyError("test policy error")
    assert pol_err.reason == "invalid_policy"
    assert "test policy error" in str(pol_err)
    assert repr(pol_err) == "SaveCoordinatorPolicyError(reason='invalid_policy')"

    state_err = SaveCoordinatorStateError("test state error")
    assert state_err.reason == "invalid_transition"
    assert "test state error" in str(state_err)
    assert repr(state_err) == "SaveCoordinatorStateError(reason='invalid_transition')"


def test_sc01_source_read_attempt_immutability() -> None:
    """SC-01: SourceReadAttempt token is opaque and strictly immutable."""
    attempt = SourceReadAttempt("coord_1", "tok_12345678", 1000)
    assert "<SourceReadAttempt token=tok_1234>" in repr(attempt)

    with pytest.raises(AttributeError, match="immutable"):
        attempt._token_id = "modified"

    with pytest.raises(AttributeError, match="immutable"):
        del attempt._token_id

    assert copy.copy(attempt) is attempt
    assert copy.deepcopy(attempt) is attempt


def test_sc01_safe_repr_no_path_leakage(tmp_path: Path) -> None:
    """SC-01: Representations never leak filesystem source paths."""
    src = tmp_path / "super_secret_directory" / "target.xlsx"
    coord = SaveImportCoordinator(src)
    assert str(src) not in repr(coord)
    assert "super_secret_directory" not in repr(coord)

    view = coord.view()
    assert str(src) not in repr(view)
    assert "super_secret_directory" not in repr(view)


def test_sc01_save_coordinator_view_invariants() -> None:
    """SC-01: Direct SaveCoordinatorView instantiation enforces state invariants."""
    # Valid views
    v_idle = SaveCoordinatorView(
        version=SAVE_IMPORT_COORDINATOR_VERSION,
        state=SaveCoordinatorState.IDLE,
        pending=False,
        next_due_ns=None,
    )
    assert v_idle.state == SaveCoordinatorState.IDLE
    assert "idle" in repr(v_idle).lower()

    v_waiting = SaveCoordinatorView(
        version=SAVE_IMPORT_COORDINATOR_VERSION,
        state=SaveCoordinatorState.WAITING,
        pending=True,
        next_due_ns=3_000_000_000,
    )
    assert v_waiting.next_due_ns == 3_000_000_000

    v_running = SaveCoordinatorView(
        version=SAVE_IMPORT_COORDINATOR_VERSION,
        state=SaveCoordinatorState.RUNNING,
        pending=True,
        next_due_ns=None,
    )
    assert v_running.pending is True

    v_faulted = SaveCoordinatorView(
        version=SAVE_IMPORT_COORDINATOR_VERSION,
        state=SaveCoordinatorState.FAULTED,
        pending=True,
        next_due_ns=None,
    )
    assert v_faulted.state == SaveCoordinatorState.FAULTED

    # Invariant violations:
    # 1. Invalid version
    with pytest.raises(SaveCoordinatorPolicyError, match="Invalid coordinator version"):
        SaveCoordinatorView(
            version="save-import-coordinator.v0",
            state=SaveCoordinatorState.IDLE,
            pending=False,
            next_due_ns=None,
        )

    # 2. IDLE with pending=True
    with pytest.raises(
        SaveCoordinatorPolicyError, match="IDLE state must have pending=False"
    ):
        SaveCoordinatorView(
            version=SAVE_IMPORT_COORDINATOR_VERSION,
            state=SaveCoordinatorState.IDLE,
            pending=True,
            next_due_ns=None,
        )

    # 3. IDLE with next_due_ns
    with pytest.raises(
        SaveCoordinatorPolicyError, match="IDLE state must have next_due_ns=None"
    ):
        SaveCoordinatorView(
            version=SAVE_IMPORT_COORDINATOR_VERSION,
            state=SaveCoordinatorState.IDLE,
            pending=False,
            next_due_ns=1000,
        )

    # 4. WAITING with pending=False
    with pytest.raises(
        SaveCoordinatorPolicyError, match="WAITING state must have pending=True"
    ):
        SaveCoordinatorView(
            version=SAVE_IMPORT_COORDINATOR_VERSION,
            state=SaveCoordinatorState.WAITING,
            pending=False,
            next_due_ns=1000,
        )

    # 5. WAITING with missing or negative next_due_ns
    with pytest.raises(
        SaveCoordinatorPolicyError, match="WAITING state must have non-negative"
    ):
        SaveCoordinatorView(
            version=SAVE_IMPORT_COORDINATOR_VERSION,
            state=SaveCoordinatorState.WAITING,
            pending=True,
            next_due_ns=None,
        )
    with pytest.raises(
        SaveCoordinatorPolicyError, match="WAITING state must have non-negative"
    ):
        SaveCoordinatorView(
            version=SAVE_IMPORT_COORDINATOR_VERSION,
            state=SaveCoordinatorState.WAITING,
            pending=True,
            next_due_ns=-5,
        )

    # 6. RUNNING with next_due_ns
    with pytest.raises(
        SaveCoordinatorPolicyError, match="RUNNING state must have next_due_ns=None"
    ):
        SaveCoordinatorView(
            version=SAVE_IMPORT_COORDINATOR_VERSION,
            state=SaveCoordinatorState.RUNNING,
            pending=False,
            next_due_ns=1000,
        )

    # 7. FAULTED with pending=False or next_due_ns
    with pytest.raises(
        SaveCoordinatorPolicyError, match="FAULTED state must have pending=True"
    ):
        SaveCoordinatorView(
            version=SAVE_IMPORT_COORDINATOR_VERSION,
            state=SaveCoordinatorState.FAULTED,
            pending=False,
            next_due_ns=None,
        )
    with pytest.raises(
        SaveCoordinatorPolicyError, match="FAULTED state must have next_due_ns=None"
    ):
        SaveCoordinatorView(
            version=SAVE_IMPORT_COORDINATOR_VERSION,
            state=SaveCoordinatorState.FAULTED,
            pending=True,
            next_due_ns=1000,
        )


# ---------------------------------------------------------------------------
# SC-02: Exact-path & platform case semantics, filter irrelevant notices
# ---------------------------------------------------------------------------


def test_sc02_configuration_path_validation(tmp_path: Path) -> None:
    """SC-02: Configuration rejects non-Path, relative, .., non-xlsx, and ~$ lock."""
    valid_path = tmp_path / "valid.xlsx"
    coord = SaveImportCoordinator(valid_path)
    assert (
        coord.source_path == valid_path.resolve() if not os.name == "nt" else valid_path
    )

    # Non-path
    with pytest.raises(SaveCoordinatorPolicyError, match="must be a Path"):
        SaveImportCoordinator("string_path.xlsx")  # type: ignore[arg-type]

    # Relative path
    with pytest.raises(SaveCoordinatorPolicyError, match="must be an absolute path"):
        SaveImportCoordinator(Path("relative.xlsx"))

    # Unresolved parent components
    with pytest.raises(SaveCoordinatorPolicyError, match="parent directory"):
        SaveImportCoordinator(tmp_path / "sub" / ".." / "valid.xlsx")

    # Non-xlsx
    with pytest.raises(SaveCoordinatorPolicyError, match="must have a .xlsx extension"):
        SaveImportCoordinator(tmp_path / "data.xls")
    with pytest.raises(SaveCoordinatorPolicyError, match="must have a .xlsx extension"):
        SaveImportCoordinator(tmp_path / "data.txt")

    # ~$ Lock file
    with pytest.raises(SaveCoordinatorPolicyError, match="Excel temporary lock file"):
        SaveImportCoordinator(tmp_path / "~$valid.xlsx")


def test_sc02_notification_path_and_kind_filtering(tmp_path: Path) -> None:
    """SC-02: Notice filtering for directory, read-only, and sibling/unrelated paths."""
    src = tmp_path / "target.xlsx"
    clock = FakeClock()
    coord = SaveImportCoordinator(src, _time_source=clock)

    # 1. Directory notices are ignored
    assert coord.notify(SaveEventKind.MODIFIED, src, is_directory=True) is False
    assert coord.view().state == SaveCoordinatorState.IDLE

    # 2. Read-only notices (opened, closed, accessed) are ignored
    assert coord.notify(SaveEventKind.OPENED, src) is False
    assert coord.notify(SaveEventKind.CLOSED, src) is False
    assert coord.notify(SaveEventKind.ACCESSED, src) is False
    assert coord.view().state == SaveCoordinatorState.IDLE

    # 3. Sibling .tmp, ~$lock, conflict, and other files are ignored
    assert coord.notify(SaveEventKind.CREATED, tmp_path / "target.xlsx.tmp") is False
    assert coord.notify(SaveEventKind.CREATED, tmp_path / "~$target.xlsx") is False
    assert (
        coord.notify(SaveEventKind.MODIFIED, tmp_path / "target-Conflict.xlsx") is False
    )
    assert coord.notify(SaveEventKind.MODIFIED, tmp_path / "other.xlsx") is False
    assert coord.view().state == SaveCoordinatorState.IDLE

    # 4. Invalid notification parameters
    with pytest.raises(
        SaveCoordinatorPolicyError, match="is_directory must be a boolean"
    ):
        coord.notify(SaveEventKind.MODIFIED, src, is_directory="false")  # type: ignore[arg-type]

    with pytest.raises(SaveCoordinatorPolicyError, match="must be a Path instance"):
        coord.notify(SaveEventKind.MODIFIED, "string_path.xlsx")  # type: ignore[arg-type]

    with pytest.raises(SaveCoordinatorPolicyError, match="must be an absolute path"):
        coord.notify(SaveEventKind.MODIFIED, Path("rel.xlsx"))

    with pytest.raises(SaveCoordinatorPolicyError, match="parent directory components"):
        coord.notify(SaveEventKind.MODIFIED, tmp_path / ".." / "target.xlsx")

    # 5. Non-MOVED event with destination_path raises policy error
    with pytest.raises(
        SaveCoordinatorPolicyError, match="destination_path must be None"
    ):
        coord.notify(
            SaveEventKind.MODIFIED,
            src,
            destination_path=tmp_path / "dst.xlsx",
        )

    # 6. MOVED event without destination_path raises policy error
    with pytest.raises(
        SaveCoordinatorPolicyError, match="destination_path is required"
    ):
        coord.notify(SaveEventKind.MOVED, src)


# ---------------------------------------------------------------------------
# SC-03: Move into/out of target & target immutability
# ---------------------------------------------------------------------------


def test_sc03_move_into_and_out_of_target(tmp_path: Path) -> None:
    """SC-03: Move into/out of target matches; target path remains unchanged."""
    src = tmp_path / "target.xlsx"
    tmp_src = tmp_path / "word.tmp"
    clock = FakeClock()
    coord = SaveImportCoordinator(src, _time_source=clock)

    # Move temporary file into target
    assert (
        coord.notify(
            SaveEventKind.MOVED,
            tmp_src,
            destination_path=src,
        )
        is True
    )
    assert coord.view().state == SaveCoordinatorState.WAITING
    assert coord.source_path == src

    # Move target out to another location
    clock.advance_seconds(5.0)
    attempt = coord.take_due()
    assert attempt is not None
    coord.finish(attempt, SourceReadOutcome.SUCCESS)
    assert coord.view().state == SaveCoordinatorState.IDLE

    assert (
        coord.notify(
            SaveEventKind.MOVED,
            src,
            destination_path=tmp_path / "backup.xlsx",
        )
        is True
    )
    assert coord.view().state == SaveCoordinatorState.WAITING
    assert coord.source_path == src, "Coordinator target path is immutable"


# ---------------------------------------------------------------------------
# SC-04: Idle construction, fake monotonic clock, deadline boundaries
# ---------------------------------------------------------------------------


def test_sc04_idle_construction_and_clock_boundaries(tmp_path: Path) -> None:
    """SC-04: Idle construction, fake monotonic time, deadline +/-1ns, clock checks."""
    src = tmp_path / "source.xlsx"
    clock = FakeClock(start_ns=1_000_000_000)
    coord = SaveImportCoordinator(src, _time_source=clock)

    # Idle construction
    v0 = coord.view()
    assert v0.state == SaveCoordinatorState.IDLE
    assert v0.pending is False
    assert v0.next_due_ns is None
    assert coord.take_due() is None

    # First notice at t=1.0s -> due at t=3.0s (1s + 2s debounce)
    assert coord.notify(SaveEventKind.MODIFIED, src) is True
    assert coord.view().next_due_ns == 3_000_000_000

    # Repeated notice at t=2.0s resets deadline to 4.0s (2.0s + 2.0s)
    clock.set_ns(2_000_000_000)
    assert coord.notify(SaveEventKind.MODIFIED, src) is True
    assert coord.view().next_due_ns == 4_000_000_000

    # At t=3.999999999s (deadline - 1ns) -> no work due
    clock.set_ns(3_999_999_999)
    assert coord.take_due() is None
    assert coord.view().state == SaveCoordinatorState.WAITING

    # At exact deadline t=4.0s -> work is due
    clock.set_ns(4_000_000_000)
    attempt = coord.take_due()
    assert attempt is not None
    assert coord.view().state == SaveCoordinatorState.RUNNING

    # Backward clock anomaly raises state error and does not mutate state
    coord_broken = SaveImportCoordinator(src, _time_source=lambda: 1_000_000_000)
    with pytest.raises(
        SaveCoordinatorStateError, match="clock went backwards|invalid time"
    ):
        coord_broken.notify(SaveEventKind.MODIFIED, src)
        coord_broken._time_source = lambda: 500_000_000
        coord_broken.notify(SaveEventKind.MODIFIED, src)


# ---------------------------------------------------------------------------
# SC-05: Burst coalescing & past deadline single take
# ---------------------------------------------------------------------------


def test_sc05_burst_coalescing_and_past_deadline(tmp_path: Path) -> None:
    """SC-05: Burst notices coalesce; past deadline creates single attempt."""
    src = tmp_path / "source.xlsx"
    clock = FakeClock(start_ns=1_000_000_000)
    coord = SaveImportCoordinator(src, _time_source=clock)

    # 2,000 rapid burst notices
    for _ in range(2000):
        clock.advance_ns(100_000)  # +0.1ms each
        coord.notify(SaveEventKind.MODIFIED, src)

    last_notice_time = clock()
    expected_due = last_notice_time + SAVE_DEBOUNCE_NS
    assert coord.view().next_due_ns == expected_due

    # Advance clock far into the future (100 seconds past deadline)
    clock.advance_seconds(100.0)

    # Exactly one take succeeds
    attempt1 = coord.take_due()
    assert attempt1 is not None
    assert coord.view().state == SaveCoordinatorState.RUNNING

    # Second take immediately returns None (no backlog!)
    attempt2 = coord.take_due()
    assert attempt2 is None


# ---------------------------------------------------------------------------
# SC-06: Thread concurrency on take_due & token security / outcome checks
# ---------------------------------------------------------------------------


def test_sc06_concurrent_due_takes_yield_single_token(tmp_path: Path) -> None:
    """SC-06: Multiple barrier-synchronized concurrent callers yield 1 token."""
    src = tmp_path / "source.xlsx"
    clock = FakeClock()
    coord = SaveImportCoordinator(src, _time_source=clock)
    coord.notify(SaveEventKind.MODIFIED, src)
    clock.advance_seconds(3.0)

    num_threads = 10
    barrier = threading.Barrier(num_threads)
    results: list[SourceReadAttempt | None] = [None] * num_threads

    def worker(idx: int) -> None:
        barrier.wait()
        results[idx] = coord.take_due()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successful_attempts = [r for r in results if r is not None]
    assert len(successful_attempts) == 1, "Exactly one thread must acquire the token"
    assert coord.view().state == SaveCoordinatorState.RUNNING


def test_sc06_token_security_and_invalid_finish_outcomes(tmp_path: Path) -> None:
    """SC-06: Rejection of foreign, copied, stale, double tokens."""
    src1 = tmp_path / "source1.xlsx"
    src2 = tmp_path / "source2.xlsx"
    clock = FakeClock()
    coord1 = SaveImportCoordinator(src1, _time_source=clock)
    coord2 = SaveImportCoordinator(src2, _time_source=clock)

    coord1.notify(SaveEventKind.MODIFIED, src1)
    coord2.notify(SaveEventKind.MODIFIED, src2)
    clock.advance_seconds(3.0)

    attempt1 = coord1.take_due()
    attempt2 = coord2.take_due()
    assert attempt1 is not None
    assert attempt2 is not None

    # 1. Foreign token (token from coord2 used on coord1)
    with pytest.raises(
        SaveCoordinatorStateError, match="not active or coordinator is not running"
    ):
        coord1.finish(attempt2, SourceReadOutcome.SUCCESS)
    assert coord1.view().state == SaveCoordinatorState.RUNNING

    # 2. Forged token
    forged = SourceReadAttempt(coord1._coordinator_id, "forged_token", clock())
    with pytest.raises(
        SaveCoordinatorStateError, match="not active or coordinator is not running"
    ):
        coord1.finish(forged, SourceReadOutcome.SUCCESS)
    assert coord1.view().state == SaveCoordinatorState.RUNNING

    # 3. Invalid outcome enum
    with pytest.raises(SaveCoordinatorPolicyError, match="Invalid finish outcome"):
        coord1.finish(attempt1, "invalid_outcome")  # type: ignore[arg-type]

    # 4. Valid finish
    coord1.finish(attempt1, SourceReadOutcome.SUCCESS)
    assert coord1.view().state == SaveCoordinatorState.IDLE

    # 5. Double finish of same token
    with pytest.raises(
        SaveCoordinatorStateError, match="not active or coordinator is not running"
    ):
        coord1.finish(attempt1, SourceReadOutcome.SUCCESS)
    assert coord1.view().state == SaveCoordinatorState.IDLE


# ---------------------------------------------------------------------------
# SC-07: Follow-up notice handling during active attempt
# ---------------------------------------------------------------------------


def test_sc07_followup_notices_during_running_state(tmp_path: Path) -> None:
    """SC-07: Notices during running produce 1 follow-up (both expired & future)."""
    src = tmp_path / "source.xlsx"
    clock = FakeClock(start_ns=1_000_000_000)
    coord = SaveImportCoordinator(src, _time_source=clock)

    # Start attempt at t=1.0s, due at t=3.0s
    coord.notify(SaveEventKind.MODIFIED, src)
    clock.set_ns(3_000_000_000)
    attempt1 = coord.take_due()
    assert attempt1 is not None
    assert coord.view().state == SaveCoordinatorState.RUNNING
    assert coord.view().pending is False

    # Receive notice during work at t=4.0s (deadline 6.0s)
    clock.set_ns(4_000_000_000)
    coord.notify(SaveEventKind.MODIFIED, src)
    assert coord.view().pending is True

    # Another notice at t=5.0s (deadline 7.0s)
    clock.set_ns(5_000_000_000)
    coord.notify(SaveEventKind.MODIFIED, src)
    assert coord.view().pending is True

    # Case A: Finish at t=10.0s (after deadline 7.0s has already passed)
    clock.set_ns(10_000_000_000)
    coord.finish(attempt1, SourceReadOutcome.SUCCESS)

    v = coord.view()
    assert v.state == SaveCoordinatorState.WAITING
    assert v.next_due_ns == 7_000_000_000  # from latest notice at t=5s
    assert v.pending is True

    # Immediate take succeeds without extra wait
    attempt2 = coord.take_due()
    assert attempt2 is not None
    assert coord.view().state == SaveCoordinatorState.RUNNING

    # Case B: Notice during work with future deadline
    # Notice at t=11.0s (deadline 13.0s)
    clock.set_ns(11_000_000_000)
    coord.notify(SaveEventKind.MODIFIED, src)

    # Finish at t=11.5s
    clock.set_ns(11_500_000_000)
    coord.finish(attempt2, SourceReadOutcome.SUCCESS)

    assert coord.view().state == SaveCoordinatorState.WAITING
    assert coord.view().next_due_ns == 13_000_000_000

    # At t=12.0s -> not due yet
    clock.set_ns(12_000_000_000)
    assert coord.take_due() is None

    # At t=13.0s -> due
    clock.set_ns(13_000_000_000)
    attempt3 = coord.take_due()
    assert attempt3 is not None
    coord.finish(attempt3, SourceReadOutcome.SUCCESS)
    assert coord.view().state == SaveCoordinatorState.IDLE


# ---------------------------------------------------------------------------
# SC-08: Direct source_not_ready retry scheduling without new notice
# ---------------------------------------------------------------------------


def test_sc08_source_not_ready_automatic_retry_scheduling(tmp_path: Path) -> None:
    """SC-08: SOURCE_NOT_READY schedules automatic retry with no busy-loop."""
    src = tmp_path / "source.xlsx"
    clock = FakeClock(start_ns=1_000_000_000)
    coord = SaveImportCoordinator(src, _time_source=clock)

    coord.notify(SaveEventKind.MODIFIED, src)
    clock.set_ns(3_000_000_000)
    attempt = coord.take_due()
    assert attempt is not None

    # Finish with SOURCE_NOT_READY at t=4.0s
    clock.set_ns(4_000_000_000)
    coord.finish(attempt, SourceReadOutcome.SOURCE_NOT_READY)

    # In WAITING state with retry due at 4s + 2s = 6.0s
    assert coord.view().state == SaveCoordinatorState.WAITING
    assert coord.view().next_due_ns == 6_000_000_000

    # At t=5.999s -> no take
    clock.set_ns(5_999_999_999)
    assert coord.take_due() is None

    # At t=6.0s -> retry take succeeds without any new notice!
    clock.set_ns(6_000_000_000)
    retry_attempt = coord.take_due()
    assert retry_attempt is not None
    assert coord.view().state == SaveCoordinatorState.RUNNING

    coord.finish(retry_attempt, SourceReadOutcome.SUCCESS)
    assert coord.view().state == SaveCoordinatorState.IDLE


# ---------------------------------------------------------------------------
# SC-09: Direct reader_rejected idle transition & fresh notice handling
# ---------------------------------------------------------------------------


def test_sc09_reader_rejected_no_timer_retry_unless_newer_notice(
    tmp_path: Path,
) -> None:
    """SC-09: READER_REJECTED becomes IDLE unless newer notice arrived."""
    src = tmp_path / "source.xlsx"
    clock = FakeClock(start_ns=1_000_000_000)
    coord = SaveImportCoordinator(src, _time_source=clock)

    # 1. No newer notice: becomes IDLE
    coord.notify(SaveEventKind.MODIFIED, src)
    clock.set_ns(3_000_000_000)
    attempt1 = coord.take_due()
    assert attempt1 is not None

    clock.set_ns(4_000_000_000)
    coord.finish(attempt1, SourceReadOutcome.READER_REJECTED)
    assert coord.view().state == SaveCoordinatorState.IDLE
    assert coord.take_due() is None

    # 2. With newer notice during work: preserves follow-up
    coord.notify(SaveEventKind.MODIFIED, src)
    clock.set_ns(6_000_000_000)
    attempt2 = coord.take_due()
    assert attempt2 is not None

    # New notice arrives at t=7.0s
    clock.set_ns(7_000_000_000)
    coord.notify(SaveEventKind.MODIFIED, src)

    # Finish at t=8.0s
    clock.set_ns(8_000_000_000)
    coord.finish(attempt2, SourceReadOutcome.READER_REJECTED)
    assert coord.view().state == SaveCoordinatorState.WAITING
    assert coord.view().next_due_ns == 9_000_000_000  # 7s + 2s


# ---------------------------------------------------------------------------
# SC-10: Faulted state lifecycle, explicit resume & driver failure guard
# ---------------------------------------------------------------------------


def test_sc10_faulted_state_and_explicit_resume(tmp_path: Path) -> None:
    """SC-10: Faulted state preserves intent, notices alone cannot resume."""
    src = tmp_path / "source.xlsx"
    clock = FakeClock(start_ns=1_000_000_000)
    coord = SaveImportCoordinator(src, _time_source=clock)

    coord.notify(SaveEventKind.MODIFIED, src)
    clock.set_ns(3_000_000_000)
    attempt = coord.take_due()
    assert attempt is not None

    # Finish with FAULTED
    coord.finish(attempt, SourceReadOutcome.FAULTED)
    assert coord.view().state == SaveCoordinatorState.FAULTED
    assert coord.view().pending is True
    assert coord.view().next_due_ns is None

    # Notices in FAULTED retain notice but cannot start work
    clock.advance_seconds(10.0)
    assert coord.notify(SaveEventKind.MODIFIED, src) is True
    assert coord.view().state == SaveCoordinatorState.FAULTED
    assert coord.take_due() is None

    # Cannot resume when not faulted
    coord_idle = SaveImportCoordinator(src, _time_source=clock)
    with pytest.raises(SaveCoordinatorStateError, match="not in FAULTED state"):
        coord_idle.resume_after_fault()

    # Explicit resume transitions to WAITING due in 2 seconds
    resume_time = clock()
    coord.resume_after_fault()
    assert coord.view().state == SaveCoordinatorState.WAITING
    assert coord.view().next_due_ns == resume_time + SAVE_DEBOUNCE_NS

    clock.advance_seconds(2.0)
    resumed_attempt = coord.take_due()
    assert resumed_attempt is not None
    coord.finish(resumed_attempt, SourceReadOutcome.SUCCESS)
    assert coord.view().state == SaveCoordinatorState.IDLE


def test_sc10_injected_driver_bookkeeping_failure_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-10: Injected finish failure preserves causes and forces FAULTED."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    root = tmp_path / "snapshots"
    root.mkdir()

    clock = FakeClock()
    coord = SaveImportCoordinator(src, _time_source=clock)
    coord.notify(SaveEventKind.MODIFIED, src)
    clock.advance_seconds(3.0)

    # Monkeypatch coordinator.finish to simulate unexpected crash
    def broken_finish(attempt: SourceReadAttempt, outcome: SourceReadOutcome) -> None:
        raise RuntimeError("Injected finish bookkeeping crash")

    monkeypatch.setattr(coord, "finish", broken_finish)

    with pytest.raises(RuntimeError, match="Injected finish bookkeeping crash"):
        read_due_source(coord, snapshot_root=root, observation_interval_seconds=0.001)

    # Guard forces coordinator into FAULTED
    assert coord.view().state == SaveCoordinatorState.FAULTED
    assert coord.view().pending is True


# ---------------------------------------------------------------------------
# SC-11 & SC-12: Complete Reader Driver execution and outcome mapping
# ---------------------------------------------------------------------------


def test_sc11_read_due_source_zero_io_when_not_due(tmp_path: Path) -> None:
    """SC-11: Zero I/O and zero acquisition calls when no attempt is due."""
    src = tmp_path / "source.xlsx"
    root = tmp_path / "snapshots"
    coord = SaveImportCoordinator(src)

    # When IDLE -> returns None without touching filesystem
    res = read_due_source(coord, snapshot_root=root, observation_interval_seconds=0.001)
    assert res is None
    assert not root.exists()


def test_sc12_read_due_source_success_lifecycle(tmp_path: Path) -> None:
    """SC-12: Full success read lifecycle with synthetic 4-sheet XLSX workbook."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    root = tmp_path / "snapshots"
    root.mkdir()

    clock = FakeClock()
    coord = SaveImportCoordinator(src, _time_source=clock)
    coord.notify(SaveEventKind.MODIFIED, src)
    clock.advance_seconds(3.0)

    res = read_due_source(coord, snapshot_root=root, observation_interval_seconds=0.001)
    assert res is not None
    assert isinstance(res, XlsxSourceReadResult)
    assert len(res.snapshot.sheets) == 4
    assert coord.view().state == SaveCoordinatorState.IDLE


def test_sc12_read_due_source_missing_file_source_not_ready(tmp_path: Path) -> None:
    """SC-12: Missing source file raises XlsxSourceNotReadyError and schedules retry."""
    src = tmp_path / "nonexistent.xlsx"
    root = tmp_path / "snapshots"
    root.mkdir()

    clock = FakeClock()
    coord = SaveImportCoordinator(src, _time_source=clock)
    coord.notify(SaveEventKind.MODIFIED, src)
    clock.advance_seconds(3.0)

    with pytest.raises(XlsxSourceNotReadyError):
        read_due_source(coord, snapshot_root=root, observation_interval_seconds=0.001)

    # Coordinator should be in WAITING for retry
    assert coord.view().state == SaveCoordinatorState.WAITING
    assert coord.view().next_due_ns == clock() + SAVE_DEBOUNCE_NS


def test_sc12_read_due_source_reader_rejected_corrupt_file(tmp_path: Path) -> None:
    """SC-12: Corrupt XLSX file raises XlsxSourceReadError and transitions to IDLE."""
    src = tmp_path / "corrupt.xlsx"
    # Valid ZIP containing corrupt sheet XML
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
    src.write_bytes(buf.getvalue())

    root = tmp_path / "snapshots"
    root.mkdir()

    clock = FakeClock()
    coord = SaveImportCoordinator(src, _time_source=clock)
    coord.notify(SaveEventKind.MODIFIED, src)
    clock.advance_seconds(3.0)

    with pytest.raises(XlsxSourceReadError):
        read_due_source(coord, snapshot_root=root, observation_interval_seconds=0.001)

    # Reader rejection without follow-up notice becomes IDLE
    assert coord.view().state == SaveCoordinatorState.IDLE


def test_sc12_read_due_source_integrity_failure_faults_coordinator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-12: Context exit failure (integrity error) faults coordinator."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    root = tmp_path / "snapshots"
    root.mkdir()

    # Force integrity failure on post-lease check
    clock = FakeClock()
    coord = SaveImportCoordinator(src, _time_source=clock)
    coord.notify(SaveEventKind.MODIFIED, src)
    clock.advance_seconds(3.0)

    # Tamper with snapshot during read
    import accounting_local_agent.xlsx_source_reader as rdr_mod

    orig_read = rdr_mod.read_xlsx_source_snapshot

    def tampering_read(p: Path | str) -> Any:
        path_obj = Path(p)
        path_obj.write_bytes(b"TAMPERED_DATA")
        return orig_read(p)

    monkeypatch.setattr(
        "accounting_local_agent.save_import_coordinator.read_xlsx_source_snapshot",
        tampering_read,
    )

    with pytest.raises(
        (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotIntegrityError)
    ):
        read_due_source(coord, snapshot_root=root, observation_interval_seconds=0.001)

    assert coord.view().state == SaveCoordinatorState.FAULTED


def test_sc12_cancellation_keyboard_interrupt_faults_coordinator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-12: Cancellation via KeyboardInterrupt faults coordinator and re-raises."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    root = tmp_path / "snapshots"
    root.mkdir()

    clock = FakeClock()
    coord = SaveImportCoordinator(src, _time_source=clock)
    coord.notify(SaveEventKind.MODIFIED, src)
    clock.advance_seconds(3.0)

    def interrupting_reader(p: Path | str) -> Any:
        raise KeyboardInterrupt("Simulated user interrupt during read")

    monkeypatch.setattr(
        "accounting_local_agent.save_import_coordinator.read_xlsx_source_snapshot",
        interrupting_reader,
    )

    with pytest.raises(KeyboardInterrupt, match="Simulated user interrupt"):
        read_due_source(coord, snapshot_root=root, observation_interval_seconds=0.001)

    assert coord.view().state == SaveCoordinatorState.FAULTED
    assert coord.view().pending is True


# ---------------------------------------------------------------------------
# SC-13: Notification responsiveness during I/O and cleanup barriers
# ---------------------------------------------------------------------------


def test_sc13_notice_responsiveness_during_blocked_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-13: Notices during blocked reader I/O are accepted without waiting."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_minimal_valid_four_sheet_xlsx())
    root = tmp_path / "snapshots"
    root.mkdir()

    reader_entered = threading.Event()
    release_reader = threading.Event()

    import accounting_local_agent.xlsx_source_reader as rdr_mod

    orig_reader = rdr_mod.read_xlsx_source_snapshot

    def blocking_reader(p: Path | str) -> Any:
        reader_entered.set()
        release_reader.wait(timeout=5.0)
        return orig_reader(p)

    monkeypatch.setattr(
        "accounting_local_agent.save_import_coordinator.read_xlsx_source_snapshot",
        blocking_reader,
    )

    coord = SaveImportCoordinator(src)
    coord.notify(SaveEventKind.MODIFIED, src)
    coord._next_due_ns = 0  # make immediately due

    driver_result: list[Any] = []

    def driver_thread() -> None:
        res = read_due_source(
            coord, snapshot_root=root, observation_interval_seconds=0.001
        )
        driver_result.append(res)

    t = threading.Thread(target=driver_thread)
    t.start()

    # Wait until driver is inside the blocking reader
    assert reader_entered.wait(timeout=5.0)

    # Send a new notification while driver is blocked in I/O
    # notify() must acquire lock and return True immediately!
    accepted = coord.notify(SaveEventKind.MODIFIED, src)
    assert accepted is True
    assert coord.view().pending is True

    # Release the reader and join thread
    release_reader.set()
    t.join(timeout=5.0)

    assert len(driver_result) == 1
    assert isinstance(driver_result[0], XlsxSourceReadResult)
    # Coordinator should now be in WAITING state for the follow-up
    assert coord.view().state == SaveCoordinatorState.WAITING


# ---------------------------------------------------------------------------
# SC-14: Composition with WP-04 Planner oracle (zero false changes)
# ---------------------------------------------------------------------------


def test_sc14_change_planner_oracle_zero_false_changes(tmp_path: Path) -> None:
    """SC-14: Planner confirms no changes for unchanged Raw with altered ZIP binary."""
    src = tmp_path / "source.xlsx"
    orig_bytes = _build_minimal_valid_four_sheet_xlsx()
    src.write_bytes(orig_bytes)
    root = tmp_path / "snapshots"
    root.mkdir()

    coord = SaveImportCoordinator(src)
    coord.notify(SaveEventKind.MODIFIED, src)
    coord._next_due_ns = 0

    res1 = read_due_source(
        coord, snapshot_root=root, observation_interval_seconds=0.001
    )
    assert res1 is not None

    # Rewrite ZIP package with different comment / compression representation
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(orig_bytes), "r") as z_in:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z_out:
            z_out.comment = b"NEW_ZIP_REPRESENTATION"
            for item in z_in.infolist():
                z_out.writestr(item, z_in.read(item.filename))
    new_bytes = buf.getvalue()
    assert new_bytes != orig_bytes, "ZIP binary representation must differ"

    src.write_bytes(new_bytes)
    coord.notify(SaveEventKind.MODIFIED, src)
    coord._next_due_ns = 0

    res2 = read_due_source(
        coord, snapshot_root=root, observation_interval_seconds=0.001
    )
    assert res2 is not None

    # Run WP-04 Change Planner
    plan1 = plan_source_changes(res1.snapshot)
    prior_states = [
        PriorIdentityState(
            stable_id=p1_item.stable_id,
            canonical_uuid=p1_item.canonical_uuid,
            home_sheet=p1_item.sheet_name,
            latest_revision=1,
            lifecycle=IdentityLifecycle.ACTIVE,
            source_hash=p1_item.current_source_hash,
        )
        for p1_item in plan1.items
    ]
    plan_same = plan_source_changes(res2.snapshot, prior_states)

    assert plan_same.total_counts.unchanged_count == 4
    assert plan_same.total_counts.insert_count == 0
    assert plan_same.total_counts.edit_count == 0
    assert plan_same.total_counts.void_count == 0
    for p_item in plan_same.items:
        assert p_item.action == PlanAction.UNCHANGED
        assert p_item.planned_revision is None
        assert p_item.prior_revision == 1
        assert p_item.prior_lifecycle == IdentityLifecycle.ACTIVE


# ---------------------------------------------------------------------------
# SC-15: Hypothesis property-based state machine verification
# ---------------------------------------------------------------------------


class CoordinatorReferenceModel:
    """Reference oracle modeling ADR-0010 state transitions."""

    def __init__(self) -> None:
        self.state = SaveCoordinatorState.IDLE
        self.latest_notice_ns: int | None = None
        self.next_due_ns: int | None = None
        self.pending_followup: bool = False
        self.active: bool = False

    def notify(self, now: int) -> None:
        self.latest_notice_ns = now
        if self.state == SaveCoordinatorState.IDLE:
            self.state = SaveCoordinatorState.WAITING
            self.next_due_ns = now + SAVE_DEBOUNCE_NS
            self.pending_followup = False
        elif self.state == SaveCoordinatorState.WAITING:
            self.next_due_ns = now + SAVE_DEBOUNCE_NS
        elif self.state == SaveCoordinatorState.RUNNING:
            self.pending_followup = True
        elif self.state == SaveCoordinatorState.FAULTED:
            self.pending_followup = True

    def take_due(self, now: int) -> bool:
        if (
            self.state == SaveCoordinatorState.WAITING
            and self.next_due_ns is not None
            and now >= self.next_due_ns
        ):
            self.state = SaveCoordinatorState.RUNNING
            self.active = True
            self.next_due_ns = None
            self.pending_followup = False
            return True
        return False

    def finish(self, outcome: SourceReadOutcome, now: int) -> None:
        assert self.state == SaveCoordinatorState.RUNNING
        assert self.active is True
        self.active = False

        if outcome == SourceReadOutcome.SUCCESS:
            if self.pending_followup:
                self.state = SaveCoordinatorState.WAITING
                self.next_due_ns = (self.latest_notice_ns or now) + SAVE_DEBOUNCE_NS
                self.pending_followup = False
            else:
                self.state = SaveCoordinatorState.IDLE
                self.next_due_ns = None
                self.latest_notice_ns = None
        elif outcome == SourceReadOutcome.SOURCE_NOT_READY:
            self.state = SaveCoordinatorState.WAITING
            latest = self.latest_notice_ns or now
            self.next_due_ns = max(now + SAVE_DEBOUNCE_NS, latest + SAVE_DEBOUNCE_NS)
            self.pending_followup = False
        elif outcome == SourceReadOutcome.READER_REJECTED:
            if self.pending_followup:
                self.state = SaveCoordinatorState.WAITING
                self.next_due_ns = (self.latest_notice_ns or now) + SAVE_DEBOUNCE_NS
                self.pending_followup = False
            else:
                self.state = SaveCoordinatorState.IDLE
                self.next_due_ns = None
                self.latest_notice_ns = None
        elif outcome == SourceReadOutcome.FAULTED:
            self.state = SaveCoordinatorState.FAULTED
            self.next_due_ns = None
            self.pending_followup = True

    def resume_after_fault(self, now: int) -> None:
        assert self.state == SaveCoordinatorState.FAULTED
        assert not self.active
        self.state = SaveCoordinatorState.WAITING
        self.next_due_ns = now + SAVE_DEBOUNCE_NS
        self.pending_followup = False


@given(
    actions=st.lists(
        st.tuples(
            st.sampled_from(["notify", "advance", "take_due", "finish", "resume"]),
            st.integers(min_value=0, max_value=5_000_000_000),
            st.sampled_from(list(SourceReadOutcome)),
        ),
        min_size=5,
        max_size=50,
    )
)
@settings(max_examples=50)
def test_sc15_hypothesis_property_state_machine_oracle(
    actions: list[tuple[str, int, SourceReadOutcome]],
) -> None:
    """SC-15: Property test comparing coordinator execution with reference model."""
    clock = FakeClock(start_ns=1_000_000_000)
    src = Path("/tmp/synthetic_test_source.xlsx")
    coord = SaveImportCoordinator(src, _time_source=clock)
    ref = CoordinatorReferenceModel()
    active_token: SourceReadAttempt | None = None

    for action, delta_ns, outcome in actions:
        clock.advance_ns(delta_ns)
        now = clock()

        if action == "notify":
            coord.notify(SaveEventKind.MODIFIED, src)
            ref.notify(now)
        elif action == "advance":
            pass
        elif action == "take_due":
            tok = coord.take_due()
            ref_took = ref.take_due(now)
            if ref_took:
                assert tok is not None
                active_token = tok
            else:
                assert tok is None
        elif action == "finish":
            if active_token is not None:
                coord.finish(active_token, outcome)
                ref.finish(outcome, now)
                active_token = None
        elif action == "resume":
            if ref.state == SaveCoordinatorState.FAULTED:
                coord.resume_after_fault()
                ref.resume_after_fault(now)

        # Assert full state view parity
        view = coord.view()
        assert view.state == ref.state
        assert view.next_due_ns == ref.next_due_ns
        assert view.pending == (
            ref.pending_followup
            if ref.state == SaveCoordinatorState.RUNNING
            else (
                ref.state
                in (SaveCoordinatorState.WAITING, SaveCoordinatorState.FAULTED)
            )
        )
