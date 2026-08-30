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
