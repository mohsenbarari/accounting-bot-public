# ADR-0010: Deterministic Save notification and source-read coordination

- Status: Accepted
- Date: 2026-09-02
- Work Package: Phase 1 — WP-07
- Decision owner: Codex Project Manager under O-46/O-49

## Context

O-31 and Roadmap section 5.1 require a two-second quiet period after the latest
relevant file event, coalescing of consecutive Saves, and exactly one follow-up when
a Save arrives during an import. WP-06 now supplies stable snapshot leases and WP-05
reads them. Neither component owns event scheduling.

This ADR resolves O-72 for a bounded coordinator and a single-attempt Reader driver.
The unit of work here is a source-read attempt, not a committed financial import.
The future transactional importer must extend ownership through its commit boundary
under a separate reviewed contract; returning a Reader result is never an ACK or a
durable baseline update.

## Constraints

- Preserve the accepted two-second debounce and the unchanged WP-06 stability checks.
- Use generated synthetic files only. Real Excel/OneDrive observation, COM/UUID writes,
  watchdog Observer startup, fiscal selection, SQLite/Outbox, network and production
  operations remain outside this package.
- The coordinator stores bounded volatile scheduling state, never a financial event
  queue, workbook rows, historical exceptions or a persistent import baseline.
- No dependency, lockfile, source/hash contract or WP-05/WP-06 behavior change.

## Options considered

### Option A — Explicit coordinator with atomic attempt ownership

- Benefits: deterministic time and interleaving tests; event callbacks remain quick;
  parsing and cleanup have one completion boundary; the later watcher can adapt its
  notifications without owning debounce policy.
- Costs/risks: introduces explicit states and completion outcomes. Volatile state
  does not recover missed notifications after process termination.
- Reversibility: high; no schema or external service is introduced.

### Option B — Copy and parse in each watcher callback

- Benefits: fewer initial abstractions.
- Costs/risks: blocks event delivery, duplicates work, misses Saves during parsing
  and incorrectly treats an event as proof of a complete Save.
- Reversibility: moderate, with coupled watcher and acquisition lifecycles.

### Option C — Implement watcher, COM and transactional importer together

- Benefits: immediate end-to-end wiring.
- Costs/risks: combines unresolved platform evidence, protected Excel actions and
  persistence decisions; failures cannot be isolated to this scheduling boundary.
- Reversibility: lower because durable state and platform lifecycle are involved.

## Accepted decision

Choose Option A. Implement `save-import-coordinator.v1` in `accounting-local-agent`
using standard-library timing and synchronization. One application-owned coordinator
represents one configured source path. The guarantee is per coordinator instance;
there is no implicit global registry or process-wide file lock.

### Notification boundary

- Accept typed `created`, `modified`, `deleted` and `moved` notices. Known read-only
  notices `opened`, `closed` and `accessed` are ignored, as are directory notices.
- Compare only the configured exact absolute path. Apply host-native lexical path
  normalization/case handling (`normpath`/`normcase`), not `resolve`, `stat`, directory
  enumeration, symlink traversal, fuzzy matching or filesystem identity lookup.
  Reject relative paths and unresolved `..` components. Configuration must be an
  `.xlsx` path and must not name an Excel `~$` lock file; existence is not required.
- Created/modified/deleted notices match their source path. Moved notices match when
  either endpoint is the configured path. A temporary source moved to the target is
  relevant; a target moved away is also relevant, but the Reader still opens only the
  configured path. Missing targets are handled by WP-06, never by following a move.
- Sibling `.tmp`, lock, conflict, archive and snapshot paths are ignored by exact
  matching. Do not infer OneDrive conflict naming rules or search for an alternative
  workbook. Unrelated notices cannot reset a deadline or mark a follow-up pending.
- Notice paths/kinds and `is_directory` are validated before state mutation. Moves
  require a destination; other kinds forbid one. `is_directory` must be a Boolean.

### Time and ownership

- `SAVE_DEBOUNCE_NS = 2_000_000_000` is fixed. Use `time.monotonic_ns` through a private
  test seam; no wall-clock timestamps, timer threads, sleep loop or adjustable public
  debounce bypass. The WP-06 observation interval remains an explicit separate input.
- Sample time inside the state lock. Equal monotonic readings are allowed. Invalid
  or backward readings fail without changing state; tests use a controlled clock.
- State methods are thread-safe and atomic. Never hold the state lock during file I/O,
  hashing, parsing, waiting or a consumer operation. The coordinator creates no thread.
- `take_due()` returns no work before the exact deadline or while already running.
  On a due attempt it atomically reserves one opaque immutable token and consumes the
  current pending intent. Later notices create at most one new pending intent.
- Only the exact active token object issued by that coordinator can finish an attempt,
  exactly once. Foreign, distinct forged/copied, stale or already completed tokens and invalid
  outcomes raise a safe state/policy error without changing the active or pending work.
- Keep only the latest relevant-notice time, deadline, state and active-token metadata;
  do not retain one item per notification. Construction is idle and performs no I/O.

### State transitions

States are `IDLE`, `WAITING`, `RUNNING` and `FAULTED`. Let `E` be the latest matching
notice time, `F` completion time and `D` the fixed two-second interval.

