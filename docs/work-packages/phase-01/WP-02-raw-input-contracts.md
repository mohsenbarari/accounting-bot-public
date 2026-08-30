# WP-02: Raw input contract registry

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 source-boundary foundation only; this Work Package cannot close G1
- Status: Issued
- Issued by: Codex Project Manager
- Issued on: 2026-08-30
- Required skill: `accounting-bot-implementer`
- Branch: `antigravity/phase-01-raw-input-contracts`
- Baseline: latest clean `origin/main` containing this Work Package
- Handoff path: `handoffs/phase-01/wp-02-raw-input-contracts/`

## Objective

Create the smallest versioned, immutable and fully tested registry that defines the four approved Excel input-sheet boundaries. The registry must make it impossible for a formula, cached formula result, derived column or unrelated workbook column to be treated as a raw accounting input by later ingestion code.

This package defines structure and classification only. It does not open a workbook, parse XLSX XML, generate or write UUIDs, normalize dates or numbers, calculate hashes, persist rows or apply accounting rules.

## Roadmap traceability

- Roadmap sections 5.2 and 5.4: whitelist-only extraction, raw metadata and formula/cached-value exclusion.
- Roadmap section 5.1 and O-03: stable UUIDv7 locations `Z/P/P/D` and row-number independence.
- Roadmap section 5.5 and O-25: integer Toman, Decimal quantities/purity and no Float.
- Roadmap section 5.5 and O-26: Raw Immutable is limited to literal source inputs; derived values are reproducible downstream.
- Phase 1 and G1: define the four-sheet raw whitelist before canonicalization, revision storage and atomic import.
- O-46 through O-51: bounded implementation authority, protected assets, handoff and independent review.

## Authoritative v1 sheet contract

The implementation must expose these contracts as data, not as duplicated conditionals. Internal field names below are normative for this package.

| Sheet | Stable ID | Literal raw input columns | Activity columns | Known derived/display columns excluded from Raw |
|---|---|---|---|---|
| `خرید-فروش` | `Z` → `record_id` | `B` → `date_raw`; `C` → `party_name_raw`; `D` → `transaction_type_raw`; `E` → `item_name_raw`; `F` → `quantity_raw`; `G` → `unit_price_toman_raw`; `H` → `discount_toman_raw`; `J` → `notes_raw` | `C,D,E,F,G,H,J` | `A` row number; `I` total amount |
| `دریافت-پرداخت` | `P` → `record_id` | `B` → `date_raw`; `C` → `party_name_raw`; `D` → `entry_type_raw`; `E` → `amount_toman_raw`; `F` → `notes_raw`; `G` → `account_code_raw`; `H` → `customer_flag_raw` | `C,D,E,F,G,H` | `A` row number |
| `ورود-خروج` | `P` → `record_id` | `B` → `date_raw`; `C` → `party_name_raw`; `D` → `movement_type_raw`; `E` → `item_name_raw`; `F` → `quantity_raw`; `G` → `purity_raw`; `I` → `notes_raw`; `K` → `customer_flag_raw` | `C,D,E,F,G,I,K` | `A` row number; `H` weight 750; `J` invoice number |
| `لیست کسبه` | `D` → `party_id` | `B` → `party_name_raw`; `C` → `phone_number_raw` | `B,C` | `A` row number |

### Header and address rules

1. The contract version is the stable public constant `raw-source-contract.v1`.
2. Required Persian headers are:
   - `خرید-فروش`: `B تاریخ`, `C نام`, `D شرح`, `E کالا`, `F مقدار`, `G فی`, `H تخفیف`, `J توضیحات`.
   - `دریافت-پرداخت`: `B تاریخ`, `C نام`, `D شرح`, `E مبلغ`, `F توضیحات`.
   - `ورود-خروج`: `B تاریخ`, `C نام`, `D شرح`, `E کالا`, `F مقدار`, `G عیار`, `I توضیحات`.
   - `لیست کسبه`: `B نام`, `C شماره تماس`.
3. Auxiliary columns `دریافت-پرداخت!G:H` and `ورود-خروج!K` currently have no required header; their exact column addresses are authoritative.
4. The operational technical headers are `record_id` in `Z/P/P` and `party_id` in `D`. This package records those reserved locations but does not require, generate, validate or write actual IDs.
5. Unlisted columns are outside the raw boundary. Adding a workbook column does not implicitly expand the whitelist.
6. A source row number/cell address may later be retained as audit metadata, but it is not a raw financial field or stable identity.

### Value-kind metadata

Each whitelisted field must declare one of these expected downstream kinds without coercing a value in this package:

- `raw_text`: dates, names, types, item names, notes, account/customer flags and phone numbers.
- `integer_toman`: unit price, discount and receipt/payment amount.
- `decimal`: quantity and purity.
- `uuid7`: the reserved technical ID fields.

There must be no Float value kind. Parsing, validation, requiredness by transaction type and canonical formatting belong to later Work Packages.

