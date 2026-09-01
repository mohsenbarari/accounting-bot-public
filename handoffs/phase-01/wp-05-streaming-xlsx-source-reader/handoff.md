# Handoff

## Identity

- Phase: 1 — source and data-model foundation (Phase 1 authorized, Gate G1 is open / in-progress)
- Work Package: WP-05: Read-only streaming XLSX source reader and physical row tracker (Status: REQUEST_CHANGES / R5-G Windows Mypy Typing Correction Delivery)
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
  - `fe7126c` (docs(handoff): update WP-05 handoff evidence, matrix, and test results for R5-C review fixes)
  - `9d8f677` (docs(handoff): finalize WP-05 R5-C evidence, exact XR-09 wording, and portable tracked scan)
  - `c4a8292` (fix(local_agent,tests): optimize streaming memory lifecycle and harden Windows memory probe (R5-D))
  - `995f8b1` (docs(handoff): update WP-05 handoff evidence, benchmark, and acceptance matrix for R5-D)
  - `02f7acd` (test(local_agent): measure call-window current RSS and add sampler tests (R5-E))
  - `ee33f95` (docs(handoff): record R5-E call-window RSS evidence, benchmark results, and matrix updates)
  - `c3c5978` (test(local_agent): harden sampler lifecycle, probe error propagation, and VmRSS grammar (R5-F))
  - `4cda4d7` (docs(handoff): record R5-F sampler lifecycle and parser hardening evidence)
  - `7baff38` (fix(tests): cast Windows WorkingSetSize to concrete float for Mypy win32 (R5-G))
- Implementer: Google Antigravity
- Reviewer: Codex

## Scope

### Requested outcome

Implement a read-only streaming Excel `.xlsx` source reader under `apps/local_agent` using standard library `zipfile` and existing pinned `lxml 6.1.2`, extracting literal raw source inputs from the four approved sheets according to `RAW_CONTRACT_REGISTRY`, strictly excluding formula elements and formula coverage ranges, evaluating row activity from literal inputs, validating Persian and technical headers and UUIDv7 identifiers, and returning an immutable `XlsxSourceReadResult` containing a validated `ValidatedSourceWorkbookSnapshot` (WP-04) alongside an immutable mapping of physical row locations (`SourceRowLocation`).

This delivery finalizes remediation package R5-G addressing third CI run `33503837653` on PR #19:
- **CI Run 33503837653 Outcome:**
  - Linux job `99843105587`: SUCCESS in 23s (Mypy clean, benchmark duration 1.6929s, baseline current RSS 27.47 MiB, call peak RSS 59.06 MiB, 15,000 rows, full suite 200 passed in 6.61s).
  - Windows job `99843105723`: FAILURE in 36s during Mypy check with error `tests\test_xlsx_source_reader.py:2694: error: Returning Any from function declared to return "float" [no-any-return]` before pytest execution.
- **R5-G1 (Windows WorkingSetSize Explicit Concrete Typing Conversion):**
  - Converted `counters.WorkingSetSize` explicitly via concrete numeric conversions (`working_set_bytes = int(counters.WorkingSetSize)` and `working_set_mib = float(working_set_bytes) / (1024.0 * 1024.0)`) in both the benchmark-embedded probe and the module-level helper `_get_current_process_rss_mib()`.
  - Validated that both native Linux Mypy (`/root/.local/bin/uv run mypy .`) and cross-platform Windows target (`/root/.local/bin/uv run mypy --platform win32 .`) pass with zero errors across all 21 source files.
- **R5-G2 / Contract Preservation:**
  - Preserved the accepted typed WinAPI signatures, `PROCESS_MEMORY_COUNTERS` 10-field layout, `WorkingSetSize` current-memory semantics, 5 ms sampling interval, one-shot lifecycle state machine, termination verification guard, strict `VmRSS` parser, and all 200 repository tests.
  - Maintained zero changes to product code `xlsx_source_reader.py`, `.github/workflows/ci.yml`, dependencies, lockfile, ADRs, or Work Packages.

### Review items status

