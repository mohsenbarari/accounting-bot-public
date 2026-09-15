# WP-16: Coordinate one due identified XLSX read

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 marker-aware import-pipeline prerequisite; cannot close G1
- Status: Issued and accepted by Codex PM after independent plan review; implementation evidence pending
- Issued on: 2026-09-15
- Required workflow: `accounting-bot-implementer`; non-author implementation review required
- Implementation branch: `antigravity/phase-01-identified-save-import-driver`
- Planning source head: `7c3657dd53d1d813c34418f750b204f30ae4992a`
- Execution baseline: latest clean `origin/main` containing ADR-0024 and this WP; record its actual SHA before coding
- Handoff path: `handoffs/phase-01/wp-16-identified-save-import-driver/`
- Gate state: G1 remains OPEN / IN PROGRESS

## Objective and traceability

Add the two-symbol `identified-save-import-driver.v1` API defined by [ADR-0024](../../adr/ADR-0024-identified-save-import-driver.md). It reserves one due WP-07 coordinator attempt, invokes the accepted WP-12 identified-source reader exactly once for the coordinator’s configured source, and returns the exact `IdentifiedXlsxSource` only after ZIP close, lease integrity verification and cleanup have succeeded.

This is the smallest safe integration after completed WP-15. The atomic store is a library core, but the accepted runtime path still delivers an unidentified WP-05 result. Connecting that result directly to persistence would lose the marker/key/hash/snapshot provenance established by WP-12. Marker-aware due-read delivery is therefore the immediate prerequisite before any runtime-to-store consumer or durable retry owner is introduced.

Independent review rejected the earlier direct local-agent-to-persistence adapter because the supplied planning evidence did not independently establish that package dependency and frozen resolution. This WP does not assert or introduce that edge: it imports no `accounting_persistence` module, changes no manifest or lockfile and does not call WP-15. ADR-0018 and WP-15 are context only for the explicit persistence boundary.

The sequencing is an ordinary Phase-1 technical decision under existing Owner authority. Implementation, independent review, PR and green native-CI merge require no renewed ordinary Owner approval.

All workbooks, UUIDs, paths and filesystem events used for evidence must be unmistakably synthetic and temporary. Never inspect or modify a real/reference workbook or copy, OneDrive data, a real/sample database, credentials, production state or another protected asset.

## Public API

Add to `accounting_local_agent.save_import_coordinator` and export from `accounting_local_agent` exactly:

1. `IDENTIFIED_SAVE_IMPORT_DRIVER_VERSION`
2. `read_due_identified_source`

The version is exactly `identified-save-import-driver.v1`. Existing `SAVE_IMPORT_COORDINATOR_VERSION`, `read_due_source` and every accepted public signature remain unchanged.

Exact signature:

```python
read_due_identified_source(
    coordinator: SaveImportCoordinator,
    *,
    snapshot_root: Path,
    observation_interval_seconds: float,
) -> IdentifiedXlsxSource | None
```

The function accepts no alternate source path, key, snapshot, file hash, reader callback, store, transaction, import/event identity, clock, random generator, approval flag or public fault hook.

An invalid coordinator root is rejected before property access or state mutation with exact `TypeError("Invalid identified save import driver input.")`. The existing WP-06/WP-12 validation and error contracts govern `snapshot_root` and `observation_interval_seconds`; the driver forwards them unchanged and does not normalize, weaken or reinterpret their policy.

## Required behavior

