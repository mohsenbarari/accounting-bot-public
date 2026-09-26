# Handoff: WP-17 Identified Source Watch Runtime

## Identity
- Work package: WP-17 — Deliver identified source results through the watch runtime
- Component version: `identified-source-watch-runtime.v1`
- Phase / gate: Phase 1 / G1 (G1 remains OPEN / IN PROGRESS)
- Execution baseline: `d5da5386accc8cd63d19c90da7e41aab5dd88a25` (preceding recovery baseline: `95487cb01ea73a232f127d61ba9b1324c13d920d`)
- Tested implementation commit: `45ca7653600bd430d02bba4c9839f18b2413afcf`
- Preceding stage job ID: `p01-wp17-pm-recovery-0-s01e0009-001ee6bf`
- Supplemental receipt SHA-256: `d5cd3ccaf66a07c268450e5586b1b4bbdef30ccaa020e90ba17455ac02222e40`
- Rollback receipt SHA-256: `54e232b86742e079d7db0818548dae7d7f1ca838c99f39cda447a2a6f4bff077`
- Recorded at UTC: `2026-09-26T01:56:40.353522+00:00`
- Full change commit chain: `c143a13340ce2ffe5dc5e665e66963ec3ee56a73`, `9239ea1cd7abae90eb4da1ad68eb16a0329a3cec`, `95487cb01ea73a232f127d61ba9b1324c13d920d`, `45ca7653600bd430d02bba4c9839f18b2413afcf`
- Predecessor work package: WP-16 (merge head `5f1d45ef0f40615b32b5a886248e0bba09724e53`)
- Normative document SHA-256 digests (from source capture):
  - `docs/adr/ADR-0025-identified-source-watch-runtime.md`: `527acd804059128617fa904609ffc945626751503926520160dda80508c073e3`
  - `docs/work-packages/phase-01/WP-17-identified-source-watch-runtime.md`: `e539ef654b7a38138706f56aaa4a46dd2e59871a7c6517f691e9a5eaf28d6ba6`
- Five implementation allowed files SHA-256 digests at tested implementation commit `45ca7653600bd430d02bba4c9839f18b2413afcf`:
  - `apps/local_agent/src/accounting_local_agent/source_watch_runtime.py`: `80a43a67f1bd0111842b53e3d001886c0fb6256ea10709075b501b371b8b62bc`
  - `apps/local_agent/src/accounting_local_agent/__init__.py`: `8ab3d20130e01a6a176bd105416f2236c3efc0a9495db750fe62bd5276c9b100`
  - `apps/local_agent/README.md`: `507765267cddfca16d5dd039710cd5adb758d5ebab432da172c1a469e606c40b`
  - `tests/test_identified_source_watch_runtime.py`: `67da136b62702f63cb2c08f75ef88c0041449687aeec74f59b6cc3a8ee493923`
  - `tests/test_identified_save_import_driver.py`: `1867152416bc513456e719753a82e8c0077fd37732ab7fc1092439813e515c19`

## Scope
- Delivers additive `SourceWatchRuntime.run_identified(self, consumer: Callable[[IdentifiedXlsxSource], None]) -> None` and `IDENTIFIED_SOURCE_WATCH_RUNTIME_VERSION = 'identified-source-watch-runtime.v1'`.
- Shares one single-use runtime lifecycle (`new -> running -> stopping -> stopped` or `failed`), observer backend, exact-path event filtering, two-second debounce, liveness checking, stop handling, and teardown ordering with raw mode (`run`).
- In identified mode, calls `read_due_identified_source` lock-free at due points and delivers verified `IdentifiedXlsxSource` synchronously by object identity.
- In raw mode, preserves existing `read_due_source` behavior and exports without modification.
- Handles non-terminal direct `XlsxSourceNotReadyError` (retried by coordinator) and `XlsxSourceReadError`/`XlsxSourceIdentityError` (reader rejection awaiting notice or follow-up).
- Surfaces unexpected, policy, storage, integrity, cleanup, coordinator, or grouped failures as fatal (`FAILED` state).
- Narrowly corrects WP-16 predecessor tests ID-01 and ID-12 in `tests/test_identified_save_import_driver.py` without expanding scope.
- In recovery commit `45ca7653600bd430d02bba4c9839f18b2413afcf`, addressed worker join edge cases where controlled delayed workers join cleanly during teardown.
- Out of scope: SQLite persistence integration, `SourceImportRequest`, store commits, durable retry/ACK protocol, process-wide single-instance locks, real workbooks or OneDrive.

