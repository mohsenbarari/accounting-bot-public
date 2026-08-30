# WP-03: Canonical source values and deterministic hashing

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 canonical-value/change-detection foundation only; this Work Package cannot close G1
- Status: Accepted and merged
- Issued by: Codex Project Manager
- Issued on: 2026-08-30
- Accepted by: Codex Project Manager
- Accepted on: 2026-08-30
- Review evidence: PR #13 and final CI Run `33329429229`
- Merge commit: `6be9fef5d2b9922c51f2beab6b9c178b4795a243`
- Required skill: `accounting-bot-implementer`
- Branch: `antigravity/phase-01-canonical-source-hashing`
- Baseline: latest clean `origin/main` containing this Work Package and ADR-0006
- Handoff path: `handoffs/phase-01/wp-03-canonical-source-hashing/`

## Objective

Implement the smallest shared, immutable and versioned canonicalization boundary that converts WP-02 literal source values into deterministic bytes and calculates `source_hash` and `sheet_snapshot_hash` identically on Windows and Linux. Add the approved Jalali-date representation and Iran-time conversion foundation without reading a workbook or applying accounting rules.

This package must make mapping order and row sort irrelevant while making a canonical source edit, insert or delete observable. It must not implement `ledger_hash`, XLSX extraction, persistence, revision/void behavior or financial calculations.

## Roadmap and ADR traceability

- Roadmap sections 4.2 and 5.2 plus O-03: versioned SHA-256, row-number independence and sorted UUID/hash sheet snapshots.
- Roadmap sections 5.4/5.5 and O-25/O-26: literal-input-only boundary, Integer Toman, exact Decimal and no Float.
- Roadmap section 16 and O-42: raw Jalali date preservation, canonical query date, fiscal-year key, `persiantools`, `zoneinfo` and `Asia/Tehran`.
- O-68 and [ADR-0006](../../adr/ADR-0006-canonical-source-hashing.md): exact v1 byte contract, normalization rules, versioning and deferred `ledger_hash`.
- [WP-02](WP-02-raw-input-contracts.md): authoritative sheet names, raw field order, stable IDs and formula/derived exclusions.
- O-46 through O-51: bounded implementation, protected assets, handoff and independent Codex review.

## Authoritative v1 contract

The implementation must expose stable public constants for:

- `jalali-date.v1`
- `source-hash.v1`
- `sheet-snapshot-hash.v1`

Canonical bytes and payloads must implement ADR-0006 exactly:

```text
["source-hash.v1","raw-source-contract.v1",sheet_name,[
  [field_name,type_tag,canonical_value], ...
]]

["sheet-snapshot-hash.v1","raw-source-contract.v1",sheet_name,[
  [canonical_uuid7,source_hash], ...
]]
```

Use UTF-8 without BOM/final newline and compact JSON equivalent to `ensure_ascii=False`, `separators=(",", ":")`, `allow_nan=False`. Do not rely on Dictionary insertion order. `source_hash` field order is exactly each WP-02 contract's `raw_columns` order; snapshot pairs are sorted by canonical UUID bytes. SHA-256 output is exactly 64 lowercase hexadecimal characters.

### Source-row rules

1. Accept an exact field-name mapping for one approved sheet. Missing or extra fields, an unknown sheet, a technical ID, a derived/unlisted field or an unsupported value type must fail with a typed domain-specific exception.
2. `record_id`/`party_id`, source row/cell, headers, formula/cached values, derived/display values and activity metadata never enter `source_hash`.
3. `None` is canonical JSON `null` and differs from empty text.
4. Raw text accepts only `str | None`; preserve code points, leading/trailing whitespace and empty text exactly. Do not trim, apply Unicode normalization or alias Persian/Arabic letters.
5. Integer Toman accepts nonBoolean `int`, an integral finite `Decimal`, or strict integer text. Canonical value is a base-10 string with no plus sign or leading zero; negative zero becomes `0`.
6. Decimal accepts finite `Decimal`, nonBoolean `int`, or strict fixed-point text. Canonical value is plain base-10 text without exponent; remove insignificant fractional trailing zeros and canonicalize any zero to `0` while preserving all significant precision.
7. Numeric text may normalize outer whitespace and ASCII/Persian/Arabic digits. Reject Float, Boolean, NaN, Infinity, exponent notation, thousands/group separators, internal whitespace and ambiguous decimal punctuation.
8. The `date_raw` fields in the three transactional sheets use the `jalali_date` tag. Accept only text/None, ASCII/Persian/Arabic digits, outer whitespace, a four-digit year, one/two-digit month and day, and `/` or `-`. Canonical non-null value is `YYYY-MM-DD`. Every grammar-matching value is explicitly interpreted as Jalali; do not auto-detect Gregorian dates or infer a date from an Excel serial, `datetime` or Save/import timestamp.
9. Equivalent accepted numeric/date forms have identical canonical values; raw-text differences remain different.
10. Row requiredness and blank-row detection remain out of scope. `None` can be canonicalized so a later validator can apply business/row rules without changing this byte contract.

