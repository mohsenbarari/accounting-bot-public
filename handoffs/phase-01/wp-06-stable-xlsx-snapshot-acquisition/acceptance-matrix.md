# Acceptance Matrix: WP-06 Stable XLSX Snapshot Acquisition and Cleanup

## Overview

- **Work Package:** `docs/work-packages/phase-01/WP-06-stable-xlsx-snapshot-acquisition.md`
- **Governing Architecture & ADR:** `docs/adr/ADR-0002-windows-excel-agent.md`, `docs/adr/ADR-0008-streaming-xlsx-source-reader.md`, `docs/adr/ADR-0009-stable-xlsx-snapshot-acquisition.md`
- **Component Version:** `XLSX_SNAPSHOT_ACQUISITION_VERSION = "xlsx-snapshot-acquisition.v1"`
- **Baseline Commit:** `bc085acd8852e2293fdfcb786a7694fe96e93407`
- **Current Branch:** `antigravity/phase-01-stable-xlsx-snapshot-acquisition`
- **Remediation Scope:** Codex Round 1 Review Remediation (R1–R7)

---

## Detailed Requirement Verification (SA-01 to SA-14 & R1 to R7)

| Item ID | Requirement / Scope | Status | Verification & Evidence |
| :--- | :--- | :--- | :--- |
| **SA-01** | Public API, version export, typed taxonomy & reason strings, sanitized error formatting | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa01_public_api_exports_and_version`, `test_sa01_stable_xlsx_snapshot_invariants`, `test_sa01_error_taxonomy_reasons_and_retryability`, `test_sa01_no_path_or_secret_leakage_in_messages_and_repr` |
| **SA-02** | Two ordered observations separated by explicit interval; rejection of non-positive intervals, non-xlsx files, and non-directory roots | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa02_two_observations_and_sleeper`, `test_sa02_invalid_arguments_and_policy_rejections`, `test_sa02_non_regular_file_rejection_policy` |
| **SA-03** | Source file read-only immutability (bytes, sha256, size, mtime_ns, permissions unchanged across successes and failures) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa03_source_immutability_on_success_and_failures` |
| **SA-04** | Missing, disappearing, or inaccessible source files raise retryable `XlsxSourceNotReadyError` | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa04_missing_and_disappearing_source_handling`, `test_sa04_inaccessible_file_permission_lock` |
| **SA-05** | In-place mutation and atomic replacement via `os.replace` at every race window abort acquisition cleanly with retryable error | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa05_race_mutation_fault_injections[...]` (5 parametrized race stages), `test_sa05_atomic_os_replace_at_every_race_point[...]` (5 parametrized stages) |
| **SA-06** | Streaming copy with bounded chunks (`_copy_chunk_size`), no unbounded `read(-1)`, exact byte count and SHA-256 computation | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa06_streaming_copy_bounded_chunks`, `test_sa06_bounded_stream_wrapper_verification` |
| **SA-07** | Fast central directory structure and `[Content_Types].xml` verification without `testzip()` or member decoding (R1); storage write/flush/fsync taxonomy (R2) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa07_zip_central_directory_only_and_no_testzip_or_open_called`, `test_sa07_invalid_container_and_storage_faults`, `test_sa07_io_faults_write_flush_fsync_storage_taxonomy` |
| **SA-08** | Promoted snapshot `.xlsx` leased exclusively to consumer; original source mutation isolated; private 0700/0600 POSIX permissions (R4) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa08_promoted_path_and_source_mutation_isolation`, `test_sa08_posix_private_file_permissions` |
| **SA-09** | Snapshot modification, deletion, `os.replace`, or symlink replacement during lease detected as non-retryable integrity failure (R5) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa09_lease_mutation_and_deletion_detection`, `test_sa09_atomic_os_replace_during_lease`, `test_sa09_symlink_replacement_during_lease` |
| **SA-10** | Acquisition and lease cleanup failure visibility; concurrent errors combined into `ExceptionGroup`/`BaseExceptionGroup` without loss (R3) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa10_cleanup_failure_and_exception_chaining`, `test_sa10_acquisition_and_cleanup_coincident_exception_group`, `test_sa10_consumer_and_cleanup_coincident_exception_group`, `test_sa10_consumer_integrity_and_cleanup_coincident_exception_group` |
| **SA-11** | Concurrent acquisitions with identical AND distinct contents operate in disjoint directories without collision or leftover artifacts | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa11_concurrent_disjoint_acquisitions` |
| **SA-12** | Full 4-sheet synthetic workbook acquired and validated; 100% snapshot and hash parity against independent WP-04 / WP-03 oracle | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa12_end_to_end_reader_and_planner_oracle` |
| **SA-13** | Source mutation prevents invalid/partial change plan construction or execution voids | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa13_source_change_prevents_partial_planner_voids` |
| **SA-14** | Hypothesis property testing over arbitrary chunk sizes and intervals; combined 15,000-row streaming benchmark | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa14_hypothesis_chunk_and_interval_properties`, `test_sa14_combined_15000_row_benchmark` |

---

## Review Focus Points (R1 to R7) Traceability

1. **R1 (Removal of member scanning & testzip):** `ZipFile.testzip()` completely removed; Central Directory and `[Content_Types].xml` validated in isolation. Validated in `test_sa07_zip_central_directory_only_and_no_testzip_or_open_called`.
2. **R2 (I/O Durability & Taxonomy):** Flush and fsync `OSError` exceptions are never swallowed; explicitly mapped to non-retryable `XlsxSnapshotStorageError`. Validated in `test_sa07_io_faults_write_flush_fsync_storage_taxonomy`.
3. **R3 (Exception Group Retention):** Coincident acquisition + cleanup, consumer + cleanup, and consumer + integrity + cleanup exceptions preserved using Python 3.13 `ExceptionGroup`/`BaseExceptionGroup`. Validated in `test_sa10_*`.
4. **R4 (Private Artifacts & Non-Regular File Policy):** POSIX mode `0700` for lease directory and `0600` for candidate and final files; non-regular files (FIFO, socket, device, directory) rejected before open with `XlsxSourcePolicyError`. Validated in `test_sa02_non_regular_file_rejection_policy` and `test_sa08_posix_private_file_permissions`.
5. **R5 (True Replacement Detection):** Promoted file identity (`st_dev`, `st_ino`, `st_size`, `st_mtime_ns`) tracked at promotion; post-lease check validates `lstat` mode (regular file, not symlink) and inode/device equality to catch `os.replace` even with identical bytes. Validated in `test_sa05_atomic_os_replace_at_every_race_point` and `test_sa09_atomic_os_replace_during_lease`.
6. **R6 (Sanitized Error Messages):** Constant generic exception messages prevent leaking sensitive paths, directories, or raw OS error text. Validated in `test_sa01_no_path_or_secret_leakage_in_messages_and_repr`.
7. **R7 (SA Evidence Expansion):** Full evidence for inaccessible/locked source (`test_sa04_inaccessible_file_permission_lock`), chunk-bounded streams without `read(-1)` (`test_sa06_bounded_stream_wrapper_verification`), concurrent disjoint acquisitions (`test_sa11_concurrent_disjoint_acquisitions`), and full workbook equality against WP-04 oracle (`test_sa12_end_to_end_reader_and_planner_oracle`).

---

## Coverage gaps

- None for local Linux platform.
- Windows execution and benchmark metrics are PENDING independent Codex CI execution on PR.

---

## Gate statement

- Gate G1 status remains `OPEN / IN PROGRESS`.
- Implementer status for WP-06 is `REQUEST_CHANGES` pending Codex PR review and merge.
