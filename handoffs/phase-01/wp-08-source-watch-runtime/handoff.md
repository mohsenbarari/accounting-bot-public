# Handoff: WP-08 Managed Source Watcher and Serial Read Runtime

## Identity

- **Work Package:** `WP-08: Managed source watcher and serial read runtime`
- **Phase:** `1 — source and data-model foundation`
- **Component Version:** `SOURCE_WATCH_RUNTIME_VERSION = "source-watch-runtime.v1"`
- **Baseline Commit:** `c7520c94606f37720f3e023b753e36d1c1f433a3`
- **Tested Code Commit SHA:** `809572f1b0e996c114ab5d5b6719ad151aab10ff`
- **Target Branch:** `antigravity/phase-01-source-watch-runtime`
- **Gate G1 Status:** `OPEN / IN PROGRESS`

## Scope

Delivers the managed source watcher and serial read runtime under ADR-0011 and WP-08, remediated for all review findings (R1 through R8 across rounds 1 to 7):
1. **Public API & Version:** Implemented and exported `SOURCE_WATCH_RUNTIME_VERSION = "source-watch-runtime.v1"`, `SourceWatchRuntime`, `SourceWatchRuntimeState`, `SourceWatchRuntimeReason`, `SourceWatchRuntimeError`, and frozen `SourceWatchRuntimeView`.
2. **Lexical Validation & Non-Containment:** Strict constructor validation of `source_path` (.xlsx, non-lock `~$`, absolute, rejecting relative segments `'..'`/`'.'`) and `snapshot_root` with cross-platform containment enforcement.
3. **Table-Driven Watchdog Event Adapter:** Single `dispatch()` handler mapping file creations, modifications, deletions, and moves to `SaveImportCoordinator` notices; safely ignores directory events, temporary files, conflict files, read-only events, and unknown events (`future_event`) without filesystem I/O.
4. **Debounced Coordinator Runtime Loop:** Driver coordinates serial snapshot acquisition and reading through `read_due_source`; enqueues an initial `MODIFIED` hint upon startup; uses a 1.0s maximum idle wait for liveness checking.
5. **Synchronous Caller-Thread Delivery:** Delivers successful reader snapshots synchronously to the consumer on the run caller's thread after clean lease exit; drains active cycles gracefully upon `request_stop()`.
6. **Thread Lifecycle & Multi-Failure Grouping:** Strict worker tracking and joined teardown; deduplication strictly on exact instance identities, preserving independent errors with pre-existing or shared causes in `[run_error, async_error, *teardown_errors]` order; raw `BaseException` (like `KeyboardInterrupt`) preserved without wrapping; worker thread join failure wrapped in `SHUTDOWN_FAILED` with cause; mixed cancellation and normal stop failure in `BaseExceptionGroup`.
7. **Native OS Integration & WP-04 Composition (WR-15..17):** Real filesystem watcher responsiveness for in-place saves, atomic replaces, missing-source creation, and 4 distinct generations with bounded delivery deadlines. Initial cursor taken under lock before `run_thread.start()` ensures pre-existing files are never missed due to thread scheduling races. Dynamic cursors taken under lock immediately before writes adhere to WR-D contract without brittle exact delivery count assertions. Generation 3 Formula/Cache XML verified against independent WP-04 Planner oracle (0 changes, 5 UNCHANGED) prior to Generation 4 raw edit (1 EDIT, 4 UNCHANGED).
8. **Controlled Waiter & Timing Verification:** `ControlledConditionWaiter` hooks `Condition.wait` to eliminate arbitrary sleeps and deterministically verify deadline boundaries (deadline - 1ns wait duration 0.0001s, exact deadline cycle admission, latest-event debounce reset, and 0 retry I/O on reader error). WR-07 Case D lost-wake prevention tested via dedicated notice sender thread and ACK event (`result_recorded_d`) for recorded wait result, confirming condition wake without lock contention, timeouts, arbitrary sleeps, or rescuer notifications.

## Roadmap traceability

