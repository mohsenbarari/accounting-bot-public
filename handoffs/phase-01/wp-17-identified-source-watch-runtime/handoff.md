# Handoff: WP-17 Identified Source Watch Runtime

## Identity
- Work package: WP-17 — Deliver identified source results through the watch runtime
- Component version: `identified-source-watch-runtime.v1`
- Phase / gate: Phase 1 / G1 (G1 remains OPEN / IN PROGRESS)
- Baseline commit: `d5da5386accc8cd63d19c90da7e41aab5dd88a25`
- Tested implementation commit: `c143a13340ce2ffe5dc5e665e66963ec3ee56a73`
- Predecessor work package: WP-16 (merge head `5f1d45ef0f40615b32b5a886248e0bba09724e53`)
- Normative document SHA-256 digests (from source capture):
  - `docs/adr/ADR-0025-identified-source-watch-runtime.md`: `527acd804059128617fa904609ffc945626751503926520160dda80508c073e3`
  - `docs/work-packages/phase-01/WP-17-identified-source-watch-runtime.md`: `e539ef654b7a38138706f56aaa4a46dd2e59871a7c6517f691e9a5eaf28d6ba6`

## Scope
- Delivers additive `SourceWatchRuntime.run_identified(self, consumer: Callable[[IdentifiedXlsxSource], None]) -> None` and `IDENTIFIED_SOURCE_WATCH_RUNTIME_VERSION = 'identified-source-watch-runtime.v1'`.
- Shares one single-use runtime lifecycle, observer backend, exact-path event filtering, two-second debounce, liveness checking, stop handling, and teardown ordering with raw mode (`run`).
- In identified mode, calls `read_due_identified_source` lock-free at due points and delivers verified `IdentifiedXlsxSource` synchronously by object identity.
- In raw mode, preserves existing `read_due_source` behavior and exports without modification.
- Handles non-terminal direct `XlsxSourceNotReadyError` (retried by coordinator) and `XlsxSourceReadError`/`XlsxSourceIdentityError` (reader rejection awaiting notice or follow-up).
- Surfaces unexpected, policy, storage, integrity, cleanup, coordinator, or grouped failures as fatal (`FAILED` state).
- Narrowly corrects WP-16 predecessor tests ID-01 and ID-12 in `tests/test_identified_save_import_driver.py` without expanding scope.
- Out of scope: SQLite persistence integration, `SourceImportRequest`, store commits, durable retry/ACK protocol, process-wide single-instance locks, real workbooks or OneDrive.

## Roadmap traceability
- Traces directly to Roadmap sections 5.1 and 19.1 (interactive save tracking, quiet period, identified source ingestion).
- Implements ADR-0025 under Phase 1 delegated authority.
- Advances prerequisite wiring for G1 marker-aware ingestion pipeline without closing G1.

## Changed files
Implementation files changed at tested commit `c143a13340ce2ffe5dc5e665e66963ec3ee56a73`:
- `apps/local_agent/src/accounting_local_agent/source_watch_runtime.py` (SHA-256: `b3d68df71902697a79f0dd9da740836fb955ad697c05f12dc64920690d7a7303`): Added `run_identified`, `IDENTIFIED_SOURCE_WATCH_RUNTIME_VERSION`, and common internal loop helper `_execute_loop`.
- `apps/local_agent/src/accounting_local_agent/__init__.py` (SHA-256: `8ab3d20130e01a6a176bd105416f2236c3efc0a9495db750fe62bd5276c9b100`): Added `IDENTIFIED_SOURCE_WATCH_RUNTIME_VERSION` to public exports.
- `apps/local_agent/README.md` (SHA-256: `507765267cddfca16d5dd039710cd5adb758d5ebab432da172c1a469e606c40b`): Documented identified mode, version constant, lifecycle, and synthetic example.
- `tests/test_identified_source_watch_runtime.py` (SHA-256: `65b5b3faf398c7af8a8312ff32c865ffb8506819ac373f4964c2d8e75c6222e1`): New test module containing 24 test function definitions covering IW-01 through IW-16.
- `tests/test_identified_save_import_driver.py` (SHA-256: `1867152416bc513456e719753a82e8c0077fd37732ab7fc1092439813e515c19`): Narrow corrections to `test_id01_public_api_and_import_inertness` (permitting additive WP-17 constant) and `test_id12_architecture_boundary_preservation` (focusing raw-mode driver checks on execution path).

## Schema and migrations
- No database schema, migration, or SQLite table added or modified.
- The runtime product module (`source_watch_runtime.py`) does not import, reference, or invoke `accounting_persistence`. The test module `tests/test_identified_source_watch_runtime.py` (`test_iw14_persistence_trapping_and_oracle_comparison`) deliberately imports `accounting_persistence` solely as an isolation guard canary to verify that runtime execution does not touch persistence symbols, and `apps/local_agent/README.md` references it only to document boundary separation.