| Trigger | State/result |
|---|---|
| Matching notice while idle/waiting | `WAITING`; one pending attempt due at `E + D` |
| Matching notice while running | Stay `RUNNING`; remember one follow-up and latest `E` |
| Due take while waiting | `RUNNING`; one active token; consume pending intent |
| Success, no newer notice | `IDLE` |
| Success with newer notice | `WAITING`, due at `E + D`; immediately eligible if this time has already passed |
| Direct source-not-ready failure | `WAITING`, due at `max(F + D, E + D)`; retry remains pending even without a new notice |
| Reader rejection, no newer notice | `IDLE`; no timer retry of the rejected generation; a fresh matching notice can schedule another attempt |
| Reader rejection with newer notice | `WAITING`, due at `E + D`; preserve the follow-up |
| Non-retryable acquisition, unexpected or grouped failure | `FAULTED`; preserve a single pending intent; no automatic take |
| Matching notice while faulted | Stay `FAULTED`; retain the latest notice, without starting work |
| Explicit `resume_after_fault()` | `WAITING`, due two seconds after resume; one attempt even if no fresh notice arrived |

Every actual subsequent notice while waiting moves its due time to that notice plus
two seconds. An overdue retry is not a backlog of retries: one successful take creates
one attempt. `resume_after_fault()` is valid only in `FAULTED` with no active attempt;
it is an application control for explicitly handled faults, not a new product UI or
an authorization rule for accounting edits. No attempt is abandoned on a timeout or
cancelled merely because another notice arrives.

### Reader driver and failure boundary

Expose one synchronous `read_due_source(...)` driver. It reserves an attempt, opens
the configured source using the unchanged `open_stable_xlsx_snapshot(...)`, passes
only the lease's managed path to `read_xlsx_source_snapshot(...)`, exits the context
completely, finishes the token, and only then returns the existing
`XlsxSourceReadResult`. With no due work it returns `None` and does no filesystem I/O.
The caller may run this driver on a worker thread; thread creation and observer
lifecycle are outside this package.

The driver maps outcomes as follows and re-raises the original failure after updating
state. It does not convert an error into `None`, an empty workbook or a success flag.

- A direct `XlsxSourceNotReadyError`: source-not-ready retry.
- A direct `XlsxSourceReadError` after clean lease exit: Reader rejection.
- Every other failure, including any `ExceptionGroup`/`BaseExceptionGroup`, cleanup or
  integrity failure and cancellation via `BaseException`: faulted, with active
  ownership released and the original failure/group preserved. The presence of a
  retryable member inside a group never makes the group retryable.
- A Reader result followed by a failing context exit is failure, never success. If
  driver bookkeeping itself fails, a token-scoped internal failure guard must release
  only that driver's still-active token into faulted state and preserve newer intent.
  It must not release a different attempt. Retain both causes when another exception
  is already active. This guarded driver recovery is separate from the no-mutation
  rule for an invalid standalone state-method call.

No last-successful hash, financial revision or durable baseline is updated here.
Never suppress a read by comparing the whole-file SHA-256 from WP-06. Logical
no-change behavior is demonstrated with WP-04's accepted Planner over the complete
Reader result: even a different ZIP/formula representation may have unchanged Raw.

### Diagnostics and public surface

Expose only the version/constants, typed event/state/outcome enums, opaque attempt,
immutable path-free state view, coordinator, driver and safe policy/state errors
defined in WP-07. Public coordinator/attempt/view representations and default errors
must not contain source/snapshot paths, workbook values or raw exception text.
Underlying driver exceptions retain the existing WP-05/WP-06 typed diagnostics and
causes; do not interpolate them into a new public message.

## Roadmap and acceptance impact

- References: sections 5.1/5.2, 15.3/15.6 and 19.1, O-31, O-46/O-49, O-70/O-71/O-72.
- WP-07 must prove exact-path filtering, deadline boundaries, notice bursts, atomic
  reservation/completion, all outcomes, Saves during Reader/cleanup, and retry without
  another notice. Tests use a fake monotonic clock and deterministic thread barriers.
- Composition uses real generated four-sheet ZIP/XLSX files and the unchanged WP-06,
  WP-05 and WP-04 contracts. Retain all 274 existing tests and their platform evidence,
  including native Windows symlink coverage and existing benchmark limits.
- Real filesystem notification delivery, Windows/OneDrive Save/rename timing, crash
  recovery/startup rescan, COM/UUID, persistence, business validation and the lifetime
  of a committed import remain separate evidence. G1 stays open.

## Migration, rollback and reconsideration

No schema, migration or durable data is introduced. The new unused adapter can be
disabled without deleting source files or financial events. Volatile scheduling
intent is lost on process termination; production restart reconciliation must be
designed before deployment. Revisit this ADR before extending attempt ownership
through a database commit, adding startup recovery, changing retry policy or changing
the accepted two-second business rule.

## Approval

Codex Project Manager accepts this bounded technical decision on 2026-09-02 following
the Owner's instruction to continue with WP-07. Implementation starts only after this
ADR and WP-07 are merged. This records a technical contract, not proof of its tests,
closure of G1, or authority to use real data or mutate protected assets.
