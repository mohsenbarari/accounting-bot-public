"""Tests for WP-06 Stable XLSX Snapshot Acquisition and Cleanup (SA-01 to SA-14)."""

from __future__ import annotations

import ctypes
import dataclasses
import hashlib
import io
import os
import stat
import subprocess
import sys
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
)
from accounting_contracts.source_change_plan import (
    PlanAction,
    SourceRowInput,
    SourceSheetInput,
    build_prior_identity_registry,
    build_source_workbook_snapshot,
    plan_source_changes,
)
from accounting_local_agent import (
    XLSX_SNAPSHOT_ACQUISITION_VERSION,
    StableXlsxSnapshot,
    XlsxSnapshotAcquisitionError,
    XlsxSnapshotAcquisitionReason,
    XlsxSnapshotCleanupError,
    XlsxSnapshotIntegrityError,
    XlsxSnapshotStorageError,
    XlsxSourceNotReadyError,
    XlsxSourcePolicyError,
    open_stable_xlsx_snapshot,
    read_xlsx_source_snapshot,
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


def _build_valid_test_xlsx() -> bytes:
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


def _flatten_exceptions(exc: BaseException) -> list[BaseException]:
    """Helper flattening nested ExceptionGroup instances into a flat list."""
    if isinstance(exc, (ExceptionGroup, BaseExceptionGroup)):
        res: list[BaseException] = []
        for sub in exc.exceptions:
            res.extend(_flatten_exceptions(sub))
        return res
    return [exc]


# ============================================================================
# SA-01: Public version/API exports, immutable metadata, and invariants
# ============================================================================


def test_sa01_public_api_exports_and_version() -> None:
    """SA-01: Public version and exports match contract exactly."""
    assert XLSX_SNAPSHOT_ACQUISITION_VERSION == "xlsx-snapshot-acquisition.v1"

    import accounting_local_agent as pkg

    exported = set(pkg.__all__)
    expected = {
        "XLSX_SNAPSHOT_ACQUISITION_VERSION",
        "DEFAULT_COPY_CHUNK_SIZE",
        "StableXlsxSnapshot",
        "XlsxSnapshotAcquisitionError",
        "XlsxSnapshotAcquisitionReason",
        "XlsxSnapshotCleanupError",
        "XlsxSnapshotIntegrityError",
        "XlsxSnapshotStorageError",
        "XlsxSourceNotReadyError",
        "XlsxSourcePolicyError",
        "open_stable_xlsx_snapshot",
    }
    assert expected.issubset(exported)


def test_sa01_stable_xlsx_snapshot_invariants(tmp_path: Path) -> None:
    """SA-01: StableXlsxSnapshot validates all fields and is immutable."""
    valid_path = (tmp_path / "valid.xlsx").resolve()
    valid_hash = "a" * 64

    snap = StableXlsxSnapshot(
        version=XLSX_SNAPSHOT_ACQUISITION_VERSION,
        snapshot_path=valid_path,
        file_sha256=valid_hash,
        byte_count=1024,
        source_mtime_ns=1700000000000,
    )
    assert snap.version == XLSX_SNAPSHOT_ACQUISITION_VERSION
    assert snap.snapshot_path == valid_path
    assert snap.file_sha256 == valid_hash
    assert snap.byte_count == 1024
    assert snap.source_mtime_ns == 1700000000000

    # Immutability check
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.byte_count = 2048  # type: ignore[misc]

    # Rejection of invalid version
    with pytest.raises(ValueError, match="Invalid acquisition version"):
        StableXlsxSnapshot(
            version="wrong.v1",
            snapshot_path=valid_path,
            file_sha256=valid_hash,
            byte_count=1024,
            source_mtime_ns=100,
        )

    # Rejection of non-Path snapshot_path
    with pytest.raises(TypeError, match="must be a Path instance"):
        StableXlsxSnapshot(
            version=XLSX_SNAPSHOT_ACQUISITION_VERSION,
            snapshot_path="/tmp/str.xlsx",  # type: ignore[arg-type]
            file_sha256=valid_hash,
            byte_count=1024,
            source_mtime_ns=100,
        )

    # Rejection of relative Path
    with pytest.raises(ValueError, match="must be an absolute path"):
        StableXlsxSnapshot(
            version=XLSX_SNAPSHOT_ACQUISITION_VERSION,
            snapshot_path=Path("rel.xlsx"),
            file_sha256=valid_hash,
            byte_count=1024,
            source_mtime_ns=100,
        )

    # Rejection of non-.xlsx extension
    with pytest.raises(ValueError, match="must have .xlsx extension"):
        StableXlsxSnapshot(
            version=XLSX_SNAPSHOT_ACQUISITION_VERSION,
            snapshot_path=(tmp_path / "file.csv").resolve(),
            file_sha256=valid_hash,
            byte_count=1024,
            source_mtime_ns=100,
        )

    # Rejection of uppercase or non-64 hex sha256
    with pytest.raises(ValueError, match="lowercase 64-hex"):
        StableXlsxSnapshot(
            version=XLSX_SNAPSHOT_ACQUISITION_VERSION,
            snapshot_path=valid_path,
            file_sha256="A" * 64,
            byte_count=1024,
            source_mtime_ns=100,
        )

    with pytest.raises(ValueError, match="lowercase 64-hex"):
        StableXlsxSnapshot(
            version=XLSX_SNAPSHOT_ACQUISITION_VERSION,
            snapshot_path=valid_path,
            file_sha256="12345",
            byte_count=1024,
            source_mtime_ns=100,
        )

    # Rejection of negative / bool byte_count
    with pytest.raises(ValueError, match="byte_count must be a nonnegative integer"):
        StableXlsxSnapshot(
            version=XLSX_SNAPSHOT_ACQUISITION_VERSION,
            snapshot_path=valid_path,
            file_sha256=valid_hash,
            byte_count=-1,
            source_mtime_ns=100,
        )

    with pytest.raises(ValueError, match="byte_count must be a nonnegative integer"):
        StableXlsxSnapshot(
            version=XLSX_SNAPSHOT_ACQUISITION_VERSION,
            snapshot_path=valid_path,
            file_sha256=valid_hash,
            byte_count=True,
            source_mtime_ns=100,
        )

    # Rejection of negative / bool source_mtime_ns
    with pytest.raises(
        ValueError, match="source_mtime_ns must be a nonnegative integer"
    ):
        StableXlsxSnapshot(
            version=XLSX_SNAPSHOT_ACQUISITION_VERSION,
            snapshot_path=valid_path,
            file_sha256=valid_hash,
            byte_count=100,
            source_mtime_ns=-5,
        )

    with pytest.raises(
        ValueError, match="source_mtime_ns must be a nonnegative integer"
    ):
        StableXlsxSnapshot(
            version=XLSX_SNAPSHOT_ACQUISITION_VERSION,
            snapshot_path=valid_path,
            file_sha256=valid_hash,
            byte_count=100,
            source_mtime_ns=False,
        )


def test_sa01_error_taxonomy_reasons_and_retryability() -> None:
    """SA-01: Error classes enforce reason strings and retryability rules."""
    e_ready = XlsxSourceNotReadyError("File missing")
    assert e_ready.reason == XlsxSnapshotAcquisitionReason.SOURCE_NOT_READY
    assert e_ready.retryable is True
    assert "[source_not_ready] File missing" in str(e_ready)

    e_pol = XlsxSourcePolicyError("Bad extension")
    assert e_pol.reason == XlsxSnapshotAcquisitionReason.SOURCE_POLICY_VIOLATION
    assert e_pol.retryable is False

    e_stor = XlsxSnapshotStorageError("Root full")
    assert e_stor.reason == XlsxSnapshotAcquisitionReason.SNAPSHOT_STORAGE_FAILURE
    assert e_stor.retryable is False

    e_int = XlsxSnapshotIntegrityError("Hash mismatch")
    assert e_int.reason == XlsxSnapshotAcquisitionReason.SNAPSHOT_INTEGRITY_FAILURE
    assert e_int.retryable is False

    e_cln = XlsxSnapshotCleanupError("File busy")
    assert e_cln.reason == XlsxSnapshotAcquisitionReason.SNAPSHOT_CLEANUP_FAILURE
    assert e_cln.retryable is False

    # Inconsistent retryable rejection in direct construction
    with pytest.raises(ValueError, match="Inconsistent retryable"):
        XlsxSnapshotAcquisitionError(
            reason=XlsxSnapshotAcquisitionReason.SOURCE_NOT_READY,
            retryable=False,
        )

    with pytest.raises(ValueError, match="Inconsistent retryable"):
        XlsxSnapshotAcquisitionError(
            reason=XlsxSnapshotAcquisitionReason.SOURCE_POLICY_VIOLATION,
            retryable=True,
        )

    with pytest.raises(ValueError, match="Invalid acquisition reason"):
        XlsxSnapshotAcquisitionError(reason="invalid_reason_string")


def test_sa01_no_path_or_secret_leakage_in_messages_and_repr(
    tmp_path: Path,
) -> None:
    """SA-01 & R2-06: Errors never leak secret file names or paths."""
    sentinel_dir = tmp_path / "TOP_SECRET_FINANCIAL_DIR_9988"
    sentinel_dir.mkdir()
    sentinel_src = sentinel_dir / "CONFIDENTIAL_PAYROLL_2026.xlsx"
    sentinel_filename = "CONFIDENTIAL_PAYROLL_2026.xlsx"
    sentinel_dirname = "TOP_SECRET_FINANCIAL_DIR_9988"

    root = tmp_path / "SECRET_SNAPSHOT_ROOT_7766"
    root.mkdir()

    # 1. Missing source (XlsxSourceNotReadyError)
    try:
        with open_stable_xlsx_snapshot(sentinel_src, root, 0.001):
            pass
    except XlsxSourceNotReadyError as exc:
        msg = str(exc)
        rep = repr(exc)
        assert sentinel_filename not in msg and sentinel_dirname not in msg
        assert sentinel_filename not in rep and sentinel_dirname not in rep

    # 2. Non-.xlsx policy error (XlsxSourcePolicyError)
    bad_pol_src = sentinel_dir / "CONFIDENTIAL_PAYROLL_2026.csv"
    try:
        with open_stable_xlsx_snapshot(bad_pol_src, root, 0.001):
            pass
    except XlsxSourcePolicyError as exc:
        msg = str(exc)
        rep = repr(exc)
        assert sentinel_filename not in msg and sentinel_dirname not in msg
        assert sentinel_filename not in rep and sentinel_dirname not in rep

    # 3. Missing snapshot root (XlsxSnapshotStorageError)
    missing_root = tmp_path / "SECRET_MISSING_ROOT_5544"
    sentinel_src.write_bytes(_build_valid_test_xlsx())
    try:
        with open_stable_xlsx_snapshot(sentinel_src, missing_root, 0.001):
            pass
    except XlsxSnapshotStorageError as exc:
        msg = str(exc)
        rep = repr(exc)
        assert "SECRET_MISSING_ROOT_5544" not in msg
        assert "SECRET_MISSING_ROOT_5544" not in rep


# ============================================================================
# SA-02: Two ordered observations and interval policy validation
# ============================================================================


def test_sa02_two_observations_and_sleeper(tmp_path: Path) -> None:
    """SA-02: Two ordered observations occur separated by exact interval."""
    src = tmp_path / "test.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    sleep_calls: list[float] = []

    def mock_sleeper(dur: float) -> None:
        sleep_calls.append(dur)

    with open_stable_xlsx_snapshot(src, root, 0.05, _sleeper=mock_sleeper) as snap:
        assert snap.byte_count == src.stat().st_size

    assert sleep_calls == [0.05]


def test_sa02_invalid_arguments_and_policy_rejections(tmp_path: Path) -> None:
    """SA-02 & R2-06: Rejects str paths, non-xlsx extensions, bad roots."""
    root = tmp_path / "root"
    root.mkdir()
    valid_src = tmp_path / "source.xlsx"
    valid_src.write_bytes(_build_valid_test_xlsx())

    # String source_path rejection
    str_src: Any = str(valid_src)
    with pytest.raises(XlsxSourcePolicyError, match="must be a Path instance"):
        with open_stable_xlsx_snapshot(str_src, root, 0.01):
            pass

    # String snapshot_root rejection
    str_root: Any = str(root)
    with pytest.raises(XlsxSnapshotStorageError, match="must be a Path instance"):
        with open_stable_xlsx_snapshot(valid_src, str_root, 0.01):
            pass

    # Invalid observation intervals
    for bad_interval in [
        0.0,
        -1.0,
        float("nan"),
        float("inf"),
        float("-inf"),
        True,
        False,
        "0.5",
    ]:
        with pytest.raises(XlsxSourcePolicyError):
            with open_stable_xlsx_snapshot(
                valid_src,
                root,
                bad_interval,  # type: ignore[arg-type]
            ):
                pass

    # Invalid source path extension (message does NOT leak filename)
    for bad_ext in ["bad_file.xls", "bad_file.csv", "bad_file.txt"]:
        bad_p = tmp_path / bad_ext
        bad_p.write_bytes(b"data")
        with pytest.raises(XlsxSourcePolicyError) as exc_info:
            with open_stable_xlsx_snapshot(bad_p, root, 0.01):
                pass
        assert bad_ext not in str(exc_info.value)
        assert "Source file must have .xlsx extension" in str(exc_info.value)

    # Directory source path
    dir_src = tmp_path / "directory.xlsx"
    dir_src.mkdir()
    with pytest.raises(XlsxSourcePolicyError, match="points to a directory"):
        with open_stable_xlsx_snapshot(dir_src, root, 0.01):
            pass

    # Non-existent snapshot_root
    non_root = tmp_path / "missing_root"
    with pytest.raises(XlsxSnapshotStorageError, match="does not exist"):
        with open_stable_xlsx_snapshot(valid_src, non_root, 0.01):
            pass

    # Non-directory snapshot_root (file instead of dir)
    file_root = tmp_path / "file_root"
    file_root.write_bytes(b"not a dir")
    with pytest.raises(XlsxSnapshotStorageError, match="not a directory"):
        with open_stable_xlsx_snapshot(valid_src, file_root, 0.01):
            pass


def test_sa02_initial_source_symlink_rejection(tmp_path: Path) -> None:
    """SA-02 & R2-02: Initial symlink source rejected with policy error."""
    if not hasattr(os, "symlink"):
        pytest.skip("Symlinks not supported on this platform")

    root = tmp_path / "root"
    root.mkdir()
    target_src = tmp_path / "target.xlsx"
    target_src.write_bytes(_build_valid_test_xlsx())

    symlink_src = tmp_path / "symlink_source.xlsx"
    symlink_src.symlink_to(target_src)

    with pytest.raises(XlsxSourcePolicyError, match="cannot be a symlink"):
        with open_stable_xlsx_snapshot(symlink_src, root, 0.01):
            pass


def test_sa02_non_regular_file_rejection_policy(tmp_path: Path) -> None:
    """SA-02 & R2-01: Non-regular source (FIFO/socket) rejected with policy error."""
    root = tmp_path / "root"
    root.mkdir()
    fifo_src = tmp_path / "fifo_source.xlsx"

    if hasattr(os, "mkfifo"):
        try:
            os.mkfifo(fifo_src)
            with pytest.raises(XlsxSourcePolicyError, match="must be a regular file"):
                with open_stable_xlsx_snapshot(fifo_src, root, 0.01):
                    pass
        finally:
            if fifo_src.exists():
                fifo_src.unlink()


# ============================================================================
# SA-03: Source bytes, hash, size, mtime, permissions remain unchanged
# ============================================================================


def test_sa03_source_immutability_on_success_and_failures(
    tmp_path: Path,
) -> None:
    """SA-03 & R2-07: Source file is strictly read-only across all outcomes."""
    src = tmp_path / "source.xlsx"
    raw_data = _build_valid_test_xlsx()
    src.write_bytes(raw_data)
    root = tmp_path / "root"
    root.mkdir()

    initial_stat = src.stat()
    initial_sha = hashlib.sha256(raw_data).hexdigest()

    def assert_source_unchanged() -> None:
        current_stat = src.stat()
        assert current_stat.st_size == initial_stat.st_size
        assert current_stat.st_mtime_ns == initial_stat.st_mtime_ns
        assert current_stat.st_mode == initial_stat.st_mode
        assert hashlib.sha256(src.read_bytes()).hexdigest() == initial_sha

    # 1. Success case
    with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None) as snap:
        assert snap.file_sha256 == initial_sha
    assert_source_unchanged()

    # 2. Consumer exception
    with pytest.raises(RuntimeError, match="consumer crash"):
        with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None):
            raise RuntimeError("consumer crash")
    assert_source_unchanged()

    # 3. Injected failure during verification
    def reverify_fail(stage: str, s: Path, t: Path | None) -> None:
        if stage == "before_candidate_reverify" and t is not None:
            t.write_bytes(b"corrupted candidate")

    with pytest.raises(
        (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotIntegrityError)
    ):
        with open_stable_xlsx_snapshot(
            src, root, 0.001, _sleeper=lambda _: None, _fault_hook=reverify_fail
        ):
            pass
    assert_source_unchanged()


