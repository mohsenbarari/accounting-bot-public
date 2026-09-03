# Handoff

## Identity

- Phase: 1 — source and data-model foundation; G1 OPEN / IN PROGRESS.
- Work Package: [WP-13](../../../docs/work-packages/phase-01/WP-13-source-identity-projection.md).
- Contract: `source-identity-projection.v1`, [ADR-0016](../../../docs/adr/ADR-0016-source-identity-projection.md), O-78.
- Branch/worktree: `codex/phase-01-source-identity-projection`, isolated checkout.
- Planning commit: `6914d6436fe7eede210932ce59d2f61b6e3b4de1`, merged as PR #43.
- Execution baseline: `dd4b183a5ede1eb75e73c4268789ac7eb759fe1b`.
- Tested code commit: `3ef927dd5016e463b4eed910933f29052c8c7967`.
- Delivery: subsequent documentation-only commit; its actual SHA is recorded in
  the local delivery result after commit creation, avoiding a self-referential SHA.
- Implementer: Codex under the issued implementer workflow.
- Reviewer: separate non-author Codex agent; R1 closed on the tested code,
  no open code findings; final delivery review pending.

## Scope

The new pure catalog validates permanent home sheets, single-source transaction
ownership, consistent UUID/revision states and active membership freshness.
It retains immutable committed views and derives global heads/indexes. The
projector uses the existing exact active-source resolver, retains that source's
members/tombstones, and borrows only present known nonmember parties.

This prevents absent archive identities from becoming VOID and prevents known
parties from restarting at revision 1. It rejects relocation and foreign
transaction reuse before a caller can plan. Five additive exports and README
guidance document representation, purity, cost and the future commit boundary.

No prior product contract, runtime, storage, source identity writer/enrollment,
financial/RS policy, dependency or CI behavior was changed. All fixtures are
synthetic. No real workbook/copy was opened or changed.

## Roadmap traceability

| Roadmap section / O-item | Approved status | Implemented behavior |
|---|---|---|
| 4.2 / O-03 | Confirmed permanent identity | Global home-sheet and transaction-owner checks; no UUID generation/repair. |
| 4.3 / O-58 / O-69 | Confirmed revision/VOID semantics | Existing WP-04 table preserved; scoped prior retains tombstones and known party revision. |
| 5.3 / O-07 | Confirmed archive immutability | Historical views unchanged; absent nonmembers never enter operational prior. |
| O-76 / ADR-0014 | Accepted exact-key resolver | Same stored prior remains the resolver result; projection is a separate explicit adapter. |
| O-77 / ADR-0015 | Accepted same-acquisition reader | Synthetic test-only composition; leased source bytes and cleanup preserved. |
| O-78 / ADR-0016 | Design approved; evidence pending PM acceptance | Catalog and projection with IP-01..16 evidence; no storage/commit claim. |

## Changed files

| File | Change | Reason |
|---|---|---|
| packages/contracts/src/accounting_contracts/source_identity_projection.py | New pure contract implementation | O-78 API, validation and projection. |
| packages/contracts/src/accounting_contracts/__init__.py | Five additive exports | Issued public API. |
| packages/contracts/README.md | Usage, cost and trust/commit boundary | Prevent projection from being mistaken for durable state. |
| tests/test_source_identity_projection.py | 99 independently addressed cases | API, ownership, scoped revisions, failure, mutation oracles and purity. |
| tests/test_source_identity_projection_composition.py | Six cases | XLSX composition, generated histories and indexed/15,000-identity evidence. |
| tests/source_identity_projection_support.py | Synthetic inputs and tuple/dictionary reference model | Independent expected states/actions and test-only membership evolution. |
| tests/source_identity_projection_import_probe.py | Isolated target-module execution guard | Normal import and actual injected-write negative control. |
| tests/source_identity_projection_benchmark.py | Isolated complete-scope benchmark | Separate fixture/catalog/projection/Planner time and call-window RSS. |
| handoffs/phase-01/wp-13-source-identity-projection/ | This three-document package | Recorded evidence and bounded rollback. |

All 24 pre-existing test/helper files and their 678 collected nodes are unchanged.

## Schema and migrations

