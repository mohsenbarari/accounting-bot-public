# WP-08: Managed source watcher and serial read runtime

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 native notification and runtime boundary; cannot close G1
- Status: Accepted and merged
- Issued by: Codex Project Manager
- Issued on: 2026-09-02
- Accepted by: Codex Project Manager
- Accepted on: 2026-09-02
- Review evidence: PR #28 and final CI Run `33666088469`; [Codex acceptance](../../../handoffs/phase-01/wp-08-source-watch-runtime/codex-review.md)
- Reviewed head: `2f51f2ef18a09f3c931dde46de5c3a1dbb12a4f3`
- Merge commit: `0d1df855c39ab7ac5b8d5492915fb1014dc096cf`
- Required skill: `accounting-bot-implementer`
- Branch: `antigravity/phase-01-source-watch-runtime`
- Planning baseline: `0e9bb74b79c8bd9517a1fc93fef1684f36a81b4a`
- Execution baseline: latest clean `origin/main` containing this WP and ADR-0011;
  record its exact SHA before implementation, not the planning baseline above
- Handoff path: `handoffs/phase-01/wp-08-source-watch-runtime/`

## Objective and traceability

Connect the existing watchdog native observer to WP-07's coordinator and serial
WP-06/WP-05 Reader driver. Deliver successful results on one caller thread while
callbacks remain responsive, and own observer startup, stop, failure and cleanup.

- Roadmap 5.1, 19.1 and Phase 1: exact Save path, quiet period and follow-up behavior.
- O-73 / [ADR-0011](../../adr/ADR-0011-source-watch-runtime.md): normative contract;
  read the entire ADR before implementation.
- O-53 / ADR-0002: the selected Windows interactive Python/watchdog stack.
- O-72 / [WP-07](WP-07-save-import-coordinator.md): authoritative scheduling,
  attempt ownership and driver error policy; preserve it.
- O-70/O-71 / WP-05/WP-06: complete Reader results and managed snapshot cleanup.
- O-46 through O-51: bounded implementation, safe public artifacts and Codex review.

This package authorizes tests that start real OS observers only in generated
synthetic temporary directories. It does not authorize observation of the real
accounting folder or any real-data copy, Excel/OneDrive/COM use, UUID writes,
SQLite/Outbox/financial commits, CLI/service installation, network or production.

## Required public API

Implement `accounting_local_agent.source_watch_runtime` and export:

- `SOURCE_WATCH_RUNTIME_VERSION = "source-watch-runtime.v1"`.
- `SourceWatchRuntimeState`: `new`, `running`, `stopping`, `stopped`, `failed`.
- `SourceWatchRuntimeReason`: `invalid_policy`, `invalid_transition`,
  `observer_start_failed`, `event_delivery_failed`, `observer_stopped_unexpectedly`,
  `source_read_failed`, `consumer_failed`, `shutdown_failed`.
- `SourceWatchRuntimeError`: typed `reason`, fixed safe default message, no required
  caller-supplied diagnostic string. Preserve original causes as specified in ADR.
- `SourceWatchRuntimeView`: frozen, path-free `version`, `state`, `stop_requested`.
  Validate direct construction: exact version and enum; exact Boolean stop flag;
  `new`/`running` require false, `stopping`/`stopped` require true, `failed` permits
  either. Invalid construction uses `invalid_policy`; no mutable backing state leaks.
- `SourceWatchRuntime(source_path: Path, *, snapshot_root: Path, observation_interval_seconds: float)`.
- `run(consumer: Callable[[XlsxSourceReadResult], None]) -> None` on that instance.
- `request_stop() -> None` and `view() -> SourceWatchRuntimeView` on that instance.

The constructor performs lexical validation only and owns a private fresh coordinator.
Validate a noncallable consumer before admitting `run`, with no state/resource change.
Use fixed safe representations for runtime/error/view objects; source paths and
Reader contents are not diagnostics. Private observer/clock/wait/driver test seams
are permitted; they are not public dependency-injection or debounce-bypass APIs.

Use the existing WP-07/WP-06 numeric policy for the observation interval, including
rejection of Boolean, zero, negative and nonfinite inputs. Reject `snapshot_root`
inside/equal to the source parent, respecting path components and native case rules.
No source file existence check is needed before startup; the observer must report an
inaccessible/missing parent as startup failure.

## Implementation requirements

### WR-A: Adapter and startup