### Jalali date and Iran-time rules

Add an immutable typed result for a non-null parsed Jalali date containing at least:

- original raw text unchanged;
- canonical Jalali `YYYY-MM-DD`;
- Gregorian `date` for query/storage;
- Jalali year, month and day;
- `fiscal_year`, equal to the Jalali year;
- `jalali-date.v1` calculation version.

Use the locked `persiantools` implementation for calendar validation/conversion and stdlib `zoneinfo.ZoneInfo("Asia/Tehran")` for aware timestamp conversion. Add one pure helper that converts an aware instant to Iran time and rejects naive datetimes. Financial-date parsing must not accept a timestamp, ensuring Save/import time around midnight cannot change the Excel financial day.

`accounting-contracts` may add the approved direct dependency `persiantools>=6.2.0,<6.3.0`; update `uv.lock` through `uv`, not by hand. No other runtime dependency is authorized.

### Snapshot rules

1. Accept only an approved sheet plus UUIDv7/source-hash pairs.
2. Canonicalize UUIDs to lowercase hyphenated RFC form and require RFC variant/version 7; do not generate IDs.
3. Reject blank/malformed/wrong-version/duplicate IDs and any source hash not matching `[0-9a-f]{64}`.
4. Pair order and workbook row sort must not change bytes/hash. An insert, delete or changed source hash must change the result.
5. An empty pair collection has deterministic canonical bytes and digest.
6. Do not compare a snapshot with storage or decide whether a missing UUID is a valid void; later full-snapshot validation/import owns that behavior.

## Preconditions and environment

- Work only in `/srv/accounting-bot/workspace` after confirming a clean `main` exactly equal to `origin/main`, then create the exact branch above.
- Invoke and follow `accounting-bot-implementer` before changing files.
- Use committed Python 3.13, `uv 0.12.7` and the workspace lockfile. Confirm the selected `persiantools` resolution in the lockfile and Handoff.
- Do not modify `ROADMAP.md`, ADR-0006, accepted Work Packages/ADRs or existing WP-02 mappings.
- Do not open, copy, inspect or modify any real/reference workbook or SQLite/database artifact. All tests and Golden Vectors must be synthetic.

## In scope

1. Add focused canonical-date/time and canonical-hashing modules under `packages/contracts/src/accounting_contracts/` with typed exceptions, immutable results and documented public APIs.
2. Reuse `RAW_CONTRACT_REGISTRY`; do not duplicate sheet/field definitions or order in a second registry.
3. Add the single approved direct `persiantools` dependency to `accounting-contracts` and update the workspace lockfile reproducibly.
4. Document canonicalization, version boundaries, deliberate normalization/non-normalization and the fact that the package is not an XLSX parser or Ledger engine.
5. Add deterministic unit, Golden Vector and Hypothesis property tests using only synthetic Persian labels and fictitious values.
6. Produce and validate the required Handoff, Acceptance Matrix and test-results artifacts.

## Out of scope

