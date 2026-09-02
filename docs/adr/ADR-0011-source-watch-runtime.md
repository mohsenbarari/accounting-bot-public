# ADR-0011: Managed source watcher and serial read runtime

- Status: Accepted
- Date: 2026-09-02
- Work Package: Phase 1 — WP-08
- Decision owner: Codex Project Manager under O-46/O-49

## Context

WP-07 accepts normalized notices and performs one due source-read attempt through
WP-06 and WP-05. It does not receive filesystem events or run a waiting loop. The
next bounded step toward Roadmap sections 5.1 and 19.1 is to connect those accepted
components to the already selected watchdog dependency, with explicit startup,
delivery, shutdown and failure behavior.

This ADR resolves O-73. It authorizes a library runtime tested exclusively against
generated synthetic files. A delivered Reader result is still not an import,
financial event, durable baseline or ACK. Financial validation, fiscal/archive
selection and ownership through a SQLite commit require later contracts.

## Constraints and existing evidence

- Preserve ADR-0010's exact path, two-second monotonic debounce, retry and one-active-
  attempt rules. WP-05/WP-06 retain their current validation and lease boundary.
- `watchdog==6.0.0` is already resolved in `uv.lock`; do not upgrade it or add a
  dependency. ADR-0002 selected watchdog for the Windows interactive agent.
