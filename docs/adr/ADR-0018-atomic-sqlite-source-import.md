# ADR-0018: Commit one source generation atomically to the local SQLite store

- Status: Accepted technical design by Codex Project Manager; implementation evidence pending
- Date: 2026-09-04
- Phase / gate: Phase 1 / G1 remains OPEN / IN PROGRESS
- Decision: O-80 in [ROADMAP.md](../../ROADMAP.md)
- Work package: [WP-15](../work-packages/phase-01/WP-15-source-import-store.md)
- Component version: `source-import-store.v1`

## Context and bounded decision

The accepted pipeline now acquires one stable XLSX lease, reads its marker and Raw
rows from the same package, validates required fields, derives fiscal evidence,
projects the selected source membership, plans deterministic transitions and
encodes exact Raw rows. None of those components commits durable state. The
`accounting-persistence` package is still a scaffold, while Roadmap 5.2 and 15.3
require Raw history, revisions, membership and local `change_events` to succeed or
roll back together under one `import_id`.

Introduce a local SQLite component for an already declared active source. It owns
one transaction per import, reconstructs the comparison basis from committed rows,
rejects stale proposals, reruns the accepted validators and Planner, appends exact
Raw revisions and durable change events, records membership even when a row is
UNCHANGED, and returns a commit receipt. It is not wired into the watcher and does
not open a path itself. All WP-15 evidence uses fresh temporary synthetic databases.

This first store version has an explicit bootstrap boundary: initialization creates
a new empty schema for one caller-supplied `device_id` and one caller-supplied ACTIVE
`SourceBindingKey`. It does not generate, infer, enroll, repair, archive or activate
a source. A later authorized enrollment/rollover component may populate the same
validated schema through a separately reviewed migration or API. WP-15 must not
touch the existing sample database or any real workbook/database copy.

## Public API

Add `accounting_persistence.source_import_store` and export exactly these ten
symbols through `accounting_persistence.__init__`:

| Symbol | Contract |
|---|---|
| `SOURCE_IMPORT_STORE_VERSION` | Exact string `source-import-store.v1`. |
| `SourceImportStoreReason` | Strict StrEnum with the reasons below. |
| `SourceImportStoreError` | `RuntimeError` subclass with a fixed-message reason; ordinary causes may be retained. |
| `SourceImportDisposition` | Strict StrEnum: `committed` or `replayed`. |
| `SourceImportRequest` | Frozen/slotted, defensively frozen commit input. |
| `SourceImportReceipt` | Frozen/slotted result containing counts and sequence range, never Raw. |
| `SourceImportStoreView` | Frozen/slotted consistent generation and reconstructed registry view. |
| `initialize_source_import_store` | Create only a brand-new empty local store and its first active key. |
| `read_source_import_store` | Read and validate one committed generation. |
| `commit_source_import` | Validate, plan and atomically commit or replay one import. |

Exact signatures:

```python
initialize_source_import_store(
    connection: sqlite3.Connection,
    *,
    device_id: uuid.UUID,
    active_source: SourceBindingKey,
) -> SourceImportStoreView

read_source_import_store(
    connection: sqlite3.Connection,
) -> SourceImportStoreView

commit_source_import(
    connection: sqlite3.Connection,
    request: SourceImportRequest,
) -> SourceImportReceipt
```

The caller owns and keeps the connection. No public path, URI, engine/session,
SQL string, migration switch, transaction callback, fault-injection hook, force
flag or trust/approval Boolean is accepted. Entry requires an idle connection;
the component never commits or rolls back unrelated caller work and never closes
the connection. Preserve caller row factory, isolation setting, busy timeout,
trace/progress callbacks and journal/synchronous policy. Foreign-key enforcement
must already be ON and is verified before schema/data operations; the component
does not toggle this connection-wide caller setting.

### Request, view and receipt

`SourceImportRequest` has only these init fields:

```text
source_key: SourceBindingKey
expected_generation: int >= 0, bool rejected
import_id: RFC UUIDv7 object
observed_at_utc: exact datetime object, timezone-aware and normalized to UTC
file_sha256: lowercase 64-hex string
snapshot: ValidatedSourceWorkbookSnapshot
event_ids: Mapping[stable UUIDv7, event UUIDv7]
```