- **Decisions Implemented:** O-31 (Isolated snapshot lifecycle), O-53 (Windows Local Agent stack / execution architecture), O-70 (Streaming XLSX source reader), O-71 (Bounded memory & linear copy), O-72 (Save debounce & coalescing state machine), O-73 (Managed source watcher & runtime lifecycle), ADR-0011 (Source watch runtime architecture).
- **Roadmap Section 5.1 & 19.1:** Connects filesystem notifications to the save-import coordinator and read pipeline, establishing the native watcher runtime boundary for Phase 1.
- **Evidence Status:** Recorded under `test-results.txt` and `acceptance-matrix.md`.

## Review findings resolution summary (R7, R8 Round 7)

| Review Item | Description & Root Cause | Resolution |
| :--- | :--- | :--- |
| **WR-07 Case D ACK Synchronization** | Sender completion did not guarantee runner had appended wait result to list before test thread asserted; 50ms sleep was arbitrary. | Added `result_recorded_d = threading.Event()` signaled under `wait_results_lock` immediately after `res = orig_wait_d(timeout)` is appended. Test thread awaits `result_recorded_d.wait(timeout=5.0)` deterministically. Removed arbitrary `time.sleep(0.02)` and `time.sleep(0.05)`. Maintained strict assertion `wait_results[0] is True` and clean thread join in `finally`. |
| **WR-15 & WR-17 Initial Cursor Timing** | `cursor1 = len(results)` was acquired after `run_thread.start()`, creating a race condition where fast startup delivery could set `cursor1 = 1` and miss the initial snapshot. | Moved initial cursor acquisition under lock immediately BEFORE `run_thread.start()` (`with lock: cursor1 = len(results)`). Pre-existing startup snapshot is guaranteed to be detected from index 0 even if delivered before `start()` returns. |
| **Rollback SHA Verification & Scratch Execution** | Fixed rollback SHA list required validation against `git rev-list`; scratch rollback claim needed live execution verification rather than purely prescriptive instructions. | Queried full commit list via `git rev-list c7520c94606f37720f3e023b753e36d1c1f433a3..809572f1b0e996c114ab5d5b6719ad151aab10ff`, verifying all 9 commits. Executed live scratch rollback on temporary branch `scratch/verify-rollback`: all 9 commits reverted cleanly, confirmed working tree matched baseline with `git diff --quiet c7520c94606f37720f3e023b753e36d1c1f433a3` (Exit 0), and cleanly removed scratch branch. |
| **Full-Repository Type Checking** | Verification requires whole-repository scope (28 source files). | Confirmed `uv run mypy .` and `uv run mypy --platform win32 .` pass with 0 errors across 28 source files. |
| **Segregated Platform Status** | Linux local, Windows local, and CI must be reported distinctly. | Reported Linux Native as PASS, Windows Local as NOT RUN (unsupported host), and Windows CI execution as PENDING on PR. |

## Changed files

- `apps/local_agent/src/accounting_local_agent/source_watch_runtime.py`: Core managed source watcher runtime implementation (R2 closed; untouched in Round 7).
- `apps/local_agent/src/accounting_local_agent/__init__.py`: Exported WP-08 public symbols (`SOURCE_WATCH_RUNTIME_VERSION`, `SourceWatchRuntime`, `SourceWatchRuntimeState`, `SourceWatchRuntimeReason`, `SourceWatchRuntimeError`, `SourceWatchRuntimeView`).
- `apps/local_agent/README.md`: Documented architecture, lifecycle transitions, exception hierarchy, and synthetic-only library usage example.
- `tests/test_source_watch_runtime.py`: Deterministic unit, barrier, race condition, fault injection, and exception grouping test suite (33 tests covering WR-01 through WR-14; WR-07 Case D ACK-synchronized result recording).
- `tests/test_source_watch_runtime_native.py`: Real native OS filesystem observer integration test suite (3 tests covering WR-15 through WR-17; initial cursor acquired before start and dynamic cursors adhering to WR-D).
- `handoffs/phase-01/wp-08-source-watch-runtime/handoff.md`: Handoff summary, verified fixed rollback instructions, and audit traceability.
- `handoffs/phase-01/wp-08-source-watch-runtime/acceptance-matrix.md`: Detailed requirement acceptance matrix (WR-01 to WR-18).
- `handoffs/phase-01/wp-08-source-watch-runtime/test-results.txt`: Direct command execution logs, quality scan records, scratch verification proof, and benchmark metrics on tested code commit `809572f1b0e996c114ab5d5b6719ad151aab10ff`.

