# Codex acceptance: WP-11 Source binding

- Decision: accepted and merged; synthetic logical source-routing evidence only.
- Decision owner: Codex Project Manager under O-46/O-49.
- Date: 2026-09-03.
- Approved execution baseline: `b9502a8f9050257c3dd4c19f9a99d19e0e608a62`.
- Tested implementation: `e0f4ac3abe27085f726c83d0f6161e01aba692e4`.
- Independently reviewed delivery: `cf7773b115c8318d444e95df3b630625a23e9ded`.
- Implementation PR: [#38](https://github.com/mohsenbarari/accounting-bot-public/pull/38).
- Merge commit: `ece6b6397fb04b6c0e33d460c43b034e1adcca9c`.
- Pre-merge CI: [Run 33736482076](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33736482076), successful on the reviewed head.
- Post-merge CI: [Run 33736784918](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33736784918), successful on the exact merge commit.
- Gate G1 remains **OPEN / IN PROGRESS**. No next work package is issued here.

## Review responsibility and decision

Codex implemented the issued WP-11. A separate existing Codex agent that had not
authored this implementation reviewed the exact delivery against O-76, ADR-0014
and SB-01..14. It examined the product module, nine exports, README, tests/import
probe and all three Handoff documents. Its disposition was **no actionable findings**.
It confirmed baseline-to-code-to-delivery ancestry, the clean implementation
checkout and the eight-file scope, including preservation of existing tests.

The reviewer independently ran all dedicated tests on Windows Python 3.13.15:
**71 passed in 1.16s**, exit 0. Handoff validation, scope and whitespace checks
also returned exit 0. Its scale case used 15,000 prior rows, 100 sources and
1,000 lookups: fixture 0.140573s, construction 0.000106s and lookup batch 0.000739s.
It reviewed the author's full-suite, static-check and scan evidence without
claiming to have rerun those commands. No tracked file or Git ref was changed
by the reviewer. Its report, exact commands, raw combined output and exit codes
are retained locally as `independent-review.md`, `independent-review.log` and
`independent-review-results.json` in the WP-11 implementation review artifacts.

Codex Project Manager accepts the bounded package using that non-author review,
the [acceptance matrix](acceptance-matrix.md), the [execution results](test-results.txt)
and the native CI evidence below. The Owner explicitly authorized publication of
the exact eight-file delivery to the operational public repository, PR creation,
native CI and merge after successful checks. The PR was merged with the reviewed
head guard after both required jobs succeeded. No administrator bypass, direct
main commit, force push or branch deletion occurred. The merge tree equals the
reviewed delivery tree; the clean local main advanced by fast-forward only.

This record resolves the review and native CI items that were pending at the
implementation handoff. The original three Handoff files remain unchanged as
the record of that earlier execution.

## Accepted evidence

- SB-01/SB-11: the exact version and nine-symbol API, frozen/slotted types and
  computed results retain supplied nested objects. The fresh-import probe guards
  target-module execution after code/dependency loading; an injected write fails
  for the specific guard reason with exit 73 while ordinary execution succeeds.
  Package/submodule identities remain intact. Post-fixture guards cover file,
  network, clock, random, UUID and thread seams, with a negative control. These
  focused guards are regression evidence, not a universal Python purity sandbox.
  Deliberate iterator/internal failures and cancellation propagate unchanged.
- SB-02/SB-03: UUIDv7 objects retain identity and obey variant/version validation;
  annual years use the accepted date parser with exact-int validation and no
  current-year floor. State values require the exact enum, rejecting raw strings
  and equal foreign enums. ACTIVE requires no final hash; ARCHIVED requires exact
  lowercase 64-hex text. Invalid key/prior/hash inputs fail with fixed errors.
- SB-04/SB-08: registry construction consumes a supplied iterable once, makes a
  defensive canonical tuple and enforces unique source IDs/years and at most one
  active source. Empty and archive-only registries work. Its private immutable
  source-ID index supports lookup. Frozen results derive their own disposition,
  record and prior; injected derived fields fail. Nested priors are hidden in
  repr and synthetic sensitive input markers are absent from public errors.
- SB-05..SB-07: both public resolution entry points return the exact active record
  and prior for the matching key. Archives and unknown IDs select no operational
  prior; an unknown ID claiming a registered year has no fallback. A known ID
  with the wrong year raises the typed error. Resolution never calls the Planner,
  Reader or watcher, manufactures a first-import prior or grants import permission.
- SB-09/SB-10: empty four-sheet, party-only, undated and mixed-year snapshots retain
  their independent requiredness/fiscal evidence while logical routing is stable.
  Sixty Hypothesis examples compare actual record permutations and routing
  changes with an independently declared model, including unknown IDs, year
  mismatches, canonical order, exact selected objects and unrelated records.
- SB-12: explicit WP-04 composition uses deliberately prepared archived A and
  active B prior views. Independently expected IDs/actions/revisions cover no
  changes, edit, deletion, complete empty B and first import into an empty B
  prior. B's deletions never target A; archived/unknown resolution does not invoke
  planning. The test checks exact identities and revisions, not counts alone.
- SB-13: a shared party UUID in A/B historical views retains the distinct supplied
  revisions without merging or renumbering. A 15,000-row prior, 100 annual records
  and 1,000 lookups are checked with guards against traversing prior identities
  or scanning annual records during lookup. Construction is O(s log s) for s
  sources; lookup uses the source-ID index without copying prior rows. Measured
  timings are observations and introduce no new performance threshold.
- SB-14: all 459 baseline cases are retained unchanged; 71 new cases produce
  530 collected tests. Frozen sync/lock, Ruff, full mypy and win32 mypy, dedicated
  and full tests, original benchmarks, Handoff validation, whitespace and public
  scans passed. Native CI supplies the four symlink cases unavailable locally.

## Native CI evidence

| Execution | Platform | Full suite | Expected skips |
|---|---|---|---|
| PR #38, Run 33736482076 | Windows | 529 passed in 65.66s | 1 POSIX permission check |
| PR #38, Run 33736482076 | Linux | 528 passed in 39.62s | 2 Windows handle checks |
| main, Run 33736784918 | Windows | 529 passed in 61.30s | 1 POSIX permission check |
| main, Run 33736784918 | Linux | 528 passed in 46.51s | 2 Windows handle checks |

All four mandatory Windows symlink scenarios execute in both CI runs. Windows'
only skipped case is the POSIX permission check. The unchanged capability guard
fails if the mandatory symlink capability is unavailable in CI. Both platforms
also passed lock verification, frozen sync, Ruff and mypy.

The author's local full run reported 525 passed and five skipped in 58.54s:
four Windows symlink-privilege limitations and one POSIX-only case. Those local
skips were not passing evidence; native CI resolves the four-case gap. No local
Linux execution is claimed. Original Reader/acquisition benchmark limits remain
15 seconds / 128 MiB. Recorded local Windows results were 2.7602s / 61.19 MiB
and 2.8524s / 61.45 MiB; both tests also run unchanged in each native full suite.

## Scope and rollback

The implementation delivery scan covered 135 tracked files and all eight
introduced branch blobs with zero reported prohibited-asset, credential or
host-address findings. Only three Handoff files separate tested code from the
delivery. This acceptance change contains only ROADMAP and this review record;
product code, tests, dependencies, CI, ADR/WP and earlier Handoff files are intact.

The code rollback was executed in an isolated clean clone by reverting fixed
commit `e0f4ac3abe27085f726c83d0f6161e01aba692e4` without committing. Both worktree
and index exactly matched baseline `b9502a8f9050257c3dd4c19f9a99d19e0e608a62`.
That evidence covers only the code stage, excluding later Handoff documentation,
the shared merge and this acceptance record. No shared or production state was
rolled back.

## Accepted limits

`source-binding.v1` operates on trusted caller metadata. It does not attest that
a logical key and raw rows came from the same stable workbook acquisition or
that the supplied prior is a durable, correct membership projection. Physical
marker format/reading, initial enrollment, global identity/home-sheet/revision
continuity and atomic final import/archive/activation with crash recovery remain
separate prerequisites for operational import.

Shared-party fixtures prove preservation of supplied historical views only.
ACTIVE is not import permission; ARCHIVED is not an empty successful change plan.
Requiredness, fiscal evidence and financial/RS rules retain their separate scope.
All fixtures are synthetic. No real workbook, Excel/OneDrive/COM acceptance,
UUID writeback, database/outbox commit, deployment or final Owner testing is
claimed. G1's repeatable end-to-end import criterion remains open.
