# Handoff: WP-08 Managed Source Watcher and Serial Read Runtime

## Identity

- **Work Package:** `WP-08: Managed source watcher and serial read runtime`
- **Phase:** `1 — source and data-model foundation`
- **Component Version:** `SOURCE_WATCH_RUNTIME_VERSION = "source-watch-runtime.v1"`
- **Baseline Commit:** `c7520c94606f37720f3e023b753e36d1c1f433a3`
- **Target Branch:** `antigravity/phase-01-source-watch-runtime`
- **Gate G1 Status:** `OPEN / IN PROGRESS`

## Scope

Delivers the managed source watcher and serial read runtime under ADR-0011 and WP-08, remediated for all review findings (R1 through R8):
1. **Public API & Version:** Implemented and exported `SOURCE_WATCH_RUNTIME_VERSION = "source-watch-runtime.v1"`, `SourceWatchRuntime`, `SourceWatchRuntimeState`, `SourceWatchRuntimeReason`, `SourceWatchRuntimeError`, and frozen `SourceWatchRuntimeView`.
2. **Lexical Validation & Non-Containment:** Strict constructor validation of `source_path` (.xlsx, non-lock `~$`, absolute, rejecting relative segments `'..'`/`'.'`) and `snapshot_root` with cross-platform containment enforcement.
3. **Table-Driven Watchdog Event Adapter:** Single `dispatch()` handler mapping file creations, modifications, deletions, and moves to `SaveImportCoordinator` notices; safely ignores directory events, temporary files, conflict files, read-only events, and unknown events (`future_event`) without filesystem I/O.
4. **Debounced Coordinator Runtime Loop:** Driver coordinates serial snapshot acquisition and reading through `read_due_source`; enqueues an initial `MODIFIED` hint upon startup; uses a 1.0s maximum idle wait for liveness checking.
5. **Synchronous Caller-Thread Delivery:** Delivers successful reader snapshots synchronously to the consumer on the run caller's thread after clean lease exit; drains active cycles gracefully upon `request_stop()`.
6. **Thread Lifecycle & Multi-Failure Grouping (R1/R2):** Strict worker tracking and joined teardown; deduplication strictly on exact instance identities, preserving independent errors with shared causes in `[run_error, async_error, *teardown_errors]` order; raw `BaseException` (like `KeyboardInterrupt`) preserved without wrapping.
7. **Native OS Integration & WP-04 Composition (WR-15..17):** Real filesystem watcher responsiveness for in-place saves, atomic replaces, missing-source creation, and 4 distinct generations including Generation 3 Formula/Cache XML verified against independent WP-04 Planner oracle (0 changes).

## Roadmap traceability

- **Decisions Implemented:** O-31 (Isolated snapshot lifecycle), O-53 (Windows Local Agent stack / execution architecture), O-70 (Streaming XLSX source reader), O-71 (Bounded memory & linear copy), O-72 (Save debounce & coalescing state machine), O-73 (Managed source watcher & runtime lifecycle), ADR-0011 (Source watch runtime architecture).
- **Roadmap Section 5.1 & 19.1:** Connects filesystem notifications to the save-import coordinator and read pipeline, establishing the native watcher runtime boundary for Phase 1.
- **Evidence Status:** Recorded under `test-results.txt` and `acceptance-matrix.md`.

## Review findings resolution summary (Round 2)

| Review Item | Description & Root Cause | Resolution |
| :--- | :--- | :--- |
| **R2.a** | Independent failures with shared `__cause__` were dropped by cause equality check. | Deduplication in `source_watch_runtime.py` updated to check exact exception instances (`e is te`). Both `[driver_ki, stop_se]` preserved in `BaseExceptionGroup`. |
| **R2.b** | Async error during start was returned raw instead of wrapped in `EVENT_DELIVERY_FAILED`. | `self._async_error` is wrapped in `SourceWatchRuntimeError(EVENT_DELIVERY_FAILED, __cause__=original_error)` and deduplicated against teardown stop failures. |
| **R2.c** | Coordinator init constructor catch converted `KeyboardInterrupt` to `INVALID_POLICY`. | Catch boundary changed to `except Exception:`, passing `KeyboardInterrupt` and raw `BaseException` instances through raw. |
| **R7** | Missing barrier synchronization, burst blocking phases, reader rejection retry semantics, and formula/cache XML generation. | `test_wr07` rewritten with `threading.Barrier`; `test_wr08` tests 2,000 burst notices across all 4 blocking phases; `test_wr09` tests reader rejection retry; `test_wr17` generates Generation 3 formula/cache XML and asserts 0 changes against WP-04 Planner oracle. |
| **R8** | Reconstruct handoff bundle from raw execution logs, exact 21 node IDs, accurate git SHA via `git rev-parse HEAD`, separate native vs CI status. | Handoff regenerated with exact raw logs, node IDs, benchmark measurements, and precise file rollback instructions. |

## Changed files

