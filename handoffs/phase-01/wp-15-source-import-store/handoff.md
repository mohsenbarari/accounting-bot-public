# Handoff

## Identity

- Phase: 1 — source and data-model foundation; G1 OPEN / IN PROGRESS.
- Work Package: WP-15, `source-import-store.v1`, IS-01 through IS-16 and Round 5 review items (R3.a, R3.b, W1, R4).
- Branch: `antigravity/phase-01-source-import-store`.
- Planning baseline: `9c3aa61fd6b5ead5e711f405d45e3beae920f1f8`.
- Execution baseline: `0915081b3a44e08dee935785c6c1ba7a96135fcb` (clean `origin/main` containing Roadmap 0.58, O-80, ADR-0018, and WP-15).
- Tested code parent commit SHA: `7517173da979bb7884efdda3bdc5445e2e563ef8`.
- Tested code SHA: `a41539f76e7c57e15cf8a6824edae480095923ca`.
- Delivery: the subsequent documentation commit containing the three handoff files.
- Implementer: Antigravity, applying `accounting-bot-implementer`.
- Reviewer: Codex Project Manager (independent non-author review and acceptance authority).

## Scope

### Requested outcome

Implement the ten-symbol public API in `accounting_persistence.source_import_store`
specified by ADR-0018 with full Round 5 corrective requirements:
1. R3.a: Complete Receipt comparison across all public fields (`disposition`, `import_id`, `source_id`, `fiscal_year`, `base_generation`, `committed_generation`, `file_sha256`, `total_row_count`, `total_counts`, `per_sheet_counts`, `event_count`, `first_sequence`, `last_sequence`), including field-by-field `PlanCounts` comparison and sheet-by-sheet counts, for both `COMMITTED` and `REPLAYED` across all 40 Hypothesis histories in `test_is14`. Negative controls in `test_r5_01` verifying that mutating any field causes `AssertionError`.
2. R3.b: Reject unrelated mutation setup/runtime failures in `test_is14_controlled_product_code_mutations`. Define explicit semantic failure predicate for all 7 mutants. Unrelated `RuntimeError` or raw cancellations fail the harness. Negative control in `test_r5_02`.
3. W1: Native Windows path safety in `test_is11_cross_process_crash_and_restart_recovery`. Pass `tests_dir` and `db_file` as subprocess arguments (`sys.argv[1]`, `sys.argv[2]`). Bounded timeout on ACK reading with child stderr reporting. Guaranteed cleanup in `finally`. Regression control in `test_r5_03`.
4. R4: Additive, non-overlapping IS-16 timing breakdown (`val_proj_pure + encode_total + sql_write + commit_phase + residual == commit_total`). Consistent RSS unit conversion: raw KiB from `/usr/bin/time -v` converted to MiB using 1024 (`212,212 KiB = 207.24 MiB`), distinguished from call-window VmRSS (`Baseline 78.02 MiB, Peak 83.84 MiB, Delta 5.82 MiB`).

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

### Out of scope

- Watcher runtime wiring or Excel COM integration (belongs to later work packages).
- Modifying production databases (`data/accounting.sqlite3`), real Excel workbooks, or OneDrive paths.
- Altering core contracts in `accounting_contracts`, root dependencies, CI configurations, or Roadmap gates.

## Roadmap traceability

| Roadmap section / O-item | Status | Implemented behavior |
|---|---|---|
| Section 4.3, 5.2 (Step 5), 15.3, O-80 / ADR-0018 | Approved design; implementation delivered for PM review | Atomic SQLite source revision and change-event store with strict transactionality |
| Phase 1 / WP-15 IS-01..16 | Issued; independent non-author review required | Complete ten-symbol public API, 55 test nodes, scale benchmark |
| O-68 (WP-03), O-69 (WP-04) | Existing accepted contracts | Used for canonical hashing, snapshot validation, and change plan computation |
| O-76 (WP-09, WP-10) | Existing accepted contracts | Used for requiredness validation and fiscal date derivations |
| O-78 (WP-11, WP-13) | Existing accepted contracts | Source binding keys and identity projections preserved |
| O-79 (WP-14) | Existing accepted contract | Source raw codec used for lossless payload encoding and decoding |
| O-46 / O-49 and Gate G1 | PM governance; OPEN / IN PROGRESS | Implementation does not self-approve; Gate G1 remains open |

