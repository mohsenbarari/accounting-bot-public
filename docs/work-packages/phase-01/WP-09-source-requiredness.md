# WP-09: Required-field preflight for raw source snapshots

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 essential-field validation prerequisite; cannot close G1
- Status: Issued — implementation may start after the planning PR is merged
- Issued by: Codex Project Manager
- Issued on: 2026-09-02
- Required skill: `accounting-bot-implementer`
- Branch: `antigravity/phase-01-source-requiredness`
- Planning baseline: `82179589b002082c7761be7f21ba99d5931bc034`
- Execution baseline: latest clean `origin/main` containing this WP and ADR-0012;
  record the actual SHA before coding, not the earlier planning baseline
- Handoff path: `handoffs/phase-01/wp-09-source-requiredness/`

## Objective

Implement the pure `source-requiredness.v1` preflight defined by
[ADR-0012](../../adr/ADR-0012-source-requiredness.md). Given a complete immutable
WP-04 snapshot, report all missing required fields deterministically while retaining
every original row/value/hash. Distinguish missing/blank from real zero and keep
optional notes, contact data and item-dependent purity optional at this boundary.

The output is not permission to commit an import. This package does not implement
fiscal/archive policy, financial code/direction rules, party/item resolution, RS
pairing, conditional purity, SQLite, Outbox, a watcher consumer or financial reports.
Only generated synthetic values/files are authorized; no real workbook or data copy.

## Traceability and normative contract

- Roadmap 5.2 step 3 and 5.4: essential raw-data validation before commit.
- Sections 6/7, O-18/O-33/O-60: required amount/price/item/quantity, zero versus null,
  integer Toman preservation and optional notes.
- O-16/O-17/O-32: retain unknown/unresolved data; do not turn presence checking into
  resolution or a new reason to stop unrelated data.
- O-68/O-69/O-70: preserve canonical typing, hashes, complete snapshots and Reader
  activity/exclusion rules. O-74 / ADR-0012 defines only this added preflight.
- O-46 through O-51: independent acceptance, bounded changes and public-artifact safety.

Read ADR-0012 in full. Its required-field table, presence predicate, public API,
constructor invariants, issue order and deferred prerequisites are normative.
There is no caller-configurable policy or override that makes required fields optional.

## Implementation boundary

Add `packages/contracts/src/accounting_contracts/source_requiredness.py`, using only
the standard library and existing `accounting-contracts` modules. Export the six
public symbols listed by ADR-0012. The report constructor receives only the existing
complete snapshot, computes issues/counts, retains its identity and excludes raw
snapshot values from repr. Supported direct construction cannot accept fabricated
issues/counts/status. Use immutable typed metadata, not dicts exposed for mutation.

The evaluator only checks requiredness. `None` means `MISSING_VALUE`; required text
whose `str.strip()` is empty means `BLANK_TEXT`. Do not infer positivity, valid
transaction codes, a current year, a party identity, an item unit, valid RS pairs,
or financial eligibility from a passing report. Do not change any source values.

Use the existing registry for sheet/field ordering and field kinds; store only the
required field-name selections from the ADR rather than another column/header map.
Issue aggregation must avoid nested full-snapshot scans. A direct scan plus bounded
issue sorting is acceptable: O(n + k log k) time and O(k) additional report storage,
where n is checked rows and k is emitted issues. Do not copy all raw row mappings or
recompute hashes already verified by the existing snapshot type.

No threads, time/random sources, reads/writes, network calls or caller mutation.
Unknown nonblank names/items/codes produce no requiredness issue. Upstream snapshot
construction errors remain errors; they cannot be converted to a passing empty report.

## Acceptance matrix

Every row below needs explicit test node IDs and recorded evidence. Use expected
results specified independently from the production requiredness table/evaluator.

