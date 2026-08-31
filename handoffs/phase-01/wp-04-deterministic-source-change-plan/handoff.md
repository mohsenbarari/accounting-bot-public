# Handoff

## Identity

- Phase: 1 — source and data-model foundation (Phase 1 authorized, Gate G1 is open / in-progress)
- Work Package: WP-04: Validated full-source snapshots and deterministic change planning
- Branch/worktree: antigravity/phase-01-deterministic-source-change-plan
- Commit(s):
  - `41c979f` (feat(contracts): implement validated full-source snapshots and deterministic change planning)
  - `fec8c02` (fix(contracts): address Codex review feedback for WP-04)
  - `bf715ab` (fix(contracts): address Round 2 review feedback for WP-04)
- Implementer: Google Antigravity
- Reviewer: Codex

## Scope

### Requested outcome

Implement the smallest pure, deeply immutable and deterministic source-boundary model that validates a complete in-memory snapshot containing exactly all four approved WP-02 sheets, recalculates every row's WP-03 `source_hash` and every sheet's `sheet_snapshot_hash`, compares that snapshot with a validated registry of prior identities, and emits a stable, typed plan of `insert`, `edit`, `void` and `unchanged` transitions with correct revisions and counts.

### In scope

- Public module `accounting_contracts.source_change_plan` containing:
  - Version constant `SOURCE_CHANGE_PLAN_VERSION = "source-change-plan.v1"`.
  - Immutable enums `PlanAction` (`insert`, `edit`, `void`, `unchanged`) and `IdentityLifecycle` (`active`, `voided`).
  - Typed exceptions: `SourceChangePlanError`, `IncompleteSnapshotError`, `DuplicateIdentityError`, `InvalidIdentityError`, `IdentityRelocationError`, `InvalidPriorStateError`.
  - Immutable input and validated row/sheet/workbook snapshot models: `SourceRowInput`, `SourceSheetInput`, `ValidatedSourceRow`, `ValidatedSourceSheetSnapshot`, `ValidatedSourceWorkbookSnapshot`.
  - Snapshot builder `build_source_workbook_snapshot` requiring all four approved sheets, recalculating all hashes deterministically, and enforcing global UUID uniqueness.
  - Immutable prior identity model: `PriorIdentityState`, `PriorIdentityRegistry`, and builder `build_prior_identity_registry`.
  - Deterministic planner `plan_source_changes` implementing the full ADR-0007 transition table, revision advancement, identity relocation error, and deterministic sorting by sheet registry order and UUID bytes.
  - Summary count types `PlanCounts`, `PlanItem` (with `is_reactivation` property), and result `DeterministicSourceChangePlan`.
- Public API exports in `accounting_contracts/__init__.py`.
- Module documentation in `packages/contracts/README.md`.
- Deterministic unit, table-driven, idempotency, synthetic 15,000-row complexity benchmark, and Hypothesis property tests in `tests/test_source_change_plan.py`.
- Handoff artifacts and validation.

### Out of scope

- Opening/reading/copying/modifying `.xlsx`/`.xlsm`, Excel COM, XML parsing, cell extraction, Save monitoring, temporary file snapshots or OneDrive behavior.
- UUID generation, insertion, repair or writeback; workbook headers/filter ranges.
- Required-field, row-activity, transaction-type requiredness, alias/item resolution, RS pairing, opening balances or business validation.
- SQLite/PostgreSQL schema or migration, repositories, transactions, `import_id`, timestamps, revision persistence, Outbox, API or network sync.
- `ledger_hash`, Financial Events, Ledger Entries, balances, reports, PDF or Telegram behavior.
- Any new runtime dependency or architecture-layer reversal.
- Real data, phone numbers, Telegram identity, credentials, host/IP, production systems, edits to `main`, push, PR creation or merge.

## Roadmap traceability

