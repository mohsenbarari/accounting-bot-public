# Handoff

## Identity

- Phase: 1 — source and data-model foundation (Phase 1 authorized, Gate G1 is open / in-progress)
- Work Package: WP-05: Read-only streaming XLSX source reader and physical row tracker (Status: REQUEST_CHANGES / Limited R5-B Remediation)
- Branch/worktree: antigravity/phase-01-streaming-xlsx-source-reader
- Commit(s):
  - `5dd5d17` (feat(local_agent): implement streaming XLSX source reader and physical row tracker (WP-05))
  - `2e06bd5` (fix(local_agent): address Codex review findings R1-R9 in streaming XLSX source reader (WP-05))
  - `daedaff` (fix(local_agent): address Codex review round 2 findings R1-R9 in streaming XLSX reader (WP-05))
  - `d2e725c` (fix(local_agent): address Codex review round 3 findings R1-R6 in streaming XLSX reader (WP-05))
  - `e3e69d1` (fix(local_agent): address Codex review round 4 findings R4-01-R4-06 in streaming XLSX reader (WP-05))
  - `75ee8bb` (fix(local_agent): preserve raw types and validate numerics per R5-01 (WP-05))
  - `739b0b6` (fix(local_agent): remediate R5-A regressions A-01, A-02, A-03 (WP-05))
  - `b5ce0b5` (test(local_agent): complete R5-A evidence for E-01, E-02, E-03 (WP-05))
  - `8fab170` (docs(handoff): record R5-A evidence completion and benchmark results (WP-05))
  - `1ada40e` (fix(local_agent): selective SST index collection and activity exclusion per R5-02 (WP-05))
  - `4049a6c` (docs(handoff): record R5-B evidence completion and benchmark results (WP-05))
  - `b227875` (fix(local_agent): remediate R5-B findings RB-01..RB-04 for WP-05)
- Implementer: Google Antigravity
- Reviewer: Codex

## Scope

### Requested outcome

Implement a read-only streaming Excel `.xlsx` source reader under `apps/local_agent` using standard library `zipfile` and existing pinned `lxml 6.1.2`, extracting literal raw source inputs from the four approved sheets according to `RAW_CONTRACT_REGISTRY`, strictly excluding formula elements and formula coverage ranges, evaluating row activity from literal inputs, validating Persian and technical headers and UUIDv7 identifiers, and returning an immutable `XlsxSourceReadResult` containing a validated `ValidatedSourceWorkbookSnapshot` (WP-04) alongside an immutable mapping of physical row locations (`SourceRowLocation`).

This turn completes the limited remediation of review delivery R5-B based on findings RB-01 through RB-05:
- **Finding RB-01 (Pass 1 inlineStr container extraction):**
  - Updated `_discover_sheet_metadata_and_needed_sst` (Pass 1) to pass `&lt;is&gt;` child element directly to `_extract_text_from_si_or_is` instead of `&lt;c&gt;`.
  - Supports plain text, rich runs (`&lt;r&gt;&lt;t&gt;`), XML comments/tails, and Persian/ASCII zeros across all 4 sheets and namespaces.
- **Finding RB-02 (Escape & whitespace unification):**
  - Unified OOXML escape decoding (`_decode_ooxml_escapes`) and whitespace semantics so direct string cells (`t="str"`) decode escapes before testing activity.
  - Escaped whitespace (`_x0020_`, `_x0009_`, `_x000D_`, `_x00A0_`) is evaluated as inactive whitespace, whereas literal escaped markers (`_x005F_x0020_`) remain active.
- **Finding RB-03 (Lazy secondary SST evaluation & consumer coordinates):**
  - Secondary SST index evaluation is strictly lazy: candidate cells in inactive rows are not evaluated for `int()` or length limits, safely skipping 5000-digit string indices.
  - Decoded SST entries track exact consumer coordinates (`sheet_name`, `cell_ref`, `physical_row_number`), propagating location metadata to needed SST decode failures and surrogate errors.
- **Finding RB-04 (Regression suite completion):**
  - Created 26 comprehensive standalone regression tests in `tests/test_xlsx_source_reader_sst_activity_regressions.py` covering RB-01..RB-04, Cases 1–10, paired controls, full snapshot equality, WP-03 source hashes, Decimal scale preservation, equivalent representations/permutations, and stream pass bounds.
