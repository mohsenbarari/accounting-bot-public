# Handoff

## Identity

- Phase: 1 — source and data-model foundation (Phase 1 authorized, Gate G1 is open / in-progress)
- Work Package: WP-03: Canonical source values and deterministic hashing
- Branch/worktree: antigravity/phase-01-canonical-source-hashing
- Commit(s):
  - `4a31183` (feat(contracts): implement canonical Jalali date parsing and deterministic hashing)
- Implementer: Google Antigravity
- Reviewer: Codex

## Scope

### Requested outcome

Implement the smallest shared, immutable and versioned canonicalization boundary that converts WP-02 literal source values into deterministic bytes and calculates `source_hash` and `sheet_snapshot_hash` identically on Windows and Linux. Add the approved Jalali-date representation and Iran-time conversion foundation without reading a workbook or applying accounting rules.

### In scope

- Direct dependency addition of `persiantools>=6.2.0,<6.3.0` to `packages/contracts/pyproject.toml` and lockfile update via `uv lock`.
- Public module `accounting_contracts.canonical_date` containing:
  - Version constant `JALALI_DATE_VERSION = "jalali-date.v1"`.
  - Immutable dataclass `CanonicalJalaliDate` holding `raw_text`, canonical `YYYY-MM-DD`, queryable Gregorian `date`, year, month, day, `fiscal_year` equal to Jalali year, and calculation version.
  - Jalali calendar validation via `persiantools.jdatetime.JalaliDate` for leap years (1403/1399 vs non-leap 1402 Esfand 30) and month-end families (1-6: 31, 7-11: 30).
  - Strict matching separator regex (`/` or `-`) and ASCII digit matching `[0-9]`.
  - Timezone conversion `to_iran_time` with `zoneinfo.ZoneInfo("Asia/Tehran")`, rejecting naive datetimes.
- Public module `accounting_contracts.canonical_hashing` containing:
  - Version constants `SOURCE_HASH_VERSION = "source-hash.v1"` and `SHEET_SNAPSHOT_HASH_VERSION = "sheet-snapshot-hash.v1"`.
  - TypeTag validation prior to None evaluation, raising typed `CanonicalValueError`.
  - Normalization policies for `raw_text`, `jalali_date`, `integer_toman`, `decimal`.
  - Strict rejection of Float, Boolean, NaN, Infinity, exponent notation, thousands separators, and ambiguous punctuation via `[0-9]` regexes.
  - Inferred `date_raw` field classification without hardcoding transactional sheet lists.
  - `compute_source_hash` using compact UTF-8 JSON array serialization and SHA-256 digests.
  - `compute_sheet_snapshot_hash` using binary UUIDv7 sorting and SHA-256 digests.
- Public API exports in `accounting_contracts/__init__.py`.
- Documentation in `packages/contracts/README.md`.
- Deterministic, Golden Vector, and Hypothesis property tests in `tests/test_canonical_date.py` and `tests/test_canonical_hashing.py`.
- Handoff artifacts and validation.

### Out of scope

- Opening/reading/copying/modifying `.xlsx`/`.xlsm`, Excel COM, XML parsing, Save monitoring, snapshots on disk or OneDrive behavior.
- UUID generation, insertion, repair or writeback; workbook headers/filter ranges; use of a reference workbook even read-only.
- Row activity/required-field/business validation, alias/item resolution, RS pairing or opening-balance rules.
- `ledger_hash`, Financial Events, calculations, rounding weight 750, balances, reports, PDF or Telegram behavior.
- SQLite/PostgreSQL schema or migration, Raw Revision, void detection, import transactions, Outbox, API or network sync.
- Normalizing raw text, phone numbers, party/item names or accounting codes.
- Dependency or architecture changes other than the explicitly approved `persiantools` addition and lockfile update.
- Real data, phone/Telegram identity, credentials, host addresses, production systems, edits to `main`, push, PR creation or merge.

## Roadmap traceability