# ============================================================================
# SA-04: Missing, locked, and changing sources fail with retryable error
# ============================================================================


def test_sa04_missing_and_disappearing_source_handling(tmp_path: Path) -> None:
    """SA-04: Missing or disappearing file raises retryable XlsxSourceNotReadyError."""
    root = tmp_path / "root"
    root.mkdir()
    missing_src = tmp_path / "missing.xlsx"

    with pytest.raises(XlsxSourceNotReadyError) as exc_info:
        with open_stable_xlsx_snapshot(missing_src, root, 0.01):
            pass
    assert exc_info.value.retryable is True

    # File modified during observation window
    src = tmp_path / "changing.xlsx"
    src.write_bytes(_build_valid_test_xlsx())

    def modifying_sleeper(_: float) -> None:
        src.write_bytes(b"new content during sleep")

    with pytest.raises(XlsxSourceNotReadyError) as exc_info2:
        with open_stable_xlsx_snapshot(src, root, 0.01, _sleeper=modifying_sleeper):
            pass
    assert exc_info2.value.retryable is True


def test_sa04_inaccessible_file_permission_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SA-04 & R2-04: Inaccessible source raises retryable error."""
    root = tmp_path / "root"
    root.mkdir()
    src = tmp_path / "locked.xlsx"
    src.write_bytes(_build_valid_test_xlsx())

    orig_lstat = Path.lstat

    def mock_lstat(self: Path) -> os.stat_result:
        if self == src:
            raise PermissionError("Access denied (simulated lock)")
        return orig_lstat(self)

    monkeypatch.setattr(Path, "lstat", mock_lstat)
    with pytest.raises(XlsxSourceNotReadyError) as exc_info:
        with open_stable_xlsx_snapshot(src, root, 0.001):
            pass
    assert exc_info.value.retryable is True


# ============================================================================
# SA-05: In-place mutation and atomic replacement at every race point
# ============================================================================


@pytest.mark.parametrize(
    "fault_stage",
    [
        "during_observation",
        "before_copy_open",
        "during_copy_chunk",
        "before_source_reverify",
        "during_source_reverify",
    ],
)
def test_sa05_race_mutation_fault_injections(tmp_path: Path, fault_stage: str) -> None:
    """SA-05: Source in-place mutation at any stage aborts acquisition."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    mutated_bytes = _build_valid_test_xlsx() + b"extra_mutated_tail"

    def inject_mutation(stage: str, s: Path, t: Path | None) -> None:
        if stage == fault_stage:
            s.write_bytes(mutated_bytes)

    with pytest.raises(XlsxSourceNotReadyError) as exc_info:
        with open_stable_xlsx_snapshot(
            src, root, 0.001, _sleeper=lambda _: None, _fault_hook=inject_mutation
        ):
            pass
    assert exc_info.value.retryable is True
    assert list(root.iterdir()) == []


@pytest.mark.parametrize(
    "fault_stage",
    [
        "during_observation",
        "before_copy_open",
        "during_copy_chunk",
        "before_source_reverify",
        "during_source_reverify",
    ],
)
def test_sa05_atomic_os_replace_at_every_race_point(
    tmp_path: Path, fault_stage: str
) -> None:
    """SA-05 & R2-03: Atomic replacement at any stage aborts acquisition."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    replacement_src = tmp_path / "replacement.xlsx"
    replacement_src.write_bytes(_build_valid_test_xlsx() + b"replacement_bytes")

    def inject_replacement(stage: str, s: Path, t: Path | None) -> None:
        if stage == fault_stage and replacement_src.exists():
            os.replace(replacement_src, s)

    with pytest.raises(XlsxSourceNotReadyError) as exc_info:
        with open_stable_xlsx_snapshot(
            src,
            root,
            0.001,
            _sleeper=lambda _: None,
            _fault_hook=inject_replacement,
        ):
            pass
    assert exc_info.value.retryable is True
    assert list(root.iterdir()) == []


def test_sa05_source_symlink_race_mutation_with_same_inode(
    tmp_path: Path,
) -> None:
    """SA-05 & R2-02: Renaming source and replacing with symlink is detected."""
    if not hasattr(os, "symlink"):
        pytest.skip("Symlinks not supported on this platform")

    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    renamed_target = tmp_path / "renamed_same_inode.xlsx"

    def inject_symlink_swap(stage: str, s: Path, t: Path | None) -> None:
        if stage == "during_copy_chunk" and s.exists():
            os.replace(s, renamed_target)
            s.symlink_to(renamed_target)

    with pytest.raises(XlsxSourceNotReadyError) as exc_info:
        with open_stable_xlsx_snapshot(
            src,
            root,
            0.001,
            _sleeper=lambda _: None,
            _fault_hook=inject_symlink_swap,
        ):
            pass
    assert exc_info.value.retryable is True
    assert list(root.iterdir()) == []


# ============================================================================
# SA-06: Streaming copy is bounded, exact, and SHA-256 verified
# ============================================================================


def test_sa06_streaming_copy_bounded_chunks(tmp_path: Path) -> None:
    """SA-06: Verifies chunked copying never exceeds specified chunk size."""
    src = tmp_path / "source.xlsx"
    data = _build_valid_test_xlsx()
    src.write_bytes(data)
    root = tmp_path / "root"
    root.mkdir()

    chunk_size = 256  # small bounded chunk
    expected_sha = hashlib.sha256(data).hexdigest()

    with open_stable_xlsx_snapshot(
        src,
        root,
        0.001,
        _sleeper=lambda _: None,
        _copy_chunk_size=chunk_size,
    ) as snap:
        assert snap.byte_count == len(data)
        assert snap.file_sha256 == expected_sha
        assert snap.snapshot_path.stat().st_size == len(data)


def test_sa06_bounded_stream_wrapper_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SA-06: Proves no read(-1) occurs and all reads <= copy_chunk_size."""
    src = tmp_path / "source.xlsx"
    data = _build_valid_test_xlsx()
    src.write_bytes(data)
    root = tmp_path / "root"
    root.mkdir()

    read_calls: list[int] = []
    orig_open = open

    class MonitoredFile:
        def __init__(self, f: Any) -> None:
            self._f = f

        def read(self, n: int = -1) -> bytes:
            assert n is not None and n > 0, f"Unbounded read({n}) detected!"
            read_calls.append(n)
            res = self._f.read(n)
            assert isinstance(res, bytes)
            return res

        def fileno(self) -> int:
            res = self._f.fileno()
            assert isinstance(res, int)
            return res

        def __enter__(self) -> MonitoredFile:
            self._f.__enter__()
            return self

        def __exit__(self, *args: Any) -> Any:
            return self._f.__exit__(*args)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._f, name)

    def monitored_open(file: Any, mode: str = "r", **kwargs: Any) -> Any:
        f = orig_open(file, mode, **kwargs)
        if mode == "rb":
            return MonitoredFile(f)
        return f

    monkeypatch.setattr("builtins.open", monitored_open)

    with open_stable_xlsx_snapshot(
        src, root, 0.001, _sleeper=lambda _: None, _copy_chunk_size=512
    ) as snap:
        assert snap.byte_count == len(data)

    assert read_calls, "No read calls captured"
    copy_reads = [r for r in read_calls if r == 512]
    assert len(copy_reads) > 0, "No 512-byte copy chunks captured"
    for r_size in read_calls:
        # Candidate validation reads EOCD (up to 4096 bytes), copy reads <= 512
        assert r_size <= 4096, f"Unbounded read({r_size}) detected!"