## Roadmap traceability
- Traces directly to Roadmap sections 5.1 and 19.1 (interactive save tracking, quiet period, identified source ingestion).
- Implements ADR-0025 under Phase 1 delegated authority.
- Advances prerequisite wiring for G1 marker-aware ingestion pipeline without closing G1.

## Changed files
Implementation files changed across scopes:
- Receipt-verified changes in tested implementation commit `45ca7653600bd430d02bba4c9839f18b2413afcf` relative to execution baseline `95487cb01ea73a232f127d61ba9b1324c13d920d`:
  - `apps/local_agent/src/accounting_local_agent/source_watch_runtime.py` (before: `b3d68df71902697a79f0dd9da740836fb955ad697c05f12dc64920690d7a7303`, after: `80a43a67f1bd0111842b53e3d001886c0fb6256ea10709075b501b371b8b62bc`): Worker thread join handling during teardown to ensure owned workers join cleanly even when teardown races with delayed worker loop exit.
  - `tests/test_identified_source_watch_runtime.py` (before: `c7683f85541903c5111ff4e84fc17a31b7716057bedb43525b12e2ddafc7ee04`, after: `67da136b62702f63cb2c08f75ef88c0041449687aeec74f59b6cc3a8ee493923`): Added focused regression test `test_iw09_controlled_delayed_worker_joins_cleanly` under `TestIdentifiedSourceWatchRuntimeLifecycle`.
- Execution baseline diff and commit history scope:
  - WP-17's approved implementation scope spans five files: the two receipt-verified files above (`source_watch_runtime.py` and `tests/test_identified_source_watch_runtime.py`), plus `apps/local_agent/src/accounting_local_agent/__init__.py`, `apps/local_agent/README.md`, and `tests/test_identified_save_import_driver.py`.
  - Historical commit progression: Predecessor WP-16 merged at head `5f1d45ef0f40615b32b5a886248e0bba09724e53`. Execution baseline containing ADR-0025 and WP-17 specification is `d5da5386accc8cd63d19c90da7e41aab5dd88a25`. Branch development introduced the five-file WP-17 implementation across commits `c143a133`, `9239ea1c`, and recovery baseline `95487cb01ea73a232f127d61ba9b1324c13d920d`. Recovery commit `45ca7653600bd430d02bba4c9839f18b2413afcf` applied the clean worker join teardown fix and its regression test node.
  - Complete five-file reviewed change set across verified execution baseline `d5da5386accc8cd63d19c90da7e41aab5dd88a25...45ca7653600bd430d02bba4c9839f18b2413afcf`:
    1. `apps/local_agent/src/accounting_local_agent/source_watch_runtime.py` (SHA: `80a43a67f1bd0111842b53e3d001886c0fb6256ea10709075b501b371b8b62bc`): Additive export `IDENTIFIED_SOURCE_WATCH_RUNTIME_VERSION = 'identified-source-watch-runtime.v1'`, additive method `SourceWatchRuntime.run_identified`, shared execution loop dispatching to `read_due_identified_source` in identified mode, and worker join teardown synchronization.
    2. `apps/local_agent/src/accounting_local_agent/__init__.py` (SHA: `8ab3d20130e01a6a176bd105416f2236c3efc0a9495db750fe62bd5276c9b100`): Export of `IDENTIFIED_SOURCE_WATCH_RUNTIME_VERSION`.
    3. `apps/local_agent/README.md` (SHA: `507765267cddfca16d5dd039710cd5adb758d5ebab432da172c1a469e606c40b`): Documented identified runtime monitoring, version constant, and synthetic library usage example.
    4. `tests/test_identified_source_watch_runtime.py` (SHA: `67da136b62702f63cb2c08f75ef88c0041449687aeec74f59b6cc3a8ee493923`): New comprehensive test suite covering IW-01 through IW-16 and regression test node `test_iw09_controlled_delayed_worker_joins_cleanly`.
    5. `tests/test_identified_save_import_driver.py` (SHA: `1867152416bc513456e719753a82e8c0077fd37732ab7fc1092439813e515c19`): Narrow predecessor-test adjustments to `test_id01_public_api_and_import_inertness` (admitting additive WP-17 version export) and `test_id12_architecture_boundary_preservation` (verifying raw-mode driver selection on execution path while preserving persistence boundary prohibitions).