Copy and freeze `event_ids`; do not retain a mutable caller mapping. The mapping
must contain exactly one distinct event ID for each INSERT/EDIT/VOID plan item and
none for UNCHANGED items. This comparison occurs after the store recomputes the
plan. IDs are caller-supplied so a retry can use the same identities; this package
does not read a clock or random source. Row IDs, import IDs, event IDs and device
IDs use canonical UUID bytes in SQLite, not locale-dependent text.

`SourceImportStoreView` contains component/schema version, nonnegative generation,
positive next sequence, retained `device_id`, reconstructed immutable
`SourceBindingRegistry`, and optional last committed import/file identifiers. The
registry is excluded from repr. The view exposes no connection and is a snapshot,
not a live transaction or authorization to import.

`SourceImportReceipt` contains disposition, import/source IDs, base and committed
generation, file hash, total/per-sheet Planner counts, event count, and nullable
first/last sequence. COMMITTED is returned only after `COMMIT` succeeds. REPLAYED
returns the original immutable result for the same `import_id` and request digest;
it allocates no sequence and performs no write. Reconstructed equal receipts need
not have Python object identity.

## Schema version 1

Use stdlib `sqlite3`, because this is the Windows agent's local mirror/outbox.
SQLAlchemy/Alembic remain the server/PostgreSQL choice from ADR-0003. Create the
following `STRICT` tables and named indexes/triggers with fixed columns and
foreign keys; `PRAGMA user_version` is exactly 1:

- `source_store_meta`: singleton row, component/schema version, state generation,
  next sequence and 16-byte device ID.
- `source_bindings`: 16-byte source ID, declared fiscal year, ACTIVE/ARCHIVED state,
  nullable archived final-file hash and last committed import/file identifiers.
  A partial unique index permits at most one ACTIVE row; source ID and fiscal year
  are independently unique.
- `source_revisions`: append-only `(stable_id, revision)` history with home sheet,
  ACTIVE/VOIDED lifecycle, source hash, exact nullable WP-14 Raw payload, creating
  import ID, version hash and nullable previous-version hash.
- `source_memberships`: nondeleting `(source_id, stable_id)` membership with the
  committed revision visible to that source plus first/last import IDs. Membership
  remains after VOID and can point to an existing global party revision without
  creating a new revision.
- `source_imports`: append-only import identity/request digest, source key, base and
  committed generation, UTC observation, file hash, aggregate counts and nullable
  sequence range.
- `source_import_sheets`: four append-only rows per committed import containing
  authoritative sheet name/order, snapshot hash, row count and per-action counts.
- `change_events`: append-only local history/outbox with positive contiguous
  sequence number, unique 16-byte event ID, device/import/source/row identities,
  revision, `upsert`/`void`, declared fiscal year, nullable canonical financial
  date, UTC observation, canonical payload, payload hash and previous-version hash.

Use CHECK constraints for fixed versions/enums, UUID/hash lengths, positive
revisions/sequences, lifecycle/hash/Raw nullability, sequence-range consistency
and all nonnegative counts. Use foreign keys from memberships, revisions, imports,
sheets and events. Triggers reject UPDATE or DELETE on revisions, imports, import
sheets and events. Membership may advance its revision/import reference but cannot
be deleted or retargeted. These controls defend product mistakes; a caller able to
drop schema objects or replace the database is outside the authenticity boundary.

Initialization succeeds only when the connection has no transaction, user tables,
views or triggers and `user_version == 0`. It creates every object and inserts the
singleton plus exactly one empty ACTIVE binding in one transaction. A byte-for-byte
equivalent second call may return the current view only if the schema and supplied
device/source identities match; any partial, foreign, unknown-version or mismatched
database fails without modifying it. There is no upgrade, repair or destructive
reset in v1.

## Consistent read and validation

`read_source_import_store` uses one read transaction and returns only after it has
loaded a self-consistent committed generation. Reconstruct `PriorIdentityState`
objects from each membership's referenced revision, then `SourceBindingRecord`,
`SourceBindingRegistry` and `SourceIdentityCatalog` using the accepted constructors.
The returned view retains the registry; the catalog may remain internal.

