# Handoff: WP-09 Round 3 bounded continuation

## Identity

- Phase: 1; Work Package: WP-09 source-requiredness.v1.
- Branch: `antigravity/phase-01-source-requiredness`.
- Approved execution baseline: `d59e20accfa57cfc67de8a8ca6d31deba8a6d405`.
- Prior completed delivery: `fd857e9ddaf352f7c7acf81848022052069e1861`.
- Incoming Antigravity partial completion: `d1dacdf0aa0ce3747e2db610c664167a198ad333`.
- Tested code commit: `0fc4c6ae85edbc69de0893e154c64630dc9a21fc`.
- Earlier local handoff: `1de1a7823e35c0d3934dfaee981f0c2d99403e09`; the current
  delivery refreshes that documentation with the authorized Linux evidence.
- Product code remains identical to `f142de97aec65242be8994a6b8fd73537c1f0c12`; R1 stays closed.
- Implementers: Antigravity through d1dacdf; Codex for the bounded continuation.
- Independent review of the Codex-authored test correction: PENDING.
- Delivery is a subsequent documentation-only commit; its SHA is returned by Git after commit, not embedded recursively in this file.
- G1: OPEN / IN PROGRESS.

## Scope

The Owner asked Codex to inspect the last Antigravity prompt's progress and continue
the work after the implementation service became unavailable. Existing O-46 and
the implementer skill name Antigravity as implementer and forbid self-approval.
This handoff records the bounded Owner-requested continuation, preserves separate
acceptance, and does not claim a general governance change or amend ROADMAP.
The Owner subsequently explicitly authorized transfer of the test corrections
and evidence runner to the existing development server, native Linux validation,
and recording this continuation in `/srv/accounting-bot/workspace`.

The Round 3 prompt allowed only WP-09 tests, a focused helper and these three
handoff files. Antigravity committed real row/key permutations and subprocess
isolation but stopped before refreshing the evidence. Its guard still allowed
application reads of code-looking and timezone-looking paths, and its negative
actions ran before the target import. Codex reproduced a permitted application
read of the requiredness source file on d1dacdf, then completed only these tests.

- R2: retained actual row-only, key-only and combined variants across four sheets;
  strengthened generated variants with explicit order-change and value-equality assertions.
- R3: execute the fresh target module in a subprocess through its actual import
  loader. Load that exact code before installing the guard; forbid application
  builtins/io/os/pathlib I/O without a filename or directory exemption while
  executing module code. Network, clocks, UUID generation and thread start remain guarded.
- Seven negative controls execute inside the target import, with an entered marker,
  guard-specific exit 73 and failed-module cleanup: Path.open write; builtins, io,
  os and pathlib reads; a real contracts source path; and a timezone-looking fixture.
- Parent package/submodule identities are captured before every child and checked
  in finally afterward, including rejected imports. The genuine enum remains usable.
- R4: refreshed evidence, platform separation and fixed-code rollback below.

All upstream product code/tests, dependencies, CI rules, benchmark limits, ADRs,
WP specifications and ROADMAP remain unchanged. No persistence or financial
admission behavior is introduced. No WP-10 work is included.

## Roadmap traceability

| Authority | Contribution |
|---|---|
| O-74 / ADR-0012 / WP-09 SR-01 and SR-12 | Inert target execution and independent permutation evidence |
| WP-09 SR-02/SR-03/SR-13 | Preserve accepted independent matrices, omission controls and exact 15,000-row results |
| O-68/O-69/O-70 | Preserve source values, immutable snapshots, hashes and Reader behavior |
| O-46/O-47/O-48 | Bounded continuation, separate review, evidence and reversible code commits |
| G1 / Roadmap 5.2 and 5.4 | Requiredness is one prerequisite; passing does not authorize import commit |

## Changed files

Relative to the incoming Antigravity code commit:

| File | Change |
|---|---|
| tests/test_source_requiredness.py | Import subprocess calls, negative controls, parent identity checks, generated-order assertions |
| tests/source_requiredness_import_probe.py | Focused subprocess-only loader and side-effect guard |
| handoffs/phase-01/wp-09-source-requiredness/handoff.md | This continuation handoff |
| handoffs/phase-01/wp-09-source-requiredness/acceptance-matrix.md | Current SR-01..14 nodes and coverage gaps |
| handoffs/phase-01/wp-09-source-requiredness/test-results.txt | Captured validation evidence |

## Schema and migrations

None. Product code is unchanged from the closed R1 commit. No database or real
workbook has been accessed or changed for this continuation.

## Commands and exit codes

Exact commands and captured output are in test-results.txt. Paths naming the local
review checkout or uv executable are normalized there; original logs remain with
the review artifacts. XML in parameter IDs is entity-escaped and trailing whitespace
is trimmed as disclosed there. Results, metrics and exit codes remain unchanged.

