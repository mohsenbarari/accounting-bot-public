# Acceptance Matrix: WP-06 Stable XLSX Snapshot Acquisition and Cleanup

## Overview

- **Work Package:** `docs/work-packages/phase-01/WP-06-stable-xlsx-snapshot-acquisition.md`
- **Governing Architecture & ADR:** `docs/adr/ADR-0002-windows-excel-agent.md`, `docs/adr/ADR-0008-streaming-xlsx-source-reader.md`, `docs/adr/ADR-0009-stable-xlsx-snapshot-acquisition.md`
- **Component Version:** `XLSX_SNAPSHOT_ACQUISITION_VERSION = "xlsx-snapshot-acquisition.v1"`
- **Baseline Commit:** `bc085acd8852e2293fdfcb786a7694fe96e93407`
- **Current Branch:** `antigravity/phase-01-stable-xlsx-snapshot-acquisition`
- **Remediation Scope:** Codex Round 2 Review Remediation (R2-01 to R2-08)

---

## Detailed Requirement Verification (SA-01 to SA-14 & R2-01 to R2-07)

| Item ID | Requirement / Scope | Status | Verification & Evidence |
| :--- | :--- | :--- | :--- |
| **SA-01** | Public API, version export, typed taxonomy & reason strings, sanitized error formatting without path/member leaks | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa01_public_api_exports_and_version`, `test_sa01_stable_xlsx_snapshot_invariants`, `test_sa01_error_taxonomy_reasons_and_retryability`, `test_sa01_no_path_or_secret_leakage_in_messages_and_repr` |
| **SA-02** | Two ordered observations; rejection of non-positive intervals, non-xlsx files, non-directory roots, string paths (R2-06), and initial symlinks (R2-02) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa02_two_observations_and_sleeper`, `test_sa02_invalid_arguments_and_policy_rejections`, `test_sa02_initial_source_symlink_rejection`, `test_sa02_non_regular_file_rejection_policy` |
| **SA-03** | Source file read-only immutability (bytes, sha256, size, mtime_ns, permissions unchanged across all outcomes) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa03_source_immutability_on_success_and_failures` |
| **SA-04** | Missing, disappearing, or inaccessible source files raise retryable `XlsxSourceNotReadyError` | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa04_missing_and_disappearing_source_handling`, `test_sa04_inaccessible_file_permission_lock` |
| **SA-05** | In-place mutation and atomic replacement via `os.replace` at every race window abort acquisition cleanly with retryable error; same-inode symlink swap detected (R2-02) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa05_race_mutation_fault_injections[...]` (5 stages), `test_sa05_atomic_os_replace_at_every_race_point[...]` (5 stages), `test_sa05_source_symlink_race_mutation_with_same_inode` |
| **SA-06** | Streaming copy with bounded chunks (`_copy_chunk_size`), no unbounded `read(-1)`, exact byte count and SHA-256 computation | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa06_streaming_copy_bounded_chunks`, `test_sa06_bounded_stream_wrapper_verification` |
| **SA-07** | Fast central directory structure and `[Content_Types].xml` verification without `testzip()`; candidate symlink rejection leaving external target untouched (R2-01); pre-existing final snapshot not overwritten or deleted (R2-01); real stream read/write/flush/fsync errors mapped with correct taxonomy (R2-04) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa07_zip_central_directory_only_and_no_testzip_or_open_called`, `test_sa07_invalid_container_and_storage_faults`, `test_sa07_candidate_symlink_rejection_leaves_target_untouched`, `test_sa07_pre_existing_final_snapshot_not_overwritten_or_deleted`, `test_sa07_real_stream_io_faults_and_short_write` |
| **SA-08** | Promoted snapshot `.xlsx` leased exclusively to consumer; original source mutation isolated; private 0700/0600 POSIX permissions; snapshot_path never resolves externally (R2-01) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa08_promoted_path_and_source_mutation_isolation`, `test_sa08_posix_private_file_permissions`, `test_sa08_snapshot_path_never_resolves_externally` |
| **SA-09** | Snapshot modification, deletion, `os.replace` during lease detected on Linux & Windows with replacement file preserved (R2-03), or symlink replacement (R2-01) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa09_lease_mutation_and_deletion_detection`, `test_sa09_atomic_os_replace_during_lease_preserves_replacement_file`, `test_sa09_symlink_replacement_during_lease` |
| **SA-10** | Acquisition and lease cleanup failure visibility; concurrent errors combined into `ExceptionGroup`/`BaseExceptionGroup` with underlying causes preserved (R2-05) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa10_acquisition_and_cleanup_coincident_exception_group`, `test_sa10_consumer_and_cleanup_coincident_exception_group`, `test_sa10_consumer_integrity_and_cleanup_coincident_exception_group` |
| **SA-11** | Concurrent acquisitions: SHA equality for identical sources, distinct SHAs for different sources, disjoint paths, independent exit lifecycle without race interference (R2-07) | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa11_concurrent_disjoint_acquisitions` |
| **SA-12** | Full 4-sheet synthetic workbook acquired and validated; 100% snapshot and hash parity against independent WP-04 / WP-03 oracle | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa12_end_to_end_reader_and_planner_oracle` |
| **SA-13** | Source mutation prevents invalid/partial change plan construction or execution voids | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa13_source_change_prevents_partial_planner_voids` |
| **SA-14** | Hypothesis property testing over arbitrary chunk sizes and intervals; combined 15,000-row streaming benchmark | PASS | `tests/test_xlsx_snapshot_acquisition.py::test_sa14_hypothesis_chunk_and_interval_properties`, `test_sa14_combined_15000_row_benchmark` |

---

## Review Focus Points (R2-01 to R2-07) Traceability

1. **R2-01 (Artifact Ownership, No-Follow & Safe Cleanup):** Identity of lease directory, candidate, and final recorded and tracked via lstat/fstat; candidate symlink rejected without touching external targets; pre-existing final snapshot is never overwritten or deleted; cleanup unlinks strictly matching artifacts; unexpected replacements left untouched for forensics.
2. **R2-02 (Source Safety & Symlink No-Follow):** Initial symlinks rejected with `XlsxSourcePolicyError`; conversion of source to symlink at any race point aborts with `XlsxSourceNotReadyError`; POSIX `O_NOFOLLOW` and handle/path identity verification prevent following symlinks.
3. **R2-03 (Real Replacement Detection on Linux & Windows):** Cross-platform st_dev/st_ino/mtime/size checks detect `os.replace` during lease even with 100% identical content; replacement file is preserved on disk with `XlsxSnapshotIntegrityError` and `XlsxSnapshotCleanupError`.
4. **R2-04 (Precise I/O Error Taxonomy):** Source read errors mapped to `XlsxSourceNotReadyError` (`retryable=True`); candidate write, short-write, flush, fsync, and promotion errors mapped to `XlsxSnapshotStorageError` (`retryable=False`); underlying causes preserved in chained exceptions.
5. **R2-05 (Complete Cleanup Error Retention):** `_cleanup_managed_artifacts` preserves all underlying `OSError` instances as causes in `XlsxSnapshotCleanupError`; multi-exception combinations (acquisition+cleanup, consumer+cleanup, consumer+integrity+cleanup) verified with group flattening.
6. **R2-06 (Strict Path API & Sanitized Error Messages):** `open_stable_xlsx_snapshot` requires `Path` instances for `source_path` and `snapshot_root`; generic sanitized messages ensure secret filenames and directories are never leaked.
7. **R2-07 (Expanded SA Evidence & Oracle Parity):** Multi-concurrent acquisitions verify SHA parity on identical sources, distinct SHAs on different sources, and independent early exit; full 4-sheet workbook equality verified against independent WP-04 / WP-03 oracle.

---

## Coverage gaps

- None for local Linux platform.
- Windows runtime execution and benchmark metrics remain PENDING independent Codex CI execution on PR.

---

## Gate statement

- Gate G1 status remains `OPEN / IN PROGRESS`.
- Implementer status for WP-06 is `REQUEST_CHANGES` pending Codex PR review and merge.
