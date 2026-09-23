# WP-17: Deliver identified source results through the watch runtime

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 marker-aware runtime-delivery prerequisite; cannot close G1
- Status: Issued and accepted by Codex PM after independent plan review; implementation evidence pending
- Issued on: 2026-09-23
- Required workflow: `accounting-bot-implementer`; non-author implementation review required
- Implementation branch: `antigravity/phase-01-identified-source-watch-runtime`
- Planning source head: `5f1d45ef0f40615b32b5a886248e0bba09724e53`
- Execution baseline: latest clean `origin/main` containing ADR-0025 and this WP; record its actual SHA before coding
- Handoff path: `handoffs/phase-01/wp-17-identified-source-watch-runtime/`
- Gate state: G1 remains OPEN / IN PROGRESS

## Objective and traceability

Implement the additive `identified-source-watch-runtime.v1` API defined by [ADR-0025](../../adr/ADR-0025-identified-source-watch-runtime.md). The existing watch runtime must be able to schedule the completed WP-16 driver and deliver the exact same-lease marker/hash/Raw result without changing its accepted raw-result entry point.

The atomic SQLite store remains a library core. This package does not connect it to the local agent: coordinator success occurs before the runtime consumer is called, so durable consumer retry and ambiguous commit recovery still need a separate reviewed composition contract. This package establishes only the marker-aware runtime prerequisite.

The sequencing is an ordinary Phase-1 technical decision under existing Owner authority. Implementation, independent review, PR and green native-CI merge require no renewed ordinary Owner approval.

All workbooks, paths, identities and filesystem events used for evidence must be unmistakably synthetic and temporary. Never inspect or modify a real/reference workbook or copy, OneDrive data, a real/sample database, credentials, production state or another protected asset.

## Public API

Add to `accounting_local_agent.source_watch_runtime` and export from `accounting_local_agent` exactly:

```python
IDENTIFIED_SOURCE_WATCH_RUNTIME_VERSION = "identified-source-watch-runtime.v1"
```

Add to the existing class exactly:

```python
def run_identified(
    self,
    consumer: Callable[[IdentifiedXlsxSource], None],
) -> None: ...
```

`run_identified` accepts no additional positional or keyword parameters. It exposes no source path, source key, snapshot, hash, coordinator, due-read callback, connection, request, import identity, clock, random source, retry callback, approval flag or public fault hook.

The following remain unchanged:

- `SOURCE_WATCH_RUNTIME_VERSION`
- `SourceWatchRuntime.__init__`
- `SourceWatchRuntime.run(Callable[[XlsxSourceReadResult], None])`
- `request_stop`, `view` and path/configuration properties
- all state, view, reason and error types
- `read_due_source` and `read_due_identified_source`

No new public exception is permitted. Invalid consumer handling must be identical to the accepted raw entry point and occur before observer creation/start, event admission, attempt reservation or driver invocation.

## Required implementation behavior

1. Both entry points share one single-use runtime lifecycle. After either starts, the other cannot start on the same object.
2. Raw mode calls only `read_due_source`. Identified mode calls only `read_due_identified_source`.
3. Identified driver arguments are exactly the runtime-owned coordinator, `snapshot_root` and `observation_interval_seconds`.
4. `None` causes no consumer call. A result is delivered once, synchronously and by object identity.
5. Initial notice, exact-path native event mapping, debounce, coalescing, waiting, liveness, stop and teardown remain common and behaviorally identical between modes.
6. Callback threads perform no XLSX I/O and never call either consumer.
7. No lifecycle or coordinator lock is held across the due-read call, consumer call, snapshot I/O or backend join.
8. Direct not-ready errors remain nonterminal and use the coordinator-installed fixed retry.
9. Direct Raw-reader and marker-identity rejection remain nonterminal and wait for a matching notice or admitted follow-up.
10. Policy/storage/integrity/cleanup errors, unexpected exceptions and all exception groups remain fatal; nested member types do not downgrade groups.
11. Consumer failure is fatal, is reported through the accepted runtime error boundary and does not requeue or redeliver the already completed attempt.
12. A follow-up admitted during read or consumer execution is considered only after the synchronous consumer returns and is suppressed by an admitted stop or terminal failure.
13. Primary and teardown failures retain accepted order and group kind. Distinct exceptions with a shared cause remain distinct.
14. Direct non-`Exception` cancellation and exit objects retain the accepted raw-runtime teardown and propagation behavior.
15. Identified mode opens no database, constructs no persistence DTO and imports no `accounting_persistence` module.
16. Importing the changed modules starts no filesystem, database, network, clock, random, observer or thread action.

