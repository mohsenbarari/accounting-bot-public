# Acceptance Matrix: WP-06 Stable XLSX Snapshot Acquisition and Cleanup

## Overview

- **Work Package:** `docs/work-packages/phase-01/WP-06-stable-xlsx-snapshot-acquisition.md`
- **Governing Architecture & ADR:** `docs/adr/ADR-0002-windows-excel-agent.md`, `docs/adr/ADR-0008-streaming-xlsx-source-reader.md`, `docs/adr/ADR-0009-stable-xlsx-snapshot-acquisition.md`
- **Component Version:** `XLSX_SNAPSHOT_ACQUISITION_VERSION = "xlsx-snapshot-acquisition.v1"`
- **Baseline Commit:** `bc085acd8852e2293fdfcb786a7694fe96e93407`
- **Current Branch:** `antigravity/phase-01-stable-xlsx-snapshot-acquisition`
- **Remediation Scope:** Codex Round 12 Review Remediation (R12-01 to R12-04)

---

## Detailed Requirement Verification (SA-01 to SA-14 & R12-01 to R12-04)

| Item ID | Requirement / Scope | Status | Verification & Evidence |
| :--- | :--- | :--- | :--- |
| **SA-01** | Public API, version export, typed taxonomy & reason strings, sanitized error formatting without path/member leaks | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa01_public_api_exports_and_version`, `test_sa01_stable_xlsx_snapshot_invariants`, `test_sa01_error_taxonomy_reasons_and_retryability`, `test_sa01_no_path_or_secret_leakage_in_messages_and_repr` |
| **SA-02** | Two ordered observations; rejection of non-positive intervals, non-xlsx files, non-directory roots, string paths, and initial symlinks (with honest symlink capability probe) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa02_two_observations_and_sleeper`, `test_sa02_invalid_arguments_and_policy_rejections`, `test_sa02_initial_source_symlink_rejection`, `test_sa02_non_regular_file_rejection_policy` |
| **SA-03** | Source file read-only immutability (bytes, sha256, size, mtime_ns, permissions unchanged across all outcomes) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa03_source_immutability_on_success_and_failures` |
| **SA-04** | Missing, disappearing, or inaccessible source files raise retryable `XlsxSourceNotReadyError` | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa04_missing_and_disappearing_source_handling`, `test_sa04_inaccessible_file_permission_lock` |
| **SA-05** | In-place mutation and atomic replacement via `os.replace` at every race window abort acquisition cleanly with retryable error (or detect OS kernel file locking on Windows with source verified and next-generation acquired); symlink swap detected during observation | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa05_race_mutation_fault_injections[...]` (5 stages), `test_sa05_atomic_os_replace_at_every_race_point[...]` (5 stages), `test_sa05_source_symlink_race_mutation_with_same_inode` |
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

## Round 12 Refinements & Deterministic Oracles (R12-01 to R12-04)

1. **R12-01: Windows Handle Close Lifecycle & Typed Error Hierarchy (`test_r10_02`, `test_r11_01`):**
   - In `_close_windows_handle`, wrapped lookup, `argtypes`/`restype` setup, and API call in `try...except Exception as exc:`. Converts `AttributeError`, `ctypes.ArgumentError`, `TypeError`, `OSError`, and `ret == False` into `XlsxSnapshotCleanupError(f"CloseHandle failed on handle {handle}...")` with reason `SNAPSHOT_CLEANUP_FAILURE` and cause chained (`WINDOWS_CLOSE_HANDLE_TYPED_ERROR = True`).
   - In `_create_and_anchor_lease_dir_windows`, immediate single ownership of opened handle with `try...except` ensuring no handle leak. On double failure, chains `query_exc from cln_err` where `cln_err` contains `exc` (`WINDOWS_DOUBLE_FAILURE_CAUSES_PRESERVED = True`).
   - In `open_stable_xlsx_snapshot`, catches close failures from `_close_windows_handle` in `finally` and raises `XlsxSnapshotCleanupError("Failed to close lease directory anchor")`, aborting without yield (`WINDOWS_CLOSE_HANDLE_FINALLY_FAIL_CLOSED = True`).
   - Test `test_r10_02` covers: return-true, return-false, lookup failure (`AttributeError`), call failure (`ctypes.ArgumentError`), query failure + successful close, query failure + close failure, and close failure on context exit. Exact `call_count` asserted across all test cases.
   - Enhanced native test `test_r11_01_windows_handle_protect_from_close_native_oracle` with full WinAPI prototype declarations and cleanup verification in `finally`.

2. **R12-02: Windows Lease Directory Anchor Oracle (`test_r8_01`):**
   - Updated `test_r8_01_anchor_failure_between_mkdir_and_open_fail_closed_oracle` to intercept `CreateFileW` on Windows when targeting `foreign_dir`, failing with `-1` (`ERROR_ACCESS_DENIED`).
   - Asserts `anchor_yielded is False`, `XlsxSnapshotStorageError` is raised, `foreign_dir.exists()`, `displaced_owned_dir.exists()`, and `list(foreign_dir.iterdir()) == []` on both POSIX and Windows backends.

3. **R12-03: Candidate Protection and Writer Coverage (`test_r8_08`, `test_r8_09`, `test_sa05`):**
   - In `test_r8_08`, verified transient fstat retry targets the exact same file descriptor and asserted candidate descriptor is cleanly closed upon context exit.
   - In `open_stable_xlsx_snapshot`, added fault hook `after_candidate_close_before_cleanup` immediately after `os.close(dst_fd)` when fstat fails. In `test_r8_09`, asserted unconditionally that foreign replacement was created, survived cleanup, retains exact foreign bytes, storage error was visible, and no snapshot was yielded.
   - In `test_sa05_atomic_os_replace_at_every_race_point`, asserted `attempted`, `succeeded`, and `blocked` outcomes. If blocked by Windows kernel file locking, verified `PermissionError` (WinError 32/5), verified source remained uncorrupted, verified clean snapshot, and verified subsequent acquisition of the new generation after reader exit.
   - In `test_sa05_source_symlink_race_mutation_with_same_inode`, performed symlink swap during `during_observation` so swap succeeds cleanly and observation 2 detects and rejects the symlink.

4. **R12-04: Honest Symlink Capability Probing & CI Guard:**
   - In `_probe_symlink_capability(tmp_path)`, explicitly probes symlink creation and skips only for `WinError 1314` (`ERROR_PRIVILEGE_NOT_HELD`) or `errno.EPERM`. Any unexpected `OSError` raises immediately.
   - In `_assert_or_skip_symlink_capability(tmp_path)`, checks `CI=true` or `GITHUB_ACTIONS=true` and calls `pytest.fail` if capability is missing in CI, preventing silent green jobs.

---

## Coverage gaps

- **Linux (Native Platform):** Full test suite (270/270 active tests passing, 3 benchmark runs under 12.5s and 60 MiB RSS).
- **Windows Runtime Platform:** Platform-conditional runtime execution (`test_r9_10`, `test_r11_01`) is recorded as **PENDING** independent Codex CI execution on PR. Static type compliance verified via `mypy --platform win32` (Exit 0).

---

## Gate statement

- Gate G1 status remains `OPEN / IN PROGRESS`.
- Implementer status for WP-06 is `REQUEST_CHANGES` pending Codex PR review and merge.
