# Handoff: WP-07 Save Debounce, Coalescing, and Import Coordination

## Identity

- **Work Package:** `WP-07: Save debounce, coalescing, and import coordination`
- **Phase:** `1 — source and data-model foundation`
- **Component Version:** `SAVE_IMPORT_COORDINATOR_VERSION = "save-import-coordinator.v1"`
- **Baseline Commit:** `55b965dc6781c9371045f2ecf905ec27f52b64a5`
- **Target Branch:** `antigravity/phase-01-save-import-coordinator`
- **Linear Branch Commits:**
  - `3a84402` — `feat(local_agent,tests): implement save-import-coordinator.v1 and tests under WP-07`
- **Gate G1 Status:** `OPEN / IN PROGRESS`

## Scope

Delivers the core save-import coordinator and single-attempt driver under ADR-0010 and WP-07:
1. **Save Debounce & Coalescing Engine:** Fixed 2.0s debounce (`SAVE_DEBOUNCE_NS = 2_000_000_000`), lexical host-native path comparison (`normpath`/`normcase`), ignoring irrelevant sibling files (`.tmp`, `~$`, conflicts, archives, snapshots, directories, read-only notices).
2. **Move Event Support:** Symmetrical Move-in and Move-out matching with strict coordinator target path immutability.
3. **Thread-Safe State Machine:** Opaque `SourceReadAttempt` capability tokens, `SaveCoordinatorView` invariant validation, clean state transitions (`IDLE`, `WAITING`, `RUNNING`, `FAULTED`), and lock-free execution during I/O and parsing.
4. **Follow-Up Coalescing:** Remembers at most one pending follow-up notice during active attempt execution, resolving both expired and future deadlines upon attempt finish.
5. **Outcome Mapping & Recovery:** Automatic retry on direct `XlsxSourceNotReadyError` at completion + 2s; clean transition to `IDLE` on `XlsxSourceReadError` without timer retry of rejected generation; `FAULTED` state on infrastructure/integrity failures with explicit `resume_after_fault`; driver failure guard ensuring tokens never leak.
6. **Reader Driver (`read_due_source`):** Synchronously coordinates attempt reservation, snapshot acquisition (`open_stable_xlsx_snapshot`), streaming read (`read_xlsx_source_snapshot`), verified context exit, attempt bookkeeping, and exception preservation.

## Roadmap traceability

- **Decisions Implemented:** O-31 (Isolated snapshot lifecycle), O-53 (Windows Local Agent stack / execution architecture), O-71 (Bounded memory & linear copy).
- **Roadmap Section 5.2 Step 1 & Step 3:** Completes the local save debounce and coordination foundation for Step 1, preparing the driver for consuming workers in Phase 1 without premature worker loop coupling.
- **Evidence Status:** Recorded under `test-results.txt` and `acceptance-matrix.md`.

## Changed files

- `apps/local_agent/src/accounting_local_agent/__init__.py`: Exported WP-07 symbols (`SAVE_IMPORT_COORDINATOR_VERSION`, `SAVE_DEBOUNCE_NS`, `SaveImportCoordinator`, `SaveCoordinatorView`, `SourceReadAttempt`, `SaveCoordinatorState`, `SaveEventKind`, `SourceReadOutcome`, `SaveCoordinatorError`, `SaveCoordinatorPolicyError`, `SaveCoordinatorStateError`, `read_due_source`).
- `apps/local_agent/src/accounting_local_agent/save_import_coordinator.py`: Core coordinator implementation, state machine, data contracts, and `read_due_source` driver.
- `apps/local_agent/README.md`: Documented coordinator architecture, API contracts, and validation commands.
- `tests/test_save_import_coordinator.py`: Comprehensive test suite containing 25 tests covering SC-01 through SC-16, synthetic fixtures, Hypothesis property testing, and WP-04 Planner composition.
- `handoffs/phase-01/wp-07-save-import-coordinator/handoff.md`: Handoff summary and audit traceability.
- `handoffs/phase-01/wp-07-save-import-coordinator/acceptance-matrix.md`: Detailed requirement acceptance matrix.
- `handoffs/phase-01/wp-07-save-import-coordinator/test-results.txt`: Verbatim command outputs, quality scan records, and benchmark metrics.

## Schema and migrations

- None (WP-07 is purely a local agent save debounce and coordination component).

## Commands and exit codes

1. `uv run ruff format --check .` (Exit 0, 64 files already formatted)
2. `uv run ruff check .` (Exit 0, all checks passed)
3. `uv run mypy .` (Exit 0, 25 source files checked)
4. `uv run mypy --platform win32 .` (Exit 0, 25 source files checked)
5. `uv run pytest tests/test_save_import_coordinator.py -v` (Exit 0, 25 passed in 1.19s)
6. `uv run pytest -v` (Exit 0, 297 passed, 2 skipped in 38.39s)
7. `git diff --check origin/main...HEAD` (Exit 0, 0 whitespace errors)
8. `git ls-files` check (Exit 0, 0 prohibited files)
9. `git grep` sensitive patterns check (Exit 0 clean)

## Tests and evidence

- Full repository suite: 297 tests passing cleanly (2 platform-conditional skipped Windows-handle tests).
- SC-01 to SC-16 coverage: 25 dedicated unit, integration, fault-injection, concurrency, hypothesis, and composition tests.
- 15,000-row streaming benchmark maintained: 12.11s duration (under 30.0s limit), 60.04 MiB peak RSS (under 128 MiB limit).
- Verbatim execution logs and metrics recorded in `test-results.txt`.

## Assumptions and open items

- **Clock Source:** Default clock uses `time.monotonic_ns()`. `FakeClock` used in test fixtures for deterministic microsecond-level time control.
- **Worker Integration:** WP-07 delivers the coordinator and single-attempt driver (`read_due_source`). The long-running watcher/sync worker loop and Telegram notifications belong to subsequent work packages.

## Risks

- **Host Monotonic Clock Anomalies:** If a broken system clock returns backwards or negative time values, `SaveImportCoordinator` fails closed by raising `SaveCoordinatorStateError` without corrupting state.
- **Concurrent Lock Holding:** State lock is held strictly during brief memory-only transitions; locks are never held across file I/O, snapshot copy, or workbook parsing.

## Rollback

1. Delete the branch `antigravity/phase-01-save-import-coordinator`.
2. Checkout `main` at baseline `55b965dc6781c9371045f2ecf905ec27f52b64a5`.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation and test verification for WP-07 are completed and stopped for handoff review. Gate G1 remains `OPEN / IN PROGRESS`. No Gate approval, merge, push, deploy, or next Work Package has been performed.
