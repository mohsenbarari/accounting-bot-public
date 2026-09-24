# ADR-0025: Deliver identified source reads through the existing watch runtime

- Status: Issued and accepted by Codex PM after independent plan review; revised after CI correction; implementation evidence pending
- Date: 2026-09-24
- Phase / gate: Phase 1 / G1 remains OPEN / IN PROGRESS
- Predecessor: completed WP-16, merge head `5f1d45ef0f40615b32b5a886248e0bba09724e53`
- Revision dependency snapshot: `85663125a906d55b9c7efad4f3d2412f5e496004`
- Work package: [WP-17](../work-packages/phase-01/WP-17-identified-source-watch-runtime.md)
- Component version: `identified-source-watch-runtime.v1`

## Context and decision

WP-15 is an atomic SQLite library core. WP-16 returns a due-read result whose marker key, file digest and Raw snapshot came from one verified XLSX lease. `SourceWatchRuntime.run` still invokes the older raw-result driver. Add an alternate terminal entry to the existing single-use runtime so a caller can receive the exact identified result. Observer, coordinator, debounce, wake, lifecycle and teardown ownership remain shared. The accepted raw entry retains its type and behavior.

This is an ordinary Phase-1 sequencing decision under existing Owner authority. It creates no application-to-persistence dependency, store acknowledgment or durable redelivery promise. The CI correction permits two narrow predecessor-test updates: WP-16 ID-01 must allow a later additive export while retaining predecessor assertions, and WP-16 ID-12 must test raw-mode driver selection on its execution path rather than reject every identified-driver reference in the module. ID-12 imports three helpers from `tests/test_source_watch_runtime.py`, so that module is required implementation context. The new IW test module is required handoff context for exact node mapping. No implementation evidence is claimed by this revision.

## Public API

Add `IDENTIFIED_SOURCE_WATCH_RUNTIME_VERSION = 'identified-source-watch-runtime.v1'` in `accounting_local_agent.source_watch_runtime` and export it through `accounting_local_agent`. Add exactly `SourceWatchRuntime.run_identified(self, consumer: Callable[[IdentifiedXlsxSource], None]) -> None`.

The method accepts no source path, key, file hash, snapshot, coordinator, driver callback, store, connection, transaction, import identity, clock, random source, approval flag, retry callback or public fault hook. Constructor arguments and all existing public APIs remain unchanged. `SOURCE_WATCH_RUNTIME_VERSION`, `SourceWatchRuntime.run(Callable[[XlsxSourceReadResult], None])`, runtime state/view/error types and WP-16 symbols retain their accepted signatures and meanings. No new public exception hierarchy is introduced. Consumer validation uses the same boundary, error type, reason and fixed message as `run`, before observer creation or start, event admission, attempt reservation or driver invocation.

## Execution and delivery

1. `run` and `run_identified` are alternate terminal entries for one single-use object. Starting either consumes its lifecycle; the other cannot start later or concurrently.
2. Startup, non-recursive watch scope, initial logical `MODIFIED` notice, exact-path event mapping, two-second debounce, waiting, wakeups, observer liveness checks, stop admission and teardown follow the accepted runtime contract.
3. Native callbacks perform only accepted event adaptation, coordinator notification and wakeup. They never acquire, parse or persist a workbook or call a consumer.
4. At each due point, identified mode calls only `read_due_identified_source`; raw mode calls only `read_due_source`. Arguments are the runtime-owned coordinator and unchanged snapshot root and observation interval.
5. `None` causes no consumer call. A non-None result is delivered synchronously, once per completed attempt and by object identity. The runtime never reconstructs or separately associates key, digest or Raw.
6. WP-16 completes ZIP/member close, lease verification, cleanup and coordinator success bookkeeping before returning. No result is delivered earlier. No lifecycle or coordinator lock is held during driver, consumer, snapshot I/O or backend join.
7. Consumer completion is not a source-read acknowledgment. Consumer failure cannot undo the completed coordinator attempt or make this runtime redeliver it. A follow-up admitted during reading or consumption is considered only after synchronous consumer return and only if stop/failure rules allow another attempt.

A private shared loop may avoid duplication but cannot become public or change raw-mode behavior. Importing changed modules remains inert: no filesystem, database, network, clock, random, observer or thread action occurs at import time.

## Outcomes, errors and stopping

A direct `XlsxSourceNotReadyError` has already installed WP-16's fixed retry. The runtime remains running, delivers nothing and waits for that deadline without a new notice. A direct `XlsxSourceReadError`, including `XlsxSourceIdentityError`, has already completed reader rejection. The runtime remains running, delivers nothing and waits for a fresh matching notice unless an admitted follow-up exists.