## Changed files

| File | Change | Reason |
|---|---|---|
| `packages/persistence/src/accounting_persistence/source_import_store.py` | Unchanged from R4 | Product code preserved exactly; reviewed SHA-256 is `b9492262e9d77b508afb6df56174679ec8c04f6d3800279abcaff8c4df49cb4c` |
| `packages/persistence/src/accounting_persistence/__init__.py` | Unchanged from R1 | Additive re-export of the ten public symbols |
| `packages/persistence/README.md` | Unchanged from R1 | Document local SQLite persistence store architecture and usage |
| `tests/test_source_import_store.py` | Modified | 55 test nodes covering IS-01 through IS-16 and Round 5 review items (R3.a, R3.b, W1, R4) |
| `tests/source_import_test_helpers.py` | Unchanged from R1 | Synthetic fixtures, deterministic UUIDv7 generator, and reference models |
| `tests/source_import_store_import_probe.py` | Unchanged from R1 | Subprocess import probe guarding side-effect free import (IS-01) |
| `handoffs/phase-01/wp-15-source-import-store/acceptance-matrix.md` | Modified | Updated criterion-to-test mapping matrix with Round 5 evidence |
| `handoffs/phase-01/wp-15-source-import-store/test-results.txt` | Modified | Verifiable command outputs and benchmark figures for Round 5 |
| `handoffs/phase-01/wp-15-source-import-store/handoff.md` | Modified | Comprehensive implementation handoff document for Round 5 |

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
| `uv sync --frozen --all-packages --all-groups` | 0 | Verify environment and dependencies (Checked 81 packages in 3ms) |
| `uv lock --check` | 0 | Verify 88-package lockfile consistency (Resolved 88 packages in 4ms) |
| `uv run ruff format --check .` | 0 | Verify code formatting across workspace (140 files) |
| `uv run ruff check .` | 0 | Verify lint checks across workspace |
| `uv run mypy .` | 0 | Linux static type check across 59 source files |
| `uv run mypy --platform win32 .` | 0 | Windows cross-platform static type check across 59 source files |
| `uv run pytest tests/test_source_import_store.py -v -o addopts=""` | 0 | 55 dedicated WP-15 tests (all PASS in 49.11s) |
| `/usr/bin/time -v uv run pytest tests/test_source_import_store.py -k "test_is16" -s` | 0 | 15,000-row scale benchmark with RSS and time profiling |
| `uv run pytest tests/test_source_import_store.py -k "test_r5 or test_is11 or test_is14" -v -o addopts=""` | 0 | R3.a, R3.b, W1 controls and affected tests (6 passed in 3.70s) |
| `uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s` | 0 | WP-05 15,000-row reader benchmark |
| `uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s` | 0 | WP-06 15,000-row acquisition benchmark |
| `uv run pytest tests/test_xlsx_source_identity.py::test_xi14_combined_15000_row_benchmark -v -s` | 0 | WP-12 15,000-row identity benchmark |
| `uv run pytest -v` | 0 | Full regression suite (1009 passed, 2 expected platform skips in 175.01s) |
| `git diff --check 0915081b3a44e08dee935785c6c1ba7a96135fcb...a41539f76e7c57e15cf8a6824edae480095923ca` | 0 | Clean diff check with zero whitespace violations |
| `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-15-source-import-store` | 0 | Handoff document structural validation |

## Tests and evidence