Follow ADR-0011 exactly: one nonrecursive watch of the source parent, native Observer,
callback admission before startup, one post-start initial coordinator notice, and
complete preservation of both moved endpoints. Known read-only/directory/unknown
events are ignored. Decode native path representations without filesystem lookup.
Recognized malformed file events fail visibly through the run thread.

Do not perform work twice through `on_any_event` plus per-kind dispatch. Do not
retarget a renamed source, read temporary/conflict files, watch all descendants,
or assume a filesystem event makes a file safe to parse. Capture owned worker
references before operations that can partially start/remove them.

### WR-B: Waiting and serial reads

Use coordinator deadlines, a condition/event protocol and a maximum idle wait of
one second for backend liveness. There is no hot loop, second debounce, direct call
to WP-05 or bypass of WP-06. Retry and Reader rejection use the coordinator's existing
state; only direct typed errors qualify. All other driver failures end the run.

Keep synchronization out of Reader I/O and consumer code. Use an explicit cycle
admission point to resolve stop-versus-next-attempt races. Deliver each successful
non-`None` result once after lease cleanup, on the run caller's thread. A graceful
stop during an admitted cycle still allows that result's delivery; an asynchronous
fault suppresses delivery if it wins consumer admission. No new cycle runs while a
consumer is blocked, and new notices still coalesce.

### WR-C: Lifecycle and errors

Implement the single-use lifecycle, nonblocking/idempotent stop request, partial-
startup cleanup, liveness checks and error preservation in ADR-0011. `request_stop`
from the consumer cannot self-join. Duplicate/concurrent run attempts cannot allocate
a second observer. No normal return while owned workers remain alive.

Retain the first asynchronous failure and independently observed run/teardown
failures with original identity/cause preserved. Use safe grouped messages and
BaseExceptionGroup when needed. Do not append raw path/content markers to our
default errors or log caught callback exceptions. Test the cleanup path itself;
do not imply that arbitrary native hangs can be forcibly recovered.

### WR-D: Synthetic native integration

Use `pytest` temporary directories outside watched snapshot storage. Generate XLSX
fixtures at runtime using the established four-sheet helpers. Start the real native
Observer for the current platform and synchronize on readiness/result events.
Every test has a finite failure deadline and joins test/run/backend threads in a
`finally` path. A failed test must not leave a watcher running after the suite.

OS notification counts and ordering differ. Assert eventual correct content and
stable cleanup, not an exact native callback sequence or elapsed time equal to two
seconds. Exact coalescing/count/deadline assertions belong to deterministic tests.
Neither Windows nor Linux may skip these native integration tests merely because
events did not arrive; timeout is a failure. Existing unrelated platform-specific
skips remain as in the accepted suite.

## Acceptance matrix

Each row must cite exact test names and captured evidence, including negative cases.

