# Handoff

## Identity

- Phase: 1 — source and data-model foundation; G1 OPEN / IN PROGRESS.
- Work Package: WP-15, `source-import-store.v1`, IS-01 through IS-16 and Round 4 review items R1 through R4.
- Branch: `antigravity/phase-01-source-import-store`.
- Planning baseline: `9c3aa61fd6b5ead5e711f405d45e3beae920f1f8`.
- Execution baseline: `0915081b3a44e08dee935785c6c1ba7a96135fcb` (clean `origin/main` containing Roadmap 0.58, O-80, ADR-0018, and WP-15).
- Tested code parent commit SHA: `fe3b50707c6694e2235765f5efede6c2ea89c3f0`.
- Tested code SHA: `2f1db9f0b121ef65c4e23357f588836ed2b22628`.
- Delivery: the subsequent documentation commit containing the three handoff files.
- Implementer: Antigravity, applying `accounting-bot-implementer`.
- Reviewer: Codex Project Manager (independent non-author review and acceptance authority).

## Scope

### Requested outcome

Implement the ten-symbol public API in `accounting_persistence.source_import_store`
specified by ADR-0018 with full Round 4 corrective requirements:
1. R1: Validate canonical RFC 9562 UUIDv7 format/version/variant on reconstructed Events, and enforce strict canonical UTC ISO 8601 formatting on creating Import timestamps and event observation timestamps, rejecting UUIDv4 tampered events, impossible calendar dates (`2026-99-99T25:61:61+00:00`), non-UTC offsets (`+03:30`), and offset-free strings with `INCONSISTENT_STATE`.
2. R2: Protect transaction acquisition and transaction execution as one owned lifecycle. Ensure BEGIN and BEGIN IMMEDIATE failures or cancellations (including raw KeyboardInterrupt, SystemExit, and ordinary exceptions) trigger rollback before propagation, leaving `in_transaction == False` and preserving caller-owned transactions.
3. R3: Finish IS-09 and IS-14 evidence: eliminate arbitrary sleep in IS-09 via synchronized connection instrumentation; expand IS-14 Hypothesis test to a full 7-table in-memory oracle modeling all rows, hashes, and receipts across 40 runs; isolate raw payload hashing mutation in probe 6 using Decimal scale variations (`qty="2"` vs `qty="2.0"`).
4. R4: Provide truthful documentation of all 52 actual test nodes, qualify SQL aggregate scans honestly, isolate single-commit and full-package rollback rehearsals in a clean scratch worktree, and separate IS-16 timing breakdowns into validation/projection components and residual overhead.

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
| Phase 1 / WP-15 IS-01..16 | Issued; independent non-author review required | Complete ten-symbol public API, 52 test nodes, scale benchmark |
| O-68 (WP-03), O-69 (WP-04) | Existing accepted contracts | Used for canonical hashing, snapshot validation, and change plan computation |
| O-76 (WP-09, WP-10) | Existing accepted contracts | Used for requiredness validation and fiscal date derivations |
| O-78 (WP-11, WP-13) | Existing accepted contracts | Source binding keys and identity projections preserved |
| O-79 (WP-14) | Existing accepted contract | Source raw codec used for lossless payload encoding and decoding |
| O-46 / O-49 and Gate G1 | PM governance; OPEN / IN PROGRESS | Implementation does not self-approve; Gate G1 remains open |

## Changed files

| File | Change | Reason |
|---|---|---|
| `packages/persistence/src/accounting_persistence/source_import_store.py` | Modified | Core implementation of `source-import-store.v1` with R1 UUIDv7 and canonical UTC validation, R2 owned transaction acquisition cleanup, R3 synchronized concurrency and 7-table oracle support |
| `packages/persistence/src/accounting_persistence/__init__.py` | Unchanged from R1 | Additive re-export of the ten public symbols |
| `packages/persistence/README.md` | Unchanged from R1 | Document local SQLite persistence store architecture and usage |
| `tests/test_source_import_store.py` | Modified | 52 test nodes covering IS-01 through IS-16 and review items R1 through R4 |
| `tests/source_import_test_helpers.py` | Unchanged from R1 | Synthetic fixtures, deterministic UUIDv7 generator, and reference models |
| `tests/source_import_store_import_probe.py` | Unchanged from R1 | Subprocess import probe guarding side-effect free import (IS-01) |
| `handoffs/phase-01/wp-15-source-import-store/acceptance-matrix.md` | Modified | Updated criterion-to-test mapping matrix with Round 4 evidence |
| `handoffs/phase-01/wp-15-source-import-store/test-results.txt` | Modified | Verifiable command outputs and benchmark figures for Round 4 |
| `handoffs/phase-01/wp-15-source-import-store/handoff.md` | Modified | Comprehensive implementation handoff document for Round 4 |

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
| `uv sync --frozen --all-packages --all-groups` | 0 | Verify environment and dependencies |
| `uv lock --check` | 0 | Verify 88-package lockfile consistency |
| `uv run ruff format --check .` | 0 | Verify code formatting across workspace (140 files) |
| `uv run ruff check .` | 0 | Verify lint checks across workspace |
| `uv run mypy .` | 0 | Linux static type check across 59 source files |
| `uv run mypy --platform win32 .` | 0 | Windows cross-platform static type check across 59 source files |
| `uv run pytest tests/test_source_import_store.py -v -o addopts=""` | 0 | 52 dedicated WP-15 tests (all PASS in 44.33s) |
| `/usr/bin/time -v uv run pytest tests/test_source_import_store.py -k "test_is16" -s` | 0 | 15,000-row scale benchmark with RSS and time profiling |
| `uv run pytest tests/test_source_import_store.py -k "test_r2" -v -s -o addopts=""` | 0 | R2 constant statement and decode bounds across 1, 5, 20, 50 generations |
| `uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s` | 0 | WP-05 15,000-row reader benchmark |
| `uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s` | 0 | WP-06 15,000-row acquisition benchmark |
| `uv run pytest tests/test_xlsx_source_identity.py::test_xi14_combined_15000_row_benchmark -v -s` | 0 | WP-12 15,000-row identity benchmark |
| `uv run pytest -v` | 0 | Full regression suite (1006 passed, 2 expected platform skips in 158.72s) |
| `git diff --check 0915081b3a44e08dee935785c6c1ba7a96135fcb...2f1db9f0b121ef65c4e23357f588836ed2b22628` | 0 | Clean diff check with zero whitespace violations |
| `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-15-source-import-store` | 0 | Handoff document structural validation |