## Schema and migrations
- No database schema, migration, or SQLite table added or modified.
- The runtime product module (`source_watch_runtime.py`) does not import, reference, or invoke `accounting_persistence`.
- The test module `tests/test_identified_source_watch_runtime.py` (`test_iw14_persistence_trapping_and_oracle_comparison`) imports `accounting_persistence` solely as an isolation guard canary to verify that runtime execution does not touch persistence symbols.
- `apps/local_agent/README.md` references persistence only to document boundary separation.

## Commands and exit codes
### Implementation-stage checks (tested commit 45ca7653600bd430d02bba4c9839f18b2413afcf)
| Command | Exit Code | Result |
|---|:---:|---|
| `/python/bin/python3.13 -m pytest -vv -- tests/test_identified_source_watch_runtime.py tests/test_identified_save_import_driver.py tests/test_source_watch_runtime.py tests/test_source_watch_runtime_native.py tests/test_save_import_coordinator.py tests/test_xlsx_source_identity_lifecycle.py tests/test_architecture_guard.py tests/test_xlsx_source_identity.py` | 0 | 396 passed in 88.04s (0:01:28) |
| `/venv/bin/ruff check --no-cache .` | 0 | All checks passed! |
| `/venv/bin/ruff format --check --no-cache .` | 0 | 150 files already formatted |
| `/python/bin/python3.13 -m mypy --cache-dir /tmp/mypy .` | 0 | Success: no issues found in 61 source files (unused section note for pyproject.toml) |
| `/python/bin/python3.13 -m mypy --platform win32 --cache-dir /tmp/mypy-win32 .` | 0 | Success: no issues found in 61 source files (unused section note for pyproject.toml) |
| `/python/bin/python3.13 -m pytest -q` | 0 | Full suite passed; 2 Windows-only skips in test_xlsx_snapshot_acquisition.py |

### Handoff-stage preflight checks
| Command | Exit Code | Result |
|---|:---:|---|
| `git diff --check 45ca7653600bd430d02bba4c9839f18b2413afcf` | 0 | Clean diff, no trailing whitespace or whitespace errors |
| `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-17-identified-source-watch-runtime` | 0 | Handoff validation passed. |

### Supplemental controller-bound quality gates
| Command | Exit Code | Result |
|---|:---:|---|
| `/root/.local/bin/uv lock --check` | 0 | Resolved 88 packages in 6ms |
| `/root/.local/bin/uv sync --frozen --all-packages --all-groups` | 0 | Resolved 88 packages; synchronized packages successfully |
| `/root/.local/bin/uv run pytest --collect-only -q tests/test_identified_source_watch_runtime.py tests/test_identified_save_import_driver.py` | 0 | tests/test_identified_save_import_driver.py: 70; tests/test_identified_source_watch_runtime.py: 88 |
| `/root/.local/bin/uv run pytest -q -s tests/test_identified_save_import_driver.py::TestIdentifiedSaveImportDriverBenchmark::test_id14_identified_15000_row_benchmark` | 0 | 15,000 rows; duration: 9.781682164873928s (< 15.0s); peak RSS: 81.73046875 MiB (< 128.0 MiB); fixture: 0.6218414921313524s |
| `/root/.local/bin/uv run pytest -q -s tests/test_identified_source_watch_runtime.py::TestIdentifiedSourceWatchRuntimeBenchmark::test_iw16_identified_15000_row_runtime_benchmark` | 0 | 15,000 rows; duration: 8.7487s (< 15.0s); peak RSS: 87.81 MiB (< 128.0 MiB); no second workbook buffer |
| `git diff --check d5da5386accc8cd63d19c90da7e41aab5dd88a25..45ca7653600bd430d02bba4c9839f18b2413afcf` | 0 | Clean diff across execution baseline |