| ID | Required evidence |
|---|---|
| WR-01 | Version, exports, immutable view and safe representations; invalid configuration/consumer/transition causes no state change or I/O. Path type, unresolved parent, `.xlsx`/lock-name, native case and snapshot-root containment boundaries. |
| WR-02 | Captured backend factory proves exactly one Observer, exact source parent and `recursive=False`; constructor and stop-before-run allocate nothing; source may be absent. |
| WR-03 | Table-driven events: created/modified/deleted, both move directions, unrelated/temp/conflict paths, directories, all read-only and unknown kinds; bytes/str decoding and one delivery per dispatch. No handler filesystem I/O. |
| WR-04 | Missing/malformed mutating paths, invalid fields and coordinator callback exceptions are surfaced; first callback failure wakes the run and later callbacks cannot mutate scheduling. |
| WR-05 | Initial hint occurs after successful start, never on failed/pre-stopped start; real startup notices coalesce, and a preexisting synthetic file is read without another event. |
| WR-06 | Fake monotonic clock and controlled waiter prove deadline minus 1 ns, exact deadline, latest-event debounce, spurious wake handling and idle liveness wait; no spin, timer-per-event or no-due file I/O. |
| WR-07 | Barrier tests place notice, stop and fault between predicate inspection and waiting; no lost wake. Stop versus cycle/consumer admission has a recorded winner and the loser does not mutate/launch work. |
| WR-08 | At least 2,000 deterministic notices coalesce with bounded runtime-owned state; notices remain responsive while acquisition, Reader, cleanup or consumer blocks. No second cycle during consumer execution. |
| WR-09 | Unchanged WP-07 driver is used; direct NotReady retries without a new event at its existing deadline; direct Reader rejection waits for fresh input/preserves an existing follow-up. No callback or result on failure. |
| WR-10 | Successful result identity delivered once on the run thread after lease exit/cleanup; stop during an admitted read drains that delivery; asynchronous fault suppresses an unadmitted consumer. |
| WR-11 | Concurrent run calls allocate only one backend; stop-before-run, stop during startup, idle, read and consumer; repeated stop and consumer calling stop; all terminal run calls rejected. |
| WR-12 | Factory/schedule/start failures, including one worker started before failure, receive complete teardown. Missing parent failure is safe and never creates the watched parent. |
| WR-13 | Dispatcher death and emitter death each fail visibly at a loop boundary; planned shutdown is not misclassified. Record blocked-I/O liveness limitation. |
| WR-14 | Driver/grouped error, consumer failure, callback failure, cancellation and stop/join failures; simultaneous failures retain independent identities/causes, safe messages and failed terminal state. No cleanup error masks the primary error. |
| WR-15 | Native Linux/Windows: startup read of a preexisting workbook, then in-place Save and atomic replacement at the same target with distinct content generations; eventual results match the current synthetic content and snapshots are cleaned. |
| WR-16 | Native Linux/Windows: source absent at startup then created; delete/recreate and move away/into target; unrelated/lock files cannot become the acquired source. Stop leaves no owned observer/emitter/run thread alive. |
| WR-17 | End-to-end successful results fed to the independent WP-04 planner oracle: row sort and formula/cache-only modification produce zero planned changes; raw edits produce only expected changes. No database/financial-event claim. |
| WR-18 | Full existing suite retained (314 collected tests before additions), Ruff, native and win32 mypy, handoff validator, public data/secret scan, clean diff and Windows/Linux CI. Existing WP-05/WP-06 15,000-row benchmarks remain under 15 seconds and 128 MiB with their original measurement scope. |

Do not weaken assertions, replace barriers with short sleeps, swallow thread
exceptions, mark timing failures as skips, or raise existing benchmark limits.
Capture test-thread failures and propagate them to pytest; a background traceback
with a passing test is not evidence. Deterministic concurrency tests must inspect
the admission/loser outcome while the winning operation is still held at a barrier.

## Allowed changes

- New `apps/local_agent/src/accounting_local_agent/source_watch_runtime.py`.
- Its public exports in `apps/local_agent/src/accounting_local_agent/__init__.py`.
- Runtime lifecycle/limitations and a synthetic-only library usage example in
  `apps/local_agent/README.md`; no operational real-path example or launcher.
- New `tests/test_source_watch_runtime.py` and
  `tests/test_source_watch_runtime_native.py`; focused synthetic helpers under
  `tests/` only when needed, preserving existing fixture behavior.
- Completed files under the named handoff directory.

No edits to ROADMAP, accepted ADRs/WPs, WP-05/WP-06/WP-07 code/tests, Domain/contracts,
dependency manifests/lockfile, CI policy or other packages. If a genuine integration
issue requires an existing API/contract change, record the smallest reproduction and
stop for Codex's scope decision instead of silently widening the package.

## Validation and handoff

Start from a clean main containing the issued documents. Record exact baseline,
branch and linear commits. Run from the repository root and capture output/exit code:

```text
uv sync --frozen --all-packages --all-groups
uv run pytest tests/test_source_watch_runtime.py -v
uv run pytest tests/test_source_watch_runtime_native.py -v
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run mypy --platform win32 .
uv run pytest -v
uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s
uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s
git diff --check origin/main...HEAD
python .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-08-source-watch-runtime
```

Record benchmark commands/results using the unchanged existing harnesses. Handoff
must include `handoff.md`, `acceptance-matrix.md`, `test-results.txt`, named tests for
WR-01..WR-18, native backend/platform and thread-cleanup evidence, retained risks,
rollback, and protected-assets/secret scan. Local Linux evidence and win32 mypy are
not substitutes for native Windows runtime/CI evidence; report unavailable evidence
honestly so Codex can complete the independent review.

After validation, report the exact final HEAD and stop for Codex review. Do not
push, open/merge a PR, approve acceptance, close G1, alter the Roadmap, start another
WP, install a service or observe real folders. Implementation completion is not
independent acceptance.
