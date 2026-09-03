# Handoff: WP-10 source fiscal evidence

## Identity

- Phase: 1; Work Package: WP-10 / source-fiscal-evidence.v1.
- Approved execution baseline: `d14ef4abbe858ca99501d5eac2c44c7179388410` (merged planning PR #34).
- Branch: `codex/phase-01-source-fiscal-evidence`, as issued by WP-10.
- Tested code commit: `b9230689c3d9273231d017d71a1ce6873bd8c2a7`.
- Implementer: Codex, on the Owner's instruction to continue the issued package.
- Independent reviewer: PENDING, must be a non-author.
- Delivery is a following documentation-only commit. Its actual SHA is reported
  after Git creates it, not recursively embedded as an unknown own SHA here.
- G1: OPEN / IN PROGRESS. Roadmap remains version 0.48.

## Scope

Implement the approved six-symbol library contract of ADR-0013. Observe every raw
transaction date using the accepted Jalali parser; preserve null-date metadata,
count business-party rows separately, and retain the original complete snapshot.
The report constructor computes ordered rows/years/counts and prevents injection
of derived fields. Public metadata is frozen/slotted with fixed typed validation
errors and no raw date/name/contact values in repr.

The library does not select a year, admit/reject a workbook, detect opening
balances, bind a source, change archive state, invoke a watcher/Planner/requiredness
check, write a file/database, or authorize an import/deletion. No dependencies,
accepted Raw/date/hash/snapshot contracts, existing tests, CI or Gate rules changed.

## Roadmap traceability

| Roadmap section / decision | Approved status | Implemented contribution |
|---|---|---|
| O-75 / ADR-0013 / WP-10 | Issued technical contract | Deterministic fiscal observations without operational-year selection |
| 5.3 / O-07 | Confirmed rule; fiscal binding remains future work | Content date evidence; filenames/UUID time cannot select a year |
| O-42 / O-68 | Accepted parser | Reuse canonical Jalali fiscal-year result without alternate calendar logic |
| 4.3 / 5.1 / O-06 / O-69 | Confirmed | Empty and all-null snapshots retained without row minima |
| O-69 / O-70 / O-74 | Accepted contracts | Raw values/identities/hashes retained; WP-09 and WP-04 stay independent |
| O-46 / O-47 / O-48 / G1 | Separate implementation and acceptance | Isolated code, tests, evidence, reversible commit, no self-approval |

## Changed files

| File | Change | Reason |
|---|---|---|
| packages/contracts/src/accounting_contracts/source_fiscal_evidence.py | New pure module and six public symbols | ADR-0013 |
| packages/contracts/src/accounting_contracts/__init__.py | Export six approved names | Public contract |
| packages/contracts/README.md | Usage and explicit observation limits | Library documentation |
| tests/test_source_fiscal_evidence.py | 56 dedicated cases and independent oracles | FE-01..13 |
| tests/source_fiscal_evidence_import_probe.py | Focused target import guard and controlled write injection | FE-01/FE-11 |
| handoffs/phase-01/wp-10-source-fiscal-evidence/handoff.md | This handoff | WP-10 evidence |
| handoffs/phase-01/wp-10-source-fiscal-evidence/acceptance-matrix.md | FE-01..14 node mapping and gaps | WP-10 evidence |
| handoffs/phase-01/wp-10-source-fiscal-evidence/test-results.txt | Captured command output, failed environment probe and local results | WP-10 evidence |

## Schema and migrations

- Schema impact and migrations: none.
- Existing public contracts and all 403 baseline collected test cases are unchanged.
- Data: only synthetic fixtures; no reference workbook or real accounting data.

## Commands and exit codes

Exact commands and outputs, with disclosed local path normalization, appear in
`test-results.txt`. UV uses existing frozen packages offline; Python 3.13.15.

| Command | Exit code | Result |
|---|---:|---|
| uv sync --frozen --all-packages --all-groups --offline | 0 | 85 installed packages verified |
| uv lock --check --offline | 0 | 88 resolved packages |
| uv run --offline ruff format --check . | 0 | 88 files |
| uv run --offline ruff check . | 0 | No lint issues |
| uv run --offline mypy . | 0 | 34 source files |
| uv run --offline mypy --platform win32 . | 0 | 34 source files |
| uv run --offline pytest --collect-only -q --ignore=tests/test_source_fiscal_evidence.py | 0 | 403 retained nodes |
| Dedicated WP-10 pytest with captured temp/cache options | 0 | 56 passed in 2.03s |
| Full pytest with CI=true and mandatory symlink preconditions | 1 | 454 passed; 4 absent-privilege precondition failures; 1 POSIX skip |
| Full local pytest, CI unset, fresh local cache | 0 | 454 passed, 5 skipped in 59.35s; no warnings |
| Original WP-05 15,000-row benchmark node | 0 | 2.8131s; peak 60.09 MiB |
| Original WP-06 15,000-row benchmark node | 0 | 2.8452s; peak 61.66 MiB |
| git diff --check d14ef4abbe858ca99501d5eac2c44c7179388410...b9230689c3d9273231d017d71a1ce6873bd8c2a7 | 0 | No whitespace errors |
| Baseline test/protected-file diff checks | 0 | No upstream changes |
| Local tracked-file and branch-blob scan | 0 | 123 tracked files, 5 new blobs, zero findings at code SHA |
| Isolated fixed-code revert and index/worktree comparison | 0 | Exactly matches baseline |

The handoff validator and final documentation scan are run after this file is
created; final delivery verification is retained with the local delivery record.

## Tests and evidence

`acceptance-matrix.md` maps exact function node IDs, including parameter families,
to FE-01..14. Test-results retains concrete collected case names and raw output.
All 403 baseline nodes remain unchanged; the new 56 cases give 459 collected.

Fresh-import purity covers exact target-module execution after its loader reads
the code; normal dependency/bootstrap reads are outside that claim. There is no
path allowlist during guarded target execution. A real target-scope write injection
raises the specific guard exception and exit 73, while ordinary import succeeds.
After fixture construction, evaluation guards file/network/clock/random/UUID/thread
seams and existing Planner/requiredness/hash calls, with a write negative control.

The independent fixture oracle declares years without calling the production
parser/report. Property cases reverse actual sheet, row and key inputs and assert
changed order plus equal raw values. FE-12 checks every row and count in a
15,000-row fixture and retains all original identities/values/hashes. Measured
fixture construction is 0.830156s and evaluation 0.098809s; no new time cap is claimed.

## Assumptions and open items

- Native Windows local validation is complete with the disclosed five skips.
- The strict CI-style local run cannot meet four symlink preconditions because
  this account lacks SeCreateSymbolicLinkPrivilege (Windows error 1314).
- Linux local NOT RUN; Linux/Windows CI PENDING on this implementation SHA.
- Non-author review and separate Project Manager acceptance remain required.
- Filename conventions, active/archive source binding and year transitions remain
  deferred policy; this package introduces no substitute decision.

## Risks

The report is evidence, not authority to compare a workbook with a registry from
another fiscal context. Native CI and independent review are still required before
acceptance. Local timing results do not replace real Excel/OneDrive acceptance.
Reflection-based mutation is outside ordinary frozen/slot guarantees, per ADR-0013.

## Rollback

The actual tested rollback covers code commit `b9230689c3d9273231d017d71a1ce6873bd8c2a7` only. In an isolated clean
clone created from that commit, `git revert --no-commit b9230689c3d9273231d017d71a1ce6873bd8c2a7` completed without
conflict. Both `git diff --quiet d14ef4abbe858ca99501d5eac2c44c7179388410` and `git diff --cached --quiet d14ef4abbe858ca99501d5eac2c44c7179388410`
returned 0, proving exact baseline worktree/index restoration. The implementation
branch remained untouched. Later handoff documentation is not included in this proof.

For an eventual reviewed rollback, first inspect `git status --short` and preserve
unrelated changes. Reproduce the fixed-code revert in a separate review branch and
inspect its diff before applying it to the shared history. A rollback of the later
documentation commit requires its actual delivered SHA, available in the final
delivery record; do not substitute a floating HEAD or a guessed commit. No blind
file deletion, broad checkout, hard reset, force push or production rollback is needed.

## Protected assets

- [x] ROADMAP, ADRs, WP specification and Gate statuses unchanged.
- [x] No reference workbook or unauthorized real-data copy was opened or modified.
- [x] No real accounting data, phone/Telegram identity, PDF, SQLite database, dump,
  token, credential or private key was added; all workbook fixtures are synthetic.
- [x] No production server, Telegram, database, DNS, certificate or backup changed.
- [x] No upstream tests/product logic, lockfile, manifests, CI or skills changed.
- [x] No destructive migration or unrelated user change included.
- [x] No external repository write, Push, PR, merge, deploy or next WP performed.

## Stop state

Implementation is handed off pending non-author review and native two-platform CI.
FE-14 is partial, so this is not WP-10 acceptance. G1 stays OPEN / IN PROGRESS.
The implementer does not approve its own change or continue into another package.
