# WP-07: Save debounce, coalescing and source-read coordination

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 scheduling boundary only; this package cannot close G1
- Status: Issued — implementation and evidence pending
- Issued by: Codex Project Manager
- Issued on: 2026-09-02
- Required skill: `accounting-bot-implementer`
- Branch: `antigravity/phase-01-save-import-coordinator`
- Baseline: latest clean `origin/main` containing this Work Package and ADR-0010
- Handoff path: `handoffs/phase-01/wp-07-save-import-coordinator/`

## Objective and boundary

Implement the deterministic, thread-safe coordinator for notifications about one exact
source path, plus one synchronous source-read attempt driver using the accepted
WP-06 snapshot lease and WP-05 Reader. Prove the two-second quiet period, coalescing,
one active attempt and one follow-up, without relying on real notification timing.

This package processes normalized notices supplied by a caller. It does not start a
watchdog Observer, monitor real directories, attach to Excel, write UUIDs, implement
startup reconciliation, commit an import or produce financial/Sync events. A completed
read is not a committed import or a durable baseline. Only generated synthetic files
are allowed. No protected real-data or production action is authorized.

## Authority and traceability

- Roadmap sections 5.1/5.2 and O-31: exact operational path, two-second debounce,
  coalescing and one follow-up after a Save during work.
- O-72 / [ADR-0010](../../adr/ADR-0010-save-import-coordinator.md): normative event,
  time, ownership, transition, retry and driver contract. Read the entire ADR first.
- [WP-06](WP-06-stable-xlsx-snapshot-acquisition.md) /
  [ADR-0009](../../adr/ADR-0009-stable-xlsx-snapshot-acquisition.md): accepted source
  stability, managed lease, safe failures and cleanup; keep unchanged.
- [WP-05](WP-05-streaming-xlsx-source-reader.md) and
  [WP-04](WP-04-deterministic-source-change-plan.md): complete Reader output and
  independent logical no-change evidence; scheduling does not replace validation.
- O-46 through O-51: bounded implementation and independent review.

## Required public API

Implement `accounting_local_agent.save_import_coordinator` and export:

- `SAVE_IMPORT_COORDINATOR_VERSION = "save-import-coordinator.v1"`.
- `SAVE_DEBOUNCE_NS = 2_000_000_000`.
- `SaveEventKind`: `created`, `modified`, `deleted`, `moved`, `opened`, `closed`,
  `accessed`; the last three are ignored after input validation.
- `SaveCoordinatorState`: `idle`, `waiting`, `running`, `faulted`.
- `SourceReadOutcome`: `success`, `source_not_ready`, `reader_rejected`, `faulted`.
- `SourceReadAttempt`: immutable opaque capability for one active reservation.
- `SaveCoordinatorView`: immutable, path-free fields `version`, `state`, `pending`,
  `next_due_ns`. `next_due_ns` is a nonnegative exact integer only in `waiting`,
  otherwise `None`; `pending` is false in idle, true in waiting/faulted and records a
  newer notice in running. Direct construction must enforce these invariants.
- `SaveCoordinatorError`, `SaveCoordinatorPolicyError`, `SaveCoordinatorStateError`:
  stable safe reasons `invalid_policy` and `invalid_transition` on the subclasses;
  no caller-provided raw diagnostic message is needed.
- `SaveImportCoordinator(source_path: Path)` with:
  - `notify(kind: SaveEventKind, source_path: Path, *, destination_path: Path | None = None, is_directory: bool = False) -> bool`;
  - `take_due() -> SourceReadAttempt | None`;
  - `finish(attempt: SourceReadAttempt, outcome: SourceReadOutcome) -> None`;
  - `resume_after_fault() -> None`;
  - `view() -> SaveCoordinatorView`.
- `read_due_source(coordinator: SaveImportCoordinator, *, snapshot_root: Path, observation_interval_seconds: float) -> XlsxSourceReadResult | None`.

Do not add timer/observer startup, an event-history queue, an asynchronous job service,
a public clock/debounce bypass, a user-supplied filesystem, a hash cache or a last-import
baseline. Use a private monotonic-clock seam for deterministic tests.

## Required behavior

### SC-A: Exact notices and time

