# Handoff: WP-09 Required-Field Preflight for Raw Source Snapshots (Round 2 Correction)

## Identity

- **Phase:** 1 — source and data-model foundation
- **Work Package:** WP-09 Required-field preflight for raw source snapshots (Round 2 Bounded Correction)
- **Branch/worktree:** `antigravity/phase-01-source-requiredness`
- **Execution Baseline:** `d59e20accfa57cfc67de8a8ca6d31deba8a6d405` (PR #31 merged into main)
- **Prior Code Commit:** `f142de97aec65242be8994a6b8fd73537c1f0c12`
- **Prior Delivery HEAD:** `528c313c57eba2ad8eb84296293fa9b8ef43484a`
- **Tested Code Commit SHA:** `844421a0d4ffd6a7fb9a4d064084f469daf2ecc6`
- **Product Code Status:** 100% UNCHANGED from `f142de97aec65242be8994a6b8fd73537c1f0c12` (R1 closed)
- **Implementer:** Google Antigravity
- **Reviewer:** Codex Project Manager
- **Gate G1 Status:** `OPEN / IN PROGRESS`

## Scope

### Requested outcome

Address the remaining R2–R4 test and evidence corrections requested by Codex independent review on delivery `528c313c57eba2ad8eb84296293fa9b8ef43484a`:
1. **R1 — Product code status:** R1 is closed. Product code in `packages/contracts/src/accounting_contracts/source_requiredness.py` remains 100% unchanged from `f142de97aec65242be8994a6b8fd73537c1f0c12`.
2. **R2 — Complete independent text-field and generated-permutation coverage:** Cover present, nonblank surrounding whitespace preserved verbatim, None, empty string, ASCII whitespace, and Unicode whitespace across all nine required raw-text fields from a test-owned matrix. Extend generated property tests to span all four sheets and bounded actual sheet, row, and mapping-order permutations with inverted UUID byte order against an independent oracle. Demonstrate that both omissions are detected: buy/sell required `transaction_type_raw` and receipts/payments blank `entry_type_raw`.
3. **R3 — Finish SR-01 and SR-13 without widening product scope:** Add a focused SR-01 test that actually executes a fresh module import of `accounting_contracts.source_requiredness` under a side-effect guard (evicting from `sys.modules`, forbidding writes, network, clocks, UUIDs, and threads while permitting Python module-loader reads). In SR-13, derive and compare the full independently expected ordered tuple `(sheet_name, UUID, field_name, reason)` for all 3,000 issues. Pre-capture row references, raw mappings, and source hashes before evaluation and verify 100% post-evaluation retention across all 15,000 rows. Record separate fixture build and evaluator timings, distinguish code-inspection complexity from observed timings, and assert no invented ceilings.
4. **R4 — Correct rollback scope and update supported claims:** Verify complete scratch branch rollback sequence back to baseline `d59e20accfa57cfc67de8a8ca6d31deba8a6d405` with zero diff. Commit test corrections first, record the tested commit SHA, capture all commands and outputs, and commit documentation afterward without self-hash placeholder loops.

### In scope

- `tests/test_source_requiredness.py`: Expanded 9-field text matrix, inert fresh import test, 4-sheet property permutations, and strict 15,000-row issue/retention assertions (45 tests total).
- `handoffs/phase-01/wp-09-source-requiredness/acceptance-matrix.md`: Updated matrix mapping all SR-01..14 requirements to executing test nodes.
- `handoffs/phase-01/wp-09-source-requiredness/test-results.txt`: Recaptured command outputs, benchmark timings, both in-memory mutation checks, and scratch rollback proof.
- `handoffs/phase-01/wp-09-source-requiredness/handoff.md`: Updated handoff document.

### Out of scope

- Product code changes: Strictly forbidden (R1 closed).
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
| `tests/test_source_requiredness.py` | Modified | R2/R3: 9-field text matrix, inert fresh import test, 4-sheet property permutations, 15k-row issue & retention verification. |
| `handoffs/phase-01/wp-09-source-requiredness/acceptance-matrix.md` | Modified | R4: Updated acceptance matrix mapping all requirements to executing test nodes. |
| `handoffs/phase-01/wp-09-source-requiredness/test-results.txt` | Modified | R4: Recorded real commands, exit codes, benchmark timings, both mutation checks, and scratch rollback proof. |
| `handoffs/phase-01/wp-09-source-requiredness/handoff.md` | Modified | R4: Updated handoff report with R2–R4 disposition, rollback sequence, and platform status. |

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
| `uv run pytest tests/test_source_requiredness.py -v -s` | 0 | Run dedicated WP-09 unit, integration, and property suite (45 passed in 3.69s). |
| `uv run ruff format --check .` | 0 | Verify code formatting compliance across all 81 files. |
| `uv run ruff check .` | 0 | Run ruff linter checks across all 81 files (0 warnings/errors). |
| `uv run mypy .` | 0 | Static type safety check on Linux target across 30 source files (0 errors). |
| `uv run mypy --platform win32 .` | 0 | Static type safety check on Win32 target across 30 source files (0 errors). |
| `uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s` | 0 | WP-05 streaming reader benchmark: 10.68s / 61.94 MiB (< 15s / 128 MiB). |
| `uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s` | 0 | WP-06 acquisition benchmark: 11.68s / 61.64 MiB (< 15s / 128 MiB). |
| `uv run pytest -v -k "not test_bench"` | 0 | Full repository regression test suite: 393 passed, 2 skipped in 64.98s. |
| `git diff --check d59e20accfa57cfc67de8a8ca6d31deba8a6d405...HEAD` | 0 | Verify zero whitespace errors against approved main baseline. |
| `git ls-files \| grep -E "\.(env\|key\|pem\|pfx\|p12\|kdbx\|sqlite\|db)$" \|\| echo "CLEAN_EXIT_$?"` | 0 | Security audit scan for prohibited sensitive files (normalized exit 1 = 0 found). |
| `git grep -n -i -E "(BEGIN PRIVATE KEY\|AKIA[0-9A-Z]{16}\|ghp_[0-9a-zA-Z]{36})" -- ':!handoffs/' \|\| echo "CLEAN_CODEBASE_EXIT_$?"` | 0 | Security audit scan for leaked private keys and tokens across codebase (normalized exit 1 = 0 found). |
| `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-09-source-requiredness` | 0 | Validate completeness and formatting of handoff package. |

## Tests and evidence

- **Full repository suite:** 393 tests passing cleanly on Linux native (2 platform-conditional skipped Windows-handle tests; 395 collected total, preserving all 350 baseline tests).
- **WP-09 Unit & Property suite:** 45 dedicated tests passing in 3.69s in `tests/test_source_requiredness.py`.
- **SR-13 Preflight Scale Benchmark:** 15,000 rows evaluated by `evaluate_source_requiredness` in **0.0349s** (linear single-pass O(N) complexity by code inspection, fixture generation 2.6894s). All 3,000 issues match independently expected ordered tuple `(sheet_name, UUID, field_name, reason)`. 100% row object, raw mapping, and source hash retention verified across all 15,000 rows.
- **Streaming Benchmarks (15,000 rows):** WP-05 Reader at 10.68s / 61.94 MiB peak RSS; WP-06 Acquisition at 11.68s / 61.64 MiB peak RSS (strictly within 15.0s / 128.0 MiB ceilings).
- **Controlled In-Memory Mutation Proof:**
  - Omission of buy/sell `transaction_type_raw` failed `test_sr02_omission_of_transaction_type_raw_is_detected` with exit code 1 (`assert True is False`).
  - Omission of receipts/payments blank `entry_type_raw` failed `test_sr03_omission_of_receipts_payments_blank_entry_type_is_detected` with exit code 1 (`assert True is False`).
- **Platform Separation:**
  - **Linux Local:** PASS (393 tests passed, 2 skipped on Linux native runtime).
  - **Windows Local:** NOT RUN (Linux host development environment; static type safety validated via `mypy --platform win32 .` with 0 errors in 30 source files).
  - **Windows CI:** PENDING automated GitHub Actions CI execution upon PR submission.

## Assumptions and open items

- **Pure Evaluation Boundary:** `evaluate_source_requiredness` is a stateless, pure library function that performs no filesystem I/O, spawns no background threads, and accesses no clocks or random sources (verified via side-effect guard in SR-10).
- **Fresh Module Import Purity:** Fresh import of `accounting_contracts.source_requiredness` executes completely inertly without application file writes, network calls, clock queries, UUID generations, or thread startups (verified via side-effect guard in SR-01).
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

2. **Complete Rollback Sequence from Tested Code Commit (`844421a0d4ffd6a7fb9a4d064084f469daf2ecc6`):**
   In an isolated scratch branch or clean clone starting at `844421a0d4ffd6a7fb9a4d064084f469daf2ecc6`:
   ```bash
   git revert --no-edit 844421a0d4ffd6a7fb9a4d064084f469daf2ecc6
   git revert --no-edit 528c313c57eba2ad8eb84296293fa9b8ef43484a
   git revert --no-edit f142de97aec65242be8994a6b8fd73537c1f0c12
   git revert --no-edit cea3cf67baa73ff50f4a5783689506f84d8ef40e
   git revert --no-edit 46020d6f45a4af66cc52fe660829516c5c792c98
   git diff --quiet d59e20accfa57cfc67de8a8ca6d31deba8a6d405
   ```
   Verified on scratch branch `scratch/verify-rollback`: working tree matches baseline `d59e20accfa57cfc67de8a8ca6d31deba8a6d405` with 100% exact zero diff (Exit code 0).

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation of Round 2 corrections (R2–R4), expanded 9-field text matrix, inert fresh import test, 4-sheet property permutations, 15,000-row exact issue and retention verification, static typing, verified scratch rollback, and handoff documentation for WP-09 are completed and stopped for independent Codex review. Product code in `accounting_contracts` is confirmed 100% unchanged from `f142de97aec65242be8994a6b8fd73537c1f0c12`. Gate G1 remains `OPEN / IN PROGRESS`. No Gate approval, merge, push, deploy, or next Work Package has been performed.
