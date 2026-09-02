# WP-06: Stable XLSX snapshot acquisition and cleanup

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 stable-file acquisition boundary only; this package cannot close G1
- Status: Accepted and merged
- Issued by: Codex Project Manager
- Issued on: 2026-09-01
- Accepted by: Codex Project Manager
- Accepted on: 2026-09-02
- Review evidence: PR #22 and final CI Run `33616797405`
- Merge commit: `1346abf8b0bda329e6df452bc16a0a0f7d9ed9f7`
- Required skill: `accounting-bot-implementer`
- Branch: `antigravity/phase-01-stable-xlsx-snapshot-acquisition`
- Baseline: latest clean `origin/main` containing this Work Package and ADR-0009
- Handoff path: `handoffs/phase-01/wp-06-stable-xlsx-snapshot-acquisition/`

## Objective

Implement the bounded adapter that converts one caller-supplied, exact operational
`.xlsx` path into one verified, immutable-for-the-lease temporary snapshot. Prove that
WP-05 reads only that copy, that a concurrent source change cannot produce a partial
result and that all known temporary artifacts are removed after ordinary success or
controlled failure.

This is not the Save/OneDrive monitor or the production importer. It does not watch a
directory, implement the two-second debounce/coalescing state machine, connect to Excel
COM, write UUIDs, select a fiscal year, validate accounting rules, read SQLite state,
commit changes, create an Outbox item or contact a server. No real workbook or
real-data copy is required or authorized.

## Authority and traceability

- Roadmap sections 5.1/5.2, 15.3 and 19.1 plus O-31: stable observations, fixed-copy
  import and protection against partial Save/Replace reads.
- O-53 / [ADR-0002](../../adr/ADR-0002-windows-excel-agent.md): independent local-agent
  adapter and fixed XLSX snapshot boundary.
- [WP-05](WP-05-streaming-xlsx-source-reader.md) / [ADR-0008](../../adr/ADR-0008-streaming-xlsx-source-reader.md):
  accepted Reader which consumes an already stable path and remains unchanged.
- O-71 / [ADR-0009](../../adr/ADR-0009-stable-xlsx-snapshot-acquisition.md): normative
  stability, copy, integrity, lease, cleanup and error contract.
- O-46 through O-51: bounded implementation, synthetic-only evidence, protected assets
  and independent Codex review.

Read ADR-0009 completely before implementation. Its acquisition/lifecycle decisions are
normative; the requirements below define executable evidence.

## Public API and placement

1. Add a focused module in `accounting_local_agent` and export
   `XLSX_SNAPSHOT_ACQUISITION_VERSION = "xlsx-snapshot-acquisition.v1"`, immutable
   `StableXlsxSnapshot`, `XlsxSnapshotAcquisitionReason`, the typed error family and
   `open_stable_xlsx_snapshot(...)` from the package root.
2. `open_stable_xlsx_snapshot` accepts only an exact source `Path`, an existing
   controlled snapshot-root `Path` and an explicit finite positive observation interval.
   It returns a typed context manager and yields only after all checks and promotion
   succeed. Keep clock/sleep/file-operation fault seams private or test-only; do not
   expose a general plug-in filesystem in the product API.
3. `StableXlsxSnapshot` contains exactly the acquisition version, managed snapshot
   path, lowercase SHA-256 file digest, nonnegative exact byte count and nonnegative
   source `mtime_ns`. It is defensively immutable; direct construction rejects Boolean,
   malformed digest, wrong version, a non-`Path`/non-absolute/non-`.xlsx` managed path or
   negative count/time. Lease ownership and deletion remain internal to the context
   manager; a caller-constructed metadata object never grants cleanup authority.
4. The acquisition digest is named `file_sha256`; never reuse or recalculate WP-03
   source/sheet hashes and never call it a ledger hash. Do not store source content,
   absolute source path, rows or workbook metadata in the result.
