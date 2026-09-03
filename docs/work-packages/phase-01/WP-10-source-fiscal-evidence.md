# WP-10: Fiscal-year evidence from raw source dates

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 fiscal-context evidence prerequisite; cannot close G1
- Status: Issued — implementation may start after the planning PR is merged
- Issued by: Codex Project Manager
- Issued on: 2026-09-03
- Implementation workflow: `accounting-bot-implementer`; non-author review before acceptance
- Implementation branch: `codex/phase-01-source-fiscal-evidence`
- Planning baseline: `16cb3be7cf255ec46e626d7f86eb0d668a297288`
- Execution baseline: latest clean `origin/main` containing this WP and ADR-0013;
  record the actual SHA before coding, not the planning baseline
- Handoff path: `handoffs/phase-01/wp-10-source-fiscal-evidence/`

## Objective and traceability

Implement `source-fiscal-evidence.v1` as specified in
[ADR-0013](../../adr/ADR-0013-source-fiscal-evidence.md). Produce deterministic row
and year-count evidence from the existing raw dates of a complete four-sheet
snapshot, retaining the original snapshot and all raw values/identities/hashes.

Roadmap 5.3/O-07 requires content-based fiscal context and archive protection;
O-42/O-68 supplies the accepted Jalali parser. O-06/O-69 preserves valid empty
snapshots and forbids invented deletion thresholds. O-74/WP-09 remains the
independent requiredness check. O-75 records this bounded observation contract.

The report does not select an operational year, grant import permission, classify
archives or opening balances, or reject mixed/no-date evidence. No filename,
machine date, UUID timestamp or caller-selected year is an input. All fixtures
are synthetic; no real workbook, copy, watched directory or data is authorized.

## Implementation boundary

Read ADR-0013 in full. Its six public symbols, input/output fields, constructor
invariants, ordering, source selection and limitations are normative. Add the
pure module and exports without changing the accepted Raw/date/hash/snapshot/
requiredness/Reader/Planner APIs or behavior. Use existing registry/constants and
the accepted date parser; add no dependencies or alternate calendar logic.

The report constructor computes all evidence from its sole snapshot argument.
No filtering, rewriting, caller-provided counters/year sets or eligibility flags.
The three transaction sheets contribute `date_raw`; business-party rows contribute
only their separate count. Metadata must expose no raw date/name/contact text.

This package's expected report cost is O(n log n) time at most and O(n) metadata.
Do not recompute accepted hashes or copy all raw row mappings. No new timing cap
is imposed on the evaluator; record measured duration separately from fixture
construction and retain the existing Reader/acquisition benchmark gates.

## Acceptance matrix

Every row requires explicit test node IDs and captured evidence. Expected years,
row tuples and counts must come from independently declared/generated inputs, not
the production report or its parser/helper as an oracle.

