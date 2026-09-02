# Handoff: WP-08 Managed Source Watcher and Serial Read Runtime

## Identity

- **Work Package:** `WP-08: Managed source watcher and serial read runtime`
- **Phase:** `1 — source and data-model foundation`
- **Component Version:** `SOURCE_WATCH_RUNTIME_VERSION = "source-watch-runtime.v1"`
- **Baseline Commit:** `c7520c94606f37720f3e023b753e36d1c1f433a3`
- **Target Branch:** `antigravity/phase-01-source-watch-runtime`
- **Gate G1 Status:** `OPEN / IN PROGRESS`

## Scope

Delivers the managed source watcher and serial read runtime under ADR-0011 and WP-08, remediated for all review findings (R1 through R8 across rounds 1 to 5):
1. **Public API & Version:** Implemented and exported `SOURCE_WATCH_RUNTIME_VERSION = "source-watch-runtime.v1"`, `SourceWatchRuntime`, `SourceWatchRuntimeState`, `SourceWatchRuntimeReason`, `SourceWatchRuntimeError`, and frozen `SourceWatchRuntimeView`.
2. **Lexical Validation & Non-Containment:** Strict constructor validation of `source_path` (.xlsx, non-lock `~$`, absolute, rejecting relative segments `'..'`/`'.'`) and `snapshot_root` with cross-platform containment enforcement.
3. **Table-Driven Watchdog Event Adapter:** Single `dispatch()` handler mapping file creations, modifications, deletions, and moves to `SaveImportCoordinator` notices; safely ignores directory events, temporary files, conflict files, read-only events, and unknown events (`future_event`) without filesystem I/O.
4. **Debounced Coordinator Runtime Loop:** Driver coordinates serial snapshot acquisition and reading through `read_due_source`; enqueues an initial `MODIFIED` hint upon startup; uses a 1.0s maximum idle wait for liveness checking.
5. **Synchronous Caller-Thread Delivery:** Delivers successful reader snapshots synchronously to the consumer on the run caller's thread after clean lease exit; drains active cycles gracefully upon `request_stop()`.
6. **Thread Lifecycle & Multi-Failure Grouping:** Strict worker tracking and joined teardown; deduplication strictly on exact instance identities, preserving independent errors with pre-existing or shared causes in `[run_error, async_error, *teardown_errors]` order; raw `BaseException` (like `KeyboardInterrupt`) preserved without wrapping; worker thread join failure wrapped in `SHUTDOWN_FAILED` with cause; mixed cancellation and normal stop failure in `BaseExceptionGroup`.
7. **Native OS Integration & WP-04 Composition (WR-15..17):** Real filesystem watcher responsiveness for in-place saves, atomic replaces, missing-source creation, and 4 distinct generations with bounded delivery deadlines, including Generation 3 Formula/Cache XML verified against independent WP-04 Planner oracle (0 changes, 5 UNCHANGED) prior to Generation 4 raw edit (1 EDIT, 4 UNCHANGED).
8. **Controlled Waiter & Timing Verification:** `ControlledConditionWaiter` hooks `Condition.wait` to eliminate arbitrary sleeps and deterministically verify deadline boundaries (deadline - 1ns wait duration 0.0001s, exact deadline cycle admission, latest-event debounce reset, and 0 retry I/O on reader error).

## Roadmap traceability

- **Decisions Implemented:** O-31 (Isolated snapshot lifecycle), O-53 (Windows Local Agent stack / execution architecture), O-70 (Streaming XLSX source reader), O-71 (Bounded memory & linear copy), O-72 (Save debounce & coalescing state machine), O-73 (Managed source watcher & runtime lifecycle), ADR-0011 (Source watch runtime architecture).
- **Roadmap Section 5.1 & 19.1:** Connects filesystem notifications to the save-import coordinator and read pipeline, establishing the native watcher runtime boundary for Phase 1.
- **Evidence Status:** Recorded under `test-results.txt` and `acceptance-matrix.md`.

## Review findings resolution summary (R7, R8 Round 5)

