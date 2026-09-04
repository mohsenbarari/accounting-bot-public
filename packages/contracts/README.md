# accounting-contracts

Contracts, DTOs, interfaces and settings models for Agent and Server communication.

## Raw Source Input Contract Registry (`raw_input_contracts`)

The raw input contracts module (`accounting_contracts.raw_input_contracts`) provides the authoritative, frozen, and versioned structural boundaries for the four approved Excel input sheets:
- `خرید-فروش` (Buy / Sell)
- `دریافت-پرداخت` (Receipts / Payments)
- `ورود-خروج` (Inventory Movements)
- `لیست کسبه` (Business Parties)

### Core Principles
- **Source-Boundary Contract Only:** This package defines structure, column roles, and classification rules. It is **not** an XLSX parser, cell evaluation engine, or accounting calculation model.
- **Formula & Cached-Value Exclusion:** Formula cells (including their cached results), derived columns (such as visual row numbers or calculated totals), and unlisted columns are strictly classified as excluded from Raw Immutable input.
- **Value Kinds:** Only `raw_text`, `integer_toman`, `decimal`, and `uuid7` are allowed. Binary `Float` is strictly forbidden.
- **Immutability:** Contract objects and the registry are frozen and self-validating against architectural invariants.

## Canonical Date and Iran Time (`canonical_date`)

The canonical date module (`accounting_contracts.canonical_date`) provides Jalali calendar parsing and timezone utilities:
- **Jalali Date Version:** `jalali-date.v1`
- **Validation & Conversion:** Uses `persiantools.jdatetime.JalaliDate` to validate leap years, month boundaries, and produce queryable Gregorian `datetime.date`.
- **Fiscal Year:** Derives `fiscal_year` key directly from the Jalali year.
- **Timezone:** Uses `Asia/Tehran` via standard library `zoneinfo`. Rejects naive datetimes to avoid ambiguous financial timestamp interpretations.

## Canonical Hashing (`canonical_hashing`)

The canonical hashing module (`accounting_contracts.canonical_hashing`) implements ADR-0006 for deterministic SHA-256 change detection:
- **Source Hash Version:** `source-hash.v1`
- **Snapshot Hash Version:** `sheet-snapshot-hash.v1`
- **Byte Serialization:** Uses compact UTF-8 JSON array serialization (`ensure_ascii=False`, `separators=(',', ':')`, `allow_nan=False`) without reliance on dict key order.
- **Normalization Policy:**
  - `raw_text`: Exact code-point preservation without Unicode normalization, letter substitution, or trimming.
  - `integer_toman`: Strict integer format without group separators or scientific notation.
  - `decimal`: Plain base-10 format without scientific notation or insignificant trailing fractional zeros.
  - `jalali_date`: Canonical `YYYY-MM-DD` representation.
- **Snapshot Sorting:** Pairs are ordered strictly by canonical UUIDv7 binary bytes, ensuring row permutation invariance.

## Deterministic Source Change Planning (`source_change_plan`)

The source change planning module (`accounting_contracts.source_change_plan`) implements ADR-0007 for validating full-source snapshots and producing deterministic change plans:
- **Plan Contract Version:** `source-change-plan.v1`
- **Full Workbook Requirement:** Requires an explicit declaration of all four approved sheets exactly once before any planning can occur. An incomplete or partial snapshot is rejected and cannot authorize void transitions.
- **Prior Identity State:** Tracks all known prior UUIDv7 identities, their immutable home sheets, positive integer revision numbers, and active/voided lifecycle states.
- **Transition Classification:**
  - `insert`: Unknown identity -> planned revision 1.
  - `unchanged`: Active identity with matching hash -> no revision.
  - `edit`: Active identity with changed hash or voided identity reactivated -> planned revision n+1.
  - `void`: Active identity absent from the validated full snapshot -> planned revision n+1.
  - Settled voided identities that remain absent emit no plan item (idempotent no-op).
- **Identity Invariant:** UUIDs are globally unique across all sheets; relocation between sheets raises `IdentityRelocationError`.
- **Deterministic Ordering:** Items are sorted by authoritative sheet registry order and UUID binary bytes with O(N log N) time and O(N) memory complexity.

## Source Requiredness Preflight (`source_requiredness`)