## Schema and migrations

- None (WP-08 is purely a local agent filesystem watcher and serial read runtime component).

## Commands and exit codes

1. `uv sync --frozen --all-packages --all-groups` (Exit 0, 81 packages checked)
2. `uv lock --check` (Exit 0, 88 packages resolved)
3. `uv run ruff format --check .` (Exit 0, 74 files checked)
4. `uv run ruff check .` (Exit 0, all checks passed)
5. `uv run mypy .` (Exit 0, 28 source files checked across repository)
6. `uv run mypy --platform win32 .` (Exit 0, 28 source files checked across repository)
7. `uv run pytest tests/test_source_watch_runtime.py tests/test_source_watch_runtime_native.py -v` (Exit 0, 36 passed in 28.64s)
8. `uv run pytest tests/ -k "not test_bench"` (Exit 0, 348 passed, 2 skipped in 63.23s)
9. `uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s` (Exit 0, 10.70s / 61.90 MiB)
10. `uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s` (Exit 0, 11.39s / 61.25 MiB)
11. `git diff --check` (Exit 0, 0 whitespace errors)
12. `git ls-files | grep -E "\.(env|key|pem|pfx|p12|kdbx|sqlite|db)$"` (Exit 0 on check, 0 prohibited files)
13. `git grep -i -E "(BEGIN PRIVATE KEY|AKIA[0-9A-Z]{16}|ghp_[0-9a-zA-Z]{36})"` (Exit 0 on check, 0 sensitive credentials)
14. `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-08-source-watch-runtime` (Exit 0, validation passed)

## Tests and evidence

- **Full repository suite:** 348 tests passing cleanly on Linux native (2 platform-conditional skipped Windows-handle tests; 350 collected total).
- **Watcher Runtime tests:** 36 dedicated unit, deterministic barrier, native OS observer, lifecycle race, and composition tests (33 in `tests/test_source_watch_runtime.py`, 3 in `tests/test_source_watch_runtime_native.py`).
- **15,000-row streaming benchmarks:** WP-05 Reader at 10.70s duration / 61.90 MiB peak RSS; WP-06 Acquisition at 11.39s duration / 61.25 MiB peak RSS (strictly within 15.0s / 128.0 MiB ceilings).
- **Platform Separation:**
  - **Linux Local:** PASS (executed natively and passed all 36 WP-08 tests, 348 repository tests, and benchmarks).
  - **Windows Local:** NOT RUN (Linux host environment; static type safety validated via `mypy --platform win32 .` with 0 errors in 28 source files).
  - **Windows CI:** PENDING automated CI runner execution upon PR creation.
- Direct execution logs, scratch rollback verification, and metrics recorded in `test-results.txt`.

## Assumptions and open items

- **Clock Source:** Default clock uses `time.monotonic_ns()`. `FakeClock` used in test fixtures for deterministic time control.
- **Observer Ownership:** `SourceWatchRuntime` creates, schedules, starts, stops, and joins its private `watchdog.observers.Observer` instance and captured worker emitters. Construction performs zero thread allocations.
- **Single-use Lifecycle:** Each `SourceWatchRuntime` instance supports exactly one execution of `run()`. Terminal instances (`STOPPED`, `FAILED`) reject subsequent `run()` invocations.
- **Remediation R1-R8:** All items from Codex review rounds 1 to 7 (worker tracking before start, raw BaseException preservation, expected worker liveness, watchdog dispatch override, lexical path relative segment rejection, atomic teardown view invariant, shared cause deduplication, error origin disentanglement, async start error wrapping with stop failure, coordinator constructor cancellation, barrier tests, 4-phase 2,000 notice burst blocking, reader rejection retry, formula/cache XML generation with WP-04 planner oracle, runner thread exception propagation, independent dispatcher failure, cause identities, join failure, mixed cancellation, WR-07 Case D ACK synchronization, initial native cursor timing before start, verified fixed rollback SHAs, and full WR-01..18 test coverage) have been fully addressed and tested.

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

