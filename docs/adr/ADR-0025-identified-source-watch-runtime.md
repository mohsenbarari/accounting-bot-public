# ADR-0025: Deliver identified source reads through the existing watch runtime

- Status: Issued and accepted by Codex PM after independent plan review; implementation evidence pending
- Date: 2026-09-23
- Phase / gate: Phase 1 / G1 remains OPEN / IN PROGRESS
- Predecessor: completed WP-16 at source head `5f1d45ef0f40615b32b5a886248e0bba09724e53`
- Work package: [WP-17](../work-packages/phase-01/WP-17-identified-source-watch-runtime.md)
- Component version: `identified-source-watch-runtime.v1`

## Context and bounded decision

The accepted source-import store is a library core, and WP-16 completed the dependency-free due-read step that binds the XLSX marker, file digest and Raw snapshot to one verified lease. The remaining `SourceWatchRuntime.run` path still invokes the older raw-result driver and therefore cannot deliver that provenance to a future application composition boundary.

Changing `run` to return identified results would break its accepted consumer type and behavior. Connecting a watch callback directly to SQLite would also cross the still-unproven application-to-persistence dependency and would leave consumer-crash and durable-retry ownership undefined.

Add the smallest safe prerequisite: an additive identified entry point on the existing runtime. It reuses the same observer, coordinator, debounce, wake, lifecycle and teardown machinery but selects the completed WP-16 driver and synchronously delivers its exact `IdentifiedXlsxSource`. It does not import persistence, create an import request, acknowledge a store commit or promise durable redelivery.

This is an ordinary Phase-1 sequencing decision within confirmed O-72, O-73 and O-77 rules and delegated Owner authority. It introduces no new business decision, service, dependency or protected action.

## Public API

Add in `accounting_local_agent.source_watch_runtime` and export through `accounting_local_agent` exactly one new module symbol:

```python
IDENTIFIED_SOURCE_WATCH_RUNTIME_VERSION = "identified-source-watch-runtime.v1"
```

Add exactly this public method to the existing `SourceWatchRuntime` class:

```python
def run_identified(
    self,
    consumer: Callable[[IdentifiedXlsxSource], None],
) -> None: ...
```

The method accepts no source path, source key, file hash, snapshot, coordinator, driver callback, store, connection, transaction, import identity, clock, random source, approval flag, retry callback or public fault hook. Constructor arguments and all existing public APIs remain unchanged.

`SOURCE_WATCH_RUNTIME_VERSION`, `SourceWatchRuntime.run(Callable[[XlsxSourceReadResult], None])`, every runtime state/view/error type and every WP-16 symbol retain their accepted signatures and meanings. No new public exception hierarchy is introduced.

Consumer validation must occur at the same boundary and use the same error type, reason and fixed public message as existing `run`. An invalid consumer cannot construct or start an observer, admit an event, reserve a coordinator attempt or invoke either due-read driver.

## Execution and delivery contract

1. `run_identified` is an alternate terminal entry point for the same single-use runtime object. Calling either `run` or `run_identified` consumes its one lifecycle; the other cannot subsequently or concurrently start another execution.
2. Startup, non-recursive watch scope, initial logical `MODIFIED` notice, exact-path event mapping, two-second debounce, waiting, wakeups, observer liveness checks, stop admission and teardown are exactly those of the accepted runtime.
3. Native callbacks remain nonblocking and perform only accepted event adaptation, coordinator notification and wakeup. They never acquire, parse or persist a workbook.
4. At each accepted due-read point, identified mode calls `read_due_identified_source` and never `read_due_source`. Raw mode continues to call `read_due_source` and never the identified driver.
5. Driver calls use only the runtime-owned coordinator and its unchanged `snapshot_root` and `observation_interval_seconds`. No key, digest or Raw object is separately supplied or reconstructed.
6. A `None` driver result produces no consumer call. A non-None result is passed synchronously and by object identity to `consumer` exactly once for that completed attempt.
7. The driver has already closed ZIP/member handles, verified and cleaned its lease, and completed coordinator success bookkeeping before it returns. The runtime cannot deliver a result before those boundaries.
8. The runtime holds no lifecycle or coordinator state lock while the driver, consumer, snapshot I/O or observer join executes.
9. Consumer completion is not a source-read acknowledgment. A consumer exception cannot change the already completed coordinator attempt or cause this runtime to redeliver the result.
10. Work remains serial: a follow-up admitted while reading or consuming is considered only after the current synchronous consumer returns and only if stop/failure rules still permit another attempt.

A private shared loop or private callable parameter may remove duplication, but it must not become public or alter raw-mode behavior. Importing the module remains inert: no filesystem, database, network, clock, random, observer or thread action occurs at import time.

## Outcome and error contract

Identified mode uses the accepted runtime classifier and lifecycle:

