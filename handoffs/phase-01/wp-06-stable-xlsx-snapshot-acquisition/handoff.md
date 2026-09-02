# Handoff: WP-06 Stable XLSX Snapshot Acquisition and Cleanup

## Identity

- **Work Package:** `WP-06: Stable XLSX snapshot acquisition and cleanup`
- **Phase:** `1 — source and data-model foundation`
- **Component Version:** `XLSX_SNAPSHOT_ACQUISITION_VERSION = "xlsx-snapshot-acquisition.v1"`
- **Baseline Commit:** `bc085acd8852e2293fdfcb786a7694fe96e93407`
- **Target Branch:** `antigravity/phase-01-stable-xlsx-snapshot-acquisition`
- **Linear Branch Commits:**
  - `84d6cfa` — `feat(local_agent): implement stable XLSX snapshot acquisition and cleanup (WP-06)`
  - `6b87a51` — `docs(handoff): add WP-06 stable XLSX snapshot acquisition handoff package`
  - `d47a56e` — `fix(local_agent): address Codex review R1-R7 on snapshot acquisition`
  - `2547d14` — `docs(handoff): update WP-06 handoff with Codex review R1-R7 evidence`
  - `d6358d9` — `style(tests): format snapshot acquisition tests to 88 char limit`
  - `3f33c16` — `fix(local_agent): address Codex review round 2 R2-01 to R2-07`
  - `7923ab5` — `docs(handoff): update WP-06 handoff with Codex review round 2 evidence`
  - `1280493` — `docs(handoff): finalize exact commit list in handoff package`
  - `d3afceb` — `fix(local_agent): address Codex review round 3 R3-01 to R3-06`
  - `34d26e0` — `docs(handoff): update WP-06 handoff with Codex review round 3 evidence`
  - `49c2727` — `docs(handoff): finalize exact commit list for round 3 handoff`
  - `19248db` — `style(tests): format test_sa06 comment under 88 chars`
  - `66fa211` — `docs(handoff): synchronize full commit list in handoff package`
  - `b75f7c4` — `fix(local_agent): address Codex review round 4 R4-01 to R4-06`
  - `8130234` — `docs(handoff): update WP-06 handoff with Codex review round 4 evidence`
  - `a5bd904` — `docs(handoff): finalize exact commit list for round 4 handoff`
  - `064e9fb` — `style(tests): format tests/test_xlsx_snapshot_acquisition.py with ruff`
  - `f25fc32` — `docs(handoff): synchronize full commit list in handoff package`
  - `36774fc` — `fix(local_agent): address Codex review round 5 R5-01 to R5-06`
  - `8a6b75d` — `docs(handoff): update WP-06 handoff with Codex review round 5 evidence`
  - `987d404` — `docs(handoff): finalize exact commit list for round 5 handoff`
  - `d3de83e` — `fix(local_agent): address Codex review round 6 R6-01 to R6-05`
  - `6e09c9d` — `docs(handoff): update WP-06 handoff with Codex review round 6 evidence`
  - `3030ecc` — `docs(handoff): finalize exact commit list for round 6 handoff`
  - `ef3c1b7` — `fix(local_agent): address Codex review round 7 R7-01 to R7-05`
  - `09b0299` — `docs(handoff): update WP-06 handoff with Codex review round 7 evidence`
  - `d10492b` — `docs(handoff): finalize exact commit list for round 7 handoff`
  - `a935c08` — `fix(local_agent,tests): address Codex review round 8 R8-01 to R8-04`
  - `2f816b6` — `docs(handoff): update WP-06 handoff with Codex review round 8 evidence`
  - `2373d36` — `docs(handoff): finalize exact commit list for round 8 handoff`
  - `d5afc04` — `fix(local_agent,tests): address Codex review round 9 R9-01 to R9-04`
  - `0258c97` — `docs(handoff): update WP-06 handoff with Codex review round 9 evidence`
  - `7791133` — `docs(handoff): finalize exact commit list for round 9 handoff`
  - `e141bc8` — `fix(local_agent,tests): address Codex review round 10 R10-01 to R10-03`
  - `3ba087a` — `docs(handoff): update WP-06 handoff with Codex review round 10 evidence`
  - `89c40de` — `fix(local_agent,tests): address Codex review round 11 R11-01 to R11-04`
  - `2f19d90` — `fix(local_agent): resolve Ruff B904 exception chaining in win query`
- **Gate G1 Status:** `OPEN / IN PROGRESS` (`REQUEST_CHANGES` pending Codex PR review and merge)

## Scope

