"""Stable XLSX snapshot acquisition and controlled cleanup (WP-06).

Implements the bounded adapter that converts a caller-supplied, exact operational
.xlsx path into a verified, immutable-for-the-lease temporary snapshot.
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import hashlib
import math
import os
import re
import time
import uuid
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

__all__ = [
    "DEFAULT_COPY_CHUNK_SIZE",
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
]

XLSX_SNAPSHOT_ACQUISITION_VERSION: str = "xlsx-snapshot-acquisition.v1"
DEFAULT_COPY_CHUNK_SIZE: int = 64 * 1024  # 64 KiB
_SHA256_HEX_REGEX = re.compile(r"^[0-9a-f]{64}$")


class XlsxSnapshotAcquisitionReason(enum.StrEnum):
    """Machine-readable reasons for snapshot acquisition errors."""

    SOURCE_NOT_READY = "source_not_ready"
    SOURCE_POLICY_VIOLATION = "source_policy_violation"
    SNAPSHOT_STORAGE_FAILURE = "snapshot_storage_failure"
    SNAPSHOT_INTEGRITY_FAILURE = "snapshot_integrity_failure"
    SNAPSHOT_CLEANUP_FAILURE = "snapshot_cleanup_failure"


class XlsxSnapshotAcquisitionError(Exception):
    """Base exception for all XLSX snapshot acquisition and cleanup failures."""

    def __init__(
        self,
        reason: XlsxSnapshotAcquisitionReason | str,
        message: str = "",
        *,
        retryable: bool | None = None,
    ) -> None:
        try:
            enum_reason = XlsxSnapshotAcquisitionReason(reason)
        except ValueError as exc:
            raise ValueError(f"Invalid acquisition reason: {reason!r}") from exc

        expected_retryable = (
            enum_reason == XlsxSnapshotAcquisitionReason.SOURCE_NOT_READY
        )
        if retryable is not None and retryable != expected_retryable:
            raise ValueError(
                f"Inconsistent retryable={retryable} for reason={enum_reason.value}"
            )

        self.reason: XlsxSnapshotAcquisitionReason = enum_reason
        self.retryable: bool = expected_retryable
        self._message: str = message
        super().__init__(self._format_safe_message())

    def _format_safe_message(self) -> str:
        if self._message:
            return f"[{self.reason.value}] {self._message}"
        return f"[{self.reason.value}]"

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(reason={self.reason.value!r}, "
            f"retryable={self.retryable})"
        )


class XlsxSourceNotReadyError(XlsxSnapshotAcquisitionError):
    """Source file is missing, locked, changing, or not a valid container."""

    def __init__(self, message: str = "", *, retryable: bool | None = None) -> None:
        super().__init__(
            reason=XlsxSnapshotAcquisitionReason.SOURCE_NOT_READY,
            message=message,
            retryable=retryable,
        )


class XlsxSourcePolicyError(XlsxSnapshotAcquisitionError):
    """Source path or acquisition policy violates contract (non-retryable)."""

    def __init__(self, message: str = "", *, retryable: bool | None = None) -> None:
        super().__init__(
            reason=XlsxSnapshotAcquisitionReason.SOURCE_POLICY_VIOLATION,
            message=message,
            retryable=retryable,
        )


class XlsxSnapshotStorageError(XlsxSnapshotAcquisitionError):
    """Snapshot root, directory creation, write, or promotion failed."""

    def __init__(self, message: str = "", *, retryable: bool | None = None) -> None:
        super().__init__(
            reason=XlsxSnapshotAcquisitionReason.SNAPSHOT_STORAGE_FAILURE,
            message=message,
            retryable=retryable,
        )


class XlsxSnapshotIntegrityError(XlsxSnapshotAcquisitionError):
    """Candidate, source, or leased snapshot digest or byte mismatch."""

    def __init__(self, message: str = "", *, retryable: bool | None = None) -> None:
        super().__init__(
            reason=XlsxSnapshotAcquisitionReason.SNAPSHOT_INTEGRITY_FAILURE,
            message=message,
            retryable=retryable,
        )


class XlsxSnapshotCleanupError(XlsxSnapshotAcquisitionError):
    """Managed snapshot file or lease directory could not be removed (non-retryable)."""

    def __init__(self, message: str = "", *, retryable: bool | None = None) -> None:
        super().__init__(
            reason=XlsxSnapshotAcquisitionReason.SNAPSHOT_CLEANUP_FAILURE,
            message=message,
            retryable=retryable,
        )


@dataclasses.dataclass(frozen=True, slots=True)
class StableXlsxSnapshot:
    """Immutable metadata descriptor for a verified, leased temporary snapshot.

    Lease ownership and deletion remain internal to the context manager.
    """

    version: str
    snapshot_path: Path
    file_sha256: str
    byte_count: int
    source_mtime_ns: int

    def __post_init__(self) -> None:
        if self.version != XLSX_SNAPSHOT_ACQUISITION_VERSION:
            raise ValueError(
                f"Invalid acquisition version: {self.version!r}, "
                f"expected {XLSX_SNAPSHOT_ACQUISITION_VERSION!r}"
            )
        if not isinstance(self.snapshot_path, Path):
            raise TypeError(
                f"snapshot_path must be a Path instance, got {type(self.snapshot_path)}"
            )
        if not self.snapshot_path.is_absolute():
            raise ValueError(
                f"snapshot_path must be an absolute path, got {self.snapshot_path}"
            )
        if not self.snapshot_path.name.lower().endswith(".xlsx"):
            raise ValueError(
                "snapshot_path must have .xlsx extension, "
                f"got {self.snapshot_path.name}"
            )
        if not isinstance(self.file_sha256, str) or not _SHA256_HEX_REGEX.fullmatch(
            self.file_sha256
        ):
            raise ValueError(
                "file_sha256 must be a lowercase 64-hex SHA-256 string, "
                f"got {self.file_sha256!r}"
            )
        if (
            isinstance(self.byte_count, bool)
            or not isinstance(self.byte_count, int)
            or self.byte_count < 0
        ):
            raise ValueError(
                f"byte_count must be a nonnegative integer, got {self.byte_count!r}"
            )
        if (
            isinstance(self.source_mtime_ns, bool)
            or not isinstance(self.source_mtime_ns, int)
            or self.source_mtime_ns < 0
        ):
            raise ValueError(
                "source_mtime_ns must be a nonnegative integer, "
                f"got {self.source_mtime_ns!r}"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class _SourceObservation:
    """Internal snapshot of file metadata during observation."""

    size: int
    mtime_ns: int
    device: int
    inode: int


def _get_path_observation(path: Path) -> _SourceObservation:
    try:
        st = path.stat()
    except (FileNotFoundError, PermissionError) as exc:
        raise XlsxSourceNotReadyError(
            "Source file not accessible during observation"
        ) from exc
    except OSError as exc:
        raise XlsxSourceNotReadyError(
            "Source file stat failed during observation"
        ) from exc

    return _SourceObservation(
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
        device=st.st_dev,
        inode=st.st_ino,
    )


def _check_fd_observation(fd: int, expected: _SourceObservation) -> None:
    try:
        st = os.fstat(fd)
    except OSError as exc:
        raise XlsxSourceNotReadyError("Source handle fstat failed") from exc

    if (
        st.st_size != expected.size
        or st.st_mtime_ns != expected.mtime_ns
        or st.st_dev != expected.device
        or st.st_ino != expected.inode
    ):
        raise XlsxSourceNotReadyError(
            "Source file handle metadata modified or replaced"
        )


def _stream_hash_file(
    path: Path,
    chunk_size: int,
    *,
    expected_observation: _SourceObservation | None = None,
    fault_hook: Callable[[str, Path, Path | None], None] | None = None,
    fault_stage: str = "",
    target_path: Path | None = None,
) -> tuple[str, int]:
    hasher = hashlib.sha256()
    total_bytes = 0

    try:
        with open(path, "rb") as f:
            if expected_observation is not None:
                _check_fd_observation(f.fileno(), expected_observation)

            while True:
                if fault_hook is not None and fault_stage:
                    fault_hook(fault_stage, path, target_path)
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
                total_bytes += len(chunk)

            if expected_observation is not None:
                _check_fd_observation(f.fileno(), expected_observation)
    except (FileNotFoundError, PermissionError) as exc:
        raise XlsxSourceNotReadyError("Source file not accessible during read") from exc
    except OSError as exc:
        raise XlsxSourceNotReadyError("Source file read failed") from exc

    return hasher.hexdigest().lower(), total_bytes


def _validate_zip_container(path: Path) -> None:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            name_list = zf.namelist()
            if "[Content_Types].xml" not in name_list:
                raise XlsxSourceNotReadyError(
                    "Snapshot candidate is missing [Content_Types].xml package marker"
                )
            bad_file = zf.testzip()
            if bad_file is not None:
                raise XlsxSourceNotReadyError(
                    f"Corrupted member in ZIP archive: {bad_file}"
                )
    except zipfile.BadZipFile as exc:
        raise XlsxSourceNotReadyError(
            "Snapshot candidate is not a valid ZIP container"
        ) from exc
    except (FileNotFoundError, PermissionError) as exc:
        raise XlsxSourceNotReadyError(
            "Snapshot candidate not accessible for ZIP validation"
        ) from exc
    except OSError as exc:
        raise XlsxSourceNotReadyError("Snapshot candidate ZIP read failed") from exc


@contextlib.contextmanager
def open_stable_xlsx_snapshot(
    source_path: Path | str,
    snapshot_root: Path | str,
    observation_interval_seconds: float,
    *,
    _sleeper: Callable[[float], None] | None = None,
    _copy_chunk_size: int = DEFAULT_COPY_CHUNK_SIZE,
    _fault_hook: Callable[[str, Path, Path | None], None] | None = None,
) -> Iterator[StableXlsxSnapshot]:
    """Acquire a verified, stable, temporary snapshot of an XLSX source workbook.

    Yields an immutable `StableXlsxSnapshot` for the duration of the context.
    On context exit (normal return or exception), verifies lease integrity and cleans
    up the temporary lease directory.
    """
    if isinstance(source_path, (str, Path)):
        src = Path(source_path)
    else:
        raise XlsxSourcePolicyError(
            f"source_path must be Path or str, got {type(source_path)}"
        )

    if not src.name.lower().endswith(".xlsx"):
        raise XlsxSourcePolicyError(
            f"source_path must have .xlsx extension, got {src.name!r}"
        )

    if isinstance(snapshot_root, (str, Path)):
        root = Path(snapshot_root)
    else:
        raise XlsxSnapshotStorageError(
            f"snapshot_root must be Path or str, got {type(snapshot_root)}"
        )

    if not root.exists() or not root.is_dir():
        raise XlsxSnapshotStorageError(
            f"snapshot_root does not exist or is not a directory: {root}"
        )

    if isinstance(observation_interval_seconds, bool) or not isinstance(
        observation_interval_seconds, (int, float)
    ):
        raise XlsxSourcePolicyError(
            "observation_interval_seconds must be a positive float, "
            f"got {type(observation_interval_seconds)}"
        )

    if (
        not math.isfinite(observation_interval_seconds)
        or observation_interval_seconds <= 0.0
    ):
        raise XlsxSourcePolicyError(
            "observation_interval_seconds must be a finite positive number, "
            f"got {observation_interval_seconds}"
        )

    if _copy_chunk_size <= 0:
        raise XlsxSnapshotStorageError("Chunk size must be a positive integer")

    sleeper = _sleeper if _sleeper is not None else time.sleep

    if not src.exists():
        raise XlsxSourceNotReadyError("Source file does not exist")
    if src.is_dir():
        raise XlsxSourcePolicyError("Source path points to a directory")

    if _fault_hook is not None:
        _fault_hook("before_observation", src, None)

    obs1 = _get_path_observation(src)

    sleeper(observation_interval_seconds)

    if _fault_hook is not None:
        _fault_hook("during_observation", src, None)

    obs2 = _get_path_observation(src)

    if (
        obs1.size != obs2.size
        or obs1.mtime_ns != obs2.mtime_ns
        or obs1.device != obs2.device
        or obs1.inode != obs2.inode
    ):
        raise XlsxSourceNotReadyError(
            "Source file modified or replaced during observation window"
        )

    lease_id = uuid.uuid4().hex
    lease_dir = root / f"acq-{lease_id}"
    try:
        lease_dir.mkdir(parents=False, exist_ok=False)
    except OSError as exc:
        raise XlsxSnapshotStorageError(
            f"Failed to create lease directory: {exc}"
        ) from exc

    part_file = lease_dir / "snapshot.part"
    final_file = lease_dir / "snapshot.xlsx"
    is_promoted = False
    recorded_sha256 = ""
    recorded_bytes = 0

    def _cleanup_managed_artifacts() -> list[str]:
        errors: list[str] = []
        try:
            if part_file.exists():
                part_file.unlink()
        except OSError as exc:
            errors.append(f"Failed to unlink partial file: {exc}")

        try:
            if final_file.exists():
                final_file.unlink()
        except OSError as exc:
            errors.append(f"Failed to unlink snapshot file: {exc}")

        try:
            if lease_dir.exists():
                lease_dir.rmdir()
        except OSError as exc:
            errors.append(f"Failed to remove lease directory: {exc}")

        return errors

    try:
        if _fault_hook is not None:
            _fault_hook("before_copy_open", src, part_file)

        hasher = hashlib.sha256()
        copied_bytes = 0

        try:
            with open(src, "rb") as src_f:
                _check_fd_observation(src_f.fileno(), obs2)

                try:
                    with open(part_file, "xb") as dst_f:
                        while True:
                            if _fault_hook is not None:
                                _fault_hook("during_copy_chunk", src, part_file)
                            chunk = src_f.read(_copy_chunk_size)
                            if not chunk:
                                break
                            dst_f.write(chunk)
                            hasher.update(chunk)
                            copied_bytes += len(chunk)

                        dst_f.flush()
                        try:
                            os.fsync(dst_f.fileno())
                        except OSError:
                            pass
                except OSError as exc:
                    raise XlsxSnapshotStorageError(
                        f"Failed to write snapshot candidate: {exc}"
                    ) from exc

                _check_fd_observation(src_f.fileno(), obs2)
        except XlsxSnapshotAcquisitionError:
            raise
        except (FileNotFoundError, PermissionError) as exc:
            raise XlsxSourceNotReadyError(
                "Source file not accessible during copy"
            ) from exc
        except OSError as exc:
            raise XlsxSourceNotReadyError(
                f"Source file read failed during copy: {exc}"
            ) from exc

        if copied_bytes != obs2.size:
            raise XlsxSourceNotReadyError(
                f"Copied byte count ({copied_bytes}) differed from observed "
                f"size ({obs2.size})"
            )

        copy_sha256 = hasher.hexdigest().lower()
        recorded_sha256 = copy_sha256
        recorded_bytes = copied_bytes

        if _fault_hook is not None:
            _fault_hook("before_source_reverify", src, part_file)

        src_reverify_sha, src_reverify_len = _stream_hash_file(
            src,
            _copy_chunk_size,
            expected_observation=obs2,
            fault_hook=_fault_hook,
            fault_stage="during_source_reverify",
            target_path=part_file,
        )

        obs_after = _get_path_observation(src)
        if (
            obs_after.size != obs2.size
            or obs_after.mtime_ns != obs2.mtime_ns
            or obs_after.device != obs2.device
            or obs_after.inode != obs2.inode
            or src_reverify_len != copied_bytes
            or src_reverify_sha != copy_sha256
        ):
            raise XlsxSourceNotReadyError(
                "Source file changed during or after copy verification"
            )

        if _fault_hook is not None:
            _fault_hook("before_candidate_reverify", src, part_file)

        cand_sha, cand_len = _stream_hash_file(
            part_file,
            _copy_chunk_size,
            fault_hook=_fault_hook,
            fault_stage="during_candidate_reverify",
            target_path=part_file,
        )

        if cand_len != copied_bytes or cand_sha != copy_sha256:
            raise XlsxSnapshotIntegrityError(
                "Candidate partial file verification failed"
            )

        if _fault_hook is not None:
            _fault_hook("before_zip_validation", src, part_file)

        _validate_zip_container(part_file)

        if _fault_hook is not None:
            _fault_hook("before_promotion", src, part_file)

        try:
            part_file.replace(final_file)
            is_promoted = True
        except OSError as exc:
            raise XlsxSnapshotStorageError(
                f"Atomic promotion of snapshot candidate failed: {exc}"
            ) from exc

        snapshot_obj = StableXlsxSnapshot(
            version=XLSX_SNAPSHOT_ACQUISITION_VERSION,
            snapshot_path=final_file.resolve(),
            file_sha256=copy_sha256,
            byte_count=copied_bytes,
            source_mtime_ns=obs2.mtime_ns,
        )
    except BaseException:
        _cleanup_managed_artifacts()
        raise

    consumer_exc: BaseException | None = None
    try:
        yield snapshot_obj
    except BaseException as exc:
        consumer_exc = exc
        raise
    finally:
        integrity_exc: BaseException | None = None
        cleanup_exc: BaseException | None = None

        if is_promoted:
            try:
                if not final_file.exists() or not final_file.is_file():
                    raise XlsxSnapshotIntegrityError(
                        "Leased snapshot file disappeared or was replaced with non-file"
                    )
                st = final_file.stat()
                if st.st_size != recorded_bytes:
                    raise XlsxSnapshotIntegrityError(
                        "Leased snapshot byte count was modified during lease"
                    )
                final_sha, final_len = _stream_hash_file(final_file, _copy_chunk_size)
                if final_len != recorded_bytes or final_sha != recorded_sha256:
                    raise XlsxSnapshotIntegrityError(
                        "Leased snapshot content was modified during lease"
                    )
            except BaseException as exc:
                if isinstance(exc, XlsxSnapshotAcquisitionError):
                    integrity_exc = exc
                else:
                    integrity_exc = XlsxSnapshotIntegrityError(
                        f"Leased snapshot post-verification failed: {exc}"
                    )
                    integrity_exc.__cause__ = exc

        cleanup_errors = _cleanup_managed_artifacts()
        if cleanup_errors:
            cleanup_exc = XlsxSnapshotCleanupError("; ".join(cleanup_errors))

        secondary_exc = integrity_exc or cleanup_exc
        if secondary_exc is not None:
            if consumer_exc is not None:
                secondary_exc.__cause__ = consumer_exc
                raise secondary_exc
            else:
                raise secondary_exc
