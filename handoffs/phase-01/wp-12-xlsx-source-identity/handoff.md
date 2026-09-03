# Handoff: WP-12 XLSX source identity

## Identity

- Phase 1; package WP-12; component `xlsx-source-identity.v1`.
- Planning baseline: `31c4d48280ea5e1562982443ca69e8ffe69b3612`.
- Planning code: `1fe51c7a1da34fda9de10409a7719ce16d745026`, merged through PR #40.
- Approved execution baseline: `82eb695658262bf092dedbe337fdd0eacebba9ba`.
- Branch: `codex/phase-01-xlsx-source-identity`, as issued by WP-12.
- Initial code commit: `4e4d5e0ec54240d7dc9e1e50abd7887b5f4138e7`.
- Reviewed correction and final tested code: `8cb4fb408eeefe718ae0dcd96d41532021392b18`.
- Implementer: Codex. Non-author reviewer: existing `wp10_independent_review`
  agent, assigned specifically to WP-12. Code review and independent execution
  have closed R1/R2; final documentation review follows this delivery commit.
- Delivery HEAD is a following documentation-only commit, reported after Git
  assigns it. An unknown own SHA is not recursively inserted into these files.
- Roadmap remains version 0.52; G1 remains OPEN / IN PROGRESS.

## Scope

Implement the seven exports and complete read-only adapter specified by
[ADR-0015](../../../docs/adr/ADR-0015-xlsx-source-identity.md) and
[WP-12](../../../docs/work-packages/phase-01/WP-12-xlsx-source-identity.md).
One WP-06 stable lease and one ZIP object supply both the versioned, unlinked
`AccountingBot.SourceIdentity` custom property and the existing WP-05 Raw reader.
The immutable result associates the exact `SourceBindingKey`, Raw result and
acquisition SHA-256/byte count. It is returned only after ZIP close, lease
revalidation and cleanup succeed.

The OPC profile checks candidate cardinality before filtering invalid/external
relations, canonical internal targets, exact content-type association, consistent
Transitional/Strict namespaces, property identity/type/link rules and the literal
version/UUIDv7/year grammar. All three metadata members have a declared and actual
decompressed 1-MiB limit. XML parsing disables external resolution and rejects
DTDs; no workbook extraction, full-workbook buffer or second snapshot is added.

The adapter does not infer a missing identity from filename or dates. A copied
marker remains the same logical key. It supplies neither authentication nor
source enrollment, prior-state registration, permission to Commit or durable
identity/revision/rollover state. No marker writer or operational command is added.

## Roadmap traceability

| Reference | Approved contribution |
|---|---|
| 5.1 / 5.2 / O-71 | Complete, stable, read-only acquisition and cleanup |
| 5.3 / O-07 / O-77 | Annual key from the same acquired bytes as Raw; archive routing stays explicit |
| O-69 / O-74 / O-75 / O-76 | Existing Planner, requiredness, fiscal and binding contracts remain unchanged |
| ADR-0015 / WP-12 | Exact wire/profile/error/lifecycle contracts and XI-01..16 evidence |
| O-46 / O-48 / O-51 | Isolated implementation, non-author review, synthetic evidence and public scans |

## Changed files

| File | Purpose |
|---|---|
| apps/local_agent/src/accounting_local_agent/xlsx_source_identity.py | New adapter and seven-symbol contract |
| apps/local_agent/src/accounting_local_agent/__init__.py | Additive public exports |
| apps/local_agent/README.md | Library usage and trust/error boundaries |
| tests/test_xlsx_source_identity.py | 111 protocol, constructor, property and benchmark cases |
| tests/test_xlsx_source_identity_lifecycle.py | 37 acquisition, lifecycle, failure and composition cases |
| tests/xlsx_source_identity_fixtures.py | Generated synthetic OPC/XLSX fixtures |
| tests/xlsx_source_identity_import_probe.py | Fresh target execution guard and injected-write control |
| tests/xlsx_source_identity_benchmark.py | Isolated full-adapter timing/RSS probe |
| handoffs/phase-01/wp-12-xlsx-source-identity/handoff.md | Delivery scope and boundaries |
| handoffs/phase-01/wp-12-xlsx-source-identity/acceptance-matrix.md | XI-01..16 node mapping and limitations |
| handoffs/phase-01/wp-12-xlsx-source-identity/test-results.txt | Captured command evidence |

