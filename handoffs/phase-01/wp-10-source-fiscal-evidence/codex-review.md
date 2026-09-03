# Codex acceptance: WP-10 Source fiscal evidence

- Decision: accepted and merged; synthetic fiscal-year observations only.
- Decision owner: Codex Project Manager under O-46/O-49.
- Date: 2026-09-03.
- Approved execution baseline: `d14ef4abbe858ca99501d5eac2c44c7179388410`.
- Tested implementation code: `b9230689c3d9273231d017d71a1ce6873bd8c2a7`.
- Independently reviewed delivery: `e99c8dc2180ed65b244686346967f5152da8cb81`.
- Implementation PR: [#35](https://github.com/mohsenbarari/accounting-bot-public/pull/35).
- Merge commit: `057377582cd044dd61b41b89f3299604b9579589`.
- Pre-merge CI: [Run 33728526844](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33728526844), both jobs successful on the reviewed head.
- Post-merge CI: [Run 33729096159](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33729096159), both jobs successful on the exact merge commit.
- Gate G1 remains **OPEN / IN PROGRESS**. No next work package is issued here.

## Review responsibility and decision

The Owner instructed Codex to implement the issued WP-10 and then explicitly
authorized a separate non-author reviewer. The `wp10_independent_review` agent
examined the exact delivery against ADR-0013 and FE-01..14: product code, six
public exports, README, import probe, independent test oracles and Handoff.
Its disposition was **no actionable findings**. It independently confirmed the
linear ancestry, clean working tree and eight-file implementation/delivery scope.

The separate reviewer ran all 56 dedicated tests on Windows Python 3.13.15:
**56 passed in 2.16s**, exit 0. Its 15,000-row result separated fixture construction
(0.872605s) from evaluation (0.104360s). The Handoff validator and whitespace/scope
checks also passed. It inspected the recorded full-suite results without claiming
to have rerun the full suite. No product, test or documentation files were changed
by the reviewer. Its independent command output was captured in the agent tool
response; no separate raw log file was saved by that reviewer.

Codex Project Manager combines that non-author review with the acceptance matrix
and native CI evidence below to accept WP-10. The Owner explicitly authorized the
reviewed public payload, PR and CI, then instructed continuation after the PR was
reported ready for merge. Normal PR protection and an exact-head match were used;
no administrator bypass, direct main commit, force push or branch deletion occurred.
The merge tree equals the independently reviewed delivery tree.

This acceptance record resolves the review and native CI items that correctly
remained pending in the implementation-time Handoff. The original three Handoff
documents are retained unchanged as the record of that earlier execution.

## Accepted evidence

- FE-01/FE-11: exact version and six-symbol public API, immutable computed report,
  inert target-module execution, repeatability and original object retention.
  The subprocess probe guards target execution after code/dependency loading;
  a file write injected into that target scope fails with the specific guard
  exception and exit 73, while ordinary import succeeds. Parent/submodule export
  identities remain intact. Evaluation guards file, network, clock, random, UUID
  and thread seams and includes a specific write negative control. This focused
  regression guard is not a claim of a universal Python purity sandbox.
- FE-02..FE-05: all three transaction sheets contribute every raw date or None;
  business-party rows contribute only their separate count. One-year, mixed-year,
  all-null, empty and party-only snapshots have exact independently expected rows,
  years and counts. Deleting every transaction row remains valid; explicit WP-09
  and Planner results are independent of fiscal observation.
- FE-06/FE-07: declared Persian/Arabic/ASCII date vectors, whitespace/separators,
  Nowruz and leap/end-of-year dates use the accepted parser. Invalid dates fail
  upstream construction. Names, notes, contact/amount text, UUID timestamps and
  misleading filenames do not select years; opening-looking rows remain ordinary
  observations. Formula/cache-only changes cannot supply source dates.
- FE-08/FE-09: metadata checks transaction sheets, UUIDv7, exact integer years and
  positive counts with fixed typed messages. None is only a row-year value.
  Direct report construction computes its own rows/counts; injected fields and
  ordinary mutation fail. Raw markers do not appear in report/metadata repr or
  public input errors. Cancellation and unexpected failures propagate unchanged.
- FE-10: 35 generated cases compare complete metadata/counts against a declared
  oracle under actual sheet, row and mapping-key reversals. The tests assert
  changed input order and equal raw values. A single date-year/None transition
  changes exactly one row's metadata and the independently expected counts.
- FE-12: all 15,000 synthetic rows are accounted for: 13,500 transaction rows and
  1,500 business-party rows, including null dates and three declared years.
  Every metadata entry and year count is compared with the independent oracle;
  snapshot/row/raw/value/UUID/hash identity and values are retained. The bounded
  scan/count/sort algorithm is O(n log n) time at most and O(n) metadata, without
  raw-mapping copies or hash rebuilding. No new performance ceiling is invented.
- FE-13: four synthetic XLSX generations plus an empty workbook compose the
  existing Reader, requiredness and Planner APIs. Missing/formula dates remain
  None, actual physical row reorder and formula/cache changes preserve evidence
  and Planner outcomes, and one raw date edit changes one expected row. Original
  workbook bytes are unchanged. All calls to other pure checks are explicit.
- FE-14: all 403 baseline cases remain unchanged; the 56 new cases yield 459
  collected tests. Frozen sync/lock, Ruff, full mypy and win32 mypy, original
  benchmarks, Handoff validation, whitespace and publication scans passed.
  Native CI supplies the four symlink cases unavailable locally. See the retained
  [acceptance matrix](acceptance-matrix.md) and [execution results](test-results.txt).

## Native CI evidence

| Execution | Platform | Full suite | Expected skips |
|---|---|---|---|
| PR #35, Run 33728526844 | Windows | 458 passed in 62.28s | 1 POSIX permission check |
| PR #35, Run 33728526844 | Linux | 457 passed in 45.82s | 2 Windows handle checks |
| main, Run 33729096159 | Windows | 458 passed in 61.48s | 1 POSIX permission check |
| main, Run 33729096159 | Linux | 457 passed in 45.25s | 2 Windows handle checks |

All four mandatory Windows symlink scenarios execute in both native CI runs.
Windows' only skipped case is the POSIX permission check. The unchanged symlink
capability guard fails, rather than skips, if CI lacks the required capability.
Both platforms also passed lock verification, frozen sync, Ruff and mypy.

The recorded local full run passed 454 cases and skipped five: four missing
Windows symlink privileges and one POSIX check. A separate local CI=true probe
failed those four privilege preconditions with error 1314, as required. Those
failures were disclosed as environment limitations, not passing evidence.
Native CI resolves the four-case gap. No local Linux execution was claimed.

Original 15,000-row benchmark gates remain 15 seconds / 128 MiB. Recorded local
Windows Reader and acquisition/Reader results were respectively 2.8131s / 60.09 MiB
and 2.8452s / 61.66 MiB. Both benchmarks also run unchanged in the native full suites.

## Artifact and rollback checks

The final delivery scan covered 126 tracked files and all eight introduced branch
blobs, with zero reported prohibited-asset, credential or host-address findings.
The tested-code-to-delivery diff contains only the three original Handoff files.
This acceptance change contains only ROADMAP and this separate review record;
product code, tests, dependencies, CI and issued ADR/WP contracts remain unchanged.

The fixed-code rollback was executed in an isolated clean clone by reverting
`b9230689c3d9273231d017d71a1ce6873bd8c2a7` without committing. Both worktree and index
exactly matched baseline `d14ef4abbe858ca99501d5eac2c44c7179388410`. That proof covers
the code stage; it excludes the later Handoff documentation, shared merge and this
acceptance record. No shared branch or production asset was rolled back.

## Accepted limits

`source-fiscal-evidence.v1` observes raw date years; it does not select an active
year, classify an archive, reject mixed/undated sources or authorize import/deletion.
Durable source binding, fiscal registry partitioning, opening transfers, financial
and RS semantics, persistence and crash recovery require separate decisions/work.

All fixtures are synthetic. No reference workbook, real accounting data, Excel/
OneDrive or COM/UUID writeback acceptance, deployment or final Owner testing is
claimed. G1's end-to-end repeatable import criterion remains open.