- Frozen offline sync and lock check: 0.
- Ruff format and lint: 0 (82 formatted files).
- Full mypy and win32 mypy: 0 (31 source files each).
- Dedicated WP-09, full suite and each unchanged 15,000-row benchmark: Windows exit 0.
- Linux command results are recorded separately below and in test-results.txt.
- Baseline whitespace, product equality and upstream-test equality: 0.
- Both in-memory omission probes: expected pytest exit 1; each mutant causes two failures.
- Fixed-code rollback: 0; exact baseline tree equality after eight reverts.
- Code publication scan: 117 tracked files and 19 introduced blobs, zero findings.
- Handoff validator is run after this evidence is written; its output accompanies delivery.

## Tests and evidence

| Platform | WP-09 | Full suite |
|---|---|---|
| Windows local | 53 passed in 2.83s | 398 passed, 5 skipped in 55.25s |
| Linux local | 53 passed in 5.78s | 401 passed, 2 skipped in 71.95s (0:01:11) |

Windows skips are the four existing symlink scenarios lacking local privilege and
one existing POSIX-only permission scenario. No skips or CI policy were changed.
Unhandled-thread warnings were promoted to errors for the full run.

Windows Reader: 2.7084 seconds / 61.58 MiB peak RSS.
Windows acquisition plus Reader: 2.8835 seconds / 60.09 MiB peak RSS.
The approved 15-second / 128-MiB limits are unchanged.

Linux benchmark evidence:

`[WP-05 BENCHMARK] 15,000 active rows -> read_build_seconds: 10.5979s | baseline_current_rss_mib: 30.16 MiB | call_peak_rss_mib: 61.96 MiB | rows: 15000 | platform: linux`

`[WP-06 BENCHMARK] 15,000 active rows (Acquisition + Reader) -> duration: 11.0432s | baseline_current_rss_mib: 29.95 MiB | call_peak_rss_mib: 61.68 MiB | rows: 15000 | file_bytes: 549787 | file_sha: fa34b81daef9da28afa6a9202a87dab257a1bbf272d3c37be896bbfda6bcce29 | platform: linux`

`[SR-13 SCALE BENCHMARK] 15,000 rows -> eval_seconds: 0.0365s | fixture_build_seconds: 2.8191s | checked_rows: 15000 | failed_rows: 3000 | issues: 3000`


The 16-field None and nine-field text matrices remain independent of product
tables. Required-field omission yields two failures / 16 passes; blank-field
omission yields two failures / eight passes. Mutations exist only in the probe
process. The 15,000-row test still compares all 3,000 ordered issues and pre/post
row, mapping and hash identities; timings are separate from fixture generation.

## Assumptions and open items

- Linux: PASS on the exact tested code; see recorded Linux output.
- Native Windows/Linux CI: PENDING; Windows CI must run all four symlink scenarios.
- Independent review: PENDING for the Codex-authored test/helper changes.
- Guard scope is target module execution. Accepted package dependencies and the
  single loader code read are prepared outside that guard. This is a focused
  regression test, not an assertion that dependency bootstrap or Python itself is
  an I/O-free security sandbox.

## Risks

Passing requiredness is not financial validation or permission to commit an
import. Synthetic tests do not prove real Excel/OneDrive or end-to-end G1 behavior.
Implementation validation by the author must not be labeled independent acceptance.

## Rollback

Proof starts exactly at tested code `0fc4c6ae85edbc69de0893e154c64630dc9a21fc` in a separate clean clone. It includes
the incoming d1dacdf test commit and all earlier WP-09 code/handoff commits. It
excludes the later documentation commit containing this handoff.

Before applying any rollback elsewhere, inspect `git status` and the exact HEAD;
do not alter unrelated or uncommitted work. Reproduce first in a disposable clone:

```text
git rev-parse HEAD
git status --porcelain
git revert --no-edit 0fc4c6ae85edbc69de0893e154c64630dc9a21fc
git revert --no-edit d1dacdf0aa0ce3747e2db610c664167a198ad333
git revert --no-edit fd857e9ddaf352f7c7acf81848022052069e1861
git revert --no-edit 844421a0d4ffd6a7fb9a4d064084f469daf2ecc6
git revert --no-edit 528c313c57eba2ad8eb84296293fa9b8ef43484a
git revert --no-edit f142de97aec65242be8994a6b8fd73537c1f0c12
git revert --no-edit cea3cf67baa73ff50f4a5783689506f84d8ef40e
git revert --no-edit 46020d6f45a4af66cc52fe660829516c5c792c98
git diff --quiet d59e20accfa57cfc67de8a8ca6d31deba8a6d405
git status --porcelain
```

This sequence was executed; all reverts succeeded, baseline diff returned 0 and
the scratch tree was clean. There is no database rollback claim or blind removal.

## Protected assets

- ROADMAP, ADR/WP, upstream code/tests, dependencies and CI policy unchanged.
- Synthetic fixtures only; no reference Excel, real data, credentials or production changes.
- Existing development history is preserved; no force-push, reset or destructive cleanup.
- No public push, PR, merge, deployment, Gate approval or next work package.

## Stop state

The bounded test corrections and recorded local evidence are delivered for
separate review. R1 and accepted product behavior remain closed. Outstanding
platform/CI and independent-review requirements above must be satisfied before
acceptance. G1 remains OPEN / IN PROGRESS.