# ============================================================================
# SA-07: Invalid ZIP/container, write/flush/fsync storage faults
# ============================================================================


def test_sa07_zip_central_directory_only_and_no_testzip_or_open_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SA-07: testzip/open are NEVER called; Central Directory accepted."""
    root = tmp_path / "root"
    root.mkdir()

    # Build XLSX with extra unrelated members BEFORE monkeypatching
    builder = SyntheticXlsxBuilder()
    u1 = _make_uuid7(b"sheet1_row2_uuid")
    builder.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u1, 2)])
    u2 = _make_uuid7(b"sheet2_row2_uuid")
    builder.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u2, 2)])
    u3 = _make_uuid7(b"sheet3_row2_uuid")
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u3, 2)])
    u4 = _make_uuid7(b"sheet4_row2_uuid")
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u4, 2)])
    builder.add_helper_sheet("unreferenced_notes", "<note>extra</note>")

    src = tmp_path / "extra_members.xlsx"
    src.write_bytes(builder.build_bytes())

    # Intercept ZipFile.open and ZipFile.testzip: must NOT be called for reading
    def forbidden_open(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("ZipFile.open was called during acquisition!")

    def forbidden_testzip(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("ZipFile.testzip was called during acquisition!")

    monkeypatch.setattr(zipfile.ZipFile, "open", forbidden_open)
    monkeypatch.setattr(zipfile.ZipFile, "testzip", forbidden_testzip)

    with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None) as snap:
        assert snap.snapshot_path.exists()
    assert list(root.iterdir()) == []


def test_sa07_invalid_container_and_storage_faults(tmp_path: Path) -> None:
    """SA-07: Corrupted ZIP or missing package marker fails typed and cleans."""
    root = tmp_path / "root"
    root.mkdir()

    # 1. Non-ZIP content
    non_zip = tmp_path / "non_zip.xlsx"
    non_zip.write_bytes(b"not a zip file at all")
    with pytest.raises(XlsxSourceNotReadyError, match="not a valid ZIP container"):
        with open_stable_xlsx_snapshot(non_zip, root, 0.001, _sleeper=lambda _: None):
            pass
    assert list(root.iterdir()) == []

    # 2. ZIP without [Content_Types].xml
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dummy.txt", "hello")
    missing_ct = tmp_path / "missing_ct.xlsx"
    missing_ct.write_bytes(buf.getvalue())

    with pytest.raises(
        XlsxSourceNotReadyError, match="missing \\[Content_Types\\]\\.xml"
    ):
        with open_stable_xlsx_snapshot(
            missing_ct, root, 0.001, _sleeper=lambda _: None
        ):
            pass
    assert list(root.iterdir()) == []

    # 3. Promotion failure injection
    valid_src = tmp_path / "valid.xlsx"
    valid_src.write_bytes(_build_valid_test_xlsx())

    def block_promotion(stage: str, s: Path, t: Path | None) -> None:
        if stage == "before_promotion" and t is not None:
            t.unlink()

    with pytest.raises((ExceptionGroup, BaseExceptionGroup, XlsxSnapshotStorageError)):
        with open_stable_xlsx_snapshot(
            valid_src,
            root,
            0.001,
            _sleeper=lambda _: None,
            _fault_hook=block_promotion,
        ):
            pass
    assert list(root.iterdir()) == []


def test_sa07_candidate_symlink_rejection_leaves_target_untouched(
    tmp_path: Path,
) -> None:
    """SA-07 & R2-01: Candidate symlink fails and leaves target untouched."""
    if not hasattr(os, "symlink"):
        pytest.skip("Symlinks not supported on this platform")

    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    external_target = tmp_path / "external_target.xlsx"
    external_target.write_bytes(_build_valid_test_xlsx())
    initial_target_bytes = external_target.read_bytes()

    def inject_candidate_symlink(stage: str, s: Path, t: Path | None) -> None:
        if stage == "before_zip_validation" and t is not None:
            t.unlink()
            t.symlink_to(external_target)

    with pytest.raises(
        (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotStorageError)
    ) as exc_info:
        with open_stable_xlsx_snapshot(
            src,
            root,
            0.001,
            _sleeper=lambda _: None,
            _fault_hook=inject_candidate_symlink,
        ):
            pass

    flat = _flatten_exceptions(exc_info.value)
    assert any(isinstance(e, XlsxSnapshotStorageError) for e in flat)

    # External target must remain completely untouched
    assert external_target.exists()
    assert external_target.read_bytes() == initial_target_bytes


def test_sa07_pre_existing_final_snapshot_not_overwritten_or_deleted(
    tmp_path: Path,
) -> None:
    """SA-07 & R2-01: Pre-existing final snapshot file is NOT overwritten or deleted."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    sentinel_bytes = b"PRE_EXISTING_SNAPSHOT_SENTINEL_DATA"
    created_sentinel_file: Path | None = None

    def inject_pre_existing_final(stage: str, s: Path, t: Path | None) -> None:
        nonlocal created_sentinel_file
        if stage == "before_promotion" and t is not None:
            final_p = t.parent / "snapshot.xlsx"
            final_p.write_bytes(sentinel_bytes)
            created_sentinel_file = final_p

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotStorageError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src,
                root,
                0.001,
                _sleeper=lambda _: None,
                _fault_hook=inject_pre_existing_final,
            ):
                pass

        flat = _flatten_exceptions(exc_info.value)
        assert any(
            isinstance(e, XlsxSnapshotStorageError) and "already exists" in str(e)
            for e in flat
        )
        assert created_sentinel_file is not None
        assert created_sentinel_file.exists()
        assert created_sentinel_file.read_bytes() == sentinel_bytes
    finally:
        # Controlled test cleanup
        if created_sentinel_file is not None and created_sentinel_file.exists():
            created_sentinel_file.unlink()
            try:
                created_sentinel_file.parent.rmdir()
            except OSError:
                pass


def test_sa07_real_stream_io_faults_and_short_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SA-07 & R2-04: Stream read/write/flush/fsync errors mapped with taxonomy."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    # 1. Real Source Read OSError -> XlsxSourceNotReadyError (retryable=True)
    orig_open = open

    class FailingReadStream:
        def __init__(self, f: Any) -> None:
            self._f = f

        def read(self, n: int = -1) -> bytes:
            raise OSError("Hardware disk read failure")

        def fileno(self) -> int:
            return int(self._f.fileno())

        def __enter__(self) -> FailingReadStream:
            self._f.__enter__()
            return self

        def __exit__(self, *args: Any) -> Any:
            return self._f.__exit__(*args)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._f, name)

    def failing_read_open(file: Any, mode: str = "r", **kwargs: Any) -> Any:
        f = orig_open(file, mode, **kwargs)
        if mode == "rb":
            return FailingReadStream(f)
        return f

    monkeypatch.setattr("builtins.open", failing_read_open)
    with pytest.raises(XlsxSourceNotReadyError) as exc_info1:
        with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None):
            pass
    assert exc_info1.value.retryable is True
    monkeypatch.undo()

    # 2. Real Candidate Write Short Write -> XlsxSnapshotStorageError (retryable=False)
    class ShortWriteStream:
        def __init__(self, f: Any) -> None:
            self._f = f

        def write(self, b: bytes) -> int:
            return len(b) - 1  # Short write

        def fileno(self) -> int:
            return int(self._f.fileno())

        def __enter__(self) -> ShortWriteStream:
            self._f.__enter__()
            return self

        def __exit__(self, *args: Any) -> Any:
            return self._f.__exit__(*args)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._f, name)

    def failing_write_open(file: Any, mode: str = "r", **kwargs: Any) -> Any:
        f = orig_open(file, mode, **kwargs)
        if mode == "wb":
            return ShortWriteStream(f)
        return f

    monkeypatch.setattr("builtins.open", failing_write_open)
    with pytest.raises(XlsxSnapshotStorageError) as exc_info2:
        with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None):
            pass
    assert exc_info2.value.retryable is False
    monkeypatch.undo()

    # 3. Real fsync OSError -> XlsxSnapshotStorageError (retryable=False)
    def failing_fsync(fd: int) -> None:
        raise OSError("Disk sync failure")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(XlsxSnapshotStorageError) as exc_info3:
        with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None):
            pass
    assert exc_info3.value.retryable is False
    monkeypatch.undo()


# ============================================================================
# SA-08: Consumer observes promoted .xlsx; source mutation isolated
# ============================================================================


def test_sa08_promoted_path_and_source_mutation_isolation(
    tmp_path: Path,
) -> None:
    """SA-08: Consumer sees promoted .xlsx; source mutation has no effect."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None) as snap:
        assert snap.snapshot_path.name == "snapshot.xlsx"
        assert snap.snapshot_path.exists()

        # Mutate or delete original source while inside lease
        src.write_bytes(b"completely destroyed source")

        # WP-05 Reader reads snapshot_path without error
        res = read_xlsx_source_snapshot(snap.snapshot_path)
        assert len(res.snapshot.sheets) == 4
        assert len(res.locations_by_uuid) == 4

    # On context exit, lease dir is cleaned up
    assert not snap.snapshot_path.exists()
    assert list(root.iterdir()) == []


def test_sa08_posix_private_file_permissions(tmp_path: Path) -> None:
    """SA-08 & R2-01: Verifies private 0700 dir and 0600 file on POSIX."""
    if os.name != "posix":
        pytest.skip("POSIX permission check only applicable on POSIX")

    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None) as snap:
        lease_dir = snap.snapshot_path.parent
        dir_mode = stat.S_IMODE(lease_dir.stat().st_mode)
        file_mode = stat.S_IMODE(snap.snapshot_path.stat().st_mode)

        assert dir_mode == 0o700, f"Lease dir mode: {oct(dir_mode)}"
        assert file_mode == 0o600, f"Snapshot file mode: {oct(file_mode)}"


def test_sa08_snapshot_path_never_resolves_externally(tmp_path: Path) -> None:
    """SA-08 & R2-01: snapshot_path never resolves to an external symlink target."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None) as snap:
        assert snap.snapshot_path.parent.parent == root
        assert not snap.snapshot_path.is_symlink()
        assert snap.snapshot_path.is_file()


# ============================================================================
# SA-09: Lease integrity and cleanup lifecycle
# ============================================================================


