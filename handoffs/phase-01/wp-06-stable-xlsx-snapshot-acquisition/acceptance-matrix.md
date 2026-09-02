# Acceptance Matrix: WP-06 Stable XLSX Snapshot Acquisition and Cleanup

## Overview

- **Work Package:** `docs/work-packages/phase-01/WP-06-stable-xlsx-snapshot-acquisition.md`
- **Governing Architecture & ADR:** `docs/adr/ADR-0002-windows-excel-agent.md`, `docs/adr/ADR-0008-streaming-xlsx-source-reader.md`, `docs/adr/ADR-0009-stable-xlsx-snapshot-acquisition.md`
- **Component Version:** `XLSX_SNAPSHOT_ACQUISITION_VERSION = "xlsx-snapshot-acquisition.v1"`
- **Baseline Commit:** `bc085acd8852e2293fdfcb786a7694fe96e93407`
- **Current Branch:** `antigravity/phase-01-stable-xlsx-snapshot-acquisition`
- **Remediation Scope:** Codex Round 10 Review Remediation (R10-01 to R10-03)

---

## Detailed Requirement Verification (SA-01 to SA-14 & R10-01 to R10-03)

| Item ID | Requirement / Scope | Status | Verification & Evidence |
| :--- | :--- | :--- | :--- |
| **SA-01** | Public API, version export, typed taxonomy & reason strings, sanitized error formatting without path/member leaks | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa01_public_api_exports_and_version`, `test_sa01_stable_xlsx_snapshot_invariants`, `test_sa01_error_taxonomy_reasons_and_retryability`, `test_sa01_no_path_or_secret_leakage_in_messages_and_repr` |
| **SA-02** | Two ordered observations; rejection of non-positive intervals, non-xlsx files, non-directory roots, string paths, and initial symlinks | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa02_two_observations_and_sleeper`, `test_sa02_invalid_arguments_and_policy_rejections`, `test_sa02_initial_source_symlink_rejection`, `test_sa02_non_regular_file_rejection_policy` |
| **SA-03** | Source file read-only immutability (bytes, sha256, size, mtime_ns, permissions unchanged across all outcomes) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa03_source_immutability_on_success_and_failures` |
| **SA-04** | Missing, disappearing, or inaccessible source files raise retryable `XlsxSourceNotReadyError` | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa04_missing_and_disappearing_source_handling`, `test_sa04_inaccessible_file_permission_lock` |
| **SA-05** | In-place mutation and atomic replacement via `os.replace` at every race window abort acquisition cleanly with retryable error; same-inode symlink swap detected | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa05_race_mutation_fault_injections[...]` (5 stages), `test_sa05_atomic_os_replace_at_every_race_point[...]` (5 stages), `test_sa05_source_symlink_race_mutation_with_same_inode` |
| **SA-06** | Streaming copy with bounded chunks (`_copy_chunk_size`), no unbounded `read(-1)`, exact byte count and SHA-256 computation | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa06_streaming_copy_bounded_chunks`, `test_sa06_bounded_stream_wrapper_verification` |
| **SA-07** | Fast central directory structure and `[Content_Types].xml` verification on dedicated Candidate handle; candidate symlink rejection leaving external target untouched; pre-existing final snapshot not overwritten or deleted; real stream read/write/flush/fsync errors mapped with correct taxonomy | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa07_zip_central_directory_only_and_no_testzip_or_open_called`, `test_sa07_invalid_container_and_storage_faults`, `test_sa07_candidate_symlink_rejection_leaves_target_untouched`, `test_sa07_pre_existing_final_snapshot_not_overwritten_or_deleted`, `test_sa07_real_stream_io_faults_and_short_write` |
| **SA-08** | Promoted snapshot `.xlsx` leased exclusively to consumer; original source mutation isolated; private 0700/0600 POSIX permissions; snapshot_path never resolves externally | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa08_promoted_path_and_source_mutation_isolation`, `test_sa08_posix_private_file_permissions`, `test_sa08_snapshot_path_never_resolves_externally` |
| **SA-09** | Snapshot modification, deletion, `os.replace` during lease detected on Linux & Windows with replacement/tampered file preserved for forensics, or symlink replacement | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa09_lease_mutation_and_deletion_detection`, `test_sa09_atomic_os_replace_during_lease_preserves_replacement_file`, `test_sa09_symlink_replacement_during_lease` |
| **SA-10** | Acquisition and lease cleanup failure visibility; concurrent errors combined into `ExceptionGroup`/`BaseExceptionGroup` with underlying causes preserved | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa10_acquisition_and_cleanup_coincident_exception_group`, `test_sa10_consumer_and_cleanup_coincident_exception_group`, `test_sa10_consumer_integrity_and_cleanup_coincident_exception_group` |
| **SA-11** | Concurrent acquisitions: SHA equality for identical sources, distinct SHAs for different sources, disjoint paths, independent exit lifecycle without race interference | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa11_concurrent_disjoint_acquisitions` |
| **SA-12** | Full 4-sheet synthetic workbook acquired and validated; 100% snapshot and hash parity against independent WP-04 / WP-03 oracle | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa12_end_to_end_reader_and_planner_oracle` |
| **SA-13** | Source mutation prevents invalid/partial change plan construction or execution voids | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa13_source_change_prevents_partial_planner_voids` |
| **SA-14** | Hypothesis property testing over arbitrary chunk sizes and intervals; combined 15,000-row streaming benchmark | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa14_hypothesis_chunk_and_interval_properties`, `test_sa14_combined_15000_row_benchmark` |

