# Acceptance Matrix: WP-06 Stable XLSX Snapshot Acquisition and Cleanup

## Overview

- **Work Package:** `docs/work-packages/phase-01/WP-06-stable-xlsx-snapshot-acquisition.md`
- **Governing Architecture & ADR:** `docs/adr/ADR-0002-windows-excel-agent.md`, `docs/adr/ADR-0008-streaming-xlsx-source-reader.md`, `docs/adr/ADR-0009-stable-xlsx-snapshot-acquisition.md`
- **Component Version:** `XLSX_SNAPSHOT_ACQUISITION_VERSION = "xlsx-snapshot-acquisition.v1"`
- **Baseline Commit:** `bc085acd8852e2293fdfcb786a7694fe96e93407`
- **Current Branch:** `antigravity/phase-01-stable-xlsx-snapshot-acquisition`
- **Remediation Scope:** Codex Round 5 Review Remediation (R5-01 to R5-06)

---

## Detailed Requirement Verification (SA-01 to SA-14 & R5-01 to R5-06)

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

## Round 5 Refinements & Eight Verified Deterministic Oracles (R5-01 to R5-06)

1. **Oracle 1 — Candidate fstat Failure Lifecycle (R5-02 & R5-05):** Injected candidate `os.fstat(dst_fd)` failure closes FD immediately with zero leaks (`FD delta = []`) and cleans up `snapshot_root` (`snapshot_root contents = []`). Verified in `test_r5_01_candidate_fstat_failure_fd_lifecycle_and_leak_oracle`.
2. **Oracle 2 — First Lease lstat Failure Cleanup (R5-02 & R5-05):** Injected one-shot failure on initial `lease_dir.lstat()` leaves no orphaned directory (`snapshot_root contents = []`). Verified in `test_r5_02_first_lease_lstat_failure_without_replacement_oracle`.
3. **Oracle 3 — Early Lease Replacement Protection (R5-01, R5-02 & R5-05):** Foreign pre-existing candidate/lease entity is preserved on disk (`foreign survives = True`) and reports visible `XlsxSnapshotCleanupError`. Verified in `test_r5_03_early_lease_replacement_foreign_survives_oracle`.
4. **Oracle 4 — Partial POSIX Promotion Cleanup (R5-03 & R5-05):** When `os.link(part, final)` succeeds and `part.unlink()` fails, acquisition raises `XlsxSnapshotStorageError` and cleanup removes both `part` and `final` (`snapshot_root contents = []`). Verified in `test_r5_04_partial_posix_promotion_cleanup_oracle`.
5. **Oracle 5 — Lease lstat/open Race Taxonomy (R5-04 & R5-05):** Race deletion between lease-exit `lstat` and `open` maps to `XlsxSnapshotIntegrityError` without top-level `XlsxSnapshotStorageError`. Verified in `test_r5_05_lease_lstat_open_race_taxonomy_oracle`.
6. **Oracle 6 — Inode Reuse Conservative Fingerprinting (R5-01 & R5-05):** Inode reuse with foreign payload is detected via size/digest check on handle; foreign file survives deletion and reports `XlsxSnapshotCleanupError`. Verified in `test_r5_06_inode_reuse_protection_oracle`.
7. **Oracle 7 — Candidate mtime_ns = 0 in Context (R5-05):** Candidate with real `mtime_ns = 0` verified in context (`snap.snapshot_path.stat().st_mtime_ns == 0`) and subsequent mutations raise `XlsxSnapshotIntegrityError`. Verified in `test_r5_07_real_mtime_zero_integrity_oracle`.
8. **Oracle 8 — Real Identity Unavailable Provider Fallback (R5-05):** Provider returning `(None, None)` executes all operations cleanly and removes artifacts on normal completion. Verified in `test_r5_08_real_identity_unavailable_fallback_oracle`.

---

## Coverage gaps

- None for local Linux platform.
- Windows runtime execution and benchmark metrics remain PENDING independent Codex CI execution on PR.

---

## Gate statement

- Gate G1 status remains `OPEN / IN PROGRESS`.
- Implementer status for WP-06 is `REQUEST_CHANGES` pending Codex PR review and merge.