5. No `skip_stability`, `allow_partial`, caller-supplied digest, caller-selected output
   filename, overwrite mode or retained-snapshot flag is permitted.

## Required behavior

### A. Policy, observation and source safety

- Accept `.xlsx` case-insensitively and reject a directory, non-file or other extension
  before copying. Read exactly the supplied path. Do not enumerate the parent directory,
  guess an archive/current workbook, follow a fallback, inspect conflict-copy siblings
  or apply a fuzzy filename match.
- Reject a Boolean, NaN, infinity, zero or negative observation interval. Unit and
  property tests must use a deterministic private sleeper/clock seam instead of making
  the suite wait in wall-clock time.
- Take two observations separated by the interval and compare size, `mtime_ns` and the
  platform's meaningful device/file identity. Open the source read-only and compare
  handle metadata with the accepted observation before and after each source pass.
- A source that disappears, becomes inaccessible, is replaced, changes identity,
  length, timestamp or digest, or cannot yet form a readable ZIP container produces a
  typed retryable not-ready error. It does not yield a path/result or invoke WP-05.
- Never write, rename, chmod, touch or open the source for update. Tests must compare its
  bytes/SHA-256, size, `mtime_ns` and permissions before and after success and every
  injected failure supported by the test platform.

### B. Controlled streaming copy and verification

- Require the caller's controlled root to exist and be a directory. Create one
  unpredictable private child directory per acquisition and one exclusive `.part`
  candidate inside it. Never use a source-derived filename and never overwrite an
  existing path.
- Copy through fixed bounded reads and writes while calculating SHA-256 and byte count.
  A test double must prove there is no `read()`-all or source-size allocation and that
  the maximum requested chunk is bounded by the implementation constant.
- Compare bytes copied with the accepted source size. Flush the candidate and, where
  supported by the normal file API, synchronize the file before verification.
- Re-read the exact source in a second full verification pass and require unchanged
  handle/path metadata, byte count and digest. Re-read the candidate and require the
  same byte count and digest. Fault-injection tests cover changes before open, during
  copy, between copy and verification, during verification and an atomic source
  replacement.
- Open the candidate with standard `zipfile` container parsing before promotion. Reject
  a non-ZIP, truncated/incomplete central directory or missing `[Content_Types].xml`
  package marker as not ready. Do not extract members, follow relationships, inflate
  every unrelated member or duplicate WP-05 OPC/worksheet validation.
- Atomically promote `.part` to a final unique `.xlsx` path inside the same private
  directory. The public context must never expose `.part` or an unverified candidate.

### C. Lease integrity, cleanup and concurrency

- The yielded path exists only for the context lifetime and is the only workbook path
  supplied to `read_xlsx_source_snapshot`. Verify its byte count and digest again on
  exit before deleting it; mutation, replacement or disappearance while leased is a
  typed integrity failure.
- On consumer success, consumer exception and every acquisition failure, attempt to
  remove only the exact partial/final file and private directory created by that
  acquisition. Cleanup is idempotent for already-removed managed artifacts. Never use
  recursive deletion on the caller's root and never delete an unexpected entry.
- A cleanup failure is typed and visible. If cleanup/integrity fails while another
  exception is active, preserve both failures using structured/chained Python 3.13
  semantics rather than replacing or silently logging the consumer error.
- Parallel acquisitions, including identical content, create distinct lease directories
  with no overwrite or cross-cleanup. Equal source bytes have equal `file_sha256`.
- Hard process termination, startup reaping of abandoned directories and retention
  policy are explicitly pending; do not claim them from `finally`/context tests.

### D. Typed diagnostics and composition

- Provide a base acquisition exception with stable reason and `retryable` fields plus
  typed subclasses/categories for source-not-ready, source-policy, storage, integrity
  and cleanup failures. Use the exact reason values `source_not_ready`,
  `source_policy_violation`, `snapshot_storage_failure`,
  `snapshot_integrity_failure` and `snapshot_cleanup_failure`. Only
  `source_not_ready` is retryable in version 1; derive that value from the reason and
  reject inconsistent direct construction.
