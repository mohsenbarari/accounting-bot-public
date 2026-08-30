# WP-04: Validated full-source snapshots and deterministic change planning

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 deterministic Insert/Edit/Void/No-op classification only; this Work Package cannot close G1
- Status: Issued — implementation and evidence pending
- Issued by: Codex Project Manager
- Issued on: 2026-08-30
- Required skill: `accounting-bot-implementer`
- Branch: `antigravity/phase-01-deterministic-source-change-plan`
- Baseline: latest clean `origin/main` containing this Work Package and ADR-0007
- Handoff path: `handoffs/phase-01/wp-04-deterministic-source-change-plan/`

## Objective

Implement the smallest pure, deeply immutable and deterministic source-boundary model that:

1. validates a complete in-memory snapshot containing exactly all four approved WP-02 sheets;
2. rebuilds every row's WP-03 `source_hash` and every sheet's `sheet_snapshot_hash` without trusting caller-supplied digests;
3. compares that snapshot with a validated registry of all previously known identities; and
4. emits a stable, typed plan of `insert`, `edit`, `void` and `unchanged` transitions with correct revision numbers and counts.

This package is a pure planning foundation. It must prove that an incomplete source collection cannot authorize a mass `void`, but it must not read a workbook, decide row/business requiredness, write SQLite, create an `import_id`, commit a revision or emit an Outbox event.

## Roadmap and ADR traceability

- Roadmap sections 4.2/4.3 and O-03/O-06: permanent UUIDv7 identity, unrestricted valid edits/deletes, revisions, soft-delete and row-sort independence.
- Roadmap steps 3–5 in section 5.2: full valid snapshot prerequisite; comparison by stable ID/source hash/sheet hash; Insert/Edit/Void/No-op; later atomic persistence under one `import_id`.
- Roadmap acceptance section 19.1: deterministic UUID/hash comparison, no false deletion from partial snapshots, no duplicate event on a repeated import.
- O-26/O-68 and [ADR-0006](../../adr/ADR-0006-canonical-source-hashing.md): exact literal Raw boundary and canonical source/sheet hashing.
- O-58: one stable Financial Event identity must survive Raw revisions and voids.
- O-69 and [ADR-0007](../../adr/ADR-0007-full-snapshot-change-plan.md): complete-source snapshot capability, global identity/home-sheet invariants, lifecycle transitions and deterministic plan ordering.
- [WP-02](WP-02-raw-input-contracts.md) and [WP-03](WP-03-canonical-source-hashing.md): authoritative registry, types, exact field order and hashing APIs; do not duplicate them.
- O-46 through O-51: bounded implementation, protected assets, Handoff and independent Codex review.

## Authoritative v1 contract

Expose a stable public version constant:

- `SOURCE_CHANGE_PLAN_VERSION = "source-change-plan.v1"`

Place the focused implementation under `packages/contracts/src/accounting_contracts/` (recommended module: `source_change_plan.py`) and export the supported API from `accounting_contracts`. This is a source/import contract and may depend on the existing WP-02/WP-03 contract APIs; do not move it into `accounting_domain` or weaken the architecture guard.

### 1. Current source snapshot input and validation

Provide a small immutable row-input type or an equally typed builder input containing only:

- `stable_id`: UUID object or UUID text;
- `source_values`: a Mapping containing exactly the approved Raw field names for that row's declared sheet.

Provide one public validator/builder that accepts sheet declarations as an iterable rather than only a Mapping so it can detect a duplicate sheet declaration. It must:

