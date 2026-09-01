"""Tests for WP-06 Stable XLSX Snapshot Acquisition and Cleanup (SA-01 to SA-14)."""

from __future__ import annotations

import dataclasses
import hashlib
import io
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from typing import Any

import pytest
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
)
from accounting_contracts.source_change_plan import (
    PlanAction,
    build_prior_identity_registry,
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
    """SA-02: Rejects invalid paths, non-xlsx extensions, bad roots and intervals."""
    root = tmp_path / "root"
    root.mkdir()
    valid_src = tmp_path / "source.xlsx"
    valid_src.write_bytes(_build_valid_test_xlsx())

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

    # Invalid source path extension
    for bad_ext in ["source.xls", "source.csv", "source.txt", "source"]:
        bad_p = tmp_path / bad_ext
        bad_p.write_bytes(b"data")
        with pytest.raises(XlsxSourcePolicyError, match="must have .xlsx extension"):
            with open_stable_xlsx_snapshot(bad_p, root, 0.01):
                pass

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


# ============================================================================
# SA-03: Source bytes, hash, size, mtime, permissions remain unchanged
# ============================================================================


def test_sa03_source_immutability_on_success_and_failures(
    tmp_path: Path,
) -> None:
    """SA-03: Source file is strictly read-only across all outcomes."""
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

    with pytest.raises(XlsxSnapshotIntegrityError):
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
        # Mutate file during observation interval
        src.write_bytes(b"new content during sleep")

    with pytest.raises(XlsxSourceNotReadyError) as exc_info2:
        with open_stable_xlsx_snapshot(src, root, 0.01, _sleeper=modifying_sleeper):
            pass
    assert exc_info2.value.retryable is True


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
    """SA-05: Source mutation or atomic replacement at any stage aborts acquisition."""
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

    # Verify no leaked lease directory
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


# ============================================================================
# SA-07: Invalid ZIP/container, storage, and promotion faults are typed
# ============================================================================


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
            # Delete partial candidate before promotion to cause rename failure
            t.unlink()

    with pytest.raises(XlsxSnapshotStorageError, match="promotion"):
        with open_stable_xlsx_snapshot(
            valid_src,
            root,
            0.001,
            _sleeper=lambda _: None,
            _fault_hook=block_promotion,
        ):
            pass
    assert list(root.iterdir()) == []


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


# ============================================================================
# SA-09: Lease integrity and cleanup lifecycle
# ============================================================================


def test_sa09_lease_mutation_and_deletion_detection(tmp_path: Path) -> None:
    """SA-09: Modification or deletion of snapshot during lease raises error."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    # 1. Mutated during lease
    with pytest.raises(XlsxSnapshotIntegrityError, match="modified during lease"):
        with open_stable_xlsx_snapshot(
            src, root, 0.001, _sleeper=lambda _: None
        ) as snap:
            snap.snapshot_path.write_bytes(b"corrupted snapshot during lease")

    assert list(root.iterdir()) == []

    # 2. Deleted during lease
    with pytest.raises(XlsxSnapshotIntegrityError, match="disappeared or was replaced"):
        with open_stable_xlsx_snapshot(
            src, root, 0.001, _sleeper=lambda _: None
        ) as snap:
            snap.snapshot_path.unlink()

    assert list(root.iterdir()) == []


# ============================================================================
# SA-10: Cleanup failure visibility and exception chaining
# ============================================================================


def test_sa10_cleanup_failure_and_exception_chaining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SA-10: Cleanup failures raise error with exception chaining."""
    src = tmp_path / "source.xlsx"
    src.write_bytes(_build_valid_test_xlsx())
    root = tmp_path / "root"
    root.mkdir()

    orig_rmdir = Path.rmdir

    def failing_rmdir(self: Path) -> None:
        if self.name.startswith("acq-"):
            raise OSError("simulated rmdir lock error")
        orig_rmdir(self)

    monkeypatch.setattr(Path, "rmdir", failing_rmdir)

    # Case A: Consumer succeeds but cleanup fails
    with pytest.raises(XlsxSnapshotCleanupError, match="Failed to remove lease"):
        with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None):
            pass

    # Case B: Consumer fails AND cleanup fails -> secondary chained to primary
    with pytest.raises(XlsxSnapshotCleanupError) as exc_info:
        with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None):
            raise ValueError("consumer business logic crash")

    assert isinstance(exc_info.value.__cause__, ValueError)
    assert str(exc_info.value.__cause__) == "consumer business logic crash"


# ============================================================================
# SA-11: Concurrent disjoint acquisitions
# ============================================================================


def test_sa11_concurrent_disjoint_acquisitions(tmp_path: Path) -> None:
    """SA-11: Concurrent acquisitions use disjoint paths and do not collide."""
    src1 = tmp_path / "source1.xlsx"
    src2 = tmp_path / "source2.xlsx"
    data = _build_valid_test_xlsx()
    src1.write_bytes(data)
    src2.write_bytes(data)
    root = tmp_path / "root"
    root.mkdir()

    barrier = threading.Barrier(2)
    results: list[dict[str, Any]] = []

    def worker(src_p: Path) -> None:
        with open_stable_xlsx_snapshot(
            src_p, root, 0.001, _sleeper=lambda _: None
        ) as snap:
            barrier.wait()
            # Verify snapshot is valid and readable
            res = read_xlsx_source_snapshot(snap.snapshot_path)
            results.append(
                {
                    "path": snap.snapshot_path,
                    "sha": snap.file_sha256,
                    "res": res,
                }
            )

    t1 = threading.Thread(target=worker, args=(src1,))
    t2 = threading.Thread(target=worker, args=(src2,))
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert len(results) == 2
    assert results[0]["path"] != results[1]["path"]
    assert results[0]["sha"] == results[1]["sha"]  # equal content -> equal sha
    assert list(root.iterdir()) == []  # all cleaned up


# ============================================================================
# SA-12: Full four-sheet workbook passes acquisition and matches WP-04 oracle
# ============================================================================


def test_sa12_end_to_end_reader_and_planner_oracle(tmp_path: Path) -> None:
    """SA-12: Complete 4-sheet workbook passes acquisition -> reader -> change plan."""
    src = tmp_path / "full_book.xlsx"
    data = _build_valid_test_xlsx()
    src.write_bytes(data)
    root = tmp_path / "root"
    root.mkdir()

    with open_stable_xlsx_snapshot(src, root, 0.001, _sleeper=lambda _: None) as snap:
        read_res = read_xlsx_source_snapshot(snap.snapshot_path)
        assert len(read_res.snapshot.sheets) == 4
        assert len(read_res.locations_by_uuid) == 4

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
            # Simulate writer truncating or replacing file mid-flight
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