- Default `str`/`repr` output contains no raw workbook value or absolute source/snapshot
  path. Chain the underlying exception for local diagnostics without interpolating it
  into the safe message.
- Add one composition helper only if it remains a thin context/Reader call and introduces
  no persistence or retry loop. Otherwise demonstrate composition directly in tests.
- WP-05 public behavior, source contracts, canonical hashes and Planner semantics must
  remain unchanged. A stable copy does not authorize a database commit or a `void`.

## Required tests and evidence

Use generated synthetic ZIP/XLSX fixtures only. At minimum prove:

| ID | Acceptance criterion |
|---|---|
| SA-01 | Public version/API exports, immutable metadata and direct-construction invariants are exact |
| SA-02 | Two ordered observations and the explicit interval occur before source copy; invalid policy is rejected |
| SA-03 | Source bytes/hash/size/mtime/permissions remain unchanged on success and all injected failure paths |
| SA-04 | Missing, locked/inaccessible, truncated and changing sources fail with the correct retryable reason and yield nothing |
| SA-05 | In-place mutation and atomic replacement at every copy/verification race point cannot produce a snapshot |
| SA-06 | Streaming copy is bounded, exact, SHA-256 verified and independent of workbook size in memory allocation strategy |
| SA-07 | Invalid ZIP/container, write/flush/sync, disk/storage and atomic-promotion faults are typed and leave no known candidate |
| SA-08 | The consumer observes only a promoted `.xlsx`; original-path mutation after yield does not change Reader output |
| SA-09 | Lease mutation/deletion is detected; cleanup succeeds after normal return and consumer failure |
| SA-10 | Cleanup failure remains visible and preserves an already-active consumer/integrity exception |
| SA-11 | Concurrent same/different-content acquisitions use disjoint paths; cleanup of one cannot affect another |
| SA-12 | Full generated four-sheet workbook passes acquisition then WP-05 and equals the direct independent WP-04 oracle |
| SA-13 | Source change produces no Reader call/result and therefore cannot produce an incomplete Planner/void result |
| SA-14 | All existing 200 tests plus new deterministic/property/race tests, quality checks and both CI jobs pass |

Use Hypothesis for valid policy/size/chunk/concurrency-relevant invariants where useful,
but keep race injection deterministic; do not rely on probabilistic thread timing to
prove a safety property.

### Combined benchmark protocol

- Reuse or safely factor the generated WP-05 benchmark fixture: 15,000 active rows over
  all four sheets plus at least 5,000 inactive/formula-only tail rows and unrelated
  shared strings. Creating the source is outside the measured window.
- In a fresh process measure the full public acquisition context entry, WP-05 Reader
  call, lease integrity check and cleanup. Use the real standard-library copy/hash/ZIP
  path and a deterministic no-wall-clock private probe seam; do not skip a logical
  observation or verification pass.
- Print verified row count, file byte count, file SHA-256, total seconds and actual
  process peak RSS/Windows working set. Keep the existing combined limit under 15
  seconds and under 128 MiB on Linux and Windows.
- Keep the benchmark in normal pytest and visible under current CI capture behavior.
  A threshold failure is a review issue; do not raise the limit, reduce the accepted
  Reader fixture or remove a verification pass to make it green.
- These synthetic numbers do not close the real-workbook, real OneDrive or G1 criteria.

### Quality and handoff commands

Run focused tests first, then at least:

```text
uv lock --check
uv sync --frozen --all-packages --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run mypy --platform win32 .
uv run pytest -v
git diff --check origin/main...HEAD
python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-06-stable-xlsx-snapshot-acquisition/
```

Record the exact focused/benchmark/full-suite commands, OS/Python versions, exit codes
and output. Use failure-on-match tracked-asset and sensitive-pattern scans that do not
mask tool errors. Do not weaken CI, strict typing, WP-05 tests or architecture guards.

