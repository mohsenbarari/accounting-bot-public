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
| **WR-01** | Version string `source-watch-runtime.v1`, exports, frozen `SourceWatchRuntimeView`, safe static string representations; invalid constructor policy (type, parent, `.xlsx`/lock-name, relative segments `'..'`/`'.'`), native case and snapshot-root containment boundaries; non-callable consumer rejection and terminal run transitions without state corruption or I/O. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr01_public_api_exports_and_version`, `tests/test_source_watch_runtime.py::test_wr01_source_watch_runtime_view_invariants`, `tests/test_source_watch_runtime.py::test_wr01_safe_repr_no_path_leakage`, `tests/test_source_watch_runtime.py::test_wr01_constructor_lexical_validation_and_containment`, `tests/test_source_watch_runtime.py::test_wr01_consumer_validation_and_invalid_transition` |
| **WR-02** | Captured backend factory proves exactly one Observer scheduled with exact source parent path and `recursive=False`; constructor and stop-before-run allocate nothing; source may be absent at start. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr02_captured_observer_factory_and_no_alloc_on_init` |
| **WR-03** | Table-driven event adapter mapping: created, modified, deleted, moved (both directions); ignores unrelated/temp/conflict files, directory events, read-only events, and unknown event types (`future_event`); decodes native paths without filesystem lookups; single delivery per dispatch via overridden `dispatch()`. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr03_watchdog_event_adapter_table_driven_mapping` |
| **WR-04** | Missing/malformed mutating paths, non-boolean `is_directory` fields, and coordinator callback exceptions are surfaced; first callback failure closes admission, wakes run loop, and later callbacks cannot mutate scheduling. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr04_callback_boundary_fault_handling` |
| **WR-05** | Initial hint occurs after successful start, never on failed or pre-stopped start; real startup notices coalesce, and a preexisting synthetic file is read without additional filesystem events. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr05_initial_hint_and_preexisting_file_read` |
| **WR-06** | Fake monotonic clock and controlled waiter prove deadline minus 1 ns, exact deadline, latest-event debounce, spurious wake handling, and idle liveness wait; zero spin, timer-per-event, or no-due file I/O. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr06_fake_clock_deadline_boundaries_and_liveness_wait` |
| **WR-07** | Deterministic barrier tests place notice, stop, and fault between predicate inspection and waiting; no lost wakes; stop versus cycle/consumer admission has a recorded winner and loser does not mutate or launch work. Held-at-barrier assertions verify loser outcome before winner release; thread errors propagated. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr07_barrier_predicates_and_stop_race_resolution` |
| **WR-08** | Burst of 2,000 deterministic notices coalesces with bounded runtime state; notifications remain responsive while acquisition, Reader, cleanup, or consumer blocks; no second cycle admitted during consumer execution; exactly 1 follow-up read is delivered. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr08_burst_coalescing_and_responsiveness_during_blocking` |
| **WR-09** | Unchanged WP-07 driver is used; direct `XlsxSourceNotReadyError` retries at completion + 2s without fresh notice; direct `XlsxSourceReadError` (Reader rejection) enters idle state and does not retry without fresh notice, while preserving follow-up notice sent during rejected attempt; no callback or result delivered on failure. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr09_driver_error_handling_and_retry_preservation` |
| **WR-10** | Successful result delivered once on run caller's thread after lease exit and snapshot cleanup; stop during an admitted read drains that delivery; asynchronous fault suppresses unadmitted consumer. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr10_consumer_delivery_and_stop_draining` |
| **WR-11** | Concurrent run calls allocate exactly one backend; stop-before-run, stop during startup, idle, read, and consumer; repeated stop and consumer calling stop; all terminal run calls rejected. Runner thread exceptions captured and re-asserted in test thread. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr11_concurrent_run_calls_and_lifecycle_races` |
| **WR-12** | Observer factory/schedule/start failures, including one worker started before failure and cleared from set, receive complete teardown and thread joins. Inaccessible parent is safe and never creates the watched directory. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr12_partial_startup_failure_teardown` |
| **WR-13** | Unexpected observer or emitter death detected at loop boundary and fails visibly (`OBSERVER_STOPPED_UNEXPECTEDLY`); planned shutdown is not misclassified; missing emitter set detected; liveness verified before initial hint. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr13_dispatcher_and_emitter_liveness_failure` |
| **WR-14** | Multi-failure preservation and ExceptionGroup / BaseExceptionGroup support: independent failures with shared cause are preserved in exact order `[run_error, async_error, teardown_errors]` without deduplication dropping (R2.a); async error during start wrapped in `EVENT_DELIVERY_FAILED` with cause intact and no double counting with stop failure (R2.b); raw `KeyboardInterrupt` in coordinator init passes through raw (R2.c); single BaseExceptions pass raw; test fixture threads cleaned in finally. | PASS (Linux native) | `tests/test_source_watch_runtime.py::test_wr14_multi_failure_preservation_and_exception_groups` |
| **WR-15** | Native Linux/Windows integration: startup read of a preexisting workbook, in-place Save overwrite, and atomic file replacement at same target with distinct generations; eventual results match synthetic content and active leases are cleaned. Content generation polling with bounded deadline. | PASS (Linux native) | `tests/test_source_watch_runtime_native.py::test_wr15_native_startup_read_and_inplace_and_atomic_save` |
| **WR-16** | Native Linux/Windows integration: source absent at startup then created; delete/recreate and move away/into target; irrelevant and lock files ignored; clean stop leaves zero owned threads alive. | PASS (Linux native) | `tests/test_source_watch_runtime_native.py::test_wr16_native_absent_source_lifecycle_and_thread_cleanup` |
| **WR-17** | End-to-end integration with independent WP-04 Planner oracle: row order swapping produces 0 planned changes (5 `UNCHANGED`), formula/cache-only summary XML modification produces 0 planned changes (5 `UNCHANGED`), and raw edits produce exact expected planned changes (1 `EDIT`, 4 `UNCHANGED`, matching canonical UUID and new source hash). | PASS (Linux native) | `tests/test_source_watch_runtime_native.py::test_wr17_end_to_end_wp04_planner_composition` |
| **WR-18** | Full existing suite retained (335 collected tests), Ruff check/format clean, native and win32 mypy clean, handoff validator passed, zero sensitive/prohibited files, clean diff, and streaming benchmarks well within limits. | PASS (Linux native) | Full repository suite: 333 passed, 2 platform-conditional skipped (335 collected); WP-05 benchmark 11.06s / 61.52 MiB; WP-06 benchmark 12.08s / 61.40 MiB. |

---

## Review Findings Resolution (Round 2: R2, R7, R8)

| Item | Requirement & Root Cause | Resolution & Verification |
| :--- | :--- | :--- |
| **R2.a** | Independent failures with shared `__cause__` were dropped by `__cause__` deduplication check. | Dedup condition updated in `source_watch_runtime.py` to compare exact exception instances (`e is te`) rather than cause equality. Verified via `test_wr14` (Driver `KeyboardInterrupt` with mock `OSError` cause + Stop `SystemExit` with same mock `OSError` cause preserved in `BaseExceptionGroup([driver_ki, stop_se])`). |
| **R2.b** | Async error during start (e.g. coordinator raises `RuntimeError` during start event dispatch) was returned raw instead of wrapped in `SourceWatchRuntimeError(EVENT_DELIVERY_FAILED)`. | Loop exception handling in `source_watch_runtime.py` wraps `self._async_error` in `SourceWatchRuntimeError(EVENT_DELIVERY_FAILED, __cause__=original_error)` and deduplicates against teardown stop failures. Verified in `test_wr14`. |
| **R2.c** | `SaveImportCoordinator` constructor `try...except BaseException:` caught `KeyboardInterrupt` and converted it to `INVALID_POLICY`. | Constructor catch boundary changed to `except Exception:`, allowing `KeyboardInterrupt` and raw `BaseException` instances to pass through raw. Verified in `test_wr14`. |
| **R7** | Real barrier-synchronized tests, 2,000 burst blocking phases, Reader rejection without/with follow-up, and Formula/Cache XML generation with WP-04 Planner oracle. | `test_wr07` rewritten with `threading.Barrier` holding winner while asserting loser state; `test_wr08` tests 2,000 burst notices across all 4 blocking phases; `test_wr09` tests Reader rejection and follow-up preservation; `test_wr17` generates 4 distinct generations including Generation 3 formula/cache XML verified against WP-04 Planner oracle (0 changes). |
| **R8** | Reconstruct handoff bundle from raw execution logs, exact 21 node IDs, accurate git SHA via `git rev-parse HEAD`, separate native vs CI status, and targeted rollback list. | Handoff bundle regenerated with exact raw logs, node IDs, benchmark measurements, and precise file rollback instructions. |

---

## Coverage gaps

- **Linux Native:** 333 passed, 2 platform-conditional skipped Windows-handle tests (335 collected total); 21/21 WP-08 tests passing; 15,000-row streaming benchmarks measured at 11.06s / 61.52 MiB (WP-05) and 12.08s / 61.40 MiB (WP-06) (strictly within 15.0s / 128.0 MiB ceilings).
- **Windows Runtime Platform:** Windows native runtime execution is **PENDING** independent Codex CI execution on PR. Static type safety for Windows targets is verified via `mypy --platform win32` (Exit 0).

---

## Gate statement

Gate G1 remains `OPEN / IN PROGRESS`. WP-08 implementation, remediation of all review items (R1 through R8), native OS integration proofs, and test evidence are complete and prepared for handoff and independent PR review. No unauthorized commits, pushes, merges, deployments, or roadmap modifications have been executed.
