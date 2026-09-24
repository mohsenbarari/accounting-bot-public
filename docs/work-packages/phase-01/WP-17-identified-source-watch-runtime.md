# WP-17: Deliver identified source results through the watch runtime

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 marker-aware runtime-delivery prerequisite; cannot close G1
- Status: Issued and accepted by Codex PM after independent plan review; revised after CI correction; implementation evidence pending
- Issued on: 2026-09-23; revised on: 2026-09-24
- Required workflow: `accounting-bot-implementer`; non-author implementation review required
- Implementation branch: `antigravity/phase-01-identified-source-watch-runtime`
- Predecessor completion: WP-16 merge head `5f1d45ef0f40615b32b5a886248e0bba09724e53`
- Planning dependency snapshot: `85663125a906d55b9c7efad4f3d2412f5e496004`
- Execution baseline: latest clean `origin/main` containing ADR-0025 and this revised WP; record its actual SHA before coding
- Handoff path: `handoffs/phase-01/wp-17-identified-source-watch-runtime/`
- Gate state: G1 remains OPEN / IN PROGRESS

## Objective and traceability

Implement the additive `identified-source-watch-runtime.v1` API in [ADR-0025](../../adr/ADR-0025-identified-source-watch-runtime.md). The accepted watcher must schedule the completed WP-16 driver and deliver its exact same-lease marker/hash/Raw result while preserving its raw-result entry. WP-15 remains a SQLite library core. Coordinator success occurs before the runtime consumer is called; durable consumer retry and ambiguous commit recovery require a later separately reviewed composition contract.

The CI correction exposed two predecessor-test assumptions: WP-16 ID-01 assumes no later public export, and WP-16 ID-12 counts any identified-driver reference in the runtime module as a raw-mode violation. This revision permits only the narrow corrections below. ID-12 imports three helpers from `tests/test_source_watch_runtime.py`, so that predecessor test module is required implementation context. The new `tests/test_identified_source_watch_runtime.py` is required handoff context for mapping exact IW test nodes. ADR-0025 behavior and every IW-01 through IW-16 criterion remain required. Implementation and review evidence are pending.

This work is ordinary Phase-1 work under existing Owner authority. Implementation, independent review, PR and green native-CI merge require no renewed ordinary approval. Evidence uses only unmistakably synthetic temporary workbooks, paths, identities and events. Real/reference workbooks or copies, OneDrive data, real/sample databases, credentials and production state remain protected.

## Public API

Add `IDENTIFIED_SOURCE_WATCH_RUNTIME_VERSION = 'identified-source-watch-runtime.v1'` to `accounting_local_agent.source_watch_runtime` and export it from `accounting_local_agent`. Add exactly `SourceWatchRuntime.run_identified(self, consumer: Callable[[IdentifiedXlsxSource], None]) -> None`.

The method accepts no extra positional or keyword parameter and exposes no alternate source path, key, snapshot, hash, coordinator, due-read callback, connection, request, import identity, clock, random source, retry callback, approval flag or public fault hook. `SOURCE_WATCH_RUNTIME_VERSION`, `SourceWatchRuntime.__init__`, `run(Callable[[XlsxSourceReadResult], None])`, `request_stop`, `view`, path/configuration properties, all state/view/reason/error types, `read_due_source` and `read_due_identified_source` remain unchanged. No new public exception is permitted. Invalid consumer handling is identical to `run` and precedes observer creation/start, event admission, attempt reservation and driver invocation.

## Required implementation behavior

1. Both entries share one single-use runtime lifecycle. After either starts, the other cannot start on the same object.
2. Raw mode calls only `read_due_source`; identified mode calls only `read_due_identified_source`.
3. Identified driver arguments are exactly the runtime-owned coordinator, `snapshot_root` and `observation_interval_seconds`.
4. `None` causes no consumer call. A result is delivered once, synchronously and by object identity.
5. Initial notice, exact-path native event mapping, debounce, coalescing, waiting, liveness, stop and teardown remain common and behaviorally identical between modes.
6. Callback threads perform no XLSX I/O and never call either consumer.
7. No lifecycle or coordinator lock is held across due read, consumer, snapshot I/O or backend join.
8. Direct not-ready errors remain nonterminal and use the coordinator-installed fixed retry.
9. Direct Raw-reader and marker-identity rejection remain nonterminal and wait for a matching notice or admitted follow-up.
10. Policy, storage, integrity, cleanup, coordinator and unexpected failures, plus all exception groups, remain fatal; nested member types do not downgrade groups.
11. Consumer failure is fatal through the accepted runtime error boundary and does not requeue or redeliver the completed attempt.
12. Follow-up admitted during read or consumer execution is considered only after synchronous consumer return and is suppressed by stop or terminal failure.
13. Primary and teardown failures retain accepted order and group kind. Distinct exceptions sharing a cause remain distinct.
14. Direct non-`Exception` cancellation and exit objects retain accepted raw-runtime teardown and propagation behavior.
15. Identified mode opens no database, constructs no persistence DTO and imports no `accounting_persistence` module.
16. Importing changed modules starts no filesystem, database, network, clock, random, observer or thread action.