- Opening/reading/copying/modifying `.xlsx`/`.xlsm`, Excel COM, XML parsing, Save monitoring, snapshots on disk or OneDrive behavior.
- UUID generation, insertion, repair or writeback; workbook headers/filter ranges; use of a reference workbook even read-only.
- Row activity/required-field/business validation, alias/item resolution, RS pairing or opening-balance rules.
- `ledger_hash`, Financial Events, calculations, rounding weight 750, balances, reports, PDF or Telegram behavior.
- SQLite/PostgreSQL schema or migration, Raw Revision, void detection, import transactions, Outbox, API or network sync.
- Normalizing raw text, phone numbers, party/item names or accounting codes.
- Dependency or architecture changes other than the explicitly approved `persiantools` addition and lockfile update.
- Real data, phone/Telegram identity, credentials, host addresses, production systems, edits to `main`, push, PR creation or merge.

## Required acceptance evidence

Record every command and exit code in `test-results.txt`.

1. Literal Golden Vectors assert exact canonical UTF-8 bytes and literal SHA-256 digest for at least one synthetic row from each approved sheet and the empty snapshot. Do not derive the expected digest inside the test from the implementation under test.
2. Tests assert public version constants and the exact top-level array layout, type tags and WP-02 field order.
3. Tests prove input Mapping order has no effect, while every canonical raw field affects `source_hash`; ID/row/formula/derived/unlisted keys are rejected rather than silently included.
4. Tests prove Null differs from empty text; raw-text whitespace/Unicode differences remain observable; equivalent accepted Persian/Arabic/ASCII numeric and date representations canonicalize as specified.
5. Tests cover positive/negative/zero Integer and Decimal values, very large/exact Decimal values and rejection of Float, Boolean, non-finite, exponent, grouping and malformed inputs. No binary Float operation may appear in the implementation.
6. Calendar tests cover Nowruz, a leap and non-leap Esfand 30, every month-end family, round-trip to Gregorian, invalid dates, accepted digit/separator variants, raw preservation and fiscal-year extraction. Tests must exercise `persiantools`, not a handwritten conversion algorithm.
7. Timezone tests use aware UTC instants on both sides of an Iran-midnight boundary, assert `Asia/Tehran`, reject naive datetime and prove the same Excel date stays the same regardless of Save/import instant.
8. Snapshot tests prove pair-permutation/sort invariance; insert/delete/edit sensitivity; deterministic empty snapshot; canonical UUID representation; and rejection of duplicate, malformed, non-v7 IDs and malformed/uppercase hashes.
9. Hypothesis tests cover deterministic repeated serialization and arbitrary pair permutations without using real accounting values.
10. Existing WP-02 tests and all prior tests remain green; `uv lock --check`, frozen all-workspace/all-group sync, Ruff format/lint, strict mypy and pytest pass on Python 3.13.
11. `git diff --check` passes. The final diff is bounded to contract modules/exports, tests, contracts documentation, approved dependency/lock metadata and WP-03 Handoff artifacts.
12. A repository scan finds no workbook/database/PDF, real identity/phone, host/IP, secret, environment artifact or captured real row value.
13. The Handoff validator exits zero for `handoffs/phase-01/wp-03-canonical-source-hashing/`.
14. After Codex independently reviews and pushes the branch, every configured GitHub Actions job must pass before acceptance or merge.

## Stop conditions

Stop and report to Codex without expanding scope if:

- baseline is dirty/divergent or contains an unexpected protected asset;
- ADR-0006 conflicts with WP-02 types/order or a required canonical rule is ambiguous;
- implementation appears to require a real workbook/database, actual cell value or production access;
- an Excel Float must be accepted directly, a raw text must be normalized, or a formula/cached value must enter a hash to pass tests;
- a `ledger_hash`, database model, XLSX parser, UUID writer/generator or business validator seems necessary;
- `persiantools` cannot satisfy required boundary cases or another dependency/handwritten calendar algorithm is proposed;
- any action would alter Excel, `ROADMAP.md`, an accepted ADR/Work Package, `main`, GitHub state or infrastructure.

## Handoff contract

Follow the repository skill exactly. Commit the bounded implementation and complete:

- `handoff.md`
- `acceptance-matrix.md`
- `test-results.txt`

Run the skill validator, then report branch/baseline, commits, changed files, dependency/lock change, public API, Golden Vectors, all commands and exit codes, Handoff path, remaining risks and pending decisions. Stop for Codex review. Do not push, open a PR, merge, deploy or continue into another Work Package.