def test_sa09_lease_mutation_and_deletion_detection(tmp_path: Path) -> None:
    """SA-09: Modification or deletion of snapshot during lease raises error."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    # 1. Mutated during lease (conservative cleanup preserves mutated file)
    leased_p: Path | None = None
    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotIntegrityError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src, root, 0.001, _sleeper=lambda _: None
            ) as snap:
                leased_p = snap.snapshot_path
                snap.snapshot_path.write_bytes(b"corrupted snapshot during lease")

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotIntegrityError) for e in flat)
    finally:
        if leased_p is not None and leased_p.exists():
            leased_p.unlink()
            try:
                leased_p.parent.rmdir()
            except OSError:
                pass

    # 2. Deleted during lease (already unlinked -> clean exit)
    with pytest.raises(
        XlsxSnapshotIntegrityError, match="disappeared or is inaccessible"
    ):
        with open_stable_xlsx_snapshot(
            src, root, 0.001, _sleeper=lambda _: None
        ) as snap:
            snap.snapshot_path.unlink()

    assert list(root.iterdir()) == []


def test_sa09_atomic_os_replace_during_lease_preserves_replacement_file(
    tmp_path: Path,
) -> None:
    """SA-09 & R2-03: os.replace during lease detected; replacement file NOT deleted."""
    src = tmp_path / "source.xlsx"
    data = _build_valid_test_xlsx()
    src.write_bytes(data)
    root = tmp_path / "root"
    root.mkdir()

    replacement = tmp_path / "replacement_same_bytes.xlsx"
    replacement.write_bytes(data)
    leased_snapshot_path: Path | None = None

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotIntegrityError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src, root, 0.001, _sleeper=lambda _: None
            ) as snap:
                leased_snapshot_path = snap.snapshot_path
                os.replace(replacement, snap.snapshot_path)

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotIntegrityError) for e in flat)
        assert any(isinstance(e, XlsxSnapshotCleanupError) for e in flat)

        # Replacement file must remain in place on disk for forensics
        assert leased_snapshot_path is not None
        assert leased_snapshot_path.exists()
    finally:
        # Controlled test cleanup
        if leased_snapshot_path is not None and leased_snapshot_path.exists():
            leased_snapshot_path.unlink()
            try:
                leased_snapshot_path.parent.rmdir()
            except OSError:
                pass


def test_sa09_symlink_replacement_during_lease(tmp_path: Path) -> None:
    """SA-09 & R2-01: Replacing snapshot with a symlink raises integrity error."""
    if not hasattr(os, "symlink"):
        pytest.skip("Symlink creation not supported")

    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    target_file = tmp_path / "target.xlsx"
    target_file.write_bytes(_build_valid_test_xlsx())
    leased_p: Path | None = None

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotIntegrityError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src, root, 0.001, _sleeper=lambda _: None
            ) as snap:
                leased_p = snap.snapshot_path
                snap.snapshot_path.unlink()
                snap.snapshot_path.symlink_to(target_file)

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotIntegrityError) for e in flat)
        assert any(isinstance(e, XlsxSnapshotCleanupError) for e in flat)
    finally:
        if leased_p is not None and (leased_p.exists() or leased_p.is_symlink()):
            leased_p.unlink()
            try:
                leased_p.parent.rmdir()
            except OSError:
                pass


# ============================================================================
# SA-10: Cleanup failure visibility and exception chaining / groups
# ============================================================================


def test_sa10_acquisition_and_cleanup_coincident_exception_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SA-10 & R2-05: Real acquisition + cleanup failure combined with causes."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    # Fault: corrupt candidate before reverify
    def corrupt_cand(stage: str, s: Path, t: Path | None) -> None:
        if stage == "before_candidate_reverify" and t is not None:
            t.write_bytes(b"corrupted")

    # Fault: mock unlink raising OSError during cleanup
    orig_unlink = Path.unlink

    def failing_unlink(self: Path) -> None:
        if (
            self.name.startswith("snapshot")
            or self.name.startswith(".qpart")
            or self.name.startswith(".qfinal")
        ):
            raise OSError("simulated unlink lock error")
        orig_unlink(self)

    monkeypatch.setattr(Path, "unlink", failing_unlink)

    with pytest.raises((ExceptionGroup, BaseExceptionGroup)) as exc_info:
        with open_stable_xlsx_snapshot(
            src, root, 0.001, _sleeper=lambda _: None, _fault_hook=corrupt_cand
        ):
            pass

    flat = _flatten_exceptions(exc_info.value)
    assert len(flat) == 2
    assert any(isinstance(e, XlsxSnapshotIntegrityError) for e in flat)
    cln_excs = [e for e in flat if isinstance(e, XlsxSnapshotCleanupError)]
    assert len(cln_excs) == 1
    assert cln_excs[0].__cause__ is not None


def test_sa10_consumer_and_cleanup_coincident_exception_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SA-10 & R2-05: Consumer failure + cleanup failure combined in ExceptionGroup."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    orig_rmdir = Path.rmdir

    def failing_rmdir(self: Path) -> None:
        if self.name.startswith("acq-") or self.name.startswith(".qdir"):
            raise OSError("simulated rmdir lock")
        orig_rmdir(self)

    monkeypatch.setattr(Path, "rmdir", failing_rmdir)

    with pytest.raises((ExceptionGroup, BaseExceptionGroup)) as exc_info:
        with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None):
            raise ValueError("consumer business logic crash")

    flat = _flatten_exceptions(exc_info.value)
    assert len(flat) == 2
    assert any(isinstance(e, ValueError) for e in flat)
    cln_excs = [e for e in flat if isinstance(e, XlsxSnapshotCleanupError)]
    assert len(cln_excs) == 1
    assert cln_excs[0].__cause__ is not None


def test_sa10_consumer_integrity_and_cleanup_coincident_exception_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SA-10 & R2-05: Consumer + Integrity + Cleanup coincident failures."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    orig_rmdir = Path.rmdir

    def failing_rmdir(self: Path) -> None:
        if self.name.startswith("acq-") or self.name.startswith(".qdir"):
            raise OSError("simulated rmdir lock")
        orig_rmdir(self)

    monkeypatch.setattr(Path, "rmdir", failing_rmdir)

    with pytest.raises((ExceptionGroup, BaseExceptionGroup)) as exc_info:
        with open_stable_xlsx_snapshot(
            src, root, 0.001, _sleeper=lambda _: None
        ) as snap:
            # Cause integrity error
            snap.snapshot_path.write_bytes(b"corrupted during lease")
            # Cause consumer error
            raise RuntimeError("consumer business exception")

    flat = _flatten_exceptions(exc_info.value)
    assert len(flat) == 3
    assert any(isinstance(e, RuntimeError) for e in flat)
    assert any(isinstance(e, XlsxSnapshotIntegrityError) for e in flat)
    cln_excs = [e for e in flat if isinstance(e, XlsxSnapshotCleanupError)]
    assert len(cln_excs) == 1
    assert cln_excs[0].__cause__ is not None


# ============================================================================
# SA-11: Concurrent disjoint acquisitions (Identical & Distinct content)
# ============================================================================


def test_sa11_concurrent_disjoint_acquisitions(tmp_path: Path) -> None:
    """SA-11 & R2-07: Concurrent acquisitions: SHA equality, disjoint paths."""
    src1 = tmp_path / "source1.xlsx"
    src2 = tmp_path / "source2.xlsx"
    src3 = tmp_path / "source3.xlsx"

    data1 = _build_valid_test_xlsx()
    builder3 = SyntheticXlsxBuilder()
    u3_1 = _make_uuid7(b"sheet1_row2_diff1")
    u3_2 = _make_uuid7(b"sheet2_row2_diff2")
    u3_3 = _make_uuid7(b"sheet3_row2_diff3")
    u3_4 = _make_uuid7(b"sheet4_row2_diff4")
    builder3.add_sheet_rows("خرید-فروش", [_sample_buy_sell_row_data(u3_1, 2)])
    builder3.add_sheet_rows(
        "دریافت-پرداخت", [_sample_receipts_payments_row_data(u3_2, 2)]
    )
    builder3.add_sheet_rows(
        "ورود-خروج", [_sample_inventory_movements_row_data(u3_3, 2)]
    )
    builder3.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u3_4, 2)])
    data3 = builder3.build_bytes()

    src1.write_bytes(data1)
    src2.write_bytes(data1)  # same content as 1
    src3.write_bytes(data3)  # different content
    root = tmp_path / "root"
    root.mkdir()

    barrier = threading.Barrier(3)
    worker1_done = threading.Event()
    results: list[dict[str, Any]] = []

    def worker1() -> None:
        with open_stable_xlsx_snapshot(
            src1, root, 0.001, _sleeper=lambda _: None
        ) as snap:
            barrier.wait()
            res = read_xlsx_source_snapshot(snap.snapshot_path)
            results.append(
                {
                    "src": src1,
                    "path": snap.snapshot_path,
                    "sha": snap.file_sha256,
                    "res": res,
                }
            )
        # Context exited -> lease 1 is cleaned up
        worker1_done.set()

    def worker_long(src_p: Path) -> None:
        with open_stable_xlsx_snapshot(
            src_p, root, 0.001, _sleeper=lambda _: None
        ) as snap:
            barrier.wait()
            # Wait until worker 1 has exited its lease
            worker1_done.wait(timeout=5.0)
            # Verify our leased snapshot still exists intact
            assert snap.snapshot_path.exists()
            res = read_xlsx_source_snapshot(snap.snapshot_path)
            results.append(
                {
                    "src": src_p,
                    "path": snap.snapshot_path,
                    "sha": snap.file_sha256,
                    "res": res,
                }
            )

    threads = [
        threading.Thread(target=worker1),
        threading.Thread(target=worker_long, args=(src2,)),
        threading.Thread(target=worker_long, args=(src3,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert len(results) == 3
    paths = {r["path"] for r in results}
    assert len(paths) == 3, "All 3 concurrent snapshot paths must be disjoint"

    # Verify SHA parity
    r1 = next(r for r in results if r["src"] == src1)
    r2 = next(r for r in results if r["src"] == src2)
    r3 = next(r for r in results if r["src"] == src3)

    assert r1["sha"] == r2["sha"], "Identical sources must have equal SHA-256"
    assert r1["sha"] != r3["sha"], "Distinct sources must have distinct SHA-256"
    assert list(root.iterdir()) == []  # all cleaned up


# ============================================================================
# SA-12: Full four-sheet workbook passes acquisition and matches WP-04 oracle
# ============================================================================


def test_sa12_end_to_end_reader_and_planner_oracle(tmp_path: Path) -> None:
    """SA-12 & R2-07: Full snapshot equality and hashes match WP-04/WP-03 oracle."""
    src = tmp_path / "full_book.xlsx"
    data = _build_valid_test_xlsx()
    src.write_bytes(data)
    root = tmp_path / "root"
    root.mkdir()

    with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None) as snap:
        assert snap.file_sha256 == hashlib.sha256(data).hexdigest()

        read_res = read_xlsx_source_snapshot(snap.snapshot_path)
        assert len(read_res.snapshot.sheets) == 4
        assert len(read_res.locations_by_uuid) == 4

        u1 = _make_uuid7(b"sheet1_row2_uuid")
        u2 = _make_uuid7(b"sheet2_row2_uuid")
        u3 = _make_uuid7(b"sheet3_row2_uuid")
        u4 = _make_uuid7(b"sheet4_row2_uuid")

        expected_oracle = build_source_workbook_snapshot(
            [
                SourceSheetInput(
                    "خرید-فروش",
                    [
                        SourceRowInput(
                            u1,
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
                        )
                    ],
                ),
                SourceSheetInput(
                    "دریافت-پرداخت",
                    [
                        SourceRowInput(
                            u2,
                            {
                                "date_raw": "1403/01/01",
                                "party_name_raw": "همکار نمونه",
                                "entry_type_raw": "RS",
                                "amount_toman_raw": "50000000",
                                "notes_raw": "تسویه حساب",
                                "account_code_raw": "101",
                                "customer_flag_raw": "1",
                            },
                        )
                    ],
                ),
                SourceSheetInput(
                    "ورود-خروج",
                    [
                        SourceRowInput(
                            u3,
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
                        )
                    ],
                ),
                SourceSheetInput(
                    "لیست کسبه",
                    [
                        SourceRowInput(
                            u4,
                            {
                                "party_name_raw": "فروشگاه نمونه",
                                "phone_number_raw": "SYNTHETIC-PHONE-001",
                            },
                        )
                    ],
                ),
            ]
        )

        assert read_res.snapshot == expected_oracle
        for s_name in RAW_CONTRACT_REGISTRY.sheets:
            assert (
                read_res.snapshot.sheets[s_name].sheet_snapshot_hash
                == expected_oracle.sheets[s_name].sheet_snapshot_hash
            )

        # Verify change planner integration on fresh registry
        empty_reg = build_prior_identity_registry([])
        plan = plan_source_changes(read_res.snapshot, empty_reg)
        assert plan.total_counts.insert_count == 4
        for item in plan.items:
            assert item.action == PlanAction.INSERT

    assert list(root.iterdir()) == []


# ============================================================================
# SA-13: Source change produces no Reader result preventing partial voids
# ============================================================================


def test_sa13_source_change_prevents_partial_planner_voids(
    tmp_path: Path,
) -> None:
    """SA-13: Changing source aborts acquisition before Reader/Planner is invoked."""
    src = tmp_path / "changing_source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    reader_invoked = False

    def race_hook(stage: str, s: Path, t: Path | None) -> None:
        if stage == "before_source_reverify":
            s.write_bytes(b"truncated")

    with pytest.raises(XlsxSourceNotReadyError):
        with open_stable_xlsx_snapshot(
            src, root, 0.001, _sleeper=lambda _: None, _fault_hook=race_hook
        ) as snap:
            reader_invoked = True
            read_xlsx_source_snapshot(snap.snapshot_path)

    assert not reader_invoked
    assert list(root.iterdir()) == []


# ============================================================================
# SA-14: Hypothesis property testing & Combined 15,000-Row Benchmark
# ============================================================================


@settings(max_examples=25, deadline=None)
@given(
    chunk_size=st.integers(min_value=64, max_value=8192),
    interval=st.floats(min_value=0.0001, max_value=0.01),
)
def test_sa14_hypothesis_chunk_and_interval_properties(
    tmp_path_factory: pytest.TempPathFactory,
    chunk_size: int,
    interval: float,
) -> None:
    """SA-14: Hypothesis property testing for arbitrary chunk sizes and intervals."""
    tmp_path = tmp_path_factory.mktemp("hyp")
    src = tmp_path / "hyp_source.xlsx"
    data = _build_valid_test_xlsx()
    src.write_bytes(data)
    root = tmp_path / "root"
    root.mkdir()

    with open_stable_xlsx_snapshot(
        src,
        root,
        interval,
        _sleeper=lambda _: None,
        _copy_chunk_size=chunk_size,
    ) as snap:
        assert snap.byte_count == len(data)
        assert snap.file_sha256 == hashlib.sha256(data).hexdigest()
        assert snap.snapshot_path.stat().st_size == len(data)

    assert list(root.iterdir()) == []


def test_sa14_combined_15000_row_benchmark(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """SA-14: Combined benchmark: 15,000 active rows in acquisition + WP-05 reader."""
    builder = SyntheticXlsxBuilder()
    builder.dimension_ref = "A1:Z1000000"
    builder.shared_strings = [f"UNUSED_SST_ENTRY_{i:04d}" for i in range(100)]

    total_active_target = 15000
    rows_per_sheet = total_active_target // 4

    for sheet_idx, s_name in enumerate(RAW_CONTRACT_REGISTRY.sheets, 1):
        sheet_rows = []
        for r_idx in range(2, rows_per_sheet + 2):
            raw_uuid_bytes = f"{sheet_idx:04d}{r_idx:012d}".encode()
            u = _make_uuid7(raw_uuid_bytes)
            if s_name == "خرید-فروش":
                sheet_rows.append(_sample_buy_sell_row_data(u, r_idx))
            elif s_name == "دریافت-پرداخت":
                sheet_rows.append(_sample_receipts_payments_row_data(u, r_idx))
            elif s_name == "ورود-خروج":
                sheet_rows.append(_sample_inventory_movements_row_data(u, r_idx))
            else:
                sheet_rows.append(_sample_business_parties_row_data(u, r_idx))

        # 1,250 tail rows per sheet (5,000 total across 4 sheets)
        for r_idx in range(rows_per_sheet + 2, rows_per_sheet + 419):
            sheet_rows.append(
                {
                    "__row_num__": r_idx,
                    "A": str(r_idx),
                    "B": "" if s_name == "لیست کسبه" else "1403/01/01",
                    "C": "",
                }
            )
        for r_idx in range(rows_per_sheet + 419, rows_per_sheet + 836):
            sheet_rows.append(
                {
                    "__row_num__": r_idx,
                    "A": str(r_idx),
                    "F": {"f": "SUM(F2:F3751)", "v": "12345.67"},
                }
            )
        for r_idx in range(rows_per_sheet + 836, rows_per_sheet + 1252):
            sheet_rows.append(
                {
                    "__row_num__": r_idx,
                    "A": {"s": "1", "v": str(r_idx)},
                    "B": {"s": "2", "v": ""},
                }
            )

        builder.add_sheet_rows(s_name, sheet_rows)

    pkg_path = tmp_path / "benchmark_15000.xlsx"
    pkg_path.write_bytes(builder.build_bytes())
    root_path = tmp_path / "snapshot_root"
    root_path.mkdir()

    # Subprocess execution to measure isolated call window time and memory
    bench_code = f"""