Validate schema identity and required indexes/triggers, singleton values, source
rules, membership/revision foreign relationships, canonical UUID/hash/date forms,
Raw codec round trips for all ACTIVE revisions referenced by current membership,
version-hash linkage, import counts/ranges and contiguous committed event sequence
from 1 through `next_sequence - 1`. Detect a corrupt or semantically inconsistent
store as INCONSISTENT_STATE; never silently skip a row, choose a newer generation,
coerce malformed data or return a partial registry. A full cryptographic audit of
external tampering and WAL/disk integrity is outside this contract.

The implementation may use indexed queries and validate only immutable history
needed for current heads plus aggregate/chain invariants rather than decode every
old Raw payload on each read. It must still detect mutation of any current referenced
row/event and prove by tests that import cost does not hide a per-row scan of every
archived revision. An explicit deep-audit API is not introduced in this package.

## Commit algorithm and idempotency

1. Validate root types without invoking caller descriptors or object repr. Compute
   a domain-separated SHA-256 request digest row by row from all request fields,
   canonical event-ID pairs and exact WP-14 encoded current rows. The digest must
   distinguish representation-only Raw changes even when `source_hash` is equal;
   do not retain a second workbook payload collection.
2. Evaluate WP-09 Requiredness. Any issue rejects the complete request as
   VALIDATION_FAILED. Evaluate WP-10 fiscal evidence for report/date derivation,
   but do not infer the source key or reject empty/mixed/different observed years;
   O-76 explicitly permits transaction years different from the declared source.
3. Start `BEGIN IMMEDIATE`, load and validate one store generation, and resolve the
   exact requested source. Only the registered ACTIVE key continues. Rebuild the
   WP-13 catalog/projection and rerun WP-04 Planner inside the protected generation.
   No caller-supplied plan, count, revision, prior, membership or sequence is trusted.
4. If `import_id` already exists, compare the fixed request digest first. An exact
   match returns its original receipt as REPLAYED even though its expected generation
   is now old. A mismatch is IDEMPOTENCY_CONFLICT. Otherwise require the stored
   generation to equal `expected_generation`; mismatch is STALE_STATE.
5. Validate `event_ids` against all and only changed plan items. Allocate consecutive
   sequence numbers in canonical Planner order. For INSERT/EDIT/reactivation append
   an ACTIVE revision with exact codec bytes. For VOID append a VOIDED revision with
   null Raw/source hash while retaining every earlier Raw revision. Revisions are
   exactly 1 or prior+1; never overwrite, reuse, skip or synthesize an intermediate.
6. Upsert membership for every current row, including an UNCHANGED globally known
   party first seen in this source. Advance memberships for changed/voided rows and
   retain absent tombstones. Never delete archive membership or rewrite an archived
   source view. The v1 initializer creates one source; independent synthetic SQL
   fixtures may construct a schema-valid archived/active history to exercise these
   already supported read/commit semantics. They are not a product rollover API.
7. Insert one immutable import row, exactly four sheet rows and one event per changed
   revision. Update active source last-import/file fields, advance next sequence by
   event count and advance state generation exactly once. A successful zero-event
   import still records its report and advances generation; it creates no revision
   or event. Only retrying the same import identity is guaranteed to return REPLAYED.
8. Build the complete receipt before committing, execute `COMMIT`, then return it.
   On any pre-commit failure, roll back every state/import/event write. No success is
   exposed before durable commit.

The request digest is an idempotency comparator, not a secret, signature or server
payload hash. Reusing a different `import_id` for the same file is a new observed
import and may create a zero-event report; the future coordinator must persist and
reuse one import identity across retries. A file reverted after intervening commits
must be planned against the new generation and can correctly create new revisions.

## Event payload and version chain

For every changed item, produce strict UTF-8 JSON bytes with no BOM/newline using
`ensure_ascii=False`, compact separators and no numeric JSON tokens except values
represented as canonical decimal strings. The exact top-level array is:

```text
["source-change-event.v1",device_id,event_id,import_id,sequence_text,
 source_id,fiscal_year_text,sheet_name,stable_id,revision_text,operation,
 financial_date_or_null,source_hash_or_null,raw_payload_base64_or_null,
 previous_version_hash_or_null,observed_at_utc]
```

