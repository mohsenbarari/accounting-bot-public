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
- **Gate G1 Status:** `OPEN / IN PROGRESS` (`REQUEST_CHANGES` pending Codex PR review and merge)

## Scope

Delivers bounded, atomic XLSX snapshot acquisition, exclusive leased consumer access, post-lease integrity verification, and namespace-safe cleanup lifecycle under ADR-0009 and WP-06.
Addresses all Round 7 Codex review remediation directives (R7-01 to R7-05):
1. **R7-01 (Object-Anchored Ownership):** Directory ownership token is captured immediately upon creation via descriptor anchor / initial stat; retries on path stat are validated against this immutable anchor, preventing adoption of foreign directories.
2. **R7-02 (Namespace-Safe Atomic Quarantine Cleanup):** Replaced TOCTOU check-then-delete patterns with atomic quarantine rename followed by handle verification and unlinking; unproven artifacts are safely preserved or restored, never deleted via wildcards or recursive rm.
3. **R7-03 & R7-04 (Four Blocking Race Tests & Rigorous Oracles):** Implemented all 4 blocking race tests and refined oracles to use real unlinks and simulated identity collision naming.
4. **R7-05 (Technical Integrity):** Preserved two-platform POSIX and Windows support using Python standard library without external dependencies.

## Roadmap traceability

- **Decisions Implemented:** O-31 (Isolated snapshot lifecycle), O-53 (Windows Local Agent stack / execution architecture), O-71 (Bounded memory & linear copy).
- **Roadmap Section 5.2 Step 1:** Implements Step 1 (Safe snapshot acquisition). Step 3 is NOT implemented by WP-06 and remains reserved for subsequent integration packages.
- **Evidence Status:** Recorded under `test-results.txt` and `acceptance-matrix.md`.

## Changed files

- `apps/local_agent/src/accounting_local_agent/__init__.py`: Exported `DEFAULT_COPY_CHUNK_SIZE`, `XLSX_SNAPSHOT_ACQUISITION_VERSION`, `StableXlsxSnapshot`, error taxonomy classes, and `open_stable_xlsx_snapshot`.
- `apps/local_agent/src/accounting_local_agent/xlsx_snapshot_acquisition.py`: Implemented versioned context manager with Round 7 review fixes (R7-01 to R7-05).
- `tests/test_xlsx_snapshot_acquisition.py`: Test suite containing 56 tests covering SA-01 to SA-14 and all 13 Round 7 oracles.
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
5. `uv run pytest tests/test_xlsx_snapshot_acquisition.py -v` (Exit 0, 56 passed in 13.38s)
6. `uv run pytest tests/test_xlsx_snapshot_acquisition.py -k "test_sa14_combined_15000_row_benchmark" -s -vv` (Exit 0, 3 runs: 10.75s, 10.27s, 10.27s duration; 59.20 MiB, 58.97 MiB, 59.18 MiB peak RSS)
7. `uv run pytest -v` (Exit 0, 256 passed in 33.83s)
8. `git ls-files` check (Exit 0, 92 tracked files, 0 prohibited files)
9. `git grep` sensitive patterns check (Exit 1 failure-on-match -> Exit 0 clean)

## Tests and evidence

- Full repository suite: 256 tests passing cleanly (zero failures).
- SA-01 to SA-14 coverage: 56 dedicated unit, integration, fault-injection, concurrency, hypothesis, and streaming benchmark tests.
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