Implement the complete path and notice rules in ADR-0010. Matching is host-native and
lexical; construction/notification/polling never inspect the filesystem. Reject a
non-Path, relative or unresolved-parent path, invalid event enum, invalid move
destination and non-Boolean directory marker with no state change. A valid configured
file may temporarily not exist. Ignore other paths and read-only/directory notices.

A move from a temporary file to the exact target must schedule work. A move away
from the target must also schedule work, but must never retarget the acquisition.
Windows case-equivalent paths match under Windows semantics; Linux comparison remains
case-sensitive. Do not implement fuzzy OneDrive name matching.

Use a private `time.monotonic_ns` seam and a fixed two-second interval. At deadline
minus one nanosecond no work is due; at the deadline exactly one take can succeed.
Store only current scheduling state, not a list of notices or timer objects. An
unrelated event must not postpone work or manufacture a follow-up.

### SC-B: Ownership and transitions

Implement every transition in ADR-0010, including retry without another notice,
Reader rejection awaiting a fresh Save, faulted-state event retention and explicit
resume. Every valid notice while running coalesces into one pending follow-up whose
deadline comes from the latest notice. An expired follow-up deadline permits an
immediate take after completion, without an extra two-second wait.

State methods must be thread-safe. Two concurrent due callers cannot both reserve;
notification must remain responsive while a driver is blocked in file I/O or parsing.
Never hold the state lock across the WP-06 context, Reader call or cleanup.

Finish only the exact active token from that coordinator, exactly once. Distinct
lookalike/copied tokens confer no authority; copying may also be explicitly rejected.
Invalid token,
outcome, time or transition must leave the state unchanged. Invalid calls cannot
clear a pending Save, release another token or advance its deadline. Equal monotonic
readings are valid; a backward or invalid clock reading is an invalid transition.

### SC-C: One complete Reader attempt

The thin driver must do no file I/O when no work is due. For a reserved attempt, call
`open_stable_xlsx_snapshot` on the unchanged configured source, then
`read_xlsx_source_snapshot` only on the yielded managed path. Release the lease and
verify cleanup/integrity before reporting success or returning a Reader result.

Re-raise failures with original identities/causes intact after finishing the correct
outcome. Only a direct `XlsxSourceNotReadyError` schedules an automatic timed retry.
A direct Reader error after clean lease exit yields `reader_rejected`. Any group,
non-retryable acquisition error, unknown exception or `BaseException` cancellation
faults the coordinator and releases active ownership. A retryable member in a group
does not justify retry. A token-scoped driver failure guard must release only its
still-active reservation and preserve pending intent if bookkeeping fails; retain
both failures during exception handling and never release a different active token.
Faulted state cannot run until explicit resume.

The state model accepts explicit outcomes for composition/testing; those outcomes do
not certify workbook validity or grant commit authority. The driver is the supplied
path that proves complete acquisition, Reader and context-exit success together.
Production persistence must have a separately reviewed completion boundary.

## Acceptance matrix

Use deterministic fake time, controlled barriers and real generated ZIP/XLSX fixtures.
The evidence must assert observable transitions/results, exact counts and original
failure identity, not merely that a private helper was invoked.

