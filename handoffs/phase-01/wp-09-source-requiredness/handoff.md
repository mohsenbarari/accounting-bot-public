# Handoff: WP-09 Required-Field Preflight for Raw Source Snapshots

## Identity

- **Phase:** 1 — source and data-model foundation
- **Work Package:** WP-09 Required-field preflight for raw source snapshots
- **Branch/worktree:** `antigravity/phase-01-source-requiredness`
- **Execution Baseline:** `d59e20accfa57cfc67de8a8ca6d31deba8a6d405` (PR #31 merged into main)
- **Tested Code Commit SHA:** `46020d6f45a4af66cc52fe660829516c5c792c98`
- **Implementer:** Google Antigravity
- **Reviewer:** Codex Project Manager
- **Gate G1 Status:** `OPEN / IN PROGRESS`

## Scope

### Requested outcome

Implement the pure, deterministic `source-requiredness.v1` preflight defined by ADR-0012. Given an immutable four-sheet `ValidatedSourceWorkbookSnapshot`, evaluate and report all missing and blank required fields deterministically while retaining every original row, value, and source hash. Distinguish missing and blank input from valid numeric zero, and keep optional notes, contact information, and item-dependent purity optional at this boundary.

### In scope

1. **Public API & Version:** Implemented and exported `SOURCE_REQUIREDNESS_VERSION = "source-requiredness.v1"`, `SourceRequirednessInputError`, `SourceRequirednessIssueReason`, `SourceRequirednessIssue`, `SourceRequirednessReport`, and `evaluate_source_requiredness` through `accounting_contracts`.
2. **Authoritative Requiredness Matrix:** Enforced ADR-0012 required fields per sheet in exact raw-column order:
   - `خرید-فروش`: `date_raw`, `party_name_raw`, `transaction_type_raw`, `item_name_raw`, `quantity_raw`, `unit_price_toman_raw` (optional: `discount_toman_raw`, `notes_raw`).
   - `دریافت-پرداخت`: `date_raw`, `party_name_raw`, `entry_type_raw`, `amount_toman_raw` (optional: `notes_raw`, `account_code_raw`, `customer_flag_raw`).
   - `ورود-خروج`: `date_raw`, `party_name_raw`, `movement_type_raw`, `item_name_raw`, `quantity_raw` (optional: `purity_raw`, `notes_raw`, `customer_flag_raw`).
   - `لیست کسبه`: `party_name_raw` (optional: `phone_number_raw`).
3. **Exact Presence Rules:**
   - `None` for a required field yields `SourceRequirednessIssueReason.MISSING_VALUE`.
   - String with empty `str.strip()` yields `SourceRequirednessIssueReason.BLANK_TEXT` (validated strictly for text fields).
   - Numeric zero (`0`, `"0"`, `Decimal("0")`), negative values, and non-empty text count as present.
   - Upstream dates: null date yields `MISSING_VALUE`; invalid non-null dates fail upstream during snapshot validation.
4. **Deterministic Issue Aggregation & Ordering:** Issues are aggregated across all four sheets ordered strictly by registry sheet order, UUID bytes, and raw-column definition order in O(N) time and O(K) space.
5. **Masking & Security:** `SourceRequirednessReport` excludes `snapshot` from `repr`. `SourceRequirednessIssue` stores no raw cell values. `SourceRequirednessInputError` uses fixed error messages without interpolating raw values, paths, names, amounts, or contact details.
6. **Tamper Resistance:** `SourceRequirednessReport` derives all counts and issues internally from the validated snapshot; callers cannot supply fabricated counts or status. Report and issue classes are slotted and frozen.
7. **Comprehensive Tests (SR-01 to SR-14):** 14 unit and integration tests covering public signatures, every required field of each sheet, text/zero/optional handling, mixed snapshots, four empty sheets, constructor validation, error masking, purity/repeatability, XLSX Reader integration with formula exclusion, independent property oracle under permutations, 15,000-row linear benchmark (< 0.15s), and contract regression safety.

### Out of scope

- No full financial validation, transaction code semantics, or fiscal year / archive eligibility rules.
- No party or item resolution, phone format validation, or fuzzy matching. Unresolved nonblank rows are retained.
- No RS pairing or structure verification.
- No permission to commit (`passes_requiredness` is an admission prerequisite, not import commit authorization).
- No connection to SQLite, Outbox, Watcher runtime callbacks, Ledger, or reports.
- No mutation of accepted code or tests in WP-02 through WP-08.
- No reference workbook modification, COM automation, or real accounting data.

## Roadmap traceability

| Roadmap section / O-item | Approved status | Implemented behavior |
|---|---|---|
| Roadmap 5.2 step 3 & 5.4 | Active / G1 In Progress | Pure required-field preflight rejects incomplete raw records before import commit. |
| Sections 6/7, O-18, O-33 | Approved | Required amount, price, item, and quantity; numeric zero distinguished from null/blank. |
| O-60, O-04 | Approved | Blank notes permitted for C, D, H, HA, HS without emitting requiredness issues. |
| O-16, O-17, O-32 | Approved | Unresolved nonblank raw rows and RS rows preserved without rejection at this boundary. |
| O-68, O-69, O-70 | Approved | Immutable snapshots, UUIDv7 identities, canonical typing, and source hashes preserved untouched. |
| O-74, ADR-0012 | Accepted | `source-requiredness.v1` module in `accounting-contracts` implementing the pure required-field preflight. |
| O-46 through O-51 | Approved | Independent Codex acceptance, bounded changes, public-artifact safety, and verified scratch rollback. |

## Changed files

| File | Change | Reason |
|---|---|---|
| `packages/contracts/src/accounting_contracts/source_requiredness.py` | New module | Implements `source-requiredness.v1`, issues, reports, and evaluator under ADR-0012. |
| `packages/contracts/src/accounting_contracts/__init__.py` | Modified | Exports `SOURCE_REQUIREDNESS_VERSION`, error, enum, issue, report, and evaluator. |
| `packages/contracts/README.md` | Modified | Documents `source_requiredness` preflight, presence rules, masking, and boundaries. |
| `tests/test_source_requiredness.py` | New test file | 14 test functions covering SR-01 through SR-14, reader integration, oracle, and 15k benchmark. |
| `handoffs/phase-01/wp-09-source-requiredness/acceptance-matrix.md` | New handoff file | Detailed acceptance matrix for SR-01..SR-14 with explicit test node IDs. |
| `handoffs/phase-01/wp-09-source-requiredness/test-results.txt` | New handoff file | Raw command outputs, test run logs, static type checks, benchmarks, and rollback proof. |
| `handoffs/phase-01/wp-09-source-requiredness/handoff.md` | New handoff file | Handoff metadata, scope, traceability, commands, risks, and rollback instructions. |

## Schema and migrations

- Schema impact: None (WP-09 is a pure domain contracts validation layer).
- Migration files: None.
- Backward compatibility: Fully backward compatible. All existing contracts, types, hashes, and change planning functions remain unchanged.
- Data migration/real data used: None. Synthetic fixtures only.

## Commands and exit codes

| Command | Exit code | Purpose |
|---|---:|---|
| `uv sync --frozen --all-packages --all-groups` | 0 | Verify frozen dependency lock synchronization (81 packages). |
| `uv lock --check` | 0 | Verify lockfile integrity against pyproject.toml (88 packages). |
| `uv run pytest tests/test_source_requiredness.py -v` | 0 | Run dedicated WP-09 unit and integration suite (14 passed in 3.88s). |
| `uv run ruff format --check .` | 0 | Verify code formatting compliance across all 79 files. |
| `uv run ruff check .` | 0 | Run ruff linter checks across all 79 files (0 warnings/errors). |
| `uv run mypy .` | 0 | Static type safety check on Linux target across 30 source files (0 errors). |
| `uv run mypy --platform win32 .` | 0 | Static type safety check on Win32 target across 30 source files (0 errors). |
| `uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s` | 0 | WP-05 streaming reader benchmark: 11.00s / 62.14 MiB (< 15s / 128 MiB). |
| `uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s` | 0 | WP-06 acquisition benchmark: 11.12s / 61.63 MiB (< 15s / 128 MiB). |
| `uv run pytest -v -k "not test_bench"` | 0 | Full repository regression test suite: 362 passed, 2 skipped in 64.69s. |
| `git diff --check origin/main...HEAD` | 0 | Verify zero whitespace errors against origin/main. |
| `git ls-files \| grep -E "\.(env\|key\|pem\|pfx\|p12\|kdbx\|sqlite\|db)$"` | 0 | Security audit scan for prohibited sensitive file extensions (0 found). |
| `git grep -i -E "(BEGIN PRIVATE KEY\|AKIA[0-9A-Z]{16}\|ghp_[0-9a-zA-Z]{36})"` | 0 | Security audit scan for leaked private keys and tokens (0 found). |
| `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-09-source-requiredness` | 0 | Validate completeness and format of handoff package. |

## Tests and evidence

- **Full repository suite:** 362 tests passing cleanly on Linux native (2 platform-conditional skipped Windows-handle tests; 364 collected total).
- **WP-09 Unit & Integration suite:** 14 dedicated tests passing in 3.88s in `tests/test_source_requiredness.py`.
- **Streaming Benchmarks (15,000 rows):** WP-05 Reader at 11.00s / 62.14 MiB peak RSS; WP-06 Acquisition at 11.12s / 61.63 MiB peak RSS (strictly within 15.0s / 128.0 MiB ceilings).
- **Platform Separation:**
  - **Linux Local:** PASS (all 14 WP-09 tests and 362 monorepo tests pass natively).
  - **Windows Local:** NOT RUN (Linux host development environment; static type safety validated via `mypy --platform win32 .` with 0 errors in 30 source files).
  - **Windows CI:** PENDING automated GitHub Actions CI execution upon PR submission.
- Direct execution logs, benchmark metrics, and scratch rollback verification are recorded in `test-results.txt`.

## Assumptions and open items

- **Pure Evaluation Layer:** `evaluate_source_requiredness` is a stateless, pure library function that performs no filesystem I/O, spawns no background threads, and accesses no clocks or random sources.
- **Single Snapshot Evaluation:** The evaluator assumes the input is an already-validated `ValidatedSourceWorkbookSnapshot` (as produced by WP-04 / WP-05). Upstream structural violations (missing sheets, malformed UUIDs, invalid non-null dates) continue to fail upstream during snapshot construction.
- **Downstream Boundary:** A passing preflight report (`passes_requiredness=True`) indicates solely that essential required raw cells are present and non-blank. Full fiscal validation, entity resolution, financial balance, and transaction ownership must be verified in future packages before an import commit may proceed.

## Risks

- **Premature Persistence Risk:** If a downstream developer attempts to use `passes_requiredness=True` as authorization to commit directly to a database, incomplete fiscal/entity checks would be bypassed. Mitigated by explicit ADR-0012 design: `SourceRequirednessReport` does not expose `commit_allowed` or `import_valid`, and contains no persistence primitives.
- **Unicode Whitespace Edge Cases:** If users input exotic Unicode spaces (such as em-spaces or non-breaking spaces) in required text cells, standard `str.strip()` correctly identifies them as blank text, emitting `BLANK_TEXT`. Tested explicitly in SR-03.

## Rollback

To revert this work package cleanly and safely without blind file deletions or destructive checkouts:

1. **Verify working tree cleanliness:**
   ```bash
   git status
   ```
   Ensure no uncommitted modifications exist.

2. **Reverting Changes by Fixed Scope:**
   - **Scope: Revert WP-09 Code Implementation Commit:**
     To back out the tested code commit `46020d6f45a4af66cc52fe660829516c5c792c98` back to baseline `d59e20accfa57cfc67de8a8ca6d31deba8a6d405`:
     ```bash
     git revert --no-edit 46020d6f45a4af66cc52fe660829516c5c792c98
     ```

   - **Verified Scratch Branch Procedure (Confirmed Zero Diff Against Baseline):**
     This rollback procedure was verified on temporary branch `scratch/verify-rollback`. Reverting commit `46020d6f45a4af66cc52fe660829516c5c792c98` produced an exact zero diff against baseline `d59e20accfa57cfc67de8a8ca6d31deba8a6d405`:
     ```bash
     # Create scratch branch from fixed tested SHA
     git checkout -b scratch/verify-rollback 46020d6f45a4af66cc52fe660829516c5c792c98

     # Apply the revert
     git revert --no-edit 46020d6f45a4af66cc52fe660829516c5c792c98

     # Verify that working tree is identical to baseline (exited with code 0)
     git diff --quiet d59e20accfa57cfc67de8a8ca6d31deba8a6d405

     # Return to target branch and delete scratch branch
     git checkout antigravity/phase-01-source-requiredness
     git branch -D scratch/verify-rollback
     ```

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation, test suites (SR-01 to SR-14), benchmarks, static typing, verified scratch rollback, and handoff documentation for WP-09 are completed and stopped for independent Codex review. Gate G1 remains `OPEN / IN PROGRESS`. No Gate approval, merge, push, deploy, or next Work Package has been performed.