## Allowed files and changes

- Focused acquisition module(s) under
  `apps/local_agent/src/accounting_local_agent/` and package exports/README.
- New acquisition tests and narrowly factored synthetic test-fixture support.
- The WP-06 Handoff directory produced from the required Skill templates.
- No dependency/lockfile change is expected. Stop for Codex before changing a contract
  package, WP-05 semantics, accepted test oracle, CI workflow or another application.

## Preconditions, stop conditions and handoff

Start only in `/srv/accounting-bot/workspace`, with a clean `main` exactly equal to
`origin/main` and containing ADR-0009/WP-06. Activate the repository
`accounting-bot-implementer` Skill and create the exact implementation branch above.

Stop for Codex if the baseline is dirty/divergent; a safe implementation appears to
require a new dependency, watcher/COM/SQLite work, recursive deletion, a weakened
Reader/test/benchmark, a real workbook, or a change to an accepted source/hash contract.
Do not reinterpret protected-action permission from the Owner's authorization to issue
this package.

Complete `handoff.md`, `acceptance-matrix.md` and `test-results.txt`, run the validator,
make linear implementation/evidence commits and report exact baseline/HEAD, changed
files, API/error taxonomy, observation/copy/verification/cleanup strategy, focused and
full tests, benchmark measurements, residual risks and protected-asset checks. Stop for
independent Codex review. Do not Push, create a PR, Merge, deploy, edit Roadmap, access
real Excel or start WP-07.

## Independent acceptance record

Codex Project Manager accepted this package after independently reviewing candidate
`503fe2c9635bf268c91c720462413ac75e568efc` against baseline
`bc085acd8852e2293fdfcb786a7694fe96e93407`, replaying the previously failing
candidate-creation, Windows error-lifecycle and foreign-file replacement scenarios,
and merging [PR #22](https://github.com/mohsenbarari/accounting-bot-public/pull/22)
with the Owner's explicit authorization.

- [CI Run 33616797405](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33616797405)
  passed both quality/test jobs on the reviewed HEAD. Linux: 272 passed and 2
  Windows-only skips. Windows: 273 passed and 1 POSIX-only skip; all four symlink
  scenarios executed successfully.
- Independent full-suite replay passed on Linux (272 passed, 2 Windows-only skips)
  and local Windows (269 passed, 4 symlink privilege skips and 1 POSIX-only skip).
  Native Windows handle protection and ordinary lease lifecycle tests executed.
- Independent pre-existing-candidate and hardlink probes confirmed no snapshot was
  yielded, foreign bytes survived and the source remained unchanged. A normal fresh
  Windows process yielded a valid four-sheet snapshot and closed its handle after
  integrity verification and cleanup.
- Independent injected Create/Query/Close failures confirmed safe public typed
  messages, preservation of both original causes for simultaneous Query/Close
  failure, and exactly one close attempt. The replacement oracle demonstrated an
  actual file-identity change rather than only editing bytes in the same file.
- The complete 15,000-active-row acquisition/Reader benchmark measured
  `12.8081s / 59.83 MiB` on Linux and `2.8819s / 56.52 MiB` on local Windows,
  below the unchanged 15-second and 128-MiB limits. Normal CI also passed this
  benchmark on both platforms.
- Lock/frozen sync, Ruff format/lint, strict typing, Handoff validation and diff
  checks passed. The defined prohibited-asset and credential-pattern scan found no
  matches across 92 tracked files and 89 branch blobs. Review and test fixtures
  used generated synthetic workbooks only.

Acceptance covers stable snapshot acquisition, lease integrity and ownership-checked
cleanup only. Save/OneDrive monitoring, debounce/coalescing, real workbook behavior,
COM/UUID writes, fiscal/archive selection, SQLite/import commits, Outbox and crash
recovery remain outside this package. G1 remains open. This acceptance does not issue
WP-07 or authorize protected real-data or production actions.