---

## Round 10 Refinements & Deterministic Oracles (R10-01 to R10-03)

1. **R10-01: Independent and Explicit WinAPI Loading (`test_r10_01`):**
   - Submodule `ctypes.wintypes` is explicitly imported at product level (`_ctypes_wintypes`) and resolved via `_get_wintypes()`.
   - No Windows execution path relies on prior imports from pytest, host applications, or external tests (`WINTYPES_EXPLICIT_IMPORT_STANDALONE = True`, `WINTYPES_PRELOAD_INDEPENDENT = True`).
   - Covered across all three Windows WinAPI operations: `_create_and_anchor_lease_dir_windows`, `MoveFileExW` in `_atomic_move_no_replace`, and `_close_windows_handle`.
   - WinAPI initialization errors are wrapped into typed acquisition error taxonomy with chained causes (`__cause__`).
   - Verified via fresh subprocess test (`test_r10_01_fresh_subprocess_import_and_wintypes_independence`) using `sys.executable` with zero preloaded test fixtures.

2. **R10-02: Full 128-bit File ID and 64-bit Volume Identity from Windows Handle (`test_r10_02`, `test_r9_10`):**
   - Implemented `FILE_ID_INFO` structure (`VolumeSerialNumber: ULONGLONG` [uint64], `FileId: FILE_ID_128` [16 bytes]) queried via `GetFileInformationByHandleEx(FileIdInfo = 18)`.
   - Volume serial number is kept as full 64-bit integer (`WINDOWS_VOLUME_SERIAL_64BIT_EXACT = True`) and file ID as full 128-bit integer (`WINDOWS_FILE_ID_INFO_128BIT_EXACT = True`) without 32-bit/64-bit masking or truncation.
   - Matches exact `Path.lstat().st_dev` and `st_ino` representation in CPython on Windows.
   - Argtypes and restype accurately configured for all WinAPI calls, including `CloseHandle` in failure paths (`WINDOWS_HANDLE_CLOSE_EXACTLY_ONCE = True`).
   - Tested for successful identity retrieval on fixture with `VolumeSerialNumber > 2**32` and `FileId > 2**64`, as well as `CreateFileW` and `GetFileInformationByHandleEx` failure paths.

3. **R10-03: Deterministic ENOSYS/EINVAL and Missing Symbol Oracle (`test_r9_04`):**
   - Built a callable mock function wrapper (`MockCtypesFunc`) that tracks call counts while allowing `argtypes` and `restype` assignment.
   - Tested `ENOSYS` (errno 38) and `EINVAL` (errno 22) independently, verifying the syscall stub is invoked (`RENAME_NOREPLACE_STUB_INVOKED = True`), fails closed with `OSError` (`RENAME_NOREPLACE_ENOSYS_FAILED_CLOSED = True`, `RENAME_NOREPLACE_EINVAL_FAILED_CLOSED = True`), and leaves both source and destination untouched (`RENAME_NOREPLACE_SOURCE_PRESERVED = True`, `RENAME_NOREPLACE_FOREIGN_DEST_PRESERVED = True`).
   - Tested missing `renameat2` symbol independently (`RENAME_NOREPLACE_MISSING_SYMBOL_FAILED_CLOSED = True`).

---

## Coverage gaps

- **Linux (Native Platform):** Full test suite (270/270 active tests passing, 3 benchmark runs under 13s and 61 MiB RSS).
- **Windows Runtime Platform:** Platform-conditional runtime execution (`test_r9_10`) is recorded as **PENDING** independent Codex CI execution on PR. Static type compliance verified via `mypy --platform win32` (Exit 0).

---

## Gate statement

- Gate G1 status remains `OPEN / IN PROGRESS`.
- Implementer status for WP-06 is `REQUEST_CHANGES` pending Codex PR review and merge.