| ID | Required evidence |
|---|---|
| SC-01 | Public version/API, strict inputs, immutable views/tokens, valid direct view construction and safe representations/errors |
| SC-02 | Exact-path and platform case semantics; lock/tmp/conflict/archive/snapshot/unrelated paths and directory/read-only notices have no scheduling effect |
| SC-03 | Move into/out of target, including temporary source into target; acquisition always uses configured target and never follows a renamed destination |
| SC-04 | Idle construction; deadline -1ns/exact/+1ns; repeated notices reset to latest +2s; unrelated notices do not delay work; invalid/backward time cannot mutate state |
| SC-05 | Thousands of notices before work coalesce to one reservation; a due time far in the past creates one attempt, not a retry backlog |
| SC-06 | Two barrier-controlled due callers yield exactly one token; foreign/copied/stale/double-finish tokens and invalid outcomes preserve active/pending state |
| SC-07 | Multiple notices during work produce exactly one follow-up; verify both already-expired and still-future follow-up deadlines and notices racing with finish |
| SC-08 | Direct not-ready schedules one retry at completion +2s or later latest-notice deadline, with no further notice; later readiness succeeds; no busy-loop retry |
| SC-09 | Reader rejection has no unchanged-generation timer retry; a newer/fresh notice survives and can succeed |
| SC-10 | Policy/storage/integrity/cleanup/unknown/grouped failures fault, release active ownership and preserve pending intent; notices alone cannot resume; explicit resume schedules one attempt; injected driver-bookkeeping failure preserves both errors and cannot leave its token running or clear another token |
| SC-11 | No due work means zero acquisition/Reader calls and zero filesystem I/O; due work passes the exact source then managed lease path, and returns only after successful context exit |
| SC-12 | Synthetic success, source-change failure, late Reader failure, cleanup/integrity-after-Reader failure and cancellation retain typed causes/groups, yield no false result, preserve source bytes/metadata and observe WP-06 ownership rules |
| SC-13 | Matching notice delivered while the real Reader or lease cleanup is barrier-blocked is accepted without waiting for that I/O; reserved attempts never overlap; pending follow-up survives |
| SC-14 | Independent WP-04 Planner oracle over Reader results proves no Insert/Edit/Void for unchanged Raw, including a changed ZIP/formula representation; coordinator never treats whole-file SHA as source hash or advances a baseline |
| SC-15 | Hypothesis operation traces compare full observable state and successful take/completion counts with a separately written transition-table oracle; cover time advance, notices, take, finish and fault resume |
| SC-16 | Retain all 274 existing tests, platform skip rules, native Windows symlink execution in CI, and unchanged 15,000-row benchmark limits; full Linux/Windows quality and tests pass |

Thread tests need explicit rendezvous and bounded failure timeouts; do not prove races
by random sleeps. Fake clock must drive the real coordinator. For composition, do not
mock away both WP-06 and WP-05: include real public acquisition/Reader calls and actual
four-sheet generated files. Failure probes may use narrow private seams while keeping
the observed lifecycle real. A failed cleanup may correctly leave an unowned foreign
artifact; do not delete it to make a test pass.

## Quality and evidence

Run focused tests before the whole suite. At minimum record exact commands, exit
codes, Python/platform, collected/passed/skipped counts and skip reasons:

```text
uv lock --check
uv sync --frozen --all-packages --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run mypy --platform win32 .
uv run pytest tests/test_save_import_coordinator.py -v
uv run pytest -v
git diff --check origin/main...HEAD
python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-07-save-import-coordinator/
```

Run failure-on-match tracked-asset and sensitive-pattern scans without masking tool
errors. Do not commit generated workbooks, databases, raw logs with private paths,
credentials or real data. Linux-only implementation evidence must not claim Windows
execution; native Windows and both CI jobs are independent acceptance requirements.

## Allowed changes

- New `apps/local_agent/src/accounting_local_agent/save_import_coordinator.py`.
- Package exports in `apps/local_agent/src/accounting_local_agent/__init__.py`.
- `apps/local_agent/README.md` documenting the bounded API and lifecycle.
- New `tests/test_save_import_coordinator.py` and, only if needed, focused new synthetic
  fixture helpers. Reuse fixtures without changing accepted Reader/acquisition tests.
- The three completed handoff files under the declared WP-07 handoff path.

Do not modify WP-05/WP-06 modules, source/hash/Planner contracts, dependencies/lockfile,
CI, Roadmap, this Work Package, ADRs or the Skill. If an accepted contract appears to
prevent a safe implementation, document the exact conflict and stop for Codex review.

## Preconditions and handoff

Start in `/srv/accounting-bot/workspace` only after main is clean, equals `origin/main`
and contains this package plus ADR-0010. Activate the repository implementer Skill and
create the exact Antigravity branch named above. Preserve unrelated changes and all
accepted test oracles.

Complete `handoff.md`, `acceptance-matrix.md` and `test-results.txt`, validate the
handoff, make linear implementation/evidence commits, and report exact baseline/HEAD,
commit list, changed files, transition coverage, API/error behavior, concurrency and
composition evidence, test counts and remaining gaps. Record the final handoff commit
in the final report; do not create repeated documentation-only commits merely to
make a file contain the hash of its own commit.

Stop for independent Codex review. Do not push, create a PR, merge, deploy, change
Roadmap/Gates, access real Excel, start an Observer or continue into WP-08.
