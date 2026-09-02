# Acceptance Matrix: WP-06 Stable XLSX Snapshot Acquisition and Cleanup

## Overview

- **Work Package:** `docs/work-packages/phase-01/WP-06-stable-xlsx-snapshot-acquisition.md`
- **Governing Architecture & ADR:** `docs/adr/ADR-0002-windows-excel-agent.md`, `docs/adr/ADR-0008-streaming-xlsx-source-reader.md`, `docs/adr/ADR-0009-stable-xlsx-snapshot-acquisition.md`
- **Component Version:** `XLSX_SNAPSHOT_ACQUISITION_VERSION = "xlsx-snapshot-acquisition.v1"`
- **Baseline Commit:** `bc085acd8852e2293fdfcb786a7694fe96e93407`
- **Current Branch:** `antigravity/phase-01-stable-xlsx-snapshot-acquisition`
- **Remediation Scope:** Codex Round 11 Review Remediation (R11-01 to R11-04)

---

## Detailed Requirement Verification (SA-01 to SA-14 & R11-01 to R11-04)

| Item ID | Requirement / Scope | Status | Verification & Evidence |
| :--- | :--- | :--- | :--- |
| **SA-01** | Public API, version export, typed taxonomy & reason strings, sanitized error formatting without path/member leaks | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa01_public_api_exports_and_version`, `test_sa01_stable_xlsx_snapshot_invariants`, `test_sa01_error_taxonomy_reasons_and_retryability`, `test_sa01_no_path_or_secret_leakage_in_messages_and_repr` |
| **SA-02** | Two ordered observations; rejection of non-positive intervals, non-xlsx files, non-directory roots, string paths, and initial symlinks (with runtime symlink privilege probe) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa02_two_observations_and_sleeper`, `test_sa02_invalid_arguments_and_policy_rejections`, `test_sa02_initial_source_symlink_rejection`, `test_sa02_non_regular_file_rejection_policy` |
| **SA-03** | Source file read-only immutability (bytes, sha256, size, mtime_ns, permissions unchanged across all outcomes) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa03_source_immutability_on_success_and_failures` |
| **SA-04** | Missing, disappearing, or inaccessible source files raise retryable `XlsxSourceNotReadyError` | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa04_missing_and_disappearing_source_handling`, `test_sa04_inaccessible_file_permission_lock` |
| **SA-05** | In-place mutation and atomic replacement via `os.replace` at every race window abort acquisition cleanly with retryable error (or detect OS kernel file locking on Windows); same-inode symlink swap detected | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa05_race_mutation_fault_injections[...]` (5 stages), `test_sa05_atomic_os_replace_at_every_race_point[...]` (5 stages), `test_sa05_source_symlink_race_mutation_with_same_inode` |
| **SA-06** | Streaming copy with bounded chunks (`_copy_chunk_size`), no unbounded `read(-1)`, exact byte count and SHA-256 computation | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa06_streaming_copy_bounded_chunks`, `test_sa06_bounded_stream_wrapper_verification` |
| **SA-07** | Fast central directory structure and `[Content_Types].xml` verification on dedicated Candidate handle; candidate symlink rejection leaving external target untouched; pre-existing final snapshot not overwritten or deleted; real stream read/write/flush/fsync errors mapped with correct taxonomy | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa07_zip_central_directory_only_and_no_testzip_or_open_called`, `test_sa07_invalid_container_and_storage_faults`, `test_sa07_candidate_symlink_rejection_leaves_target_untouched`, `test_sa07_pre_existing_final_snapshot_not_overwritten_or_deleted`, `test_sa07_real_stream_io_faults_and_short_write` |
| **SA-08** | Promoted snapshot `.xlsx` leased exclusively to consumer; original source mutation isolated; private 0700/0600 POSIX permissions; snapshot_path never resolves externally | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa08_promoted_path_and_source_mutation_isolation`, `test_sa08_posix_private_file_permissions`, `test_sa08_snapshot_path_never_resolves_externally` |
| **SA-09** | Snapshot modification, deletion, `os.replace` during lease detected on Linux & Windows with replacement/tampered file preserved for forensics, or symlink replacement | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa09_lease_mutation_and_deletion_detection`, `test_sa09_atomic_os_replace_during_lease_preserves_replacement_file`, `test_sa09_symlink_replacement_during_lease` |
| **SA-10** | Acquisition and lease cleanup failure visibility; concurrent errors combined into `ExceptionGroup`/`BaseExceptionGroup` with underlying causes preserved | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa10_acquisition_and_cleanup_coincident_exception_group`, `test_sa10_consumer_and_cleanup_coincident_exception_group`, `test_sa10_consumer_integrity_and_cleanup_coincident_exception_group` |
| **SA-11** | Concurrent acquisitions: SHA equality for identical sources, distinct SHAs for different sources, disjoint paths, independent exit lifecycle without race interference | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa11_concurrent_disjoint_acquisitions` |
| **SA-12** | Full 4-sheet synthetic workbook acquired and validated; 100% snapshot and hash parity against independent WP-04 / WP-03 oracle | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa12_end_to_end_reader_and_planner_oracle` |
| **SA-13** | Source mutation prevents invalid/partial change plan construction or execution voids | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa13_source_change_prevents_partial_planner_voids` |
| **SA-14** | Hypothesis property testing over arbitrary chunk sizes and intervals; combined 15,000-row streaming benchmark with full 64-char SHA256 and unbuffered output | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa14_hypothesis_chunk_and_interval_properties`, `test_sa14_combined_15000_row_benchmark` |