- 55 dedicated test nodes in `tests/test_source_import_store.py` cover all 16 acceptance criteria and review findings R1 through R5.
- Scale benchmark IS-16: 15,000 synthetic rows committed.
  - Non-overlapping additive phase breakdown: `fixture=2.934s, val_proj_pure=0.736s (digest_pure=0.072s, req_fisc=0.425s, plan=0.240s), encode_total=0.948s (digest_enc=0.349s, rev_enc=0.598s), sql_write=1.786s, commit_phase=0.185s, residual=1.623s, restart_read=5.777s, verify=3.307s, replay=7.211s, gen2=8.457s`.
  - Additive commit check: `val_proj_pure (0.736s) + encode_total (0.948s) + sql_write (1.786s) + commit_phase (0.185s) + residual (1.623s) = 5.278s` (equals total commit window).
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
  - Negative control in `test_r5_02` proves unrelated `RuntimeError` during fixture setup fails the harness rather than being counted as a kill.
  - Exact source-byte restoration verified in `finally` blocks and rehearsed in an isolated worktree (`b9492262e9d77b508afb6df56174679ec8c04f6d3800279abcaff8c4df49cb4c`).
- W1 Native Windows Path Safety:
  - Child script generation in `test_is11_cross_process_crash_and_restart_recovery` passes `tests_dir` and `db_file` as `sys.argv` arguments instead of string template interpolation, eliminating `\U` escape `SyntaxError`s on Windows paths.
  - Negative and regression control in `test_r5_03` proves representative Windows paths (`C:\Users\Alice\Accounting Bot's Data\test_source.sqlite3`) cause `SyntaxError` under string interpolation and compile cleanly under `sys.argv`.
  - Bounded ACK wait timeout (10s), stderr reporting on failure, and guaranteed `proc.terminate()` / `proc.kill()` in `finally`.
- Concurrency and crash safety (IS-09):
  - Bounded `SynchronizedLoserConnection` with thread `Event`/`ACK` instrumentation witnesses both contenders in SQLite with zero arbitrary sleep.
  - Winner succeeds and replays cleanly; loser receives `STALE_STATE` and retries cleanly.
- Error sanitization (IS-12):
  - All public errors verified safe and free from internal paths, SQL fragments, or sensitive raw data.

## Assumptions and open items

- Execution environment: Tests executed on Linux (Python 3.13.15, SQLite 3.53.1).
  Native Windows handle protection tests (`test_r11_01_windows_handle_protect_from_close_native_oracle` and `test_r9_10_windows_runtime_full_lifecycle_platform_conditional` in `test_xlsx_snapshot_acquisition.py`) were skipped as expected on Linux and will run in Windows CI.
- Synthetic data only: All fixtures and databases are synthetic.
- Independent acceptance: Non-author review by Codex PM is required for final acceptance.

## Risks

- SQLite concurrency: SQLite operates with file-level/database-level locking. Under high write concurrency,
  `BEGIN IMMEDIATE` serializes transactions cleanly, but callers must handle `STORAGE_FAILURE` / busy timeouts appropriately.
- Diagnostic traces: While public error messages are sanitized, attached `__cause__` exceptions may contain
  internal diagnostics. This is documented in the API specifications.

## Rollback

1. Verify clean status: `git status --porcelain`.
2. Single-round code rollback:
   Revert tested-code commit `a41539f76e7c57e15cf8a6824edae480095923ca`:
   `git revert --no-edit a41539f76e7c57e15cf8a6824edae480095923ca`
   Tree matches pre-round delivery HEAD `7517173da979bb7884efdda3bdc5445e2e563ef8` identically (`git diff --quiet 7517173da979bb7884efdda3bdc5445e2e563ef8 HEAD` returns exit code 0). Rehearsed in isolated scratch worktree.
3. Full WP-15 package rollback:
   Revert all 9 WP-15 commits:
   `git revert --no-edit 0915081b3a44e08dee935785c6c1ba7a96135fcb..a41539f76e7c57e15cf8a6824edae480095923ca`
   Tree matches execution baseline `0915081b3a44e08dee935785c6c1ba7a96135fcb` identically (`git diff --quiet 0915081b3a44e08dee935785c6c1ba7a96135fcb HEAD` returns exit code 0). Rehearsed in isolated scratch worktree.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated by implementation.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation is complete at the tested-code SHA `a41539f76e7c57e15cf8a6824edae480095923ca`
and handed off for independent non-author review. No gate approval, merge, or deployment
is performed by the implementer. Gate G1 remains OPEN / IN PROGRESS.
