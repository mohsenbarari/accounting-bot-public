# ADR-0024: Coordinate one due identified XLSX read before store integration

- Status: Issued and accepted by Codex PM after independent plan review; implementation evidence pending
- Date: 2026-09-15
- Phase / gate: Phase 1 / G1 remains OPEN / IN PROGRESS
- Predecessor: completed WP-15 at source head `7c3657dd53d1d813c34418f750b204f30ae4992a`
- Work package: [WP-16](../work-packages/phase-01/WP-16-identified-save-import-driver.md)
- Component version: `identified-save-import-driver.v1`

## Context and bounded decision

The accepted save pipeline has two disconnected application paths. WP-07/WP-08 reserve a due attempt and call `read_due_source`, which returns an unidentified WP-05 Raw result. WP-12 separately acquires one stable lease and returns an `IdentifiedXlsxSource` whose marker key, file digest and Raw snapshot came from the same XLSX package and whose lease cleanup completed before delivery.

WP-15 completed the atomic SQLite library core, but a safe runtime consumer cannot use it while the scheduled path still delivers an unbound Raw snapshot. Re-associating a key or file hash after that delivery would restore the generation-mixing risk resolved by WP-12.

An earlier plan proposed a local-agent adapter importing `accounting_persistence`. Independent plan review found that the supplied evidence did not independently establish that direct runtime dependency and its frozen lock resolution. Protected TOML files are not valid worker context, and this decision does not infer permission to change them.

Select the earlier, dependency-free prerequisite instead: add an identified-source variant of the existing due-read driver. It reserves the same coordinator token and invokes the complete WP-12 read once. It does not import or call persistence, change dependency metadata, initialize a store or construct an import request. ADR-0018 and WP-15 remain unchanged library boundaries for a later package after all intervening provenance and durable retry prerequisites are established.

This is a technical sequencing decision within confirmed O-72/O-73/O-77 rules and delegated Phase-1 authority. It introduces no Owner business decision and uses only synthetic evidence.

## Public API

Add to `accounting_local_agent.save_import_coordinator` and export through `accounting_local_agent` exactly two symbols:

- `IDENTIFIED_SAVE_IMPORT_DRIVER_VERSION`
- `read_due_identified_source`

The version constant is exactly `identified-save-import-driver.v1`.

Exact signature:

```python
read_due_identified_source(
    coordinator: SaveImportCoordinator,
    *,
    snapshot_root: Path,
    observation_interval_seconds: float,
) -> IdentifiedXlsxSource | None
```

The function accepts no source path, source key, snapshot, file hash, reader callback, store, transaction, plan, import/event identity, time source, random source, approval flag or public fault hook. The configured source can only come from the coordinator, and the returned key/hash/Raw can only come from the one WP-12 result.

The coordinator root must be an exact `SaveImportCoordinator`; subclasses and foreign lookalikes are rejected before property access or state mutation with:

```text
TypeError: Invalid identified save import driver input.
```

No new public exception hierarchy is introduced. Existing WP-06, WP-07 and WP-12 exception types and fixed public messages remain authoritative.

## Algorithm and success boundary

1. Validate the exact coordinator root without invoking arbitrary descriptors or repr.
2. Call `coordinator.take_due()` exactly once.
3. If no attempt is due, return `None`. Do not invoke the filesystem, WP-12 reader or any state transition beyond the observational reservation check.
4. For the returned opaque attempt, read `coordinator.source_path` and invoke `read_identified_xlsx_source` exactly once with that path and the unchanged `snapshot_root` and `observation_interval_seconds` arguments.
5. Do not hold coordinator state locks across acquisition, ZIP/XML reading, close, lease verification or cleanup.
6. After WP-12 returns, complete the coordinator attempt with exactly the success transition already used by `read_due_source`.
7. Return the exact WP-12 object only after success bookkeeping completes. Do not copy its snapshot, retain another workbook buffer or independently expose/rebuild its key or hash.

`read_identified_xlsx_source` already returns only after its ZIP and member streams close and the WP-06 lease passes exit integrity verification and cleanup. Therefore the driver must not announce success or expose the result earlier. A failure from close, integrity verification, cleanup or coordinator completion prevents any result from escaping.

## Outcome and error contract

The driver reuses the accepted WP-07 outcome semantics rather than creating a second scheduler taxonomy.

### Source not ready

A direct `XlsxSourceNotReadyError` is completed using exactly the source-not-ready outcome used by `read_due_source`. The same exception object is re-raised. The coordinator schedules the accepted retry no earlier than the fixed two-second interval after completion, without requiring a new notice. A pre-existing follow-up is not lost.

Only a direct error receives this classification. An `ExceptionGroup` or `BaseExceptionGroup` containing a not-ready member remains a grouped fatal failure because the outcome is ambiguous.

### Reader or identity rejection

A direct `XlsxSourceReadError`, including `XlsxSourceIdentityError`, is completed using exactly the existing reader-rejected outcome and re-raised with identity preserved. It does not schedule autonomous retry. The coordinator waits for a fresh matching save, while retaining a follow-up notice already admitted during the attempt.

This rule covers invalid/missing/ambiguous source markers and WP-05 package/header/cell/formula/UUID rejection. It does not reinterpret those failures as enrollment permission or repair a workbook.

### Fatal and grouped failures

