# Handoff

## Identity

- Phase: 1 — source and data-model foundation (Phase 1 authorized, Gate G1 is open / in-progress)
- Work Package: WP-05: Read-only streaming XLSX source reader and physical row tracker (Status: REQUEST_CHANGES / R5-C Finalization Delivery)
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
  - `4049a6c` (fix(local_agent): selective SST index collection and activity exclusion per R5-02 (WP-05))
  - `b227875` (fix(local_agent): remediate R5-B findings RB-01..RB-04 for WP-05)
  - `8b57e62` (test(local_agent): complete RB-04 SST activity regression assertions and observer bounds (WP-05))
  - `fd13b70` (docs(handoff): update WP-05 handoff with verified RB-04/RB-05 evidence)
  - `ce21abc` (test(local_agent): expand RB-04 activity matrix, independent oracle assertions and failure cleanup (WP-05))
  - `b0933ce` (docs(handoff): record R5-B evidence completion, clean test results, and benchmark baseline (WP-05))
  - `ac523df` (feat(local_agent): harden constructors, expand property/lifecycle tests, and optimize streaming reader (R5-C))
- Implementer: Google Antigravity
- Reviewer: Codex

## Scope

### Requested outcome

Implement a read-only streaming Excel `.xlsx` source reader under `apps/local_agent` using standard library `zipfile` and existing pinned `lxml 6.1.2`, extracting literal raw source inputs from the four approved sheets according to `RAW_CONTRACT_REGISTRY`, strictly excluding formula elements and formula coverage ranges, evaluating row activity from literal inputs, validating Persian and technical headers and UUIDv7 identifiers, and returning an immutable `XlsxSourceReadResult` containing a validated `ValidatedSourceWorkbookSnapshot` (WP-04) alongside an immutable mapping of physical row locations (`SourceRowLocation`).

This delivery completes the comprehensive remediation package R5-C addressing findings R5-04, R5-05, R5-06, and the remaining R5-B evidence items:
- **C-01 / Finding R5-04 (Constructor Hardening & Invariance Matrix):**
  - Hardened `SourceRowLocation` to validate sheet name against registry, enforce integer type (rejecting bools/floats), and enforce physical row bounds ($2 \le r \le 1{,}048{,}576$).
  - Hardened `XlsxSourceReadResult` to validate `ValidatedSourceWorkbookSnapshot` instance, check reader version string, defensively wrap locations in read-only `MappingProxyType`, verify exact 1-to-1 UUID identity match with snapshot rows, check sheet correspondence for each UUID, and reject duplicate physical rows within the same sheet.
  - Added comprehensive positive and negative matrix tests in `tests/test_xlsx_source_reader.py::test_r6_direct_construction_comprehensive_matrix`.
- **C-02 / Finding R5-05 (Hypothesis Property Testing & Planner Full Lifecycle):**
  - Added genuine Hypothesis property-based testing (`@given`) in `tests/test_xlsx_source_reader.py::test_r6_hypothesis_comprehensive_invariance_property` testing random permutations of sheet order, row reversals, cell reversals, and string storage modes (`inline`, `direct_str`, `sst`).
  - Added comprehensive WP-04 Change Planner lifecycle transitions test on actual XLSX bytes in `tests/test_xlsx_source_reader.py::test_r6_planner_full_lifecycle_transitions_and_idempotency` (verifying initial Inserts, subsequent in-place Edits, Voids via row deletion, Reactivations via row re-addition, rejection of Cross-sheet UUID collisions, and repeat execution Idempotency).
  - Added 4th sheet late-failure all-or-nothing test in `tests/test_xlsx_source_reader.py::test_r6_all_or_nothing_fourth_sheet_late_failure`.
- **C-03 / Remaining R5-B Evidence Items:**
  - In `tests/test_xlsx_source_reader_sst_activity_regressions.py`, dynamically look up activity and date column letters from `RAW_CONTRACT_REGISTRY`.
  - Added `test_rb_04_raw_text_numeric_appearance_leading_zeros_and_spaces` for synthetic raw text with leading zeros and spaces (`" 000123 "`).
  - Added `test_rb_03_missing_sst_v_element_on_active_row_fails_and_inactive_row_ignored` verifying missing `&lt;v&gt;` on active rows raises `XlsxCellError(REASON_CELL_INVALID_SST_INDEX)` with coordinate metadata, while missing `&lt;v&gt;` on inactive rows is safely ignored.
  - Added direct XML tree inspection in `test_rb_04_equivalent_representations_and_row_cell_permutation` asserting index $\to$ string/UUID remapping in `xl/sharedStrings.xml`.
- **C-04 / Finding R5-06 (Streaming Performance Optimization):**
  - Profiled and optimized Pass 1 and Pass 2 hot loops: precomputed column letter and name dictionaries, fast ASCII cell reference parsing, cached worksheet root elements, precomputed XML tag sets, sibling element cleanup without getroottree recursion, and selective string extraction.
  - Streaming benchmark for 15,000 active rows + 5,000 tail rows executes in **12.3673s** with **81.78 MiB** peak RSS (well within the approved limits of < 15.0s and < 128 MiB).
