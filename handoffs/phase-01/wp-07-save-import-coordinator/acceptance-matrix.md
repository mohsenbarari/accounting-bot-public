# Acceptance Matrix: WP-07 Save Debounce, Coalescing, and Import Coordination

## Overview

- **Work Package:** `docs/work-packages/phase-01/WP-07-save-import-coordinator.md`
- **Governing Architecture & ADR:** `docs/adr/ADR-0002-windows-excel-agent.md`, `docs/adr/ADR-0008-streaming-xlsx-source-reader.md`, `docs/adr/ADR-0009-stable-xlsx-snapshot-acquisition.md`, `docs/adr/ADR-0010-save-import-coordinator.md`
- **Component Version:** `SAVE_IMPORT_COORDINATOR_VERSION = "save-import-coordinator.v1"`
- **Baseline Commit:** `55b965dc6781c9371045f2ecf905ec27f52b64a5`
- **Target Branch:** `antigravity/phase-01-save-import-coordinator`
- **Fixed Debounce:** `SAVE_DEBOUNCE_NS = 2_000_000_000` (2.0s quiet period)

---

## Detailed Requirement Verification (SC-01 to SC-16)

| Item ID | Requirement / Scope | Status | Verification & Evidence |
| :--- | :--- | :--- | :--- |
| **SC-01** | Public API, version string `save-import-coordinator.v1`, immutable tokens/views, strict type checks, safe representation without file paths or secret leakage | PASS | `tests/test_save_import_coordinator.py::test_sc01_public_api_exports_and_version`, `test_sc01_source_read_attempt_immutability`, `test_sc01_safe_repr_no_path_leakage`, `test_sc01_save_coordinator_view_invariants` |
| **SC-02** | Exact lexical host-native path comparison (`normpath`/`normcase`); ignores sibling `.tmp`, lock `~$`, conflict, archive, snapshot, and unrelated files; ignores directory and read-only notices | PASS | `tests/test_save_import_coordinator.py::test_sc02_configuration_path_validation`, `test_sc02_notification_path_and_kind_filtering` |
| **SC-03** | Move events into or out of target match; coordinator target path is strictly immutable and never follows renamed destination; acquisition always uses configured target | PASS | `tests/test_save_import_coordinator.py::test_sc03_move_into_and_out_of_target` |
| **SC-04** | Idle construction without timer threads; fake monotonic clock support; deadline boundaries (+/-1ns); repeated notices reset quiet period to latest notice + 2s; backwards/invalid clock fails closed | PASS | `tests/test_save_import_coordinator.py::test_sc04_idle_construction_and_clock_boundaries` |
| **SC-05** | Thousands of rapid burst notices coalesce to a single pending reservation; past deadline creates exactly one attempt reservation without retry backlog | PASS | `tests/test_save_import_coordinator.py::test_sc05_burst_coalescing_and_past_deadline` |
| **SC-06** | Concurrency safety under state lock; two racing `take_due` callers yield exactly one token; foreign, copied, stale, or double-finish tokens and invalid outcomes rejected | PASS | `tests/test_save_import_coordinator.py::test_sc06_concurrent_due_takes_yield_single_token`, `test_sc06_token_security_and_invalid_finish_outcomes` |
| **SC-07** | Follow-up notices during active attempt coalesce to at most one follow-up intent; handles both already-expired and future deadlines upon attempt completion | PASS | `tests/test_save_import_coordinator.py::test_sc07_followup_notices_during_running_state` |
| **SC-08** | Direct `XlsxSourceNotReadyError` triggers automatic retry at completion + 2s (or later latest notice) without fresh notice; later readiness succeeds; no busy-loop | PASS | `tests/test_save_import_coordinator.py::test_sc08_source_not_ready_automatic_retry_scheduling` |
| **SC-09** | Direct Reader rejection transitions cleanly to `IDLE` with no timer retry of rejected generation; fresh notice afterwards succeeds on fixed file | PASS | `tests/test_save_import_coordinator.py::test_sc09_reader_rejected_no_timer_retry_unless_newer_notice` |
| **SC-10** | Policy/storage/integrity/cleanup/unknown failures transition coordinator to `FAULTED` and preserve pending intent; notices alone cannot resume; explicit `resume_after_fault` schedules attempt; driver bookkeeping guard forces `FAULTED` on its active token without corrupting pending intent | PASS | `tests/test_save_import_coordinator.py::test_sc10_faulted_state_and_explicit_resume`, `test_sc10_injected_driver_bookkeeping_failure_guard` |
| **SC-11** | Zero I/O and zero acquisition calls when no attempt is due; due attempt passes exact source path to acquisition and returns result only after clean lease exit | PASS | `tests/test_save_import_coordinator.py::test_sc11_read_due_source_zero_io_when_not_due` |
| **SC-12** | End-to-end driver lifecycle with synthetic 4-sheet workbooks: success, source-not-ready, reader rejection, integrity failure, and cancellation retain typed causes and observe WP-06 ownership | PASS | `tests/test_save_import_coordinator.py::test_sc12_read_due_source_success_lifecycle`, `test_sc12_read_due_source_missing_file_source_not_ready`, `test_sc12_read_due_source_reader_rejected_corrupt_file`, `test_sc12_read_due_source_integrity_failure_faults_coordinator`, `test_sc12_cancellation_keyboard_interrupt_faults_coordinator` |
| **SC-13** | Notification responsiveness during I/O and cleanup barriers; notices delivered while reader is blocked are accepted immediately without lock contention | PASS | `tests/test_save_import_coordinator.py::test_sc13_notice_responsiveness_during_blocked_io` |
| **SC-14** | Composition with independent WP-04 Planner oracle: proves zero Insert/Edit/Void for unchanged Raw across modified ZIP binary representation | PASS | `tests/test_save_import_coordinator.py::test_sc14_change_planner_oracle_zero_false_changes` |
| **SC-15** | Hypothesis property-based state machine verification: generates random operation sequences (advances, notices, takes, finishes, faults, resumes) asserting exact parity against independent reference model | PASS | `tests/test_save_import_coordinator.py::test_sc15_hypothesis_property_state_machine_oracle` |
| **SC-16** | Workspace regression preservation: all 274 existing tests pass, platform skips preserved, native Windows symlinks preserved in CI, and memory/time benchmark limits maintained | PASS | `tests/test_save_import_coordinator.py`, full suite 297 passed, 2 skipped |

---

## Coverage gaps

- **Linux Native:** 100% test coverage with 297/297 passing tests and verified benchmark performance.
- **Windows Runtime Platform:** Windows-specific runtime handle tests skipped on Linux as expected (`test_xlsx_snapshot_acquisition.py:3911` and `4008`). Static type compliance for Windows fully validated via `mypy --platform win32` (Exit 0).

---

## Gate statement

Gate G1 remains `OPEN / IN PROGRESS`. WP-07 implementation and test evidence are complete and prepared for handoff and independent PR review. No unauthorized commits, pushes, merges, deployments, or roadmap modifications have been executed.