- Schema impact and migration files: none.
- Backward compatibility: WP-04, WP-11 and all other accepted APIs unchanged.
- Data migration/real data: none; only generated fixtures and in-memory metadata.

## Commands and exit codes

Exact commands/output and original log hashes appear in `test-results.txt`.

| Command | Exit code | Result |
|---|---:|---|
| uv sync --frozen --all-packages --all-groups | 0 | Isolated Windows environment; pinned Python 3.13.15. |
| uv lock --check | 0 | 88 packages; no lockfile change. |
| uv run ruff format --check . | 0 | 120 files formatted. |
| uv run ruff check . | 0 | Clean. |
| uv run mypy . | 0 | 49 source files on Windows default platform. |
| uv run mypy --platform win32 . | 0 | 49 source files; static win32 check, not Linux/native CI evidence. |
| uv run pytest tests/test_source_identity_projection.py tests/test_source_identity_projection_composition.py -v | 0 | 105 passed in 3.48s. |
| uv run pytest --collect-only -q | 0 | 783 cases; 678 original plus 105 new. |
| uv run pytest -v | 0 | 778 passed, 5 skipped in 68.48s. |
| uv run pytest tests/test_source_identity_projection_composition.py -k ip14 -v -s | 0 | Two archive-index guards and complete 15,000-identity oracle. |
| Original WP-05, WP-06 and WP-12 combined benchmark commands | 0 | All existing 15-second / 128-MiB boundaries passed. |
| python ../mutation_probe.py | 0 | Three isolated injected faults produced the expected assertion failures, child exit 1 each. |
| python ../verify_code.py | 0 | Eight-file scope, five exports, unchanged baseline tests and required logs. |
| python ../scan_implementation.py | 0 | 156 tracked plus 3 candidate documents / 10 branch blobs; zero configured-pattern findings. |
| python ../verify_rollback.py | 0 | Fixed code inverse matched baseline index and tree in isolation. |
| git diff --check dd4b183a5ede1eb75e73c4268789ac7eb759fe1b...HEAD | 0 | No whitespace errors. |

An initial sandbox dependency sync could not access PyPI hatchling metadata.
The same frozen sync succeeded with network authorization; its failure log is
retained separately. This required no manifest/lock change. The artifact runner
uses UTF-8 and a workspace-local pytest temp root. No machine configuration or
symlink privilege was changed.

## Tests and evidence

The first non-author review of code `9b1278c208a1c4df724645d5514ae6bdd3fce801`
found one P2 defect: whole-dataclass equality rejected valid immutable subclasses
with identical state fields. Correction `3ef927dd5016e463b4eed910933f29052c8c7967`
compares the four normative home/revision/lifecycle/hash fields explicitly.
Four new base/subclass regressions cover both archive/active placements and
ACTIVE/VOIDED states. All four failed with the actual catalog rejection before
the correction, then passed within the 105-case dedicated suite. Canonical head
and selected active objects are retained; upstream validation remains unchanged.
Original initial-code logs are retained in `initial-code-evidence`, and the
initial independent review/probe logs remain unchanged beside the R1 evidence.
The same reviewer independently reran all 105 dedicated cases (3.46s, exit 0)
and the original unchanged probe, which now accepts the valid semantic pair.
`independent-review-r1.md` closes the sole finding; this does not replace native
CI or the final committed-delivery review.

See `acceptance-matrix.md` for every IP-01..16 node family. The 105 new cases
include all four home sheets, global equal-revision disagreement below a later
head, first membership after UNCHANGED, VOID/reactivation across years, mixed
Raw dates independent of the marker, and actual formula/cache/reorder fixtures.

IP-12 runs 60 valid generated histories with real input permutations and 30
generated conflicting histories. The reference model is test-only and never
consumes the actual projection/plan to construct its expected committed state.

The three IP-13 mutations replace only the function in isolated child memory:
archive flattening, dropped borrowing and ownership bypass. Each fails
`assert outcome == expected[case]`, not setup/import; source bytes remain unchanged.
Fresh-module purity guards cover target execution after code/dependency loading,
not the Python interpreter's entire import process. Evaluation guards are
installed after fixture construction and include an injected actual file write.
They inspect the documented seams, not every possible future Python I/O API.