## Schema and migrations

None. Existing Reader/acquisition/coordinator/runtime product code, contracts,
all baseline test/helper files, manifests, lockfile, CI, skills, Roadmap, ADRs and
WP documents are unchanged. All 530 baseline test nodes remain collected. New
files and additive exports/docs are confined to the issued allowlist.

## Commands and exit codes

Local native Windows, Python 3.13.15; isolated virtual environment with a writable
workspace test-temporary root. Exact commands, timestamps and captured output
appear in test-results.txt. All final code checks used `8cb4fb4`; the controlled
mutation was recorded on `4e4d5e0` and its parser/oracle remain unchanged by R1/R2.

| Command | Exit | Evidence section |
|---|---:|---|
| `uv sync --frozen --all-packages --all-groups` | 0 | sync |
| `uv lock --check` | 0 | lock |
| `uv run ruff format --check .` | 0 | format |
| `uv run ruff check .` | 0 | lint |
| `uv run mypy .` | 0 | mypy |
| `uv run mypy --platform win32 .` | 0 | mypy-win32 |
| `uv run pytest tests/test_xlsx_source_identity.py tests/test_xlsx_source_identity_lifecycle.py -v` | 0 | dedicated |
| `uv run pytest --collect-only -q` | 0 | collect |
| `uv run pytest --collect-only -q -o addopts=` | 0 | Derived new-node index; full raw index retained locally |
| `uv run pytest -v` | 0 | full |
| `uv run pytest tests/test_xlsx_source_identity.py::test_xi14_combined_15000_row_benchmark -v -s` | 0 | benchmark-xi14 |
| `uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s` | 0 | benchmark-xr12 |
| `uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s` | 0 | benchmark-sa14 |
| `git diff --check 82eb695658262bf092dedbe337fdd0eacebba9ba...HEAD` | 0 | whitespace |
| `python .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-12-xlsx-source-identity` | 0 | handoff |
| Controlled constant-key runtime mutation | 1, expected rejection | mutation |
| Isolated fixed-code rollback and scope/asset inspection | 0 | rollback / scope / code-scan |

## Tests and evidence

- Dedicated: 148 passed in 14.95s. Full repository: 678 collected; 673 passed,
  5 skipped in 72.65s. No unhandled test-thread warning was emitted.
- Four skips require Windows SeCreateSymbolicLinkPrivilege, unavailable locally
  (WinError 1314). The fifth is POSIX-only permissions. The unchanged CI guard
  makes missing Windows symlink capability a failure; these skips are not passes.
- Ruff format: 107 Python files; lint clean. Both full mypy commands: 43 source
  files, no errors. The default command ran on Windows, not on a Linux host.
- XI-14 complete adapter: 15,000 rows, 2.668312s, 82.304688 MiB peak RSS. Fixture
  generation took 2.094276s separately. Key, byte hash/count, every expected row
  identity and Raw outcome, source bytes and lease cleanup are asserted.
- Original WP-05: 2.8061s / 59.36 MiB. Original WP-06: 2.9449s / 59.04 MiB.
  All three retain the existing 15-second / 128-MiB boundaries.
- The non-author independently ran all 148 dedicated cases: 148 passed in 11.87s,
  with no skips/warnings. Its XI-14 measurement was 2.639666s / 83.183594 MiB.
- Review R1 reproduced invalid DEFLATE bytes in each metadata member as raw
  `zlib.error`. The correction preserves it as the cause of the existing typed
  corrupt-ZIP error. Six regression cases also preserve an independent ZIP close
  failure in order. The reviewer's separate three-member reproduction now passes.
- Review R2 added the missing markerless mixed-year fixture; the standalone Raw
  reader and fiscal evaluator independently establish observed years 1403/1404
  before the complete adapter rejects the missing marker.
- The constant-key mutation fails at `assert first.key == key` in XI-13, with
  the expected UUID/year differing from the injected constant. This is runtime
  rebinding of the actual parser function, not a committed product mutation.
