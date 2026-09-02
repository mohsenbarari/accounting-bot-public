# Codex acceptance: WP-08 Source watch runtime

- Decision: accepted and merged, including post-merge WR-08 test stabilization; synthetic native watcher/Reader boundary only.
- Reviewer: Codex Project Manager, independent of the implementer.
- Date: 2026-09-02.
- Baseline: `c7520c94606f37720f3e023b753e36d1c1f433a3`.
- Reviewed head: `2f51f2ef18a09f3c931dde46de5c3a1dbb12a4f3`.
- Implementer's tested-code commit: `809572f1b0e996c114ab5d5b6719ad151aab10ff`.
- Implementation PR: [#28](https://github.com/mohsenbarari/accounting-bot-public/pull/28).
- Merge commit: `0d1df855c39ab7ac5b8d5492915fb1014dc096cf`.
- Initial implementation CI: [Run 33666088469](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33666088469), both jobs successful on the reviewed head.
- Stabilization baseline: `0d1df855c39ab7ac5b8d5492915fb1014dc096cf`.
- Stabilization tested code: `c77f85b69e353a3e057de15436a3f8498c8f9a65`.
- Stabilization reviewed head: `a593e937b4ac4fdfc2a9711f9ce94b71448e2954`.
- Stabilization PR: [#30](https://github.com/mohsenbarari/accounting-bot-public/pull/30).
- Stabilization merge: `fdaa98b0d5f7a38041c84b8f18d5eafbf02cdc2e`.
- Final correction CI: [Run 33670197344](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33670197344), both jobs successful on the stabilization head.
- Owner explicitly authorized public publication, PR/CI and merge after both jobs passed.
- Gate G1 remains **OPEN / IN PROGRESS**. No subsequent work package is issued here.

## Independent review outcome

The initial eight review passes closed R7/R8. The subsequent WR-08 timing finding
was reopened after a Windows CI failure and is now closed by independent review
and correction CI. R2 and R8 remain closed. The correction changes only one test
file and three handoff files. Runtime blob
`adb3e06bf926215730ebf7904445891cfaf9fd07` is unchanged from the accepted R2 correction.

The runtime uses one native nonrecursive observer and the existing WP-07 coordinator
and WP-06/WP-05 read pipeline. Successful results are delivered serially after lease
cleanup. Cycle and consumer admission resolve stop/fault races; teardown tracks and
joins owned workers. Independent failure identities and causes, including cancellation
and mixed exception groups, are preserved.

The final Case D scheduling replay held the runner after Condition.wait returned True
but before recording the result. The test waited for its dedicated ACK, then passed
after the runner was released. No arbitrary sleep or rescue notification was needed.

Separate native replays of WR-15 and WR-17 allowed the initial snapshot to arrive
before thread start returned to the test caller. Both tests retained cursor zero and
passed. Later generations retain dynamic cursors and content predicates, including a
fresh formula/cache generation evaluated by the independent WP-04 planner before the
raw-edit generation. No exact native delivery count is assumed. The replays left no
new live threads.

The nine rollback commits and their order exactly match Git. Codex applied the fixed
range through `git revert --no-commit` in a separate disposable checkout; its resulting
tree equals the baseline tree `46f916ffbc505c7876dd745de3c6e1ace44616cb`. The delivery
branch and real assets were not rolled back.

## Initial implementation CI evidence

| Platform | Full suite | Expected skips | Reader benchmark | Acquisition + Reader benchmark |
|---|---|---|---|---|
| Linux | 348 passed in 42.32s | 2 Windows handle tests | 2.9465s / 61.33 MiB | 3.0166s / 60.38 MiB |
| Windows | 349 passed in 55.71s | 1 POSIX permission test | 4.4782s / 61.02 MiB | 4.5422s / 60.11 MiB |

Both jobs passed lock verification, frozen dependency sync, Ruff format/lint and mypy.
All 36 WP-08 tests, including three native observer integration tests, ran on both
platforms. All four Windows symlink scenarios ran; there was no missing-privilege
skip. The mandatory CI failure rule for unavailable symlink support remains intact.
The original 15,000-row benchmark limits remain 15 seconds and 128 MiB.

Independent local full suites also passed: Linux 348 passed / 2 skipped in 62.58s;
Windows 345 passed / 5 skipped in 53.61s. Four local Windows skips reflected symlink
privileges; CI resolves that evidence gap. Background thread warnings were treated
as errors in these independent runs.

Independent local Reader and combined benchmarks were 10.4715s / 61.77 MiB and
10.0601s / 61.59 MiB on Linux; 2.7789s / 60.14 MiB and 2.8512s / 60.72 MiB on Windows.
Full-scope mypy and win32 mypy, Ruff, handoff validation and baseline whitespace checks
passed. The pre-publication scan covered 108 tracked files and all 44 branch blobs,
with zero findings in prohibited-asset, binary, credential and host-address patterns.
This is bounded scan evidence, not a guarantee of detecting every possible secret.

## Post-merge WR-08 stabilization and final evidence

After PR #28 merged, the first run of acceptance PR #29,
[33666981958](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33666981958),
passed Linux but failed Windows in the Cleanup phase of
`test_wr08_burst_coalescing_and_responsiveness_during_blocking`: only one of two
expected results had arrived. Its fixed 50 ms wait allowed the test's stop request
to cancel a pending follow-up on a slower runner. PR #29 remained draft pending
the correction. The initial successful implementation CI remains valid historical
evidence; this later failure is recorded separately.

PR #30 replaces arbitrary sleeps in all four phases with condition-entry ACKs and
second-delivery events. FakeClock advances after the runtime enters its wait, and
Stop follows confirmed second delivery. Cleanup also has a controlled delay gate.
Release waits assert success and finally blocks stop and join the test runners.

Codex independently held the second driver call in each of Consumer, Acquisition,
Reader and Cleanup until the test entered its completion wait. Each phase retained
one result and no stop request while held, then finished with exactly two reads,
two deliveries, STOPPED state and no new live threads. All stop calls followed the
second-delivery event. This probe passed; its two preimported-Hypothesis assertion
rewrite warnings were unrelated to thread handling.

| Platform | Full suite on correction CI | Expected skips | Reader benchmark | Acquisition + Reader benchmark |
|---|---|---|---|---|
| Linux | 348 passed in 41.87s | 2 Windows handle tests | 3.3070s / 61.35 MiB | 3.3141s / 61.53 MiB |
| Windows | 349 passed in 55.56s | 1 POSIX permission test | 4.6933s / 61.34 MiB | 4.6462s / 60.64 MiB |

Both final correction jobs passed lock verification, frozen sync, Ruff format/lint,
full mypy and the complete 350-case suite. All 36 WP-08 tests and all four Windows
symlink scenarios executed. The original 15s / 128 MiB benchmark ceilings hold.

Independent local full suites on the correction also passed with unhandled test
thread warnings treated as errors: Linux 348 passed / 2 skipped in 62.47s; Windows
345 passed / 5 skipped in 51.28s. Four local Windows skips reflected symlink privilege;
correction CI supplies that evidence. Local Reader/combined benchmarks were
10.5145s / 61.70 MiB and 10.6732s / 61.52 MiB on Linux, and 2.7540s / 61.02 MiB and
2.7696s / 58.95 MiB on Windows. Ruff, full mypy and win32 mypy (28 source files),
handoff validation and baseline whitespace checks passed. The correction publication
scan covered 108 tracked files and all four new branch blobs with zero findings.

Codex also replayed `git revert --no-commit c77f85b69e353a3e057de15436a3f8498c8f9a65`
from that tested commit in a disposable checkout. `git diff --quiet` against
`0d1df855c39ab7ac5b8d5492915fb1014dc096cf` exited 0. This proves the code-commit
rollback; the later handoff commit is separate. No delivery or production branch
was rolled back. The acceptance branch includes the correction merge before its
own final CI; no tests, product code or CI policy are changed by acceptance records.

## Accepted limits

The tests observe only generated synthetic files. This acceptance does not authorize
real Excel or OneDrive observation, COM/UUID writes, installation, databases, protected
data or production changes. Successful Reader delivery is not a committed import,
durable baseline or financial ACK.

The upstream native event queue has no contractual memory bound. Native event loss,
overflow reconciliation, automatic restart/resubscription and crash recovery remain
outside this package. Blocked I/O or consumer code can delay liveness detection and
shutdown; the runtime does not promise a hard shutdown deadline. Full end-to-end G1
evidence remains outstanding.