## Tests and evidence

- 52 dedicated test nodes in `tests/test_source_import_store.py` cover all 16 acceptance criteria and review findings R1 through R4.
- Scale benchmark IS-16: 15,000 synthetic rows committed.
  - Phase timing breakdown: `fixture=3.112s, val_proj=1.126s (digest=0.472s, req_fisc=0.444s, plan=0.210s), encode=0.930s, sql_write=1.571s, commit_phase=0.215s, residual=1.112s, restart_read=5.717s, verify=3.170s, replay=5.710s, gen2=7.084s`.
  - Process call window RSS: Baseline 77.92 MiB, Peak 83.66 MiB, Delta 5.73 MiB (Linux `/proc/self/status` `VmRSS` method covering only the `commit_source_import` invocation).
  - Whole-process maximum RSS via `/usr/bin/time -v`: 211,828 KB (~206.86 MiB) covering the entire pytest execution. Both well below the approved 350 MiB target.
  - Storage: SQLite DB 32.95 MiB, WAL 0.00 MiB.
  - Query bounds: Read 50 queries (15,000 decodes), Replay 54 queries (15,000 decodes).
  - Independent verification: fresh connection close/reopen, independent item-by-item comparison of all 15,000 memberships, revisions, WP-14 Raw values/hashes (`decode_source_raw_row`), and change events against input.
  - Large second generation (15,000 rows with 1,500 edits = 10% edit subset) committed in 7.084s.
- R2 constant bounds and aggregate query qualification:
  - Verified across histories of 1, 5, 20, and 50 generations.
  - Statement counts remain exactly constant (52 read queries, 70 commit queries across 5, 20, 50 generations).
  - Current-head decode count remains strictly bounded (1 on read, <= 2 on commit).
  - The constant statement count does not imply history-independent total CPU or time: aggregate queries (`COUNT`, `MAX`, `COALESCE`) scan history tables, producing increasing SQLite VM instruction counts (2337 / 4244 / 11474 / 25934 at 1 / 5 / 20 / 50 generations).
- Transaction acquisition ownership and cleanup (R2):
  - `BEGIN` and `BEGIN IMMEDIATE;` protected within try blocks with `_handle_transaction_failure`.
  - Failures occurring before or immediately after BEGIN trigger rollback, leaving `in_transaction == False`, and propagate raw `KeyboardInterrupt`, `SystemExit`, or `ExceptionGroup`/`BaseExceptionGroup`. Pre-existing caller transactions remain untouched.
- Concurrency and crash safety (R3 / IS-09):
  - Bounded `SynchronizedLoserConnection` with thread `Event`/`ACK` instrumentation witnesses both contenders in SQLite with zero arbitrary sleep.
  - Winner succeeds and replays cleanly; loser receives `STALE_STATE` and retries cleanly.
- Property testing and controlled mutations (R3 / IS-14):
  - 40 Hypothesis examples generate multi-step import histories verified against an independent 7-table in-memory oracle modeling complete rows, revisions, lossless raw values/bytes, provenance, and full receipts.
  - 7 controlled product code mutations (stale check, unchanged membership, append-only revision, outbox insert, sequence advance, request raw digest with Decimal scale probe, predecessor link) are detected and restored with exact byte restoration checks.
- Error sanitization: All public errors verified safe and free from internal paths, SQL fragments, or sensitive raw data (IS-12).

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
   Revert tested-code commit `2f1db9f0b121ef65c4e23357f588836ed2b22628`:
   `git revert --no-edit 2f1db9f0b121ef65c4e23357f588836ed2b22628`
   Tree matches pre-round delivery HEAD `fe3b50707c6694e2235765f5efede6c2ea89c3f0` identically (`git diff fe3b50707c6694e2235765f5efede6c2ea89c3f0 HEAD` is empty). Rehearsed in isolated scratch worktree.
3. Full WP-15 package rollback:
   Revert all 7 WP-15 commits:
   `git revert --no-edit 0915081b3a44e08dee935785c6c1ba7a96135fcb..2f1db9f0b121ef65c4e23357f588836ed2b22628`
   Tree matches execution baseline `0915081b3a44e08dee935785c6c1ba7a96135fcb` identically (`git diff 0915081b3a44e08dee935785c6c1ba7a96135fcb HEAD` is empty). Rehearsed in isolated scratch worktree.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated by implementation.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation is complete at the tested-code SHA `2f1db9f0b121ef65c4e23357f588836ed2b22628`
and handed off for independent non-author review. No gate approval, merge, or deployment
is performed by the implementer. Gate G1 remains OPEN / IN PROGRESS.