A private common-loop refactor is permitted only inside `source_watch_runtime.py`. It cannot expose a new hook, weaken an existing assertion or change externally observable raw-mode behavior.

## Acceptance matrix

Expected calls, states, deadlines, delivered object identities and error ordering must come from literal fixtures or an independent model, never from the helper under test. Give every case a distinct Pytest node ID.

| ID | Nonoverlapping required evidence |
|---|---|
| IW-01 | Assert the exact new constant value, export and method signature. Snapshot every prior public export/signature/version constant unchanged, and prove fresh imports are side-effect free with an injected control that would fail on I/O, time, random or thread activity. |
| IW-02 | Cover entry admission only: invalid consumers fail before observer construction or state mutation; sequential and concurrent attempts to call either entry point on one runtime admit exactly one execution and use the accepted invalid-state error for every loser. |
| IW-03 | Cover mode selection only: a scripted due point makes raw mode invoke only `read_due_source` and identified mode invoke only `read_due_identified_source`, with the exact runtime-owned arguments. Identified mode delivers the identical result once; `None` is never delivered. |
| IW-04 | Cover startup only: identified mode uses the existing non-recursive watch target, starts its backend before admitting the initial logical notice, passes that notice through the full debounce and performs no eager read. Partial-start failures leave no owned worker alive. |
| IW-05 | Cover post-start event scheduling only: create/modify/delete and both move endpoints follow accepted exact-path rules; unrelated paths do nothing; bursts coalesce and notices admitted during one blocked read or consumer create at most one follow-up. |
| IW-06 | Cover expected read outcomes only: direct not-ready performs no delivery and retries at the exact accepted deadline without a notice; direct Raw rejection and marker rejection separately perform no delivery and wait for a fresh matching notice while preserving an admitted follow-up. Runtime state remains running. |
| IW-07 | Cover fatal driver outcomes only: policy, storage, integrity, cleanup, coordinator, unexpected, `ExceptionGroup` and `BaseExceptionGroup` failures enter `FAILED`, admit no later read and preserve primary/teardown ordering, group kind, distinct shared-cause members and direct cancellation semantics. |
| IW-08 | Cover consumer semantics only: a successful consumer is synchronous and serial; its return value is ignored. Ordinary, grouped and cancellation failures use the accepted consumer-failure/teardown boundary, cause no redelivery and suppress pending follow-up execution. |
| IW-09 | Cover stop boundaries only: stop while waiting performs no read; stop during an admitted identified read permits its completion and delivery; stop during consumer permits that call to finish; both latter cases suppress subsequent follow-up and join all owned workers. |
| IW-10 | Exercise execution/callback races with Barriers, Events and acknowledgements: concurrent run entries, stop versus wake, callback versus teardown and liveness failure have one owner, no callback I/O, no lost terminal failure and no live worker in `finally`. No arbitrary sleep is evidence. |
| IW-11 | On each native CI platform, use temporary synthetic paths and the real observer backend to prove initial-file and create/save/replace/move/delete notice delivery into identified-mode scheduling. Use bounded event acknowledgements and report platform skips honestly. The driver may be isolated here so this criterion tests native watching, not parsing. |
| IW-12 | Run a generated four-sheet XLSX with a valid synthetic marker through the real WP-16 acquisition/identity stack from identified runtime mode. Assert exact key, file digest, byte count, Raw values/types/hashes, object identity, synchronous consumer thread and complete lease cleanup. |
| IW-13 | Replace or mutate the generated live workbook at controlled acquisition and follow-up boundaries. Every delivered result must contain marker, digest and Raw from one accepted lease generation, successive results must be fresh objects, and failed generations must never reach the consumer. |
| IW-14 | Prove architectural preservation: trap every `accounting_persistence` import/call, database open and persistence DTO construction; verify none occurs. Run the accepted raw entry point through success, expected errors, consumer failure and stop, comparing states/calls/errors to an independent pre-change oracle. |
| IW-15 | Run at least 40 generated runtime histories against an independent state model, varying entry mode, due/not-due, notices, not-ready, rejection, fatal failure, consumer failure, follow-up and stop. Targeted mutations must detect wrong-driver selection, early delivery, duplicate delivery, lost follow-up and raw-mode drift. |
| IW-16 | Process at least 15,000 generated identified rows through identified runtime mode and the accepted WP-16 stack with the deliberate debounce advanced by a fake monotonic clock. Validate every identity and record the driver call-window time and peak RSS while preserving the 15-second/128-MiB acquisition/identity gate and proving no second workbook buffer. Preserve all existing tests and automatic quality gates. |