IP-14 measured 15,000 global identities and 11,250 projected states/Planner items,
all independently checked. Fixture: 1.533233s; Catalog: 0.023534s; Projection:
0.013218s; Planner: 0.051547s; call-window peak RSS: 86.910156 MiB. No new
performance threshold is claimed. Archive access is separately trapped after
catalog construction at two history sizes.

Original combined benchmarks: WP-05 2.7735s / 60.95 MiB; WP-06 2.9163s /
60.59 MiB; WP-12 2.634347s / 84.011719 MiB (fixture 2.297831s separate).

Native local Windows: PASS with four symlink-privilege skips and one POSIX-only
skip disclosed. Native local Linux: NOT RUN (no Linux execution host used).
Native Windows/Linux PR CI: PENDING; all four Windows symlink tests must execute
under the existing mandatory CI precondition before acceptance.

Original command bytes, argv, JSON checks, mutation logs, rollback log and
independent review artifacts are retained outside Git in
`work/reviews/wp13-implementation-20260903/`.

## Assumptions and open items

- Supplied membership is trusted committed metadata. Validation cannot prove
  omitted archives, correct source association, full revision history or Raw
  provenance. It is not authentication or a complete-history attestation.
- A borrowed party with UNCHANGED still needs durable new-source membership on
  commit. Proposed output does not advance membership or any revision.
- One consistent durable generation, stale-state protection, atomic Raw/revision/
  membership/outbox, enrollment and final import/archive/activation with crash
  recovery remain separate contracts and G1 dependencies.
- Real Excel Save/SaveAs/OneDrive marker retention remains unproved.

## Risks

- Feeding incomplete or incorrectly associated metadata can hide ownership
  conflicts; future storage must enforce the full generation boundary.
- Changing ownership/revision semantics after operational use requires another
  ADR and migration/replay evidence; no such migration occurs here.
- A local Windows privilege skip is not successful native CI coverage.

## Rollback

The verified inverse starts at tested code
`3ef927dd5016e463b4eed910933f29052c8c7967` and reverses these two fixed commits in
this order: `3ef927dd5016e463b4eed910933f29052c8c7967`, then
`9b1278c208a1c4df724645d5514ae6bdd3fce801`. Execution baseline is
`dd4b183a5ede1eb75e73c4268789ac7eb759fe1b`. This excludes later Handoff-only
commits and any future PR merge. Do not run it blindly against another checkout.

1. Inspect `git status --short --branch` and preserve unrelated changes. Verify
   the intended checkout's actual SHA before proceeding.
2. In a new clean isolated scratch checkout starting exactly at the tested code,
   run `git revert --no-edit 3ef927dd5016e463b4eed910933f29052c8c7967 9b1278c208a1c4df724645d5514ae6bdd3fce801`.
3. Require both `git diff --quiet dd4b183a5ede1eb75e73c4268789ac7eb759fe1b`
   and `git diff --cached --quiet dd4b183a5ede1eb75e73c4268789ac7eb759fe1b`
   to exit 0, then check clean status and equal Git tree IDs.
4. These steps actually ran in `rollback-r1-checkout`; the implementation
   checkout (including its untracked delivery draft) and main were untouched.
   No scratch deletion was needed.
5. For a later delivery/merge, first enumerate and review its exact code/docs/
   merge SHAs and repeat in isolation. This code-only witness does not claim
   removal of later evidence documents. No hard reset, force push, broad file
   deletion or real-data migration is authorized.

## Protected assets

- [x] ROADMAP.md, ADR/WP documents and Gate status were not modified.
- [x] Reference Excel and real-data copies were neither read nor modified.
- [x] No real accounting data, phone, Telegram identity, PDF, database, dump,
  token, credential or private key was added; fixtures are synthetic.
- [x] No production Telegram/server/database/DNS/certificate/backup was mutated.
- [x] No destructive migration or unrelated user change was included.
- [x] No publication, PR, merge or deployment was performed for this implementation.

## Stop state

Implementation and non-author code review are complete locally. Final
delivery verification, native Windows/Linux CI and PM acceptance remain separate.
No Gate approval, next Work Package or protected action follows from this handoff;
G1 remains OPEN / IN PROGRESS.
