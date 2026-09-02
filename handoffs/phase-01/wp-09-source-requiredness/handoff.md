# Handoff: WP-09 Required-Field Preflight for Raw Source Snapshots (Round 1 Correction)

## Identity

- **Phase:** 1 — source and data-model foundation
- **Work Package:** WP-09 Required-field preflight for raw source snapshots (Round 1 Bounded Correction)
- **Branch/worktree:** `antigravity/phase-01-source-requiredness`
- **Execution Baseline:** `d59e20accfa57cfc67de8a8ca6d31deba8a6d405` (PR #31 merged into main)
- **Prior Code Commit:** `46020d6f45a4af66cc52fe660829516c5c792c98`
- **Prior Delivery HEAD:** `cea3cf67baa73ff50f4a5783689506f84d8ef40e`
- **Tested Code Commit SHA:** `f142de97aec65242be8994a6b8fd73537c1f0c12`
- **Implementer:** Google Antigravity
- **Reviewer:** Codex Project Manager
- **Gate G1 Status:** `OPEN / IN PROGRESS`

## Scope

### Requested outcome

Address the four bounded correction items (R1 through R4) requested by Codex independent review on delivery `cea3cf67baa73ff50f4a5783689506f84d8ef40e`:
1. **R1 — Enforce actual reason enum type:** Replace reason coercion with strict type validation `type(self.reason) is not SourceRequirednessIssueReason`. Both canonical strings and foreign StrEnum members raise `SourceRequirednessInputError`. Genuine members are retained by identity. Restriction of `BLANK_TEXT` to required text fields is preserved.
2. **R2 — Independent matrix and real property scenarios:** Write complete approved required-fields matrix independently in test code covering all 16 entries without importing product maps. Add real property testing under sheet, row, and mapping-key permutations with inverted UUID order, single-value transitions, exact raw/hash retention, and Hypothesis randomized combinations. Include controlled mutation proof demonstrating that omitting buy/sell `transaction_type_raw` is caught immediately.
3. **R3 — Complete issued acceptance evidence:** Implement side-effect guard in SR-10 forbidding filesystem, network, clock, and UUID generation during evaluation; test optional fields individually and together plus C/D/H/HA/HS with blank notes in SR-05; directly construct report on failing snapshot in SR-08; test invalid supplied args and marker-bearing metadata in SR-09 with non-empty issues assertion; assert read-only byte preservation in SR-11; separate fixture timing from evaluator timing in SR-13 with no invented ceilings; cite upstream tests for negative cases in SR-04, SR-07, and SR-11.
4. **R4 — Recapture handoff evidence:** Map each assertion to tests that actually execute it, record real commands and outputs, document grep exit codes accurately, maintain explicit rollback scopes, and separate platform evidence.

### In scope

- `packages/contracts/src/accounting_contracts/source_requiredness.py`: Strict reason enum type enforcement under R1.
- `tests/test_source_requiredness.py`: Comprehensive test suite expansions under R1, R2, and R3 (34 tests total).
- `handoffs/phase-01/wp-09-source-requiredness/acceptance-matrix.md`: Updated acceptance matrix with exact test nodes and upstream citations.
- `handoffs/phase-01/wp-09-source-requiredness/test-results.txt`: Recaptured command outputs, benchmark measurements, mutation check, and scratch rollback proof.
- `handoffs/phase-01/wp-09-source-requiredness/handoff.md`: Updated handoff document.

### Out of scope

- No changes to `ROADMAP.md` or ADR/WP scope.
- No changes to upstream Raw contracts, canonical date/hashing, Change Planner, XLSX Reader, Snapshot Acquisition, Save Import Coordinator, or Watcher runtime.
- No database persistence, Outbox, Telegram bot, or Ledger changes.
- No dependency or lockfile changes.
- No real accounting data, tokens, keys, or external mutations.
- No git push, PR creation, merge, deploy, or commencement of WP-10.

## Roadmap traceability

| Roadmap section / O-item | Approved status | Implemented behavior |
|---|---|---|
| Roadmap 5.2 step 3 & 5.4 | Active / G1 In Progress | Pure required-field preflight rejects incomplete raw records before import commit. |
| Sections 6/7, O-18, O-33 | Approved | Required amount, price, item, and quantity; numeric zero distinguished from null/blank. |
| O-60, O-04 | Approved | Blank notes permitted for C, D, H, HA, HS without emitting requiredness issues. |
| O-16, O-17, O-32 | Approved | Unresolved nonblank raw rows and RS rows preserved without rejection at this boundary. |
| O-68, O-69, O-70 | Approved | Immutable snapshots, UUIDv7 identities, canonical typing, and source hashes preserved untouched. |
| O-74, ADR-0012 | Accepted | `source-requiredness.v1` module in `accounting-contracts` implementing pure preflight. |
| O-46 through O-51 | Approved | Independent Codex acceptance, bounded changes, public-artifact safety, and verified scratch rollback. |

## Changed files

| File | Change | Reason |
|---|---|---|
| `packages/contracts/src/accounting_contracts/source_requiredness.py` | Modified | R1: Replaced reason coercion with strict type validation `type(self.reason) is SourceRequirednessIssueReason`. |
| `tests/test_source_requiredness.py` | Modified | R1–R3: Independent matrix, 16 parametrized entries, property tests, side-effect guard, mutation check, scale benchmark. |
| `handoffs/phase-01/wp-09-source-requiredness/acceptance-matrix.md` | Modified | R4: Updated acceptance matrix mapping all SR-01..14 requirements to executing test nodes. |
| `handoffs/phase-01/wp-09-source-requiredness/test-results.txt` | Modified | R4: Recorded real commands, exit codes, benchmark timings, mutation failure, and scratch rollback proof. |
| `handoffs/phase-01/wp-09-source-requiredness/handoff.md` | Modified | R4: Updated handoff report with R1–R4 disposition, rollback scopes, and platform status. |

## Schema and migrations

- Schema impact: None.
- Migration files: None.
- Backward compatibility: 100% backward compatible. All existing contracts, types, and change planning APIs remain untouched.
- Data migration/real data used: None. Synthetic fixtures only.

## Commands and exit codes

| Command | Exit code | Purpose |
|---|---:|---|
| `uv sync --frozen --all-packages --all-groups` | 0 | Verify frozen dependency lock synchronization (81 packages). |
| `uv lock --check` | 0 | Verify lockfile integrity against pyproject.toml (88 packages). |
| `uv run pytest tests/test_source_requiredness.py -v -s` | 0 | Run dedicated WP-09 unit, integration, and property suite (34 passed in 3.80s). |
| `uv run ruff format --check .` | 0 | Verify code formatting compliance across all 81 files. |
| `uv run ruff check .` | 0 | Run ruff linter checks across all 81 files (0 warnings/errors). |
| `uv run mypy .` | 0 | Static type safety check on Linux target across 30 source files (0 errors). |
| `uv run mypy --platform win32 .` | 0 | Static type safety check on Win32 target across 30 source files (0 errors). |
| `uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s` | 0 | WP-05 streaming reader benchmark: 11.02s / 61.65 MiB (< 15s / 128 MiB). |
| `uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s` | 0 | WP-06 acquisition benchmark: 11.40s / 61.77 MiB (< 15s / 128 MiB). |
| `uv run pytest -v -k "not test_bench"` | 0 | Full repository regression test suite: 382 passed, 2 skipped in 66.19s. |
| `git diff --check d59e20accfa57cfc67de8a8ca6d31deba8a6d405...HEAD` | 0 | Verify zero whitespace errors against approved main baseline. |
| `git ls-files \| grep -E "\.(env\|key\|pem\|pfx\|p12\|kdbx\|sqlite\|db)$" \|\| echo "CLEAN_EXIT_$?"` | 0 | Security audit scan for prohibited sensitive files (normalized exit 1 = 0 found). |
| `git grep -n -i -E "(BEGIN PRIVATE KEY\|AKIA[0-9A-Z]{16}\|ghp_[0-9a-zA-Z]{36})" -- ':!handoffs/' \|\| echo "CLEAN_CODEBASE_EXIT_$?"` | 0 | Security audit scan for leaked private keys and tokens across codebase (normalized exit 1 = 0 found). |
| `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-09-source-requiredness` | 0 | Validate completeness and formatting of handoff package. |

## Tests and evidence

- **Full repository suite:** 382 tests passing cleanly on Linux native (2 platform-conditional skipped Windows-handle tests; 384 collected total, preserving all 350 baseline tests).
- **WP-09 Unit & Property suite:** 34 dedicated tests passing in 3.80s in `tests/test_source_requiredness.py`.
- **SR-13 Preflight Scale Benchmark:** 15,000 rows evaluated by `evaluate_source_requiredness` in **0.0396s** (linear O(N), fixture generation 2.9844s).
- **Streaming Benchmarks (15,000 rows):** WP-05 Reader at 11.02s / 61.65 MiB peak RSS; WP-06 Acquisition at 11.40s / 61.77 MiB peak RSS (strictly within 15.0s / 128.0 MiB ceilings).
- **Controlled Mutation Proof:** In-memory omission of `transaction_type_raw` caused 2 test failures in `test_sr02` (`assert True is False`), proving immediate failure detection.
- **Platform Separation:**
  - **Linux Local:** PASS (382 tests passed, 2 skipped on Linux native runtime).
  - **Windows Local:** NOT RUN (Linux host development environment; static type safety validated via `mypy --platform win32 .` with 0 errors in 30 source files).
  - **Windows CI:** PENDING automated GitHub Actions CI execution upon PR submission.

## Assumptions and open items

- **Pure Evaluation Boundary:** `evaluate_source_requiredness` is a stateless, pure library function that performs no filesystem I/O, spawns no background threads, and accesses no clocks or random sources (verified via side-effect guard in SR-10).
- **Preflight vs. Commit:** Passing requiredness preflight (`passes_requiredness=True`) indicates solely that essential required raw cells are present and non-blank. Full fiscal validation, entity resolution, financial balance, and transaction ownership must be verified in future packages before an import commit may proceed.

## Risks

- **Downstream Misuse:** Mitigated by design: `SourceRequirednessReport` does not expose `commit_allowed` or `import_valid`, and contains no persistence primitives.
- **Unicode Whitespace:** Thoroughly verified by SR-03: `str.strip()` correctly flags non-breaking and ideographic spaces as `BLANK_TEXT`.

## Rollback

To revert changes cleanly and safely:

1. **Verify working tree cleanliness:**
   ```bash
   git status
   ```
   Ensure no uncommitted modifications exist.

2. **Rollback by Explicit Scope:**

   - **Scope 1: Revert Round 1 Code Correction (`f142de97aec65242be8994a6b8fd73537c1f0c12`):**
     To revert solely the Round 1 product and test changes back to previous delivery HEAD `cea3cf67baa73ff50f4a5783689506f84d8ef40e`:
     ```bash
     git revert --no-edit f142de97aec65242be8994a6b8fd73537c1f0c12
     ```
     Verified on scratch branch `scratch/verify-rollback`: working tree matches `cea3cf67baa73ff50f4a5783689506f84d8ef40e` with 0 diff.

   - **Scope 2: Full Rollback of WP-09 Back to Approved Baseline (`d59e20accfa57cfc67de8a8ca6d31deba8a6d405`):**
     To completely back out all WP-09 code and documentation commits back to baseline `d59e20accfa57cfc67de8a8ca6d31deba8a6d405`:
     ```bash
     git revert --no-edit f142de97aec65242be8994a6b8fd73537c1f0c12
     git revert --no-edit cea3cf67baa73ff50f4a5783689506f84d8ef40e
     git revert --no-edit 46020d6f45a4af66cc52fe660829516c5c792c98
     ```
     Verified on scratch branch `scratch/verify-rollback`: working tree matches `d59e20accfa57cfc67de8a8ca6d31deba8a6d405` with 100% exact zero diff.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation of Round 1 corrections (R1–R4), expanded test suites, property testing, benchmarks, static typing, verified scratch rollback, and handoff documentation for WP-09 are completed and stopped for independent Codex review. Gate G1 remains `OPEN / IN PROGRESS`. No Gate approval, merge, push, deploy, or next Work Package has been performed.
