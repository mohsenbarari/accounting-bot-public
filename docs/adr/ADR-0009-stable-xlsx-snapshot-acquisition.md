# ADR-0009: Stable XLSX snapshot acquisition and cleanup

- Status: Accepted
- Date: 2026-09-01
- Work Package: Phase 1 — WP-06
- Decision owner: Codex Project Manager under O-46/O-49

## Context

WP-05 reads a caller-supplied stable XLSX file and deliberately does not acquire that
file. The Roadmap requires the production importer to wait for stability, copy the
whole workbook into controlled temporary storage, audit the file hash, read only that
copy and clean it after both success and controlled failure. Without a separate
boundary, a writer or OneDrive replacement could make one import observe a mixture of
file generations and could turn an incomplete read into false edits or voids.

This ADR resolves O-71. It refines the fixed-snapshot portion of ADR-0002 and O-31; it
does not implement file-system event monitoring, Excel COM, UUID writeback or a
database import.

## Constraints

- The reference workbook and every real-data copy remain protected. Implementation
  evidence uses only generated synthetic files.
- The source is opened read-only. Acquisition must never change its bytes, metadata,
  permissions or Excel state.
- The caller supplies the exact configured source path after a future coordinator has
  completed the accepted two-second debounce. This component does not scan a directory
  or select a workbook by name.
- A returned snapshot must represent one complete, stable source generation or no
  snapshot may be returned.
- Temporary files are local implementation artifacts and must not be committed,
  uploaded, logged by absolute path or retained after an ordinary success/failure
  lifecycle.
- Use Python 3.13 standard-library file, hashing, ZIP and temporary-file facilities.
  No dependency or lockfile change is justified for this boundary.
- G1 remains open until monitoring, UUID, validation, persistence and end-to-end import
  evidence are independently accepted.

## Options considered

### Option A — Separate acquisition lease before the accepted Reader

- Benefits: isolates time-of-check/time-of-use races, makes cleanup ownership explicit,
  provides one auditable file hash and keeps WP-05 purely read-only.
- Costs/risks: requires additional reads of the source and careful error/cleanup
  handling.
- Reversibility: high; the adapter has no persistent schema or external side effect.

### Option B — Let WP-05 read the operational path directly

- Benefits: fewer file operations.
- Costs/risks: a Save or atomic OneDrive replacement during parsing can mix source
  generations or produce a false deletion plan.
- Reversibility: low after such a result reaches persistence.

### Option C — Copy on every file-system event

- Benefits: simple trigger wiring.
- Costs/risks: events do not prove Save completion; repeated Excel/OneDrive events create
  partial and duplicate candidates and couple monitoring to acquisition.
- Reversibility: moderate, with unnecessary temporary-file churn.

## Recommendation and accepted decision

Option A is accepted. Implement `xlsx-snapshot-acquisition.v1` in
`accounting-local-agent` as a context-managed lease placed immediately before the
accepted WP-05 Reader.

### Public boundary and metadata

Expose `XLSX_SNAPSHOT_ACQUISITION_VERSION = "xlsx-snapshot-acquisition.v1"` and a
typed `open_stable_xlsx_snapshot(...)` context-manager API. It accepts an exact source
`Path`, an existing controlled snapshot-root `Path` and an explicit positive finite
stability-observation interval. The operational interval remains configurable pending
real Windows/OneDrive evidence; the accepted two-second debounce belongs to the later
coordinator.

The context yields an immutable `StableXlsxSnapshot` only after acquisition is fully
verified. Its public metadata is limited to the version, managed snapshot path,
lowercase 64-character SHA-256 file digest, exact byte count and observed source
`mtime_ns`. The file digest is an audit/content digest and must never be confused with
WP-03 `source_hash`, `sheet_snapshot_hash` or a future ledger hash. Direct construction
must enforce the same invariants as factory construction. The object and default error
messages must not expose the absolute source path.

There is no bypass flag such as `skip_stability`, `allow_partial`, caller-supplied hash
or caller-selected output filename.

### Stability and copy protocol

1. Validate that the supplied source names an ordinary `.xlsx` file under platform
   file semantics. Read only that exact path; do not enumerate siblings, follow a
   fallback name or inspect a OneDrive conflict candidate.
2. Take two source observations separated by the supplied interval. Size and
   nanosecond modification time must match; device/file identity must also match where
   the platform exposes a meaningful identity. Boolean, nonfinite, zero or negative
   intervals are invalid policy, not a zero-delay probe.
3. Open the exact source read-only and compare handle metadata with the accepted
   observation before and after every source pass. A missing file, sharing/access
   failure, replacement, truncation or size/mtime/identity drift is a retryable
   not-ready result, never a partial success.