import sys, time, threading, re
from pathlib import Path
from accounting_local_agent import open_stable_xlsx_snapshot, read_xlsx_source_snapshot

def parse_linux_proc_status_vmrss_mib(content: str) -> float:
    found = False
    val_mib = 0.0
    for line in content.splitlines():
        if line.startswith("VmRSS:"):
            if found:
                raise RuntimeError(
                    "Linux /proc/self/status contains duplicate VmRSS line"
                )
            found = True
            parts = line.split()
            if (
                len(parts) != 3
                or parts[0] != "VmRSS:"
                or parts[2] != "kB"
                or not re.fullmatch(r"^[1-9][0-9]*$", parts[1])
            ):
                raise RuntimeError(
                    f"Linux /proc/self/status invalid VmRSS token format: '{{line}}'"
                )
            val_mib = int(parts[1]) / 1024.0
    if not found:
        raise RuntimeError("Linux /proc/self/status missing VmRSS entry")
    return val_mib

def get_current_process_rss_mib() -> float:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_uint32),
                ("PageFaultCount", ctypes.c_uint32),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE

        try:
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
        except Exception:
            psapi = kernel32

        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
            ctypes.c_uint32,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        handle = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        ):
            err = ctypes.get_last_error()
            raise RuntimeError(
                f"Windows GetProcessMemoryInfo failed with error code {{err}}"
            )

        working_set_bytes = int(counters.WorkingSetSize)
        working_set_mib = float(working_set_bytes) / (1024.0 * 1024.0)
        if working_set_mib <= 0.0:
            raise RuntimeError(
                "Windows GetProcessMemoryInfo returned non-positive "
                f"set: {{working_set_mib}}"
            )
        return working_set_mib
    elif sys.platform.startswith("linux"):
        try:
            with open("/proc/self/status", encoding="utf-8") as f:
                return parse_linux_proc_status_vmrss_mib(f.read())
        except Exception as exc:
            raise RuntimeError(f"Failed to read Linux current RSS: {{exc}}") from exc
    else:
        raise RuntimeError(f"Unsupported benchmark platform: {{sys.platform}}")

class CallWindowRssSampler:
    def __init__(self, interval_seconds: float = 0.005) -> None:
        self.interval_seconds = interval_seconds
        self._peak_rss_mib = 0.0
        self._stop_event = threading.Event()
        self._thread = None
        self._error = None
        self._state = "INITIAL"

    def start(self) -> None:
        if self._state != "INITIAL":
            raise RuntimeError("Sampler can only be started once")
        self._state = "RUNNING"
        self._peak_rss_mib = get_current_process_rss_mib()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                current = get_current_process_rss_mib()
                if current > self._peak_rss_mib:
                    self._peak_rss_mib = current
                self._stop_event.wait(self.interval_seconds)
        except Exception as exc:
            self._error = exc

    def stop_and_get_peak(self) -> float:
        if self._state != "RUNNING":
            raise RuntimeError("Sampler cannot be stopped in current state")
        self._state = "STOPPED"
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                raise RuntimeError(
                    "Sampler worker thread failed to terminate within timeout"
                )
        if self._error is not None:
            raise RuntimeError(
                f"Sampler worker failed: {{self._error}}"
            ) from self._error
        return self._peak_rss_mib

sampler = CallWindowRssSampler(interval_seconds=0.005)
baseline_rss = get_current_process_rss_mib()

src_p = Path({str(pkg_path)!r})
root_p = Path({str(root_path)!r})

t0 = time.perf_counter()
sampler.start()
total_rows = 0
file_sha = ""
file_bytes = 0

try:
    with open_stable_xlsx_snapshot(
        src_p, root_p, 0.001, _sleeper=lambda _: None
    ) as snap:
        file_sha = snap.file_sha256
        file_bytes = snap.byte_count
        res = read_xlsx_source_snapshot(snap.snapshot_path)
        for s in res.snapshot.sheets.values():
            total_rows += len(s.rows)
finally:
    call_peak_rss = sampler.stop_and_get_peak()

duration = time.perf_counter() - t0

# Verify lease cleanup in benchmark
assert list(root_p.iterdir()) == [], "Lease directory leaked in benchmark!"

