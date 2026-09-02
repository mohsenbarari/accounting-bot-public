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
