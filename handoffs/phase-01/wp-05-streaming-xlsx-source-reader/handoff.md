# Handoff

## Identity

- Phase: 1 — source and data-model foundation (Phase 1 authorized, Gate G1 is open / in-progress)
- Work Package: WP-05: Read-only streaming XLSX source reader and physical row tracker (Status: REQUEST_CHANGES / R5-C Focused Review Fixes Delivery)
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
  - `fdaabe2` (docs(handoff): record completed R5-C evidence, benchmark and provenance)
  - `3b1d3af` (fix(local_agent,tests): canonicalize location map ordering, expand property/lifecycle tests, and enforce complete benchmark contracts (R5-C))
- Implementer: Google Antigravity
- Reviewer: Codex

## Scope

### Requested outcome

Implement a read-only streaming Excel `.xlsx` source reader under `apps/local_agent` using standard library `zipfile` and existing pinned `lxml 6.1.2`, extracting literal raw source inputs from the four approved sheets according to `RAW_CONTRACT_REGISTRY`, strictly excluding formula elements and formula coverage ranges, evaluating row activity from literal inputs, validating Persian and technical headers and UUIDv7 identifiers, and returning an immutable `XlsxSourceReadResult` containing a validated `ValidatedSourceWorkbookSnapshot` (WP-04) alongside an immutable mapping of physical row locations (`SourceRowLocation`).

This focused review-fix turn completes remediation items F1 through F4:
- **F1 / Finding R5-04 (Canonical Location-Map Order in Direct Construction):**
  - In `XlsxSourceReadResult.__post_init__`, after typed validation of all keys/values/invariants, defensively rebuild `locations_by_uuid` in ascending `uuid.bytes` order before wrapping with `MappingProxyType`.
  - Regression coverage in `test_r6_direct_construction_comprehensive_matrix` directly constructs from reverse-inserted mappings, verifies exact iteration order by `uuid.bytes`, tests caller permutations, proves caller mutation cannot alter the result, and retains all typed negative cases.
- **F2 / Finding R5-05 (Real XR-09 Property Evidence & Faithful Lifecycle Advancement):**
  - Repaired `test_r6_hypothesis_comprehensive_invariance_property` with 2 distinct active rows in `خرید-فروش` so all generated parameters (`sheet_order`, `reverse_rows`, `reverse_cells`, `string_mode`, `row_offset`) alter actual XLSX bytes and are observable in pre-reader XML. Compares complete snapshots, locations, and change planner output with nontrivial prior registry.
  - Repaired `test_r6_planner_full_lifecycle_transitions_and_idempotency` so state advancement is semantically correct (preserves prior revision on `UNCHANGED` items, uses `planned_revision` for `INSERT`, `EDIT`/reactivation and `VOID` transitions, retains voided identities, and verifies exact `PlanItem` fields and idempotency).
- **F3 / Finding R5-06 (Contract-Complete XR-12 Benchmark):**
  - Maintained `< 15.0s` and `< 128.0 MiB` limits and 15,000 active rows scale.
  - Extended synthetic tail to include 5,000 total representative inactive, formula-only (`&lt;f&gt;`), and style/formatting-only (`s="1"`, `s="2"`) rows across all 4 sheets plus 100 unused SST entries, asserted from pre-reader XML.
  - Subprocess returns 4 `sheet_snapshot_hash` values compared against literal, deterministic 64-hex golden digests checked into the test.
  - Benchmark executes in **10.5785s** with **80.41 MiB** peak RSS on Linux.
- **F4 (Accurate, Reproducible Handoff Evidence & Portable Scan):**
  - Synchronized all handoff files with corrected implementation and actual command outputs.
  - Preserved historical records, including Codex's independent result at `fdaabe2`: 194 passed, benchmark 11.1023s / 81.57 MiB on Linux.
  - Replaced scratch script with portable failure-on-match `git grep` command distinguishing "no matches" from tool failure.

### Review items status

