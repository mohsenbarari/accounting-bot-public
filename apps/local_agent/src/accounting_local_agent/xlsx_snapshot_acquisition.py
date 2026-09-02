"""Stable XLSX snapshot acquisition and controlled cleanup (WP-06).

Implements the bounded adapter that converts a caller-supplied, exact operational
.xlsx path into a verified, immutable-for-the-lease temporary snapshot.
"""

from __future__ import annotations

import contextlib
import ctypes
import dataclasses
import enum
import hashlib
import math
import os
import re
import stat
import sys
import time
import uuid
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

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

    def __init__(
        self,
        message: str = "Source file is not ready for snapshot acquisition",
        *,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(
            reason=XlsxSnapshotAcquisitionReason.SOURCE_NOT_READY,
            message=message,
            retryable=retryable,
        )


class XlsxSourcePolicyError(XlsxSnapshotAcquisitionError):
    """Source path or acquisition policy violates contract (non-retryable)."""

    def __init__(
        self,
        message: str = "Source path or acquisition policy violates contract",
        *,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(
            reason=XlsxSnapshotAcquisitionReason.SOURCE_POLICY_VIOLATION,
            message=message,
            retryable=retryable,
        )


class XlsxSnapshotStorageError(XlsxSnapshotAcquisitionError):
    """Snapshot root, directory creation, write, or promotion failed."""

    def __init__(
        self,
        message: str = "Snapshot storage operation failed",
        *,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(
            reason=XlsxSnapshotAcquisitionReason.SNAPSHOT_STORAGE_FAILURE,
            message=message,
            retryable=retryable,
        )


class XlsxSnapshotIntegrityError(XlsxSnapshotAcquisitionError):
    """Candidate, source, or leased snapshot digest or byte mismatch."""

    def __init__(
        self,
        message: str = "Snapshot integrity verification failed",
        *,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(
            reason=XlsxSnapshotAcquisitionReason.SNAPSHOT_INTEGRITY_FAILURE,
            message=message,
            retryable=retryable,
        )


class XlsxSnapshotCleanupError(XlsxSnapshotAcquisitionError):
    """Managed snapshot file or lease directory could not be removed."""

    def __init__(
        self,
        message: str = "Snapshot cleanup operation failed",
        *,
        retryable: bool | None = None,
    ) -> None:
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
            raise ValueError("snapshot_path must have .xlsx extension")
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
class _FileToken:
    """Internal immutable metadata token capturing verified file observation."""

    device: int | None
    inode: int | None
    size: int | None = None
    mtime_ns: int | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class _ArtifactOwnershipToken:
    """Ownership token used exclusively for safe cleanup without wildcards."""

    device: int | None
    inode: int | None
    size: int | None = None
    expected_sha256: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class _VerifiedSnapshotToken:
    """Verified token used exclusively to authorize yielding to consumer."""

    device: int | None
    inode: int | None
    size: int
    mtime_ns: int
    sha256: str


def _extract_device_and_inode(
    st: os.stat_result,
) -> tuple[int | None, int | None]:
    """Extract platform device and inode identifiers, or (None, None) if unavailable."""
    return st.st_dev, st.st_ino


try:
    import ctypes.wintypes as _ctypes_wintypes
except ImportError:
    _ctypes_wintypes = None  # type: ignore[assignment]

_ctypes_provider: Any = ctypes


def _get_wintypes() -> Any:
    """Safely retrieve wintypes module from provider or explicit import."""
    if hasattr(_ctypes_provider, "wintypes") and _ctypes_provider.wintypes is not None:
        return _ctypes_provider.wintypes
    if _ctypes_wintypes is not None:
        return _ctypes_wintypes
    try:
        import ctypes.wintypes as wt

        return wt
    except ImportError:
        return None


def _atomic_move_no_replace(src: Path, dst: Path) -> None:
    """Atomic move with fail-if-exists semantics on Linux and Windows.

    Uses renameat2(RENAME_NOREPLACE) on Linux and MoveFileExW without
    MOVEFILE_REPLACE_EXISTING on Windows. Fails closed if the platform
    primitive is unsupported or fails with ENOSYS/EINVAL.
    """
    if sys.platform.startswith("linux"):
        try:
            libc = _ctypes_provider.CDLL(None, use_errno=True)
            if not hasattr(libc, "renameat2"):
                raise OSError(38, "renameat2 is not supported on this system")

            renameat2_func = libc.renameat2
            renameat2_func.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renameat2_func.restype = ctypes.c_int

            at_fdcwd = -100
            rename_noreplace = 1
            ret = renameat2_func(
                at_fdcwd,
                os.fsencode(str(src)),
                at_fdcwd,
                os.fsencode(str(dst)),
                rename_noreplace,
            )
            if ret != 0:
                err = _ctypes_provider.get_errno()
                if err == 17:  # EEXIST
                    raise FileExistsError("Destination path already exists")
                raise OSError(err, os.strerror(err))
            return
        except (AttributeError, OSError) as exc:
            if isinstance(exc, FileExistsError):
                raise
            raise OSError(f"Linux atomic move failed: {exc}") from exc
    elif sys.platform == "win32":
        try:
            wintypes = _get_wintypes()
            if wintypes is None:
                raise OSError("Windows wintypes module is not available")
            kernel32 = _ctypes_provider.WinDLL("kernel32", use_last_error=True)
            move_file_ex_w = kernel32.MoveFileExW
            move_file_ex_w.argtypes = [
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
            ]
            move_file_ex_w.restype = wintypes.BOOL

            # MoveFileExW flags: MOVEFILE_WRITE_THROUGH (0x8) without
            # MOVEFILE_REPLACE_EXISTING (0x1)
            flags = 0x8
            ret = move_file_ex_w(str(src), str(dst), flags)
            if not ret:
                err = _ctypes_provider.get_last_error()
                if err in (80, 183):  # ERROR_FILE_EXISTS, ERROR_ALREADY_EXISTS
                    raise FileExistsError("Destination path already exists")
                raise _ctypes_provider.WinError(err)
            return
        except (AttributeError, OSError) as exc:
            if isinstance(exc, FileExistsError):
                raise
            raise OSError(f"Windows atomic move failed: {exc}") from exc
    else:
        raise OSError("Atomic no-replace move is not supported on this platform")


class _FILE_ID_128(ctypes.Structure):
    _fields_ = [("Identifier", ctypes.c_ubyte * 16)]


class _FILE_ID_INFO(ctypes.Structure):
    _fields_ = [
        ("VolumeSerialNumber", ctypes.c_uint64),
        ("FileId", _FILE_ID_128),
    ]


def _create_and_anchor_lease_dir_windows(
    lease_dir: Path,
) -> tuple[_ArtifactOwnershipToken, int]:
    """Create lease directory and acquire Windows directory handle anchor
    via FileIdInfo.
    """
    lease_dir.mkdir(parents=False, exist_ok=False)

    wintypes = _get_wintypes()
    if wintypes is None:
        raise OSError("Windows wintypes module is not available")

    try:
        kernel32 = _ctypes_provider.WinDLL("kernel32", use_last_error=True)
        create_file_w = kernel32.CreateFileW
        create_file_w.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file_w.restype = wintypes.HANDLE
    except (AttributeError, OSError) as exc:
        raise OSError(f"Failed to initialize WinAPI CreateFileW: {exc}") from exc

    generic_read = 0x80000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    file_share_delete = 0x00000004
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    invalid_handle_value = (
        wintypes.HANDLE(-1).value
        if hasattr(wintypes, "HANDLE") and hasattr(wintypes.HANDLE(-1), "value")
        else -1
    )

    handle = create_file_w(
        str(lease_dir),
        generic_read,
        file_share_read | file_share_write | file_share_delete,
        None,
        open_existing,
        file_flag_backup_semantics | file_flag_open_reparse_point,
        None,
    )

    if handle in (-1, invalid_handle_value, None) or handle == 0:
        err = _ctypes_provider.get_last_error()
        raise OSError(f"CreateFileW failed on lease directory with error {err}")

    # Query FILE_ID_INFO (FileIdInfo = 18 in FILE_INFO_BY_HANDLE_CLASS)
    file_id_info_class = 18
    try:
        get_info_ex = kernel32.GetFileInformationByHandleEx
        get_info_ex.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        get_info_ex.restype = wintypes.BOOL

        info = _FILE_ID_INFO()
        success = get_info_ex(
            wintypes.HANDLE(handle),
            file_id_info_class,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
    except Exception as exc:
        _close_windows_handle(int(handle))
        raise OSError(
            f"GetFileInformationByHandleEx failed on lease directory: {exc}"
        ) from exc

    if not success:
        err = _ctypes_provider.get_last_error()
        _close_windows_handle(int(handle))
        raise OSError(
            f"GetFileInformationByHandleEx failed on lease directory with error {err}"
        )

    vol_id = int(info.VolumeSerialNumber)
    file_id = int.from_bytes(bytes(info.FileId.Identifier), byteorder="little")
    token = _ArtifactOwnershipToken(
        device=vol_id,
        inode=file_id,
    )
    return token, int(handle)


def _close_windows_handle(handle: int) -> None:
    """Close a Windows HANDLE safely, ensuring argtypes and restype are configured."""
    if (sys.platform == "win32" or _ctypes_provider is not ctypes) and handle not in (
        -1,
        0,
        None,
    ):
        try:
            wintypes = _get_wintypes()
            if wintypes is not None:
                kernel32 = _ctypes_provider.WinDLL("kernel32", use_last_error=True)
                close_h = kernel32.CloseHandle
                close_h.argtypes = [wintypes.HANDLE]
                close_h.restype = wintypes.BOOL
                close_h(wintypes.HANDLE(handle))
        except Exception:
            pass


def _get_path_observation(path: Path) -> _FileToken:
    try:
        st = path.lstat()
    except (FileNotFoundError, PermissionError) as exc:
        raise XlsxSourceNotReadyError(
            "Source file not accessible during observation"
        ) from exc
    except OSError as exc:
        raise XlsxSourceNotReadyError(
            "Source file stat failed during observation"
        ) from exc

    if stat.S_ISLNK(st.st_mode):
        raise XlsxSourceNotReadyError("Source file was converted to a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise XlsxSourceNotReadyError("Source file is not a regular file")

    dev, ino = _extract_device_and_inode(st)
    return _FileToken(
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
        device=dev,
        inode=ino,
    )


def _check_fd_observation(fd: int, expected: _FileToken) -> None:
    try:
        st = os.fstat(fd)
    except OSError as exc:
        raise XlsxSourceNotReadyError("Source handle fstat failed") from exc

    if not stat.S_ISREG(st.st_mode):
        raise XlsxSourceNotReadyError("Source handle is not a regular file")

    dev, ino = _extract_device_and_inode(st)
    if (
        (expected.size is not None and st.st_size != expected.size)
        or (expected.mtime_ns is not None and st.st_mtime_ns != expected.mtime_ns)
        or (expected.device is not None and dev != expected.device)
        or (expected.inode is not None and ino != expected.inode)
    ):
        raise XlsxSourceNotReadyError(
            "Source file handle metadata modified or replaced"
        )


def _open_source_nofollow(path: Path) -> int:
    """Open source file descriptor without following symlinks where supported."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY

    try:
        fd = os.open(path, flags)
    except (FileNotFoundError, PermissionError) as exc:
        raise XlsxSourceNotReadyError("Source file not accessible for opening") from exc
    except OSError as exc:
        raise XlsxSourceNotReadyError("Source file open failed") from exc

    try:
        fst = os.fstat(fd)
        lst = path.lstat()
        if stat.S_ISLNK(lst.st_mode) or not stat.S_ISREG(fst.st_mode):
            raise XlsxSourceNotReadyError(
                "Source file is a symlink or non-regular file"
            )
        f_dev, f_ino = _extract_device_and_inode(fst)
        l_dev, l_ino = _extract_device_and_inode(lst)
        if (f_dev is not None and f_dev != l_dev) or (
            f_ino is not None and f_ino != l_ino
        ):
            raise XlsxSourceNotReadyError(
                "Source file handle does not match path identity"
            )
    except XlsxSnapshotAcquisitionError:
        os.close(fd)
        raise
    except (FileNotFoundError, PermissionError) as exc:
        os.close(fd)
        raise XlsxSourceNotReadyError("Source file stat failed after open") from exc
    except OSError as exc:
        os.close(fd)
        raise XlsxSourceNotReadyError("Source file stat failed after open") from exc

    return fd


def _open_candidate_nofollow(
    path: Path, expected_dev: int | None, expected_ino: int | None
) -> int:
    """Open candidate file descriptor without following symlinks and verify identity."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY

    try:
        lst = path.lstat()
    except (FileNotFoundError, PermissionError) as exc:
        raise XlsxSnapshotStorageError(
            "Candidate partial file is inaccessible"
        ) from exc
    except OSError as exc:
        raise XlsxSnapshotStorageError("Candidate partial file stat failed") from exc

    if stat.S_ISLNK(lst.st_mode):
        raise XlsxSnapshotStorageError(
            "Candidate partial file was replaced with a symlink"
        )
    if not stat.S_ISREG(lst.st_mode):
        raise XlsxSnapshotStorageError("Candidate partial file is not a regular file")

    l_dev, l_ino = _extract_device_and_inode(lst)
    if (expected_dev is not None and l_dev != expected_dev) or (
        expected_ino is not None and l_ino != expected_ino
    ):
        raise XlsxSnapshotStorageError("Candidate partial file identity was replaced")

    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise XlsxSnapshotStorageError("Candidate partial file open failed") from exc

    try:
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode):
            raise XlsxSnapshotStorageError("Candidate handle is not a regular file")
        f_dev, f_ino = _extract_device_and_inode(fst)
        if (expected_dev is not None and f_dev != expected_dev) or (
            expected_ino is not None and f_ino != expected_ino
        ):
            raise XlsxSnapshotStorageError("Candidate handle identity does not match")
    except XlsxSnapshotAcquisitionError:
        os.close(fd)
        raise
    except OSError as exc:
        os.close(fd)
        raise XlsxSnapshotStorageError("Candidate fstat failed") from exc

    return fd


def _stream_hash_source(
    path: Path,
    chunk_size: int,
    *,
    expected_observation: _FileToken,
    fault_hook: Callable[[str, Path, Path | None], None] | None = None,
    fault_stage: str = "",
    target_path: Path | None = None,
) -> tuple[str, int]:
    hasher = hashlib.sha256()
    total_bytes = 0

    fd = _open_source_nofollow(path)
    try:
        with open(fd, "rb", closefd=True) as src_f:
            _check_fd_observation(src_f.fileno(), expected_observation)

            while True:
                if fault_hook is not None and fault_stage:
                    fault_hook(fault_stage, path, target_path)
                try:
                    chunk = src_f.read(chunk_size)
                except OSError as exc:
                    raise XlsxSourceNotReadyError("Source file read failed") from exc
                if not chunk:
                    break
                hasher.update(chunk)
                total_bytes += len(chunk)

            _check_fd_observation(src_f.fileno(), expected_observation)
    except XlsxSnapshotAcquisitionError:
        raise
    except (FileNotFoundError, PermissionError) as exc:
        raise XlsxSourceNotReadyError("Source file not accessible during read") from exc
    except OSError as exc:
        raise XlsxSourceNotReadyError("Source file read failed") from exc

    return hasher.hexdigest().lower(), total_bytes


def _stream_hash_candidate(
    path: Path,
    chunk_size: int,
    *,
    expected_dev: int | None = None,
    expected_ino: int | None = None,
    fault_hook: Callable[[str, Path, Path | None], None] | None = None,
    fault_stage: str = "",
    target_path: Path | None = None,
) -> tuple[str, int]:
    hasher = hashlib.sha256()
    total_bytes = 0

    fd = _open_candidate_nofollow(path, expected_dev, expected_ino)
    try:
        with open(fd, "rb", closefd=True) as f:
            while True:
                if fault_hook is not None and fault_stage:
                    fault_hook(fault_stage, path, target_path)
                try:
                    chunk = f.read(chunk_size)
                except OSError as exc:
                    raise XlsxSnapshotStorageError(
                        "Candidate partial file read failed"
                    ) from exc
                if not chunk:
                    break
                hasher.update(chunk)
                total_bytes += len(chunk)
    except XlsxSnapshotAcquisitionError:
        raise
    except OSError as exc:
        raise XlsxSnapshotStorageError("Candidate partial file read failed") from exc

    return hasher.hexdigest().lower(), total_bytes


def _stream_hash_leased_snapshot(
    path: Path,
    chunk_size: int,
    *,
    expected_dev: int | None = None,
    expected_ino: int | None = None,
) -> tuple[str, int]:
    """Re-stream and verify leased snapshot digest, mapping errors to IntegrityError."""
    hasher = hashlib.sha256()
    total_bytes = 0

    try:
        fd = _open_candidate_nofollow(path, expected_dev, expected_ino)
    except Exception as exc:
        raise XlsxSnapshotIntegrityError(
            "Leased snapshot handle could not be opened for verification"
        ) from exc

    try:
        with open(fd, "rb", closefd=True) as f:
            while True:
                try:
                    chunk = f.read(chunk_size)
                except OSError as exc:
                    raise XlsxSnapshotIntegrityError(
                        "Leased snapshot read failed"
                    ) from exc
                if not chunk:
                    break
                hasher.update(chunk)
                total_bytes += len(chunk)
    except XlsxSnapshotAcquisitionError:
        raise
    except Exception as exc:
        raise XlsxSnapshotIntegrityError("Leased snapshot read failed") from exc

    return hasher.hexdigest().lower(), total_bytes


def _validate_zip_container_candidate(
    path: Path, expected_dev: int | None, expected_ino: int | None
) -> None:
    """Verify ZIP Central Directory directly on verified Candidate handle."""
    fd = _open_candidate_nofollow(path, expected_dev, expected_ino)
    try:
        with open(fd, "rb", closefd=True) as f:
            with zipfile.ZipFile(f, "r") as zf:
                name_list = zf.namelist()
                if "[Content_Types].xml" not in name_list:
                    raise XlsxSourceNotReadyError(
                        "Candidate is missing [Content_Types].xml marker"
                    )
    except XlsxSnapshotAcquisitionError:
        raise
    except zipfile.BadZipFile as exc:
        raise XlsxSourceNotReadyError(
            "Snapshot candidate is not a valid ZIP container"
        ) from exc
    except (EOFError, KeyError, ValueError, OSError) as exc:
        raise XlsxSourceNotReadyError(
            "Snapshot candidate ZIP Central Directory is invalid or truncated"
        ) from exc


def _promote_candidate_atomic_fail_if_exists(
    part_file: Path,
    final_file: Path,
    owned_part_token: _ArtifactOwnershipToken | None,
) -> tuple[int | None, int | None]:
    """Promote part_file to final_file using the atomic no-replace primitive."""
    try:
        _atomic_move_no_replace(part_file, final_file)
    except FileExistsError as exc:
        raise XlsxSnapshotStorageError(
            "Promoted file already exists in private lease directory"
        ) from exc
    except OSError as exc:
        raise XlsxSnapshotStorageError(
            "Atomic promotion of snapshot candidate failed"
        ) from exc

    fin_dev = owned_part_token.device if owned_part_token else None
    fin_ino = owned_part_token.inode if owned_part_token else None
    return fin_dev, fin_ino


def _combine_exceptions(
    message: str, excs: list[BaseException]
) -> BaseException | None:
    """Helper preserving all active exceptions using native ExceptionGroup."""
    valid_excs: list[BaseException] = []
    for e in excs:
        if e is not None:
            valid_excs.append(e)

    if not valid_excs:
        return None
    if len(valid_excs) == 1:
        return valid_excs[0]

    all_standard = all(isinstance(e, Exception) for e in valid_excs)
    if all_standard:
        standard_excs = [e for e in valid_excs if isinstance(e, Exception)]
        return ExceptionGroup(message, standard_excs)
    return BaseExceptionGroup(message, valid_excs)


@contextlib.contextmanager
def open_stable_xlsx_snapshot(
    source_path: Path,
    snapshot_root: Path,
    observation_interval_seconds: float,
    *,
    _sleeper: Callable[[float], None] | None = None,
    _copy_chunk_size: int = DEFAULT_COPY_CHUNK_SIZE,
    _fault_hook: Callable[[str, Path, Path | None], None] | None = None,
) -> Iterator[StableXlsxSnapshot]:
    """Acquire a verified, stable, temporary snapshot of an XLSX source workbook.

    Yields an immutable `StableXlsxSnapshot` for the duration of the context.
    On context exit (normal return or exception), verifies lease integrity and cleans
    up the temporary lease directory using atomic quarantine.
    """
    if not isinstance(source_path, Path):
        raise XlsxSourcePolicyError("source_path must be a Path instance")

    if not source_path.name.lower().endswith(".xlsx"):
        raise XlsxSourcePolicyError("Source file must have .xlsx extension")

    if not isinstance(snapshot_root, Path):
        raise XlsxSnapshotStorageError("snapshot_root must be a Path instance")

    if not snapshot_root.exists() or not snapshot_root.is_dir():
        raise XlsxSnapshotStorageError(
            "Snapshot root does not exist or is not a directory"
        )

    if isinstance(observation_interval_seconds, bool) or not isinstance(
        observation_interval_seconds, (int, float)
    ):
        raise XlsxSourcePolicyError(
            "observation_interval_seconds must be a positive finite float"
        )

    if (
        not math.isfinite(observation_interval_seconds)
        or observation_interval_seconds <= 0.0
    ):
        raise XlsxSourcePolicyError(
            "observation_interval_seconds must be a positive finite float"
        )

    if _copy_chunk_size <= 0:
        raise XlsxSnapshotStorageError("Chunk size must be a positive integer")

    src = source_path
    root = snapshot_root
    sleeper = _sleeper if _sleeper is not None else time.sleep

    try:
        src_lstat = src.lstat()
    except (FileNotFoundError, PermissionError) as exc:
        raise XlsxSourceNotReadyError(
            "Source file does not exist or is inaccessible"
        ) from exc
    except OSError as exc:
        raise XlsxSourceNotReadyError("Source file lstat failed") from exc

    # Reject symlinks and non-regular files
    if stat.S_ISDIR(src_lstat.st_mode):
        raise XlsxSourcePolicyError("Source path points to a directory")
    if stat.S_ISLNK(src_lstat.st_mode):
        raise XlsxSourcePolicyError("Source path cannot be a symlink")
    if (
        stat.S_ISFIFO(src_lstat.st_mode)
        or stat.S_ISSOCK(src_lstat.st_mode)
        or stat.S_ISCHR(src_lstat.st_mode)
        or stat.S_ISBLK(src_lstat.st_mode)
        or not stat.S_ISREG(src_lstat.st_mode)
    ):
        raise XlsxSourcePolicyError("Source path must be a regular file")

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
    lease_dir = (root / f"acq-{lease_id}").resolve()
    part_file = lease_dir / "snapshot.part"
    final_file = lease_dir / "snapshot.xlsx"
    is_posix = os.name == "posix" or sys.platform != "win32"

    owned_lease_token: _ArtifactOwnershipToken | None = None
    owned_part_token: _ArtifactOwnershipToken | None = None
    owned_final_token: _ArtifactOwnershipToken | None = None
    verified_final_token: _VerifiedSnapshotToken | None = None

    def _cleanup_managed_artifacts() -> list[BaseException]:
        cleanup_excs: list[BaseException] = []
        files_cleaned_successfully = True

        # 1. Cleanup part_file via Atomic Quarantine (fail-if-exists)
        try:
            if part_file.exists(follow_symlinks=False):
                q_part = lease_dir / f".qpart-{uuid.uuid4().hex}"
                moved_part = False
                try:
                    _atomic_move_no_replace(part_file, q_part)
                    moved_part = True
                except OSError as exc:
                    files_cleaned_successfully = False
                    cln_err = XlsxSnapshotCleanupError(
                        "Failed to quarantine candidate file"
                    )
                    cln_err.__cause__ = exc
                    cleanup_excs.append(cln_err)

                if moved_part:
                    try:
                        q_lst = q_part.lstat()
                        is_owned = False
                        if not stat.S_ISLNK(q_lst.st_mode) and stat.S_ISREG(
                            q_lst.st_mode
                        ):
                            p_dev, p_ino = _extract_device_and_inode(q_lst)
                            if (
                                owned_part_token is not None
                                and (
                                    owned_part_token.inode is None
                                    or p_ino == owned_part_token.inode
                                )
                                and (
                                    owned_part_token.device is None
                                    or p_dev == owned_part_token.device
                                )
                            ):
                                is_owned = True

                        if is_owned:
                            if _fault_hook is not None:
                                _fault_hook("inside_part_unlink", part_file, q_part)
                            # R9-03: Re-attest identity of q_part before unlink
                            try:
                                post_part_lst = q_part.lstat()
                                is_still_owned = False
                                if not stat.S_ISLNK(
                                    post_part_lst.st_mode
                                ) and stat.S_ISREG(post_part_lst.st_mode):
                                    post_dev, post_ino = _extract_device_and_inode(
                                        post_part_lst
                                    )
                                    if (
                                        owned_part_token is not None
                                        and (
                                            owned_part_token.inode is None
                                            or post_ino == owned_part_token.inode
                                        )
                                        and (
                                            owned_part_token.device is None
                                            or post_dev == owned_part_token.device
                                        )
                                    ):
                                        is_still_owned = True
                                if is_still_owned:
                                    q_part.unlink()
                                else:
                                    files_cleaned_successfully = False
                                    cleanup_excs.append(
                                        XlsxSnapshotCleanupError(
                                            "Candidate file unproven or replaced"
                                        )
                                    )
                            except OSError as post_exc:
                                files_cleaned_successfully = False
                                cleanup_excs.append(post_exc)
                        else:
                            files_cleaned_successfully = False
                            cleanup_excs.append(
                                XlsxSnapshotCleanupError(
                                    "Candidate file unproven or replaced"
                                )
                            )
                            # Restore foreign file to original path if vacant
                            try:
                                _atomic_move_no_replace(q_part, part_file)
                            except OSError:
                                pass
                    except OSError as exc:
                        files_cleaned_successfully = False
                        cleanup_excs.append(exc)
        except OSError as exc:
            files_cleaned_successfully = False
            cleanup_excs.append(exc)

        # 2. Cleanup final_file via Atomic Quarantine (fail-if-exists)
        try:
            if final_file.exists(follow_symlinks=False):
                q_final = lease_dir / f".qfinal-{uuid.uuid4().hex}"
                moved_final = False
                try:
                    _atomic_move_no_replace(final_file, q_final)
                    moved_final = True
                except OSError as exc:
                    files_cleaned_successfully = False
                    cln_err = XlsxSnapshotCleanupError(
                        "Failed to quarantine snapshot file"
                    )
                    cln_err.__cause__ = exc
                    cleanup_excs.append(cln_err)

                if moved_final:
                    try:
                        q_lst = q_final.lstat()
                        is_owned = False
                        if not stat.S_ISLNK(q_lst.st_mode) and stat.S_ISREG(
                            q_lst.st_mode
                        ):
                            f_dev, f_ino = _extract_device_and_inode(q_lst)
                            if (
                                owned_final_token is not None
                                and (
                                    owned_final_token.inode is None
                                    or f_ino == owned_final_token.inode
                                )
                                and (
                                    owned_final_token.device is None
                                    or f_dev == owned_final_token.device
                                )
                            ):
                                is_owned = True
                                if (
                                    owned_final_token.size is not None
                                    and q_lst.st_size != owned_final_token.size
                                ):
                                    is_owned = False

                                if (
                                    is_owned
                                    and owned_final_token.expected_sha256 is not None
                                ):
                                    try:
                                        chk_fd = _open_candidate_nofollow(
                                            q_final,
                                            owned_final_token.device,
                                            owned_final_token.inode,
                                        )
                                        with open(chk_fd, "rb", closefd=True) as chk_f:
                                            chk_hasher = hashlib.sha256()
                                            while True:
                                                chk_chunk = chk_f.read(_copy_chunk_size)
                                                if not chk_chunk:
                                                    break
                                                chk_hasher.update(chk_chunk)
                                            exp_sha1 = owned_final_token.expected_sha256
                                            if (
                                                chk_hasher.hexdigest().lower()
                                                != exp_sha1
                                            ):
                                                is_owned = False
                                    except Exception:
                                        is_owned = False

                        if is_owned:
                            if _fault_hook is not None:
                                _fault_hook(
                                    "inside_final_unlink",
                                    final_file,
                                    q_final,
                                )
                            # R9-03: Re-attest identity/size/sha before
                            # unlink
                            try:
                                post_fin_lst = q_final.lstat()
                                is_still_owned = False
                                if not stat.S_ISLNK(
                                    post_fin_lst.st_mode
                                ) and stat.S_ISREG(post_fin_lst.st_mode):
                                    post_dev, post_ino = _extract_device_and_inode(
                                        post_fin_lst
                                    )
                                    if (
                                        owned_final_token is not None
                                        and (
                                            owned_final_token.inode is None
                                            or post_ino == owned_final_token.inode
                                        )
                                        and (
                                            owned_final_token.device is None
                                            or post_dev == owned_final_token.device
                                        )
                                    ):
                                        is_still_owned = True
                                        if (
                                            owned_final_token.size is not None
                                            and post_fin_lst.st_size
                                            != owned_final_token.size
                                        ):
                                            is_still_owned = False

                                        if (
                                            is_still_owned
                                            and owned_final_token.expected_sha256
                                            is not None
                                        ):
                                            try:
                                                chk_fd = _open_candidate_nofollow(
                                                    q_final,
                                                    owned_final_token.device,
                                                    owned_final_token.inode,
                                                )
                                                with open(
                                                    chk_fd, "rb", closefd=True
                                                ) as chk_f:
                                                    chk_hasher = hashlib.sha256()
                                                    while True:
                                                        chk_chunk = chk_f.read(
                                                            _copy_chunk_size
                                                        )
                                                        if not chk_chunk:
                                                            break
                                                        chk_hasher.update(chk_chunk)
                                                    oft = owned_final_token
                                                    exp_sha = oft.expected_sha256
                                                    if (
                                                        chk_hasher.hexdigest().lower()
                                                        != exp_sha
                                                    ):
                                                        is_still_owned = False
                                            except Exception:
                                                is_still_owned = False

                                if is_still_owned:
                                    q_final.unlink()
                                else:
                                    files_cleaned_successfully = False
                                    cleanup_excs.append(
                                        XlsxSnapshotCleanupError(
                                            "Snapshot file unproven or replaced"
                                        )
                                    )
                            except OSError as post_exc:
                                files_cleaned_successfully = False
                                cleanup_excs.append(post_exc)
                        else:
                            files_cleaned_successfully = False
                            cleanup_excs.append(
                                XlsxSnapshotCleanupError(
                                    "Snapshot file unproven or replaced"
                                )
                            )
                            # Restore foreign file to original path if vacant
                            try:
                                _atomic_move_no_replace(q_final, final_file)
                            except OSError:
                                pass
                    except OSError as exc:
                        files_cleaned_successfully = False
                        cleanup_excs.append(exc)
        except OSError as exc:
            files_cleaned_successfully = False
            cleanup_excs.append(exc)

        # 3. Cleanup lease_dir via Atomic Quarantine (if files cleanly unlinked)
        if files_cleaned_successfully:
            try:
                if lease_dir.exists(follow_symlinks=False):
                    q_dir = root / f".qdir-{uuid.uuid4().hex}"
                    moved_dir = False
                    try:
                        _atomic_move_no_replace(lease_dir, q_dir)
                        moved_dir = True
                    except OSError as exc:
                        cln_err = XlsxSnapshotCleanupError(
                            "Failed to quarantine lease directory"
                        )
                        cln_err.__cause__ = exc
                        cleanup_excs.append(cln_err)

                    if moved_dir:
                        try:
                            q_dir_lst = q_dir.lstat()
                            is_dir_owned = False
                            if not stat.S_ISLNK(q_dir_lst.st_mode) and stat.S_ISDIR(
                                q_dir_lst.st_mode
                            ):
                                d_dev, d_ino = _extract_device_and_inode(q_dir_lst)
                                if (
                                    owned_lease_token is not None
                                    and (
                                        owned_lease_token.inode is None
                                        or d_ino == owned_lease_token.inode
                                    )
                                    and (
                                        owned_lease_token.device is None
                                        or d_dev == owned_lease_token.device
                                    )
                                ):
                                    is_dir_owned = True

                            if is_dir_owned:
                                if _fault_hook is not None:
                                    _fault_hook("inside_lease_rmdir", lease_dir, q_dir)
                                # R9-03: Re-attest identity of q_dir before
                                # rmdir
                                try:
                                    post_dir_lst = q_dir.lstat()
                                    is_dir_still_owned = False
                                    if not stat.S_ISLNK(
                                        post_dir_lst.st_mode
                                    ) and stat.S_ISDIR(post_dir_lst.st_mode):
                                        d_dev2, d_ino2 = _extract_device_and_inode(
                                            post_dir_lst
                                        )
                                        if (
                                            owned_lease_token is not None
                                            and (
                                                owned_lease_token.inode is None
                                                or d_ino2 == owned_lease_token.inode
                                            )
                                            and (
                                                owned_lease_token.device is None
                                                or d_dev2 == owned_lease_token.device
                                            )
                                        ):
                                            is_dir_still_owned = True

                                    if is_dir_still_owned:
                                        try:
                                            q_dir.rmdir()
                                        except OSError as rmdir_exc:
                                            cleanup_excs.append(rmdir_exc)
                                            # Restore directory to original path if
                                            # vacant
                                            try:
                                                _atomic_move_no_replace(
                                                    q_dir, lease_dir
                                                )
                                            except OSError:
                                                pass
                                    else:
                                        cleanup_excs.append(
                                            XlsxSnapshotCleanupError(
                                                "Lease directory unproven or replaced"
                                            )
                                        )
                                except OSError as post_exc:
                                    cleanup_excs.append(post_exc)
                            else:
                                cleanup_excs.append(
                                    XlsxSnapshotCleanupError(
                                        "Lease directory unproven or replaced"
                                    )
                                )
                                # Restore foreign directory to original path if vacant
                                try:
                                    _atomic_move_no_replace(q_dir, lease_dir)
                                except OSError:
                                    pass
                        except OSError as exc:
                            cleanup_excs.append(exc)
            except OSError as exc:
                cleanup_excs.append(exc)

        return cleanup_excs

    try:
        # Create lease dir & record immutable descriptor/handle anchor (fail-closed)
        posix_dir_fd: int = -1
        win_dir_handle: int = -1
        try:
            if is_posix:
                lease_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
                try:
                    os.chmod(lease_dir, 0o700)
                except OSError:
                    pass

                if _fault_hook is not None:
                    _fault_hook("after_mkdir_before_anchor", lease_dir, None)

                # Establish immutable descriptor anchor immediately after mkdir
                open_flags = os.O_RDONLY
                if hasattr(os, "O_DIRECTORY"):
                    open_flags |= os.O_DIRECTORY
                if hasattr(os, "O_NOFOLLOW"):
                    open_flags |= os.O_NOFOLLOW

                try:
                    posix_dir_fd = os.open(lease_dir, open_flags)
                    if _fault_hook is not None:
                        _fault_hook("after_anchor_open_before_fstat", lease_dir, None)
                    dir_st = os.fstat(posix_dir_fd)
                    l_dev, l_ino = _extract_device_and_inode(dir_st)
                    owned_lease_token = _ArtifactOwnershipToken(
                        device=l_dev,
                        inode=l_ino,
                    )
                except OSError as exc:
                    raise XlsxSnapshotStorageError(
                        "Failed to establish lease directory anchor"
                    ) from exc
            else:
                # Windows real directory handle anchor via CreateFileW
                if _fault_hook is not None:
                    _fault_hook("after_mkdir_before_anchor", lease_dir, None)

                try:
                    (
                        owned_lease_token,
                        win_dir_handle,
                    ) = _create_and_anchor_lease_dir_windows(lease_dir)
                except OSError as exc:
                    raise XlsxSnapshotStorageError(
                        "Failed to establish lease directory anchor"
                    ) from exc
        except OSError as exc:
            if posix_dir_fd >= 0:
                try:
                    os.close(posix_dir_fd)
                except OSError:
                    pass
                posix_dir_fd = -1
            if win_dir_handle != -1:
                _close_windows_handle(win_dir_handle)
                win_dir_handle = -1
            if isinstance(exc, XlsxSnapshotAcquisitionError):
                raise
            raise XlsxSnapshotStorageError("Failed to create lease directory") from exc
        finally:
            if posix_dir_fd >= 0:
                try:
                    os.close(posix_dir_fd)
                except OSError:
                    pass
            if win_dir_handle != -1:
                _close_windows_handle(win_dir_handle)

        # Post-mkdir verification of path identity against immutable anchor
        try:
            try:
                check_st = lease_dir.lstat()
            except OSError:
                # One-shot retry for transient stat failure; must match immutable anchor
                check_st = lease_dir.lstat()

            c_dev, c_ino = _extract_device_and_inode(check_st)
            if (
                owned_lease_token.device is not None
                and c_dev != owned_lease_token.device
            ) or (
                owned_lease_token.inode is not None and c_ino != owned_lease_token.inode
            ):
                raise XlsxSnapshotStorageError(
                    "Lease directory replaced after creation"
                )
        except OSError as exc:
            if isinstance(exc, XlsxSnapshotAcquisitionError):
                raise
            raise XlsxSnapshotStorageError("Failed to stat lease directory") from exc

        if _fault_hook is not None:
            _fault_hook("before_copy_open", src, part_file)

        hasher = hashlib.sha256()
        copied_bytes = 0

        # Open source file with nofollow protection
        src_fd = _open_source_nofollow(src)
        try:
            with open(src_fd, "rb", closefd=True) as src_f:
                _check_fd_observation(src_f.fileno(), obs2)

                # Open candidate with exclusive creation and private mode
                dst_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    dst_flags |= os.O_NOFOLLOW
                if hasattr(os, "O_BINARY"):
                    dst_flags |= os.O_BINARY

                try:
                    dst_fd = os.open(part_file, dst_flags, 0o600)
                except OSError as exc:
                    raise XlsxSnapshotStorageError(
                        "Failed to create snapshot candidate file"
                    ) from exc

                try:
                    try:
                        dst_st = os.fstat(dst_fd)
                    except OSError:
                        # Handle-based retry on open descriptor for transient failure
                        dst_st = os.fstat(dst_fd)
                    p_dev, p_ino = _extract_device_and_inode(dst_st)
                    owned_part_token = _ArtifactOwnershipToken(
                        device=p_dev,
                        inode=p_ino,
                    )
                    dst_f = open(dst_fd, "wb", closefd=True)
                except BaseException as pre_stream_exc:
                    try:
                        os.close(dst_fd)
                    except OSError:
                        pass
                    if isinstance(pre_stream_exc, XlsxSnapshotAcquisitionError):
                        raise
                    raise XlsxSnapshotStorageError(
                        "Failed to initialize snapshot candidate handle"
                    ) from pre_stream_exc

                try:
                    with dst_f:
                        while True:
                            if _fault_hook is not None:
                                _fault_hook("during_copy_chunk", src, part_file)

                            try:
                                chunk = src_f.read(_copy_chunk_size)
                            except OSError as exc:
                                raise XlsxSourceNotReadyError(
                                    "Source file read failed during copy"
                                ) from exc

                            if not chunk:
                                break

                            if _fault_hook is not None:
                                _fault_hook("before_write", src, part_file)

                            try:
                                written = dst_f.write(chunk)
                                if written != len(chunk):
                                    raise XlsxSnapshotStorageError(
                                        "Short write occurred during snapshot copy"
                                    )
                            except XlsxSnapshotAcquisitionError:
                                raise
                            except OSError as exc:
                                raise XlsxSnapshotStorageError(
                                    "Failed to write snapshot candidate to storage"
                                ) from exc

                            hasher.update(chunk)
                            copied_bytes += len(chunk)

                        if _fault_hook is not None:
                            _fault_hook("before_flush", src, part_file)

                        try:
                            dst_f.flush()
                        except OSError as exc:
                            raise XlsxSnapshotStorageError(
                                "Failed to flush snapshot candidate to storage"
                            ) from exc

                        if _fault_hook is not None:
                            _fault_hook("before_fsync", src, part_file)

                        try:
                            os.fsync(dst_f.fileno())
                        except OSError as exc:
                            raise XlsxSnapshotStorageError(
                                "Failed to fsync snapshot candidate to storage"
                            ) from exc
                except XlsxSnapshotAcquisitionError:
                    raise
                except OSError as exc:
                    raise XlsxSnapshotStorageError(
                        "Failed to write snapshot candidate"
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
                "Source file read failed during copy"
            ) from exc

        if copied_bytes != obs2.size:
            raise XlsxSourceNotReadyError(
                "Copied byte count differed from observed size"
            )

        copy_sha256 = hasher.hexdigest().lower()
        if owned_part_token is not None:
            owned_part_token = _ArtifactOwnershipToken(
                device=owned_part_token.device,
                inode=owned_part_token.inode,
                size=copied_bytes,
                expected_sha256=copy_sha256,
            )

        if _fault_hook is not None:
            _fault_hook("before_source_reverify", src, part_file)

        src_reverify_sha, src_reverify_len = _stream_hash_source(
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

        cand_sha, cand_len = _stream_hash_candidate(
            part_file,
            _copy_chunk_size,
            expected_dev=owned_part_token.device if owned_part_token else None,
            expected_ino=owned_part_token.inode if owned_part_token else None,
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

        # Validate ZIP Central Directory on verified handle
        _validate_zip_container_candidate(
            part_file,
            owned_part_token.device if owned_part_token else None,
            owned_part_token.inode if owned_part_token else None,
        )

        if _fault_hook is not None:
            _fault_hook("before_promotion", src, part_file)

        # Candidate identity check immediately before promotion
        try:
            cand_pre_promo_st = part_file.lstat()
            if stat.S_ISLNK(cand_pre_promo_st.st_mode) or not stat.S_ISREG(
                cand_pre_promo_st.st_mode
            ):
                raise XlsxSnapshotStorageError(
                    "Candidate file is a symlink or non-regular file"
                )
            c_dev, c_ino = _extract_device_and_inode(cand_pre_promo_st)
            if (
                (
                    owned_part_token is not None
                    and owned_part_token.device is not None
                    and c_dev != owned_part_token.device
                )
                or (
                    owned_part_token is not None
                    and owned_part_token.inode is not None
                    and c_ino != owned_part_token.inode
                )
                or (
                    owned_part_token is not None
                    and owned_part_token.size is not None
                    and cand_pre_promo_st.st_size != owned_part_token.size
                )
            ):
                raise XlsxSnapshotStorageError(
                    "Candidate file identity or size was replaced before promotion"
                )
        except OSError as exc:
            if isinstance(exc, XlsxSnapshotAcquisitionError):
                raise
            raise XlsxSnapshotStorageError("Failed to stat candidate file") from exc

        # Promote candidate to final with atomic fail-if-exists primitive
        promo_dev, promo_ino = _promote_candidate_atomic_fail_if_exists(
            part_file, final_file, owned_part_token
        )
        owned_final_token = _ArtifactOwnershipToken(
            device=promo_dev,
            inode=promo_ino,
            size=copied_bytes,
            expected_sha256=copy_sha256,
        )

        if is_posix:
            try:
                os.chmod(final_file, 0o600)
            except OSError:
                pass

        if _fault_hook is not None:
            _fault_hook("after_promotion", src, final_file)

        # Attestation of promoted file immediately after promotion & before yield
        try:
            promoted_st = final_file.lstat()
            if stat.S_ISLNK(promoted_st.st_mode) or not stat.S_ISREG(
                promoted_st.st_mode
            ):
                raise XlsxSnapshotStorageError("Promoted file is invalid or symlink")

            p_dev, p_ino = _extract_device_and_inode(promoted_st)
            if (
                owned_final_token is not None
                and owned_final_token.device is not None
                and p_dev != owned_final_token.device
            ) or (
                owned_final_token is not None
                and owned_final_token.inode is not None
                and p_ino != owned_final_token.inode
            ):
                raise XlsxSnapshotStorageError(
                    "Promoted file identity does not match candidate"
                )

            # Re-stream and attest full content digest on verified handle
            attested_sha, attested_len = _stream_hash_candidate(
                final_file,
                _copy_chunk_size,
                expected_dev=p_dev,
                expected_ino=p_ino,
            )
            if attested_len != copied_bytes or attested_sha != copy_sha256:
                raise XlsxSnapshotIntegrityError(
                    "Promoted snapshot content does not match verified candidate"
                )

            verified_final_token = _VerifiedSnapshotToken(
                device=p_dev,
                inode=p_ino,
                size=promoted_st.st_size,
                mtime_ns=promoted_st.st_mtime_ns,
                sha256=copy_sha256,
            )
        except OSError as exc:
            if isinstance(exc, XlsxSnapshotAcquisitionError):
                raise
            raise XlsxSnapshotStorageError(
                "Failed to stat promoted snapshot file"
            ) from exc

        snapshot_obj = StableXlsxSnapshot(
            version=XLSX_SNAPSHOT_ACQUISITION_VERSION,
            snapshot_path=final_file,
            file_sha256=copy_sha256,
            byte_count=copied_bytes,
            source_mtime_ns=obs2.mtime_ns if obs2.mtime_ns is not None else 0,
        )
    except BaseException as acq_exc:
        cleanup_errs = _cleanup_managed_artifacts()
        if cleanup_errs:
            acq_cleanup_exc = XlsxSnapshotCleanupError(
                "Failed to cleanup managed artifacts"
            )
            acq_cleanup_exc.__cause__ = _combine_exceptions(
                "Underlying cleanup errors", cleanup_errs
            )
            combined = _combine_exceptions(
                "Snapshot acquisition failed with cleanup errors",
                [acq_exc, acq_cleanup_exc],
            )
            if combined is not None:
                raise combined from acq_exc
        raise acq_exc

    consumer_exc: BaseException | None = None
    try:
        yield snapshot_obj
    except BaseException as exc:
        consumer_exc = exc
        raise
    finally:
        integrity_exc: BaseException | None = None
        cleanup_exc: BaseException | None = None

        if verified_final_token is not None:
            try:
                if _fault_hook is not None:
                    _fault_hook("before_lease_reverify", final_file, None)

                try:
                    lst = final_file.lstat()
                except (FileNotFoundError, PermissionError) as exc:
                    raise XlsxSnapshotIntegrityError(
                        "Leased snapshot file disappeared or is inaccessible"
                    ) from exc
                except OSError as exc:
                    raise XlsxSnapshotIntegrityError(
                        "Leased snapshot file lstat failed"
                    ) from exc

                if stat.S_ISLNK(lst.st_mode):
                    raise XlsxSnapshotIntegrityError(
                        "Leased snapshot was replaced with a symlink"
                    )
                if not stat.S_ISREG(lst.st_mode):
                    raise XlsxSnapshotIntegrityError(
                        "Leased snapshot is not a regular file"
                    )

                lst_dev, lst_ino = _extract_device_and_inode(lst)
                if (
                    verified_final_token.device is not None
                    and lst_dev != verified_final_token.device
                ) or (
                    verified_final_token.inode is not None
                    and lst_ino != verified_final_token.inode
                ):
                    raise XlsxSnapshotIntegrityError(
                        "Leased snapshot file identity was replaced during lease"
                    )

                if lst.st_mtime_ns != verified_final_token.mtime_ns:
                    raise XlsxSnapshotIntegrityError(
                        "Leased snapshot mtime was modified during lease"
                    )

                if lst.st_size != verified_final_token.size:
                    raise XlsxSnapshotIntegrityError(
                        "Leased snapshot byte count was modified during lease"
                    )

                if _fault_hook is not None:
                    _fault_hook("between_lease_lstat_and_open", final_file, None)

                # Dedicated helper maps all I/O / handle errors to IntegrityError
                try:
                    final_sha, final_len = _stream_hash_leased_snapshot(
                        final_file,
                        _copy_chunk_size,
                        expected_dev=verified_final_token.device,
                        expected_ino=verified_final_token.inode,
                    )
                except Exception as exc:
                    raise XlsxSnapshotIntegrityError(
                        "Leased snapshot handle read/verification failed"
                    ) from exc

                if (
                    final_len != verified_final_token.size
                    or final_sha != verified_final_token.sha256
                ):
                    raise XlsxSnapshotIntegrityError(
                        "Leased snapshot content was modified during lease"
                    )
            except BaseException as exc:
                if isinstance(exc, XlsxSnapshotAcquisitionError):
                    integrity_exc = exc
                else:
                    integrity_exc = XlsxSnapshotIntegrityError(
                        "Leased snapshot post-verification failed"
                    )
                    integrity_exc.__cause__ = exc

        cleanup_errors = _cleanup_managed_artifacts()
        if cleanup_errors:
            cleanup_exc = XlsxSnapshotCleanupError("Failed to remove managed artifacts")
            cleanup_exc.__cause__ = _combine_exceptions(
                "Underlying cleanup errors", cleanup_errors
            )

        excs_to_raise: list[BaseException] = []
        if consumer_exc is not None:
            excs_to_raise.append(consumer_exc)
        if integrity_exc is not None:
            excs_to_raise.append(integrity_exc)
        if cleanup_exc is not None:
            excs_to_raise.append(cleanup_exc)

        if len(excs_to_raise) == 1:
            if excs_to_raise[0] is not consumer_exc:
                raise excs_to_raise[0]
        elif len(excs_to_raise) > 1:
            combined_lease_exc = _combine_exceptions(
                "Snapshot lease closed with multiple errors",
                excs_to_raise,
            )
            if combined_lease_exc is not None:
                raise combined_lease_exc
