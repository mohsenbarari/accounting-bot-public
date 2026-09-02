# Codex acceptance: WP-08 Source watch runtime

- Decision: accepted and merged; synthetic native watcher/Reader boundary only.
- Reviewer: Codex Project Manager, independent of the implementer.
- Date: 2026-09-02.
- Baseline: `c7520c94606f37720f3e023b753e36d1c1f433a3`.
- Reviewed head: `2f51f2ef18a09f3c931dde46de5c3a1dbb12a4f3`.
- Implementer's tested-code commit: `809572f1b0e996c114ab5d5b6719ad151aab10ff`.
- Implementation PR: [#28](https://github.com/mohsenbarari/accounting-bot-public/pull/28).
- Merge commit: `0d1df855c39ab7ac5b8d5492915fb1014dc096cf`.
- Final CI: [Run 33666088469](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33666088469), both jobs successful on the reviewed head.
- Owner explicitly authorized public publication, PR/CI and merge after both jobs passed.
- Gate G1 remains **OPEN / IN PROGRESS**. No subsequent work package is issued here.

## Independent review outcome

All findings from eight review passes are closed, including R7/R8; R2 remains closed.
The final remediation changes only tests and handoff evidence. Runtime blob
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

## CI evidence

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