| Review Item | Description & Root Cause | Resolution |
| :--- | :--- | :--- |
| **WR-17 Oracle Defect** | Generation 3 Formula test relied on `time.sleep(1.0)` and `results[-1]`, failing to deterministically await generation delivery and risking reading Generation 2 or overlapping Generation 4. | Implemented `_wait_for_snapshot` with bounded deadline and `cursor=2` slicing `results[cursor:]`. Generation 3 snapshot is strictly delivered and verified (`res3 is not res2`, `snap3 is not snap2`) and validated against WP-04 Planner (0 changes, 5 `UNCHANGED`) before Generation 4 is written. Safe runner stop and join in `finally`. |
| **WR-07 Race State Assertion** | Immediate post-`request_stop()` check rejected `STOPPED` if runner finished teardown quickly, asserting only `STOPPING`. | Case A allows `state in (STOPPING, STOPPED)` immediately after stop request, and strictly asserts `STOPPED` after runner join. Added Case A2 which holds teardown via observer stop barrier, deterministically asserting `STOPPING` while held, and `STOPPED` after release and join. Case D asserts lost-wake prevention. |
| **WR-06 & WR-09 Timing Control** | Tests relied on arbitrary `time.sleep()` for deadline, idle, and retry waits. | Replaced arbitrary sleeps with `ControlledConditionWaiter` hooked into `runtime._condition.wait`, providing deterministic lock release synchronization (`wait_for_ack()`), verifying exact wait timeouts (e.g. 0.0001s at deadline - 1ns, 1.0s in idle), and confirming 0 retry I/O upon reader error. |
| **WR-11 Concurrent Loser Outcome** | Winner runner was not held while checking losers; stop during startup before run loop was not tested. | Winner held in consumer barrier while asserting all loser threads exit with `INVALID_TRANSITION` and only 1 backend observer is allocated (`factory_calls == 1`). Added dedicated test for `request_stop()` during observer factory initialization before run loop. |
| **WR-13 Dispatcher-Only Death** | Dispatcher death test called `mock_obs.stop()`, inadvertently killing emitters as well. | Set only `mock_obs2._stop_event.set()` and joined `mock_obs2` without stopping emitters. At failure detection, asserted `not mock_obs2.is_alive()` AND `all(em.is_alive() for em in mock_obs2.emitters)`. |
| **WR-14 Join Failure, Mixed Cancellation & Cause Identities** | Missing worker thread join failure in teardown, mixed cancellation + normal stop failure, and cause identities for 3 simultaneous standard exceptions. | Added dedicated tests: join failure wrapped in `SHUTDOWN_FAILED` with `__cause__ is join_os_err`; mixed cancellation (`KeyboardInterrupt`) + stop failure (`RuntimeError`) wrapped in `BaseExceptionGroup`; asserted exact identity (`is`) for all 3 standard exception causes. |
| **R8 Safe Rollback & Platform Logging** | Rollback instructions must specify exact commit range and avoid destructive baseline checkouts; evidence must record current real SHA and distinct native Linux vs CI Windows status. | Documented exact commit range (`c7520c94606f37720f3e023b753e36d1c1f433a3..HEAD` for package, single commit for Round 5) with scratch-branch verification. Test logs and SHA updated with live run outputs. |

## Changed files

- `apps/local_agent/src/accounting_local_agent/source_watch_runtime.py`: Core managed source watcher runtime implementation (R2 closed; untouched in Round 5).
- `apps/local_agent/src/accounting_local_agent/__init__.py`: Exported WP-08 public symbols (`SOURCE_WATCH_RUNTIME_VERSION`, `SourceWatchRuntime`, `SourceWatchRuntimeState`, `SourceWatchRuntimeReason`, `SourceWatchRuntimeError`, `SourceWatchRuntimeView`).
- `apps/local_agent/README.md`: Documented architecture, lifecycle transitions, exception hierarchy, and synthetic-only library usage example.
- `tests/test_source_watch_runtime.py`: Deterministic unit, barrier, race condition, fault injection, and exception grouping test suite (33 tests covering WR-01 through WR-14).
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
5. `uv run mypy --config-file pyproject.toml apps/local_agent/src packages/contracts/src packages/domain/src tests` (Exit 0, 24 source files checked)
6. `uv run mypy --config-file pyproject.toml --platform win32 apps/local_agent/src packages/contracts/src packages/domain/src tests` (Exit 0, 24 source files checked)
7. `uv run pytest tests/test_source_watch_runtime.py tests/test_source_watch_runtime_native.py -v` (Exit 0, 36 passed in 33.13s)
8. `uv run pytest tests/ -k "not test_bench"` (Exit 0, 348 passed, 2 skipped in 70.84s)
9. `uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s` (Exit 0, 11.66s / 61.53 MiB)
10. `uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s` (Exit 0, 11.12s / 61.58 MiB)
11. `git diff --check origin/main...HEAD` (Exit 0, 0 whitespace errors)
12. `git ls-files | grep -E "(\.xlsx$|\.pdf$|\.db$|\.sqlite$|\.env$|secrets|credentials)"` (Exit 0 on check, 0 prohibited files)
13. `git grep -i -E "password|secret|api_key|private_key" -- apps/` (Exit 0 on check, 0 sensitive credentials)
14. `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-08-source-watch-runtime` (Exit 0, validation passed)

