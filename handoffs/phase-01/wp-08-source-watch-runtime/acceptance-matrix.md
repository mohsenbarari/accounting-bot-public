# Acceptance Matrix: WP-08 Managed Source Watcher and Serial Read Runtime

## Overview

- **Work Package:** `docs/work-packages/phase-01/WP-08-source-watch-runtime.md`
- **Governing Architecture & ADR:** `docs/adr/ADR-0002-windows-excel-agent.md`, `docs/adr/ADR-0008-streaming-xlsx-source-reader.md`, `docs/adr/ADR-0009-stable-xlsx-snapshot-acquisition.md`, `docs/adr/ADR-0010-save-import-coordinator.md`, `docs/adr/ADR-0011-source-watch-runtime.md`
- **Component Version:** `SOURCE_WATCH_RUNTIME_VERSION = "source-watch-runtime.v1"`
- **Execution Baseline:** `c7520c94606f37720f3e023b753e36d1c1f433a3`
- **Target Branch:** `antigravity/phase-01-source-watch-runtime`
- **Fixed Debounce:** `SAVE_DEBOUNCE_NS = 2_000_000_000` (2.0s quiet period)
- **Traceability References:** Roadmap 5.1, 19.1, O-53, O-70, O-71, O-72, O-73, ADR-0011

---

## Detailed Requirement Verification (WR-01 to WR-18)

| Item ID | Requirement / Scope | Status | Verification & Evidence |
| :--- | :--- | :--- | :--- |
| **WR-01** | Version string `source-watch-runtime.v1`, exports, frozen `SourceWatchRuntimeView`, safe static string representations; invalid constructor policy (type, parent, `.xlsx`/lock-name, native case, snapshot-root containment); non-callable consumer rejection and terminal run transitions without state corruption or I/O. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr01_public_api_exports_and_version`, `test_wr01_source_watch_runtime_view_invariants`, `test_wr01_safe_repr_no_path_leakage`, `test_wr01_constructor_lexical_validation_and_containment`, `test_wr01_consumer_validation_and_invalid_transition` |
| **WR-02** | Captured backend factory proves exactly one Observer scheduled with exact source parent path and `recursive=False`; constructor and stop-before-run allocate nothing; source may be absent at start. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr02_captured_observer_factory_and_no_alloc_on_init` |
| **WR-03** | Table-driven event adapter mapping: created, modified, deleted, moved (both directions); ignores unrelated/temp/conflict files, directory events, and read-only/unknown kinds; decodes native paths without filesystem lookups; single delivery per dispatch. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr03_watchdog_event_adapter_table_driven_mapping` |
| **WR-04** | Missing/malformed mutating paths, invalid fields, and coordinator callback exceptions are surfaced; first callback failure closes admission, wakes run loop, and later callbacks cannot mutate scheduling. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr04_callback_boundary_fault_handling` |
| **WR-05** | Initial hint occurs after successful start, never on failed or pre-stopped start; real startup notices coalesce, and a preexisting synthetic file is read without additional filesystem events. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr05_initial_hint_and_preexisting_file_read` |
| **WR-06** | Fake monotonic clock and controlled waiter prove deadline minus 1 ns, exact deadline, latest-event debounce, spurious wake handling, and idle liveness wait; zero spin, timer-per-event, or no-due file I/O. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr06_fake_clock_deadline_boundaries_and_liveness_wait` |
| **WR-07** | Deterministic barrier tests place notice, stop, and fault between predicate inspection and waiting; no lost wakes; stop versus cycle/consumer admission has a recorded winner and loser does not mutate or launch work. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr07_barrier_predicates_and_stop_race_resolution` |
| **WR-08** | Burst of 2,000 deterministic notices coalesces with bounded runtime state; notifications remain responsive while acquisition, Reader, cleanup, or consumer blocks; no second cycle admitted during consumer execution. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr08_burst_coalescing_and_responsiveness_during_blocking` |
| **WR-09** | Unchanged WP-07 driver is used; direct `XlsxSourceNotReadyError` retries at completion + 2s without fresh notice; direct `XlsxSourceReadError` waits for fresh input while preserving existing follow-up; no callback or result on failure. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr09_driver_error_handling_and_retry_preservation` |
| **WR-10** | Successful result delivered once on run caller's thread after lease exit and snapshot cleanup; stop during an admitted read drains that delivery; asynchronous fault suppresses unadmitted consumer. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr10_consumer_delivery_and_stop_draining` |
| **WR-11** | Concurrent run calls allocate exactly one backend; stop-before-run, stop during startup, idle, read, and consumer; repeated stop and consumer calling stop; all terminal run calls rejected. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr11_concurrent_run_calls_and_lifecycle_races` |
| **WR-12** | Observer factory/schedule/start failures, including one worker started before failure, receive complete teardown and thread joins. Inaccessible parent is safe and never creates the watched directory. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr12_partial_startup_failure_teardown` |
| **WR-13** | Unexpected observer or emitter death detected at loop boundary and fails visibly; planned shutdown is not misclassified; blocked-I/O liveness limitation recorded. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr13_dispatcher_and_emitter_liveness_failure` |
| **WR-14** | Multi-failure preservation and ExceptionGroup / BaseExceptionGroup support; simultaneous driver, consumer, callback, and teardown errors retain independent causes and failed terminal state without masking primary error. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr14_multi_failure_preservation_and_exception_groups` |
| **WR-15** | Native Linux/Windows integration: startup read of a preexisting workbook, in-place Save overwrite, and atomic file replacement at same target with distinct generations; eventual results match synthetic content and active leases are cleaned. | PASS (Linux native) | `tests/test_source_watch_runtime_native.py::test_wr15_native_startup_read_and_inplace_and_atomic_save` |
| **WR-16** | Native Linux/Windows integration: source absent at startup then created; delete/recreate and move away/into target; irrelevant and lock files ignored; clean stop leaves zero owned threads alive. | PASS (Linux native) | `tests/test_source_watch_runtime_native.py::test_wr16_native_absent_source_lifecycle_and_thread_cleanup` |
| **WR-17** | End-to-end integration with independent WP-04 Planner oracle: row order swapping and formula/cache-only modifications produce zero planned changes; raw edits produce exact expected changes. | PASS (Linux native) | `tests/test_source_watch_runtime_native.py::test_wr17_end_to_end_wp04_planner_composition` |
| **WR-18** | Full existing suite retained (334 collected tests), Ruff check/format clean, native and win32 mypy clean, handoff validator passed, zero sensitive/prohibited files, clean diff, and streaming benchmarks well within limits. | PASS (Linux native) | Full repository suite: 332 passed, 2 platform-conditional skipped (334 collected); WP-05 benchmark 11.38s / 61.82 MiB; WP-06 benchmark 10.83s / 61.24 MiB. |

---

## Coverage gaps

- **Linux Native:** 332 passed, 2 platform-conditional skipped Windows-handle tests (334 collected total); 20/20 WP-08 tests passing; 15,000-row streaming benchmarks measured at 11.38s / 61.82 MiB (WP-05) and 10.83s / 61.24 MiB (WP-06) (strictly within 15.0s / 128.0 MiB ceilings).
- **Windows Runtime Platform:** Windows runtime execution is **PENDING** independent Codex CI execution on PR. Static type safety for Windows targets is verified via `mypy --platform win32` (Exit 0).

---

## Gate statement

Gate G1 remains `OPEN / IN PROGRESS`. WP-08 implementation, native OS integration proofs, and test evidence are complete and prepared for handoff and independent PR review. No unauthorized commits, pushes, merges, deployments, or roadmap modifications have been executed.