Native notification latency is not converted into a new arbitrary performance threshold. Bounded diagnostic timeouts may prevent hangs, but elapsed sleep or polling alone cannot prove ordering or concurrency.

## Exact implementation files

The implementation stage may change exactly:

- `apps/local_agent/src/accounting_local_agent/source_watch_runtime.py`
- `apps/local_agent/src/accounting_local_agent/__init__.py`
- `apps/local_agent/README.md`
- `tests/test_identified_source_watch_runtime.py`

The handoff stage changes exactly:

- `handoffs/phase-01/wp-17-identified-source-watch-runtime/handoff.md`
- `handoffs/phase-01/wp-17-identified-source-watch-runtime/acceptance-matrix.md`
- `handoffs/phase-01/wp-17-identified-source-watch-runtime/test-results.txt`

No implementer edit to Roadmap, ADR/WP text, existing tests/helpers, coordinator, acquisition, reader, identity, contracts, persistence, dependency manifests, lockfile, CI configuration or benchmark limits is permitted.

## Out of scope

- Importing or calling WP-15, opening/initializing SQLite, constructing `SourceImportRequest`, allocating an import ID or committing source state.
- Durable callback acknowledgment, retry after consumer/process failure, pending-attempt persistence or restart recovery.
- A second runtime class, constructor mode, public driver injection, process-wide single-instance lock or background result queue.
- Marker or UUID writing, enrollment, active-source registration, archive activation, rollover or opening balances.
- Real workbook/database access, Excel COM, OneDrive or native real-source experiments.
- Financial rules, RS/item/alias resolution, affected-domain calculation, Ledger, discrepancy UI, server Sync/ACK/signatures, Telegram, backup/restore or deployment.
- New dependencies or services, production action, G1 closure or weakening an accepted contract/test.

## Validation, ownership and delivery

No test is claimed as run by this planning document. The controller runs complete automatic gates for both workflow stages, including frozen synchronization and lock verification, focused tests, Ruff format/lint, both Mypy targets, collection, architecture guard, full regression, focused IW-16 and predecessor benchmarks, whitespace checks, public/protected-asset/secret scans and final handoff validation.

Static win32 Mypy is not native Windows evidence. Unavailable native execution remains pending until later native CI. The handoff maps IW-01 through IW-16 to exact nodes and independent evidence, records the execution baseline and exact preceding tested implementation commit without self-reference, supplies source hashes and exact command outputs, and describes platform scope, raw-runtime preservation, error/lifecycle behavior, provenance, the no-persistence boundary and remaining durable-consumer limitation.

Rollback reverts only the additive constant, method, export, README section and focused tests through explicit reviewed commits in an isolated checkout. It does not edit an XLSX source, broadly delete snapshot artifacts, touch a database or alter WP-15 schema/history. Rehearse rollback only with generated disposable fixtures.

Stop for non-author review after handoff. The implementer cannot accept its own evidence, close G1, deploy or begin WP-18. Independent review, PR and green native-CI merge proceed under existing Owner authority without renewed ordinary approval. G1 remains OPEN / IN PROGRESS.