- Code scan: 144 tracked files and 11 branch-introduced blobs, zero findings.
  Scope inspection retained all 530 baseline nodes plus 148 new nodes. The
  final delivery scan additionally includes the three packaged documents.
- Full raw logs and verification tools remain outside the published payload at
  `work/reviews/wp12-implementation-20260903/`; portable evidence is in this package.

## Assumptions and open items

- Windows local: PASS with five disclosed skips. Linux local: NOT RUN. Native
  Windows/Linux PR CI: PENDING. Static win32 checking does not replace native CI.
- Non-author code review is complete; final Handoff review and PM acceptance are
  pending. This implementation delivery does not close G1 or start another WP.
- The import guard covers fresh execution of the target module. Dependency
  bootstrap and loading its source/compiled code occur before the guard; import
  identities are checked. The negative control injects an actual denied write.
- Runtime guards allow existing acquisition's private lease UUIDs and temporary
  writes. They forbid new source-identity generation, networking, extraction and
  implicit downstream routing/evaluation/runtime/persistence effects.
- On Windows, the replacement variant of XI-08 closes the ZIP at a controlled
  injection seam before replacing its pathname, because native open-handle
  sharing can otherwise reject replacement. The WP-06 lease stays active and its
  actual exit integrity validation must reject the replacement. In-place tamper
  and delayed successful delivery are separately exercised.
- Missing package Content_Types is rejected earlier by WP-06 as SourceNotReady;
  the new metadata helper's missing-part taxonomy is also checked directly. Raw
  parsing retains the existing Reader's XML/CRC behavior. New DEFLATE failures
  are typed without rewriting the existing Reader.
- The result constructor validates representation and retains nested objects;
  only the complete reader function performs the recorded I/O lifecycle. Public
  new typed messages hide raw values/paths; diagnostic causes are intentionally
  preserved and are not asserted to be fully redacted exception trees.

## Risks

Synthetic OPC fixtures prove the approved reader profile. Real Excel Save,
OneDrive conflict handling and cross-application custom-property retention have
not been tested against a real workbook. Marker provenance/enrollment, durable
global identity and home-sheet membership, prior projection, atomic final import,
shared revisions and rollover remain future work. A copied key or ACTIVE binding
is not authentication or approval to Commit. Native CI remains a required gate.

## Rollback

Rollback was actually executed in the fresh `rollback-final-checkout`, created
from the fixed tested code `8cb4fb408eeefe718ae0dcd96d41532021392b18`, on
`scratch/wp12-final-rollback`. A clean `git status --porcelain` was confirmed first.
Both code commits were inverted newest first:

```text
git revert --no-commit 8cb4fb408eeefe718ae0dcd96d41532021392b18 4e4d5e0ec54240d7dc9e1e50abd7887b5f4138e7
git diff --quiet 82eb695658262bf092dedbe337fdd0eacebba9ba
git diff --cached --quiet 82eb695658262bf092dedbe337fdd0eacebba9ba
```

Every command returned zero: the entire working tree and index equal the fixed
execution baseline. The implementation branch stayed at its code SHA and clean;
root main was not modified. This proof covers the eight code/test/usage files
at that exact starting SHA. It excludes this later three-file Handoff commit and
any future shared-branch merge. Reverting delivery docs or a merged PR requires
an explicitly selected fixed-commit scope; this is not a floating HEAD recipe.
No deletion, reset, force push or real-data operation is required.

## Protected assets

- No real workbook, real-data copy, accounting row, phone/Telegram identity,
  database, PDF, key, credential or production dump was accessed or committed.
- All workbook bytes and probe payloads are generated synthetic temporary data.
- Roadmap, ADR/WP authority, existing tests, dependencies, CI and skills are unchanged.
- No production service, Telegram message, DNS, paid resource or deployment changed.
- No destructive migration, broad reset/checkout or unrelated user edit is included.

## Stop state

Implementation is handed off under accounting-bot-implementer for final non-author
documentation review and PM disposition. This package records no public push, PR,
merge, deployment, next-package start or Gate acceptance. Any later PM action and
native CI evidence must identify the actual delivered commit separately. G1 is
OPEN / IN PROGRESS.
