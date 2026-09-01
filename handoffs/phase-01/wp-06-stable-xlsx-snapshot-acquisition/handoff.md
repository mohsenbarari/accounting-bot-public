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
- **Gate G1 Status:** `OPEN / IN PROGRESS` (`REQUEST_CHANGES` pending Codex PR review and merge)

## Scope

Delivers bounded, atomic XLSX snapshot acquisition, exclusive leased consumer access, post-lease integrity verification, and cleanup lifecycle under ADR-0009 and WP-06.
Addresses all Round 2 Codex review remediation directives (R2-01 to R2-08):
1. **R2-01:** Identity tracking of lease directory, candidate, and final; candidate symlink rejected without touching external targets; pre-existing final snapshot is never overwritten or deleted; cleanup unlinks strictly matching artifacts; unexpected replacements left untouched for forensics.
2. **R2-02:** Initial symlinks rejected with `XlsxSourcePolicyError`; conversion of source to symlink at any race point aborts with `XlsxSourceNotReadyError`; POSIX `O_NOFOLLOW` and handle/path identity verification prevent following symlinks.
3. **R2-03:** Cross-platform st_dev/st_ino/mtime/size checks detect `os.replace` during lease even with 100% identical content; replacement file is preserved on disk with `XlsxSnapshotIntegrityError` and `XlsxSnapshotCleanupError`.
4. **R2-04:** Source read errors mapped to `XlsxSourceNotReadyError` (`retryable=True`); candidate write, short-write, flush, fsync, and promotion errors mapped to `XlsxSnapshotStorageError` (`retryable=False`); underlying causes preserved in chained exceptions.
5. **R2-05:** `_cleanup_managed_artifacts` preserves all underlying `OSError` instances as causes in `XlsxSnapshotCleanupError`; multi-exception combinations (acquisition+cleanup, consumer+cleanup, consumer+integrity+cleanup) verified with group flattening.
6. **R2-06:** `open_stable_xlsx_snapshot` requires `Path` instances for `source_path` and `snapshot_root`; generic sanitized messages ensure secret filenames and directories are never leaked.
7. **R2-07:** Multi-concurrent acquisitions verify SHA parity on identical sources, distinct SHAs on different sources, and independent early exit; full 4-sheet workbook equality verified against independent WP-04 / WP-03 oracle.

## Roadmap traceability

- **Decisions Implemented:** O-31 (Isolated snapshot lifecycle), O-53 (Windows Local Agent stack / execution architecture), O-71 (Bounded memory & linear copy).
- **Roadmap Section 5.2 Step 1:** Implements Step 1 (Safe snapshot acquisition). Step 3 is NOT implemented by WP-06 and remains reserved for subsequent integration packages.
- **Evidence Status:** Recorded under `test-results.txt` and `acceptance-matrix.md`.

## Changed files

- `apps/local_agent/src/accounting_local_agent/__init__.py`: Exported `DEFAULT_COPY_CHUNK_SIZE`, `XLSX_SNAPSHOT_ACQUISITION_VERSION`, `StableXlsxSnapshot`, error taxonomy classes, and `open_stable_xlsx_snapshot`.
- `apps/local_agent/src/accounting_local_agent/xlsx_snapshot_acquisition.py`: Implemented versioned context manager with Round 2 review fixes (R2-01 to R2-06).
- `tests/test_xlsx_snapshot_acquisition.py`: Test suite containing 43 tests covering SA-01 to SA-14 and R2-01 to R2-07 evidence.
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
5. `uv run pytest tests/test_xlsx_snapshot_acquisition.py -v` (Exit 0, 43 passed in 12.91s)
6. `uv run pytest tests/test_xlsx_snapshot_acquisition.py -k "test_sa14_combined_15000_row_benchmark" -s -vv` (Exit 0, 3 runs: 10.82s, 10.72s, 10.67s duration; 59.26 MiB, 58.96 MiB, 59.07 MiB peak RSS)
7. `uv run pytest -v` (Exit 0, 243 passed in 34.13s)
8. `git ls-files` check (Exit 0, 92 tracked files, 0 prohibited files)
9. `git grep` sensitive patterns check (Exit 1 failure-on-match -> Exit 0 clean)

## Tests and evidence

- Full repository suite: 243 tests passing cleanly (zero failures).
- SA-01 to SA-14 coverage: 43 dedicated unit, integration, fault-injection, concurrency, hypothesis, and streaming benchmark tests.
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