A direct non-retryable acquisition policy, storage, integrity or cleanup error; an unexpected ordinary exception; and any `ExceptionGroup` or `BaseExceptionGroup` use the existing token-scoped fatal transition. The coordinator becomes faulted and requires its accepted explicit resume operation. Nested types do not downgrade a group to retryable or reader-rejected.

If identified reading and lease exit both fail, preserve their accepted order. If attempt completion or guarded fault handling also fails, preserve the primary error first and each later state-management error after it. Distinct exception objects sharing one cause are not deduplicated.

Direct `KeyboardInterrupt`, `SystemExit` and other non-Exception `BaseException` instances propagate with original identity after successful token-scoped fault bookkeeping. If bookkeeping also fails, use `BaseExceptionGroup`; ordinary-only combinations use `ExceptionGroup`. The adapter does not flatten an existing group or replace accepted public messages. Chained causes and tracebacks may retain diagnostic values and are outside public redaction guarantees.

Errors from `take_due()` propagate without calling WP-12 or attempting ownership of an absent token. Errors after a token is reserved must not leave that token silently running.

## Scheduling, concurrency and follow-up

The accepted coordinator remains the sole owner of debounce and attempt state:

- concurrent driver calls cannot reserve the same attempt;
- a losing not-due call returns `None` and performs no I/O;
- notices admitted while the identified read is running coalesce into at most one follow-up;
- unrelated notices do not create or delay work;
- source-not-ready retry, reader rejection, fatal state and explicit resume retain their WP-07 meanings;
- the driver adds no sleep, thread, queue, timer or process-wide lock.

Race tests use barriers, events, pipes or explicit acknowledgements with bounded waits. Every worker must report errors and be joined or terminated in `finally`; timing sleeps are not evidence.

## Ownership and architectural boundaries

- `SaveImportCoordinator` owns the opaque attempt and temporary scheduling state.
- WP-06 owns only the exact lease directory/artifacts created for the call and cleans them before delivery.
- WP-12 owns source marker discovery and binding marker/file digest/Raw to one acquired package.
- The caller owns the source file, snapshot-root policy and runtime lifecycle; this function neither edits nor watches the source.
- No database connection exists in this API. The function must not import `accounting_persistence`, call WP-15, open a database path or construct persistence DTOs.
- `SourceWatchRuntime` remains unchanged and continues using its existing raw-result driver. Switching it to identified results requires a later reviewed package with an explicit consumer and lifecycle contract.
- Existing `SAVE_IMPORT_COORDINATOR_VERSION`, `read_due_source`, WP-06/WP-07/WP-08/WP-12 APIs, error taxonomies and performance gates remain unchanged.

The absence of a persistence import is normative, not merely an implementation preference. It resolves the unproven dependency edge without authorizing protected manifest/lock changes or moving XLSX concepts into persistence.

## Edge behavior

- Empty four-sheet sources with a valid marker return a valid identified result; this driver applies no business or requiredness admission.
- Missing or malformed markers are reader rejection, not source-not-ready, automatic enrollment or fallback to filename/year.
- A live-file replace during acquisition follows WP-06 source-not-ready/integrity rules. Marker and Raw are never read from different accepted leases.
- A save during close or cleanup can create one follow-up, but the current result still succeeds only after its own lease cleanup.
- A follow-up uses a fresh WP-12 acquisition. No key/hash/result from the previous attempt is cached or reused.
- Success followed by a coordinator completion failure is not returned as success.
- Invalid snapshot-root or observation policy follows existing WP-06/WP-12 validation and then the driver’s fatal bookkeeping; it is not silently corrected.
- The function does not infer that an identified result is registered, active, current against a store or authorized for commit.

## Rejected alternatives

- A direct local-agent-to-persistence adapter is not issued because the supplied source-head evidence does not independently close its dependency/lock prerequisite.
- Independently accepting a key, hash or snapshot is rejected because it can mix source generations.
- Changing `read_due_source` to return a different type is rejected as a breaking accepted-contract change.
- Rewiring `SourceWatchRuntime` in this slice is rejected because it would combine driver selection, consumer typing, fatal lifecycle and future persistent retry ownership.
- Retrying marker/Raw rejection automatically is rejected because only source-not-ready has accepted autonomous retry semantics.
- Generating marker, row, import or event identities is outside this read-only component and would require separate authority and contracts.

## Cost, rollback and consequences

The driver retains only one opaque coordinator token and one returned identified result. Its overhead is O(1) beyond accepted WP-06/WP-12 acquisition and reader work, with no second workbook, Raw, encoded-row or event-payload buffer. The existing 15,000-row, 15-second and 128-MiB acquisition/identity gates remain unchanged; no new arbitrary performance threshold is introduced.

Before operational use, rollback reverts only the additive driver constant/function, package exports, README description and focused tests through explicit reviewed commits in an isolated checkout. It does not change an XLSX source, delete snapshots broadly, touch a database or alter WP-15 schema/history. Rollback evidence uses fresh generated fixtures only.

This decision makes scheduled reading marker-aware without crossing the unresolved persistence dependency boundary. Persistent retry identities, runtime consumer wiring, store commit, restart recovery, real Excel marker retention, enrollment/rollover, discrepancy reporting and end-to-end import remain separate prerequisites. No test is claimed as run during planning. G1 remains OPEN / IN PROGRESS.
