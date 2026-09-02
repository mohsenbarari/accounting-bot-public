# Handoff: WP-07 Save Debounce, Coalescing, and Import Coordination

## Identity

- **Work Package:** `WP-07: Save debounce, coalescing, and import coordination`
- **Phase:** `1 — source and data-model foundation`
- **Component Version:** `SAVE_IMPORT_COORDINATOR_VERSION = "save-import-coordinator.v1"`
- **Baseline Commit:** `55b965dc6781c9371045f2ecf905ec27f52b64a5`
- **Target Branch:** `antigravity/phase-01-save-import-coordinator`
- **Linear Branch Commits:**
  - `3a84402` — `feat(local_agent,tests): implement save-import-coordinator.v1 and tests under WP-07`
  - `2617237` — `docs(handoff): add WP-07 save import coordinator handoff package`
  - `4f5042c` — `docs(handoff): synchronize commit list in WP-07 handoff`
  - `16a49b5` — `fix(tests): import orig_read from rdr_mod for mypy compliance`
  - `7ccdc9c` — `docs(handoff): finalize commit list in WP-07 handoff package`
  - `efd2990` — `docs(handoff): record final commit in WP-07 handoff`
  - `deca59b` — `fix(local_agent,tests): address Codex review round 1 W7-R1-01 to W7-R1-06`
  - `7dd4aba` — `docs(handoff): update WP-07 handoff package with Codex review round 1 fixes`
  - `ca306cf` — `docs(handoff): finalize commit list in WP-07 handoff package`
- **Gate G1 Status:** `OPEN / IN PROGRESS`

## Scope

Delivers the core save-import coordinator and single-attempt driver under ADR-0010 and WP-07, with all Codex Round 1 review remediations (W7-R1-01 through W7-R1-06):
1. **Save Debounce & Coalescing Engine:** Fixed 2.0s debounce (`SAVE_DEBOUNCE_NS = 2_000_000_000`), lexical host-native path comparison (`normpath`/`normcase`), ignoring irrelevant sibling files (`.tmp`, `~$`, conflicts, archives, snapshots, directories, read-only notices).
2. **Move Event Support:** Symmetrical Move-in and Move-out matching with strict coordinator target path immutability.
3. **Thread-Safe State Machine & Atomic Token Reservation (W7-R1-02):** Opaque `SourceReadAttempt` capability tokens constructed atomically before state mutation with zero state corruption on transient failure; `SaveCoordinatorView` invariant validation; clean state transitions (`IDLE`, `WAITING`, `RUNNING`, `FAULTED`); and lock-free execution during I/O and parsing.
4. **Follow-Up Coalescing:** Remembers at most one pending follow-up notice during active attempt execution, resolving both expired and future deadlines upon attempt finish.
5. **Outcome Mapping & Recovery:** Automatic retry on direct `XlsxSourceNotReadyError` at completion + 2s; clean transition to `IDLE` on `XlsxSourceReadError` without timer retry of rejected generation; `FAULTED` state on infrastructure/integrity failures with explicit `resume_after_fault`.
6. **Single-Path Driver & Cause Preservation (W7-R1-01):** `read_due_source` executes attempt completion exactly once, preserving both original reader causes and bookkeeping exceptions via `ExceptionGroup` without overwriting `__cause__`; token-scoped guard guarantees tokens never leak and never mutates foreign tokens.
7. **Constant & Safe Error Messages (W7-R1-04):** Error boundaries never leak confidential paths or secret markers in error messages.
8. **Strict Types & Enums (W7-R1-05):** Strict integer time enforcement (rejecting booleans/floats) and strict Enum instance checks without implicit string coercion.

## Roadmap traceability

- **Decisions Implemented:** O-31 (Isolated snapshot lifecycle), O-53 (Windows Local Agent stack / execution architecture), O-71 (Bounded memory & linear copy), O-72 (Save debounce, coalescing, and coordinator state machine), ADR-0010 (Save import coordinator architecture).
- **Roadmap Section 5.2 Step 1 & Step 3:** Completes the local save debounce and coordination foundation for Step 1, preparing the driver for consuming workers in Phase 1 without premature worker loop coupling.
- **Evidence Status:** Recorded under `test-results.txt` and `acceptance-matrix.md`.

## Changed files

- `apps/local_agent/src/accounting_local_agent/__init__.py`: Exported WP-07 symbols (`SAVE_IMPORT_COORDINATOR_VERSION`, `SAVE_DEBOUNCE_NS`, `SaveImportCoordinator`, `SaveCoordinatorView`, `SourceReadAttempt`, `SaveCoordinatorState`, `SaveEventKind`, `SourceReadOutcome`, `SaveCoordinatorError`, `SaveCoordinatorPolicyError`, `SaveCoordinatorStateError`, `read_due_source`).
- `apps/local_agent/src/accounting_local_agent/save_import_coordinator.py`: Core coordinator implementation, state machine, data contracts, and `read_due_source` driver.
- `apps/local_agent/README.md`: Documented coordinator architecture, API contracts, and validation commands.
- `tests/test_save_import_coordinator.py`: Comprehensive test suite containing 34 tests covering SC-01 through SC-16, Codex review items W7-R1-01 to W7-R1-06, synthetic fixtures, Hypothesis property testing, and WP-04 Planner composition.
- `handoffs/phase-01/wp-07-save-import-coordinator/handoff.md`: Handoff summary and audit traceability.
- `handoffs/phase-01/wp-07-save-import-coordinator/acceptance-matrix.md`: Detailed requirement acceptance matrix.
- `handoffs/phase-01/wp-07-save-import-coordinator/test-results.txt`: Verbatim command outputs, quality scan records, and benchmark metrics.

## Schema and migrations

- None (WP-07 is purely a local agent save debounce and coordination component).

## Commands and exit codes

1. `uv run ruff format --check .` (Exit 0, 66 files checked)
2. `uv run ruff check .` (Exit 0, all checks passed)
3. `uv run mypy .` (Exit 0, 25 source files checked)
4. `uv run mypy --platform win32 .` (Exit 0, 25 source files checked)
5. `uv run pytest tests/test_save_import_coordinator.py -v` (Exit 0, 34 passed in 1.35s)
6. `uv run pytest -v` (Exit 0, 306 passed, 2 skipped in 37.37s)
7. `git diff --check origin/main...HEAD` (Exit 0, 0 whitespace errors)
8. `git ls-files | grep -E "(\.xlsx$|\.pdf$|\.db$|\.sqlite$|\.env$|secrets|credentials)"` (Exit 1 on grep -> 0 prohibited files)
9. `git grep -i -E "password|secret|api_key|private_key" -- apps/` (Exit 1 on grep -> 0 findings)

## Tests and evidence

- **Full repository suite:** 306 tests passing cleanly on Linux native (2 platform-conditional skipped Windows-handle tests).
- **Coordinator tests:** 34 dedicated unit, integration, fault-injection, concurrency, deterministic barrier, hypothesis, and composition tests.
- **15,000-row streaming benchmark:** 11.71s duration (strictly within 15.0s ceiling), 59.96 MiB peak RSS (strictly within 128.0 MiB ceiling).
- **Windows Platform:** Static type safety validated via `mypy --platform win32` (Exit 0); native runtime execution is pending independent Codex CI execution on PR.
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