| ID | Required evidence |
|---|---|
| SR-01 | Exact version/exports/signatures and inert pure library behavior. Existing package/dependency boundaries and all accepted source contract versions remain unchanged. |
| SR-02 | For every required field of each of the four sheets, a valid WP-04 input with that field `None` emits exactly the independently expected `MISSING_VALUE` issue. Multiple rows/fields aggregate all issues. |
| SR-03 | Required text: `None`, empty string, ASCII whitespace and Unicode whitespace have the specified distinct reasons; nonblank text and preserved surrounding whitespace are present. No letter normalization, case folding or fuzzy matching. Null date is missing; a non-null invalid date is rejected upstream. |
| SR-04 | Required numeric zero, accepted signed values and WP-03-supported Integer/Decimal/text forms count as present and retain exact raw representation/hash. `None` is missing. Float, Boolean and nonfinite/invalid inputs cannot be admitted through a bypass or fallback. |
| SR-05 | Optional fields individually and together may be null without an issue: discount/notes, money auxiliary fields, contact data and purity. C/D/H/HA/HS with blank notes retain their rows. Nonblank unknown names/items/codes and RS rows are not rejected merely for being unresolved. No financial-validity claim. |
| SR-06 | A single mixed four-sheet snapshot produces the full exact ordered issue tuple and correct checked/failed-row/issue counts. A row missing several fields counts once as failed. All good rows and bad rows remain in the same snapshot object. |
| SR-07 | Four present empty sheets produce zero rows/issues and `passes_requiredness=True`; a missing/duplicate/unknown sheet or invalid UUID is rejected by the existing constructor. No row minimum, change-volume threshold or newly invented activity rule. |
| SR-08 | Constructors enforce valid issue metadata, sheet/required-field association and reason/type compatibility; report direct construction recomputes results. Attempts to inject passing flags, omit actual issues, fabricate counts or mutate fields/tuples fail. |
| SR-09 | Invalid API argument types produce the fixed typed error; ordinary signature errors may remain TypeError. Markers placed in invalid arguments and synthetic raw notes/names/contact fields do not appear in error messages or report repr. No raw values are stored in issues. |
| SR-10 | Repeated evaluation gives identical issues/counts and never mutates/replaces raw mappings, values, IDs or hashes. Relevant file/network/clock/UUID-generation seams are forbidden in a purity test. Avoid arbitrary sleep or background threads. |
| SR-11 | Generated four-sheet XLSX → existing WP-05 Reader → preflight: active rows with missing essential input survive extraction and are reported; optional blanks pass. A formula-excluded required cell yields missing raw input; unrelated derived/formula/cache-only changes with unchanged valid Raw do not change issues/hashes. Retain read-only file bytes and existing Reader failure behavior. |
| SR-12 | Independent property oracle from generated row specifications matches all issues/counts under sheet, row and input-mapping permutations. Mutating one required value across present/null/blank changes only the expected issue; do not call production requiredness helpers to calculate expectations. |
| SR-13 | A generated 15,000-row snapshot with a known mix of valid and incomplete rows yields exact counts/order and preserves identities/hashes. Record evaluator duration and memory/algorithm observations separately from fixture creation; demonstrate no quadratic row scans. Keep the original Reader/acquisition benchmark scopes and limits unchanged. |
| SR-14 | Preserve all 350 existing collected tests; Ruff format/lint, full mypy and win32 mypy, full suite, handoff validator, baseline whitespace and publication scans pass. Required native Windows/Linux CI, including all four Windows symlink scenarios, remains mandatory for Codex acceptance. |

Use only values constructible through the existing public WP-04 builder for normal
cases. Do not mutate frozen snapshots or bypass accepted constructors just to force
unreachable invalid-value cases. Negative upstream construction is separate evidence
and must not be reported as a successful preflight on invalid data.

## Allowed changes

- New `packages/contracts/src/accounting_contracts/source_requiredness.py`.
- Public exports in `packages/contracts/src/accounting_contracts/__init__.py`.
- Requiredness usage and limitations in `packages/contracts/README.md`.
- New `tests/test_source_requiredness.py` and, if useful, a separate new
  `tests/test_source_requiredness_reader_integration.py`; new focused synthetic
  helpers under `tests/` only when necessary, preserving existing fixture behavior.
- Completed handoff files under the named WP-09 directory.

No changes to ROADMAP, ADRs/WPs, current Raw/canonical/Planner/Reader/acquisition/
coordinator/runtime code or tests, Domain, persistence, manifests/lockfile, CI policy
or benchmark limits. If an existing API makes the normative behavior impossible,
record the smallest reproduction and stop for Codex's bounded decision. Do not
silently widen scope, add dependencies or connect a passing report to persistence.

## Validation and handoff

Start from a clean approved main containing the issued planning documents. Record
the actual baseline, branch and linear commits. From repository root, capture exact
commands, output and exit status:

```text
uv sync --frozen --all-packages --all-groups
uv lock --check
uv run pytest tests/test_source_requiredness.py -v
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run mypy --platform win32 .
uv run pytest -v
uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s
uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s
git diff --check origin/main...HEAD
python .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-09-source-requiredness
```

Run any added integration test file explicitly as well. The original 15,000-row
Reader/acquisition limits remain 15 seconds / 128 MiB. Record Linux local, Windows
local and Windows CI evidence separately; static win32 typing is not a runtime test.
Report unavailable platform evidence honestly for independent Codex completion.

Provide `handoff.md`, `acceptance-matrix.md`, `test-results.txt` with SR-01..SR-14
node IDs, output/counter examples, raw preservation, exact tested code SHA and
code-to-delivery differences, scan results, limits and scoped rollback guidance.
Use fixed existing SHAs for rollback. A code-only rollback is not proof of reverting
later documentation; identify the actual checked scope. No blind removal or broad
checkout. Do not add commits solely to record their own as-yet-unknown hashes: record
the tested code SHA in the handoff and obtain the final delivery HEAD in the report.

After validation, report the exact final HEAD and stop for independent Codex review.
Do not push, create/merge PRs, approve acceptance, change Roadmap/G1, start another WP,
run a database or use any real-data/protected asset. Implementation completion is
not acceptance, and `passes_requiredness` is not permission to commit an import.
