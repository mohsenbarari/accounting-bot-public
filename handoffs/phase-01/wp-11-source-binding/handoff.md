# Handoff: WP-11 source binding

## Identity

- Phase: 1; Work Package: WP-11 / source-binding.v1.
- Approved execution baseline: `b9502a8f9050257c3dd4c19f9a99d19e0e608a62` (merged planning PR #37).
- Branch: `codex/phase-01-source-binding`, as issued by WP-11.
- Tested code commit: `e0f4ac3abe27085f726c83d0f6161e01aba692e4`.
- Implementer: Codex, on the Owner's instruction to continue the issued package.
- Independent reviewer: PENDING; must be a non-author.
- Delivery is a following documentation-only commit. Its actual SHA is reported
  after Git creates it, not recursively embedded as an unknown own SHA here.
- G1: OPEN / IN PROGRESS. Roadmap remains version 0.50.

## Scope

Implement the nine-symbol logical binding contract in
[ADR-0014](../../../docs/adr/ADR-0014-source-binding.md) and
[WP-11](../../../docs/work-packages/phase-01/WP-11-source-binding.md).
Keys validate UUIDv7/year; records validate exact lifecycle and final byte-hash
metadata. Registries defensively consume records once, enforce unique source IDs
and years plus at most one ACTIVE record, and retain nested prior objects intact.
Resolution uses an immutable source-ID index and selects only the exact active
source's prior view. Archives/unknown IDs select None; a known ID with a different
declared year raises a fixed typed error. Derived result fields cannot be injected.

Records are trusted supplied metadata. No physical workbook attestation, marker
format/read/write, enrollment, final-import receipt, archive transition, SQLite,
Outbox, source-membership projection or operational Planner integration is added.
Dates and deletion volume do not choose a source. Shared party identities and
supplied historical revisions stay intact; global revision persistence is deferred.

## Roadmap traceability

| Reference | Approved status | Implemented contribution |
|---|---|---|
| O-76 / ADR-0014 / WP-11 | Issued technical contract | Explicit key, immutable registry and exact prior routing |
| 5.3 / O-07 | Confirmed | Archive disposition selects no operational prior |
| 4.3 / 5.1 / O-06 | Confirmed | Empty snapshots and deletion volume do not change routing |
| O-69 | Accepted Planner/prior model | Existing per-source views retained; explicit synthetic composition |
| O-75 | Accepted observation boundary | No year inference from dates, filename or UUID creation time |
| O-46 / O-47 / O-48 | Separate implementation and acceptance | Isolated branch, evidence and non-author review requirement |

## Changed files

| File | Change | Reason |
|---|---|---|
| packages/contracts/src/accounting_contracts/source_binding.py | Pure nine-symbol library | ADR-0014 |
| packages/contracts/src/accounting_contracts/__init__.py | Add nine public exports | Issued API |
| packages/contracts/README.md | Usage and trust limits | Accurate library integration boundary |
| tests/test_source_binding.py | 71 cases in 21 test functions; independent routing/Planner/property evidence | SB-01..13 |
| tests/source_binding_import_probe.py | Fresh target execution guard and write-injection control | SB-01/SB-11 |
| handoffs/phase-01/wp-11-source-binding/handoff.md | Scope and delivery record | O-48 |
| handoffs/phase-01/wp-11-source-binding/acceptance-matrix.md | Node IDs and coverage limits | O-48 |
| handoffs/phase-01/wp-11-source-binding/test-results.txt | Captured commands and raw output | O-48 |

## Schema and migrations

None. No existing product behavior or stored schema changes. Existing modules
other than additive exports, all 459 baseline test cases, dependencies, CI and
skills are unchanged. No real source or real-data copy was accessed.

## Commands and exit codes

Windows, Python 3.13.15, uv 0.12.7. An isolated virtual environment and a workspace
temporary directory were used. See test-results.txt for exact output per command.

| Command | Exit code | Evidence |
|---|---:|---|
| `uv sync --frozen --all-packages --all-groups` | 0 | sync |
| `uv lock --check` | 0 | lock |
| `uv run ruff format --check .` | 0 | format |
| `uv run ruff check .` | 0 | lint |
| `uv run mypy .` | 0 | mypy |
| `uv run mypy --platform win32 .` | 0 | mypy-win32 |
| `uv run pytest tests/test_source_binding.py -v` | 0 | dedicated |
| `uv run pytest --collect-only -q` | 0 | collect |
| `uv run pytest -v` | 0 | full |
| `uv run pytest tests/test_source_binding.py::test_sb13_large_prior_is_never_traversed_or_copied -v -s` | 0 | scale |
| `uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s` | 0 | reader |
| `uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s` | 0 | acquisition |
| `git diff --check b9502a8f9050257c3dd4c19f9a99d19e0e608a62...HEAD` | 0 | whitespace |
| `python .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-11-source-binding` | Recorded in final validation section of test-results.txt | Delivery validation |

## Tests and evidence

- Dedicated: 71 passed in 1.01s. Total: 530 collected, 525 passed, 5 skipped in 58.54s.
- Four local skips lack Windows SeCreateSymbolicLinkPrivilege (1314); one is the
  POSIX-only permission test. The four symlink cases remain mandatory in CI;
  their unchanged CI precondition fails instead of skipping without capability.
- Ruff format/lint passed (96 Python files); both full mypy commands passed
  (37 source files). Static win32 checking is not a substitute for native CI.
- SB-13: 15,000 prior identities, 100 annual records, 1,000 resolutions. Fixture
  0.136936s; registry construction 0.000113s; resolution batch 0.000771s. Guards
  intercept nested-row access and per-resolution record scans. No new timing cap.
- WP-05: 2.7602s / 61.19 MiB peak RSS. WP-06: 2.8524s / 61.45 MiB peak RSS.
  Both retain the original 15-second / 128-MiB gates.
- Scope verification compared every existing test blob with the baseline: all
  unchanged; 459 baseline cases plus 71 new cases. New module/source scope matches
  the issued package. Five code blobs and 132 tracked files passed the code scan.
- Raw logs and command metadata are retained locally under
  `work/reviews/wp11-implementation-20260903/windows-evidence/`; portable results
  are included in this delivery. Public-delivery scan is rerun after packaging.

## Assumptions and open items

- Windows local: PASS with the five disclosed skips. Linux local: NOT RUN; this
  execution has no configured Linux test host. Windows/Linux CI: PENDING on PR.
- No independent review, public push, PR or implementation acceptance has occurred.
- The resolver cannot verify a caller's claimed source-to-prior association or
  final hash. Physical marker provenance and durable identity/membership/revision
  projection remain operational prerequisites in ADR-0014.
- The fresh-import guard covers target-module execution. Its source-code loading
  and dependency bootstrap occur before the guard; no path is allowlisted inside
  target execution. A controlled write fails specifically at the guard. This
  evidence does not claim that all Python import/bootstrap activity is I/O-free.
- Registry inputs follow the declared Iterable protocol; strings, bytes and
  mappings are invalid containers. Iterable failures propagate without fallback.

## Risks

ACTIVE is a routing classification, not permission to commit. Choosing the correct
annual prior cannot establish global identity projection or durable final import.
Shared-party fixtures prove preservation of supplied views only. Local success
does not replace non-author review, native two-platform CI, or real Excel/OneDrive
acceptance. Existing local symlink privilege limits are not waived.

## Rollback

The code-only rollback was executed in a separate new checkout created from the
tested code SHA, on `codex/wp11-rollback-check`. The original implementation branch
and root main remained unchanged. The checkout was clean before the operation.

```text
git revert --no-commit e0f4ac3abe27085f726c83d0f6161e01aba692e4
git diff --quiet b9502a8f9050257c3dd4c19f9a99d19e0e608a62
git diff --cached --quiet b9502a8f9050257c3dd4c19f9a99d19e0e608a62
```

All returned zero: both working tree and index match the baseline exactly. This
proof begins at the fixed code commit and covers its five files; it excludes the
later three handoff files and any future shared-branch merge. Do not reuse the
baseline equality claim for a different starting commit. Reverting delivery docs
or a merged PR requires a separately reviewed fixed-commit scope. No recursive
delete, reset, force push or real-data operation is part of the rollback procedure.

## Protected assets

- [x] ROADMAP.md, ADRs and the issued WP were not modified by the implementer.
- [x] No reference Excel, real-data copy or real accounting data was accessed.
- [x] No real phone/Telegram identity, database, PDF, dump, key or credential added.
- [x] No production server/Telegram/DNS/database/backup or paid service changed.
- [x] No destructive migration or unrelated user modification included.

## Stop state

Implementation is stopped pending non-author review under accounting-bot-implementer
and WP-11. No acceptance, Gate approval, push, PR, merge, deployment or next package
has been performed. Native CI and G1 remain pending; this is an evidence handoff.