Quality gates and pending status:
- Protected-asset and credential scans: The supplemental receipt supplies changed-file result lists without a scan command or exit code (protected_changed_files: [], credential_pattern_changed_files: []). Without a bound command and exit code, this gate cannot be claimed as passed exit 0; both changed-file and repository-wide scan gates remain pending until bound command results are available.
- Native Windows CI execution: Pending (static win32 mypy passed exit 0, but native Windows execution remains pending CI).
- Rollback rehearsal: Rehearsed four whole-commit reverts against execution baseline d5da5386accc8cd63d19c90da7e41aab5dd88a25 in an isolated worktree (/tmp/wp17-rollback-rehearsal-45ca7653), matching baseline with exit code 0; selective rollback in the presence of later edits remains pending CI verification.

## Tests and evidence
- Focused test suite passed 396 tests across 8 test modules in 88.04s with zero failures.
- `tests/test_identified_source_watch_runtime.py` tests passed exit 0 in the focused Linux run. Native Windows execution for IW-11 remains pending CI.
- IW-16 benchmark passed with measured call-window duration 8.7487s and peak RSS 87.81 MiB (under 15.0s and 128.0 MiB limits) on 15,000 rows.
- WP-16 ID-14 predecessor benchmark passed with measured call-window duration 9.781682164873928s and peak RSS 81.73046875 MiB (under 15.0s and 128.0 MiB limits) on 15,000 rows.
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
- Rehearsal status and scope bounds:
  - Rollback readiness: Rehearsed via whole-commit reverts. The supplemental controller receipt records four whole-commit reverts on a detached worktree checkout at the tested commit (`git worktree add --detach /tmp/wp17-rollback-rehearsal-45ca7653 45ca7653600bd430d02bba4c9839f18b2413afcf`), reverting `45ca7653600bd430d02bba4c9839f18b2413afcf`, `95487cb01ea73a232f127d61ba9b1324c13d920d`, `9239ea1cd7abae90eb4da1ad68eb16a0329a3cec`, and `c143a13340ce2ffe5dc5e665e66963ec3ee56a73` with exit code 0, achieving clean status and exact tree match against execution baseline `d5da5386accc8cd63d19c90da7e41aab5dd88a25`.
  - Rehearsal receipt scope limitation: The rollback rehearsal receipt records whole-commit reverts on a checkout of the tested commit; it records no reverse-patch run, later-edit preservation test, or post-rollback pytest execution. Whole-commit rollback to baseline is verified; selective rollback in the presence of later edits remains an operational procedure pending native CI verification.
  - Scope paths: The recovery commit `45ca7653600bd430d02bba4c9839f18b2413afcf` changed two files relative to recovery baseline `95487cb01ea73a232f127d61ba9b1324c13d920d`, while the full WP-17 implementation change set spans five files relative to execution baseline `d5da5386accc8cd63d19c90da7e41aab5dd88a25` (`source_watch_runtime.py`, `__init__.py`, `README.md`, `tests/test_identified_save_import_driver.py`, `tests/test_identified_source_watch_runtime.py`).
