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
| **WR-01** | Version string `source-watch-runtime.v1`, exports, frozen `SourceWatchRuntimeView`, safe static string representations; invalid constructor policy (type, parent, `.xlsx`/lock-name, relative segments `'..'`/`'.'`), native case and snapshot-root containment boundaries; non-callable consumer rejection and terminal run transitions without state corruption or I/O. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr01_public_api_exports_and_version`; `tests/test_source_watch_runtime.py::test_wr01_source_watch_runtime_view_invariants`; `tests/test_source_watch_runtime.py::test_wr01_safe_repr_no_path_leakage`; `tests/test_source_watch_runtime.py::test_wr01_constructor_lexical_validation_and_containment`; `tests/test_source_watch_runtime.py::test_wr01_consumer_validation_and_invalid_transition` |
| **WR-02** | Captured backend factory proves exactly one Observer scheduled with exact source parent path and `recursive=False`; constructor and stop-before-run allocate nothing; source may be absent at start. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr02_captured_observer_factory_and_no_alloc_on_init` |
| **WR-03** | Table-driven event adapter mapping: created, modified, deleted, moved (both directions); ignores unrelated/temp/conflict files, directory events, read-only events, and unknown event types (`future_event`); decodes native paths without filesystem lookups; single delivery per dispatch via overridden `dispatch()`. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr03_watchdog_event_adapter_table_driven_mapping` |
| **WR-04** | Missing/malformed mutating paths, non-boolean `is_directory` fields, and coordinator callback exceptions are surfaced; first callback failure closes admission, wakes run loop, and later callbacks cannot mutate scheduling. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr04_callback_boundary_fault_handling` |
| **WR-05** | Initial hint occurs after successful start, never on failed or pre-stopped start; real startup notices coalesce, and a preexisting synthetic file is read without additional filesystem events. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr05_initial_hint_and_preexisting_file_read` |
| **WR-06** | Fake monotonic clock and controlled waiter prove deadline minus 1 ns, exact deadline, latest-event debounce, spurious wake handling, and idle liveness wait; zero spin, timer-per-event, or no-due file I/O. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr06_fake_clock_deadline_boundaries_and_liveness_wait` |
| **WR-07** | Deterministic dual-barrier tests place notice, stop, and fault between predicate inspection and waiting; no lost wakes; stop versus cycle/consumer admission has a recorded winner and loser does not mutate or launch work. Winner held at barrier while asserting loser outcome before release; all runner exceptions propagated. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr07_barrier_predicates_and_stop_race_resolution` |
| **WR-08** | Four-phase separate blocking of Acquisition, Reader, Cleanup, and Consumer with 2,000 burst notices each; notifications remain responsive; state bounded; no second cycle admitted during consumer execution; exactly 1 follow-up read delivered per phase. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr08_burst_coalescing_and_responsiveness_during_blocking` |
| **WR-09** | Unchanged WP-07 driver is used; direct `XlsxSourceNotReadyError` retries at completion + 2s without fresh notice; direct `XlsxSourceReadError` (Reader rejection) enters idle state and does not retry without fresh notice, while preserving follow-up notice sent during rejected attempt; no callback or result delivered on failure. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr09_driver_error_handling_and_retry_preservation` |
| **WR-10** | Successful result delivered once on run caller's thread after lease exit and snapshot cleanup; stop during an admitted read drains that delivery; asynchronous fault suppresses unadmitted consumer. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr10_consumer_delivery_and_stop_draining` |
| **WR-11** | Concurrent run calls allocate exactly one backend; stop-before-run, stop during startup, idle, read, and consumer; repeated stop and consumer calling stop; all terminal run calls rejected. Runner thread exceptions captured and re-asserted in test thread. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr11_concurrent_run_calls_and_lifecycle_races` |
| **WR-12** | Observer factory, schedule, and start failures, including one worker started before failure and cleared from set, receive complete teardown and thread joins. Inaccessible parent is safe and never creates the watched directory. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr12_partial_startup_failure_teardown` |
| **WR-13** | Unexpected observer or emitter death detected at loop boundary and fails visibly (`OBSERVER_STOPPED_UNEXPECTEDLY`); planned shutdown is not misclassified; missing emitter set detected; liveness verified before initial hint. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr13_dispatcher_and_emitter_liveness_failure` |
| **WR-14** | Multi-failure preservation and ExceptionGroup / BaseExceptionGroup support: pre-existing cause on primary + async error preserved in `BaseExceptionGroup([primary, async_wrapper])` (R2 Round 3); shared cause between driver and stop preserved in `[driver_ki, stop_se]` order (R2.a); async error during start + stop failure grouped in `ExceptionGroup([async_wrapper, stop_wrapper])` (R2.b); raw `KeyboardInterrupt` in coordinator init passes through raw (R2.c); single BaseExceptions pass raw. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr14_multi_failure_preservation_and_exception_groups` |
| **WR-15** | Native Linux/Windows integration: startup read of a preexisting workbook, in-place Save overwrite, and atomic file replacement at same target with distinct generations; eventual results match synthetic content and active leases are cleaned. Runner thread exceptions captured and asserted. | PASS (Linux native) | `tests/test_source_watch_runtime_native.py::test_wr15_native_startup_read_and_inplace_and_atomic_save` |
| **WR-16** | Native Linux/Windows integration: source absent at startup then created; delete/recreate and move away/into target; irrelevant and lock files ignored; clean stop leaves zero owned threads alive. Runner thread exceptions captured and asserted. | PASS (Linux native) | `tests/test_source_watch_runtime_native.py::test_wr16_native_absent_source_lifecycle_and_thread_cleanup` |
| **WR-17** | End-to-end integration with independent WP-04 Planner oracle: row order swapping produces 0 planned changes (5 `UNCHANGED`), formula/cache-only summary XML modification produces 0 planned changes (5 `UNCHANGED`), and raw edits produce exact expected planned changes (1 `EDIT`, 4 `UNCHANGED`, matching canonical UUID and new source hash). Runner thread exceptions captured and asserted. | PASS (Linux native) | `tests/test_source_watch_runtime_native.py::test_wr17_end_to_end_wp04_planner_composition` |
| **WR-18** | Full existing suite retained (335 collected tests), Ruff check/format clean, native and win32 mypy clean, handoff validator passed, zero sensitive/prohibited files, clean diff, and streaming benchmarks well within limits. | PASS (Linux native) | Full repository suite: 333 passed, 2 platform-conditional skipped (335 collected); WP-05 benchmark 11.61s / 62.03 MiB; WP-06 benchmark 11.40s / 61.62 MiB. |

