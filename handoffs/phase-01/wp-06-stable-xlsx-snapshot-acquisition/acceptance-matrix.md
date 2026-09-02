# Acceptance Matrix: WP-06 Stable XLSX Snapshot Acquisition and Cleanup

## Overview

- **Work Package:** `docs/work-packages/phase-01/WP-06-stable-xlsx-snapshot-acquisition.md`
- **Governing Architecture & ADR:** `docs/adr/ADR-0002-windows-excel-agent.md`, `docs/adr/ADR-0008-streaming-xlsx-source-reader.md`, `docs/adr/ADR-0009-stable-xlsx-snapshot-acquisition.md`
- **Component Version:** `XLSX_SNAPSHOT_ACQUISITION_VERSION = "xlsx-snapshot-acquisition.v1"`
- **Baseline Commit:** `bc085acd8852e2293fdfcb786a7694fe96e93407`
- **Current Branch:** `antigravity/phase-01-stable-xlsx-snapshot-acquisition`
- **Remediation Scope:** Codex Round 13 Review Remediation (R13-01 to R13-03)

---

## Detailed Requirement Verification (SA-01 to SA-14 & R13-01 to R13-03)

| Item ID | Requirement / Scope | Status | Verification & Evidence |
| :--- | :--- | :--- | :--- |
| **SA-01** | Public API, version export, typed taxonomy & reason strings, sanitized constant error formatting without path/member/secret leaks | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa01_public_api_exports_and_version`, `test_sa01_stable_xlsx_snapshot_invariants`, `test_sa01_error_taxonomy_reasons_and_retryability`, `test_sa01_no_path_or_secret_leakage_in_messages_and_repr` |
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

## Round 13 Refinements & Deterministic Oracles (R13-01 to R13-03)

1. **R13-01: Reversion to Exclusive Candidate Creation (`O_CREAT | O_EXCL`) & Invariant Hardening:**
   - In `open_stable_xlsx_snapshot`, reverted candidate open flags from `os.O_CREAT | os.O_TRUNC` back to `os.O_CREAT | os.O_EXCL` (`CANDIDATE_CREATION_EXCLUSIVE = True`).
   - If candidate path already exists before copy open, `os.open` fails immediately with `FileExistsError` -> typed `XlsxSnapshotStorageError("Failed to create snapshot candidate file")`, aborting without yield.
   - Pre-existing and hardlinked candidate files are never truncated, overwritten, deleted, or promoted.
   - Added two dedicated regression tests:
     - `test_r13_01_pre_existing_candidate_file_fail_closed_oracle`: Injects pre-existing candidate file at `before_copy_open`; asserts `XlsxSnapshotStorageError`, no yield, foreign candidate file survives cleanup untouched with exact initial bytes, and source workbook is untouched.
     - `test_r13_01_candidate_hardlink_to_source_immutability_oracle`: Injects hard link (`os.link(src, part_file)`) at `before_copy_open`; asserts `XlsxSnapshotStorageError`, no yield, and crucially asserts source size is NOT truncated to 0 bytes and retains 100% byte, size, mtime, and hash parity.

2. **R13-02: Sanitized Constant Typed Error Messages & Independent Exception Branching:**
   - Replaced all formatted exception messages (`f"...{exc}"`, `f"...{err}"`) in WinAPI and move helpers with constant static messages (`"Failed to initialize WinAPI CreateFileW"`, `"CreateFileW invocation failed on lease directory"`, `"CreateFileW failed on lease directory"`, `"Failed to query lease directory identity"`, `"CloseHandle failed on handle"`, `"Linux atomic move failed"`, `"Windows atomic move failed"`).
   - In `_create_and_anchor_lease_dir_windows`, when both query and close fail, both exceptions are bundled into an `ExceptionGroup`/`BaseExceptionGroup` with distinct objects: `query_exc` retains raw query exception in `__cause__`, and `cln_err` retains raw close exception in `__cause__`. Neither overwrites the other and no self-causes are formed.
   - In `test_r10_02`, verified all typed messages and string representations are free of raw exception text, secret markers, and absolute directory paths.

3. **R13-03: Real Foreign File Replacement in `test_r8_09` via `os.replace`:**
   - In `test_r8_09`, prepared an independent foreign file `prepared_foreign` with distinct bytes in the fixture.
   - In hook `after_candidate_close_before_cleanup`, executed `os.replace(prepared_foreign, t)` on the closed candidate handle.
   - Asserted `os.replace` succeeded, asserted that the replacement file identity matches `prepared_foreign` and differs from original candidate, and unconditionally asserted foreign file survival, bytes unchanged, `XlsxSnapshotStorageError` + `XlsxSnapshotCleanupError`, no yield, and source immutability.

---

## Coverage gaps

- **Linux (Native Platform):** Full test suite (272/272 active tests passing, 3 benchmark runs under 12.7s and 60 MiB RSS).
- **Windows Runtime Platform:** Platform-conditional runtime execution (`test_r9_10`, `test_r11_01`) is recorded as **PENDING** independent Codex CI execution on PR. Static type compliance verified via `mypy --platform win32` (Exit 0).

---

## Gate statement

- Gate G1 status remains `OPEN / IN PROGRESS`.
- Implementer status for WP-06 is `REQUEST_CHANGES` pending Codex PR review and merge.