- `apps/local_agent/src/accounting_local_agent/source_watch_runtime.py`: Core managed source watcher runtime implementation, event adapter, lifecycle state machine, error taxonomy, worker tracking, liveness verification, atomic view transition in teardown, and runner loop.
- `apps/local_agent/src/accounting_local_agent/__init__.py`: Exported WP-08 public symbols (`SOURCE_WATCH_RUNTIME_VERSION`, `SourceWatchRuntime`, `SourceWatchRuntimeState`, `SourceWatchRuntimeReason`, `SourceWatchRuntimeError`, `SourceWatchRuntimeView`).
- `apps/local_agent/README.md`: Documented architecture, lifecycle transitions, exception hierarchy, and synthetic-only library usage example.
- `tests/test_source_watch_runtime.py`: Deterministic unit, barrier, race condition, fault injection, and exception grouping test suite (18 tests covering WR-01 through WR-14).
- `tests/test_source_watch_runtime_native.py`: Real native OS filesystem observer integration test suite (3 tests covering WR-15 through WR-17).
- `handoffs/phase-01/wp-08-source-watch-runtime/handoff.md`: Handoff summary and audit traceability.
- `handoffs/phase-01/wp-08-source-watch-runtime/acceptance-matrix.md`: Detailed requirement acceptance matrix (WR-01 to WR-18).
- `handoffs/phase-01/wp-08-source-watch-runtime/test-results.txt`: Direct command execution logs, quality scan records, and benchmark metrics.

## Schema and migrations

- None (WP-08 is purely a local agent filesystem watcher and serial read runtime component).

## Commands and exit codes

1. `uv sync --frozen --all-packages --all-groups` (Exit 0, 81 packages checked)
2. `uv lock --check` (Exit 0, 88 packages resolved)
3. `uv run ruff format --check .` (Exit 0, 74 files checked)
4. `uv run ruff check .` (Exit 0, all checks passed)
5. `uv run mypy .` (Exit 0, 28 source files checked)
6. `uv run mypy --platform win32 .` (Exit 0, 28 source files checked)
7. `uv run pytest tests/test_source_watch_runtime.py tests/test_source_watch_runtime_native.py -v` (Exit 0, 21 passed in 27.50s)
8. `uv run pytest -v` (Exit 0, 333 passed, 2 skipped in 65.82s)
9. `uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s` (Exit 0, 11.06s / 61.52 MiB)
10. `uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s` (Exit 0, 12.08s / 61.40 MiB)
11. `git diff --check origin/main...HEAD` (Exit 0, 0 whitespace errors)
12. `git ls-files | grep -E "(\.xlsx$|\.pdf$|\.db$|\.sqlite$|\.env$|secrets|credentials)"` (Exit 0 on check, 0 prohibited files)
13. `git grep -i -E "password|secret|api_key|private_key" -- apps/` (Exit 0 on check, 0 sensitive credentials)
14. `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-08-source-watch-runtime` (Exit 0, validation passed)

## Tests and evidence

- **Full repository suite:** 333 tests passing cleanly on Linux native (2 platform-conditional skipped Windows-handle tests; 335 collected total).
- **Watcher Runtime tests:** 21 dedicated unit, deterministic barrier, native OS observer, lifecycle race, and composition tests.
- **15,000-row streaming benchmarks:** WP-05 Reader at 11.06s duration / 61.52 MiB peak RSS; WP-06 Acquisition at 12.08s duration / 61.40 MiB peak RSS (strictly within 15.0s / 128.0 MiB ceilings).
- **Windows Platform:** Static type safety validated via `mypy --platform win32` (Exit 0); native runtime execution is pending independent Codex CI execution on PR.
- Direct execution logs and metrics recorded in `test-results.txt`.

## Assumptions and open items

- **Clock Source:** Default clock uses `time.monotonic_ns()`. `FakeClock` used in test fixtures for deterministic time control.
- **Observer Ownership:** `SourceWatchRuntime` creates, schedules, starts, stops, and joins its private `watchdog.observers.Observer` instance and captured worker emitters. Construction performs zero thread allocations.
- **Single-use Lifecycle:** Each `SourceWatchRuntime` instance supports exactly one execution of `run()`. Terminal instances (`STOPPED`, `FAILED`) reject subsequent `run()` invocations.
- **Remediation R1-R8:** All items from Codex review rounds 1 and 2 (worker tracking before start, raw BaseException preservation, expected worker liveness, watchdog dispatch override, lexical path relative segment rejection, atomic teardown view invariant, shared cause deduplication, async start error wrapping, coordinator constructor cancellation, barrier tests, 2,000 notice burst blocking, reader rejection retry, formula/cache XML generation with WP-04 planner oracle, and full WR-01..18 test coverage) have been fully addressed and tested.

## Risks

- **Host Monotonic Clock Anomalies:** If a broken system clock returns backwards time values, coordinator fails closed via `SaveCoordinatorStateError` and runtime terminates cleanly in `FAILED` state.
- **Blocked Consumer Callback:** If a consumer callback takes a long time or blocks, subsequent coordinator notifications continue coalescing safely without launching concurrent read cycles.

## Rollback

To revert this work package without affecting unrelated files:
1. Revert target files to baseline `c7520c94606f37720f3e023b753e36d1c1f433a3`:
   - `git checkout c7520c94606f37720f3e023b753e36d1c1f433a3 -- apps/local_agent/src/accounting_local_agent/source_watch_runtime.py`
   - `git checkout c7520c94606f37720f3e023b753e36d1c1f433a3 -- apps/local_agent/src/accounting_local_agent/__init__.py`
   - `git checkout c7520c94606f37720f3e023b753e36d1c1f433a3 -- apps/local_agent/README.md`
   - `git rm -f tests/test_source_watch_runtime.py tests/test_source_watch_runtime_native.py`
   - `git rm -rf handoffs/phase-01/wp-08-source-watch-runtime/`

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation, remediation of all review items (R1 through R8), native OS observer tests, and test verification for WP-08 are completed and stopped for handoff review. Gate G1 remains `OPEN / IN PROGRESS`. No Gate approval, merge, push, deploy, or next Work Package has been performed.