## Commands and exit codes
| Command | Exit Code | Result |
|---|:---:|---|
| `/python/bin/python3.13 -m pytest -vv -- tests/test_identified_source_watch_runtime.py tests/test_identified_save_import_driver.py tests/test_source_watch_runtime.py tests/test_source_watch_runtime_native.py tests/test_save_import_coordinator.py tests/test_xlsx_source_identity_lifecycle.py tests/test_architecture_guard.py tests/test_xlsx_source_identity.py` | 0 | 395 passed in 91.13s |
| `/venv/bin/ruff check --no-cache .` | 0 | All checks passed! |
| `/venv/bin/ruff format --check --no-cache .` | 0 | 148 files already formatted |
| `/python/bin/python3.13 -m mypy --cache-dir /tmp/mypy .` | 0 | Success: no issues found in 61 source files (unused section note for pyproject.toml) |
| `/python/bin/python3.13 -m mypy --platform win32 --cache-dir /tmp/mypy-win32 .` | 0 | Success: no issues found in 61 source files (unused section note for pyproject.toml) |
| `/python/bin/python3.13 -m pytest -q` | 0 | Full suite passed; 2 Windows-only skips in test_xlsx_snapshot_acquisition.py |
| `git diff --check c143a13340ce2ffe5dc5e665e66963ec3ee56a73` | 0 | Clean diff, no whitespace errors (handoff stage preflight) |
| `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-17-identified-source-watch-runtime` | 0 | Handoff validation passed. (handoff stage preflight) |

Required quality gates unreported in implementation receipt:
- Frozen synchronization (`uv sync --frozen --all-packages --all-groups`): Unreported
- Lockfile verification (`uv lock --check`): Unreported
- Separate test collection (`pytest --collect-only`): Unreported
- Repository-wide protected-asset and secret scan: Unreported (formal asset verification unreported)
- IW-16 and WP-16 ID-14 benchmark elapsed duration and peak RSS measurements: Unreported in receipt (execution passed exit 0, but numerical call-window metrics remain pending reporting)
- Native Windows CI execution: Pending (static win32 mypy passed exit 0, but native Windows execution remains pending)

## Tests and evidence
- Focused test suite passed 395 tests across 8 test modules in 91.13s with zero failures.
- `tests/test_identified_source_watch_runtime.py` tests passed exit 0 in the focused Linux run, but do not establish complete criterion evidence: native Windows execution for IW-11 remains pending CI, and numerical call-window duration and peak RSS measurements for IW-16 remain pending reporting.
- Corrected predecessor tests in `tests/test_identified_save_import_driver.py` passed:
  - `TestIdentifiedSaveImportDriverApi::test_id01_public_api_and_import_inertness`
  - `TestIdentifiedSaveImportDriverIntegration::test_id12_architecture_boundary_preservation`
- Platform scope: Linux execution reported in receipt (platform: linux); native Windows execution remains pending CI. Win32 static type check via Mypy succeeded exit 0.

## Assumptions and open items
- Single-use lifecycle per `SourceWatchRuntime` instance.
- Coordinator attempt success occurs before the consumer callback is invoked. If the process or consumer fails after coordinator success, this runtime has no durable acknowledgment or replay protocol. Durable acknowledgment, retry across process restarts, and replay protocol are explicit outstanding prerequisites.
- Persistence integration (`accounting_persistence`), `SourceImportRequest`, store commits, and ambiguous commit recovery are deferred to future work packages.
- Excel COM, OneDrive, marker writing, enrollment, and real data remain unintegrated.
- Gate G1 remains OPEN / IN PROGRESS.

## Risks
- Event queue pressure: If watchdog loses a save event under queue pressure, no notice reaches the coordinator and the changed source may remain unread. Record that overflow detection and reconciliation are deferred under ADR-0011.
- Single-instance scope: No cross-process concurrency locking on the watched file; caller must not construct multiple instances for the same path.
- Liveness detection: Liveness check is bounded by the next loop iteration; a blocked reader I/O or consumer callback delays liveness detection.
- Shutdown: Teardown waits for admitted I/O and joins worker threads without hard killing threads.
- Platform event differences: Windows filesystem notification timing and coalescing may differ from Linux; native Windows CI execution remains pending.

## Rollback
- Revert the 5 changed files to their state prior to commit `c143a13340ce2ffe5dc5e665e66963ec3ee56a73` via git revert in an isolated checkout.
- Rollback affects only the additive `run_identified` method, version constant, exports, documentation, new test module, and two predecessor test adjustments.
- No database tables, schemas, migrations, or external assets are affected.
- Preserves raw-mode `run()` and all earlier WP components intact.

## Protected assets
- Tests in `tests/test_identified_source_watch_runtime.py` and other visible test sources use synthetic in-memory or pytest temporary directory fixtures (`tmp_path`) with generated 4-sheet workbooks.
- The six implementation checks and two preflight checks do not establish that every full-suite test used a pytest temporary workbook or that no protected asset was accessed. If a full-suite test accessed a protected workbook, the six receipt checks and two preflight checks would not establish otherwise.
- Claims are strictly limited to the synthetic fixtures visible in the supplied source context.
- Formal protected-asset verification and repository-wide secret/asset scanning remain unreported in the receipt.
- Formal verification that no real workbooks, OneDrive data, credentials, or real financial data were accessed, modified, or committed remains marked unreported.

## Stop state
- Tested implementation commit is strictly `c143a13340ce2ffe5dc5e665e66963ec3ee56a73`.
- Preflight verification (`git diff --check` and `validate_handoff.py`) provides evidence for this subsequent handoff stage.
- Handoff artifacts are prepared and submitted for independent non-author review.
- The implementer does not approve or self-certify acceptance, deploy code, or close Gate G1.