print(
    f"BENCHMARK_RESULT|{{duration:.4f}}|{{baseline_rss:.2f}}|"
    f"{{call_peak_rss:.2f}}|{{total_rows}}|{{file_bytes}}|{{file_sha}}"
)
"""

    res = subprocess.run(
        [sys.executable, "-c", bench_code],
        capture_output=True,
        text=True,
        check=True,
    )

    out_line = ""
    for line in res.stdout.splitlines():
        if line.startswith("BENCHMARK_RESULT|"):
            out_line = line
            break

    assert out_line, (
        f"Benchmark did not output result line:\n{res.stdout}\n{res.stderr}"
    )
    parts = out_line.strip().split("|")
    duration = float(parts[1])
    baseline_rss = float(parts[2])
    call_peak_rss = float(parts[3])
    total_rows = int(parts[4])
    file_bytes = int(parts[5])
    file_sha = parts[6]

    print(
        f"\n[WP-06 BENCHMARK] 15,000 active rows (Acquisition + Reader) -> "
        f"duration: {duration:.4f}s | "
        f"baseline_current_rss_mib: {baseline_rss:.2f} MiB | "
        f"call_peak_rss_mib: {call_peak_rss:.2f} MiB | rows: {total_rows} | "
        f"file_bytes: {file_bytes} | file_sha: {file_sha[:12]}... | "
        f"platform: {sys.platform}"
    )

    assert total_rows == 15000
    assert duration < 15.0, f"Benchmark duration {duration:.4f}s exceeds 15.0s limit"
    assert call_peak_rss < 128.0, (
        f"Benchmark call peak RSS {call_peak_rss:.2f} MiB exceeds 128.0 MiB limit"
    )


# ============================================================================
# Round 8 Oracles & Regressions (R8-01 to R8-04)
# ============================================================================


def test_r8_01_anchor_failure_between_mkdir_and_open_fail_closed_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 1 (R8-01): Fail-closed anchor failure on open prevents yield."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    displaced_owned_dir: Path | None = None
    foreign_dir: Path | None = None
    anchor_yielded = False

    def inject_displace_before_anchor(stage: str, s: Path, t: Path | None) -> None:
        nonlocal displaced_owned_dir, foreign_dir
        if stage == "after_mkdir_before_anchor":
            displaced_owned_dir = root / f"displaced-{s.name}"
            os.rename(s, displaced_owned_dir)
            s.mkdir()
            foreign_dir = s

    orig_open = os.open

    def mock_open(path: Any, flags: int, *args: int) -> int:
        if foreign_dir is not None and str(path) == str(foreign_dir):
            raise OSError("Injected descriptor anchor open failure")
        return orig_open(path, flags, *args)

    monkeypatch.setattr(os, "open", mock_open)

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotStorageError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src,
                root,
                0.001,
                _sleeper=lambda _: None,
                _fault_hook=inject_displace_before_anchor,
            ):
                anchor_yielded = True

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotStorageError) for e in flat)

        # R8-04 Invariants:
        assert anchor_yielded is False, "ANCHOR_FAILURE_YIELDED must be False"
        assert foreign_dir is not None and foreign_dir.exists(), (
            "ANCHOR_FOREIGN_SURVIVED must be True"
        )
        assert displaced_owned_dir is not None and displaced_owned_dir.exists(), (
            "ANCHOR_DISPLACED_OWNED_SURVIVED must be True"
        )
        assert list(foreign_dir.iterdir()) == [], (
            "Foreign directory must never be written to"
        )
    finally:
        if foreign_dir is not None and foreign_dir.exists():
            try:
                foreign_dir.rmdir()
            except OSError:
                pass
        if displaced_owned_dir is not None and displaced_owned_dir.exists():
            try:
                displaced_owned_dir.rmdir()
            except OSError:
                pass


def test_r8_02_anchor_failure_on_fstat_fail_closed_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 2 (R8-01): Fail-closed anchor failure on fstat prevents yield."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    orig_fstat = os.fstat
    fstat_failed = False
    anchor_yielded = False

    def mock_fstat(fd: int) -> os.stat_result:
        nonlocal fstat_failed
        if not fstat_failed:
            fstat_failed = True
            raise OSError("Injected descriptor anchor fstat failure")
        return orig_fstat(fd)

    def inject_hook(stage: str, s: Path, t: Path | None) -> None:
        if stage == "after_anchor_open_before_fstat":
            monkeypatch.setattr(os, "fstat", mock_fstat)

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotStorageError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src,
                root,
                0.001,
                _sleeper=lambda _: None,
                _fault_hook=inject_hook,
            ):
                anchor_yielded = True

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotStorageError) for e in flat)
        assert anchor_yielded is False, "ANCHOR_FAILURE_YIELDED must be False"
    finally:
        monkeypatch.undo()


def test_r8_03_qpart_foreign_destination_not_overwritten_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 3 (R8-02): Pre-existing foreign file at qpart is NOT overwritten."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    foreign_bytes = b"PRE_EXISTING_FOREIGN_QPART_PAYLOAD_R8"
    foreign_qpart_path: Path | None = None
    foreign_orig_ino: int | None = None

    fixed_uuid = "fixed_test_uuid_qpart_001"

    class FixedUUID:
        hex = fixed_uuid

    def inject_pre_existing_qpart(stage: str, s: Path, t: Path | None) -> None:
        nonlocal foreign_qpart_path, foreign_orig_ino
        if stage == "during_copy_chunk" and t is not None:
            # Pre-occupy target qpart destination in lease_dir
            qpath = t.parent / f".qpart-{fixed_uuid}"
            qpath.write_bytes(foreign_bytes)
            foreign_qpart_path = qpath
            foreign_orig_ino = qpath.lstat().st_ino
            raise OSError("Injected copy failure to trigger part cleanup")

    monkeypatch.setattr(uuid, "uuid4", lambda: FixedUUID())

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotAcquisitionError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src,
                root,
                0.001,
                _sleeper=lambda _: None,
                _fault_hook=inject_pre_existing_qpart,
            ):
                pass

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotCleanupError) for e in flat)

        # R8-04 Invariant: QPART_FOREIGN_OVERWRITTEN must be False
        assert foreign_qpart_path is not None
        assert foreign_qpart_path.exists()
        assert foreign_qpart_path.read_bytes() == foreign_bytes
        if foreign_orig_ino is not None:
            assert foreign_qpart_path.lstat().st_ino == foreign_orig_ino
    finally:
        monkeypatch.undo()
        if foreign_qpart_path is not None and foreign_qpart_path.exists():
            foreign_qpart_path.unlink()
            try:
                foreign_qpart_path.parent.rmdir()
            except OSError:
                pass


def test_r8_04_qfinal_foreign_destination_not_overwritten_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 4 (R8-02): Pre-existing foreign file at qfinal is NOT overwritten."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    foreign_bytes = b"PRE_EXISTING_FOREIGN_QFINAL_PAYLOAD_R8"
    foreign_qfinal_path: Path | None = None
    foreign_orig_ino: int | None = None

    fixed_uuid = "fixed_test_uuid_qfinal_002"

    class FixedUUID:
        hex = fixed_uuid

    def inject_pre_existing_qfinal(stage: str, s: Path, t: Path | None) -> None:
        nonlocal foreign_qfinal_path, foreign_orig_ino
        if stage == "before_lease_reverify":
            qpath = s.parent / f".qfinal-{fixed_uuid}"
            qpath.write_bytes(foreign_bytes)
            foreign_qfinal_path = qpath
            foreign_orig_ino = qpath.lstat().st_ino

    monkeypatch.setattr(uuid, "uuid4", lambda: FixedUUID())

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotCleanupError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src,
                root,
                0.001,
                _sleeper=lambda _: None,
                _fault_hook=inject_pre_existing_qfinal,
            ):
                pass

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotCleanupError) for e in flat)

        # R8-04 Invariant: QFINAL_FOREIGN_OVERWRITTEN must be False
        assert foreign_qfinal_path is not None
        assert foreign_qfinal_path.exists()
        assert foreign_qfinal_path.read_bytes() == foreign_bytes
        if foreign_orig_ino is not None:
            assert foreign_qfinal_path.lstat().st_ino == foreign_orig_ino
    finally:
        monkeypatch.undo()
        if foreign_qfinal_path is not None and foreign_qfinal_path.exists():
            foreign_qfinal_path.unlink()
            try:
                foreign_qfinal_path.parent.rmdir()
            except OSError:
                pass


def test_r8_05_qdir_foreign_destination_not_overwritten_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 5 (R8-02): Pre-existing foreign dir at qdir is NOT overwritten."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    fixed_uuid = "fixed_test_uuid_qdir_003"
    foreign_qdir_path = root / f".qdir-{fixed_uuid}"
    foreign_payload_file = foreign_qdir_path / "foreign_payload.txt"
    foreign_bytes = b"FOREIGN_QDIR_INTERNAL_DATA_R8"

    class FixedUUID:
        hex = fixed_uuid

    def inject_pre_existing_qdir(stage: str, s: Path, t: Path | None) -> None:
        if stage == "before_lease_reverify":
            foreign_qdir_path.mkdir(exist_ok=True)
            foreign_payload_file.write_bytes(foreign_bytes)

    monkeypatch.setattr(uuid, "uuid4", lambda: FixedUUID())

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotCleanupError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src,
                root,
                0.001,
                _sleeper=lambda _: None,
                _fault_hook=inject_pre_existing_qdir,
            ):
                pass

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotCleanupError) for e in flat)

        # R8-04 Invariant: QDIR_FOREIGN_OVERWRITTEN must be False
        assert foreign_qdir_path.exists()
        assert foreign_payload_file.exists()
        assert foreign_payload_file.read_bytes() == foreign_bytes
    finally:
        monkeypatch.undo()
        if foreign_payload_file.exists():
            foreign_payload_file.unlink()
        if foreign_qdir_path.exists():
            try:
                foreign_qdir_path.rmdir()
            except OSError:
                pass


def test_r8_06_restore_foreign_destination_not_overwritten_oracle(
    tmp_path: Path,
) -> None:
    """Test 6 (R8-02): Foreign file at restore path is NOT overwritten."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    foreign_bytes = b"FOREIGN_RESTORE_BLOCKING_PAYLOAD_R8"
    foreign_file_path: Path | None = None

    def inject_inode_reuse_and_swap(stage: str, s: Path, t: Path | None) -> None:
        nonlocal foreign_file_path
        if stage == "before_lease_reverify" and s.exists():
            s.unlink()
            s.write_bytes(foreign_bytes)
            foreign_file_path = s

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotIntegrityError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src,
                root,
                0.001,
                _sleeper=lambda _: None,
                _fault_hook=inject_inode_reuse_and_swap,
            ):
                pass

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotIntegrityError) for e in flat)
        assert any(isinstance(e, XlsxSnapshotCleanupError) for e in flat)

        # R8-04 Invariant: RESTORE_FOREIGN_OVERWRITTEN must be False
        assert foreign_file_path is not None
        assert foreign_file_path.exists()
        assert foreign_file_path.read_bytes() == foreign_bytes
    finally:
        if foreign_file_path is not None and foreign_file_path.exists():
            foreign_file_path.unlink()
            try:
                foreign_file_path.parent.rmdir()
            except OSError:
                pass


def test_r8_07_safe_typed_error_messages_no_path_or_raw_oserror_oracle(
    tmp_path: Path,
) -> None:
    """Test 7 (R8-03): Typed error str/repr contains no paths or raw OSError strings."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    foreign_bytes = b"FOREIGN_ERROR_CHECK_PAYLOAD_R8"

    def inject_error(stage: str, s: Path, t: Path | None) -> None:
        if stage == "before_lease_reverify" and s.exists():
            s.unlink()
            s.write_bytes(foreign_bytes)

    with pytest.raises(
        (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotAcquisitionError)
    ) as exc_info:
        with open_stable_xlsx_snapshot(
            src,
            root,
            0.001,
            _sleeper=lambda _: None,
            _fault_hook=inject_error,
        ):
            pass

    flat = _flatten_exceptions(exc_info.value)
    acq_excs = [e for e in flat if isinstance(e, XlsxSnapshotAcquisitionError)]
    assert len(acq_excs) >= 1

    path_leak_found = False
    raw_oserror_interpolated = False

    str_root = str(root)
    str_src = str(src)

    for exc in acq_excs:
        msg = str(exc)
        rep = repr(exc)
        # Check no absolute path leak in typed error message
        if str_root in msg or str_src in msg or str_root in rep or str_src in rep:
            path_leak_found = True
        # Check no raw OSError interpolation in typed error message
        if "[Errno" in msg or "OSError:" in msg or "WinError" in msg:
            raw_oserror_interpolated = True

    # R8-04 Invariants:
    assert path_leak_found is False, "TYPED_ERROR_ABSOLUTE_PATH_LEAK must be False"
    assert raw_oserror_interpolated is False, (
        "TYPED_ERROR_RAW_OSERROR_INTERPOLATED must be False"
    )


def test_r8_08_candidate_fstat_transient_failure_no_replacement_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 8: Candidate fstat fails once, retry cleans root."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    orig_fstat = os.fstat
    fstat_failed_once = False

    def get_open_fds() -> set[int]:
        if sys.platform.startswith("linux") and os.path.exists("/proc/self/fd"):
            return {int(fd) for fd in os.listdir("/proc/self/fd")}
        return set()

    initial_fds = get_open_fds()

    def mock_fstat(fd: int) -> os.stat_result:
        nonlocal fstat_failed_once
        if not fstat_failed_once and fd not in initial_fds:
            fstat_failed_once = True
            raise OSError("Injected single transient fstat error")
        return orig_fstat(fd)

    monkeypatch.setattr(os, "fstat", mock_fstat)

    # Acquisition succeeds through the retry on open descriptor
    with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None) as snap:
        assert snap.byte_count == len(_build_valid_test_xlsx())

    if initial_fds:
        current_fds = get_open_fds()
        leaked_fds = list(current_fds - initial_fds)
        assert leaked_fds == [], f"Leaked file descriptors found: {leaked_fds}"

    assert list(root.iterdir()) == []


def test_r8_09_candidate_fstat_failure_with_path_replacement_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 9: Candidate fstat fails & path swapped; foreign survives."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    foreign_bytes = b"FOREIGN_CANDIDATE_REPLACEMENT_R8"
    foreign_path: Path | None = None

    orig_fstat = os.fstat

    def get_open_fds() -> set[int]:
        if sys.platform.startswith("linux") and os.path.exists("/proc/self/fd"):
            return {int(fd) for fd in os.listdir("/proc/self/fd")}
        return set()

    initial_fds = get_open_fds()

    def mock_fstat(fd: int) -> os.stat_result:
        nonlocal foreign_path
        if fd not in initial_fds:
            # Overwrite candidate file on disk with foreign replacement
            for item in root.glob("acq-*/snapshot.part"):
                item.unlink()
                item.write_bytes(foreign_bytes)
                foreign_path = item
            raise OSError("Persistent candidate fstat error")
        return orig_fstat(fd)

    monkeypatch.setattr(os, "fstat", mock_fstat)

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotStorageError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None):
                pass

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotStorageError) for e in flat)
        assert any(isinstance(e, XlsxSnapshotCleanupError) for e in flat)

        # Foreign file survives
        assert foreign_path is not None
        assert foreign_path.exists()
        assert foreign_path.read_bytes() == foreign_bytes
    finally:
        if foreign_path is not None and foreign_path.exists():
            foreign_path.unlink()
            try:
                foreign_path.parent.rmdir()
            except OSError:
                pass


def test_r8_10_first_lease_lstat_transient_failure_no_replacement_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 10: First lease lstat fails once, retry cleans root."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    orig_lstat = Path.lstat
    failed_once = False

    def mock_lstat(self: Path) -> os.stat_result:
        nonlocal failed_once
        if not failed_once and self.parent == root and self.name.startswith("acq-"):
            failed_once = True
            raise OSError("Transient early stat failure on lease dir")
        return orig_lstat(self)

    monkeypatch.setattr(Path, "lstat", mock_lstat)

    with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None) as snap:
        assert snap.byte_count == len(_build_valid_test_xlsx())

    assert list(root.iterdir()) == []


def test_r8_11_atomic_promotion_failure_cleans_owned_artifacts_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 11: Promotion failure aborts cleanly and removes candidate."""
    import accounting_local_agent.xlsx_snapshot_acquisition as mod

    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    orig_move = mod._atomic_move_no_replace
    move_failed = False

    def failing_move(s: Path, d: Path) -> None:
        nonlocal move_failed
        if not move_failed and s.name == "snapshot.part" and d.name == "snapshot.xlsx":
            move_failed = True
            raise OSError("Simulated atomic promotion failure")
        orig_move(s, d)

    monkeypatch.setattr(mod, "_atomic_move_no_replace", failing_move)

    with pytest.raises(
        (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotStorageError)
    ) as exc_info:
        with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None):
            pass

    flat = _flatten_exceptions(exc_info.value)
    assert any(isinstance(e, XlsxSnapshotStorageError) for e in flat)
    assert list(root.iterdir()) == []