| Roadmap section / O-item | Approved status | Implemented behavior |
|---|---|---|
| Sections 4.2, 5.2 / O-03 | ✅ Approved | Versioned SHA-256 hashing, row-number independence and sorted UUID/hash sheet snapshots |
| Section 5.4 / O-25 | 🧪 Partial (Phase 1 Canonical) | Literal-input-only boundary, Integer Toman, exact Decimal and no Float canonical representation; full accounting validation deferred |
| Section 16 / O-42 | ✅ Approved | Raw Jalali date preservation, canonical query date, fiscal-year key, persiantools, zoneinfo and Asia/Tehran |
| O-68, ADR-0006 | ✅ Approved | Exact v1 byte contract, normalization rules, versioning and deferred ledger_hash |
| WP-02 | ✅ Approved | Reuse authoritative sheet names, raw field order, stable IDs and formula/derived exclusions |
| O-46 to O-51 | ✅ Approved | Bounded implementation, protected assets, handoff and independent Codex review |

## Changed files

| File | Change | Reason |
|---|---|---|
| `packages/contracts/pyproject.toml` | Modified | Add direct dependency `persiantools>=6.2.0,<6.3.0` |
| `uv.lock` | Modified | Update lockfile via uv to lock `persiantools 6.2.0` |
| `packages/contracts/src/accounting_contracts/canonical_date.py` | Created | Canonical Jalali date parsing, calendar validation, fiscal year extraction, and Asia/Tehran conversion |
| `packages/contracts/src/accounting_contracts/canonical_hashing.py` | Created | Deterministic source_hash and sheet_snapshot_hash serialization, SHA-256, tag validation, and value normalizations |
| `packages/contracts/src/accounting_contracts/__init__.py` | Modified | Export canonical date and hashing public APIs |
| `packages/contracts/README.md` | Modified | Document canonical date and canonical hashing contracts and boundaries |
| `tests/test_canonical_date.py` | Created | Deterministic tests for Jalali date parsing, leap year, month boundaries, digit variants, and timezone conversion |
| `tests/test_canonical_hashing.py` | Created | Literal Golden Vector, rejection, permutation invariance, raw field mutation, and Hypothesis property tests |
| `handoffs/phase-01/wp-03-canonical-source-hashing/*` | Created | WP-03 Handoff, Acceptance Matrix, and Test Results |

## Schema and migrations

- Schema impact: none
- Migration files: none
- Backward compatibility: fully backward-compatible contract addition
- Data migration/real data used: none

## Commands and exit codes

| Command | Exit code | Purpose |
|---|---:|---|
| `uv --version` | 0 | Verify uv version 0.12.7 |
| `uv lock --check` | 0 | Verify lockfile consistency |
| `uv sync --frozen --all-packages --all-groups` | 0 | Verify frozen dependencies installation |
| `uv run ruff format --check .` | 0 | Verify formatting compliance |
| `uv run ruff check .` | 0 | Verify linting rules compliance |
| `uv run mypy .` | 0 | Verify strict static typing |
| `uv run pytest -v` | 0 | Execute all 64 unit and property tests |
| `git diff --check origin/main...HEAD` | 0 | Verify clean diff with zero whitespace or line-ending defects against origin/main |
| `! git ls-files \| grep -E '\.(xlsx\|xls\|xlsm\|sqlite\|sqlite3\|db\|pdf\|key\|pem\|env)$'` | 0 | Verify zero forbidden extensions or database/secret files tracked in git |
| `! git grep -n -i -E '(password\s*[:=]\|secret\s*[:=]\|bearer\s+[A-Za-z0-9]\|BEGIN RSA\|BEGIN OPENSSH\|09[0-9]{9}\|[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})' -- ':!ROADMAP.md' ':!docs/adr/*' ':!.agents/*' ':!handoffs/*' ':!uv.lock'` | 0 | Verify zero sensitive patterns, private keys, IP addresses or real phone numbers in source/tests |
| `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-03-canonical-source-hashing/` | 0 | Validate completeness and markers of handoff package |

## Tests and evidence

- Acceptance evidence is mapped in `acceptance-matrix.md`.
- Raw command results are recorded in `test-results.txt`.
- Additional artifact paths: none

## Assumptions and open items

- none

## Risks

- Downstream packages (WP-04+) will consume these hashing contracts for change detection during XLSX sync.

## Rollback

1. Delete the feature branch `antigravity/phase-01-canonical-source-hashing`.
2. Checkout `main` branch to restore the repository to its clean baseline.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation is stopped pending independent Codex review. Gate G1 remains open. No Gate approval, merge, push, deploy, or next Work Package has been performed.