4. Create an unpredictable private child directory in the existing controlled root and
   an exclusively created `.part` file inside it. Stream the source in fixed bounded
   chunks while calculating SHA-256 and byte count; do not load the workbook into one
   bytes object and do not use a caller-controlled output path.
5. Re-read the exact source in a verification pass and require the same identity,
   metadata, byte count and SHA-256 as the copy pass. Re-read the temporary copy and
   require the same byte count and digest. This closes ordinary in-place-write and
   atomic-replacement races even on a file system whose timestamps have coarse or
   unusual behavior.
6. Verify the copied file as an ordinary readable ZIP/XLSX container with a
   `[Content_Types].xml` package marker, without extracting members or inflating every
   unrelated member. Detailed OPC, worksheet and consumed member validation remains
   the single responsibility of WP-05.
7. Flush the candidate, promote it atomically inside its private directory from the
   partial name to the final `.xlsx` name and only then yield it. No incomplete or
   unverified final path is observable by the consumer.

Changing the operational source after the final verification does not mutate the
snapshot already yielded; the later monitor must schedule the accepted single
follow-up import for a newly observed Save.

### Lease integrity and cleanup

The snapshot is valid only inside its context. WP-05 receives the managed path, not the
operational source path. On context exit, the manager verifies that the managed file
still has the recorded size and digest, then removes only the exact files and private
directory it created. It must not call a recursive delete on the caller's root or
remove unexpected entries.

Cleanup is attempted after consumer success and after consumer failure. A cleanup or
post-consumption integrity failure is typed and cannot be silently ignored. If a
consumer exception already exists, preserve both failures rather than replacing the
original cause. Acquisition failures clean any known partial/final candidate before
returning an error. Cleanup after process termination or machine loss is a later
startup-recovery responsibility and is not falsely claimed by a context manager.

Multiple concurrent acquisitions use separate private directories and cannot overwrite
one another. Equal source bytes produce equal file digests but do not share a mutable
lease path.

### Error boundary

Expose one typed acquisition error family with stable machine-readable reasons and a
`retryable` classification:

- source not ready: missing/inaccessible during the observation window, sharing
  conflict, metadata/identity/digest drift or an incomplete/unreadable ZIP candidate;
- source policy violation: wrong extension, non-file input or invalid probe policy;
- snapshot storage failure: controlled root, exclusive creation, write, flush or atomic
  promotion failure;
- snapshot integrity failure: copy/source/final digest or byte-count mismatch, or
  mutation while leased;
- snapshot cleanup failure: exact managed artifacts could not be removed safely.

In version 1, only `source_not_ready` has `retryable=True`; policy, storage, integrity
and cleanup failures require explicit handling and have `retryable=False`. The
classification is derived from the reason and cannot be supplied inconsistently by a
caller.

Default exception text contains only its reason/category, never source bytes, workbook
content or an absolute source/snapshot path. Preserve the underlying exception through
chaining for local diagnostics.

## Roadmap and acceptance impact

- References: sections 5.1/5.2, 15.3/15.6, 19.1, O-31, O-53, O-70 and O-71.
- WP-06 must prove deterministic observation/copy/reverification races, bounded
  streaming, ZIP/container rejection, lease integrity, cleanup and direct integration
  with the accepted WP-05 Reader using only generated synthetic files.
- The combined acquisition-to-Reader benchmark uses the existing 15,000-active-row
  synthetic workbook and unchanged 15-second/128-MiB call-window budgets on Linux and
  Windows. It does not replace the real-workbook G1 benchmark.
- Exact path event filtering, two-second debounce, Save coalescing, one follow-up import,
  OneDrive Rename/Replace behavior, file-release semantics, crash-orphan scavenging,
  COM/UUID, fiscal selection, required-field/business validation, SQLite/Outbox and
  production operations remain separate evidence.

## Migration and rollback impact

No schema, migration, workbook write, network call or durable queue is introduced.
Disabling/reverting the adapter removes only unused temporary acquisition code. No
snapshot is a durable accounting record.

## Reconsideration triggers

- Authorized real Windows/OneDrive evidence shows that the observation or identity
  token is insufficient for a supported Save/Replace sequence.
- Combined acquisition/Reader performance exceeds the unchanged G1 budget.
- A supported file system cannot provide safe atomic promotion inside the controlled
  root or reliable read-only source verification.
- Crash recovery requires a durable lease manifest; that requires a separately reviewed
  startup-lifecycle decision rather than recursive best-effort deletion.

## Approval required

Codex Project Manager approves this bounded technical decision on 2026-09-01 after the
Owner explicitly authorized issuance of WP-06. Implementation is authorized only after
this ADR and Work Package are merged. No reference Excel access, real-data operation,
Excel/COM write, deployment or production mutation is authorized.