- Finding R5-01 (Raw preservation & numeric validation): Remediated in R5-A and verified.
- Finding R5-03 (Cell coordinate validation & bounds): Remediated in R5-A and verified.
- Finding R5-02 / RB-01..RB-05 (SST index selection, inlineStr, escape semantics, lazy evaluation, regression suite): Remediated in R5-B and verified.
- Finding R5-04 / F1 (Constructor hardening, matrix tests, canonical location order): Remediated in R5-C and ready for review.
- Finding R5-05 / F2 (Hypothesis testing, XML evidence, faithful lifecycle transitions, late-failure cleanup): Remediated in R5-C and ready for review.
- Finding R5-06 / F3 (Contract-complete benchmark, pre-reader XML assertions, literal golden digests): Remediated in R5-C and ready for review.
- Finding F4 (Accurate handoff provenance, portable sensitive scan): Remediated in R5-C and ready for review.

### In scope

- Public module `accounting_local_agent.xlsx_source_reader` containing:
  - Version constant `XLSX_SOURCE_READER_VERSION = "xlsx-source-reader.v1"`.
  - Immutable dataclasses `SourceRowLocation` and `XlsxSourceReadResult` with hardened validation, defensive copying, and canonical `uuid.bytes` ordering.
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
| `apps/local_agent/src/accounting_local_agent/xlsx_source_reader.py` | Modified | Hardened `XlsxSourceReadResult` constructor with canonical `uuid.bytes` location mapping ordering; optimized streaming loops. |
| `tests/test_xlsx_source_reader.py` | Modified | Added F1 canonical order matrix tests, repaired F2 XML-observable Hypothesis property test and semantically correct lifecycle advancement, extended F3 contract-complete XR-12 benchmark fixture with pre-reader XML assertions and literal golden digests. |
| `tests/test_xlsx_source_reader_sst_activity_regressions.py` | Modified | Completed R5-B evidence: dynamic column lookup, leading zeros test, missing SST &lt;v&gt; failure on active rows vs ignored on inactive rows, and SST sharedStrings XML inspection. |
| `handoffs/phase-01/wp-05-streaming-xlsx-source-reader/*` | Modified | Synchronized handoff documents, acceptance matrix, and captured command test results for R5-C review-fix delivery. |

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
| `/root/.local/bin/uv run mypy .` | 0 | Verify strict static typing across 21 source files |
| `/root/.local/bin/uv run pytest -v tests/test_xlsx_source_reader_raw_contract_regressions.py tests/test_xlsx_source_reader_sst_activity_regressions.py tests/test_xlsx_source_reader.py -k "not test_xr12_synthetic_15000_row_benchmark"` | 0 | Execute three reader/regression test suites excluding XR-12 (104 passed in 2.73s) |
| `/root/.local/bin/uv run pytest -v` | 0 | Execute full repository test suite including XR-12 benchmark (194 passed in 19.88s) |
| `git diff --check origin/main...HEAD` | 0 | Verify clean diff with zero whitespace defects |
| `python3 -c "import subprocess, sys; res = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True); prohibited = ['.xlsx', '.xls', '.db', '.sqlite', '.pdf', '.env']; files = [line.strip().split()[-1] for line in res.stdout.splitlines() if line.strip()]; bad = [f for f in files if any(f.endswith(ext) for ext in prohibited)]; sys.exit(1 if bad else 0)"` | 0 | Verify zero forbidden binary/database/secret files tracked in git |
| `python3 -c "import subprocess, sys; res = subprocess.run(['git', 'grep', '-n', '-I', '-i', '-E', r'(password\s*[:=]|secret\s*[:=]|bearer\s+[A-Za-z0-9]|BEGIN RSA|BEGIN OPENSSH|09[0-9]{9})', '--', ':!ROADMAP.md', ':!docs/adr/*', ':!.agents/*', ':!handoffs/*', ':!uv.lock'], capture_output=True, text=True); sys.exit(0 if res.returncode == 1 else (1 if res.returncode == 0 else res.returncode))"` | 0 | Verify zero sensitive credentials, tokens, private keys, or Iranian mobile phone numbers |
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
