# Codex acceptance: WP-14 source Raw codec

- Decision: accepted and merged; synthetic pure Raw codec evidence only.
- Decision owner: Codex Project Manager under O-46/O-49.
- Date: 2026-09-04.
- Execution baseline: `a80f899aeb53feeffe9928d078de7aee68bdb6a4`.
- Initial implementation: `80339af0b882aad1180b03fed3807ae08220fe3d`.
- Corrected tested code: `f4895f992accbe6dff6f6910643962ae264343ac`.
- Independently reviewed delivery: `44d2976f9e6e52ee4d1ce7ddf791e3da955970f2`.
- Implementation PR: [#47](https://github.com/mohsenbarari/accounting-bot-public/pull/47).
- Merge commit: `e2afbf15b59df4e24b4302523531e8e27fd10566`.
- Pre-merge CI: [Run 33852528116](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33852528116), successful on the reviewed head.
- Post-merge CI: [Run 33852871012](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33852871012), successful on the exact merge.
- G1 remains **OPEN / IN PROGRESS**. No next work package is issued here.

## Review responsibility and decision

Codex implemented the issued WP-14. A separate Codex agent that did not author
the implementation reviewed O-79, ADR-0017, RC-01..14, the product/export/README
changes, all tests/helpers and the three Handoff documents. The final disposition
was **no open findings** on the delivery above. The reviewer changed no tracked
product, test or Handoff file and did not move a Git ref.

The adversarial review found one P2 defect in the initial implementation. An
invalid encoder root could expose a caller-controlled `__class__` descriptor while
the codec was constructing its input error; the resulting arbitrary exception
escaped the fixed public error contract and could include caller data. The R1
correction classifies the object from its actual type without invoking that
descriptor. Four focused error, spoofing, context and cancellation regressions
failed against the initial commit and passed at the corrected tested-code commit.
Valid input behavior and all prior contracts remained unchanged.

The non-author reviewer independently ran all **173 dedicated cases in 6.07s**
at the corrected code SHA and completed **229 additional checks**, including the
original root-boundary reproduction. It then verified the final Handoff hashes,
fixed ancestry and reviewed delivery. No product or evidence finding remains.
The author's local full suite and static checks are separate evidence; the reviewer
did not claim an independent rerun of those broader commands.

Codex Project Manager accepts this bounded package using the non-author review,
the [acceptance matrix](acceptance-matrix.md), [handoff](handoff.md), retained
[test evidence](test-results.txt) and native CI below. The Owner explicitly
authorized publication of the exact implementation delivery, its PR and normal
merge after successful Windows/Linux CI. Merge used an exact-head guard with all
required jobs successful; no administrator bypass, direct main commit, force push
or branch deletion occurred. The merge tree equals the reviewed delivery tree;
clean local main advanced by fast-forward only.

This acceptance resolves the review and native-CI items marked pending in the
original Handoff. Those three documents remain unchanged as historical execution
records. The independent reports, raw command logs and post-merge verification
remain in the local WP-14 implementation review artifact.

## Accepted evidence

- RC-01..04: the fixed five-symbol API and version initialize without scoped
  side effects. Four independent complete Golden byte vectors cover all input
  sheets. Exact original text and nulls, UTF-8 forms, int/text/Decimal types,
  signed zero, coefficient trailing zeros and Decimal exponents survive a complete
  encode/decode cycle. Decimal context, traps and flags remain unchanged on success
  and failure.
- RC-05..07: encoder and decoder reject wrong roots, malformed UTF-8/JSON,
  noncanonical spellings, numeric JSON tokens, invalid metadata, field order/count,
  UUID/hash forms, scalar tags and matching-hash grammar attacks with fixed typed
  errors. Actual-type input classification does not invoke a hostile class
  descriptor. Re-encoding decoded values reproduces the exact canonical bytes;
  input mapping permutations do not change the output or mutate caller state.
- RC-08/09: composition with WP-04 and WP-13 keeps representation-only changes
  UNCHANGED while a real Raw edit produces the expected EDIT/revision. Synthetic
  identified XLSX generations cover physical reorder, formula/cache change and
  Raw edit using one stable lease; source bytes, identity marker and cleanup are
  checked. Shared-party prior and archive Raw objects remain fixed and no durable
  membership is manufactured.
- RC-10..12: Hypothesis compares generated valid and invalid cases with independent
  scalar and wire oracles. Public messages, args and repr are fixed and marker-safe;
  ordinary causes and cancellation identity are preserved within the documented
  diagnostic boundary. Three isolated mutations break Decimal sign/scale,
  original date text and semantic-hash agreement at their intended assertions.
  Call-purity guards include an injected-write negative control, and no tracked
  mutation survives.
- RC-13: the complete 15,000-row replay checks every field, exact type/Decimal
  tuple, UUID and hash plus 15,000 complete Planner outcomes. It encoded 6,563,472
  bytes with fixture 1.7134080s, encode 0.1897502s, decode 0.9731411s, snapshot
  reconstruction 1.1848969s and Planner 0.0992505s; call-window peak RSS was
  120.23046875 MiB. The measurement creates no new performance threshold.
- RC-14: all 783 baseline node IDs and 29 existing test/helper files are unchanged;
  173 additive cases produce 956 collected tests. Frozen sync/lock, Ruff, both
  complete mypy commands, dedicated/full tests, Handoff validation, whitespace,
  scan, rollback and the existing WP-05/06/12 benchmarks passed. Native CI below
  resolves the four local Windows symlink-privilege gaps.

## Native CI evidence

| Execution | Platform | Full suite | Expected skips |
|---|---|---|---|
| PR #47, Run 33852528116 | Windows | 955 passed in 87.75s | 1 POSIX permission check |
| PR #47, Run 33852528116 | Linux | 954 passed in 60.51s | 2 Windows handle checks |
| main, Run 33852871012 | Windows | 955 passed in 93.64s | 1 POSIX permission check |
| main, Run 33852871012 | Linux | 954 passed in 60.17s | 2 Windows handle checks |

Both runs executed all four mandatory Windows symlink scenarios. The sole Windows
skip is POSIX-only; missing symlink capability would fail the unchanged CI
precondition. Every required quality/test job and step passed on the expected
head/event. No unhandled test-thread warning was recorded. GitHub emitted a
nonblocking maintenance annotation because `actions/checkout@v4` currently targets
deprecated Node 20 and is forced onto Node 24; it did not change any job conclusion
or WP-14 evidence.

The author's final local Windows full suite was 951 passed and five skipped in
80.20s; dedicated tests were 173 passed in 6.26s. Four local skips lacked symlink
privilege and one was POSIX-only. No local Linux execution is claimed. Both full
mypy commands ran on Windows and checked 55 source files; static win32 checking is
not native Linux evidence. The recorded final Ruff format check covered 131 files.

The existing bounded benchmarks remained below their accepted limits: WP-05 was
2.8214s / 58.61 MiB, WP-06 was 2.9271s / 60.20 MiB and WP-12 read was 2.7186293s /
82.84765625 MiB with 2.0581765s fixture construction recorded separately. Their
existing **15 seconds / 128 MiB** limits remain unchanged and passed in both native
full suites.

## Scope and rollback

The implementation publication scan covered 171 tracked files and thirteen
introduced branch blobs, with zero configured-pattern findings. This is bounded
asset, credential and address-pattern evidence, not proof against every possible
secret pattern. The implementation changed eight code/test/README files and three
Handoff files. It added no database, workbook, accounting data, dependency, CI
change or migration.

The implementer exercised the code-only inverse in a fresh isolated checkout at
`f4895f992accbe6dff6f6910643962ae264343ac`: revert that correction and then
`80339af0b882aad1180b03fed3807ae08220fe3d`. Both working tree and index exactly
matched execution baseline `a80f899aeb53feeffe9928d078de7aee68bdb6a4`.
The non-author reviewer checked the retained result and tree identity. This proves
the two fixed code commits only; it excludes the Handoff commit, shared merge and
this acceptance record. A later rollback requires its exact scope reviewed and
rehearsed separately in isolation.

This acceptance change contains `ROADMAP.md` and this record only. Product, tests,
contracts, dependencies, CI, ADR/WP and the original three Handoff documents remain
unchanged. Its publication scan and rollback boundary are separate from the
implementation history above.

## Accepted limits

The codec is a pure representation component. Its embedded semantic source hash
does not attest to payload-byte integrity, authenticity, source association,
revision ownership or complete history. Equivalent Raw representations can have
different codec bytes and the same source hash while remaining UNCHANGED. Public
error strings are safe; preserved causes and tracebacks can contain caller Raw data
and remain outside that guarantee.

The implementation processes one row at a time and does not retain a second
whole-workbook payload buffer. This is not admission control and adds no new time,
memory or row threshold. Parser/interpreter limits and inherited canonical-hash
expansion remain. Codec version changes after durable use require explicit migration
and replay evidence.

One consistent durable generation, stale-state rejection and atomic Raw/revision/
membership/change-event or outbox commit remain separate work. Membership must be
stored even when Planner reports UNCHANGED. Enrollment, financial validation,
crash recovery, real Excel Save/SaveAs/OneDrive marker retention, annual rollover
and server sync are not proven here.

No real workbook or copy, database commit, financial rule change, production
deployment, protected-data action or final Owner test is claimed. G1's repeatable
end-to-end import criterion remains open. This acceptance issues no next work
package and does not change the approved codec contract.
