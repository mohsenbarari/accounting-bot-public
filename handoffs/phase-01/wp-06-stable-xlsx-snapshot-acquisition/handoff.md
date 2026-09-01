# Handoff

## Identity

- Phase: 1 — source and data-model foundation (Phase 1 authorized, Gate G1 is open / in-progress)
- Work Package: WP-06: Stable XLSX snapshot acquisition and cleanup
- Branch/worktree: antigravity/phase-01-stable-xlsx-snapshot-acquisition
- Commit(s):
  - `84d6cfa` (feat(local_agent): implement stable XLSX snapshot acquisition and cleanup (WP-06))
  - `232d55f` (docs(handoff): add WP-06 stable XLSX snapshot acquisition handoff package)
- Implementer: Google Antigravity
- Reviewer: Codex

## Scope

### Requested outcome

Implement the bounded adapter that converts one caller-supplied, exact operational `.xlsx` path into one verified, immutable-for-the-lease temporary snapshot. Prove that WP-05 reads only that copy, that a concurrent source change cannot produce a partial result and that all known temporary artifacts are removed after ordinary success or controlled failure.

This delivery implements the normative requirements of ADR-0009 and WP-06:
- Two ordered file observations separated by an explicit positive finite interval before copying.
- Bounded-memory streaming copy to an unpredictable private child directory in the controlled snapshot root with exclusive `.part` candidate creation.
- SHA-256 calculation and byte count verification against observed source length.
- Independent source and candidate reverification passes comparing handle metadata, path stat, byte count, and digest.
- Readable ZIP container verification checking for `[Content_Types].xml` and valid central directory before promotion.
- Atomic promotion from `.part` to `.xlsx` inside the private lease directory.
- Post-lease integrity check and controlled cleanup of exact managed artifacts on context exit.
- Structured exception chaining when consumer and cleanup/integrity errors coincide.
- Typed error taxonomy with exact machine-readable reasons and derived `retryable` classification (`source_not_ready` is True; `source_policy_violation`, `snapshot_storage_failure`, `snapshot_integrity_failure`, `snapshot_cleanup_failure` are False).

### In scope

- Public module `apps/local_agent/src/accounting_local_agent/xlsx_snapshot_acquisition.py` containing:
  - Version constant `XLSX_SNAPSHOT_ACQUISITION_VERSION = "xlsx-snapshot-acquisition.v1"`.
  - Immutable dataclass `StableXlsxSnapshot` with slots, frozen attributes, and invariant validation.
  - Reason enum `XlsxSnapshotAcquisitionReason`.
  - Typed exceptions: `XlsxSnapshotAcquisitionError`, `XlsxSourceNotReadyError`, `XlsxSourcePolicyError`, `XlsxSnapshotStorageError`, `XlsxSnapshotIntegrityError`, `XlsxSnapshotCleanupError`.
  - Main context manager `open_stable_xlsx_snapshot(source_path, snapshot_root, observation_interval_seconds)`.
- Public exports in `apps/local_agent/src/accounting_local_agent/__init__.py`.
- Documentation in `apps/local_agent/README.md`.
- Comprehensive test suite in `tests/test_xlsx_snapshot_acquisition.py` covering criteria SA-01 through SA-14 (22 tests), including Hypothesis property tests, fault-injection race tests, and combined 15,000 active row benchmark.
- Handoff package and acceptance evidence.

### Out of scope

- Opening, reading, copying or modifying any actual/reference Excel file or live database.
- Directory watching, file-system event monitoring, or the two-second debounce / coalescing coordinator.
- Excel COM automation, UUID generation/writeback, or workbook repair.
- Business rule validation, accounting accounts, ledger entries, SQLite/PostgreSQL persistence or network APIs.
- Real phone numbers, real credentials, or production deployment.
- Merging to `main`, pushing to origin, or opening PR.

## Roadmap traceability

| Roadmap section / O-item | Approved status | Implemented behavior |
|---|---|---|
| Section 5.1 / steps 1–2 | In-progress (🧪) | Two-observation stability check, bounded streaming copy, independent verification, atomic promotion |
| Section 5.2 / step 3 | In-progress (🧪) | Isolated snapshot lease consumed by WP-05 reader and change planner |
| Section 19.1 | In-progress (🧪) | All-or-nothing acquisition, zero partial reads, zero false voids |
| O-31, O-53, O-71 | In-progress (🧪) | Context-managed snapshot lease, SHA-256 content audit, exact artifact cleanup |
| ADR-0002, ADR-0009 | In-progress (🧪) | Local agent snapshot boundary, typed error taxonomy, retryable classification |
| O-46 to O-51 | In-progress (🧪) | Bounded work package, synthetic-only evidence, handoff validator, independent review |

## Changed files

| File | Change | Reason |
|---|---|---|
| `apps/local_agent/src/accounting_local_agent/xlsx_snapshot_acquisition.py` | New | Implemented stable XLSX snapshot acquisition, bounded copy, reverification, container check, atomic promotion, lease lifecycle, and cleanup. |
| `apps/local_agent/src/accounting_local_agent/__init__.py` | Modified | Exported public snapshot acquisition types, constants, reasons, exceptions, and context manager. |
| `apps/local_agent/README.md` | Modified | Documented snapshot acquisition architecture, public API, and execution strategy. |
| `tests/test_xlsx_snapshot_acquisition.py` | New | Comprehensive test suite covering SA-01 to SA-14, Hypothesis properties, race injections, and combined 15k-row benchmark. |
| `handoffs/phase-01/wp-06-stable-xlsx-snapshot-acquisition/*` | New | Handoff document, acceptance matrix, and captured test execution logs. |

