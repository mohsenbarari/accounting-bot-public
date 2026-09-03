# Codex acceptance: WP-13 source identity projection

- Decision: accepted and merged; synthetic pure identity/membership evidence only.
- Decision owner: Codex Project Manager under O-46/O-49.
- Date: 2026-09-03.
- Execution baseline: `dd4b183a5ede1eb75e73c4268789ac7eb759fe1b`.
- Initial implementation: `9b1278c208a1c4df724645d5514ae6bdd3fce801`.
- Corrected tested code: `3ef927dd5016e463b4eed910933f29052c8c7967`.
- Independently reviewed delivery: `c4a56731d1e5b2faa611eebdf5f2e3a2005a7084`.
- Implementation PR: [#44](https://github.com/mohsenbarari/accounting-bot-public/pull/44).
- Merge commit: `3611eac8f976bebb8f6c3f3fc5c942a88dae882a`.
- Pre-merge CI: [Run 33759736020](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33759736020), successful on the reviewed head.
- Post-merge CI: [Run 33760167252](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33760167252), successful on the exact merge.
- G1 remains **OPEN / IN PROGRESS**. No next work package is issued here.

## Review responsibility and decision

Codex implemented the issued WP-13. A separate existing Codex agent that did not
author it reviewed O-78, ADR-0016, IP-01..16, all eight product/export/README/test
files and the three Handoff documents. The final disposition was **no open
findings** on the delivery above. The reviewer changed no tracked file or Git ref.

The initial review found one P2 defect: whole-dataclass equality rejected an inert,
valid frozen/slotted subclass whose state fields equalled a base instance. Both
inputs passed the unchanged nested public validators. R1 replaced that comparison
at both UUID/revision ties and active-head freshness with the explicit normative
home/revision/lifecycle/hash fields. No upstream contract or equality was changed.
Four regressions exercise both archive/active placements and ACTIVE/VOIDED states;
all four failed with the actual catalog rejection before the fix and passed after
it. Canonical head objects and selected active-state objects remain identical.

The non-author reviewer independently ran all **105 dedicated cases in 3.46s**
on the corrected code using native Windows Python 3.13.15. Its original unchanged
semantic-state probe now accepts the formerly rejected pair. The broader initial
contract review remains applicable to that two-file correction. The final
documentation pass independently verified fourteen Git/validator commands,
23 original evidence hashes, complete normalized output for thirteen logs, exact
scope/ancestry, two case-correct links and the retained rollback tree/index.
It did not claim an independent rerun of the full suite or static checks.

Codex Project Manager accepts this bounded package using that non-author review,
the [acceptance matrix](acceptance-matrix.md), [handoff](handoff.md), retained
[test evidence](test-results.txt) and native CI below. The Owner explicitly
authorized publication of the exact eleven-file delivery, its PR and normal
merge after successful Windows/Linux CI. Merge used the exact-head guard with
both required jobs successful; no administrator bypass, direct main commit,
force push or branch deletion occurred. The merge tree equals the reviewed
delivery tree; clean local main advanced by fast-forward only.

This acceptance resolves the review and native CI items pending in the original
Handoff. Those three documents remain unchanged as historical execution records.
The independent initial, correction and final reports and raw logs are retained
in local WP-13 implementation review artifacts. Original R1 execution output is
also packaged in the Handoff test evidence; the pending wording in the original
reports does not replace this later PM disposition.

## Accepted evidence

- IP-01..03: five-symbol API, fixed version, frozen/slotted catalog, constructor
  validation and immutable indexes. Registry and state object identity are
  preserved. Global heads use greatest revision with deterministic canonical
  ties, not year or input order. Permanent homes, single-source transaction
  ownership including tombstones, all represented revision ties and active
  freshness are checked. Equal but distinct valid state classes remain accepted.
- IP-04..08: exact existing resolver, typed archive/unknown/wrong-year errors
  with original causes, complete selected membership including tombstones,
  present-party borrowing and full independent Planner outcomes. Absent archive
  identities never cause false VOID. Known parties continue their revision;
  unknown identities start at one. All four sheets exercise the six accepted
  transition modes. Home relocation precedes foreign transaction ownership
  rejection; moved or reused identities are never silently regenerated.
- IP-09..12: a test-only independent A/B/C committed-metadata model records
  new-source membership even after UNCHANGED, then edit/void/reactivation retain
  global continuity while archive objects remain fixed. Invalid roots, exact
  enum reasons, safe public diagnostics, retained causes and raw cancellation
  have explicit checks. Synthetic XLSX composition joins lease/marker/Raw,
  exact binding, requiredness/fiscal evidence, projection and the unchanged
  Planner. Physical row reorder and formula/cache-only generations remain
  unchanged; one Raw edit changes exactly its expected identity/revision.
  Observed Raw years 1403/1404 do not replace declared source year 1405.
  Source bytes and lease cleanup are checked. Hypothesis runs 60 valid histories
  with actual input permutations and 30 conflicting histories; expected states
  are never manufactured from actual projection/Planner results.
- IP-13: three isolated in-memory function mutations flatten archive heads,
  drop borrowing and bypass transaction ownership. Each produces the intended
  semantic expected-value assertion failure, not an import/setup error. Product
  file bytes are unchanged; all three controls were repeated on corrected code.
- IP-14/15: after catalog construction, projection cannot revisit archives at
  history sizes one and one thousand. An isolated 15,000-global-identity fixture
  checks all 11,250 projected identities and Planner items. Scoped fresh-import
  and evaluation guards plus actual injected-write negative controls verify
  purity. Fixture construction and Python/dependency loading are outside the
  guarded operation. The existing resolver is explicitly allowed; no new I/O,
  hash recomputation, financial evaluation, Planner call or storage is implicit.
- IP-16: all 678 baseline nodes and 24 existing test/helper files are unchanged;
  105 additions produce 783 collected tests. Frozen sync/lock, Ruff, both complete
  mypy commands, dedicated/full tests, Handoff, whitespace, scan, rollback and
  the original WP-05/06/12 benchmarks passed. Native CI resolves the four local
  Windows symlink-privilege gaps.

## Native CI evidence

| Execution | Platform | Full suite | Expected skips |
|---|---|---|---|
| PR #44, Run 33759736020 | Windows | 782 passed in 76.40s | 1 POSIX permission check |
| PR #44, Run 33759736020 | Linux | 781 passed in 51.08s | 2 Windows handle checks |
| main, Run 33760167252 | Windows | 782 passed in 85.01s | 1 POSIX permission check |
| main, Run 33760167252 | Linux | 781 passed in 53.21s | 2 Windows handle checks |

Both runs executed all four mandatory Windows symlink scenarios. The sole
Windows skip is POSIX-only; missing symlink capability would fail the unchanged
CI precondition. Every required quality/test job and step passed on the expected
head/event. No unhandled test-thread warning was recorded.

The author's final local Windows full suite was 778 passed and five skipped in
68.48s; dedicated tests were 105 passed in 3.48s. Four local skips lacked symlink
privilege and one was POSIX-only. No local Linux execution is claimed. Both full
mypy commands ran on Windows and checked 49 source files; win32 static checking
is not native Linux evidence. The recorded final Ruff check reported 120 files.

The final local IP-14 measurements were fixture 1.533233s, catalog 0.023534s,
projection 0.013218s, Planner 0.051547s and call-window peak RSS 86.910156 MiB.
All 15,000 global and 11,250 projected identities were checked. This observation
introduces no performance threshold. Original WP-05/06 benchmarks measured
2.7735s / 60.95 MiB and 2.9163s / 60.59 MiB; WP-12 measured 2.634347s /
84.011719 MiB with 2.297831s fixture time separate. Their existing **15 seconds /
128 MiB** limits remain unchanged and run in both native full suites.

## Scope and rollback

The implementation publication scan covered 159 tracked files and thirteen
introduced branch blobs, with zero configured-pattern findings. It is bounded
asset/credential/address scan evidence, not a proof against every possible
secret pattern. Only the three Handoff files separate corrected code from final
delivery. This acceptance change contains ROADMAP and this record only;
product/tests/contracts, dependencies, CI, ADR/WP and original Handoff files are
unchanged. Its publication scan is separate from that thirteen-blob history.

The author actually exercised the code-only inverse in a new isolated checkout
at `3ef927dd5016e463b4eed910933f29052c8c7967`: revert that correction, then
`9b1278c208a1c4df724645d5514ae6bdd3fce801`. The clean working tree and index
exactly matched execution baseline `dd4b183a5ede1eb75e73c4268789ac7eb759fe1b`.
The non-author reviewer independently checked the retained scratch and matching
tree IDs. This proves the two fixed code commits only; it excludes subsequent
Handoff documents, the shared merge and this acceptance record. No shared or
production state was rolled back. A later rollback needs its exact commits and
scope reviewed and verified separately in isolation.

## Accepted limits

Catalog validation trusts committed membership metadata. It cannot attest to
complete history, omitted archives, source association or Raw/hash provenance.
The pure projection does not persist new membership or a revision. A shared
party that remains UNCHANGED still requires durable membership when committed.

One consistent durable generation, stale-state protection, atomic Raw/revision/
membership/outbox commit, enrollment, real Excel Save/SaveAs/OneDrive marker
retention and annual rollover with crash recovery remain separate prerequisites.
Guard and mutation evidence is focused regression protection, not a universal
Python security sandbox or storage proof. Synthetic filenames, rows and
metadata are not evidence of real workbook performance or Owner acceptance.

No real workbook/copy, database/outbox commit, financial/RS change, production
deployment, protected data action or final Owner testing is claimed. G1's
repeatable end-to-end import criterion remains open. This acceptance issues
no next work package and does not change the approved contract.