- **Finding RB-05 (Documentation integrity):**
  - Synchronized `acceptance-matrix.md`, `handoff.md`, and `test-results.txt`.
  - Restored approved performance criterion (< 15.0s, < 128 MiB) under XR-12; documented historical RED baseline and exact command outputs in `test-results.txt`.

### Review items status

- Finding R5-01 (Raw preservation & numeric validation): Remediated in R5-A and verified.
- Finding R5-03 (Cell coordinate validation & bounds): Remediated in R5-A and verified.
- Finding R5-02 / RB-01..RB-05 (SST index selection, inlineStr, escape semantics, lazy evaluation, regression suite): Remediated in R5-B and ready for review.
- Findings R5-04, R5-05, R5-06: PENDING subsequent review rounds in WP-05.

### In scope

- Public module `accounting_local_agent.xlsx_source_reader` containing:
  - Version constant `XLSX_SOURCE_READER_VERSION = "xlsx-source-reader.v1"`.
  - Immutable dataclasses `SourceRowLocation` and `XlsxSourceReadResult`.
  - Typed exceptions: `XlsxSourceReadError`, `XlsxPackageError`, `XlsxStructureError`, `XlsxHeaderError`, `XlsxCellError`, `XlsxFormulaCoverageError`, `XlsxIdentityError`.
  - Main streaming entrypoint `read_xlsx_source_snapshot(path: Path | str) -> XlsxSourceReadResult`.
  - Safe package relationship resolution (`_rels/.rels`, `xl/_rels/workbook.xml.rels`) for Transitional and Strict SpreadsheetML, validating unique `officeDocument` and `sharedStrings` targets.
  - Multi-pass streaming algorithm: Pass 1 discovers array and data-table formula coverage bounding boxes across each sheet with fast interval indexing and collects candidate Shared String Table (SST) references; SST pass selectively decodes only referenced indices; Pass 2 streams row elements, validates row 1 exact Persian and technical ID headers without stripping, evaluates row activity exclusively from literal inputs before decoding other fields, decodes cell literals, filters covered cells, verifies UUIDv7 format and global uniqueness, and constructs WP-04 snapshot inputs.
  - Strict text and leaf validation: Unsupported child tags inside text and value leaf nodes (t and v) raise `XlsxCellError(REASON_CELL_UNKNOWN_TYPE)` instead of false conversion; XML comments inside text nodes are preserved; strict worksheet root connection ignores nested worksheets inside `extLst`.
  - Raw preservation & numeric validation (Finding R5-01): Text in numeric/financial columns (`inlineStr`, `t="str"`, shared strings) retains exact original `str` (spaces, leading zeros, Persian/Arabic digits) in `raw_values`; financial numeric XML (`t=""` or `t="n"` with `v` element text) directly becomes `Decimal` (never `int` or `float`), preserving exact scale/precision; strict numeric XML regex applies only to numeric XML; active rows with explicit invalid/blank text in numeric columns raise `XlsxCellError(REASON_CELL_INVALID_NUMERIC_LEXEME)`; true missing cells remain `None`.
  - Exact Persian & technical headers matching: Validates all required Persian headers and technical ID headers (`record_id`) on row 1 with strict string equality (no `.strip()`), rejecting formula-backed or array-formula covered headers.
  - Selective SST index decoding (Finding R5-02 & RB-01..RB-03): Decodes only SST indices needed for active source inputs (excluding derived columns, non-whitelisted columns, formula cells, formula-covered cells, optional headers, and inactive rows). Inactive rows with corrupt SST strings (e.g. index 0) or unneeded leaf tags in secondary columns are completely skipped.
  - Strict numeric/SST grammar: Strict regex `^(?:0|[1-9][0-9]*)$` for SST indices; domain and canonical date validation errors directly consumed and mapped to `XlsxCellError` with coordinate metadata and zero data leakage.