### Formula and cached-value policy

- A literal cell in a whitelisted input column is classified as a raw-input candidate.
- A formula cell is never a raw-input candidate, even when it is in a whitelisted column and has a cached value.
- A stable-ID cell is classified separately from financial input.
- A known derived column and any unlisted column are excluded.
- This package may report classifications or contract violations, but must not decide whether a row with a formula in a normally whitelisted column is semantically valid. That decision requires the later row-validation and canonicalization package.

## Preconditions and environment

- Work only in `/srv/accounting-bot/workspace` after confirming a clean `main` equal to `origin/main` and then create the exact branch named above.
- Use the committed uv-managed Python 3.13 and lockfile from WP-01.
- Do not modify `ROADMAP.md`, accepted ADRs, this Work Package or WP-01 artifacts.
- Do not access any real/reference workbook, SQLite artifact or real accounting value. The structural contract above is sufficient and authoritative for this package.

## In scope

1. Add a focused module under `packages/contracts/src/accounting_contracts/` containing:
   - typed enums or literals for sheet, column role and expected value kind;
   - frozen contract objects for columns and sheets;
   - one immutable registry containing exactly the four contracts above;
   - lookup APIs that fail explicitly for unknown sheet names;
   - a pure classification API that distinguishes literal raw input, technical ID, formula/cached value, known derived column and unlisted column without reading cell values.
2. Add self-validation for registry invariants, including unique sheet names, unique source columns and internal field names per sheet, exactly one stable-ID column per sheet, no overlap among raw/ID/derived roles, valid Excel column addresses and non-empty activity-column subsets of raw inputs.
3. Document the package API and the fact that it is a source-boundary contract, not a parser or accounting model.
4. Add deterministic unit and property-based tests using only synthetic metadata. Tests must cover Persian sheet/header text without embedding any real person, phone, transaction or host value.
5. Produce and validate the required Handoff, Acceptance Matrix and test-results artifacts.

## Out of scope

- Opening, reading, copying or modifying any `.xlsx`/`.xlsm` workbook, including a copy of the reference file.
- Excel COM, `watchdog`, XLSX/XML parsing, Save monitoring, snapshots or OneDrive behavior.
- UUIDv7 generation, validation, migration or insertion into Excel.
- Row-value parsing, required-field/business validation, Persian-date canonicalization, fiscal-year logic, Decimal normalization or SHA-256 hashes.
- SQLite/PostgreSQL schemas, migrations, revision/void logic, import transactions, Outbox or network sync.
- Financial Events, C/D/RS/H/HA/HS, calculations, balances, reports, PDF or Telegram behavior.
- New dependencies, lockfile changes, CI topology changes, deployment or infrastructure mutation.
- Real data, credentials, host addresses, production systems, edits to `main`, push, PR creation or merge.

## Required acceptance evidence

Record every command and exit code in `test-results.txt`.

1. Tests assert the exact four sheet names, stable-ID locations, raw mappings, activity columns, required headers and excluded derived columns specified above.
2. Tests prove every unlisted column remains excluded and an unknown sheet lookup fails explicitly.
3. Tests prove a formula cell and its cached result never classify as raw input, including in columns `F/G/H` of `خرید-فروش`, `E` of `دریافت-پرداخت` and `F` of `ورود-خروج`.
4. Tests prove the registry cannot be mutated through its public API and that duplicate/overlapping/invalid synthetic definitions are rejected.
5. Tests prove no Float kind exists and the expected `integer_toman`, `decimal`, `raw_text` and `uuid7` assignments are exact.
6. All prior tests remain green; `uv lock --check`, frozen sync, Ruff format/lint, strict mypy and pytest all pass on Python 3.13.
7. `git diff --check` passes and the final diff contains only the bounded contract module, tests, package documentation and WP-02 Handoff artifacts.
8. A repository scan finds no workbook/database/PDF, real phone number, real identity, host address, secret, generated environment artifact or captured real row value.
9. The Handoff validator exits zero for `handoffs/phase-01/wp-02-raw-input-contracts/`.
10. After Codex independently reviews and pushes the branch, all configured GitHub Actions jobs must pass before this Work Package can be accepted or merged.

## Stop conditions

Stop and report to Codex without expanding scope if:

- the baseline is dirty, divergent or contains an unexpected protected asset;
- implementation appears to require reading a real workbook/database or inspecting real row values;
- the specified addresses or headers are internally contradictory;
- a formula/cached value must be treated as raw data to make a test pass;
- implementation requires a new dependency, canonicalization rule, database design or Roadmap/ADR change;
- any requested change would write a UUID, header or other value into Excel.

## Handoff contract

Follow the repository skill exactly. Commit the bounded implementation and complete:

- `handoff.md`
- `acceptance-matrix.md`
- `test-results.txt`

Run the skill validator, then report branch, commit, changed files, tests, handoff path, remaining risks and pending decisions. Stop for Codex review. Do not push, open a PR, merge, deploy or continue into another Work Package.