2. **Reverting Changes by Fixed Scope:**
   - **Scope A: Revert Only Round 7 Test Remediation Commit:**
     To back out only the Round 7 test commit `809572f1b0e996c114ab5d5b6719ad151aab10ff`:
     ```bash
     git revert --no-edit 809572f1b0e996c114ab5d5b6719ad151aab10ff
     ```
   - **Scope B: Revert Entire WP-08 Package:**
     To revert all WP-08 commits back to baseline commit `c7520c94606f37720f3e023b753e36d1c1f433a3`, apply reverts for the fixed commit range `c7520c94606f37720f3e023b753e36d1c1f433a3..809572f1b0e996c114ab5d5b6719ad151aab10ff`:
     Explicit commit sequence generated from `git rev-list`:
     1. `809572f1b0e996c114ab5d5b6719ad151aab10ff` (Round 7 test remediation)
     2. `a0bae5d9b819c86a24e805a427d7f6957b25a45d` (Round 6 handoff documentation)
     3. `8c670b1ad34ba8519538c19f85b96d88a02173ab` (Round 6 test remediation)
     4. `5fb76b5cea29d6eaae941d01311cc4ce7d3fc012` (Round 5 remediation)
     5. `47c962f80def95f17de6cce2c93812291bdf2ccf` (Round 4 remediation)
     6. `ccce0b2c001efae467e0d698aff19064231eba06` (Round 3 remediation)
     7. `f2a9d8312ad39d8644a8a46928ccffbc889bbb9e` (Round 2 remediation)
     8. `f922cfa6d26e70d6808cc5330b8b7e489e0e7abd` (Round 1 remediation)
     9. `2abf448610c31677702828a1064effc7a42b4189` (Initial WP-08 implementation)

     Command:
     ```bash
     git revert --no-edit c7520c94606f37720f3e023b753e36d1c1f433a3..809572f1b0e996c114ab5d5b6719ad151aab10ff
     ```

   - **Verified Scratch Branch Procedure (Confirmed Zero Diff Against Baseline):**
     This rollback procedure was verified on temporary branch `scratch/verify-rollback`. Reverting the 9 package commits produced an exact zero diff against baseline `c7520c94606f37720f3e023b753e36d1c1f433a3`:
     ```bash
     # Create scratch branch from fixed tested SHA
     git checkout -b scratch/verify-rollback 809572f1b0e996c114ab5d5b6719ad151aab10ff

     # Apply the revert across the fixed package range
     git revert --no-edit c7520c94606f37720f3e023b753e36d1c1f433a3..809572f1b0e996c114ab5d5b6719ad151aab10ff

     # Verify that working tree is identical to baseline (exited with code 0)
     git diff --quiet c7520c94606f37720f3e023b753e36d1c1f433a3

     # Run test suite to verify baseline functionality
     uv run ruff check .
     uv run pytest tests/ -k "not test_bench"

     # Return to delivery branch and delete scratch branch
     git checkout antigravity/phase-01-source-watch-runtime
     git branch -D scratch/verify-rollback
     ```
   - **Precaution:** Avoid running blind `git rm` or destructive `git checkout c7520c94606f37720f3e023b753e36d1c1f433a3 -- .` directly on the delivery branch, as that would destroy non-WP-08 modifications or uncommitted changes.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation, remediation of all review items (R1 through R8 across rounds 1 to 7), native OS observer tests, runner thread propagation, verified scratch rollback, and test verification for WP-08 are completed and stopped for handoff review. Gate G1 remains `OPEN / IN PROGRESS`. No Gate approval, merge, push, deploy, or next Work Package has been performed.
