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
- **Gate G1 Status:** `OPEN / IN PROGRESS` (`REQUEST_CHANGES` pending Codex PR review and merge)

## Scope

Delivers bounded, atomic XLSX snapshot acquisition, exclusive leased consumer access, post-lease integrity verification, and cleanup lifecycle under ADR-0009 and WP-06.
Addresses all Round 4 Codex review remediation directives (R4-01 to R4-06):
1. **R4-01 (Artifact Ownership & Cleanup Without Wildcards):** `_cleanup_managed_artifacts()` requires an exact recorded token (`_FileToken(device, inode)`); `None` is never a deletion wildcard. Foreign directories or pre-existing files are preserved on disk and reported via `XlsxSnapshotCleanupError` (`EARLY_LEASE_FOREIGN_SURVIVED = True`).
2. **R4-02 (Full FD Lifecycle & Zero Leak Verification):** All early error paths (including Candidate `os.fstat` failure) close file descriptors in `try...except` blocks; `XlsxSnapshotStorageError` is raised with `retryable=False`; verified zero FD leaks via `/proc/self/fd` (`CANDIDATE_FSTAT_LEAKED_FDS = []`).
3. **R4-03 (Content & Attestation Before Yield):** After promotion, `final_file` is reopened via no-follow handle, stat-checked, and its full SHA-256 digest re-verified against candidate digest; post-promotion swaps are detected before yield (`POST_PROMOTION_YIELDED = False`, `POST_PROMOTION_FOREIGN_SURVIVED = True`, `POST_ZIP_MUTATION_WAS_YIELDED = False`).
4. **R4-04 (Fail-if-exists Atomic Promotion Without Overwriting):** `_promote_candidate_atomic_fail_if_exists` uses `os.link` / `os.rename` with true fail-if-exists semantics; pre-existing foreign files at target path trigger `XlsxSnapshotStorageError` and are never overwritten (`PROMOTION_OVERWROTE_FOREIGN = False`).
5. **R4-05 (Zero Sentinels Removed & Exact Mtime 0 Integrity):** `mtime_ns = 0` is treated as a valid timestamp and strictly verified against lease mutations; identity unavailable conditions operate cleanly without false positives.
6. **R4-06 (Evidence & Oracle Alignment):** All 6 required oracles implemented, asserted, and verified in dedicated test cases.

## Roadmap traceability

- **Decisions Implemented:** O-31 (Isolated snapshot lifecycle), O-53 (Windows Local Agent stack / execution architecture), O-71 (Bounded memory & linear copy).
- **Roadmap Section 5.2 Step 1:** Implements Step 1 (Safe snapshot acquisition). Step 3 is NOT implemented by WP-06 and remains reserved for subsequent integration packages.
- **Evidence Status:** Recorded under `test-results.txt` and `acceptance-matrix.md`.

## Changed files

- `apps/local_agent/src/accounting_local_agent/__init__.py`: Exported `DEFAULT_COPY_CHUNK_SIZE`, `XLSX_SNAPSHOT_ACQUISITION_VERSION`, `StableXlsxSnapshot`, error taxonomy classes, and `open_stable_xlsx_snapshot`.
- `apps/local_agent/src/accounting_local_agent/xlsx_snapshot_acquisition.py`: Implemented versioned context manager with Round 4 review fixes (R4-01 to R4-06).
- `tests/test_xlsx_snapshot_acquisition.py`: Test suite containing 50 tests covering SA-01 to SA-14 and R4-01 to R4-06 evidence.
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
5. `uv run pytest tests/test_xlsx_snapshot_acquisition.py -v` (Exit 0, 50 passed in 14.53s)
6. `uv run pytest tests/test_xlsx_snapshot_acquisition.py -k "test_sa14_combined_15000_row_benchmark" -s -vv` (Exit 0, 3 runs: 10.88s, 10.37s, 10.92s duration; 59.19 MiB, 58.98 MiB, 58.94 MiB peak RSS)
7. `uv run pytest -v` (Exit 0, 250 passed in 33.27s)
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