UUIDs are canonical lowercase text inside this wire payload. Integer text uses the
same unpadded ASCII positive grammar. `operation` is `upsert` for INSERT/EDIT and
`void` for VOID. Upsert contains exact standard Base64 with padding of the WP-14
payload and its source hash. Void contains null for both and takes its canonical
financial date from the prior ACTIVE Raw revision. «لیست کسبه» always has null
financial date. Transaction upserts derive the date with the accepted canonical
Jalali parser; no local timezone/current date or source-year inference is used.

`payload_hash = sha256(payload).hexdigest()`. It is also the new revision's
`version_hash`. `previous_version_hash` is null only for revision 1 and otherwise
equals the immediately prior revision's version hash, including across VOID and
reactivation. Recompute and compare these fields while reading current heads. The
payload/hash are local event data, not the future signed/gzipped Batch envelope;
challenge, Ed25519 signature, ACK/delivery state and server acceptance are separate.

## Transactions, failures and public errors

Use these exact reasons and fixed messages:

| Reason | Value | Public message |
|---|---|---|
| `INVALID_INPUT` | `invalid_input` | `Invalid source import store input.` |
| `INVALID_SCHEMA` | `invalid_schema` | `Invalid source import store schema.` |
| `VALIDATION_FAILED` | `validation_failed` | `Source import validation failed.` |
| `SOURCE_NOT_ACTIVE` | `source_not_active` | `Source is not active.` |
| `STALE_STATE` | `stale_state` | `Source import state is stale.` |
| `IDEMPOTENCY_CONFLICT` | `idempotency_conflict` | `Source import identity conflicts.` |
| `INCONSISTENT_STATE` | `inconsistent_state` | `Source import store is inconsistent.` |
| `STORAGE_FAILURE` | `storage_failure` | `Source import storage failed.` |

The error constructor accepts only the exact reason enum; strings and foreign
StrEnums raise fixed TypeError `Invalid source import store reason.`. Typed public
str/repr/args and view/request/receipt reprs exclude path, SQL, UUID, hashes, Raw,
event payload, dates and supplied invalid object repr. Preserve ordinary causes
where useful; cause/traceback data are outside the public sanitization guarantee.

Rollback is mandatory for ordinary exceptions and cancellation. KeyboardInterrupt,
SystemExit and other non-Exception BaseExceptions propagate with original identity
after rollback. If primary and rollback failures both occur, preserve both in
ordered ExceptionGroup/BaseExceptionGroup without deduplicating distinct objects
that share a cause. If `COMMIT` raises or the process loses the response after a
successful commit, the outcome can be ambiguous; retrying the identical request and
`import_id` must resolve it as COMMITTED replay or perform the still-missing commit,
never duplicate revisions/events. Do not claim filesystem durability beyond the
caller's SQLite journal/synchronous/storage guarantees.

## Cost, evidence boundary and consequences

Use indexed lookups. For current membership M, current rows N and changed items C,
one import is at most O(M + N log N + C) time plus accepted hashing/codec costs and
O(M + N) metadata already needed for registry/Planner. Write payloads row by row;
do not accumulate a second list of all encoded Raw/event bytes. A 15,000-row first
import, restart/read and exact replay must remain below the existing Roadmap agent
import target of 350 MiB peak RSS. Record fixture, validation/planning, SQL write,
commit, restart/read and replay times plus database/WAL sizes; no new arbitrary time
limit is added. Existing WP-05/06/12 15-second / 128-MiB limits remain unchanged.

This package proves local transactional semantics on generated databases. It does
not prove real disk failure, filesystem corruption, backup/restore, SQLCipher,
network Sync, server ACK, affected-domain resolution, financial Ledger, full import
report UI, source enrollment, marker writing, archive/new-year transition, opening
balance, runtime wiring, OneDrive behavior or a real workbook. It changes no real
database and creates no committed database artifact.

After schema v1 is used by real data, changing tables, event grammar, version-chain
meaning or UUID representation requires a new migration/ADR and restore/replay
evidence. Before operational use, rollback is a fixed-SHA code/schema removal on a
fresh synthetic database only. Never reset or delete a real database to demonstrate
rollback. G1 stays open until the remaining eligibility, lifecycle, integration and
end-to-end recovery criteria are independently accepted.