| ID | Required evidence |
|---|---|
| FE-01 | Exact version, six exports, signatures and inert fresh target-module execution. State the import guard's scope; a controlled file-write injection during guarded target execution must fail for the guard-specific reason, while the ordinary import succeeds. Keep package/submodule identities intact. |
| FE-02 | One-year inputs independently cover all three transaction sheets and both public report entry points; exact row metadata and per-year counts, with optional/unknown rows retained. |
| FE-03 | A mixed-year four-sheet fixture yields the full independently expected ordered row tuple and sorted year counts. Mixed years remain evidence, with no selected year, rejection or dropped row. |
| FE-04 | None dates are represented in row metadata and undated counts; test all-null and mixed null/dated cases. No default to the machine, active, minimum or maximum year. WP-09 still reports missing required dates independently. |
| FE-05 | Four empty sheets, party-only input and deletion of every transaction row give exact empty/zero or party-only results without an exception. No row minimum, implicit Planner call or loss of snapshot identity. |
| FE-06 | Accepted WP-03 Persian/Arabic/ASCII digits, separator/whitespace variants and end-of-year/Nowruz/leap vectors produce their independently expected fiscal years. Invalid non-null dates are rejected upstream; do not bypass constructors to manufacture unreachable successful inputs. |
| FE-07 | Year-like names, notes, contacts, amounts and UUID timestamps cannot influence the result. Gregorian year/current time and filename are not substitutes for raw Jalali dates. An opening-transfer-looking row is retained as ordinary date evidence. |
| FE-08 | Metadata rejects unknown/nontransaction sheet names, invalid/non-v7 UUIDs, bool/text/invalid years and bool/nonpositive counts with fixed typed errors. None is allowed only for row evidence. Valid metadata retains its declared types. |
| FE-09 | Direct report construction recomputes every field; attempts to inject years/rows/counts/approval flags or mutate report/metadata/tuples fail. Invalid API arguments raise the defined typed error. Synthetic raw markers are absent from report/metadata repr and public input-error messages. |
| FE-10 | Independent property oracle matches all rows/counts under actual sheet, row and mapping-key permutations. Assert input-order changes and raw-value equality. Moving one raw date between declared years or None changes only the corresponding row evidence and expected counts; all snapshot rows remain. |
| FE-11 | Repeated evaluation is identical and retains snapshot, row, raw-mapping, value, UUID and hash identity/value as appropriate. Guard relevant I/O/network/clock/UUID/thread seams after fixtures are built; include a guard-specific negative control so unrelated failure cannot pass the test. |
| FE-12 | A 15,000-row synthetic fixture includes all three transaction sheets, business parties, null dates and at least three declared years. Compare every row-evidence entry and all counts to an independent oracle; preserve all identities/hashes. Record evaluator duration separately from construction and inspect absence of nested full scans. |
| FE-13 | Synthetic four-sheet XLSX → existing Reader → evidence, alongside explicit WP-09 and WP-04 calls: required/formula-excluded null dates remain observable, unrelated formula/cache changes and physical row reorder preserve fiscal evidence, and raw date edits change only expected evidence. Source bytes remain unchanged. Evidence generation never invokes, authorizes or changes the independent Planner's result, including the empty-snapshot case. |
| FE-14 | Preserve all 403 existing collected tests unchanged. Frozen sync/lock check, Ruff format/lint, full mypy and win32 mypy, dedicated/full tests, original benchmarks, handoff validation, whitespace and publication scans pass. Native Windows/Linux CI includes all four mandatory Windows symlink cases. |

Use snapshots built through the existing public WP-04 API. Date evidence may be
collected from a snapshot that fails WP-09; do not require a passing requiredness
flag. Keep upstream negative construction separate from successful evaluation.

## Allowed changes

- New `packages/contracts/src/accounting_contracts/source_fiscal_evidence.py`.
- Public exports in `packages/contracts/src/accounting_contracts/__init__.py`.
- Usage/limitations in `packages/contracts/README.md`.
- New `tests/test_source_fiscal_evidence.py` and, if needed, focused new synthetic
  helpers/integration tests under `tests/`; preserve existing tests and helpers.
- Completed files in the named WP-10 handoff directory.

No Roadmap/ADR/WP changes by the implementer, no changes to existing product modules
other than exports, and no changes to manifests, lockfile, CI policy or benchmark
limits. No fiscal binding/activation/archive state, marker convention, opening
classification, financial/RS validation, watcher callback, database, Outbox or
deployment. Report a concrete API conflict for a bounded PM decision; do not widen
the package or alter an accepted v1 contract silently.

## Validation, handoff and review

Capture exact commands, raw output and exit status from the execution baseline:

```text
uv sync --frozen --all-packages --all-groups
uv lock --check
uv run pytest tests/test_source_fiscal_evidence.py -v
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run mypy --platform win32 .
uv run pytest -v
uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s
uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s
git diff --check origin/main...HEAD
python .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-10-source-fiscal-evidence
```

Run every added integration file explicitly as well. Keep the original 15,000-row
Reader/acquisition limits at 15 seconds and 128 MiB. Record Linux local, Windows
local and CI separately; static win32 typing does not replace native execution.

Provide `handoff.md`, `acceptance-matrix.md` and `test-results.txt`, with FE-01..14
node IDs, exact tested-code and delivery SHAs, retained baseline tests, full scope
diff, scans and rollback guidance using fixed existing commits. Verify rollback
only in an isolated clean checkout and state whether later documentation is
included. Do not create self-referential commits to record an unknown own SHA.

The implementer hands off and stops for non-author review. If Codex implements,
the implementation cannot be its own independent review. Acceptance and merge
remain a separate Project Manager decision with native CI evidence. G1 stays
OPEN / IN PROGRESS; implementation completion is not operational import approval.