1. Call `coordinator.take_due()` exactly once. If no attempt is due, return `None` without acquisition, filesystem access or coordinator mutation beyond the observational `take_due` call.
2. For a reserved attempt, derive the source path only from `coordinator.source_path` and call `read_identified_xlsx_source` exactly once with that path and the supplied snapshot parameters.
3. Do not hold a coordinator state lock while acquisition, ZIP/XML reading, lease verification or cleanup runs.
4. Return the exact `IdentifiedXlsxSource` object from WP-12, without cloning or separately associating its key, file hash, byte count, Raw snapshot or locations.
5. Mark success only after `read_identified_xlsx_source` has returned, which proves its owned ZIP/member handles are closed and the WP-06 lease has passed exit verification and cleanup. If success bookkeeping fails, return no identified result.
6. A direct `XlsxSourceNotReadyError` uses exactly the existing `read_due_source` source-not-ready outcome: re-raise the same exception object and schedule the accepted fixed retry without requiring a new notice.
7. A direct `XlsxSourceReadError`, including `XlsxSourceIdentityError`, uses exactly the existing reader-rejected outcome: re-raise the same object, wait for a fresh matching save, and preserve an already admitted follow-up.
8. A non-retryable acquisition policy/storage/integrity/cleanup failure, an unexpected ordinary exception, or any `ExceptionGroup`/`BaseExceptionGroup` faults the coordinator exactly as the accepted driver does. It is never reduced to source-not-ready or reader-rejected merely because a nested member has that type.
9. `KeyboardInterrupt`, `SystemExit` and other direct non-Exception `BaseException` objects retain identity after token-scoped fault bookkeeping. If bookkeeping also fails, preserve primary first and bookkeeping failure second in the appropriate ordered exception group.
10. Preserve separate failures from the identified read, lease exit, coordinator finish and guarded fault transition. Distinct exceptions sharing a cause are not deduplicated, and no successful result masks a cleanup or state-transition failure.
11. Notifications admitted during the identified read produce at most the accepted single follow-up. Concurrent callers cannot acquire the same attempt; the loser returns `None` and performs no read.
12. The driver opens no path itself, starts no observer/thread, changes no runtime lifecycle, persists no retry identity and performs no database, network, clock, random or UUID-generation action.
13. `SourceWatchRuntime` and existing `read_due_source` remain byte-for-behavior compatible. This WP does not switch the runtime consumer to the new function.
14. Public error messages and reprs retain accepted redaction boundaries. Chained causes and tracebacks may contain diagnostics and are not represented as sanitized output.

## Acceptance matrix

Expected calls, coordinator states, deadlines and returned provenance must come from literal fixtures or an independent state oracle, never from the product helper under test. Give every case a distinct Pytest node ID.

| ID | Nonoverlapping required evidence |
|---|---|
| ID-01 | Assert the exact two additive exports, constant value and function signature while proving all prior exports/signatures/version constants are unchanged. Fresh target-module import starts no filesystem, database, network, clock, random or thread action; an injected side-effect control must fail. |
| ID-02 | Cover admission only: invalid coordinator roots fail with the fixed TypeError before descriptor/property access, while a valid not-due coordinator returns `None`, invokes no lower reader and leaves its view observationally unchanged. |
| ID-03 | Cover successful call provenance only: the lower reader receives exactly the coordinator-owned path and unchanged snapshot arguments once; the function returns the identical lower result and exposes no alternate path/key/hash/snapshot input. |
| ID-04 | Prove success ordering with explicit acknowledgements: ZIP/member close, lease verification and cleanup finish before coordinator success, and success finishes once. Inject failure at each boundary and prove no result escapes. |
| ID-05 | Exercise direct source-not-ready failures at acquisition observation/copy/container boundaries. The identical error is re-raised, only the accepted retry deadline is installed, no fresh notice is required and no result is retained. |
| ID-06 | Exercise direct Raw-reader and marker-identity rejection separately. Each identical error is re-raised through the existing reader-rejected transition, requires a fresh matching notice, ignores unrelated notices and preserves a previously admitted follow-up. |
| ID-07 | Exercise policy, storage, integrity, cleanup and unexpected ordinary failures separately. Each faults the coordinator without retry/rejection reclassification and requires the existing explicit resume operation. |
| ID-08 | Exercise grouped read/close/lease failures, finish failure, guarded-fault failure, shared causes, `KeyboardInterrupt` and `SystemExit`. Assert ordered membership, correct ExceptionGroup/BaseExceptionGroup choice and direct BaseException identity when bookkeeping succeeds. |
| ID-09 | Use Barriers, Events and acknowledgements without sleeps to race two callers and deliver notices during reading. Exactly one token/read succeeds, the losing call performs no I/O, at most one follow-up remains and every worker is joined in `finally`. |
| ID-10 | Run a generated four-sheet XLSX with a valid synthetic marker through the real acquisition/identity stack. Verify exact marker key, file hash, byte count, Raw values/types/hashes and complete lease cleanup after return. |
| ID-11 | Replace or mutate the synthetic live file at controlled acquisition boundaries and across successive due attempts. Each returned object must bind marker, file hash and Raw to one accepted lease generation; mixed-generation output is impossible. |
| ID-12 | Prove boundary preservation: block any `accounting_persistence` import/call, spy that WP-15 is never reached, preserve existing `read_due_source` behavior and show `SourceWatchRuntime` still invokes its prior raw-result path. |
| ID-13 | Run at least 40 generated coordinator histories against an independent state model, varying due/not-due, success, not-ready, rejection, fatal failure, follow-up and concurrent admission. Targeted mutations must detect early success, duplicate reads, lost follow-up and wrong error classification. |
| ID-14 | Process at least 15,000 generated identified rows through the driver and accepted WP-12 stack. Validate all identities and record driver call-window time and peak RSS while preserving the existing 15-second/128-MiB identity/acquisition gate and without a second workbook buffer. |
| ID-15 | Preserve every existing test/helper and benchmark. Frozen sync/lock, Ruff format/lint, both Mypy targets, collection, architecture guard, full regression, whitespace and public/protected-asset/secret scans pass. Native Windows/Linux CI and all four mandatory Windows symlink cases remain required before acceptance. |