## Schema and migrations

- Schema impact: none
- Migration files: none
- Backward compatibility: fully backward-compatible addition; does not alter existing contracts or WP-05 behavior
- Data migration/real data used: none

## Commands and exit codes

| Command | Exit code | Purpose |
|---|---:|---|
| `uv --version` | 0 | Verify uv version 0.12.7 |
| `/root/.local/bin/uv lock --check` | 0 | Verify lockfile consistency |
| `/root/.local/bin/uv sync --frozen --all-packages --all-groups` | 0 | Verify frozen dependencies installation |
| `/root/.local/bin/uv run ruff format --check .` | 0 | Verify formatting compliance across 58 files |
| `/root/.local/bin/uv run ruff check .` | 0 | Verify linting rules compliance |
| `/root/.local/bin/uv run mypy .` | 0 | Verify strict static typing on native Linux across 23 source files |
| `/root/.local/bin/uv run mypy --platform win32 .` | 0 | Verify strict static typing for target Windows platform across 23 source files |
| `/root/.local/bin/uv run pytest tests/test_xlsx_snapshot_acquisition.py -v` | 0 | Execute 22 focused snapshot acquisition unit and integration tests (22 passed in 14.15s) |
| `/root/.local/bin/uv run pytest tests/test_xlsx_snapshot_acquisition.py -k "test_sa14_combined_15000_row_benchmark" -s -vv` | 0 | Execute 3 clean subprocess combined benchmark repetitions under CPython 3.13.15 (11.95s/58.86M, 11.61s/59.17M, 11.35s/59.24M) |
| `/root/.local/bin/uv run pytest -v` | 0 | Execute full repository test suite (222 passed in 36.84s) |
| `git diff --check origin/main...HEAD` | 0 | Verify clean diff with zero whitespace defects |
| `python3 -c "import subprocess, sys; res = subprocess.run(['git', 'ls-files'], capture_output=True, text=True); (print('ERROR: git ls-files failed:\n' + res.stderr) or sys.exit(res.returncode)) if res.returncode != 0 else None; prohibited = ('.xlsx', '.xls', '.xlsm', '.sqlite', '.sqlite3', '.db', '.pdf', '.key', '.pem', '.env'); files = [line.strip() for line in res.stdout.splitlines() if line.strip()]; bad = [f for f in files if f.lower().endswith(prohibited)]; (print('PROHIBITED TRACKED FILES FOUND:\n' + '\n'.join(bad)) or sys.exit(1)) if bad else print('PASS: No prohibited tracked files found (checked ' + str(len(files)) + ' tracked files)')"` | 0 | Verify zero forbidden tracked binary/database/secret files in git |
| `python3 -c "import subprocess, sys; res = subprocess.run(['git', 'grep', '-n', '-I', '-i', '-E', r'(password\s*[:=]|secret\s*[:=]|bearer\s+[A-Za-z0-9]|BEGIN RSA|BEGIN OPENSSH|09[0-9]{9})', '--', ':!ROADMAP.md', ':!docs/adr/*', ':!.agents/*', ':!handoffs/*', ':!uv.lock'], capture_output=True, text=True); (print('PASS: No sensitive patterns detected (grep exit 1)') or sys.exit(0)) if res.returncode == 1 else ((print('FAIL: Found sensitive patterns:\n' + res.stdout) or sys.exit(1)) if res.returncode == 0 else (print('ERROR: git grep failed:\n' + res.stderr) or sys.exit(res.returncode)))"` | 0 | Verify zero sensitive credentials, tokens, private keys, or Iranian mobile phone numbers |
| `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-06-stable-xlsx-snapshot-acquisition/` | 0 | Validate completeness and structure of handoff package |

## Tests and evidence

- Acceptance evidence is mapped in `acceptance-matrix.md`.
- Raw command results are recorded in `test-results.txt`.
- Additional artifact paths: none

## Assumptions and open items

- Exact Caller Path: The caller supplies the exact `.xlsx` file path; directory scanning, file pattern matching, and conflict-copy sibling resolution are excluded.
- Controlled Root Directory: The caller provides an existing, controlled snapshot root directory for temporary lease allocations.
- Fixed Snapshot Lease: The snapshot exists only for the lifetime of the context; WP-05 receives the managed path rather than the operational source path.
- Non-Recursive Cleanup: Only the exact files and private lease directory created by an acquisition are removed; the caller's root is never deleted recursively.

## Risks

- File Locking Under Windows OneDrive: Real OneDrive and Excel file-sharing locks during user editing will require appropriate sharing-mode flags and coordinator debounce (to be addressed in subsequent coordinator work packages).
- Crash-Orphaned Lease Cleanup: If the local agent process is killed abnormally (`SIGKILL` or power loss), temporary files created in `snapshot_root` will remain until a future startup scavenging routine reaps them.

## Rollback

1. Delete the feature branch `antigravity/phase-01-stable-xlsx-snapshot-acquisition`.
2. Checkout `main` branch to restore the repository to its clean baseline `bc085acd8852e2293fdfcb786a7694fe96e93407`.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation is stopped pending independent Codex review. Gate G1 remains OPEN / IN PROGRESS and WP-06 is submitted for review. No Gate approval, merge, push, deploy, or next Work Package has been performed.