A private common-loop refactor is allowed only inside `source_watch_runtime.py`; it cannot expose a hook, weaken an assertion or change externally observable raw-mode behavior.

## Narrow predecessor-test correction

Only `test_id01_public_api_and_import_inertness` and `test_id12_architecture_boundary_preservation` in `tests/test_identified_save_import_driver.py` may be edited. ID-01 continues to assert every pre-WP-17 public export, accepted signature and version value, including the exact WP-16 version and function. It may recognize the single additive WP-17 constant; IW-01 independently checks the exact WP-17 API difference. Import inertness controls remain active. ID-12 restricts its raw-driver assertion to raw-mode execution rather than rejecting the identified call in `run_identified` or a private identified branch. It retains behavioral evidence that raw mode uses only the raw driver and whole-module checks against persistence imports, calls, database opening and DTO construction. Keep both node IDs and meaningful assertions. Do not waive, skip, xfail, delete or broadly rewrite them. No other existing test or helper may change.

## Acceptance matrix

Expected calls, states, deadlines, delivered object identities and error ordering come from literal fixtures or an independent model, never the helper under test. Give every case a distinct Pytest node ID.

| ID | Nonoverlapping required evidence |
|---|---|
| IW-01 | Assert the exact new constant value, export and method signature; every prior public export/signature/version remains unchanged. Fresh imports are side-effect free under an injected control that fails on I/O, time, random or thread activity. |
| IW-02 | Invalid consumers fail before observer construction or state mutation. Sequential and concurrent calls to either entry on one runtime admit exactly one execution; losers receive the accepted invalid-state error. |
| IW-03 | At a scripted due point, raw mode invokes only `read_due_source` and identified mode only `read_due_identified_source`, with exact runtime-owned arguments. Identified mode delivers the identical result once; `None` is never delivered. |
| IW-04 | Identified startup uses the existing non-recursive watch target, starts its backend before the initial logical notice, passes the notice through full debounce and performs no eager read. Partial-start failure leaves no owned worker alive. |
| IW-05 | Post-start create/modify/delete and both move endpoints follow accepted exact-path rules; unrelated paths do nothing. Bursts coalesce; notices admitted during blocked read or consumer create at most one follow-up. |
| IW-06 | Direct not-ready delivers nothing and retries at the exact accepted deadline without a notice. Direct Raw rejection and marker rejection separately deliver nothing and await a fresh matching notice while preserving admitted follow-up. Runtime stays running. |
| IW-07 | Policy, storage, integrity, cleanup, coordinator, unexpected, `ExceptionGroup` and `BaseExceptionGroup` failures enter `FAILED`, admit no later read and preserve primary/teardown order, group kind, distinct shared-cause members and direct cancellation semantics. |
| IW-08 | Successful consumer is synchronous and serial; its return is ignored. Ordinary, grouped and cancellation failures use accepted consumer-failure/teardown behavior, cause no redelivery and suppress pending follow-up. |
| IW-09 | Stop while waiting performs no read; stop during admitted identified read permits completion and delivery; stop during consumer permits that call to finish. The latter cases suppress follow-up and join all owned workers. |
| IW-10 | Execution/callback races use Barriers, Events and acknowledgements: concurrent entries, stop versus wake, callback versus teardown and liveness failure have one owner, no callback I/O, no lost terminal failure and no live worker in `finally`. |
| IW-11 | On each native CI platform, temporary synthetic paths and the real observer backend prove initial-file and create/save/replace/move/delete notice delivery into identified scheduling. Use bounded acknowledgements and report platform skips honestly. The driver may be isolated for this watcher criterion. |
| IW-12 | A generated four-sheet XLSX with a valid synthetic marker passes through real WP-16 acquisition/identity from identified runtime mode. Assert exact key, file digest, byte count, Raw values/types/hashes, object identity, synchronous consumer thread and complete lease cleanup. |
| IW-13 | Controlled live-workbook replacement or mutation at acquisition and follow-up boundaries proves every delivered marker, digest and Raw belongs to one accepted lease generation. Successive results are fresh; failed generations never reach the consumer. |
| IW-14 | Trap every `accounting_persistence` import/call, database open and persistence DTO construction. Run accepted raw mode through success, expected errors, consumer failure and stop against an independent pre-change state/call/error oracle. |
| IW-15 | At least 40 generated runtime histories run against an independent state model, varying entry mode, due/not-due, notices, not-ready, rejection, fatal failure, consumer failure, follow-up and stop. Targeted mutations detect wrong-driver selection, early or duplicate delivery, lost follow-up and raw-mode drift. |
| IW-16 | At least 15,000 generated identified rows pass through identified runtime and accepted WP-16 with deliberate debounce advanced by a fake monotonic clock. Validate every identity; record driver call-window time and peak RSS; preserve the 15-second/128-MiB acquisition/identity gate and prove no second workbook buffer. Preserve existing tests and automatic quality gates. |