---

## Round 11 Refinements & Deterministic Oracles (R11-01 to R11-04)

1. **R11-01: Windows Handle Close Error Visibility & Fail-Closed Behavior (`test_r10_02`, `test_r11_01`):**
   - In `_close_windows_handle`, verified the return `BOOL` of `CloseHandle` and raised `OSError` with `get_last_error()` on failure (`WINDOWS_CLOSE_HANDLE_RETURN_BOOL_CHECKED = True`).
   - In `_create_and_anchor_lease_dir_windows`, when both query and close fail, chained `close_exc.__cause__ = query_exc` and raised `query_exc` with `__cause__` set to `close_exc` (`WINDOWS_DOUBLE_FAILURE_CAUSES_PRESERVED = True`).
   - In `open_stable_xlsx_snapshot`, when `CloseHandle` fails in `finally`, converted to `XlsxSnapshotStorageError("Failed to close lease directory anchor") from close_exc`, preventing yield (`WINDOWS_CLOSE_HANDLE_FINALLY_FAIL_CLOSED = True`).
   - Added native test `test_r11_01_windows_handle_protect_from_close_native_oracle` using `HANDLE_FLAG_PROTECT_FROM_CLOSE = 2` on real Windows.

2. **R11-02: Oracle Alignment Across POSIX & Windows Backends:**
   - **Linux renameat2 on Windows (`test_r9_04`):** Used module seam `_platform_override` to dispatch and assert Linux `renameat2` ENOSYS, EINVAL, and Missing Symbol on all platforms, as well as Windows `MoveFileExW` failure without replace (`WINDOWS_MOVEFILEEXW_FAIL_CLOSED = True`).
   - **Anchor Directory Hook (`test_r8_01`, `test_r8_02`):** Hook `after_mkdir_before_anchor` called after `mkdir()` and before `CreateFileW`; hook `after_anchor_open_before_fstat` called after `CreateFileW` and before `GetFileInformationByHandleEx`. Tested on both POSIX (`os.fstat`) and Windows (`_query_file_id_info_windows`).
   - **Candidate fstat (`test_r8_08`, `test_r8_09`):** Intercepted candidate fstat cleanly via `_fstat_candidate_fd` without guessing fds or reading `/proc/self/fd` (`CANDIDATE_FSTAT_TRANSIENT_RETRY_PASS = True`, `CANDIDATE_FSTAT_FAILURE_FOREIGN_SURVIVED = True`).
   - **Identity Fallback (`test_r8_16`):** Mocked both `_extract_device_and_inode` and `_extract_windows_handle_identity` to `(None, None)` for hypothetical provider fallback (`FALLBACK_IDENTITY_UNAVAILABLE_PASS = True`).
   - **Writer Replace & File Locking (`test_sa05`):** Tracked `attempted`, `succeeded`, and `blocked`. If writer is blocked by Windows kernel file locking, verified uncorrupted reader result and verified replacement succeeds after handle close.

3. **R11-03: Honest Environment Symlink Capability Probing:**
   - Probed native symlink creation capability via `_can_create_symlinks(tmp_path)` across `test_sa02`, `test_sa05`, `test_sa07`, and `test_sa09` (`SYMLINK_PERMISSION_PROBED_HONESTLY = True`).
   - If user account lacks `SeCreateSymbolicLinkPrivilege` (WinError 1314), skips with clear reason without masking product failures.

4. **R11-04: Full Benchmark Output with Unbuffered Capture Escape (`test_sa14`):**
   - Output wrapped with `capsys.disabled()`, printing full 64-character SHA-256 string visible in standard `uv run pytest` CI (`BENCHMARK_FULL_SHA256_UNBUFFERED_VISIBLE = True`).

---

## Coverage gaps

- **Linux (Native Platform):** Full test suite (270/270 active tests passing, 3 benchmark runs under 12.3s and 60 MiB RSS).
- **Windows Runtime Platform:** Platform-conditional runtime execution (`test_r9_10`, `test_r11_01`) is recorded as **PENDING** independent Codex CI execution on PR. Static type compliance verified via `mypy --platform win32` (Exit 0).

---

## Gate statement

- Gate G1 status remains `OPEN / IN PROGRESS`.
- Implementer status for WP-06 is `REQUEST_CHANGES` pending Codex PR review and merge.
