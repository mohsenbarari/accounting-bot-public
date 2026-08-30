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