---

## Review Findings Resolution (Round 3: R2, R7, R8)

| Item | Requirement & Root Cause | Resolution & Verification |
| :--- | :--- | :--- |
| **R2 (Round 3)** | **Error Origin Disentanglement:** Deduplication in teardown mistakenly compared `__cause__` equality (`getattr(e, "__cause__", None) is async_err`), causing independent errors with pre-existing causes (e.g. `primary.__cause__ = async_original`) to drop the async error. | In `source_watch_runtime.py`, startup async error detection cleanly returns to teardown; step 7 deduplication strictly checks exact object identity (`e is final_async_err or e is async_err`). Verified in `test_wr14` producing `BaseExceptionGroup([primary_ki, async_wrapper])` with `primary_ki.__cause__ is async_original` and `async_wrapper.__cause__ is async_original`. |
| **R2.a/b/c** | Shared cause between Driver error and Stop error, async error during start with stop failure, and coordinator init cancellation. | All verified in `test_wr14`: shared cause preserved in `BaseExceptionGroup([driver_ki, stop_se])`; single async start error raises single wrapper; async start + stop failure raises `ExceptionGroup([async_wrapper, stop_wrapper])`; coordinator constructor cancellation passes raw `KeyboardInterrupt`. |
| **R7 (Round 3)** | **Complete Scenario Execution & Runner Propagation:** Dual barrier arrival/release synchronization, 4-phase blocking (Acq, Reader, Cleanup, Consumer), Reader rejection without/with follow-up, and all background runner exceptions captured and asserted. | `test_wr07` rewritten with arrival/release events; `test_wr08` tests 2,000 burst notices across all 4 blocking phases; `test_wr09` tests Reader rejection and follow-up preservation; `test_wr15..17` and unit tests wrap all runner threads with `runner_err` capture and `assert runner_err is None`, eliminating unhandled thread warnings and ensuring test failure on thread mutation. |
| **R8 (Round 3)** | **Handoff Evidence & Safe Rollback:** Accurate node IDs, raw execution logs, exact SHA from `git rev-parse HEAD`, separate native vs CI status, and safe rollback without invalid baseline checkout of new files. | Handoff regenerated with exact raw logs, node IDs, benchmark measurements, and precise file deletion/checkout rollback instructions. |

---

## Coverage gaps

- **Linux Native:** 333 passed, 2 platform-conditional skipped Windows-handle tests (335 collected total); 21/21 WP-08 tests passing; 15,000-row streaming benchmarks measured at 11.61s / 62.03 MiB (WP-05) and 11.40s / 61.62 MiB (WP-06) (strictly within 15.0s / 128.0 MiB ceilings).
- **Windows Runtime Platform:** Windows native runtime execution is **PENDING** independent Codex CI execution on PR. Static type safety for Windows targets is verified via `mypy --platform win32` (Exit 0).

---

## Gate statement

Gate G1 remains `OPEN / IN PROGRESS`. WP-08 implementation, remediation of all review items (R1 through R8), native OS integration proofs, and test evidence are complete and prepared for handoff and independent PR review. No unauthorized commits, pushes, merges, deployments, or roadmap modifications have been executed.