def test_r8_12_atomic_promotion_with_foreign_final_oracle(
    tmp_path: Path,
) -> None:
    """Test 12: Final pre-exists before promotion; foreign survives."""
    src = tmp_path / "source.xlsx"
    valid_bytes = _build_valid_test_xlsx()
    src.write_bytes(valid_bytes)
    root = tmp_path / "root"
    root.mkdir()

    foreign_bytes = b"FOREIGN_PREEXISTING_FINAL_SNAPSHOT_R8"
    foreign_final_p: Path | None = None

    def inject_foreign_final_before_promotion(
        stage: str, s: Path, t: Path | None
    ) -> None:
        nonlocal foreign_final_p
        if stage == "before_promotion" and t is not None:
            final_p = t.parent / "snapshot.xlsx"
            final_p.write_bytes(foreign_bytes)
            foreign_final_p = final_p

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotStorageError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src,
                root,
                0.001,
                _sleeper=lambda _: None,
                _fault_hook=inject_foreign_final_before_promotion,
            ):
                pass

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotStorageError) for e in flat)
        assert any(isinstance(e, XlsxSnapshotCleanupError) for e in flat)

        # Foreign file MUST survive
        assert foreign_final_p is not None
        assert foreign_final_p.exists()
        assert foreign_final_p.read_bytes() == foreign_bytes
    finally:
        if foreign_final_p is not None and foreign_final_p.exists():
            foreign_final_p.unlink()
            try:
                foreign_final_p.parent.rmdir()
            except OSError:
                pass


def test_r8_13_real_lease_exit_race_between_lstat_and_open_oracle(
    tmp_path: Path,
) -> None:
    """Test 13: Race between lstat and open maps to IntegrityError."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    def race_delete_between_lstat_and_open(stage: str, s: Path, t: Path | None) -> None:
        if stage == "between_lease_lstat_and_open" and s.exists():
            s.unlink()

    with pytest.raises(
        (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotIntegrityError)
    ) as exc_info:
        with open_stable_xlsx_snapshot(
            src,
            root,
            0.001,
            _sleeper=lambda _: None,
            _fault_hook=race_delete_between_lstat_and_open,
        ):
            pass

    flat = _flatten_exceptions(exc_info.value)
    assert any(isinstance(e, XlsxSnapshotIntegrityError) for e in flat)
    assert not any(isinstance(e, XlsxSnapshotStorageError) for e in flat)
    assert list(root.iterdir()) == []


def test_r8_14_simulated_identity_collision_protection_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 14: Simulated identity collision protects foreign file."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    foreign_bytes = b"FOREIGN_INODE_REUSE_PAYLOAD_R8"
    foreign_file_path: Path | None = None
    original_ino: int | None = None
    original_dev: int | None = None

    def inject_inode_reuse(stage: str, s: Path, t: Path | None) -> None:
        nonlocal foreign_file_path, original_ino, original_dev
        if stage == "before_lease_reverify" and s.exists():
            st = s.lstat()
            original_ino = st.st_ino
            original_dev = st.st_dev
            s.unlink()
            s.write_bytes(foreign_bytes)
            foreign_file_path = s

    orig_lstat = Path.lstat

    def mock_lstat(self: Path) -> os.stat_result:
        real_st = orig_lstat(self)
        if (
            self.name == "snapshot.xlsx" or self.name.startswith(".qfinal-")
        ) and original_ino is not None:
            return os.stat_result(
                (
                    real_st.st_mode,
                    original_ino,
                    (original_dev if original_dev is not None else real_st.st_dev),
                    real_st.st_nlink,
                    real_st.st_uid,
                    real_st.st_gid,
                    real_st.st_size,
                    real_st.st_atime,
                    real_st.st_mtime,
                    real_st.st_ctime,
                )
            )
        return real_st

    monkeypatch.setattr(Path, "lstat", mock_lstat)

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotIntegrityError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src,
                root,
                0.001,
                _sleeper=lambda _: None,
                _fault_hook=inject_inode_reuse,
            ):
                pass

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotIntegrityError) for e in flat)
        assert any(isinstance(e, XlsxSnapshotCleanupError) for e in flat)

        # Foreign file survives
        assert foreign_file_path is not None
        assert foreign_file_path.exists()
        assert foreign_file_path.read_bytes() == foreign_bytes
    finally:
        if foreign_file_path is not None and foreign_file_path.exists():
            foreign_file_path.unlink()
            try:
                foreign_file_path.parent.rmdir()
            except OSError:
                pass


def test_r8_15_real_mtime_zero_integrity_oracle(tmp_path: Path) -> None:
    """Test 15: Candidate mtime_ns = 0 in context verified and detected."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    def set_candidate_mtime_zero(stage: str, s: Path, t: Path | None) -> None:
        if stage == "before_promotion" and t is not None:
            os.utime(t, ns=(0, 0))

    with pytest.raises(XlsxSnapshotIntegrityError, match="mtime was modified"):
        with open_stable_xlsx_snapshot(
            src,
            root,
            0.001,
            _sleeper=lambda _: None,
            _fault_hook=set_candidate_mtime_zero,
        ) as snap:
            assert snap.snapshot_path.stat().st_mtime_ns == 0
            os.utime(snap.snapshot_path, ns=(1_000_000_000, 1_000_000_000))

    assert list(root.iterdir()) == []


def test_r8_16_real_identity_unavailable_provider_fallback_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 16: Provider returns (None, None) and executes cleanly."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    import accounting_local_agent.xlsx_snapshot_acquisition as mod

    def mock_extract(st: os.stat_result) -> tuple[int | None, int | None]:
        return None, None

    monkeypatch.setattr(mod, "_extract_device_and_inode", mock_extract)

    with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None) as snap:
        assert snap.byte_count == len(_build_valid_test_xlsx())
        read_res = read_xlsx_source_snapshot(snap.snapshot_path)
        assert len(read_res.snapshot.sheets) == 4

    assert list(root.iterdir()) == []


def test_r9_01_link_unlink_api_not_used_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R9-02 Oracle: os.link is NOT used anywhere during snapshot lifecycle."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    link_called = False

    def failing_link(s: Any, d: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal link_called
        link_called = True
        raise AssertionError("os.link must not be called under R9-02")

    monkeypatch.setattr(os, "link", failing_link)

    with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None) as snap:
        assert snap.byte_count == len(_build_valid_test_xlsx())

    # Oracle invariant: LINK_UNLINK_API_USED = False
    assert link_called is False, "LINK_UNLINK_API_USED must be False"
    assert list(root.iterdir()) == []


def test_r9_02_quarantine_foreign_source_survived_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R9-02 Oracle: Foreign source at quarantine destination is preserved."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    fixed_uuid = "fixed_uuid_qpart_r9"
    foreign_bytes = b"FOREIGN_QPART_SOURCE_DATA_R9"
    foreign_qpart: Path | None = None

    class FixedUUID:
        hex = fixed_uuid

    def inject_qpart_destination(stage: str, s: Path, t: Path | None) -> None:
        nonlocal foreign_qpart
        if stage == "before_copy_open" and t is not None:
            foreign_qpart = t.parent / f".qpart-{fixed_uuid}"
            foreign_qpart.write_bytes(foreign_bytes)

    monkeypatch.setattr(uuid, "uuid4", lambda: FixedUUID())

    try:

        def fail_during_copy(stage: str, s: Path, t: Path | None) -> None:
            inject_qpart_destination(stage, s, t)
            if stage == "before_write":
                raise OSError("Simulated write failure to trigger part cleanup")

        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotStorageError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src,
                root,
                0.001,
                _sleeper=lambda _: None,
                _fault_hook=fail_during_copy,
            ):
                pass

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotCleanupError) for e in flat)

        # Oracle invariant: QUARANTINE_FOREIGN_SOURCE_SURVIVED = True
        assert foreign_qpart is not None
        assert foreign_qpart.exists()
        assert foreign_qpart.read_bytes() == foreign_bytes
    finally:
        monkeypatch.undo()
        if foreign_qpart is not None and foreign_qpart.exists():
            foreign_qpart.unlink()
            try:
                foreign_qpart.parent.rmdir()
            except OSError:
                pass


def test_r9_03_promotion_foreign_part_survived_and_displaced_owned_survived_oracle(
    tmp_path: Path,
) -> None:
    """R9-02 Oracle: Foreign final file & displaced owned part survive promotion."""
    import accounting_local_agent.xlsx_snapshot_acquisition as mod

    src = tmp_path / "source.xlsx"
    valid_bytes = _build_valid_test_xlsx()
    src.write_bytes(valid_bytes)
    root = tmp_path / "root"
    root.mkdir()

    # 1. Test direct promotion primitive fail-if-exists semantics
    direct_dir = tmp_path / "direct_promo"
    direct_dir.mkdir()
    owned_cand = direct_dir / "snapshot.part"
    owned_cand.write_bytes(b"OWNED_CANDIDATE_DATA_R9")
    foreign_final_direct = direct_dir / "snapshot.xlsx"
    foreign_final_direct.write_bytes(b"FOREIGN_FINAL_DIRECT_R9")

    promo_failed = False
    try:
        mod._promote_candidate_atomic_fail_if_exists(
            owned_cand, foreign_final_direct, None
        )
    except XlsxSnapshotStorageError:
        promo_failed = True

    assert promo_failed is True
    assert foreign_final_direct.read_bytes() == b"FOREIGN_FINAL_DIRECT_R9", (
        "PROMOTION_FOREIGN_PART_SURVIVED must be True"
    )
    assert owned_cand.read_bytes() == b"OWNED_CANDIDATE_DATA_R9", (
        "PROMOTION_DISPLACED_OWNED_SURVIVED must be True"
    )

    # 2. Test full context lifecycle with foreign final collision
    foreign_bytes = b"FOREIGN_FINAL_PREEXISTING_R9"
    foreign_final: Path | None = None

    def inject_foreign_final(stage: str, s: Path, t: Path | None) -> None:
        nonlocal foreign_final
        if stage == "before_promotion" and t is not None:
            final_p = t.parent / "snapshot.xlsx"
            final_p.write_bytes(foreign_bytes)
            foreign_final = final_p

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotStorageError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src,
                root,
                0.001,
                _sleeper=lambda _: None,
                _fault_hook=inject_foreign_final,
            ):
                pass

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotStorageError) for e in flat)
        assert any(isinstance(e, XlsxSnapshotCleanupError) for e in flat)

        # Oracle invariant: PROMOTION_FOREIGN_PART_SURVIVED = True
        assert foreign_final is not None
        assert foreign_final.exists()
        assert foreign_final.read_bytes() == foreign_bytes
    finally:
        if foreign_final is not None and foreign_final.exists():
            foreign_final.unlink()
            for qp in foreign_final.parent.glob(".qpart-*"):
                qp.unlink()
            try:
                foreign_final.parent.rmdir()
            except OSError:
                pass