- The pinned [observer implementation](https://github.com/gorakhargosh/watchdog/blob/v6.0.0/src/watchdog/observers/api.py)
  supports nonrecursive scheduling, a dispatcher and emitters. Its event queue has
  no configured size bound by default, and stopping can join emitters internally.
  Therefore neither a fixed whole-process queue bound nor a hard shutdown deadline
  may be inferred from the bounded coordinator.
- The pinned [event definitions](https://github.com/gorakhargosh/watchdog/blob/v6.0.0/src/watchdog/events.py)
  include source/destination paths and open/close events. Event delivery is a hint
  to acquire a stable snapshot, not proof that Excel completed a Save.
- Only generated paths under test-owned temporary directories may be observed in
  this package. No real workbook, real-data copy, OneDrive folder, COM connection,
  UUID write, production installation, network operation or persistent database.

## Options considered

### Option A — One owned observer and one blocking read loop

- Benefits: event callbacks stay short; accepted scheduling remains authoritative;
  one caller thread owns Reader delivery and resource teardown; both the adapter
  and real filesystem integration can be tested independently.
- Costs/risks: introduces lifecycle and cross-thread failure handling. Native event
  loss and process termination remain outside a durable recovery guarantee.
- Reversibility: high; a library adapter with no schema or installed service.

### Option B — Read directly in watchdog handlers or spawn a thread per event

- Benefits: initially less loop code.
- Costs/risks: blocks dispatch or creates competing attempts, uncontrolled worker
  counts and difficult shutdown. Duplicates or bypasses WP-07 ownership policy.
- Reversibility: moderate.

### Option C — Add the watcher, transactional importer and installed agent together

- Benefits: a larger operational slice in one delivery.
- Costs/risks: conflates platform evidence with financial requiredness, fiscal-year
  selection, durable commit and protected deployment decisions.
- Reversibility: lower because of schema and installation state.

## Accepted decision

Choose Option A. Implement `source-watch-runtime.v1` in `accounting-local-agent`.
Construction is inert. `run(consumer)` blocks on its caller thread; the only
background threads are those owned by the native watchdog observer. The runtime
owns a fresh private WP-07 coordinator and never shares or exposes its attempt
tokens. The consumer receives completed immutable WP-05 results serially, after
the WP-06 context has exited successfully.

### Configuration and public boundary

- Configure one absolute `Path` ending in `.xlsx`, one absolute `snapshot_root`
  `Path`, and the existing positive finite `observation_interval_seconds` policy.
  Use WP-07's host-native lexical source rules and WP-06's acquisition policy;
  reject relative/unresolved-parent paths and wrong types without filesystem I/O.
- Require the snapshot root to be lexically outside the watched source parent and
  its descendants. Reject equality and containment using path components and native
  case rules, not string prefixes. This separates generated snapshot traffic from
  the watched directory; it is not a symlink or realpath security boundary.
- Do not resolve, create, enumerate, read or watch directories in the constructor.
  A source file may be missing. Its parent must already be an accessible directory
  when `run` starts; do not create or retarget that parent. WP-06 remains responsible
  for its snapshot storage operations.
- Public API and immutable, path-free lifecycle view are specified in WP-08. Errors
  have typed stable reasons and fixed default text. Configuration or transition
  errors leave state unchanged. No global singleton, public clock override,
  arbitrary observer injection or configuration/CLI loader is added.

### Start and event adaptation

1. Atomically admit at most one valid `run` invocation. Allocate one native
   `watchdog.observers.Observer`, schedule exactly the configured parent with
   `recursive=False`, and start it. Never watch the source file inode itself or
   replace the backend with polling for normal operation.
2. Preserve references to owned workers needed for teardown even if startup fails
   partway through. Event admission is installed before start so notices arriving
   during startup can be recorded. Callback failure during startup fails the run.
3. Once the observer has started and is healthy, enqueue one *logical* initial
   `modified` notice through the coordinator. It goes through the same two-second
   debounce and coalesces with actual notices; it is not a direct read or timestamp
   bypass. This allows an already present file to be read without a new Save.
4. Map `created`, `modified`, `deleted` and `moved` events to their WP-07 equivalents.
   For a move, preserve both endpoints; never change the configured source path.
   Decode `str`/`bytes` paths through the host filesystem representation, then use
   `Path`. Do not call `resolve`, `stat`, open or enumerate from a handler.
5. Ignore directory events and known read-only events (`opened`, `closed`,
   `closed_no_write`, `accessed`), as well as unknown event kinds. Valid mutating
   events for other paths are passed through the same exact-path filter and do not
   wake/postpone work. Temporary/lock/conflict paths do not match; a temporary path
   moved *to* the exact target does match.
6. A malformed recognized mutating file event, or an unexpected adapter/coordinator
   failure, is a visible runtime fault. Catch it at the callback boundary, retain
   the first asynchronous failure, close further event admission and wake the run
   thread. Never let it silently kill dispatch or print raw data from our handler.

Callbacks only adapt, notify and signal. They do no acquisition, parsing, consumer
delivery, join, sleep or durable write. The adapter must not duplicate callbacks
through both `on_any_event` and a per-kind handler. Keep only bounded runtime-owned
scheduling/fault state; do not add a list of notices, Reader results or exceptions.
The upstream native event queue is explicitly outside that bound.

### Waiting, attempts and delivery

- Use a condition/event protocol that rechecks predicates after every wake and
  cannot lose a notice/stop/fault arriving between inspecting state and waiting.
  Use the same monotonic time basis as WP-07 and derive waits from its current
  deadline. Never add another debounce or one `Timer` per notice.
- When idle, wait rather than spin or inspect the workbook. Cap each wait at one
  second to check dispatcher and owned emitter liveness using the library's
  interfaces. A missing/dead expected worker is fatal while actively watching;
  a requested teardown is not a liveness failure. Tests use private seams, not
  wall-clock sleeps, for deadline and race assertions.
- Admit each serial work cycle under the lifecycle synchronization, then release
  that lock before calling the unchanged `read_due_source`. An admitted cycle may
  finish after a concurrent stop request. A stop/fault that wins admission prevents
  the next cycle. No lifecycle/coordinator lock spans Reader I/O or consumer code.
- Direct `XlsxSourceNotReadyError` uses the retry already scheduled by WP-07;
  return to the waiting loop without manufacturing another notice or sleeping an
  extra debounce interval. A direct `XlsxSourceReadError` likewise returns to the
  loop: no delivery, and no retry until the coordinator's existing follow-up or a
  fresh relevant event. A grouped/wrapped error is not either direct case.
- All other driver failures terminate the run. Never call `resume_after_fault`
  automatically, suppress a driver error or repair ownership outside WP-07.
- A non-`None` successful result is delivered once, synchronously on the run thread,
  after lease cleanup. It is not deduplicated by file hash. No second cycle starts
  while the consumer runs; fresh notices still coalesce in the private coordinator.
- Graceful stop drains an already admitted cycle, including its successful consumer
  delivery. A previously recorded asynchronous fault suppresses a delivery that has
  not yet been admitted; a consumer already admitted may finish. Consumer admission
  and asynchronous fault recording use the same lifecycle synchronization.
- A consumer exception is fatal, including cancellation/BaseException. No replay,
  automatic restart, unbounded retention or background delivery is performed.
  The consumer is an inspection seam, not an authorized transactional-import hook.

No process-wide exclusivity is promised. One runtime instance has one observer and
serial delivery; callers must not construct multiple instances for the same source.
Liveness detection is bounded by the next loop iteration, so a blocked Reader or
consumer can delay it. This is not a watchdog for arbitrary hung user code.

### Stop, failure and teardown

- Lifecycle is `new -> running -> stopping -> stopped` for a normal run, with
  `failed` as the terminal outcome of an execution/teardown fault. `running` includes
  startup. A request before `run` moves `new -> stopped` without allocating resources.
  Subsequent `run` calls, including after failure, are invalid; make a fresh instance
  to perform another start and initial reconciliation hint.
- `request_stop()` is thread-safe, idempotent and nonblocking with respect to I/O,
  consumer execution and joins. It records the stop intent, closes event admission
  and wakes the loop. It is safe from inside the consumer. After it wins cycle
  admission, pending/follow-up work is not drained. It neither deletes source files
  nor claims to persist pending intent.
- Teardown runs on the run thread in `finally`, outside lifecycle locks: stop event
  admission, stop the observer, and join every started owned worker. Attempt the
  remaining cleanup operations even when one fails. Handle never-started workers
  explicitly, without masking other startup/join errors as benign.
- The run cannot return normal success while an owned worker remains alive. Do not
  kill a thread, use a daemon thread as a substitute for joining, or claim a hard
  timeout for native shutdown. Normal shutdown may wait for admitted I/O/consumer
  work and the backend; this limitation must be visible in the README/handoff.
- Preserve independently observed driver/consumer failure, the first asynchronous
  callback/liveness failure and teardown failures. Wrap ordinary `Exception`
  failures with fixed safe reason text and retain original causes. Preserve a
  non-`Exception` BaseException (including a mixed BaseExceptionGroup) unchanged;
  re-raise it after teardown when it is the only failure. With multiple failures,
  group in order: run failure, first asynchronous failure, teardown failures in
  operation order. Use BaseExceptionGroup if any retained member is not an Exception,
  otherwise ExceptionGroup. Do not mutate an original exception's cause to attach
  another error or turn cancellation into a routine retry. Test identity preservation.
  Safe default text applies to our own wrappers/groups; original causes are retained
  for inspection and must not be automatically logged or rendered to the user.
- A normal requested stop ends in `stopped`; any observed fault ends in `failed`
  even if stop was also requested. Terminal state is published only after teardown
  has completed or its failures have been collected. Invalid API calls cannot change
  a running or terminal instance, launch a second observer or steal another run.

### Guarantees deliberately left for later packages

The initial notice requests a fresh read on every new instance. It does not provide
journaling, event-overflow detection, periodic reconciliation, restart recovery of
uncommitted work, stale-lease cleanup or durable import idempotency. Successful
synthetic native event tests do not prove Ctrl+S, Office replacement, network shares,
OneDrive conflicts or every event under OS queue pressure. Parent deletion/worker
death fails visibly; automatic resubscription is deferred. No new edit/delete
confirmation, volume quarantine or business restriction is introduced.

## Acceptance impact and evidence

WP-08 specifies WR-01 through WR-18: deterministic adapter/time/lifecycle/race tests,
partial-start and teardown fault injection, callback responsiveness, and bounded
native-observer integration tests on both Linux and Windows using only synthetic
four-sheet XLSX files. The existing 314 collected tests and the WP-05/WP-06 15-second,
128-MiB benchmarks remain mandatory; new native tests may have explicit wall-clock
deadlines but must not weaken those established benchmark gates.

The contract decision is accepted; implementation evidence remains pending. G1 stays
OPEN / IN PROGRESS. Real Excel/OneDrive and protected assets require separately named
Owner authority. Observer evidence alone cannot establish repeatable committed
imports or the discrepancy report required to close G1.

## Rollback and reconsideration

Disable/remove the library runtime through a reviewed change; no migration, service
registration or workbook rollback is needed. Keep WP-05/WP-06/WP-07 APIs unchanged.
Revisit this ADR before durable commit integration, automatic restart/resubscription,
periodic reconciliation, a polling fallback, bounded backend queue replacement,
multiple sources or installation in the real Windows interactive session.
