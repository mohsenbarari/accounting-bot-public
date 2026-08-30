# Handoff

## Identity

- Phase: 1 — source and data-model foundation
- Work Package: WP-02: Raw input contract registry
- Branch/worktree: antigravity/phase-01-raw-input-contracts
- Commit(s): f03c90f (implementation), e208819 (handoff documentation)
- Implementer: Google Antigravity
- Reviewer: Codex

## Scope

### Requested outcome

Create the versioned, immutable and fully tested raw input contract registry defining the four approved Excel sheet boundaries ('خرید-فروش', 'دریافت-پرداخت', 'ورود-خروج', 'لیست کسبه') with strict formula and derived column exclusion, pure cell classification and invariant validation.

### In scope

- Public module `accounting_contracts.raw_input_contracts` containing:
  - Version constant `RAW_SOURCE_CONTRACT_VERSION = "raw-source-contract.v1"`.
  - Typed `StrEnum` definitions for `ColumnRole`, `ValueKind` (no Float), and `CellClassification`.
  - Frozen, slotted contract models `RawColumnContract` and `RawSheetContract`.
  - Immutable registry `RawContractRegistry` containing the four normative sheet contracts.
  - Explicit lookup and cell classification APIs.
- Self-validation of structural invariants (column address syntax, unique letters and fields, stable ID presence, non-overlapping roles, non-empty activity columns).
- Package documentation in `packages/contracts/README.md`.
- Deterministic and Hypothesis property-based test suite in `tests/test_raw_input_contracts.py`.
- Handoff artifacts and validation.

### Out of scope

- Opening, parsing, reading or writing Excel workbooks or XML.
- UUIDv7 generation, Persian date canonicalization, number parsing or hashing.
- Database models, migrations, persistence or network sync.
- Financial events, transaction codes, accounting balance engine, PDF or Telegram logic.
- Adding new dependencies, lockfile changes, infrastructure or production mutation.
- Modifying ROADMAP.md, accepted ADRs, Gate status, or merging to main.

## Roadmap traceability

| Roadmap section / O-item | Approved status | Implemented behavior |
|---|---|---|
| Sections 5.2, 5.4 | ✅ Approved | Whitelist-only raw input extraction contracts and formula/cached-value exclusion |
| Section 5.1 / O-03 | ✅ Approved | Stable UUIDv7 technical ID locations Z/P/P/D defined as non-financial metadata |
| Section 5.5 / O-25 | ✅ Approved | Strict value kinds (raw_text, integer_toman, decimal, uuid7) with zero Float kinds |
| Section 5.5 / O-26 | ✅ Approved | Raw Immutable limited to literal source inputs; derived columns excluded |
| Phase 1 / G1 | ✅ Approved | Four-sheet raw boundary defined before downstream canonicalization and ingestion |
| O-46 to O-50 | ✅ Approved | Bounded execution on feature branch, no self-approval, full handoff matrix and stop state |

## Changed files

| File | Change | Reason |
|---|---|---|
| `packages/contracts/src/accounting_contracts/raw_input_contracts.py` | Created | Raw input contract models, normative sheet definitions, invariant validations, and registry |
| `packages/contracts/src/accounting_contracts/__init__.py` | Modified | Export raw input contract public types, constants, exceptions, and registry |
| `packages/contracts/README.md` | Modified | Document raw input contracts module scope, boundaries, and classification rules |
| `tests/test_raw_input_contracts.py` | Created | Deterministic and Hypothesis property-based tests for raw contracts and classification |
| `handoffs/phase-01/wp-02-raw-input-contracts/*` | Created | WP-02 Handoff, Acceptance Matrix, and Test Results |

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
| `uv run pytest -v` | 0 | Execute all 33 unit and property tests |
| `git diff --check origin/main...HEAD` | 0 | Verify clean diff with zero whitespace or line-ending defects against origin/main |
| `git ls-files \| grep -E '\.(xlsx\|xls\|xlsm\|sqlite\|sqlite3\|db\|pdf\|key\|pem\|env)$' \|\| true` | 0 | Verify no forbidden extensions or database/secret files tracked in git |
| `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-02-raw-input-contracts/` | 0 | Validate completeness and markers of handoff package |

## Tests and evidence

- Acceptance evidence is mapped in `acceptance-matrix.md`.
- Raw command results are recorded in `test-results.txt`.
- Additional artifact paths: none

## Assumptions and open items

- none

## Risks

- Downstream packages (WP-03+) will consume these contracts for XLSX parsing and canonicalization.

## Rollback

1. Delete the feature branch `antigravity/phase-01-raw-input-contracts`.
2. Checkout `main` branch to restore the repository to its clean baseline.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation is stopped pending independent Codex review. No Gate approval, merge, push, deploy, or next Work Package has been performed.