Concurrency and failure tests use bounded Events, Barriers, pipes or acknowledgements. Arbitrary sleeps, public fault switches, weaker assertions, relaxed thresholds and fabricated platform evidence are forbidden.

## Exact implementation files

The implementation stage may change exactly:

- `apps/local_agent/src/accounting_local_agent/save_import_coordinator.py`
- `apps/local_agent/src/accounting_local_agent/__init__.py`
- `apps/local_agent/README.md`
- `tests/test_identified_save_import_driver.py`

The handoff stage changes exactly:

- `handoffs/phase-01/wp-16-identified-save-import-driver/handoff.md`
- `handoffs/phase-01/wp-16-identified-save-import-driver/acceptance-matrix.md`
- `handoffs/phase-01/wp-16-identified-save-import-driver/test-results.txt`

No implementer edit to Roadmap, ADR/WP text, existing tests/helpers, contracts, persistence code, runtime, acquisition/reader/identity modules, dependency manifests, lockfile, CI configuration or benchmark limits is permitted.

## Out of scope

- Calling or modifying WP-15, opening/initializing a database, constructing `SourceImportRequest`, allocating import/event IDs or committing source state.
- Switching `SourceWatchRuntime` to marker-aware delivery or adding a runtime consumer.
- Persistent pending-attempt state, restart recovery, process-wide single-instance ownership or crash-durable retry identities.
- Real workbook/database access, marker/UUID writing, Excel COM, OneDrive or native real-source experiments.
- Enrollment, archive activation, annual rollover, opening balances, schema migration or repair.
- Financial rules, RS/item/alias resolution, affected-domain calculation, Ledger, discrepancy UI, server Sync/ACK/signatures, Telegram, backup/restore or deployment.
- New dependencies or services, production action, G1 closure or weakening an accepted contract/test.

## Validation, ownership and delivery

No test is claimed as run by this planning document. The controller runs complete automatic gates for both workflow stages, including frozen synchronization and lock verification, focused tests, Ruff format/lint, both Mypy targets, collection, architecture guard, full regression, focused ID-14 and predecessor benchmarks, whitespace checks, public/protected-asset/secret scans and final handoff validation.

Static win32 Mypy is not native Windows evidence. Unavailable native execution remains pending until later native CI. The handoff maps ID-01 through ID-15 to exact nodes and independent evidence, records the execution baseline and preceding tested implementation commit without self-reference, supplies source hashes and exact command outputs, and describes platform scope, dependency boundary, error/state evidence and remaining runtime/store limitations.

Rollback reverts only the additive version/function/export, its README section and focused tests through explicit reviewed commits in an isolated checkout. It does not touch an XLSX source, snapshot artifact, database or WP-15 schema. Rehearse rollback only with generated disposable fixtures.

Stop for non-author review after handoff. The implementer cannot accept its own evidence, close G1, deploy or begin WP-17. Independent review, PR and green native-CI merge proceed under existing Owner authority without renewed ordinary approval. G1 remains OPEN / IN PROGRESS.
