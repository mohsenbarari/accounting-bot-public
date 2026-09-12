# Handoff

## Identity

- Phase: 1 — source and data-model foundation; G1 OPEN / IN PROGRESS.
- Work Package: WP-15, `source-import-store.v1`, IS-01 through IS-16, Round 6 review items (R3.a, R3.b, W1, R4), and Windows ctypes HANDLE ABI correction.
- Branch: `antigravity/phase-01-source-import-store`.
- Planning baseline: `9c3aa61fd6b5ead5e711f405d45e3beae920f1f8`.
- Execution baseline: `21b6e0c27b23a5f1803eba7e9e76440e3c598d1f` (PR #50 base; origin/main containing Roadmap 0.58, O-80, ADR-0018, and WP-15; initial execution baseline was `0915081b3a44e08dee935785c6c1ba7a96135fcb`).
- Tested code parent commit SHA: `34321ebf4a3624f21da418c5e7780b290d6cbcfd`.
- Tested code SHA: `aac9c1d5318618ce5af963ab050d4b5dc1bb1939`.
- Pull Request: https://github.com/mohsenbarari/accounting-bot-public/pull/50 (PR #50).
- Delivery: the subsequent documentation commit containing the three handoff files.
- CI and execution provenance:
  * PR #50 run 34692872370: actual Windows failure due to ctypes HANDLE ABI overflow in `get_current_process_rss_mib` (`ctypes.ArgumentError: argument 1: OverflowError: int too long to convert`).
  * Fixed at code commit `aac9c1d5318618ce5af963ab050d4b5dc1bb1939`: explicit `wintypes.HANDLE` / `wintypes.DWORD` prototypes, `K32GetProcessMemoryInfo` fallback, and deterministic regression test `test_r5_04_windows_rss_ctypes_abi_regression`.
  * Controller verified local Linux quality checks on `aac9c1d5318618ce5af963ab050d4b5dc1bb1939` (Checks 0..5, all exit 0).
  * Native CI run 34694490682 on commit `aac9c1d5318618ce5af963ab050d4b5dc1bb1939`: Linux job 103555509165 SUCCESS and Windows job 103555509085 SUCCESS (observed 2026-09-12T12:50Z).
  * Mandatory merge gate: Native CI success on `aac9c1d5318618ce5af963ab050d4b5dc1bb1939` supersedes PENDING only for that tested code commit; the eventual documentation delivery commit requires fresh CI as a mandatory merge gate.
- Preceding stage job ID: `wp15-r6-continuous-s01e0253-2c3fc949`.
- Preceding stage test receipt SHA-256: `3ec4a9e6b65c17cceafa61a3a1ae0f38c280ee9626dcd4516994259fe8bb6364`.
- Preceding stage review result SHA-256: `6e3c49cfd3f0cbd3380770f4371203cb07c56d54ec977c3bcb5c00905c417662`.
- Implementer: Antigravity, applying `accounting-bot-implementer`.
- Reviewer: Codex Project Manager (independent non-author review and acceptance authority).

## Scope

### Requested outcome

Implement the ten-symbol public API in `accounting_persistence.source_import_store`
specified by ADR-0018 with full Round 6 corrective requirements:
1. R3.a: Complete Receipt comparison across all public fields (`disposition`, `import_id`, `source_id`, `fiscal_year`, `base_generation`, `committed_generation`, `file_sha256`, `total_row_count`, `total_counts`, `per_sheet_counts`, `event_count`, `first_sequence`, `last_sequence`), including field-by-field `PlanCounts` comparison and sheet-by-sheet counts, for both `COMMITTED` and `REPLAYED` across all 40 Hypothesis histories in `test_is14`. Negative controls in `test_r5_01` verifying that mutating any field causes `AssertionError`.
2. R3.b: Reject unrelated mutation setup/runtime failures in `test_is14_controlled_product_code_mutations`. Define explicit semantic failure predicate for all 7 mutants. Unrelated `RuntimeError` or raw cancellations fail the harness. Exact-harness mutation negative controls in `test_r5_02` verifying 35 mutation-harness executions across the numbered and lettered subcases followed by three inactive-harness classifier checks (forged raises, spoofed Failed, marker substrings, foreign classes, unrecorded/substituted exceptions).
3. W1: Native Windows path safety in `test_is11_cross_process_crash_and_restart_recovery`. Pass `tests_dir` and `db_file` as subprocess arguments (`sys.argv[1]`, `sys.argv[2]`). Bounded no-ACK cleanup test in `test_is11_negative_no_ack_bounded_exit_and_no_leaks` verifying bounded exit, process termination, pipe closure, and zero thread/worker leaks in `finally` without sleeping. Regression control in `test_r5_03`.
4. R4: Additive, non-overlapping IS-16 timing breakdown (`val_proj_pure + encode_total + sql_write + commit_phase + residual == commit_total`). Executable timing assertions in `assert_r4_timing_evidence` with negative controls in `test_is16_r4_timing_evidence_negative_controls` verifying rejection of negative durations, overrun beyond tolerance, or additive identity violations. Consistent RSS unit conversion: raw KiB from `/usr/bin/time -v` converted to MiB using 1024 (`212,212 KiB = 207.24 MiB`), distinguished from call-window VmRSS (`Baseline 78.02 MiB, Peak 83.84 MiB, Delta 5.82 MiB`).
5. Windows ctypes HANDLE ABI correction (PR #50 run 34692872370):
   In `tests/test_source_import_store.py` helper `get_current_process_rss_mib`, fix ctypes definitions for 64-bit Windows HANDLE:
   - Explicitly configure `kernel32.GetCurrentProcess.argtypes = []` and `kernel32.GetCurrentProcess.restype = wintypes.HANDLE`.
   - In `PROCESS_MEMORY_COUNTERS`, type `cb` and `PageFaultCount` as `wintypes.DWORD` (instead of `ctypes.c_uint32`).
   - Dynamically resolve `GetProcessMemoryInfo` from `psapi.dll`, falling back to `kernel32.K32GetProcessMemoryInfo` if psapi is unavailable, or raising `RuntimeError("Windows GetProcessMemoryInfo API entry point not found")`.
   - Explicitly define prototypes on the resolved function: `mem_func.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]` and `mem_func.restype = wintypes.BOOL`.
   - Add deterministic regression test `test_r5_04_windows_rss_ctypes_abi_regression` with fake Windows DLL/function fixtures (using 64-bit pseudo-handle `0xFFFF_FFFF_FFFF_FFFF`) proving:
     (1) primary psapi path with 64-bit handle and successful RSS conversion to MiB;
     (2) fallback to `kernel32.K32GetProcessMemoryInfo` when psapi is missing;
     (3) failure propagation when API returns 0 or entry point is not found;
     (4) unprototyped call reproduction of PR #50 `ArgumentError: argument 1: OverflowError: int too long to convert` and rejection of invalid argument/return types;
     (5) platform monkeypatches built before patching and fully restored with no real foreign-OS calls on Linux.

### In scope

- Exactly ten public symbols exported from `accounting_persistence.source_import_store`
  and re-exported additively from `accounting_persistence`:
  1. `SOURCE_IMPORT_STORE_VERSION = "source-import-store.v1"`
  2. `SourceImportStoreReason`
  3. `SourceImportStoreError`
  4. `SourceImportDisposition`
  5. `SourceImportRequest`
  6. `SourceImportReceipt`
  7. `SourceImportStoreView`
  8. `initialize_source_import_store`
  9. `read_source_import_store`
  10. `commit_source_import`
- STRICT table DDL, triggers, and foreign keys for Schema v1 under `PRAGMA user_version = 1`.
- Complete schema introspection and canonical SQL comparison validating tables, columns,
  affinities, nullability, defaults, PKs, foreign keys, triggers, and indices with zero extra objects.
- Domain-separated request digest calculation without duplicating workbook buffers in database.
- Idempotent replay checks preceding stale generation checks.
- Event generation conforming strictly to `source-change-event.v1` wire encoding and hash chains.
- Elimination of full historical Raw scans via indexed current-head queries and joined revisions/change-events.
- Concurrency, failure-injection, cross-process crash recovery, and 15,000-row scale benchmark.
- Safe 64-bit Windows ctypes HANDLE ABI typing and deterministic regression test `test_r5_04_windows_rss_ctypes_abi_regression`.

### Out of scope

- Watcher runtime wiring or Excel COM integration (belongs to later work packages).
- Modifying production databases (`data/accounting.sqlite3`), real Excel workbooks, or OneDrive paths.
- Altering core contracts in `accounting_contracts`, root dependencies, CI configurations, or Roadmap gates.

## Roadmap traceability

| Roadmap section / O-item | Status | Implemented behavior |
|---|---|---|
| Section 4.3, 5.2 (Step 5), 15.3, O-80 / ADR-0018 | Approved design; implementation delivered for PM review | Atomic SQLite source revision and change-event store with strict transactionality |
| Phase 1 / WP-15 IS-01..16 | Issued; independent non-author review required | Complete ten-symbol public API, 58 test nodes, scale benchmark |
| O-68 (WP-03), O-69 (WP-04) | Existing accepted contracts | Used for canonical hashing, snapshot validation, and change plan computation |
| O-76 (WP-09, WP-10) | Existing accepted contracts | Used for requiredness validation and fiscal date derivations |
| O-78 (WP-11, WP-13) | Existing accepted contracts | Source binding keys and identity projections preserved |
| O-79 (WP-14) | Existing accepted contract | Source raw codec used for lossless payload encoding and decoding |
| O-46 / O-49 and Gate G1 | PM governance; OPEN / IN PROGRESS | Implementation does not self-approve; Gate G1 remains open |

## Changed files

| File | Change | Reason |
|---|---|---|
| `packages/persistence/src/accounting_persistence/source_import_store.py` | Unchanged from R4 | Product code preserved exactly; SHA-256 is `b9492262e9d77b508afb6df56174679ec8c04f6d3800279abcaff8c4df49cb4c` before and after documentation work |
| `packages/persistence/src/accounting_persistence/__init__.py` | Unchanged from R1 | Additive re-export of the ten public symbols |
| `packages/persistence/README.md` | Unchanged from R1 | Document local SQLite persistence store architecture and usage |
| `tests/test_source_import_store.py` | Modified | 58 test nodes (57 prior + `test_r5_04_windows_rss_ctypes_abi_regression`) covering IS-01 through IS-16, Round 6 items (R3.a, R3.b, W1, R4), and Windows ctypes HANDLE ABI correction (git blob `7750068`, git diff from `f8ccd5c` at commit `aac9c1d5318618ce5af963ab050d4b5dc1bb1939`) |
| `tests/source_import_test_helpers.py` | Unchanged from R1 | Synthetic fixtures, deterministic UUIDv7 generator, and reference models |
| `tests/source_import_store_import_probe.py` | Unchanged from R1 | Subprocess import probe guarding side-effect free import (IS-01) |
| `handoffs/phase-01/wp-15-source-import-store/acceptance-matrix.md` | Modified | Updated criterion-to-test mapping matrix with Round 6 and Windows ABI correction evidence |
| `handoffs/phase-01/wp-15-source-import-store/test-results.txt` | Modified | Verifiable command outputs and benchmark figures for Round 6 and Windows ABI correction |
| `handoffs/phase-01/wp-15-source-import-store/handoff.md` | Modified | Comprehensive implementation handoff document for Round 6 and Windows ABI correction |

## Schema and migrations

- Schema impact: Local SQLite Schema v1 defined under `PRAGMA user_version = 1`.
  Includes 7 STRICT tables (`source_store_meta`, `source_bindings`, `source_imports`,
  `source_import_sheets`, `source_revisions`, `source_memberships`, `change_events`)
  and append-only triggers preventing UPDATE/DELETE on history tables.
- Migration files: None needed. Initial local persistence store component.
- Compatibility: Zero impact on existing packages or contracts. Caller connection settings
  are preserved without side-effects.

## Commands and exit codes

| Command | Exit code | Purpose |
|---|---:|---|
| `/venv/bin/ruff check --no-cache .` | 0 | Controller verified Check 1 on commit aac9c1d: workspace lint check (All checks passed!) |
| `/venv/bin/ruff format --check --no-cache .` | 0 | Controller verified Check 2 on commit aac9c1d: workspace format check (140 files already formatted) |
| `/python/bin/python3.13 -m mypy --cache-dir /tmp/mypy .` | 0 | Controller verified Check 3 on commit aac9c1d: Linux static type check (59 source files, 0 issues) |
| `/python/bin/python3.13 -m mypy --platform win32 --cache-dir /tmp/mypy-win32 .` | 0 | Controller verified Check 4 on commit aac9c1d: Win32 static type check (59 source files, 0 issues) |
| `/python/bin/python3.13 -m pytest -q -- tests/test_source_import_store.py` | 0 | Controller verified Check 0 on commit aac9c1d: focused WP-15 suite (58 passed in 100%) |
| `/python/bin/python3.13 -m pytest -q` | 0 | Controller verified Check 5 on commit aac9c1d: full repository regression (1012 passed, 2 skipped) |
| PR #50 run 34694490682 Linux job 103555509165 | SUCCESS | Native CI Linux job on commit aac9c1d: SUCCESS (observed 2026-09-12T12:50Z) |
| PR #50 run 34694490682 Windows job 103555509085 | SUCCESS | Native CI Windows job on commit aac9c1d: SUCCESS (observed 2026-09-12T12:50Z) |
| `git diff --check 21b6e0c27b23a5f1803eba7e9e76440e3c598d1f...aac9c1d5318618ce5af963ab050d4b5dc1bb1939` | Prescribed | Prescribed PR commit range diff check (unexecuted in controller preflight) |
| `git diff --check 0915081b3a44e08dee935785c6c1ba7a96135fcb` | 0 | Controller preflight delivery tree whitespace check with zero whitespace violations |
| `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-15-source-import-store` | 0 | Controller preflight handoff structural validation across finalized artifacts |
| `uv sync --frozen --all-packages --all-groups` | 0 | Historical toolchain check from parent commit a41539f (Checked 81 packages in 3ms) |
| `uv lock --check` | 0 | Historical lockfile check from parent commit a41539f (Resolved 88 packages in 4ms) |
| `uv run ruff format --check .` | 0 | Historical format check from parent commit a41539f (140 files already formatted) |
| `uv run ruff check .` | 0 | Historical lint check from parent commit a41539f (All checks passed!) |
| `uv run mypy .` | 0 | Historical Linux static type check from parent commit a41539f (59 source files) |
| `uv run mypy --platform win32 .` | 0 | Historical Win32 static type check from parent commit a41539f (59 source files) |
| `uv run pytest tests/test_source_import_store.py -v -o addopts=""` | 0 | Historical reference execution from parent commit a41539f (55 passed in 49.11s) |
| `/usr/bin/time -v uv run pytest tests/test_source_import_store.py -k "test_is16" -s` | 0 | Historical scale benchmark from parent commit a41539f (15,000 rows, 207.24 MiB peak RSS) |
| `uv run pytest tests/test_source_import_store.py -k "test_r5 or test_is11 or test_is14" -v -o addopts=""` | 0 | Historical focused controls from parent commit a41539f (6 passed, 49 deselected) |
| `uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s` | 0 | Historical WP-05 15,000-row reader benchmark from parent commit a41539f |
| `uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s` | 0 | Historical WP-06 15,000-row acquisition benchmark from parent commit a41539f |
| `uv run pytest tests/test_xlsx_source_identity.py::test_xi14_combined_15000_row_benchmark -v -s` | 0 | Historical WP-12 15,000-row identity benchmark from parent commit a41539f |
| `uv run pytest -v` | 0 | Historical full regression from parent commit a41539f (1009 passed, 2 skipped in 175.01s; 1011 items) |

## Tests and evidence

- 58 dedicated test nodes in `tests/test_source_import_store.py` cover all 16 acceptance criteria, review findings R1 through R6, and Windows ctypes HANDLE ABI correction (all verified passing in Check 0 on Linux; native Linux and Windows CI verified in PR #50 run 34694490682 on tested code commit `aac9c1d5318618ce5af963ab050d4b5dc1bb1939`).
- Windows ctypes HANDLE ABI correction and regression test `test_r5_04_windows_rss_ctypes_abi_regression`:
  - On PR #50 run 34692872370, native Windows execution failed in `get_current_process_rss_mib` with `ctypes.ArgumentError: argument 1: OverflowError: int too long to convert`. On 64-bit Windows, `GetCurrentProcess()` returns a 64-bit pseudo-handle `(HANDLE)-1` (`0xFFFFFFFFFFFFFFFF`), which overflows default 32-bit signed C integer argument conversions in unprototyped ctypes calls.
  - Fix: Added explicit prototypes in `get_current_process_rss_mib`:
    * `kernel32.GetCurrentProcess.argtypes = []`
    * `kernel32.GetCurrentProcess.restype = wintypes.HANDLE`
    * `PROCESS_MEMORY_COUNTERS`: typed `cb` and `PageFaultCount` as `wintypes.DWORD` (instead of `ctypes.c_uint32`).
    * Dynamic entry point resolution: attempts `psapi.GetProcessMemoryInfo`, falling back to `kernel32.K32GetProcessMemoryInfo`, or raising `RuntimeError("Windows GetProcessMemoryInfo API entry point not found")`.
    * `mem_func.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]`
    * `mem_func.restype = wintypes.BOOL`
  - Deterministic regression test `test_r5_04_windows_rss_ctypes_abi_regression` executes 5 distinct subcases using simulated 64-bit handle `0xFFFF_FFFF_FFFF_FFFF`:
    1. Primary psapi path with 64-bit handle and successful RSS conversion to MiB.
    2. Fallback to `kernel32.K32GetProcessMemoryInfo` when psapi is missing.
    3. Failure propagation: API returning 0 raises `RuntimeError("Windows GetProcessMemoryInfo failed")`; missing API raises `RuntimeError("Windows GetProcessMemoryInfo API entry point not found")`.
    4. Exact reproduction of PR #50 ArgumentError when unprototyped or using 32-bit handle parameter, plus rejection of invalid argument/return types.
    5. Clean undo of monkeypatches and live platform RSS check.
  - Verified on native Windows CI in PR #50 run 34694490682 (Windows job 103555509085 SUCCESS).
- Scale benchmark IS-16: 15,000 synthetic rows committed (both IS-16 test nodes pass in Check 0; detailed timing breakdown and RSS metrics below retained as historical reference evidence from parent commit `a41539f76e7c57e15cf8a6824edae480095923ca`):
  - Non-overlapping additive phase breakdown: `fixture=2.934s, val_proj_pure=0.736s (digest_pure=0.072s, req_fisc=0.425s, plan=0.240s), encode_total=0.948s (digest_enc=0.349s, rev_enc=0.598s), sql_write=1.786s, commit_phase=0.185s, residual=1.623s, restart_read=5.777s, verify=3.307s, replay=7.211s, gen2=8.457s`.
  - Additive commit check: `val_proj_pure (0.736s) + encode_total (0.948s) + sql_write (1.786s) + commit_phase (0.185s) + residual (1.623s) = 5.278s` (equals total commit window).
  - Executable timing assertions: `assert_r4_timing_evidence` validates non-negative phase durations, constituent projection and encode breakdown identities, accounted time within commit total, and additive identity; `test_is16_r4_timing_evidence_negative_controls` verifies negative controls against negative durations, overruns, and identity violations.
  - Process call window RSS: Baseline 78.02 MiB, Peak 83.84 MiB, Delta 5.82 MiB (Linux `/proc/self/status` `VmRSS` method covering only the `commit_source_import` invocation).
  - Whole-process maximum RSS via `/usr/bin/time -v`: 212,212 KiB = 207.24 MiB (using `212212 / 1024 = 207.238 MiB`) covering the entire pytest execution. Both well below the approved 350 MiB target.
  - Storage: SQLite DB 32.95 MiB, WAL 0.00 MiB.
  - Query bounds: Read 50 queries (15,000 decodes), Replay 54 queries (15,000 decodes).
  - Independent verification: fresh connection close/reopen, independent item-by-item comparison of all 15,000 memberships, revisions, WP-14 Raw values/hashes (`decode_source_raw_row`), and change events against input.
  - Large second generation (15,000 rows with 1,500 edits = 10% edit subset) committed in 8.457s.
- R3.a Complete Receipt Comparison:
  - Assertions check every public field: `disposition`, `import_id`, `source_id`, `fiscal_year`, `base_generation`, `committed_generation`, `file_sha256`, `total_row_count`, `total_counts` (all 4 fields), `per_sheet_counts` (all canonical sheets, all 4 fields each), `event_count`, `first_sequence`, `last_sequence`.
  - Applied to both COMMITTED and REPLAYED dispositions after every step across all 40 Hypothesis generated multi-step histories in `test_is14`.
  - Negative controls in `test_r5_01` verify that structurally valid receipts with wrong `import_id` (valid UUIDv7) or other field modifications fail receipt comparison with `AssertionError`.
- R3.b Robust Mutation Harness:
  - 7 controlled product code mutations define explicit semantic failure predicates:
    1. stale check: pytest Failed DID NOT RAISE `SourceImportStoreError(STALE_STATE)`
    2. unchanged membership: `AssertionError: last_import_id mismatch`
    3. append-only revision: pytest Failed DID NOT RAISE `sqlite3.IntegrityError`
    4. outbox insert: `AssertionError: change_events count mismatch`
    5. sequence advance: `AssertionError: next_sequence mismatch`
    6. request Raw digest: pytest Failed DID NOT RAISE `SourceImportStoreError(IDEMPOTENCY_CONFLICT)`
    7. predecessor link: `SourceImportStoreError(INCONSISTENT_STATE)`
  - Exact-harness mutation negative controls in `test_r5_02` prove 35 mutation-harness executions across the numbered and lettered subcases followed by three inactive-harness classifier checks (unrelated `RuntimeError`, `SystemExit`, `KeyboardInterrupt`, unrelated `pytest.fail.Exception`, exact-message forged `pytest.fail.Exception`, function-style/call-style/context-manager forged `pytest.raises`, spoofed `Failed` classes, marker substrings, unrecorded assertion objects, substituted exception objects, foreign error classes, subclasses) fail the harness rather than being counted as kills.
  - Exact source-byte restoration verified in `finally` blocks and rehearsed in an isolated worktree (`b9492262e9d77b508afb6df56174679ec8c04f6d3800279abcaff8c4df49cb4c`).
- W1 Native Windows Path Safety and Bounded no-ACK Cleanup:
  - Child script generation in `test_is11_cross_process_crash_and_restart_recovery` passes `tests_dir` and `db_file` as `sys.argv` arguments instead of string template interpolation, eliminating `\U` escape `SyntaxError`s on Windows paths.
  - Negative and regression control in `test_r5_03` proves representative Windows paths (`C:\Users\Alice\Accounting Bot's Data\test_source.sqlite3`) cause `SyntaxError` under string interpolation and compile cleanly under `sys.argv`.
  - Bounded no-ACK cleanup test in `test_is11_negative_no_ack_bounded_exit_and_no_leaks` verifies that silent child or retained-handle descendant does not block or leak: exit is bounded within combined timeout, child/descendant processes are terminated and reaped, reader threads and cleanup workers are joined, and stdio pipes are closed in `finally` without sleeping.
- Concurrency and crash safety (IS-09):
  - Bounded `SynchronizedLoserConnection` with thread `Event`/`ACK` instrumentation witnesses both contenders in SQLite with zero arbitrary sleep.
  - Winner succeeds and replays cleanly; loser receives `STALE_STATE` and retries cleanly.
- Error sanitization (IS-12):
  - All public errors verified safe and free from internal paths, SQL fragments, or sensitive raw data.

## Assumptions and open items

- Execution environment: Local checks 0..5 executed on Linux (Python 3.13.15, SQLite 3.53.1).
  Native CI on GitHub Actions PR #50 run 34694490682 verified on tested code commit `aac9c1d5318618ce5af963ab050d4b5dc1bb1939`: Linux job 103555509165 SUCCESS, Windows job 103555509085 SUCCESS (observed 2026-09-12T12:50Z).
  This supersedes PENDING only for commit `aac9c1d5318618ce5af963ab050d4b5dc1bb1939`; subsequent documentation delivery commits require fresh PR CI as a mandatory merge gate.
  The supplied native CI evidence establishes the Linux and Windows job SUCCESS conclusions; node-level logs for platform-conditional tests skipped on Linux (`test_r11_01_windows_handle_protect_from_close_native_oracle` and `test_r9_10_windows_runtime_full_lifecycle_platform_conditional` in `test_xlsx_snapshot_acquisition.py`) were not separately captured.
- Synthetic data only: All fixtures and databases are synthetic.
- Independent acceptance: Non-author review by Codex PM is required for final acceptance.

## Risks

- SQLite concurrency: SQLite operates with file-level/database-level locking. Under high write concurrency,
  `BEGIN IMMEDIATE` serializes transactions cleanly, but callers must handle `STORAGE_FAILURE` / busy timeouts appropriately.
- Diagnostic traces: While public error messages are sanitized, attached `__cause__` exceptions may contain
  internal diagnostics. This is documented in the API specifications.
- ctypes ABI on 64-bit Windows: Win32 API functions taking `HANDLE` or pointer types must always declare explicit `argtypes` and `restype` to prevent 64-bit integer overflow / truncation.

## Rollback

1. Clean working tree pre-check: require `git status --porcelain` to be empty.
2. Single-commit code rollback (prescribed, unrehearsed procedure for commit `aac9c1d5318618ce5af963ab050d4b5dc1bb1939`):
   Revert tested-code commit `aac9c1d5318618ce5af963ab050d4b5dc1bb1939`:
   `git revert --no-edit aac9c1d5318618ce5af963ab050d4b5dc1bb1939`
   Expected verification: path-scoped diff check on the corrected test file against parent commit `34321ebf4a3624f21da418c5e7780b290d6cbcfd`:
   `git diff --quiet 34321ebf4a3624f21da418c5e7780b290d6cbcfd HEAD -- tests/test_source_import_store.py` must exit 0, confirming identical restoration of `tests/test_source_import_store.py` to pre-correction commit `34321ebf4a3624f21da418c5e7780b290d6cbcfd` while preserving subsequent documentation commits.
3. PR-range rollback (prescribed, unrehearsed procedure for PR #50 against actual PR base `21b6e0c27b23a5f1803eba7e9e76440e3c598d1f`):
   Revert PR #50 commits back to its actual PR base:
   `git revert --no-edit 21b6e0c27b23a5f1803eba7e9e76440e3c598d1f..aac9c1d5318618ce5af963ab050d4b5dc1bb1939`
   Expected verification: path-scoped diff check against actual PR base `21b6e0c27b23a5f1803eba7e9e76440e3c598d1f`:
   `git diff --quiet 21b6e0c27b23a5f1803eba7e9e76440e3c598d1f HEAD -- packages/persistence tests/test_source_import_store.py tests/source_import_test_helpers.py tests/source_import_store_import_probe.py` must exit 0, confirming identical restoration of all WP-15 implementation files to PR base `21b6e0c27b23a5f1803eba7e9e76440e3c598d1f` without attempting to revert commits across unrelated baselines.
4. Rehearsal attribution:
   The rollback commands above for `aac9c1d5318618ce5af963ab050d4b5dc1bb1939` (and prior commit `34321ebf4a3624f21da418c5e7780b290d6cbcfd`) are prescribed, unrehearsed procedures with required expected verification conditions. No rollback rehearsal has been executed on `aac9c1d5318618ce5af963ab050d4b5dc1bb1939` or `34321ebf4a3624f21da418c5e7780b290d6cbcfd`. The detailed rehearsal command outputs recorded in `test-results.txt` are retained solely as historical rehearsal evidence captured at parent commit `a41539f76e7c57e15cf8a6824edae480095923ca` (reverting to `7517173da979bb7884efdda3bdc5445e2e563ef8`). PASS and clean-tree statements apply strictly to that historical parent-commit rehearsal.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated by implementation.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation is complete at the tested-code SHA `aac9c1d5318618ce5af963ab050d4b5dc1bb1939`
and handed off for independent non-author review. No gate approval, merge, or deployment
is performed by the implementer. Gate G1 remains OPEN / IN PROGRESS.