Delivers bounded, atomic XLSX snapshot acquisition, exclusive leased consumer access, post-lease integrity verification, and namespace-safe cleanup lifecycle under ADR-0009 and WP-06.
Addresses all Round 11 Codex review remediation directives (R11-01 to R11-04):
1. **R11-01 (Windows Handle Close Error Visibility):** Checked return `BOOL` of `CloseHandle` and raised `OSError` with `get_last_error()` on failure; preserved chained causes on double failure; raised `XlsxSnapshotStorageError` in `finally` if `CloseHandle` fails, preventing yield. Tested with native `HANDLE_FLAG_PROTECT_FROM_CLOSE` test (`test_r11_01`).
2. **R11-02 (Oracle Backend Alignment):** Aligned Linux `renameat2` dispatching on Windows via `_platform_override`; aligned anchor directory hooks after `mkdir()` and before `CreateFileW` / `GetFileInformationByHandleEx`; added dedicated candidate fstat seam `_fstat_candidate_fd`; unified provider fallback identity via `_extract_windows_handle_identity`; handled Windows open file locking vs replace outcome in `test_sa05`.
3. **R11-03 (Honest Symlink Probing):** Probed environment symlink creation privilege honestly via `_can_create_symlinks(tmp_path)` across `test_sa02`, `test_sa05`, `test_sa07`, and `test_sa09`.
4. **R11-04 (Full Benchmark Output):** Wrapped benchmark output with `capsys.disabled()`, outputting full 64-character SHA-256 string visible in standard CI execution.

## Roadmap traceability

- **Decisions Implemented:** O-31 (Isolated snapshot lifecycle), O-53 (Windows Local Agent stack / execution architecture), O-71 (Bounded memory & linear copy).
- **Roadmap Section 5.2 Step 1:** Implements Step 1 (Safe snapshot acquisition). Step 3 is NOT implemented by WP-06 and remains reserved for subsequent integration packages.
- **Evidence Status:** Recorded under `test-results.txt` and `acceptance-matrix.md`.

## Changed files

- `apps/local_agent/src/accounting_local_agent/__init__.py`: Exported `DEFAULT_COPY_CHUNK_SIZE`, `XLSX_SNAPSHOT_ACQUISITION_VERSION`, `StableXlsxSnapshot`, error taxonomy classes, and `open_stable_xlsx_snapshot`.
- `apps/local_agent/src/accounting_local_agent/xlsx_snapshot_acquisition.py`: Versioned context manager with Round 11 review fixes (R11-01 to R11-04).
- `tests/test_xlsx_snapshot_acquisition.py`: Test suite containing 72 tests covering SA-01 to SA-14 and all regression oracles.
- `handoffs/phase-01/wp-06-stable-xlsx-snapshot-acquisition/handoff.md`: Handoff summary and audit traceability.
- `handoffs/phase-01/wp-06-stable-xlsx-snapshot-acquisition/acceptance-matrix.md`: Detailed requirement acceptance matrix.
- `handoffs/phase-01/wp-06-stable-xlsx-snapshot-acquisition/test-results.txt`: Verbatim command outputs, 3-run benchmark logs, and quality scan records.

## Schema and migrations

- None (WP-06 is purely a local filesystem snapshot acquisition and cleanup adapter).

## Commands and exit codes

1. `uv run ruff format --check .` (Exit 0, 60 files formatted)
2. `uv run ruff check .` (Exit 0, all checks passed)
3. `uv run mypy .` (Exit 0, 23 source files checked)
4. `uv run mypy --platform win32 .` (Exit 0, 23 source files checked)
5. `uv run pytest tests/test_xlsx_snapshot_acquisition.py -v` (Exit 0, 70 passed, 2 skipped in 15.61s)
6. `uv run pytest tests/test_xlsx_snapshot_acquisition.py -k "test_sa14_combined_15000_row_benchmark"` (Exit 0, 3 runs: 12.22s, 11.87s, 11.68s duration; 59.82 MiB, 59.85 MiB, 59.82 MiB peak RSS)
7. `uv run pytest -v` (Exit 0, 270 passed, 2 skipped in 38.34s)
8. `git ls-files` check (Exit 0, 92 tracked files, 0 prohibited files)
9. `git grep` sensitive patterns check (Exit 1 failure-on-match -> Exit 0 clean)

## Tests and evidence

- Full repository suite: 270 tests passing cleanly (2 skipped Windows-runtime-only tests).
- SA-01 to SA-14 coverage: 72 dedicated unit, integration, fault-injection, concurrency, hypothesis, and streaming benchmark tests.
- Verbatim execution logs and metrics recorded in `test-results.txt`.

## Assumptions and open items

- **Linux (Native Platform):** Fully verified with clean test suite execution and benchmark profiling under CPython 3.13.15 on Linux.
- **Windows Runtime:** Type checked via `mypy --platform win32` (Exit 0). Runtime execution on Windows remains **PENDING** independent Codex CI execution on PR.
- Downstream orchestration (sync worker lifecycle, Telegram outbox processing) will consume `open_stable_xlsx_snapshot` in subsequent work packages.

## Risks

- Low: Component is isolated behind strict typed context manager boundary; original source workbooks are never written or modified.

## Rollback

- Revert linear commits on `antigravity/phase-01-stable-xlsx-snapshot-acquisition` back to baseline `bc085acd8852e2293fdfcb786a7694fe96e93407`.

## Protected assets

- No real Excel files, COM automation, SQLite databases, Outbox pipelines, production networks, or credentials accessed or modified. All tests use synthetic-only fixtures.

## Stop state

- Ready for Codex review and PR publication. No push, PR creation, or branch merge performed by implementer.