- **C-05 (Documentation & Provenance):**
  - Synchronized `test-results.txt`, `acceptance-matrix.md`, and `handoff.md` with complete historical measurements, verbatim execution outputs, and clean validation.

### Review items status

- Finding R5-01 (Raw preservation & numeric validation): Remediated in R5-A and verified.
- Finding R5-03 (Cell coordinate validation & bounds): Remediated in R5-A and verified.
- Finding R5-02 / RB-01..RB-05 (SST index selection, inlineStr, escape semantics, lazy evaluation, regression suite): Remediated in R5-B and verified.
- Finding R5-04 (Constructor hardening & matrix tests): Remediated in R5-C and ready for review.
- Finding R5-05 (Hypothesis testing, lifecycle transitions, late-failure cleanup): Remediated in R5-C and ready for review.
- Finding R5-06 (Streaming performance optimization & benchmark safety): Remediated in R5-C and ready for review.

### In scope

- Public module `accounting_local_agent.xlsx_source_reader` containing:
  - Version constant `XLSX_SOURCE_READER_VERSION = "xlsx-source-reader.v1"`.
  - Immutable dataclasses `SourceRowLocation` and `XlsxSourceReadResult` with hardened validation and defensive copying.
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
- Comprehensive test suite comprising 89 base tests, 39 reader suite tests in `tests/test_xlsx_source_reader.py`, 7 raw contract regression tests in `tests/test_xlsx_source_reader_raw_contract_regressions.py`, and 59 SST activity regression tests in `tests/test_xlsx_source_reader_sst_activity_regressions.py` (194 tests total).
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
| `apps/local_agent/src/accounting_local_agent/xlsx_source_reader.py` | Modified | Hardened constructors with defensive copying and duplicate row detection; optimized Pass 1 and Pass 2 hot loops (precomputed maps, fast ASCII parser, tag sets, sibling cleanup). |
| `tests/test_xlsx_source_reader.py` | Modified | Added comprehensive direct construction matrix test (C-01), WP-04 planner full lifecycle transitions test (C-02), Hypothesis property test (C-02), and 4th sheet late-failure test (C-02). |
| `tests/test_xlsx_source_reader_sst_activity_regressions.py` | Modified | Completed R5-B evidence: dynamic column lookup, leading zeros test, missing SST &lt;v&gt; failure on active rows vs ignored on inactive rows, and SST sharedStrings XML inspection. |
| `handoffs/phase-01/wp-05-streaming-xlsx-source-reader/*` | Modified | Synchronized handoff documents, acceptance matrix, and captured command test results for R5-C delivery. |

## Schema and migrations

- Schema impact: none
- Migration files: none
- Backward compatibility: fully backward-compatible addition; does not alter existing contracts or domain modules
- Data migration/real data used: none

## Commands and exit codes

| Command | Exit code | Purpose |
|---|---:|---|
| `uv --version` | 0 | Verify uv version 0.12.7 |
| `/root/.local/bin/uv lock --check` | 0 | Verify lockfile consistency |
| `/root/.local/bin/uv sync --frozen --all-packages --all-groups` | 0 | Verify frozen dependencies installation |
| `/root/.local/bin/uv run ruff format --check .` | 0 | Verify formatting compliance across 54 files |
| `/root/.local/bin/uv run ruff check .` | 0 | Verify linting rules compliance |
| `/root/.local/bin/uv run mypy apps/ packages/ tests/` | 0 | Verify strict static typing across 21 source files |
| `/root/.local/bin/uv run pytest tests/test_xlsx_source_reader_sst_activity_regressions.py -vv` | 0 | Execute standalone R5-B SST activity regression tests (59 passed in 1.62s) |
| `/root/.local/bin/uv run pytest tests/test_xlsx_source_reader_raw_contract_regressions.py -v` | 0 | Execute standalone R5-A raw contract regression tests (7 passed in 0.74s) |
| `/root/.local/bin/uv run pytest tests/test_xlsx_source_reader.py -v` | 0 | Execute base reader test suite (39 passed in 15.16s) |
| `/root/.local/bin/uv run pytest -v` | 0 | Execute full suite of 194 tests (194 passed in 21.79s) |
| `git diff --check origin/main...HEAD` | 0 | Verify clean diff with zero whitespace defects |
| `python3 -c "import subprocess, sys; out = subprocess.check_output(['git', 'ls-files'], text=True); matches = [line for line in out.splitlines() if line.endswith(('.xlsx', '.xls', '.xlsm', '.sqlite', '.sqlite3', '.db', '.pdf', '.key', '.pem', '.env'))]; sys.exit(1 if matches else 0)"` | 0 | Verify zero forbidden binary/database/secret files tracked in git |
| `python3 /root/.gemini/antigravity-ide/brain/f01b0a03-6f9b-4719-9c90-6eb87b114127/scratch/run_sensitive_scan.py` | 0 | Verify zero sensitive credentials, tokens, private keys, or Iranian mobile phone numbers |
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

Implementation is stopped pending independent Codex review of R5-C delivery. Gate G1 remains OPEN / IN PROGRESS and WP-05 remains REQUEST_CHANGES. No Gate approval, merge, push, deploy, or next Work Package has been performed.