- Public exports in `apps/local_agent/src/accounting_local_agent/__init__.py`.
- Documentation in `apps/local_agent/README.md`.
- Comprehensive test suite comprising 89 base tests, 38 reader suite tests in `tests/test_xlsx_source_reader.py`, 7 raw contract regression tests in `tests/test_xlsx_source_reader_raw_contract_regressions.py`, and 26 SST activity regression tests in `tests/test_xlsx_source_reader_sst_activity_regressions.py` (160 tests total).
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
| Section 5.1 / steps 1–2 | In-progress (🧪) | Streaming read-only XLSX extraction, formula/cache exclusion, literal retention, physical row tracking |
| Section 5.2 / step 3 | In-progress (🧪) | Full snapshot builder integration and input to change planner |
| Section 19.1 | In-progress (🧪) | All-or-nothing completion, zero partial import, zero false deletion |
| O-03, O-06, O-26 | In-progress (🧪) | Exact literal Raw boundary, UUIDv7 technical ID requirement, Persian headers |
| ADR-0008, WP-05 | In-progress (🧪) | Streaming lxml iterparse, memory bounding, safe relationship resolution, physical row location tracking |
| O-46 to O-51 | In-progress (🧪) | Handoff artifacts, benchmark evidence, independent Codex review |

## Changed files

| File | Change | Reason |
|---|---|---|
| `apps/local_agent/src/accounting_local_agent/xlsx_source_reader.py` | Modified | Implemented RB-01 inlineStr container extraction, RB-02 escape/whitespace unification, RB-03 lazy secondary SST evaluation and consumer coordinate propagation. |
| `tests/test_xlsx_source_reader_sst_activity_regressions.py` | Modified | Added RB-01..RB-04 regression tests (26 tests total) for snapshot equality, hashes, scale preservation, permutations, stream bounds, and Cases 1–10. |
| `handoffs/phase-01/wp-05-streaming-xlsx-source-reader/*` | Modified | Synchronized handoff documents, acceptance matrix, and captured command test results for R5-B remediation. |

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
| `uv run ruff format --check .` | 0 | Verify formatting compliance across 54 files |
| `uv run ruff check .` | 0 | Verify linting rules compliance |
| `uv run mypy .` | 0 | Verify strict static typing across 21 source files |
| `uv run pytest tests/test_xlsx_source_reader_sst_activity_regressions.py -v` | 0 | Execute standalone R5-B SST activity regression tests (26 passed in 0.84s) |
| `uv run pytest tests/test_xlsx_source_reader_raw_contract_regressions.py -v` | 0 | Execute standalone R5-A raw contract regression tests (7 passed in 0.63s) |
| `uv run pytest tests/test_xlsx_source_reader.py -v` | 0 | Execute base reader test suite (38 passed in 15.43s) |
| `uv run pytest -v` | 0 | Execute full suite of 160 tests (160 passed in 21.42s) |
| `git diff --check origin/main...HEAD` | 0 | Verify clean diff with zero whitespace defects |
| `python3 -c "import subprocess, sys; out = subprocess.check_output(['git', 'ls-files'], text=True); matches = [line for line in out.splitlines() if line.endswith(('.xlsx', '.xls', '.xlsm', '.sqlite', '.sqlite3', '.db', '.pdf', '.key', '.pem', '.env'))]; sys.exit(1 if matches else 0)"` | 0 | Verify zero forbidden binary/database/secret files tracked in git |
| `python3 -c "import subprocess, sys; res = subprocess.run(['git', 'grep', '-n', '-I', '-i', '-E', r'(password\s*[:=]|secret\s*[:=]|bearer\s+[A-Za-z0-9]|BEGIN RSA|BEGIN OPENSSH|09[0-9]{9})', '--', ':!ROADMAP.md', ':!docs/adr/*', ':!.agents/*', ':!handoffs/*', ':!uv.lock'], capture_output=True, text=True); sys.exit(0 if res.returncode == 1 else 1)"` | 0 | Verify zero sensitive credentials, IPs or real phone numbers in code/tests |
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

Implementation is stopped pending independent Codex review of R5-B remediations. Gate G1 remains OPEN / IN PROGRESS and WP-05 remains REQUEST_CHANGES. No Gate approval, merge, push, deploy, or next Work Package has been performed.