1. require every name in `RAW_CONTRACT_REGISTRY` exactly once;
2. reject a missing, duplicate, unknown or unapproved sheet before producing any change plan;
3. accept an explicitly present approved sheet with zero rows as a complete empty sheet;
4. reuse `compute_source_hash` for each row and `compute_sheet_snapshot_hash` for each sheet; never accept a caller-supplied `source_hash` or `sheet_snapshot_hash` as truth;
5. canonicalize and validate every UUID through the same strict UUIDv7/RFC rules already established in WP-03, without generating or repairing an ID;
6. reject a duplicate UUID across the entire four-sheet workbook, including duplicates occurring in different sheets;
7. make a defensive copy of every input collection/Mapping and expose no mutable collection in the result;
8. return a typed, deeply immutable `ValidatedSourceWorkbookSnapshot` (or equivalent clearly named type) containing deterministic sheet order, row order, per-sheet counts/hashes and the exact original scalar Raw values needed by later persistence;
9. preserve `None`, `str`, `int` and `Decimal` source values exactly in the immutable row payload while using WP-03 canonical values only for hashing; and
10. reject Boolean/Float/formula metadata/derived/unlisted/technical-ID keys through the existing canonical contract rather than silently dropping or coercing them.

The type proves only that a full four-sheet collection satisfies the v1 source contract and canonical value rules. Its documentation must state that it is necessary but not sufficient for database Commit: required-field, row-activity, transaction/business and atomic-import validation remain mandatory later. There must be no public `allow_void`, `is_complete`, `trust_hash` or similar Boolean escape hatch.

### 2. Previously known identity state

Provide an immutable prior-identity input/result model covering **all known identities**, including already voided ones. Each identity has:

- canonical UUIDv7;
- immutable `home_sheet`, which must be an approved sheet;
- positive integer `latest_revision` (Boolean is invalid);
- lifecycle exactly `active` or `voided`;
- active `source_hash` only when lifecycle is `active`.

Validation rules:

- UUIDs are globally unique in the prior registry.
- Active requires a valid lowercase 64-hex `source_hash`.
- Voided requires no active source hash.
- Empty prior state is valid for the first import.
- Unknown sheet, malformed/non-v7 UUID, duplicate UUID, zero/negative/non-integer/Boolean revision, invalid lifecycle or inconsistent lifecycle/hash fails with a typed package exception.
- No SQLite entity, repository or database session may appear in this model.

### 3. Deterministic transition table

The planner accepts only a validated current snapshot and validated prior identity state. Apply this exact table:

| Prior state | Current state in the same home sheet | Action | Planned revision | Before/after requirements |
|---|---|---|---:|---|
| unknown | present | `insert` | `1` | before absent; current row present |
| active, same hash | present | `unchanged` | no new revision | before ref and current row present |
| active, different hash | present | `edit` | prior + 1 | before ref and current row present |
| active | absent from the validated full snapshot | `void` | prior + 1 | before ref present; after absent |
| voided | absent | no item | no new revision | already settled; exclude from current counts |
| voided | present again | `edit` (reactivation) | prior + 1 | prior lifecycle voided; current row present |

Additional invariants:

1. A UUID already known in one `home_sheet` and currently present in another sheet is an identity-relocation error. Reject the complete plan; do not translate it into Void+Insert or silently alter the home sheet.
2. A `void` is a planned tombstone revision; it never deletes or overwrites the prior revision.
3. An `unchanged` item is included for per-sheet/import counts but has no planned new revision/event.
4. A reactivation is counted under `edit`, not as a new identity and not as a fifth top-level action. Its previous lifecycle makes the subtype observable.
5. A prior voided identity that remains absent emits no repeated plan item, proving logical retry idempotency.
6. Equivalent accepted numeric/date representations that produce the same WP-03 hash are `unchanged` while active. `None` versus empty text and any other hash-visible difference are `edit`.

### 4. Plan shape, ordering and complexity

Expose immutable action/lifecycle enums and immutable change-item/plan result types. Each plan item must contain enough explicit data to verify:

- action, sheet and canonical UUID;
- prior lifecycle/revision/hash when applicable;
- planned revision when applicable;
- immutable current row/source hash when applicable.

The complete plan must expose at least:

