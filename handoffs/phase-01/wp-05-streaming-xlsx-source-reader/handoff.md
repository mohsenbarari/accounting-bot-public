# Handoff

## Identity

- Phase: 1 — source and data-model foundation (Phase 1 authorized, Gate G1 is open / in-progress)
- Work Package: WP-05: Read-only streaming XLSX source reader and physical row tracker
- Branch/worktree: antigravity/phase-01-streaming-xlsx-source-reader
- Commit(s):
  - `5dd5d17` (feat(local_agent): implement streaming XLSX source reader and physical row tracker (WP-05))
- Implementer: Google Antigravity
- Reviewer: Codex

## Scope

### Requested outcome

Implement a read-only streaming Excel `.xlsx` source reader under `apps/local_agent` using standard library `zipfile` and existing pinned `lxml 6.1.2`, extracting literal raw source inputs from the four approved sheets according to `RAW_CONTRACT_REGISTRY`, strictly excluding formula elements and formula coverage ranges, evaluating row activity from literal inputs, validating Persian and technical headers and UUIDv7 identifiers, and returning an immutable `XlsxSourceReadResult` containing a validated `ValidatedSourceWorkbookSnapshot` (WP-04) alongside an immutable mapping of physical row locations (`SourceRowLocation`).

### In scope

- Public module `accounting_local_agent.xlsx_source_reader` containing:
  - Version constant `XLSX_SOURCE_READER_VERSION = "xlsx-source-reader.v1"`.
  - Immutable dataclasses `SourceRowLocation` and `XlsxSourceReadResult`.
  - Typed exceptions: `XlsxSourceReadError`, `XlsxPackageError`, `XlsxStructureError`, `XlsxHeaderError`, `XlsxCellError`, `XlsxFormulaCoverageError`, `XlsxIdentityError`.
  - Main streaming entrypoint `read_xlsx_source_snapshot(path: Path | str) -> XlsxSourceReadResult`.
  - Safe package relationship resolution (`.rels`, `xl/_rels/workbook.xml.rels`) for Transitional and Strict SpreadsheetML.
  - Multi-pass streaming algorithm: Pass 1 discovers array and data-table formula coverage bounding boxes across each sheet; Pass 2 streams row elements, checks row 1 headers, decodes cell literals, filters covered cells, evaluates row activity, verifies UUIDv7 format and global uniqueness, and constructs WP-04 snapshot inputs.
  - Full support for literal decoding: shared strings, inline strings, direct strings, OOXML escape decoding (`_xHHHH_`), direct `Decimal` parsing from numeric XML without binary float approximations, and strict rejection of numeric XML in `date_raw`/`stable_id`, boolean `b` and error `e` cell types.
- Public exports in `apps/local_agent/src/accounting_local_agent/__init__.py`.
- Documentation in `apps/local_agent/README.md`.
- Comprehensive unit, integration, property and 15,000-row streaming benchmark tests in `tests/test_xlsx_source_reader.py`.
- Handoff package and acceptance evidence.

### Out of scope

- Opening, reading, copying or modifying any actual/reference Excel file or live database.
- Excel COM, desktop automation, save/file monitoring, workbook writing/repair or UUID generation.
- Business rule validation, accounting accounts, ledger entries, SQLite/PostgreSQL persistence or network APIs.
- Real phone numbers, real credentials or production deployment.
- Merging to `main`, pushing to origin or opening PR.

## Roadmap traceability

| Roadmap section / O-item | Approved status | Implemented behavior |
|---|---|---|
| Section 5.1 / steps 1–2 | ✅ Approved | Streaming read-only XLSX extraction, formula/cache exclusion, literal retention, physical row tracking |
| Section 5.2 / step 3 | ✅ Approved | Full snapshot builder integration and input to change planner |
| Section 19.1 | ✅ Approved | All-or-nothing completion, zero partial import, zero false deletion |
| O-03, O-06, O-26 | ✅ Approved | Exact literal Raw boundary, UUIDv7 technical ID requirement, Persian headers |
| ADR-0008, WP-05 | ✅ Approved | Streaming lxml iterparse, memory bounding, safe relationship resolution, physical row location tracking |
| O-46 to O-51 | ✅ Approved | Handoff artifacts, benchmark evidence, independent Codex review |