| Roadmap section / O-item | Approved status | Implemented behavior |
|---|---|---|
| Sections 4.2, 4.3 / O-03, O-06 | ✅ Approved | Permanent UUIDv7 identity, revisions, soft-delete voids and row-order independence |
| Section 5.2 / steps 3–5 | ✅ Approved | Full valid snapshot prerequisite; comparison by stable ID/source hash/sheet hash; Insert/Edit/Void/Unchanged classification |
| Section 19.1 | ✅ Approved | Deterministic UUID/hash comparison, no false deletion from partial snapshots, no duplicate event on repeated import |
| O-26, O-68, ADR-0006 | ✅ Approved | Exact literal Raw boundary and canonical source/sheet hashing reuse |
| O-58 | ✅ Approved | Stable identity surviving revisions and voids |
| O-69, ADR-0007 | ✅ Approved | Complete-source snapshot capability, global identity/home-sheet invariants, lifecycle transitions and deterministic plan ordering |
| WP-02, WP-03 | ✅ Approved | Reuse authoritative registry, types, field order and hashing APIs |
| O-46 to O-51 | ✅ Approved | Bounded implementation, protected assets, handoff and independent Codex review |

## Changed files

| File | Change | Reason |
|---|---|---|
| `packages/contracts/src/accounting_contracts/source_change_plan.py` | Created | Complete snapshot validator, prior identity registry, and deterministic change planning engine |
| `packages/contracts/src/accounting_contracts/__init__.py` | Modified | Export public source change plan classes, functions and constants |
| `packages/contracts/README.md` | Modified | Document source change plan contract and boundaries |
| `tests/test_source_change_plan.py` | Created | Deterministic tests for snapshot validation, transition table, order invariance, idempotency, benchmark and property testing |
| `handoffs/phase-01/wp-04-deterministic-source-change-plan/*` | Created | WP-04 Handoff, Acceptance Matrix, and Test Results |

## Schema and migrations

- Schema impact: none
- Migration files: none
- Backward compatibility: fully backward-compatible contract addition
- Data migration/real data used: none

## Commands and exit codes

| Command | Exit code | Purpose |
|---|---:|---|
| `uv --version` | 0 | Verify uv version 0.12.7 |
| `uv lock --check` | 0 | Verify lockfile consistency |
| `uv sync --frozen --all-packages --all-groups` | 0 | Verify frozen dependencies installation |
| `uv run ruff format --check .` | 0 | Verify formatting compliance |
| `uv run ruff check .` | 0 | Verify linting rules compliance |
| `uv run mypy .` | 0 | Verify strict static typing across 17 files |
| `uv run pytest -v` | 0 | Execute all 88 unit, benchmark and property tests |
| `git diff --check origin/main...HEAD` | 0 | Verify clean diff with zero whitespace or line-ending defects against origin/main |
| `! git ls-files \| grep -E '\.(xlsx\|xls\|xlsm\|sqlite\|sqlite3\|db\|pdf\|key\|pem\|env)$'` | 0 | Verify zero forbidden extensions or database/secret files tracked in git |
| `! git grep -n -i -E '(password\s*[:=]\|secret\s*[:=]\|bearer\s+[A-Za-z0-9]\|BEGIN RSA\|BEGIN OPENSSH\|09[0-9]{9}\|[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})' -- ':!ROADMAP.md' ':!docs/adr/*' ':!.agents/*' ':!handoffs/*' ':!uv.lock'` | 0 | Verify zero sensitive patterns, private keys, IP addresses or real phone numbers in source/tests |
| `python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-04-deterministic-source-change-plan/` | 0 | Validate completeness and markers of handoff package |

## Tests and evidence

- Acceptance evidence is mapped in `acceptance-matrix.md`.
- Raw command results are recorded in `test-results.txt`.
- Additional artifact paths: none

## Assumptions and open items

- none

## Risks

- Downstream packages (WP-05+) will consume these change plans to drive atomic SQLite persistence and Outbox change events.

## Rollback

1. Delete the feature branch `antigravity/phase-01-deterministic-source-change-plan`.
2. Checkout `main` branch to restore the repository to its clean baseline.

## Protected assets

- [x] `ROADMAP.md` was not modified.
- [x] The reference Excel workbook and unauthorized copies were not modified.
- [x] No real accounting data, phone number, Telegram identity, PDF, SQLite database, dump, token, credential, or private key was added.
- [x] No production Telegram, server, database, DNS, certificate, backup, or external repository was mutated.
- [x] No destructive migration or unrelated user change was included.

## Stop state

Implementation is stopped pending independent Codex review. Gate G1 remains open. No Gate approval, merge, push, deploy, or next Work Package has been performed.