- contract version;
- deterministic tuple of items;
- total and per-sheet counts for `insert`, `edit`, `void` and `unchanged`;
- current per-sheet snapshot hashes/counts.

Order items by the authoritative registry sheet order and then UUID bytes. This exact result must be independent of:

- order of sheet declarations;
- order of rows inside a sheet;
- key order of each source Mapping; and
- order of prior identities.

Use UUID-indexed dictionaries/sets plus one deterministic sort. The intended bound is `O(n log n)` time at worst because of ordering and `O(n)` memory. Do not use nested scans that make comparison quadratic. Avoid storing duplicate canonical JSON/bytes in every long-lived plan item when the raw immutable row and digest are sufficient.

## Preconditions and environment

- Work only in `/srv/accounting-bot/workspace` after confirming a clean `main` exactly equal to `origin/main`, then create the exact branch above.
- Invoke and follow `accounting-bot-implementer` before changing files.
- Use committed Python 3.13, `uv 0.12.7` and the current frozen workspace lockfile.
- Do not modify `ROADMAP.md`, ADR-0006/ADR-0007, accepted Work Packages/ADRs or the WP-02/WP-03 behavior.
- Do not open, copy, inspect or modify any real/reference workbook or SQLite/database artifact. All tests use small, explicitly synthetic fixtures.

## In scope

1. One focused source-change-plan module in `accounting-contracts`, public exports and concise package documentation.
2. Typed exceptions for full-snapshot, identity-state and planning invariant failures; preserve useful exception chaining.
3. Deeply immutable source rows, four-sheet snapshot, prior identity registry, actions/items/counts and final plan.
4. Direct reuse of `RAW_CONTRACT_REGISTRY`, `compute_source_hash` and `compute_sheet_snapshot_hash`; small backward-compatible WP-03 refactoring is allowed only if necessary to expose one existing strict UUID/hash validator without duplicating behavior.
5. Deterministic unit, table-driven and Hypothesis property tests with entirely synthetic data.
6. Required Handoff, Acceptance Matrix and test-results artifacts validated by the repository skill.

## Out of scope

- Opening/reading/copying/modifying `.xlsx`/`.xlsm`, Excel COM, XML parsing, row activation, header/cell extraction, Save monitoring, temporary file snapshots or OneDrive behavior.
- UUID generation/insertion/repair/writeback or any modification to an Excel file or copy.
- Transaction-type requiredness, alias/party/item resolution, RS pairing, opening balances, financial dates beyond the existing WP-03 canonical parser, or business-validity decisions.
- SQLite/PostgreSQL schema or migration, repositories, transactions, `import_id`, timestamps, file hashes, revision persistence, Outbox/change events, affected-domain calculation or Import reports.
- `ledger_hash`, Financial Events, Ledger Entries, balances, reports, PDF, Telegram or network sync.
- A new runtime dependency, architecture-layer reversal or copy of the WP-02 registry/WP-03 hashing logic.
- Real data, plausible phone numbers, Telegram identity, credentials, host/IP, production systems, edits to `main`, push, PR creation or merge.

## Required acceptance evidence

Record every command and exit code in `test-results.txt`.