- A direct `XlsxSourceNotReadyError` has already installed the coordinator's fixed retry through WP-16. The runtime remains running, delivers nothing and waits until the accepted retry deadline without requiring a new notice.
- A direct `XlsxSourceReadError`, including `XlsxSourceIdentityError`, has already completed the reader-rejected transition. The runtime remains running, delivers nothing and waits for a fresh matching notice unless an admitted follow-up already exists.
- Acquisition policy, storage, integrity or cleanup failure; an unexpected ordinary exception; coordinator state failure; and every `ExceptionGroup` or `BaseExceptionGroup` are terminal runtime failures. Nested retryable or reader-rejection members do not downgrade a group.
- Consumer failure is terminal and uses the existing consumer-failure reason and public diagnostic boundary. The delivered result is not requeued or presented to a second consumer.
- Observer start, callback, liveness and teardown failures retain their accepted runtime reasons, ordering and independent causes.
- When a primary driver or consumer failure and teardown failure coexist, preserve the accepted primary-first order. Distinct exceptions sharing a cause are not deduplicated.
- Direct `KeyboardInterrupt`, `SystemExit` and other non-`Exception` `BaseException` objects retain the existing runtime teardown and propagation semantics. If combined with teardown failures, the appropriate `BaseExceptionGroup` is retained rather than converted to an ordinary-only group.
- Public runtime and lower-layer messages retain their accepted redaction boundaries. Causes and tracebacks may contain diagnostics and are not represented as sanitized output.

Expected direct read failures never reach the consumer. Fatal failures transition the runtime to `FAILED`, stop further event/read admission and perform owned teardown before `run_identified` returns or raises under the existing runtime contract.

## Lifecycle, stopping and concurrency

The states remain `NEW`, `RUNNING`, `STOPPING`, `STOPPED` and `FAILED` with the accepted path-free view.

- A race between two execution entry calls starts at most one observer and one run loop. The loser receives the accepted invalid-state failure and performs no read or consumer call.
- `request_stop()` remains nonblocking, idempotent and wake-producing. It admits no new attempt after the stop boundary.
- A read admitted before stop may finish and may deliver its successful identified result. A stop requested during its synchronous consumer allows that consumer to finish, then prevents a follow-up read.
- Waiting stop performs no acquisition. Startup failure, observer death and callback failure cannot leave owned observer/emitter threads running.
- Notifications during one identified read still coalesce to at most one coordinator follow-up. Unrelated paths never create or postpone work.
- Tests use bounded Events, Barriers, pipes or acknowledgements and join every worker in `finally`; arbitrary timing sleeps are not concurrency evidence.

## Ownership and architectural boundaries

- `SourceWatchRuntime` owns observer lifecycle, wakeup coordination and serial invocation.
- Its existing `SaveImportCoordinator` owns debounce, attempt tokens, follow-up state and temporary retry scheduling.
- WP-16 owns classification and completion of one identified due-read attempt.
- WP-06 and WP-12 own the temporary lease and same-package marker/hash/Raw provenance and finish cleanup before delivery.
- The caller owns the synchronous consumer and any action it performs after delivery.

No database or durable queue exists in this API. Neither runtime mode imports `accounting_persistence`, constructs `SourceImportRequest`, opens or initializes SQLite, selects a store generation, allocates an import/event identity or calls `commit_source_import`.

Because coordinator success precedes consumer invocation, this API is not a durable delivery or acknowledgment protocol. A later runtime-to-store composition must separately define stable import identity, retry after consumer/process failure, ambiguous commit recovery, restart ownership and shutdown behavior. This ADR does not authorize that later edge.

## Edge behavior

- An existing valid synthetic source is reached through the normal initial notice and debounce; identified mode has no startup bypass.
- Empty four-sheet workbooks with valid markers may be delivered; this runtime applies no requiredness, fiscal, binding or business admission.
- Missing, malformed or ambiguous markers are reader rejection, never automatic enrollment or filename/year fallback.
- A live-file replace follows the WP-06/WP-16 retry and integrity rules. No result can combine a marker, digest and Raw snapshot from different accepted leases.
- A matching notice during read or consumer execution creates at most one follow-up. Each follow-up performs a fresh identified acquisition; no prior object is cached or reused.
- `None` is never delivered. Consumer return values are ignored exactly as in the raw runtime.
- Stop, fatal failure or consumer failure cannot silently start a pending follow-up.
- Identified delivery does not prove registration, active binding, generation freshness, commit eligibility or financial validity.

## Rejected alternatives

- Replacing or changing `run` is rejected because it would break an accepted consumer contract.
- A second copied runtime class is rejected because it would duplicate observer, race and teardown ownership.
- A constructor mode flag is rejected because it would change the accepted constructor and make result typing conditional.
- A caller-supplied driver is rejected because it would expose scheduling and token ownership as a public extension point.
- Direct runtime-to-store wiring is deferred because durable retry identity, crash recovery and the package dependency boundary remain undefined.
- Retaining the Raw result and attaching a marker later is rejected because it reintroduces mixed-generation provenance.

## Cost, rollback and consequences

The runtime retains one coordinator token and one identified result at a time. Identified mode adds O(1) runtime metadata beyond accepted acquisition/identity processing and no second workbook, Raw or event buffer. The accepted 15,000-row, 15-second and 128-MiB acquisition/identity gate remains unchanged; observer scheduling latency is not hidden inside a new threshold.

Before operational use, rollback reverts only the additive version constant, method, package export, README section and focused tests through explicit reviewed commits in an isolated checkout. It preserves the original `run`, does not edit a workbook, does not delete snapshot directories broadly, and does not touch SQLite schema or history. Rollback rehearsal uses only generated disposable fixtures.

This decision makes the watcher capable of delivering marker-aware provenance without claiming persistence. Durable consumer ownership, store composition, restart recovery, enrollment/rollover, real Excel retention, discrepancy reporting and end-to-end G1 evidence remain separate work. No test is claimed as run during planning. G1 remains OPEN / IN PROGRESS.
