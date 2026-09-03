# Codex acceptance: WP-12 XLSX source identity

- Decision: accepted and merged; synthetic same-acquisition identity evidence only.
- Decision owner: Codex Project Manager under O-46/O-49.
- Date: 2026-09-03.
- Approved execution baseline: `82eb695658262bf092dedbe337fdd0eacebba9ba`.
- Initial implementation: `4e4d5e0ec54240d7dc9e1e50abd7887b5f4138e7`.
- Corrected tested code: `8cb4fb408eeefe718ae0dcd96d41532021392b18`.
- Independently reviewed delivery: `6592737622fcf0430db355c0bbd8845ba6a7a0ae`.
- Implementation PR: [#41](https://github.com/mohsenbarari/accounting-bot-public/pull/41).
- Merge commit: `0bbc101d3d7691f235425a813505582dc8b452b0`.
- Pre-merge CI: [Run 33746825519](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33746825519), successful on the reviewed head.
- Post-merge CI: [Run 33749831904](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33749831904), successful on the exact merge commit.
- G1 remains **OPEN / IN PROGRESS**. No next work package is issued here.

## Review responsibility and decision

Codex implemented the issued WP-12. A separate existing Codex agent that had not
authored its implementation reviewed O-77, ADR-0015 and XI-01..16 against all eight
product/export/README/test/helper files, the corrective diff and the three Handoff
documents. The final disposition was **no remaining actionable findings** on
the delivery above. The reviewer made no tracked-file or Git-ref changes.

The first code pass found two issues. Invalid DEFLATE payloads in each metadata
member could escape as raw zlib errors; the correction maps them to the existing
typed corrupt-ZIP error with the original cause intact. Six cases cover the three
members with and without an independent close failure. A missing explicit
markerless mixed-year case was also added: the standalone Reader and fiscal
evaluator establish years 1403/1404 before the identified reader rejects the
missing marker. A later one-line documentation correction fixed the case of the
WP-12 link for GitHub/Linux. All three findings were closed.

The reviewer independently executed both dedicated modules on corrected code
using native Windows Python 3.13.15: **148 passed in 11.87s**, exit 0, no skips
or warnings. Its separate real-DEFLATE reproduction confirmed the typed reason,
original cause, unchanged source bytes and completed lease cleanup for all three
metadata members. It inspected the author's full-suite/static/scan/mutation
evidence without claiming to have repeated all those commands. Final Handoff,
ancestry, scope, whitespace and code-preservation checks passed. It corroborated
the isolated rollback tree and index and compared eight original evidence-log
hashes with the Handoff. Reports and raw commands/results remain in the local
WP-12 implementation review artifacts; the original code-review output is also
packaged in [test-results.txt](test-results.txt).

Codex Project Manager accepts the bounded package using that non-author review,
the [acceptance matrix](acceptance-matrix.md), the [handoff](handoff.md) and the
native CI evidence below. The Owner explicitly authorized publication of the
exact eleven-file delivery to the operational public repository, PR creation and
merge after successful native Linux/Windows CI. The exact-head guard was used
when merging after both required jobs passed. No administrator bypass, direct
main commit, force push or branch deletion occurred. The merge tree equals the
reviewed delivery tree; clean local main advanced by fast-forward only.

This record resolves the final review and native CI items pending in the original
Handoff. Those three Handoff files remain unchanged as historical execution
records. Their earlier pending status is not the current PM disposition.

## Accepted evidence

- XI-01/XI-12: exact version/constants and seven-symbol API, signature and frozen,
  slotted result representation. Key/Raw objects retain identity; hash/count and
  exact enum checks reject invalid and injected fields. New typed messages and
  result repr exclude synthetic raw/path markers. Diagnostic causes remain intact;
  this is not a full exception-tree redaction claim. Direct construction validates
  representation, not file provenance or successful acquisition.
- XI-02..XI-05: independently declared marker/key and complete Raw expectations
  cover Transitional/Strict namespaces, UTF-8/UTF-16, prefix/order/target variants,
  duplicate or ambiguous package/property candidates, wrong types/links and wire
  grammar. Missing markers on empty, undated and mixed-year sources are never
  guessed; the standalone Reader remains compatible with valid markerless inputs.
  Declared year boundaries follow the accepted parser without a current-year
  floor. Unrelated custom-property values and links do not become operational data.
- XI-06: canonical internal package discovery, exact content-type association,
  malformed/unsafe XML rejection and bounded metadata reads. All three members
  exercise cap minus one, exact cap and plus one; a compressed real-stream proxy
  proves actual bounded decompression and close. The 1,048,576-byte metadata cap
  is not a Raw row or financial-change limit. No external fetch/extraction occurs.
- XI-07/XI-08: real synthetic acquisition A followed by live-path replacement B
  returns only A's associated key, Raw, hash and byte count, preserving B. Both
  readers use one identical ZIP object; the live source is not parsed after the
  lease yields. Tampering/replacement at two lifecycle points prevents return.
  Event/ACK gates prove successful return waits for ZIP close and lease exit;
  spawned test threads report errors and are joined in finally.
- XI-09: separate and concurrent acquisition, identity, Raw, ZIP close, lease
  integrity and cleanup failures preserve independent error objects, ordering,
  shared causes and cancellation in exception groups. Actual DEFLATE corruption
  no longer escapes untyped. Existing Reader XML/CRC behavior and the earlier
  acquisition missing-content-type boundary remain unchanged. Cleanup failure
  cannot become success or conceal the read failure.
- XI-10/XI-11: physical row reorder, formula/cache-only changes and unrelated
  metadata preserve Raw and independently expected Planner outcomes; a single
  raw edit changes its exact expected identity/action/revision. Explicit synthetic
  composition covers archive A, active B, unknown C, known-ID wrong-year and
  empty B. B's voids never target A's membership; archive/unknown do not call the
  Planner. Mixed/undated dates do not replace the marker's key. Production reading
  performs no implicit binding, evaluation or planning.
- XI-13: at least 30 generated cases vary real prefixes, order, canonical paths
  and key fields against an independent expected-key model while Raw stays
  identical. A controlled constant-key replacement of the actual parser failed
  at `assert first.key == key`. That runtime mutation was recorded on initial
  code; its parser and property oracle were unchanged by the correction. No
  mutation was committed and its failure was not an unrelated setup error.
- XI-14/XI-15: the complete 15,000-row adapter checks every expected row identity
  and Raw outcome, key/hash/count, unchanged source and finished cleanup, with
  fixture construction outside the measured call. Scoped guards forbid new
  source-identity generation, networking, extraction, runtime startup and implicit
  downstream/persistence effects. Existing acquisition's private lease IDs and
  temporary writes remain allowed. Guard-specific negative controls pass.
- XI-16: all 530 baseline cases and their files remain unchanged. The 148 new
  cases produce 678 collected tests. Frozen sync/lock, Ruff, full mypy and win32
  mypy, dedicated/full tests, all three benchmarks, Handoff validation, whitespace
  and public scans passed. Native CI supplies the four symlink cases unavailable
  on the local Windows account.

## Native CI evidence

| Execution | Platform | Full suite | Expected skips |
|---|---|---|---|
| PR #41, Run 33746825519 | Windows | 677 passed in 81.63s | 1 POSIX permission check |
| PR #41, Run 33746825519 | Linux | 676 passed in 47.84s | 2 Windows handle checks |
| main, Run 33749831904 | Windows | 677 passed in 73.16s | 1 POSIX permission check |
| main, Run 33749831904 | Linux | 676 passed in 51.95s | 2 Windows handle checks |

Both runs executed all four mandatory Windows symlink scenarios. The only Windows
skip is the POSIX permission check; missing symlink capability would fail the
unchanged CI precondition. Every required quality/test job and step passed on
the expected head/event. No unhandled test-thread warning was recorded.

The author's local Windows full suite was 673 passed and five skipped in 72.65s;
dedicated tests were 148 passed in 14.95s. Four local skips lacked symlink privilege
and one was POSIX-only. Native CI resolves the four-case gap. No local Linux
execution is claimed. Both full mypy commands ran locally on Windows, not Linux.
Ruff formatted 107 Python files; each full mypy command checked 43 source files.

The complete local XI-14 read took 2.668312s with 82.304688 MiB peak RSS; synthetic
fixture construction took 2.094276s separately. The reviewer independently measured
2.639666s / 83.183594 MiB with 2.082415s fixture time. Original WP-05 and WP-06
benchmarks measured 2.8061s / 59.36 MiB and 2.9449s / 59.04 MiB. All three retain
the existing **15 seconds / 128 MiB** limits and also execute in each native full
suite. These measurements do not change any threshold or imply real-workbook
performance acceptance.

## Scope and rollback

The implementation delivery scan covered 147 tracked files and all 15 introduced
branch blobs, with zero prohibited-asset, credential or host-address findings.
Only the three Handoff files separate the corrected tested code from final
delivery. This acceptance change contains ROADMAP and this review record only;
product/tests/contracts, dependencies, CI, ADR/WP and earlier Handoff files are
unchanged. Its own publication scan is recorded separately from the implementation
scan; the acceptance commit is not part of the historical fifteen-blob count.

The author executed code rollback in a fresh isolated checkout starting at
`8cb4fb408eeefe718ae0dcd96d41532021392b18`, reverting that correction and then
`4e4d5e0ec54240d7dc9e1e50abd7887b5f4138e7` without committing. Both working tree
and index exactly matched baseline `82eb695658262bf092dedbe337fdd0eacebba9ba`.
The independent reviewer corroborated those fixed identities and equality. This
proof covers the two code commits only; it excludes later Handoff documentation,
the shared merge and this acceptance record. No shared or production state was
rolled back.

## Accepted limits

The fresh import probe guards target-module execution after loading source and
dependencies. It does not claim all Python bootstrap is I/O-free. Runtime guards
and mutation are focused regression evidence, not a universal security sandbox.
On Windows, the snapshot replacement injection closes the ZIP first to permit
native pathname replacement while the actual WP-06 lease remains active; lease
exit still must reject it. Separate in-place tamper and delayed-return cases are
preserved. This does not establish real Excel handle-sharing behavior.

`xlsx-source-identity.v1` associates a parsed logical key with Raw from a controlled
lease. An unsigned copied marker does not authenticate a person/workbook, enroll
a new physical source, prove a durable prior registry or permit Commit. Real
Excel Save/SaveAs/OneDrive marker retention, conscious initial enrollment,
global UUID/home-sheet/membership/revision continuity, atomic final import and
rollover with crash recovery remain separate prerequisites.

All workbook evidence is generated synthetic data. No real workbook/copy,
Excel/COM acceptance, marker writer, database/outbox commit, deployment or final
Owner testing is claimed. G1's repeatable end-to-end import criterion remains
open. This acceptance neither issues WP-13 nor changes a financial/RS rule.
