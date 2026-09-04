# Handoff

## Identity

- Phase: 1 — source and data-model foundation; G1 OPEN / IN PROGRESS.
- Work Package: WP-15, `source-import-store.v1`, IS-01 through IS-16.
- Branch: `antigravity/phase-01-source-import-store`.
- Planning baseline: `9c3aa61fd6b5ead5e711f405d45e3beae920f1f8`.
- Execution baseline: `0915081b3a44e08dee935785c6c1ba7a96135fcb` (clean `origin/main` containing Roadmap 0.58, O-80, ADR-0018, and WP-15).
- Tested code SHA: `01efe3494899265ef8fe655041fe7f3615d913a6`.
- Delivery: the subsequent documentation commit containing the three handoff files.
- Implementer: Antigravity, applying `accounting-bot-implementer`.
- Reviewer: Codex Project Manager (independent non-author review and acceptance authority).

## Scope

### Requested outcome

Implement the ten-symbol public API in `accounting_persistence.source_import_store`
specified by ADR-0018. On a caller-owned SQLite connection, initialize the schema
with STRICT tables and append-only triggers, reconstruct committed store state into an
immutable catalog view, and atomically commit a full workbook snapshot under `BEGIN IMMEDIATE`.
Persist immutable Raw revisions, non-deleting memberships, and durable outbox change events
with deterministic wire bytes, payload hashes, and version hash chains.

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
- Elimination of full historical Raw scans via indexed current-head queries.
- Concurrency, failure-injection, cross-process crash recovery, and 15,000-row scale benchmark.

### Out of scope

- Watcher runtime wiring or Excel COM integration (belongs to later work packages).
- Modifying production databases (`data/accounting.sqlite3`), real Excel workbooks, or OneDrive paths.
- Altering core contracts in `accounting_contracts`, root dependencies, CI configurations, or Roadmap gates.

## Roadmap traceability

| Roadmap section / O-item | Status | Implemented behavior |
|---|---|---|
| Section 4.3, 5.2 (Step 5), 15.3, O-80 / ADR-0018 | Approved design; implementation delivered for PM review | Atomic SQLite source revision and change-event store with strict transactionality |
| Phase 1 / WP-15 IS-01..16 | Issued; independent non-author review required | Complete ten-symbol public API, 30 test nodes, scale benchmark |
| O-68 (WP-03), O-69 (WP-04) | Existing accepted contracts | Used for canonical hashing, snapshot validation, and change plan computation |
| O-76 (WP-09, WP-10) | Existing accepted contracts | Used for requiredness validation and fiscal date derivations |
| O-78 (WP-11, WP-13) | Existing accepted contracts | Source binding keys and identity projections preserved |
| O-79 (WP-14) | Existing accepted contract | Source raw codec used for lossless payload encoding and decoding |
| O-46 / O-49 and Gate G1 | PM governance; OPEN / IN PROGRESS | Implementation does not self-approve; Gate G1 remains open |

## Changed files

| File | Change | Reason |
|---|---|---|
| `packages/persistence/src/accounting_persistence/source_import_store.py` | New file | Core implementation of `source-import-store.v1` |
| `packages/persistence/src/accounting_persistence/__init__.py` | Modified | Additive re-export of the ten public symbols |
| `packages/persistence/README.md` | Modified | Document local SQLite persistence store architecture and usage |
| `tests/test_source_import_store.py` | New file | Comprehensive 30-test suite covering IS-01 through IS-16 and R1..R5 |
| `tests/source_import_test_helpers.py` | New file | Synthetic fixtures, deterministic UUIDv7 generator, and reference models |
| `tests/source_import_store_import_probe.py` | New file | Subprocess import probe guarding side-effect free import (IS-01) |
| `handoffs/phase-01/wp-15-source-import-store/acceptance-matrix.md` | New file | Criterion-to-test mapping matrix |
| `handoffs/phase-01/wp-15-source-import-store/test-results.txt` | New file | Verifiable command outputs and benchmark figures |
| `handoffs/phase-01/wp-15-source-import-store/handoff.md` | New file | Comprehensive implementation handoff document |

## Schema and migrations