Native notification latency receives no new arbitrary threshold. Bounded diagnostic timeouts may prevent hangs, but elapsed sleep or polling alone cannot prove ordering or concurrency.

## Exact implementation files

The implementation stage may change exactly `apps/local_agent/src/accounting_local_agent/source_watch_runtime.py`, `apps/local_agent/src/accounting_local_agent/__init__.py`, `apps/local_agent/README.md`, `tests/test_identified_source_watch_runtime.py`, and `tests/test_identified_save_import_driver.py` limited to the two named predecessor nodes. The handoff stage changes exactly `handoffs/phase-01/wp-17-identified-source-watch-runtime/handoff.md`, `handoffs/phase-01/wp-17-identified-source-watch-runtime/acceptance-matrix.md`, and `handoffs/phase-01/wp-17-identified-source-watch-runtime/test-results.txt`.

No implementer edit to Roadmap, ADR/WP text, other existing tests/helpers, coordinator, acquisition, reader, identity, contracts, persistence, dependency manifests, lockfile, CI configuration or benchmark limits is permitted.

## Out of scope

Calling WP-15, opening/initializing SQLite, constructing `SourceImportRequest`, allocating import IDs or committing state; durable callback acknowledgment or retry after consumer/process failure; pending-attempt persistence or restart recovery; another runtime class, constructor mode, public driver injection, process-wide lock or background result queue; marker/UUID writing, enrollment, active-source registration, archive activation, rollover or opening balances; real workbook/database access, Excel COM, OneDrive or native real-source experiments; financial rules, resolution, Ledger, discrepancy UI, server Sync/ACK/signatures, Telegram, backup/restore, deployment, new dependencies/services and G1 closure are outside this WP.

## Validation, receipt, ownership and delivery

No test is claimed as run by this planning document. The controller runs complete automatic quality gates for both stages, including frozen synchronization and lock verification, focused and full WP-16 predecessor tests, the focused IW suite including IW-16, Ruff format/lint, both Mypy targets, collection, architecture guard, full regression, predecessor benchmarks, whitespace checks, public/protected-asset/secret scans and final handoff validation. Native Windows/Linux CI and all four mandatory Windows symlink cases remain required before acceptance. Static win32 Mypy is not native Windows evidence; unavailable native execution stays pending.

The handoff stage has a required controller receipt binding to the actual preceding implementation stage. This receipt is a stage input outside path-based context files and must report success, the tested implementation commit, exact commands, exit codes and results. If absent, unsuccessful or incomplete, handoff stops without inventing or substituting evidence. The handoff maps IW-01 through IW-16 to exact nodes in the implementation-created test file and independent evidence; identifies both corrected predecessor nodes and their results; records the execution baseline and receipt's tested commit without self-reference; supplies source hashes for both normative documents and all five implementation allowed files; and reports platform scope, raw-runtime preservation, error/lifecycle behavior, provenance, no-persistence boundary and durable-consumer limitation.

Rollback reverts only the additive constant, method, export, README section, focused tests and narrow predecessor-test correction through reviewed commits in an isolated checkout. It does not edit an XLSX source, broadly delete snapshot artifacts, touch a database or alter WP-15 schema/history. Rehearse only with generated disposable fixtures.

Stop for non-author review after handoff. The implementer cannot accept its own evidence, close G1, deploy or begin WP-18. Independent review, PR and green native-CI merge proceed under existing Owner authority without renewed ordinary approval. G1 remains OPEN / IN PROGRESS.