Acquisition policy, storage, integrity or cleanup failure; unexpected ordinary failure; coordinator state failure; and every `ExceptionGroup` or `BaseExceptionGroup` are terminal. Nested retryable or reader-rejection members never downgrade a group. Consumer failure is terminal under the accepted consumer-failure reason and public diagnostic boundary, with no result requeue. Observer start, callback, liveness and teardown failures retain accepted reasons, ordering and independent causes. Primary driver or consumer failure precedes teardown failures; distinct objects with a shared cause remain distinct. Direct `KeyboardInterrupt`, `SystemExit` and other non-`Exception` `BaseException` objects retain accepted teardown and propagation semantics; combined teardown failure uses the appropriate group kind. Causes and tracebacks may contain diagnostics and are not sanitized-output guarantees.

Expected direct read failures never reach the consumer. Fatal failures enter `FAILED`, stop further event/read admission and complete owned teardown. States remain `NEW`, `RUNNING`, `STOPPING`, `STOPPED` and `FAILED`. `request_stop()` remains nonblocking, idempotent and wake-producing. An admitted read may finish and deliver after stop; stop during its consumer permits that call to finish and suppresses follow-up. Waiting stop performs no acquisition. Startup failure, observer death and callback failure cannot leave owned workers running. Concurrent entry admits at most one observer and loop; losers receive the accepted invalid-state error.

## Ownership and edge behavior

`SourceWatchRuntime` owns observer lifecycle, wakeups and serial invocation. Its coordinator owns debounce, attempt tokens, follow-up and temporary retry scheduling. WP-16 owns classification and completion of one identified due read. WP-06 and WP-12 own lease cleanup and same-package marker, digest and Raw provenance. The caller owns the synchronous consumer and any later action it performs.

Neither runtime mode imports `accounting_persistence`, constructs `SourceImportRequest`, opens or initializes SQLite, selects a generation, allocates an import/event identity or calls `commit_source_import`. Coordinator success precedes consumer invocation, so this API is not a durable delivery or acknowledgment protocol. Later composition must define stable import identity, retry after consumer/process failure, ambiguous commit recovery, restart ownership and shutdown behavior in a separate reviewed contract.

An existing synthetic source follows the initial notice and debounce. Empty four-sheet workbooks with valid markers may be delivered; this runtime adds no requiredness, fiscal, binding or business admission. Missing or malformed markers are reader rejection, never enrollment or filename/year fallback. Live-file replacement follows WP-06/WP-16 retry and integrity rules. Each follow-up performs a fresh identified acquisition; no prior result is cached. Consumer return values are ignored. Stop or terminal failure suppresses pending follow-up. Identified delivery does not prove registration, active binding, generation freshness, commit eligibility or financial validity.

## Revision-specific predecessor tests

Only `test_id01_public_api_and_import_inertness` and `test_id12_architecture_boundary_preservation` in `tests/test_identified_save_import_driver.py` may change. ID-01 must prove every pre-WP-17 public export remains present with its accepted signature and version, including the exact WP-16 function and version. It may admit the single additive WP-17 export; IW-01 checks that export's exact identity. ID-12 must prove which driver raw-mode execution selects without rejecting the required identified entry or private identified branch. It retains raw-mode behavioral evidence and whole-module persistence prohibitions. Neither node may be waived, skipped, xfailed, deleted or broadly rewritten. Full predecessor regression remains required.

## Cost, rollback and consequences

The runtime retains one coordinator token and one identified result at a time. Identified mode adds O(1) runtime metadata and no second workbook, Raw or event buffer. The accepted 15,000-row, 15-second and 128-MiB acquisition/identity gate remains unchanged; native notification latency receives no new arbitrary threshold.

Before operational use, rollback reverts only the additive constant, method, export, README section, focused tests and narrow predecessor-test correction through explicit reviewed commits in an isolated checkout. It preserves `run`, does not edit a workbook, broadly delete snapshot artifacts or touch SQLite history. Rehearsal uses generated disposable fixtures only.

Handoff requires a controller-bound successful implementation-stage receipt identifying the tested commit and exact command evidence. Durable consumer ownership, store composition, restart recovery, enrollment/rollover, real Excel retention, discrepancy reporting and end-to-end G1 evidence remain separate work. No test is claimed as run during planning. G1 remains OPEN / IN PROGRESS.