- Finding R5-01 (Raw preservation & numeric validation): Remediated in R5-A and verified.
- Finding R5-03 (Cell coordinate validation & bounds): Remediated in R5-A and verified.
- Finding R5-02 / RB-01..RB-05 (SST index selection, inlineStr, escape semantics, lazy evaluation, regression suite): Remediated in R5-B and verified.
- Finding R5-04 / F1 (Constructor hardening, matrix tests, canonical location order): Remediated in R5-C and verified.
- Finding R5-05 / F2, E3 (Hypothesis testing, XML evidence, faithful lifecycle transitions, late-failure cleanup): Remediated in R5-C and verified.
- Finding R5-06 / F3 (Contract-complete benchmark, pre-reader XML assertions, literal golden digests): Remediated in R5-C and verified.
- Finding F4 / E1..E3 (Accurate handoff provenance, portable tracked-asset and sensitive scans): Remediated in R5-C and verified.
- Finding CI-01 / R5-D1 (Linux RSS lifecycle optimization): Remediated in R5-D and verified.
- Finding CI-02 / R5-D2 (Hardened Windows WinAPI memory probe): Remediated in R5-D, accepted in CI run 33496419728.
- Finding R5-E1..R5-E4 (Call-window current RSS sampling & tests): Remediated in R5-E and verified.
- Finding R5-F1..R5-F4 (Sampler lifecycle, termination guard, worker error propagation, strict VmRSS): Remediated in R5-F and verified (accepted on Linux CI run 33503837653).
- Finding R5-G1..R5-G2 (Windows WorkingSetSize concrete typing conversion for win32 Mypy): Remediated in R5-G and ready for review.

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
- Comprehensive test suite comprising 89 base tests, 45 reader suite tests in `tests/test_xlsx_source_reader.py`, 7 raw contract regression tests in `tests/test_xlsx_source_reader_raw_contract_regressions.py`, and 59 SST activity regression tests in `tests/test_xlsx_source_reader_sst_activity_regressions.py` (200 tests total).
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
| `tests/test_xlsx_source_reader.py` | Modified | Applied explicit concrete `int`/`float` conversion for Windows `WorkingSetSize` in `bench_code` and `_get_current_process_rss_mib` to resolve `mypy --platform win32` without Any returns. |
| `handoffs/phase-01/wp-05-streaming-xlsx-source-reader/*` | Modified | Synchronized handoff documents, acceptance matrix, and captured command test results for R5-G delivery. |

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
| `/root/.local/bin/uv run mypy .` | 0 | Verify strict static typing on native Linux across 21 source files |
| `/root/.local/bin/uv run mypy --platform win32 .` | 0 | Verify strict static typing for target Windows platform across 21 source files |
| `/root/.local/bin/uv run pytest tests/test_xlsx_source_reader.py -k "test_r6_windows_memory_probe_structure_and_types or test_r6_current_process_rss_probe_and_sampler or test_r6_current_process_rss_sampler_worker_error_propagation or test_r6_current_process_rss_sampler_termination_guard or test_r6_current_process_rss_sampler_lifecycle_and_validation or test_r6_linux_proc_status_vmrss_parser_rejections"` | 0 | Execute 6 focused probe, sampler, lifecycle, and parser unit tests (6 passed in 2.85s) |
| `/root/.local/bin/uv run pytest tests/test_xlsx_source_reader.py -k "test_xr12_synthetic_15000_row_benchmark" -s -vv` | 0 | Execute 3 clean subprocess benchmark repetitions under CPython 3.13.15 (11.13s/59.20M, 11.82s/59.18M, 11.18s/59.26M) |
| `/root/.local/bin/uv run pytest -v` | 0 | Execute full repository test suite including XR-12 benchmark (200 passed in 22.06s) |
| `git diff --check origin/main...HEAD` | 0 | Verify clean diff with zero whitespace defects |
| `python3 -c "import subprocess, sys; res = subprocess.run(['git', 'ls-files'], capture_output=True, text=True); (print('ERROR: git ls-files failed:\n' + res.stderr) or sys.exit(res.returncode)) if res.returncode != 0 else None; prohibited = ('.xlsx', '.xls', '.xlsm', '.sqlite', '.sqlite3', '.db', '.pdf', '.key', '.pem', '.env'); files = [line.strip() for line in res.stdout.splitlines() if line.strip()]; bad = [f for f in files if f.lower().endswith(prohibited)]; (print('PROHIBITED TRACKED FILES FOUND:\n' + '\n'.join(bad)) or sys.exit(1)) if bad else print('PASS: No prohibited tracked files found (checked ' + str(len(files)) + ' tracked files)')"` | 0 | Verify zero forbidden tracked binary/database/secret files in git |
| `python3 -c "import subprocess, sys; res = subprocess.run(['git', 'grep', '-n', '-I', '-i', '-E', r'(password\s*[:=]|secret\s*[:=]|bearer\s+[A-Za-z0-9]|BEGIN RSA|BEGIN OPENSSH|09[0-9]{9})', '--', ':!ROADMAP.md', ':!docs/adr/*', ':!.agents/*', ':!handoffs/*', ':!uv.lock'], capture_output=True, text=True); (print('PASS: No sensitive patterns detected (grep exit 1)') or sys.exit(0)) if res.returncode == 1 else ((print('FAIL: Found sensitive patterns:\n' + res.stdout) or sys.exit(1)) if res.returncode == 0 else (print('ERROR: git grep failed:\n' + res.stderr) or sys.exit(res.returncode)))"` | 0 | Verify zero sensitive credentials, tokens, private keys, or Iranian mobile phone numbers |
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

Implementation is stopped pending independent Codex review and CI publication of R5-G delivery on PR #19. Gate G1 remains OPEN / IN PROGRESS and WP-05 remains REQUEST_CHANGES. No Gate approval, merge, push, deploy, or next Work Package has been performed.