1. Public API tests assert `source-change-plan.v1`, immutable action/lifecycle values, exact registry reuse and no duplicate sheet/field registry.
2. Full-snapshot tests cover all four present; an explicitly empty sheet; first import with all sheets empty; and rejection of missing, unknown and duplicate sheet declarations before a plan can exist.
3. Row tests prove UUIDv7 canonicalization, caller Mapping-order independence, exact Raw preservation via defensive copy, deep immutability and rejection of malformed/non-v7/duplicate-global IDs, missing/extra/technical/derived keys, Boolean and Float.
4. Per-sheet hash/count results exactly equal direct WP-03 computation for the same synthetic rows; callers cannot inject or override either hash.
5. Prior-state tests cover empty state, active and voided identities and every invalid combination of UUID, sheet, duplicate, revision, lifecycle and source hash.
6. One table-driven test covers every ADR-0007 transition: Insert revision 1, Active no-op, Active edit `n+1`, Active void `n+1`, settled Void no item and Voided reactivation as Edit `n+1`.
7. Tests prove a UUID moved between any two sheet types rejects the whole plan, including `record_id` to `party_id`; it must never become Void+Insert.
8. Tests prove Sort, row order, sheet declaration order, source Mapping order and prior-state order do not alter item order, fields, hashes or counts. Include a Hypothesis arbitrary-permutation property test.
9. Tests prove a changed hash changes only the intended item; same Canonical number/date representation remains unchanged; Null versus empty text edits; deleting one active identity emits exactly one void; a present but empty sheet may legitimately void all prior active identities in that sheet.
10. Retry/idempotency tests prove: identical current/prior state yields only unchanged items; applying a synthetic representation of a plan to prior state then planning the same current snapshot emits no Insert/Edit/Void; an already voided and still absent identity emits nothing; a reactivated identity becomes unchanged after the synthetic state update.
11. Count tests assert total and per-sheet Insert/Edit/Void/Unchanged counts, including the fact that settled absent Voids are not current unchanged rows.
12. Immutability tests attempt to mutate source input dictionaries/lists after construction and all exposed snapshot/prior/plan collections directly; results remain stable or raise `TypeError`/frozen-instance errors.
13. Complexity evidence includes a synthetic, non-sensitive 15,000-row benchmark or profiling test recorded in Handoff (it may be excluded from ordinary CI timing assertions) and source inspection showing UUID-indexed comparison with no all-row nested scan.
14. Existing 64 tests and all new tests pass. `uv lock --check`, frozen all-workspace/all-group sync, Ruff format/lint, strict mypy and pytest pass on Python 3.13 without dependency or lockfile drift.
15. `git diff --check` passes. The final diff is bounded to the new contracts module/exports, focused tests, contracts documentation and WP-04 Handoff artifacts; any change to WP-02/WP-03 code must be minimal, justified and explicitly highlighted.
16. Failure-on-match scans find no workbook/database/PDF, real identity/phone, host/IP, secret, environment artifact or captured real row value. Scan commands must not mask matches with `|| true` or equivalent.
17. The Handoff validator exits zero for `handoffs/phase-01/wp-04-deterministic-source-change-plan/`.
18. After Codex independently reviews and pushes the branch, every configured GitHub Actions job must pass before acceptance or merge.

## Stop conditions

Stop and report to Codex without expanding scope if:

- baseline is dirty/divergent or contains an unexpected protected asset;
- ADR-0007 conflicts with WP-02/WP-03 behavior, or any transition/home-sheet/revision rule is ambiguous;
- producing a `void` seems to require accepting a partial sheet collection, caller-provided completeness Boolean or trusted external hash;
- source/business requiredness must be invented to complete the package;
- implementation appears to require reading a workbook, source row number/cell address, real data or a database;
- an ID must be generated/repaired, a caller hash trusted, a derived/formula value admitted or a raw source value normalized beyond ADR-0006;
- SQLite models, an `import_id`, actual revision persistence, Outbox, affected-domain or financial logic seems necessary;
- a new dependency, architecture change, accepted-contract change or protected action is proposed;
- any action would alter Excel, `ROADMAP.md`, an accepted ADR/Work Package, `main`, GitHub state or infrastructure.

## Handoff contract

Follow the repository skill exactly. Commit the bounded implementation and complete:

- `handoff.md`
- `acceptance-matrix.md`
- `test-results.txt`

Run the skill validator, then report branch/baseline, commits, changed files, public API, transition table implementation, count/hash behavior, immutability strategy, property/complexity evidence, every command and exit code, Handoff path, remaining risks and pending decisions. Stop for Codex review. Do not push, open a PR, merge, deploy or continue into another Work Package.
