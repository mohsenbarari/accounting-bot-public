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
- **Gate G1 Status:** `OPEN / IN PROGRESS` (`REQUEST_CHANGES` pending Codex PR review and merge)

## Scope

Delivers bounded, atomic XLSX snapshot acquisition, exclusive leased consumer access, post-lease integrity verification, and cleanup lifecycle under ADR-0009 and WP-06.
Addresses all Round 1 Codex review remediation directives (R1–R7):
1. **R1:** Elimination of `ZipFile.testzip()` and member decoding during acquisition; isolated Central Directory structure and `[Content_Types].xml` verification.
2. **R2:** Elimination of swallowed `OSError` on flush/fsync; strict typed taxonomy mapping storage and I/O faults to non-retryable `XlsxSnapshotStorageError` and `XlsxSnapshotIntegrityError`.
3. **R3:** Full retention of coincident acquisition/consumer, integrity, and cleanup errors via native Python 3.13 `ExceptionGroup` and `BaseExceptionGroup`.
4. **R4:** Private POSIX permissions (`0700` lease directory, `0600` files) and immediate pre-open rejection of non-regular files (FIFO, socket, character/block device, directory) with `XlsxSourcePolicyError`.
5. **R5:** True atomic replacement detection via recorded file identity (`st_dev`, `st_ino`, `st_size`, `st_mtime_ns`) and post-lease checks catching `os.replace` (even with identical content) and symlink swaps.
6. **R6:** Sanitized generic error messages and `repr()` strings preventing leakage of confidential file names, paths, or raw OS error text.
7. **R7:** Expanded test evidence for locked sources, bounded streams (`_copy_chunk_size`, no `read(-1)`), multi-concurrent acquisitions, and full 4-sheet workbook parity against independent WP-04 / WP-03 oracle.

## Roadmap traceability

- **Decisions Implemented:** O-31 (Isolated snapshot lifecycle), O-53 (Two observations before copy), O-71 (Bounded memory & linear copy).
- **Evidence Status:** Recorded under `test-results.txt` and `acceptance-matrix.md`.
- **Roadmap Section 5.2 Step 3:** Prepared and enabled for Reader / Local Agent integration; WP-06 scope is strictly bounded snapshot acquisition adapter.

## Changed files

- `apps/local_agent/src/accounting_local_agent/__init__.py`: Exported `DEFAULT_COPY_CHUNK_SIZE`, `XLSX_SNAPSHOT_ACQUISITION_VERSION`, `StableXlsxSnapshot`, error taxonomy classes, and `open_stable_xlsx_snapshot`.
- `apps/local_agent/src/accounting_local_agent/xlsx_snapshot_acquisition.py`: Implemented versioned context manager with R1–R6 review fixes.
- `tests/test_xlsx_snapshot_acquisition.py`: Comprehensive test suite containing 39 tests covering SA-01 to SA-14 and R1–R7 evidence.
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
5. `uv run pytest tests/test_xlsx_snapshot_acquisition.py -v` (Exit 0, 39 passed in 13.16s)
6. `uv run pytest tests/test_xlsx_snapshot_acquisition.py -k "test_sa14_combined_15000_row_benchmark" -s -vv` (Exit 0, 3 runs: 10.83s, 10.00s, 10.31s duration; 58.68 MiB, 59.03 MiB, 58.77 MiB peak RSS)
7. `uv run pytest -v` (Exit 0, 239 passed in 34.80s)
8. `git ls-files` check (Exit 0, 92 tracked files, 0 prohibited files)
9. `git grep` sensitive patterns check (Exit 1 failure-on-match -> Exit 0 clean)

## Tests and evidence

- Full repository suite: 239 tests passing cleanly.
- SA-01 to SA-14 coverage: 39 dedicated unit, integration, fault-injection, concurrency, hypothesis, and streaming benchmark tests.
- Verbatim execution logs and metrics recorded in `test-results.txt`.

## Assumptions and open items

- Windows runtime behavior and benchmark performance are verified via cross-platform typing and will be executed by Codex in Windows CI environment.
- Downstream orchestration (sync worker lifecycle, Telegram outbox processing) will consume `open_stable_xlsx_snapshot` in subsequent work packages.

## Risks

- Low: Component is isolated behind strict typed context manager boundary; original source workbooks are never written or modified.

## Rollback

- Revert linear commits on `antigravity/phase-01-stable-xlsx-snapshot-acquisition` back to baseline `bc085acd8852e2293fdfcb786a7694fe96e93407`.

## Protected assets

- No real Excel files, COM automation, SQLite databases, Outbox pipelines, production networks, or credentials accessed or modified. All tests use synthetic-only fixtures.

## Stop state

- Ready for Codex review and PR publication. No push, PR creation, or branch merge performed by implementer.
