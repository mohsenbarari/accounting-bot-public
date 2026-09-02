# Handoff: WP-08 Managed Source Watcher and Serial Read Runtime

## Identity

- **Work Package:** `WP-08: Managed source watcher and serial read runtime`
- **Phase:** `1 — source and data-model foundation`
- **Component Version:** `SOURCE_WATCH_RUNTIME_VERSION = "source-watch-runtime.v1"`
- **Baseline Commit:** `c7520c94606f37720f3e023b753e36d1c1f433a3`
- **Target Branch:** `antigravity/phase-01-source-watch-runtime`
- **Gate G1 Status:** `OPEN / IN PROGRESS`

## Scope

Delivers the managed source watcher and serial read runtime under ADR-0011 and WP-08:
1. **Public API & Version:** Implemented and exported `SOURCE_WATCH_RUNTIME_VERSION = "source-watch-runtime.v1"`, `SourceWatchRuntime`, `SourceWatchRuntimeState`, `SourceWatchRuntimeReason`, `SourceWatchRuntimeError`, and frozen `SourceWatchRuntimeView`.
2. **Lexical Validation & Non-Containment:** Strict constructor validation of `source_path` (.xlsx, non-lock `~$`, absolute, rejecting relative `'..'`/`'.'`) and `snapshot_root` with cross-platform containment enforcement ensuring `snapshot_root` is strictly outside the watched directory.
3. **Table-Driven Watchdog Event Adapter:** Single `dispatch()` handler mapping file creations, modifications, deletions, and moves to `SaveImportCoordinator` notices; safely ignores directory events, temporary files, conflict files, read-only events, and unknown events (`future_event`) without filesystem I/O.
4. **Debounced Coordinator Runtime Loop:** Driver coordinates serial snapshot acquisition and reading through `read_due_source`; enqueues an initial `MODIFIED` hint upon startup to discover preexisting files; uses a 1.0s maximum idle wait for liveness checking.
5. **Synchronous Caller-Thread Delivery:** Delivers successful reader snapshots synchronously to the consumer on the run caller's thread after clean lease exit; drains active cycles gracefully upon `request_stop()`.
6. **Thread Lifecycle & Error Grouping:** Strict ownership, joining, and teardown of watchdog observer threads; multi-failure preservation via `ExceptionGroup` and `BaseExceptionGroup` preserving root causes, raw BaseExceptions, and ordered failure identities.
7. **Native OS Integration:** Proves real filesystem watcher responsiveness under Linux and Windows directory structures for in-place saves, atomic replaces, missing-source creation, and composition with the independent WP-04 Planner oracle.

## Roadmap traceability

- **Decisions Implemented:** O-31 (Isolated snapshot lifecycle), O-53 (Windows Local Agent stack / execution architecture), O-70 (Streaming XLSX source reader), O-71 (Bounded memory & linear copy), O-72 (Save debounce & coalescing state machine), O-73 (Managed source watcher & runtime lifecycle), ADR-0011 (Source watch runtime architecture).
- **Roadmap Section 5.1 & 19.1:** Connects filesystem notifications to the save-import coordinator and read pipeline, establishing the native watcher runtime boundary for Phase 1.
- **Evidence Status:** Recorded under `test-results.txt` and `acceptance-matrix.md`.

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
7. `uv run pytest tests/test_source_watch_runtime.py tests/test_source_watch_runtime_native.py -v` (Exit 0, 21 passed in 24.81s)
8. `uv run pytest -v` (Exit 0, 333 passed, 2 skipped in 61.87s)
9. `uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s` (Exit 0, 11.38s / 61.93 MiB)
10. `uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s` (Exit 0, 11.41s / 61.61 MiB)
11. `git diff --check origin/main...HEAD` (Exit 0, 0 whitespace errors)
12. `git ls-files | grep -E "(\.xlsx$|\.pdf$|\.db$|\.sqlite$|\.env$|secrets|credentials)"` (Exit 0 on check, 0 prohibited files)
13. `git grep -i -E "password|secret|api_key|private_key" -- apps/` (Exit 0 on check, 0 sensitive credentials)
14. `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-08-source-watch-runtime` (Exit 0, validation passed)

## Tests and evidence

- **Full repository suite:** 333 tests passing cleanly on Linux native (2 platform-conditional skipped Windows-handle tests; 335 collected total).
- **Watcher Runtime tests:** 21 dedicated unit, deterministic barrier, native OS observer, lifecycle race, and composition tests.
- **15,000-row streaming benchmarks:** WP-05 Reader at 11.38s duration / 61.93 MiB peak RSS; WP-06 Acquisition at 11.41s duration / 61.61 MiB peak RSS (strictly within 15.0s / 128.0 MiB ceilings).
- **Windows Platform:** Static type safety validated via `mypy --platform win32` (Exit 0); native runtime execution is pending independent Codex CI execution on PR.
- Direct execution logs and metrics recorded in `test-results.txt`.

## Assumptions and open items

- **Clock Source:** Default clock uses `time.monotonic_ns()`. `FakeClock` used in test fixtures for deterministic time control.
- **Observer Ownership:** `SourceWatchRuntime` creates, schedules, starts, stops, and joins its private `watchdog.observers.Observer` instance and captured worker emitters. Construction performs zero thread allocations.
- **Single-use Lifecycle:** Each `SourceWatchRuntime` instance supports exactly one execution of `run()`. Terminal instances (`STOPPED`, `FAILED`) reject subsequent `run()` invocations.
- **Remediation R1-R8:** All items from the first Codex review round (worker tracking before start, raw BaseException preservation, expected worker liveness, watchdog dispatch override, lexical path relative segment rejection, atomic teardown view invariant, and full WR-01..18 test coverage) have been fully addressed and tested.

## Risks

- **Host Monotonic Clock Anomalies:** If a broken system clock returns backwards time values, coordinator fails closed via `SaveCoordinatorStateError` and runtime terminates cleanly in `FAILED` state.
- **Blocked Consumer Callback:** If a consumer callback takes a long time or blocks, subsequent coordinator notifications continue coalescing safely without launching concurrent read cycles.

## Rollback

1. Revert changes on `antigravity/phase-01-source-watch-runtime` back to baseline `c7520c94606f37720f3e023b753e36d1c1f433a3`.
2. Clean untracked runtime and test artifacts.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation, remediation of review items R1 through R8, native OS observer tests, and test verification for WP-08 are completed and stopped for handoff review. Gate G1 remains `OPEN / IN PROGRESS`. No Gate approval, merge, push, deploy, or next Work Package has been performed.
