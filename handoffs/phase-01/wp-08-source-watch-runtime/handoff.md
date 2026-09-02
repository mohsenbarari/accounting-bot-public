# Handoff: WP-08 Managed Source Watcher and Serial Read Runtime (Post-Merge WR-08 Test Stabilization)

## Identity

- **Work Package:** `WP-08: Managed source watcher and serial read runtime`
- **Phase:** `1 — source and data-model foundation`
- **Component Version:** `SOURCE_WATCH_RUNTIME_VERSION = "source-watch-runtime.v1"`
- **Baseline Commit:** `0d1df855c39ab7ac5b8d5492915fb1014dc096cf` (PR #28 merged into main)
- **Tested Code Commit SHA:** `c77f85b69e353a3e057de15436a3f8498c8f9a65`
- **Target Branch:** `antigravity/phase-01-wp08-wr08-test-stabilization`
- **Gate G1 Status:** `OPEN / IN PROGRESS`

## Scope

Delivers post-merge test stabilization for WR-08 four-phase burst coalescing under ADR-0011 and WP-08, remediating Windows CI Run 33666981958 timing sensitivity while preserving product code (R2 strictly closed):
1. **Public API & Version:** Implemented and exported `SOURCE_WATCH_RUNTIME_VERSION = "source-watch-runtime.v1"`, `SourceWatchRuntime`, `SourceWatchRuntimeState`, `SourceWatchRuntimeReason`, `SourceWatchRuntimeError`, and frozen `SourceWatchRuntimeView`.
2. **Lexical Validation & Non-Containment:** Strict constructor validation of `source_path` (.xlsx, non-lock `~$`, absolute, rejecting relative segments `'..'`/`'.'`) and `snapshot_root` with cross-platform containment enforcement.
3. **Table-Driven Watchdog Event Adapter:** Single `dispatch()` handler mapping file creations, modifications, deletions, and moves to `SaveImportCoordinator` notices; safely ignores directory events, temporary files, conflict files, read-only events, and unknown events (`future_event`) without filesystem I/O.
4. **Debounced Coordinator Runtime Loop:** Driver coordinates serial snapshot acquisition and reading through `read_due_source`; enqueues an initial `MODIFIED` hint upon startup; uses a 1.0s maximum idle wait for liveness checking.
5. **Synchronous Caller-Thread Delivery:** Delivers successful reader snapshots synchronously to the consumer on the run caller's thread after clean lease exit; drains active cycles gracefully upon `request_stop()`.
6. **Thread Lifecycle & Multi-Failure Grouping:** Strict worker tracking and joined teardown; deduplication strictly on exact instance identities, preserving independent errors with pre-existing or shared causes in `[run_error, async_error, *teardown_errors]` order; raw `BaseException` (like `KeyboardInterrupt`) preserved without wrapping; worker thread join failure wrapped in `SHUTDOWN_FAILED` with cause; mixed cancellation and normal stop failure in `BaseExceptionGroup`.
7. **Native OS Integration & WP-04 Composition (WR-15..17):** Real filesystem watcher responsiveness for in-place saves, atomic replaces, missing-source creation, and 4 distinct generations with bounded delivery deadlines. Initial cursor taken under lock before `run_thread.start()` ensures pre-existing files are never missed due to thread scheduling races. Dynamic cursors taken under lock immediately before writes adhere to WR-D contract without brittle exact delivery count assertions. Generation 3 Formula/Cache XML verified against independent WP-04 Planner oracle (0 changes, 5 UNCHANGED) prior to Generation 4 raw edit (1 EDIT, 4 UNCHANGED).
8. **WR-08 Four-Phase Stabilization (Post-Merge):** Replaced all arbitrary `time.sleep(0.05)` calls in Consumer, Acquisition, Reader, and Cleanup phases with deterministic `ControlledConditionWaiter` ACK arrivals. Advances `FakeClock` only after witnessing runtime arrival in condition wait. Awaits completion of cycle 1 and follow-up condition wait entry via `waiter.wait_for_ack(timeout=5.0)` before advancing clock for the second cycle. Second delivery is witnessed deterministically via explicit event before `runtime.request_stop()` is called. Controlled delay witness (`followup_delay_gate` and `release_followup_delay`) in Phase 4 demonstrates that delayed follow-up execution is awaited without early stop cancellation.

## Roadmap traceability

- **Decisions Implemented:** O-31 (Isolated snapshot lifecycle), O-53 (Windows Local Agent stack / execution architecture), O-70 (Streaming XLSX source reader), O-71 (Bounded memory & linear copy), O-72 (Save debounce & coalescing state machine), O-73 (Managed source watcher & runtime lifecycle), ADR-0011 (Source watch runtime architecture).
- **Roadmap Section 5.1 & 19.1:** Connects filesystem notifications to the save-import coordinator and read pipeline, establishing the native watcher runtime boundary for Phase 1.
- **Evidence Status:** Recorded under `test-results.txt` and `acceptance-matrix.md`.

## Review findings resolution summary (Post-Merge WR-08 Stabilization)

| Review Item | Description & Root Cause | Resolution |
| :--- | :--- | :--- |
| **WR-08 Initial Wait ACK** | Arbitrary `time.sleep(0.05)` before advancing `FakeClock` to admit cycle 1. | Replaced with `waiter.wait_for_ack(timeout=5.0)` across all four phases (Consumer, Acquisition, Reader, Cleanup), confirming runtime has entered condition wait before time advances. |
| **WR-08 Follow-Up Await & Second Delivery ACK** | Fixed 50ms sleep before `request_stop()` caused premature termination on slower CI runners (Windows Run 33666981958) before follow-up cycle delivered (`len(results4) == 2` failed with actual 1). | After releasing the blocked phase, test thread deterministically awaits `waiter.wait_for_ack(timeout=5.0)` to verify runner is waiting for follow-up deadline. Clock is advanced, and second delivery is witnessed via `second_delivered*.wait(timeout=5.0)` before calling `request_stop()`. All sleeps eliminated. |
| **WR-08 Controlled Delay Witness** | Requirement to prove that delayed follow-up execution does not cause early cancellation. | In Phase 4 (Cleanup), added `followup_delay_gate` and `release_followup_delay` on the second cycle (`cleanup_calls == 2`). Test thread witnesses the delay, confirms no premature stop occurs, releases the gate, witnesses second delivery, and only then requests stop. |
| **Release Wait Assertions & Resource Cleanup** | All release events must assert success and threads must be cleaned up in `finally`. | All `release_*.wait(timeout=5.0)` calls assert success with explicit failure messages. All runners, senders, and observers are released, stopped, and joined in `finally`. Runner clean exit asserted with `runner.assert_clean_exit()`. |
| **Rollback Verification on Scratch Branch** | Rollback must be verified against new baseline `0d1df855c39ab7ac5b8d5492915fb1014dc096cf`. | Revert of `c77f85b69e353a3e057de15436a3f8498c8f9a65` verified on temporary scratch branch `scratch/verify-rollback`: confirmed 100% identical working tree against baseline via `git diff --quiet 0d1df855c39ab7ac5b8d5492915fb1014dc096cf` (Exit 0). |

## Changed files

- `apps/local_agent/src/accounting_local_agent/source_watch_runtime.py`: Core managed source watcher runtime implementation (R2 closed; untouched in this branch).
- `tests/test_source_watch_runtime.py`: Stabilized WR-08 four-phase burst coalescing test with deterministic condition waiter ACKs, delivery events, controlled delay witness, and zero sleeps.
- `handoffs/phase-01/wp-08-source-watch-runtime/handoff.md`: Handoff summary, verified fixed rollback instructions, and audit traceability.
- `handoffs/phase-01/wp-08-source-watch-runtime/acceptance-matrix.md`: Detailed requirement acceptance matrix (WR-01 to WR-18).
- `handoffs/phase-01/wp-08-source-watch-runtime/test-results.txt`: Direct command execution logs, quality scan records, scratch verification proof, and benchmark metrics on tested code commit `c77f85b69e353a3e057de15436a3f8498c8f9a65`.

## Schema and migrations

- None (WP-08 is purely a local agent filesystem watcher and serial read runtime component).

## Commands and exit codes

1. `uv sync --frozen --all-packages --all-groups` (Exit 0, 81 packages checked)
2. `uv lock --check` (Exit 0, 88 packages resolved)
3. `uv run ruff format --check .` (Exit 0, 74 files checked)
4. `uv run ruff check .` (Exit 0, all checks passed)
5. `uv run mypy .` (Exit 0, 28 source files checked across repository)
6. `uv run mypy --platform win32 .` (Exit 0, 28 source files checked across repository)
7. `uv run pytest tests/test_source_watch_runtime.py tests/test_source_watch_runtime_native.py -v` (Exit 0, 36 passed in 27.91s)
8. `uv run pytest tests/ -k "not test_bench"` (Exit 0, 348 passed, 2 skipped in 62.06s)
9. `uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s` (Exit 0, 11.28s / 61.55 MiB)
10. `uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s` (Exit 0, 12.03s / 61.73 MiB)
11. `git diff --check` (Exit 0, 0 whitespace errors)
12. `git ls-files | grep -E "\.(env|key|pem|pfx|p12|kdbx|sqlite|db)$"` (Exit 0 on check, 0 prohibited files)
13. `git grep -i -E "(BEGIN PRIVATE KEY|AKIA[0-9A-Z]{16}|ghp_[0-9a-zA-Z]{36})"` (Exit 0 on check, 0 sensitive credentials)
14. `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-08-source-watch-runtime` (Exit 0, validation passed)

## Tests and evidence

- **Full repository suite:** 348 tests passing cleanly on Linux native (2 platform-conditional skipped Windows-handle tests; 350 collected total).
- **Watcher Runtime tests:** 36 dedicated unit, deterministic barrier, native OS observer, lifecycle race, and composition tests (33 in `tests/test_source_watch_runtime.py`, 3 in `tests/test_source_watch_runtime_native.py`).
- **15,000-row streaming benchmarks:** WP-05 Reader at 11.28s duration / 61.55 MiB peak RSS; WP-06 Acquisition at 12.03s duration / 61.73 MiB peak RSS (strictly within 15.0s / 128.0 MiB ceilings).
- **Platform Separation:**
  - **Linux Local:** PASS (executed natively and passed all 36 WP-08 tests, 348 repository tests, and benchmarks).
  - **Windows Local:** NOT RUN (Linux host environment; static type safety validated via `mypy --platform win32 .` with 0 errors in 28 source files).
  - **Windows CI:** PENDING automated CI runner execution upon PR submission.
- Direct execution logs, scratch rollback verification, and metrics recorded in `test-results.txt`.

## Assumptions and open items

- **Clock Source:** Default clock uses `time.monotonic_ns()`. `FakeClock` used in test fixtures for deterministic time control.
- **Observer Ownership:** `SourceWatchRuntime` creates, schedules, starts, stops, and joins its private `watchdog.observers.Observer` instance and captured worker emitters. Construction performs zero thread allocations.
- **Single-use Lifecycle:** Each `SourceWatchRuntime` instance supports exactly one execution of `run()`. Terminal instances (`STOPPED`, `FAILED`) reject subsequent `run()` invocations.
- **Remediation R1-R8:** All items from Codex review rounds 1 to 7 and post-merge WR-08 stabilization (worker tracking before start, raw BaseException preservation, expected worker liveness, watchdog dispatch override, lexical path relative segment rejection, atomic teardown view invariant, shared cause deduplication, error origin disentanglement, async start error wrapping with stop failure, coordinator constructor cancellation, barrier tests, 4-phase 2,000 notice burst blocking with condition waiter ACKs, reader rejection retry, formula/cache XML generation with WP-04 planner oracle, runner thread exception propagation, independent dispatcher failure, cause identities, join failure, mixed cancellation, WR-07 Case D ACK synchronization, initial native cursor timing before start, verified fixed rollback SHAs, and full WR-01..18 test coverage) have been fully addressed and tested.

## Risks

- **Host Monotonic Clock Anomalies:** If a broken system clock returns backwards time values, coordinator fails closed via `SaveCoordinatorStateError` and runtime terminates cleanly in `FAILED` state.
- **Blocked Consumer Callback:** If a consumer callback takes a long time or blocks, subsequent coordinator notifications continue coalescing safely without launching concurrent read cycles.

## Rollback

To revert this stabilization commit cleanly and safely without blind file deletions or destructive checkouts:

1. **Verify working tree cleanliness:**
   ```bash
   git status
   ```
   Ensure no untracked or independent user modifications exist before proceeding.

2. **Reverting Changes by Fixed Scope:**
   - **Scope: Revert WR-08 Test Stabilization Commit:**
     To back out the test stabilization commit `c77f85b69e353a3e057de15436a3f8498c8f9a65` back to baseline `0d1df855c39ab7ac5b8d5492915fb1014dc096cf`:
     ```bash
     git revert --no-edit c77f85b69e353a3e057de15436a3f8498c8f9a65
     ```

   - **Verified Scratch Branch Procedure (Confirmed Zero Diff Against Baseline):**
     This rollback procedure was verified on temporary branch `scratch/verify-rollback`. Reverting commit `c77f85b69e353a3e057de15436a3f8498c8f9a65` produced an exact zero diff against baseline `0d1df855c39ab7ac5b8d5492915fb1014dc096cf`:
     ```bash
     # Create scratch branch from fixed tested SHA
     git checkout -b scratch/verify-rollback c77f85b69e353a3e057de15436a3f8498c8f9a65

     # Apply the revert
     git revert --no-edit c77f85b69e353a3e057de15436a3f8498c8f9a65

     # Verify that working tree is identical to baseline (exited with code 0)
     git diff --quiet 0d1df855c39ab7ac5b8d5492915fb1014dc096cf

     # Return to stabilization branch and delete scratch branch
     git checkout antigravity/phase-01-wp08-wr08-test-stabilization
     git branch -D scratch/verify-rollback
     ```
   - **Precaution:** Avoid running blind `git rm` or destructive `git checkout 0d1df855c39ab7ac5b8d5492915fb1014dc096cf -- .` directly on the branch, as that would destroy uncommitted modifications.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation, post-merge WR-08 test stabilization, native OS observer tests, runner thread propagation, verified scratch rollback, and test verification for WP-08 are completed and stopped for handoff review. Gate G1 remains `OPEN / IN PROGRESS`. No Gate approval, merge, push, deploy, or next Work Package has been performed.