## Changed files

| File | Change | Reason |
|---|---|---|
| `apps/local_agent/src/accounting_local_agent/xlsx_source_reader.py` | Created | Streaming XLSX source reader, physical row tracker, and typed error hierarchy |
| `apps/local_agent/src/accounting_local_agent/__init__.py` | Modified | Export public reader function, result types, and errors |
| `apps/local_agent/README.md` | Modified | Document reader architecture, supported profile, and blank-row rules |
| `tests/test_xlsx_source_reader.py` | Created | Full test suite covering XR-01 through XR-12 with synthetic in-memory fixtures |
| `handoffs/phase-01/wp-05-streaming-xlsx-source-reader/*` | Created | Handoff document, acceptance matrix, and test results |

## Schema and migrations

- Schema impact: none
- Migration files: none
- Backward compatibility: fully backward-compatible addition; does not alter existing contracts or domain modules
- Data migration/real data used: none

## Commands and exit codes

| Command | Exit code | Purpose |
|---|---:|---|
| `uv --version` | 0 | Verify uv version 0.12.7 |
| `uv lock --check` | 0 | Verify lockfile consistency |
| `uv sync --frozen --all-packages --all-groups` | 0 | Verify frozen dependencies installation |
| `uv run ruff format --check .` | 0 | Verify formatting compliance across 51 files |
| `uv run ruff check .` | 0 | Verify linting rules compliance |
| `uv run mypy .` | 0 | Verify strict static typing across 19 source files |
| `uv run pytest -v` | 0 | Execute all 109 unit, benchmark, integration and property tests |
| `uv run pytest tests/test_xlsx_source_reader.py -k test_xr12_synthetic_15000_row_benchmark -s` | 0 | Execute 15,000-row synthetic benchmark displaying duration (8.9897s under 15.0s) and peak memory (106.87 MiB under 128.0 MiB) |
| `git diff --check origin/main...HEAD` | 0 | Verify clean diff with zero whitespace defects |
| `! git ls-files \| grep -E '\.(xlsx\|xls\|xlsm\|sqlite\|sqlite3\|db\|pdf\|key\|pem\|env)$'` | 0 | Verify zero forbidden binary/database/secret files tracked in git |
| `! git grep -n -i -E '(password\s*[:=]\|secret\s*[:=]|bearer\s+[A-Za-z0-9]\|BEGIN RSA\|BEGIN OPENSSH\|09[0-9]{9}\|[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})' -- ':!ROADMAP.md' ':!docs/adr/*' ':!.agents/*' ':!handoffs/*' ':!uv.lock'` | 0 | Verify zero sensitive credentials, IPs or real phone numbers in code/tests |
| `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-05-streaming-xlsx-source-reader/` | 0 | Validate completeness and structure of handoff package |

## Tests and evidence

- Acceptance evidence is mapped in `acceptance-matrix.md`.
- Raw command results are recorded in `test-results.txt`.
- Additional artifact paths: none

## Assumptions and open items

- Strict Literal Extraction: Formulas and array/dataTable covered ranges are never evaluated, converted, or imported; they map to `None`.
- Immutable Physical Location Decoupling: `SourceRowLocation` is tracked alongside the snapshot but is strictly excluded from `source_hash` and `sheet_snapshot_hash`.
- No Auto-Repair: Missing UUIDv7 on active rows or duplicate UUIDs immediately abort the entire read operation with typed errors.
- All-or-Nothing Completeness: Any XML syntax error, missing sheet, or missing header fails the entire operation, preventing partial state imports.

## Risks

- Windows COM Lock Contention in Future Stages (Phase 2): When local agent monitors active workbooks during user editing, file sharing/locking must be handled with appropriate copy-on-read mechanisms (to be addressed in local agent file monitor packages).
- Shared Strings Table Scaling: Extremely large workbooks with hundreds of thousands of distinct shared strings may increase peak memory; streaming table indexing is used, but extremely large workbooks will be bounded by host memory.

## Rollback

1. Delete the feature branch `antigravity/phase-01-streaming-xlsx-source-reader`.
2. Checkout `main` branch to restore the repository to its clean baseline.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation is stopped pending independent Codex review. Gate G1 remains open. No Gate approval, merge, push, deploy, or next Work Package has been performed.