## Tests and evidence

- **Full repository suite:** 348 tests passing cleanly on Linux native (2 platform-conditional skipped Windows-handle tests; 350 collected total).
- **Watcher Runtime tests:** 36 dedicated unit, deterministic barrier, native OS observer, lifecycle race, and composition tests (33 in `tests/test_source_watch_runtime.py`, 3 in `tests/test_source_watch_runtime_native.py`).
- **15,000-row streaming benchmarks:** WP-05 Reader at 11.66s duration / 61.53 MiB peak RSS; WP-06 Acquisition at 11.12s duration / 61.58 MiB peak RSS (strictly within 15.0s / 128.0 MiB ceilings).
- **Windows Platform:** Static type safety validated via `mypy --platform win32` (Exit 0); native runtime execution is pending independent Codex CI execution on PR.
- Direct execution logs and metrics recorded in `test-results.txt`.

## Assumptions and open items

- **Clock Source:** Default clock uses `time.monotonic_ns()`. `FakeClock` used in test fixtures for deterministic time control.
- **Observer Ownership:** `SourceWatchRuntime` creates, schedules, starts, stops, and joins its private `watchdog.observers.Observer` instance and captured worker emitters. Construction performs zero thread allocations.
- **Single-use Lifecycle:** Each `SourceWatchRuntime` instance supports exactly one execution of `run()`. Terminal instances (`STOPPED`, `FAILED`) reject subsequent `run()` invocations.
- **Remediation R1-R8:** All items from Codex review rounds 1 to 5 (worker tracking before start, raw BaseException preservation, expected worker liveness, watchdog dispatch override, lexical path relative segment rejection, atomic teardown view invariant, shared cause deduplication, error origin disentanglement, async start error wrapping with stop failure, coordinator constructor cancellation, barrier tests, 4-phase 2,000 notice burst blocking, reader rejection retry, formula/cache XML generation with WP-04 planner oracle, runner thread exception propagation, independent dispatcher failure, cause identities, join failure, mixed cancellation, and full WR-01..18 test coverage) have been fully addressed and tested.

## Risks

- **Host Monotonic Clock Anomalies:** If a broken system clock returns backwards time values, coordinator fails closed via `SaveCoordinatorStateError` and runtime terminates cleanly in `FAILED` state.
- **Blocked Consumer Callback:** If a consumer callback takes a long time or blocks, subsequent coordinator notifications continue coalescing safely without launching concurrent read cycles.

## Rollback

To revert this work package cleanly and safely without blind file deletions or destructive checkouts:

1. **Verify working tree cleanliness:**
   ```bash
   git status
   ```
   Ensure no untracked or independent user modifications exist before proceeding.

2. **Reverting Changes by Scope:**
   - **Scope A: Revert Only Round 5 Remediation Commit:**
     If only the Round 5 remediation commit is to be backed out while preserving earlier WP-08 commits:
     ```bash
     # Revert the latest remediation commit
     git revert --no-edit HEAD
     ```
   - **Scope B: Revert Entire WP-08 Package:**
     To revert the entire WP-08 work package back to baseline commit `c7520c94606f37720f3e023b753e36d1c1f433a3`:
     ```bash
     git revert --no-edit c7520c94606f37720f3e023b753e36d1c1f433a3..HEAD
     ```
   - **Recommended Safe Verification Procedure:**
     To verify that the revert leaves no unexpected diffs or broken dependencies, test the revert on a temporary scratch branch before touching the delivery branch:
     ```bash
     # Create a scratch verification branch from current HEAD
     git checkout -b scratch/verify-rollback

     # Apply the revert
     git revert --no-edit c7520c94606f37720f3e023b753e36d1c1f433a3..HEAD

     # Run tests and type checks to confirm clean baseline state
     uv run ruff check .
     uv run pytest tests/ -k "not test_bench"

     # Return to delivery branch and delete scratch branch
     git checkout antigravity/phase-01-source-watch-runtime
     git branch -D scratch/verify-rollback
     ```
   - **Precaution:** Avoid running blind `git rm` or destructive `git checkout c7520c94606f37720f3e023b753e36d1c1f433a3 -- .` directly on the delivery branch if subsequent independent commits or uncommitted changes exist, as that would destroy non-WP-08 modifications.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation, remediation of all review items (R1 through R8 across rounds 1 to 5), native OS observer tests, runner thread propagation, and test verification for WP-08 are completed and stopped for handoff review. Gate G1 remains `OPEN / IN PROGRESS`. No Gate approval, merge, push, deploy, or next Work Package has been performed.