- Schema impact: New local SQLite Schema v1 defined under `PRAGMA user_version = 1`.
  Includes 7 STRICT tables (`source_store_meta`, `source_bindings`, `source_imports`,
  `source_import_sheets`, `source_revisions`, `source_memberships`, `change_events`)
  and append-only triggers preventing UPDATE/DELETE on history tables.
- Migration files: None needed. This is the initial local persistence store component.
- Compatibility: Zero impact on existing packages or contracts. Caller connection settings
  are preserved without side-effects.

## Commands and exit codes

| Command | Exit code | Purpose |
|---|---:|---|
| `uv sync --frozen --all-packages --all-groups` | 0 | Verify environment and dependencies |
| `uv lock --check` | 0 | Verify 88-package lockfile consistency |
| `uv run ruff format --check packages/ tests/` | 0 | Verify code formatting across packages/ tests/ (55 files) |
| `uv run ruff check packages/ tests/` | 0 | Verify lint checks across packages/ tests/ |
| `uv run mypy .` | 0 | Linux static type check across 59 source files |
| `uv run mypy --platform win32 .` | 0 | Windows cross-platform static type check across 59 source files |
| `uv run pytest -v tests/test_source_import_store.py` | 0 | 30 dedicated WP-15 tests |
| `uv run pytest -s tests/test_source_import_store.py -k "test_is16"` | 0 | 15,000-row scale benchmark |
| `uv run pytest` | 0 | Full regression suite (984 passed, 2 platform skips) |
| `uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark tests/test_xlsx_source_identity.py::test_xi14_combined_15000_row_benchmark -v -s` | 0 | Existing WP-05, WP-06, WP-12 benchmarks |
| `git diff --check origin/main...HEAD` | 0 | Clean diff check with zero whitespace violations |
| `uv run python .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-15-source-import-store` | 0 | Handoff document structural validation |

## Tests and evidence

- 30 dedicated test nodes in `tests/test_source_import_store.py` cover all 16 acceptance criteria and review findings R1 through R5.
- Scale benchmark IS-16: 15,000 synthetic rows committed in 4.292s.
  Process call window RSS: Baseline 77.13 MiB, Peak 82.96 MiB, Delta 5.82 MiB (Linux `/proc/self/status` `VmRSS` method), well below the approved 350 MiB target. Reference `/usr/bin/time -v` maximum RSS observed ~149.66 MiB.
  Restart/read took 4.768s; idempotent replay took 5.840s. SQLite DB size is 32.95 MiB.
  Large second generation (15,000 rows with 1,500 edits) committed in 6.602s.
- Concurrency and crash safety: Tested with thread barriers (IS-09), trigger failure injection (IS-10),
  and subprocess termination during open transactions (IS-11).
- Error sanitization: All public errors verified safe and free from internal paths, SQL fragments, or sensitive raw data (IS-12).

## Assumptions and open items

- Execution environment: Tests executed on Linux (Python 3.13.15, SQLite 3.53.1).
  Windows-specific COM/Excel tests were skipped as expected on Linux and will run in Windows CI.
- Synthetic data only: All fixtures and databases are synthetic.
- Independent acceptance: Non-author review by Codex PM is required for final acceptance.

## Risks

- SQLite concurrency: SQLite operates with file-level/database-level locking. Under high write concurrency,
  `BEGIN IMMEDIATE` serializes transactions cleanly, but callers must handle `STORAGE_FAILURE` / busy timeouts appropriately.
- Diagnostic traces: While public error messages are sanitized, attached `__cause__` exceptions may contain
  internal diagnostics. This is documented in the API specifications.

## Rollback

1. Verify clean status: `git status --porcelain`.
2. Rollback rehearsal was verified on branch `scratch/rehearse-wp15-rollback` by reverting commit `01efe3494899265ef8fe655041fe7f3615d913a6`.
3. `git diff --quiet 52447c2d9dc18b93dbdb967d81ac132aafd82761` returned exit code 0, confirming exact equality with code parent.
4. For full delivery rollback, revert the delivery commit followed by `01efe3494899265ef8fe655041fe7f3615d913a6`.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated by implementation.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation is complete at the tested-code SHA `01efe3494899265ef8fe655041fe7f3615d913a6`
and handed off for independent non-author review. No gate approval, merge, or deployment
is performed by the implementer. Gate G1 remains OPEN / IN PROGRESS.
