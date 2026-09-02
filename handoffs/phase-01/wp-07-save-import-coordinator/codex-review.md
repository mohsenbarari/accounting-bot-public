# Codex acceptance: WP-07 Save import coordinator

- Decision: accepted and merged; synthetic scheduling/Reader boundary only.
- Reviewer: Codex Project Manager, independent of the implementer.
- Date: 2026-09-02.
- Baseline: `55b965dc6781c9371045f2ecf905ec27f52b64a5`.
- Reviewed head: `83a8facb6f86e7971171953e3dfe88dafe33c957`.
- Implementation PR: [#25](https://github.com/mohsenbarari/accounting-bot-public/pull/25).
- Merge commit: `fe7e19287e66d208124487b96087a1b85f60831f`.
- Final CI: [Run 33632031865](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33632031865), both jobs successful on the reviewed head.
- Owner explicitly authorized public publication, PR/CI and merge after both jobs passed.
- Gate G1 remains **OPEN / IN PROGRESS**. No WP-08 is issued by this record.

## Independent review outcome

All findings from the four review passes are closed. The final implementation preserves
atomic reservation, exactly one completion call, independent work/completion/recovery
exception identities and causes, strict time/enum validation and safe public diagnostics.
The source path remains fixed, and the state lock is not held across Reader or lease I/O.

The last correction makes a transient-failure contention test require one winner among
the actual workers. An independent replay of the submitted test observed one OSError,
one active token and two clean empty results in the normal control, with no main-thread
fallback. A mutation that made the remaining workers skip due work now fails that same
test with AssertionError. The negative witness shares the real outcome oracle.

Forty WP-07 tests cover the SC-01 through SC-16 matrix, including exact debounce boundaries,
coalescing, moves, token ownership, follow-up, retry without a new notice, fault/resume,
exception composition, blocked Reader/cleanup responsiveness and WP-04 logical no-change
composition. The 274 earlier tests and their acceptance limits remain unchanged.

## CI evidence

| Platform | Test result | Relevant skips | Combined acquisition + Reader benchmark |
|---|---|---|---|
| Linux | 312 passed, 2 skipped in 12.34s | Two Windows handle tests | 2.5104s / 59.95 MiB |
| Windows | 313 passed, 1 skipped in 28.33s | POSIX permission test only | 4.7539s / 59.16 MiB |

Both jobs passed lock verification, frozen dependency sync, Ruff format/lint and Mypy.
All four native Windows symlink scenarios executed; no missing-privilege skip occurred.
The existing CI failure rule for unavailable symlink support was retained.
The 15,000-row benchmark ceiling remains 15 seconds and 128 MiB. Reader-only CI metrics
were 2.0632s / 59.85 MiB on Linux and 4.5770s / 60.51 MiB on Windows.

Independent native checks before publication also passed: 40 coordinator tests on each
platform, Linux full suite 312 passed / 2 skipped, and local Windows full suite
309 passed / 5 skipped. Four local Windows skips reflected missing symlink privileges;
the successful CI execution resolves that evidence gap. Independent combined benchmark
results were 11.1000s / 59.43 MiB on Linux and 2.7729s / 58.71 MiB on Windows.

Handoff validation and baseline diff checks passed. The pre-publication tracked-asset
and credential-pattern scan covered 99 tracked files and all 27 new branch blobs with
zero findings. It checked explicit prohibited extensions, binary contents, credential
patterns and public host-address patterns, propagated tool failures and failed on matches.
This is bounded scan evidence, not a guarantee of detection of every possible secret.

## Accepted limits

Successful guard recovery releases only its still-active attempt and preserves pending
intent. If acquiring the guard's own lock fails, the driver preserves all failures
without mutating ownership outside the lock; this does not guarantee recovery from a
failure of the recovery mechanism itself.

This component coordinates volatile source-read attempts. A successful read is not a
committed import, durable baseline, financial revision or ACK. Real observer delivery,
Save/OneDrive timing, COM/UUID writes, real workbook evidence, persistence/Outbox,
restart recovery and production behavior remain outside this package and outside this
acceptance. The implementation PR introduces no schema, migration or deployment change.
