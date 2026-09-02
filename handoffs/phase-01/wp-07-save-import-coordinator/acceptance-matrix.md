# Acceptance Matrix: WP-07 Save Debounce, Coalescing, and Import Coordination

## Overview

- **Work Package:** `docs/work-packages/phase-01/WP-07-save-import-coordinator.md`
- **Governing Architecture & ADR:** `docs/adr/ADR-0002-windows-excel-agent.md`, `docs/adr/ADR-0008-streaming-xlsx-source-reader.md`, `docs/adr/ADR-0009-stable-xlsx-snapshot-acquisition.md`, `docs/adr/ADR-0010-save-import-coordinator.md`
- **Component Version:** `SAVE_IMPORT_COORDINATOR_VERSION = "save-import-coordinator.v1"`
- **Baseline Commit:** `55b965dc6781c9371045f2ecf905ec27f52b64a5`
- **Target Branch:** `antigravity/phase-01-save-import-coordinator`
- **Fixed Debounce:** `SAVE_DEBOUNCE_NS = 2_000_000_000` (2.0s quiet period)
- **Traceability References:** O-31, O-53, O-71, O-72, ADR-0010

---

## Detailed Requirement Verification (SC-01 to SC-16 and W7-R1-01 to W7-R1-06)

| Item ID | Requirement / Scope | Status | Verification & Evidence |
| :--- | :--- | :--- | :--- |
| **SC-01 / W7-R1-04** | Public API, version string `save-import-coordinator.v1`, immutable tokens/views, strict type checks, safe static error representations without file paths or secret leakage | PASS (Linux native) | `tests/test_save_import_coordinator.py::test_sc01_public_api_exports_and_version`, `test_sc01_source_read_attempt_immutability`, `test_sc01_safe_repr_no_path_leakage`, `test_sc01_save_coordinator_view_invariants`, `test_w7_r1_04_constant_safe_error_messages_no_path_or_secret_leakage` |
| **SC-02 / W7-R1-05** | Exact lexical host-native path comparison (`normpath`/`normcase`); ignores sibling `.tmp`, lock `~$`, conflict, archive, snapshot, and unrelated files; ignores directory and read-only notices; strict enum validation without string coercion | PASS (Linux native) | `tests/test_save_import_coordinator.py::test_sc02_configuration_path_validation`, `test_sc02_notification_path_and_kind_filtering`, `test_w7_r1_05_strict_types_for_time_and_enums` |
| **SC-03** | Move events into or out of target match; coordinator target path is strictly immutable and never follows renamed destination; acquisition always uses configured target | PASS (Linux native) | `tests/test_save_import_coordinator.py::test_sc03_move_into_and_out_of_target` |
| **SC-04 / W7-R1-05** | Idle construction without timer threads; fake monotonic clock support; deadline boundaries (+/-1ns); repeated notices reset quiet period to latest notice + 2s; backwards/invalid clock fails closed; exact integer type enforcement | PASS (Linux native) | `tests/test_save_import_coordinator.py::test_sc04_idle_construction_and_clock_boundaries`, `test_w7_r1_05_strict_types_for_time_and_enums` |
| **SC-05** | Thousands of rapid burst notices coalesce to a single pending reservation; past deadline creates exactly one attempt reservation without retry backlog | PASS (Linux native) | `tests/test_save_import_coordinator.py::test_sc05_burst_coalescing_and_past_deadline` |
| **SC-06 / W7-R1-02** | Concurrency safety under state lock; bounded barrier synchronization; token created atomically before state mutation with zero rollback corruption; foreign, copied, stale, or double tokens and invalid outcomes rejected | PASS (Linux native) | `tests/test_save_import_coordinator.py::test_sc06_concurrent_due_takes_yield_single_token`, `test_sc06_token_security_and_invalid_finish_outcomes`, `test_w7_r1_02_take_due_error_injection_rollback_and_subsequent_success`, `test_w7_r1_02_take_due_thread_contention_with_injected_error` |
| **SC-07** | Follow-up notices during active attempt coalesce to at most one follow-up intent; handles both already-expired and future deadlines upon attempt completion | PASS (Linux native) | `tests/test_save_import_coordinator.py::test_sc07_followup_notices_during_running_state` |
| **SC-08 / W7-R1-06** | Direct `XlsxSourceNotReadyError` triggers automatic retry at completion + 2s without fresh notice; complete end-to-end retry lifecycle from missing file to success validated | PASS (Linux native) | `tests/test_save_import_coordinator.py::test_sc08_source_not_ready_automatic_retry_scheduling`, `test_w7_r1_06_source_not_ready_to_success_retry_lifecycle_without_new_notice` |
| **SC-09** | Direct Reader rejection transitions cleanly to `IDLE` with no timer retry of rejected generation; fresh notice afterwards succeeds on fixed file | PASS (Linux native) | `tests/test_save_import_coordinator.py::test_sc09_reader_rejected_no_timer_retry_unless_newer_notice` |
| **SC-10 / W7-R1-01** | Faulted state preserves intent; explicit `resume_after_fault` schedules attempt; single-path finish execution preserves both reader cause and bookkeeping exception via `ExceptionGroup`; guard never leaks or mutates foreign tokens | PASS (Linux native) | `tests/test_save_import_coordinator.py::test_sc10_faulted_state_and_explicit_resume`, `test_w7_r1_01_reader_error_with_faulty_finish_preserves_both_causes`, `test_w7_r1_01_success_with_faulty_finish_fails_closed_no_false_success`, `test_w7_r1_01_base_exception_in_finish_propagates_and_faults_active_token`, `test_w7_r1_01_guard_does_not_mutate_new_or_foreign_token` |
| **SC-11** | Zero I/O and zero acquisition calls when no attempt is due; due attempt passes exact source path to acquisition and returns result only after clean lease exit | PASS (Linux native) | `tests/test_save_import_coordinator.py::test_sc11_read_due_source_zero_io_when_not_due` |
| **SC-12 / W7-R1-01** | End-to-end driver lifecycle with synthetic 4-sheet workbooks: success, source-not-ready, reader rejection, integrity failure, and cancellation retain typed causes and observe WP-06 ownership | PASS (Linux native) | `tests/test_save_import_coordinator.py::test_sc12_read_due_source_success_lifecycle`, `test_sc12_read_due_source_missing_file_source_not_ready`, `test_sc12_read_due_source_reader_rejected_corrupt_file`, `test_sc12_read_due_source_integrity_failure_faults_coordinator`, `test_sc12_cancellation_keyboard_interrupt_faults_coordinator` |
| **SC-13 / W7-R1-06** | Notification responsiveness during I/O and cleanup barriers; deterministic event barriers prove notices delivered while reader or lease cleanup is blocked complete before worker release | PASS (Linux native) | `tests/test_save_import_coordinator.py::test_sc13_notice_responsiveness_during_blocked_io`, `test_w7_r1_06_notice_responsiveness_during_blocked_lease_cleanup` |
| **SC-14** | Composition with independent WP-04 Planner oracle: proves zero Insert/Edit/Void for unchanged Raw across modified ZIP binary representation | PASS (Linux native) | `tests/test_save_import_coordinator.py::test_sc14_change_planner_oracle_zero_false_changes` |
| **SC-15 / W7-R1-03** | Cross-platform Hypothesis property-based state machine verification using host-native temporary paths; compares state, deadlines, pending flags, take counts, and finish counts against independent reference oracle | PASS (Linux native) | `tests/test_save_import_coordinator.py::test_sc15_hypothesis_property_state_machine_oracle` |
| **SC-16** | Workspace regression preservation: full test suite passing (306 passed, 2 platform-conditional skipped), memory and duration benchmark strictly within 15.0s / 128.0 MiB limit | PASS (Linux native) | `tests/test_save_import_coordinator.py` (34/34 passed), full suite 306 passed, 2 skipped |

---

## Coverage gaps

- **Linux Native:** 306 passed, 2 platform-conditional skipped Windows-handle tests (308 collected total); 34/34 coordinator tests passing; 15,000-row streaming benchmark measured at 11.71s / 59.96 MiB (strictly within 15.0s / 128.0 MiB ceiling).
- **Windows Runtime Platform:** Windows runtime execution is **PENDING** independent Codex CI execution on PR. Static type safety for Windows targets is verified via `mypy --platform win32` (Exit 0).

---

## Gate statement

Gate G1 remains `OPEN / IN PROGRESS`. WP-07 implementation and test evidence are complete and prepared for handoff and independent PR review. No unauthorized commits, pushes, merges, deployments, or roadmap modifications have been executed.