def test_r9_04_rename_noreplace_unavailable_fails_closed_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R9-02 Oracle: renameat2 unavailable/ENOSYS fails closed with no fallback."""
    import accounting_local_agent.xlsx_snapshot_acquisition as mod

    foreign_dest = tmp_path / "foreign_dest.txt"
    foreign_dest.write_bytes(b"FOREIGN_NO_OVERWRITE_R9")

    test_src = tmp_path / "test_src.txt"
    test_src.write_bytes(b"TEST_SRC_DATA_R9")

    def mock_renameat2_unsupported(*args: Any, **kwargs: Any) -> int:
        ctypes.set_errno(38)  # ENOSYS
        return -1

    class MockLibC:
        renameat2 = mock_renameat2_unsupported

    if sys.platform.startswith("linux"):
        monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: MockLibC())

    failed_closed = False
    try:
        mod._atomic_move_no_replace(test_src, foreign_dest)
    except (OSError, FileExistsError):
        failed_closed = True

    # Oracle invariants:
    assert failed_closed is True, (
        "RENAME_NOREPLACE_UNAVAILABLE_FAILED_CLOSED must be True"
    )
    assert foreign_dest.read_bytes() == b"FOREIGN_NO_OVERWRITE_R9", (
        "RENAME_FALLBACK_FOREIGN_OVERWRITTEN must be False"
    )


def test_r9_05_empty_foreign_qdir_survived_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R9-02 Oracle: Pre-existing EMPTY foreign directory at qdir is preserved."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    fixed_uuid = "fixed_empty_qdir_uuid_r9"
    foreign_empty_qdir = root / f".qdir-{fixed_uuid}"

    class FixedUUID:
        hex = fixed_uuid

    def inject_empty_qdir(stage: str, s: Path, t: Path | None) -> None:
        if stage == "before_lease_reverify":
            foreign_empty_qdir.mkdir(exist_ok=True)

    monkeypatch.setattr(uuid, "uuid4", lambda: FixedUUID())

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotCleanupError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src,
                root,
                0.001,
                _sleeper=lambda _: None,
                _fault_hook=inject_empty_qdir,
            ):
                pass

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotCleanupError) for e in flat)

        # Oracle invariant: EMPTY_FOREIGN_QDIR_SURVIVED = True
        assert foreign_empty_qdir.exists()
        assert foreign_empty_qdir.is_dir()
    finally:
        monkeypatch.undo()
        if foreign_empty_qdir.exists():
            try:
                foreign_empty_qdir.rmdir()
            except OSError:
                pass


def test_r9_06_qpart_postverify_after_hook_replaces_oracle(
    tmp_path: Path,
) -> None:
    """R9-03: qpart swapped in hook is not deleted; displaced owned preserved."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    foreign_bytes = b"FOREIGN_QPART_SWAP_PAYLOAD_R9"
    foreign_qpart_path: Path | None = None
    displaced_owned_p: Path | None = None

    def swap_qpart_in_hook(stage: str, s: Path, t: Path | None) -> None:
        nonlocal foreign_qpart_path, displaced_owned_p
        if stage == "inside_part_unlink" and t is not None:
            displaced = t.parent / f"displaced_{t.name}"
            t.rename(displaced)
            displaced_owned_p = displaced
            t.write_bytes(foreign_bytes)
            foreign_qpart_path = t

    def trigger_part_cleanup(stage: str, s: Path, t: Path | None) -> None:
        if stage == "before_write":
            raise OSError("Trigger cleanup of part file")
        swap_qpart_in_hook(stage, s, t)

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotStorageError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src,
                root,
                0.001,
                _sleeper=lambda _: None,
                _fault_hook=trigger_part_cleanup,
            ):
                pass

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotCleanupError) for e in flat)

        # Oracle invariants:
        assert foreign_qpart_path is not None
        assert foreign_qpart_path.exists()
        assert foreign_qpart_path.read_bytes() == foreign_bytes, (
            "QPART_POSTVERIFY_FOREIGN_SURVIVED must be True"
        )
        assert displaced_owned_p is not None
        assert displaced_owned_p.exists(), (
            "QPART_POSTVERIFY_DISPLACED_OWNED_SURVIVED must be True"
        )
    finally:
        if foreign_qpart_path is not None and foreign_qpart_path.exists():
            foreign_qpart_path.unlink()
        if displaced_owned_p is not None and displaced_owned_p.exists():
            displaced_owned_p.unlink()
        for d in root.glob("acq-*"):
            try:
                d.rmdir()
            except OSError:
                pass


def test_r9_07_qfinal_postverify_after_hook_replaces_oracle(
    tmp_path: Path,
) -> None:
    """R9-03: qfinal swapped in hook is not deleted; displaced owned preserved."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    foreign_bytes = b"FOREIGN_QFINAL_SWAP_PAYLOAD_R9"
    foreign_qfinal_path: Path | None = None
    displaced_owned_p: Path | None = None

    def swap_qfinal_in_hook(stage: str, s: Path, t: Path | None) -> None:
        nonlocal foreign_qfinal_path, displaced_owned_p
        if stage == "inside_final_unlink" and t is not None:
            displaced = t.parent / f"displaced_{t.name}"
            t.rename(displaced)
            displaced_owned_p = displaced
            t.write_bytes(foreign_bytes)
            foreign_qfinal_path = t

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotCleanupError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src,
                root,
                0.001,
                _sleeper=lambda _: None,
                _fault_hook=swap_qfinal_in_hook,
            ):
                pass

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotCleanupError) for e in flat)

        # Oracle invariants:
        assert foreign_qfinal_path is not None
        assert foreign_qfinal_path.exists()
        assert foreign_qfinal_path.read_bytes() == foreign_bytes, (
            "QFINAL_POSTVERIFY_FOREIGN_SURVIVED must be True"
        )
        assert displaced_owned_p is not None
        assert displaced_owned_p.exists(), (
            "QFINAL_POSTVERIFY_DISPLACED_OWNED_SURVIVED must be True"
        )
    finally:
        if foreign_qfinal_path is not None and foreign_qfinal_path.exists():
            foreign_qfinal_path.unlink()
        if displaced_owned_p is not None and displaced_owned_p.exists():
            displaced_owned_p.unlink()
        for d in root.glob("acq-*"):
            try:
                d.rmdir()
            except OSError:
                pass


def test_r9_08_qdir_postverify_after_hook_replaces_oracle(
    tmp_path: Path,
) -> None:
    """R9-03 Oracle: qdir replaced in hook is not rmdir'd; displaced owned preserved."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    foreign_payload_bytes = b"FOREIGN_QDIR_INTERNAL_DATA_R9"
    foreign_qdir_path: Path | None = None
    displaced_owned_p: Path | None = None

    def swap_qdir_in_hook(stage: str, s: Path, t: Path | None) -> None:
        nonlocal foreign_qdir_path, displaced_owned_p
        if stage == "inside_lease_rmdir" and t is not None:
            displaced = t.parent / f"displaced_{t.name}"
            t.rename(displaced)
            displaced_owned_p = displaced
            t.mkdir()
            (t / "foreign_file.txt").write_bytes(foreign_payload_bytes)
            foreign_qdir_path = t

    try:
        with pytest.raises(
            (ExceptionGroup, BaseExceptionGroup, XlsxSnapshotCleanupError)
        ) as exc_info:
            with open_stable_xlsx_snapshot(
                src,
                root,
                0.001,
                _sleeper=lambda _: None,
                _fault_hook=swap_qdir_in_hook,
            ):
                pass

        flat = _flatten_exceptions(exc_info.value)
        assert any(isinstance(e, XlsxSnapshotCleanupError) for e in flat)

        # Oracle invariants:
        assert foreign_qdir_path is not None
        assert foreign_qdir_path.exists()
        assert (foreign_qdir_path / "foreign_file.txt").exists(), (
            "QDIR_POSTVERIFY_FOREIGN_SURVIVED must be True"
        )
        assert displaced_owned_p is not None
        assert displaced_owned_p.exists(), (
            "QDIR_POSTVERIFY_DISPLACED_OWNED_SURVIVED must be True"
        )
    finally:
        if foreign_qdir_path is not None and foreign_qdir_path.exists():
            for f in foreign_qdir_path.iterdir():
                f.unlink()
            try:
                foreign_qdir_path.rmdir()
            except OSError:
                pass
        if displaced_owned_p is not None and displaced_owned_p.exists():
            try:
                displaced_owned_p.rmdir()
            except OSError:
                pass


def test_r9_09_windows_handle_anchor_creation_and_fail_closed_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R9-01 Oracle: Windows handle anchor fails closed on CreateFileW/GetInfo error."""
    import accounting_local_agent.xlsx_snapshot_acquisition as mod

    lease_dir = tmp_path / "test_lease_win"

    class MockWintypes:
        LPCWSTR = ctypes.c_wchar_p
        DWORD = ctypes.c_uint32
        LPVOID = ctypes.c_void_p
        HANDLE = ctypes.c_void_p
        BOOL = ctypes.c_int
        FILETIME = ctypes.c_uint64

    class MockFunc:
        def __init__(self, fn: Any) -> None:
            self.fn = fn
            self.argtypes: Any = None
            self.restype: Any = None

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            return self.fn(*args, **kwargs)

    class MockKernel32FailCreate:
        def __init__(self) -> None:
            self.CreateFileW = MockFunc(lambda *args: -1)
            self.GetFileInformationByHandle = MockFunc(lambda *args: True)
            self.CloseHandle = MockFunc(lambda *args: True)

    class MockCtypesWin:
        wintypes = MockWintypes()

        def WinDLL(self, *args: Any, **kwargs: Any) -> Any:
            return MockKernel32FailCreate()

        def get_last_error(self) -> int:
            return 5

    monkeypatch.setattr(mod, "_ctypes_provider", MockCtypesWin())

    with pytest.raises(OSError, match="CreateFileW failed"):
        mod._create_and_anchor_lease_dir_windows(lease_dir)

    class MockKernel32FailGetInfo:
        def __init__(self) -> None:
            self.closed = False
            self.CreateFileW = MockFunc(lambda *args: 12345)
            self.GetFileInformationByHandle = MockFunc(lambda *args: False)
            self.CloseHandle = MockFunc(self._close)

        def _close(self, *args: Any) -> bool:
            self.closed = True
            return True

    mock_k32_info = MockKernel32FailGetInfo()

    class MockCtypesWinInfo:
        wintypes = MockWintypes()

        def WinDLL(self, *args: Any, **kwargs: Any) -> Any:
            return mock_k32_info

        def get_last_error(self) -> int:
            return 6

    monkeypatch.setattr(mod, "_ctypes_provider", MockCtypesWinInfo())

    lease_dir_2 = tmp_path / "test_lease_win_2"
    with pytest.raises(OSError, match="GetFileInformationByHandle failed"):
        mod._create_and_anchor_lease_dir_windows(lease_dir_2)

    assert mock_k32_info.closed is True


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows platform-conditional test only runs on Windows runtime",
)
def test_r9_10_windows_runtime_full_lifecycle_platform_conditional(
    tmp_path: Path,
) -> None:
    """R9-01 Oracle: Real Windows runtime full acquisition, yield, and cleanup."""
    src = tmp_path / "source.xlsx"
    valid_bytes = _build_valid_test_xlsx()
    src.write_bytes(valid_bytes)
    root = tmp_path / "root"
    root.mkdir()

    initial_src_stat = src.stat()

    with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None) as snap:
        assert snap.byte_count == len(valid_bytes)
        assert snap.snapshot_path.exists()
        read_res = read_xlsx_source_snapshot(snap.snapshot_path)
        assert len(read_res.snapshot.sheets) == 4

    assert list(root.iterdir()) == []

    final_src_stat = src.stat()
    assert src.read_bytes() == valid_bytes
    assert final_src_stat.st_size == initial_src_stat.st_size
