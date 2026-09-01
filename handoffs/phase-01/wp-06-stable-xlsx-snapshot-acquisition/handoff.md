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
- **Gate G1 Status:** `OPEN / IN PROGRESS` (`REQUEST_CHANGES` pending Codex PR review and merge)

## Scope

Delivers bounded, atomic XLSX snapshot acquisition, exclusive leased consumer access, post-lease integrity verification, and cleanup lifecycle under ADR-0009 and WP-06.
Addresses all Round 3 Codex review remediation directives (R3-01 to R3-06):
1. **R3-01:** `lease_created`, `part_created`, `final_promoted` states explicitly track lifecycle; non-recorded identities are `None`; pre-existing foreign `snapshot.part` is never deleted during cleanup.
2. **R3-02:** Cleanup begins immediately after successful `mkdir`; early failure on `chmod` or `lstat` cleans up the newly created lease directory without leaving orphaned folders.
3. **R3-03:** All secondary errors in `_open_source_nofollow` mapped to `XlsxSourceNotReadyError`; Candidate `fstat`/open errors mapped to `XlsxSnapshotStorageError`; short write or `None` write returns mapped to `XlsxSnapshotStorageError`; zero FD leaks verified across success, consumer crashes, and faults via `/proc/self/fd`.
4. **R3-04:** Promoted file is attested without following symlinks and compared to recorded candidate identity; `snapshot_path` is constructed safely without resolving through leaf symlinks.
5. **R3-05:** `mtime_ns = 0` (Epoch 1970) is treated as a valid timestamp and strictly verified; modifications during lease trigger `XlsxSnapshotIntegrityError`.
6. **R3-06:** ZIP Central Directory validation executed on verified Candidate handle with nofollow; real flush failure verified via stream wrapper.

## Roadmap traceability

- **Decisions Implemented:** O-31 (Isolated snapshot lifecycle), O-53 (Windows Local Agent stack / execution architecture), O-71 (Bounded memory & linear copy).
- **Roadmap Section 5.2 Step 1:** Implements Step 1 (Safe snapshot acquisition). Step 3 is NOT implemented by WP-06 and remains reserved for subsequent integration packages.
- **Evidence Status:** Recorded under `test-results.txt` and `acceptance-matrix.md`.

## Changed files

- `apps/local_agent/src/accounting_local_agent/__init__.py`: Exported `DEFAULT_COPY_CHUNK_SIZE`, `XLSX_SNAPSHOT_ACQUISITION_VERSION`, `StableXlsxSnapshot`, error taxonomy classes, and `open_stable_xlsx_snapshot`.
- `apps/local_agent/src/accounting_local_agent/xlsx_snapshot_acquisition.py`: Implemented versioned context manager with Round 3 review fixes (R3-01 to R3-06).
- `tests/test_xlsx_snapshot_acquisition.py`: Test suite containing 50 tests covering SA-01 to SA-14 and R3-01 to R3-06 evidence.
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
5. `uv run pytest tests/test_xlsx_snapshot_acquisition.py -v` (Exit 0, 50 passed in 13.43s)
6. `uv run pytest tests/test_xlsx_snapshot_acquisition.py -k "test_sa14_combined_15000_row_benchmark" -s -vv` (Exit 0, 3 runs: 11.83s, 10.69s, 10.60s duration; 59.02 MiB, 58.57 MiB, 58.99 MiB peak RSS)
7. `uv run pytest -v` (Exit 0, 250 passed in 33.61s)
8. `git ls-files` check (Exit 0, 92 tracked files, 0 prohibited files)
9. `git grep` sensitive patterns check (Exit 1 failure-on-match -> Exit 0 clean)

## Tests and evidence

- Full repository suite: 250 tests passing cleanly (zero failures).
- SA-01 to SA-14 coverage: 50 dedicated unit, integration, fault-injection, concurrency, hypothesis, and streaming benchmark tests.
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