- Selective reversal procedure preserving later edits:
  - Destructive whole-branch resets and blind file checkouts (such as checking out earlier revisions or execution baseline `d5da538` over target file paths) are strictly forbidden, as they unconditionally overwrite files and obliterate later edits made by subsequent commits or concurrent work. Furthermore, attempting `git checkout d5da5386accc8cd63d19c90da7e41aab5dd88a25 -- tests/test_identified_source_watch_runtime.py` fails because this test file did not exist at execution baseline `d5da538`, leaving it unmanaged or in place while discarding edits to other files.
  - Blind deletion (`rm -f tests/test_identified_source_watch_runtime.py`) is strictly forbidden, as it would delete tests or additions introduced by subsequent work packages before conflict inspection.
  - Rollback follows this selective reversal procedure:
    1. Isolated checkout: Perform rollback exclusively in an isolated worktree or branch (`git worktree add ../wp17-rollback-rehearsal HEAD`), never directly mutating an active branch with untracked or later edits.
    2. Selective reversal of product and predecessor test changes via reverse patch:
       Generate the reverse patch across the 4 pre-existing files from verified execution baseline d5da5386accc8cd63d19c90da7e41aab5dd88a25 to limit reversal strictly to WP-17 changes without removing intervening changes:
       `git diff d5da5386accc8cd63d19c90da7e41aab5dd88a25..45ca7653600bd430d02bba4c9839f18b2413afcf -- apps/local_agent/src/accounting_local_agent/source_watch_runtime.py apps/local_agent/src/accounting_local_agent/__init__.py apps/local_agent/README.md tests/test_identified_save_import_driver.py | git apply --reverse --reject`
    3. For `tests/test_identified_source_watch_runtime.py`, inspect all later changes in git history (`git log -p`) before modifying or removing any test code. If a subsequent commit modified existing test cases, assertions, fixtures, or helpers inside the WP-17 test classes (`TestIdentifiedSourceWatchRuntimeApi`, `TestIdentifiedSourceWatchRuntimeLifecycle`, `TestIdentifiedSourceWatchRuntimeNative`, `TestIdentifiedSourceWatchRuntimeIntegration`, `TestIdentifiedSourceWatchRuntimeBenchmark`), removing the entire class would delete those later edits and violate the preservation rule. Instead, require detailed inspection and selective method- or line-level reversal within those classes, reverting only the original WP-17 code lines while preserving all subsequent modifications, assertions, and added test methods intact. An entire test class may be removed only if inspection proves no later commit modified or added code inside that class. Blind deletion (`git rm`) of the whole file is strictly prohibited unless git history confirms zero subsequent commits have touched the file.
    4. Conflict inspection: Verify no `.rej` files remain, ensuring later edits in adjacent code are completely preserved without overwrite.
    5. Predecessor regression verification: Confirm pre-WP-17 baseline functionality remains clean and passing:
       `/python/bin/python3.13 -m pytest -vv -- tests/test_identified_save_import_driver.py tests/test_source_watch_runtime.py tests/test_source_watch_runtime_native.py tests/test_architecture_guard.py`
    6. Asset preservation: Normative documents (`docs/adr/ADR-0025-identified-source-watch-runtime.md`, `docs/work-packages/phase-01/WP-17-identified-source-watch-runtime.md`), source workbooks, temporary snapshot leases, and SQLite database assets remain completely untouched.
    7. Rehearsal verification: Whole-commit rehearsal proved reversion of commits `45ca7653`, `95487cb0`, `9239ea1c`, `c143a133` restores execution baseline `d5da5386accc8cd63d19c90da7e41aab5dd88a25` cleanly with exit code 0. Selective reverse patching with concurrent edits remains an unexecuted operational procedure pending CI execution.

## Protected assets
- Tests in `tests/test_identified_source_watch_runtime.py` and other visible test sources use synthetic in-memory or pytest temporary directory fixtures (`tmp_path`) with generated 4-sheet workbooks.
- The six implementation checks in the receipt do not establish that every full-suite test used a pytest temporary workbook or that no protected asset was accessed.
- Claims are strictly limited to the synthetic fixtures visible in the supplied source context.
- Supplemental controller checks recorded empty lists for changed files (`protected_changed_files: []`, `credential_pattern_changed_files: []`), but did not supply a scan command or exit code.
- Changed-file and repository-wide protected-asset and secret scanning lack bound command execution evidence and remain pending controller execution.

## Stop state
- Tested implementation commit is strictly `45ca7653600bd430d02bba4c9839f18b2413afcf`.
- Preceding stage execution receipt (`job_id: p01-wp17-pm-recovery-0-s01e0009-001ee6bf`) and supplemental checks verified exit code 0 across implementation checks, lock check, frozen sync, test collection, ID-14 benchmark (9.7817s, 81.73 MiB peak RSS), IW-16 benchmark (8.7487s, 87.81 MiB peak RSS), execution baseline diff, and whole-commit rollback rehearsal.
- Handoff-stage preflight checks (`git diff --check` and `validate_handoff.py`) verified exit code 0.
- Changed-file asset and credential scan result lists are empty (`protected_changed_files: []`, `credential_pattern_changed_files: []`), but lacking a bound scan command and exit code, the scan gate remains pending. Native Windows CI execution and selective rollback execution with later edits also remain pending.
- Handoff artifacts are submitted for independent non-author review.
- The implementer does not approve or self-certify acceptance, deploy code, or close Gate G1.