The source requiredness module (`accounting_contracts.source_requiredness`) implements ADR-0012 for validating essential required raw input fields across complete four-sheet source snapshots:
- **Preflight Version:** `source-requiredness.v1`
- **Pure Preflight Boundary:** Evaluates requiredness over an existing immutable `ValidatedSourceWorkbookSnapshot`. It does not parse XLSX files, resolve entities, execute accounting logic, or commit imports.
- **Exact Presence Rules:**
  - `None` for a required field emits `SourceRequirednessIssueReason.MISSING_VALUE`.
  - Required text whose `str.strip()` is empty emits `SourceRequirednessIssueReason.BLANK_TEXT`.
  - Numeric zero (`0`, `"0"`, `Decimal("0")`), negative values, and non-empty text count as present.
  - Optional fields (notes, discount, auxiliary codes/flags, phone numbers, purity) do not emit issues when null or blank.
- **Raw Preservation & Masked Repr:** Original raw mappings, types, and source hashes are preserved without mutation. The report excludes the snapshot from `repr` and issue objects store no raw cell values.
- **Deterministic Issue Ordering:** Issues are aggregated in registry sheet order, UUID bytes, and registry raw-column order.

## Source Fiscal Evidence (`source_fiscal_evidence`)

`source-fiscal-evidence.v1` implements ADR-0013 over an existing complete
`ValidatedSourceWorkbookSnapshot`:

```python
from accounting_contracts import evaluate_source_fiscal_evidence

report = evaluate_source_fiscal_evidence(snapshot)
assert report.snapshot is snapshot
observed_years = report.observed_years
undated_rows = report.undated_row_count
```

`SourceFiscalEvidenceReport(snapshot)` computes the same immutable report. Each
transaction row contributes sheet/UUID/year metadata, with `None` for a missing
date. Rows follow registry sheet order and UUID bytes; positive year counts are
sorted by year. Business-party rows are counted separately and never supply dates.
The snapshot and all raw values/hashes remain intact; ordinary report repr excludes
raw snapshot values. Invalid public input/metadata raises the fixed-message
`SourceFiscalEvidenceInputError` (signature misuse can raise `TypeError`).

Empty, undated and mixed-year snapshots all produce observations. The report
selects no operational year and grants no import, deletion or archive permission.
Requiredness, source binding, fiscal/archive eligibility, opening balances and
persistence require their own checks. Evaluation uses the existing Jalali parser
without filesystem, network, clock or random access.

## Annual Source Binding (`source_binding`)

`source-binding.v1` implements ADR-0014 over trusted annual metadata:

```python
from accounting_contracts import SourceBindingDisposition, resolve_source_binding

resolution = resolve_source_binding(key, registry)
if resolution.disposition is SourceBindingDisposition.ACTIVE:
    selected_prior = resolution.prior_registry
    # Continue the separately validated import pipeline for this source only.
```

`SourceBindingKey(source_id, fiscal_year)` requires a UUIDv7 object and an exact
Jalali year int. `SourceBindingRecord(key, state, prior_registry, final_file_sha256)`
retains the supplied WP-04 prior object. ACTIVE requires no final hash; ARCHIVED
requires its final file's lowercase SHA-256 metadata. `SourceBindingRegistry(records)`
copies the iterable into a sorted immutable tuple with unique IDs/years and at
most one active record. Lookup uses a private immutable source-ID index and does
not traverse prior rows. Empty and archive-only registries are valid.

Direct `SourceBindingResolution(key, registry)` computes the same routing result.
An exact active match selects the identical prior object. Archives and unknown
IDs select None; an unknown ID cannot borrow another source by year. A known ID
with a different year raises `SourceBindingInputError`. Public errors use fixed
messages and repr excludes nested prior state. Raw date observations, empty
snapshots and deletion volume are not resolver inputs.

These objects do not attest workbook identity or a committed import. Physical
marker format/read/write, binding marker and Raw to the same stable acquisition,
enrollment, global identity/revision projection and durable rollover remain
separate prerequisites. Shared permanent party UUIDs in historical views are
retained without merging or resetting revisions. The resolver performs no I/O,
calls no Planner or financial checks, and ACTIVE does not authorize a commit.

## Source Identity Projection (`source_identity_projection`)

