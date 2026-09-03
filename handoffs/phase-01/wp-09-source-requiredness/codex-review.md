# Codex acceptance: WP-09 Source requiredness

- Decision: accepted and merged; synthetic required-field preflight only.
- Decision owner: Codex Project Manager under O-46/O-49.
- Date: 2026-09-03.
- Approved implementation baseline: `d59e20accfa57cfc67de8a8ca6d31deba8a6d405`.
- Last product correction: `f142de97aec65242be8994a6b8fd73537c1f0c12`.
- Incoming continuation: `d1dacdf0aa0ce3747e2db610c664167a198ad333`.
- Final tested code: `0fc4c6ae85edbc69de0893e154c64630dc9a21fc`.
- Reviewed delivery: `05e163555d8290d2111645cb93dcbad66bd8e7f4`.
- Implementation PR: [#32](https://github.com/mohsenbarari/accounting-bot-public/pull/32).
- Merge commit: `fb077a7929a831dd7a3cee201fc16ea4da571c15`.
- Pre-merge CI: [Run 33721864845](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33721864845), both jobs successful on the reviewed delivery.
- Post-merge CI: [Run 33722627358](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33722627358), both jobs successful on the exact merge commit.
- Gate G1 remains **OPEN / IN PROGRESS**. No subsequent work package is issued here.

## Review responsibility and decision

The Owner explicitly authorized Codex to complete the bounded continuation of the
implementation after Antigravity's last delivery. Codex changed only the focused
tests/helper and handoff documents in that continuation. Product code under `apps`
and `packages` is unchanged from the independently reviewed R1 product correction.

The Owner also authorized a separate Codex reviewer. The `wp09_independent_review`
agent did not author the continuation and reviewed its tests, helper and handoff,
reading adjacent product code as needed. Its disposition on the exact delivery was
**no actionable findings**. It independently ran the SR-01/SR-12 subset on Windows
Python 3.13.15: **14 passed, 39 deselected in 1.98s**. It assessed the recorded full
suites and rollback evidence without claiming to have rerun those suites itself.

Codex Project Manager combines that separate review with the earlier product
review, the acceptance matrix and the native CI evidence below to accept WP-09.
The Owner explicitly authorized publication, PR/CI and then the ready transition
and merge. The merge used an exact delivery-head match through the normal PR path.
The merge tree equals the reviewed delivery tree. This record completes the CI
and review prerequisites that were correctly pending when the implementation
handoff was written; that historical handoff is retained unchanged.

## Accepted evidence

- SR-01/SR-10: pure evaluation, fixed version/exports, raw-object identity retention
  and fresh target-module execution under a focused subprocess guard. Seven negative
  controls reject actual import-time file access with a dedicated exception and
  exact exit marker; an unrelated subprocess failure cannot satisfy the control.
  Parent and submodule export identities survive success and rejection.
- SR-02/SR-03: an independent matrix covers all 16 required sheet/field entries and
  all 9 required text fields. None, blank text and present zero remain distinct.
  Controlled omissions of a required-field check and a blank-text check each cause
  the two expected failures, demonstrating sensitivity to those missing rules.
- SR-04 through SR-09: valid signed/zero representations and optional or unresolved
  rows are preserved; upstream invalid inputs are rejected at their own boundary.
  Reports aggregate exact ordered issues and derive their counters. Constructors
  enforce metadata, immutability and actual reason-enum membership; raw values do
  not appear in public error messages or report/issue representations.
- SR-11/SR-12: synthetic XLSX Reader composition preserves file bytes and ignores
  excluded formula/cache changes. Independent deterministic and generated oracles
  compare actual sheet, row and mapping-key permutations, with changed-order and
  preserved-value assertions on the generated inputs.
- SR-13: all 15,000 synthetic rows retain row/raw-mapping identities and hashes;
  the full ordered tuple of 3,000 expected issues matches the independent oracle.
  Evaluator duration is recorded separately from fixture construction. No new
  performance ceiling is invented.
- SR-14: all 350 baseline tests are retained without edits, plus 53 WP-09 cases.
  Frozen synchronization, lock verification, Ruff, full mypy, win32 mypy, the
  handoff validator, whitespace checks and publication scans passed. See the
  retained [acceptance matrix](acceptance-matrix.md) and [raw results](test-results.txt).

The import probe guards execution of the target requiredness module. Dependency
bootstrap and preparation of its loader/code occur outside that guard. Acceptance
does not interpret this focused regression probe as a universal purity sandbox.

## Native CI evidence

| Execution | Platform | Full suite | Expected skips |
|---|---|---|---|
| PR #32, Run 33721864845 | Windows | 402 passed in 69.96s | 1 POSIX permission check |
| PR #32, Run 33721864845 | Linux | 401 passed in 43.43s | 2 Windows handle checks |
| main, Run 33722627358 | Windows | 402 passed in 60.25s | 1 POSIX permission check |
| main, Run 33722627358 | Linux | 401 passed in 41.62s | 2 Windows handle checks |

All four Windows symlink scenarios executed in native CI. There were no local
privilege skips in CI and the mandatory failure rule for missing CI symlink
support remains intact. Both platforms passed lock verification, frozen sync,
Ruff format/lint and mypy.

The initial post-merge Linux job failed before tests during `Set up uv`: fetching
the version manifest timed out. Only that failed job was rerun with the same
commit and workflow. Attempt 2 passed and retained the original successful Windows
result. This was an infrastructure timeout, not a test failure or a code correction.

Recorded local validation on the exact tested code also passed: Windows 398 passed
and 5 platform/privilege skips in 55.25s; Linux 401 passed and 2 Windows-only skips
in 71.95s. All 53 dedicated WP-09 cases passed on each platform. CI supplies the
four Windows symlink cases unavailable in the local privilege context.

The original synthetic 15,000-row Reader/acquisition limits remain 15 seconds and
128 MiB. Recorded Linux Reader/acquisition results were 10.5979s / 61.96 MiB and
11.0432s / 61.68 MiB; Windows results were 2.7084s / 61.58 MiB and 2.8835s / 60.09 MiB.

## Artifact and rollback checks

The delivery publication scan covered 117 tracked files and 25 introduced branch
blobs, with zero reported prohibited-asset, credential or host-address findings.
This is bounded scan evidence. The tested-code-to-delivery diff contains only the
three handoff documents; the separate reviewer verified that scope and its claims.

The recorded rollback from tested code reverts the eight fixed implementation and
correction commits through `0fc4c6a` in an isolated checkout. The separate reviewer
independently checked its zero diff against the approved baseline. This proves the
documented code-stage rollback, excluding the two later delivery-document commits
and this acceptance record. The delivery and main branches were not rolled back.

## Accepted limits

`passes_requiredness` proves only presence under ADR-0012. It does not prove
financial validity or authorize a persistent import. Fiscal/archive eligibility,
code/direction semantics, identity/item resolution, RS validation, SQLite/Outbox
transactions and crash recovery require separate work and evidence.

All test inputs are synthetic. Acceptance does not cover the reference workbook,
real Excel/OneDrive behavior, COM/UUID writeback, deployment or final Owner testing.
G1's end-to-end repeatable import criterion remains open.