`source-identity-projection.v1` implements ADR-0016 for comparison across annual
memberships while preserving permanent UUID/home-sheet/revision history:

```python
from accounting_contracts import (
    SourceIdentityCatalog,
    plan_source_changes,
    project_source_prior,
)

# registry, key and snapshot here are synthetic, already validated inputs.
catalog = SourceIdentityCatalog(registry)
prior = project_source_prior(key, snapshot, catalog)
proposal = plan_source_changes(snapshot, prior)
```

The immutable catalog retains the source registry and derives global heads and
transaction owners. The same UUID/revision must have consistent state across
memberships; the active source cannot have a stale prior. A transaction UUID
belongs permanently to one source. Parties may share their permanent UUID across
annual views, which retain their original revisions and archive hashes.

Projection uses the exact WP-11 active-source resolver. It keeps every committed
member/tombstone of that source and borrows a global prior only for a known party
present now but not yet a member. Absent archive-only UUIDs cannot become false
VOID; an unchanged known party cannot become a new revision-1 INSERT. Archive or
unknown keys, moved home sheets, foreign transactions and inconsistent input
fail with `SourceIdentityProjectionError` and a `SourceIdentityProjectionReason`.
Public messages are fixed; diagnostic causes can preserve underlying failures.

Catalog creation visits supplied membership once plus an active-prior check,
with O(S + M + U log U) time, O(S + M) peak validation metadata and O(S + U)
retained indexes. Projection uses the indexes and current/active rows, at most
O(A + N log N + K log K) time and O(A + N) additional metadata, without rescanning
archive priors. No row/financial-volume threshold is added.

These are pure metadata operations. Successful construction cannot prove that
the caller supplied every committed source or associated each prior truthfully.
No file read, enrollment, automatic Planner call, mutation or commit occurs.
Future durable import must validate one committed generation and atomically
record Raw/revision/membership/outbox changes, including **new party membership
after UNCHANGED**. Otherwise its later deletion could be missed. Archive views
remain fixed; crash recovery, real-source enrollment and rollover require their
own contracts and evidence. WP-04, WP-11 and runtime behavior remain unchanged.

## Lossless Raw Row Codec (`source_raw_codec`)

`source-raw-codec.v1` implements ADR-0017 for a single validated Raw row:

```python
from accounting_contracts import decode_source_raw_row, encode_source_raw_row

# row is a synthetic, already validated WP-04 row.
payload = encode_source_raw_row(row)
restored = decode_source_raw_row(payload)
```

The deterministic UTF-8 JSON array includes versions, sheet, canonical UUID,
source hash and all whitelist fields in registry order. Null, text, int and
Decimal have distinct tags. Text is preserved exactly; Decimal retains its sign,
coefficient and exponent, including negative zero and trailing zeros. Encoder
input scalars must be exact base types; scalar subclasses are rejected without
changing upstream validators. Valid row subclasses are accepted. The decoder
requires exact bytes, strict grammar, a matching WP-03 source hash and byte-identical
canonical re-encoding. It returns a new immutable WP-04 row. No I/O, arithmetic
rounding, decimal context mutation, evaluation or Planner call occurs.

Both entry points raise fixed-message `SourceRawCodecError` with INVALID_INPUT
or INVALID_PAYLOAD. Ordinary failures retain their causes, which may contain
diagnostic data; cancellation propagates unchanged. Do not publish raw payloads
or arbitrary chained tracebacks as sanitized diagnostics.

Codec bytes preserve representations that canonical source hashing deliberately
equates. Different bytes with the same source hash still mean UNCHANGED to WP-04;
they do not create revisions/events or overwrite the Raw of a committed revision.
New-source membership after UNCHANGED remains a separate ADR-0016 obligation.
The embedded semantic hash does not authenticate byte spelling, UUID/source
association or durable history. This is a Raw component, not the complete Sync
envelope or the definition of its payload_hash. Storage must separately enforce
byte integrity, history, generation freshness and atomic Raw/membership/outbox.

Work is row by row; temporary memory depends on that row's representation and
existing canonical hashing costs. No new size/time cap or whole-workbook payload
buffer is introduced. Parser/runtime resource limits remain applicable, and this
codec does not provide transport admission controls. Future changes after durable
use require a new format version and explicit migration/replay support.
