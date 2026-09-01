# WP-05: Read-only streaming XLSX source reader

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 physical XLSX-to-validated-source boundary only; this package cannot close G1
- Status: Accepted and merged
- Issued by: Codex Project Manager
- Issued on: 2026-08-31
- Accepted by: Codex Project Manager
- Accepted on: 2026-09-01
- Review evidence: PR #19 and final CI Run `33505486610`
- Merge commit: `638053a7968110b5d3631b74d1b155da5304cd0a`
- Required skill: `accounting-bot-implementer`
- Branch: `antigravity/phase-01-streaming-xlsx-source-reader`
- Baseline: latest clean `origin/main` containing this Work Package and ADR-0008
- Handoff path: `handoffs/phase-01/wp-05-streaming-xlsx-source-reader/`

## Objective

Build the first read-only XLSX adapter that reads an already stable snapshot, extracts literal input from exactly the four approved sheets and returns a complete WP-04 source snapshot with separate source locations. Prove the behavior with generated ZIP/XML fixtures, not only mocked row dictionaries.

This is not the production importer. It does not acquire a stable copy, watch Save, write UUIDs, select a fiscal year, apply financial requiredness, read prior database state, commit SQLite or send anything to Telegram. No real workbook or real-data copy is required or authorized.

## Authority and traceability

- Roadmap sections 4.2/4.3, 5.1/5.2/5.4 and O-03/O-06/O-26: stable identity, literal-only source, valid deletion and full-snapshot boundary.
- Sections 15.6/19.1 and O-53 / [ADR-0002](../../adr/ADR-0002-windows-excel-agent.md): standard `zipfile`, streaming `lxml`, exact numbers and cross-platform reader tests.
- [WP-02](WP-02-raw-input-contracts.md): sole registry of sheets, columns, activity fields, headers and ID locations.
- [WP-03](WP-03-canonical-source-hashing.md) / [ADR-0006](../../adr/ADR-0006-canonical-source-hashing.md): unchanged Raw/canonical distinction, dates, exact numeric values and hashes.
- [WP-04](WP-04-deterministic-source-change-plan.md) / [ADR-0007](../../adr/ADR-0007-full-snapshot-change-plan.md): complete snapshot builder, global identities and deterministic change planning.
- O-70 / [ADR-0008](../../adr/ADR-0008-streaming-xlsx-source-reader.md): accepted physical-cell decoding, formula coverage, activity, completion and diagnostic contract.
- O-46 through O-51: bounded implementation, synthetic-only evidence, protected assets and independent review.

Read ADR-0008 completely before implementation. Its source-boundary decisions are normative; the requirements below define the executable package and evidence.

## Public API and placement

1. Expose `XLSX_SOURCE_READER_VERSION = "xlsx-source-reader.v1"` and a typed `read_xlsx_source_snapshot(path: Path) -> XlsxSourceReadResult` API in `accounting_local_agent`. Internal focused modules/a subpackage are allowed; do not create a service, CLI or import-time side effect.
2. `XlsxSourceReadResult` contains the existing `ValidatedSourceWorkbookSnapshot`, reader version and a UUID-to-`SourceRowLocation` mapping. A location contains the approved sheet name and physical row number in the range 2 through 1,048,576.
3. Freeze/copy all result collections defensively. Validate direct construction as well as the factory: exact identity set, matching home sheet, no duplicate physical location within a sheet, no Boolean/noninteger row number and the correct version. Location-map iteration follows UUID bytes; do not duplicate Raw rows or hashes in it.
4. Locations never enter `source_hash`, `sheet_snapshot_hash` or the WP-04 plan. Reuse `SourceRowInput`, `SourceSheetInput` and `build_source_workbook_snapshot`; do not implement a second registry, canonicalizer, UUID validator or hash algorithm.
5. Provide a typed `XlsxSourceReadError` family with stable machine-readable reasons and optional sheet/cell coordinates. Default error text must not echo raw values, formula/cache text or absolute file paths. Preserve useful internal exception context without using it as the default user/log message.
6. No public streaming of partly validated rows, `allow_partial`, `is_complete`, ID repair or cached-value fallback. Any failure aborts the call without returning a snapshot/result.

## Required extraction behavior

### A. Package, sheets and structure

- Open the supplied path read-only and read required ZIP members through file-like streams; never use `extractall`, rewrite the archive, open a live Excel session or scan directories for a workbook.
- Support Transitional and Strict `.xlsx` SpreadsheetML/relationship namespaces explicitly. Reject unsupported, encrypted, non-ZIP and macro-enabled workbook formats with a typed error, not a fallback to another reader.
- Resolve the workbook from package relationships and the selected sheets by their exact registry names and relationship IDs. Resolve worksheet/shared-string targets relative to their owning part, including valid package-absolute targets. Normalize internal paths without permitting root escape, external targets, URI queries/fragments or ambiguous ZIP names. A required workbook/worksheet/string-table target must be internal; unrelated external-link relationships are ignored, not followed or used to reject otherwise valid Raw.
- Require exactly one declaration of each approved sheet, correct relationship/part kinds, distinct targets and complete readable consumed parts. Additional helper/report sheets are allowed and their XML is not opened. A missing source sheet is not an empty source sheet.
- Validate literal row-1 headers from each contract's `required_headers_by_column` and separately `stable_id_column.required_header`. No positional fallback, fuzzy match, trim or Persian/Arabic letter normalization. A header whose registry requirement is `None` imposes no header-text constraint.
- Address cells by their explicit A1 reference and row container. Check full reference syntax and Excel bounds (`A:XFD`, rows 1 through 1,048,576); reject duplicates, missing/mismatched coordinates and unsupported structure in source `sheetData`. Do not rely on XML cell order.
- Consume selected worksheet XML to EOF. Include hidden/filtered data rows; ignore dimension/filter ranges, formatting-only tails and the display row-number column for data selection. Do not expand merged cells or fill down missing dates/names.
- Use namespace-aware direct structural paths. Unknown extensions outside source data are not input; an unsupported alternate encoding of source rows/cells must fail rather than silently disappearing.

### B. Formula and cached-result exclusion

- Call the WP-02 classification boundary before interpreting candidate values. Any `f` element excludes its cell, including shared followers with empty formula text and caches that are invalid/error/nonfinite or malformed shared-string references.
- Exclude output cells covered by array/data-table `ref` ranges even when only the anchor carries `f`. Discover coverage independently of cell/row order and raw-column membership. Index ranges compactly; do not expand a large rectangle into millions of coordinates.
- Array/data-table coverage uses a valid single A1 cell or ordered inclusive A1 rectangle within Excel bounds, containing its anchor. An indeterminate/malformed coverage range cannot be treated as an ordinary literal area. Shared `ref` alone does not authorize blanket range exclusion.
- A shared-formula range must not erase a literal override without its own formula. A literal string starting with `=` is still text, not a formula inferred from its spelling.
- Normal/shared formula expression/cache errors are irrelevant to extraction. Invalid or unsupported range metadata that makes raw/cache provenance indeterminate is a structural failure. Do not implement a formula evaluator or calculate missing values.
- Derived/unlisted cells never supply input, even when overwritten with literals. An excluded cell in a raw field supplies `None`; an excluded required header or active-row ID fails its separate requirement.

### C. Literal decoding

Apply this table only after the exclusion decision and row-activity rules. The WP-03 validator remains authoritative for accepted canonical values.

| Physical cell content | Reader value/rule |
|---|---|
| Missing cell or style-only blank with no value | `None`, not zero or empty text |
| Explicit empty shared/inline/direct text | `""`; preserve the distinction from `None` |
| Shared string (`t=s`) | Resolve its zero-based index through the related string table; a needed invalid/missing/out-of-range index is an error, never the index itself as text |
| Inline string (`t=inlineStr`) or direct string (`t=str`) | Decode literal text without trimming, normalization or implicit numeric conversion |
| Numeric XML (`t=n` or omitted) in Integer/Decimal fields | Finite `Decimal` from the ASCII stored lexeme directly; XML scientific notation is accepted without passing through Float; integral-Toman validation remains WP-03's responsibility |
| Numeric XML in auxiliary/raw-text fields | Original numeric lexeme as `str`, with no style formatting, leading-zero repair or phone normalization |
| `date_raw` and technical IDs | Text only; no numeric Excel serial, `t=d`, timestamp conversion or ID generation/repair |
| Text in Integer/Decimal fields | Preserve the text; let WP-03 apply its existing digit/whitespace/fixed-point rules; textual exponent notation remains invalid |
| Retained Boolean, error, unsupported type or malformed numeric encoding | Typed failure; never coerce to text/zero/Null or drop the row |

Rich text consists only of the ordered plain text fragments under `t`/`r/t`, not `rPh`, style or annotation text. Decode each fragment's OOXML escapes once before joining: cover carriage returns, escaped literal `_xHHHH_` tokens and valid UTF-16 surrogate pairs without recursive decoding or replacement of unpaired surrogates. Normal XML entity decoding is transport decoding, not Raw normalization.

No quantization, rounding, Toman conversion, formatted-number interpretation, Formula/Cached Value or accounting enrichment is allowed. Excluding formula cells must happen before attempting to resolve a cached shared-string index or parse a cached number.

The special `date_raw` text/date boundary follows WP-03's existing field-name convention, not a second hard-coded list of transactional sheets.

### D. Row activity and identity

- Row 1 is headers; rows 2 onward are candidates. Activity uses only the registry's `activity_columns` and retained literal input, never a hard-coded list of sheet/field names.
- Numeric zero is activity. Missing values, `""` and whitespace-only text are not. Whitespace checking must not alter retained text. Malformed nonblank activity encodings are errors; they cannot be used to classify a row as inactive.
- Date-only, ID-only, formula-only and formatting-only rows are inactive. Omit an inactive row even if it has a leftover date/ID; do not generate/reserve/check its identity as an active identity. Do not apply date canonical validation to an inactive template row.
- Clearing every activity field therefore removes the row from the source collection. There is no confirmation, row-count threshold or special restriction for clearing/deleting party-list rows. This is not yet an executed void or database change.
- Require the technical UUIDv7 for every active row. Missing, malformed, non-v7 or duplicate IDs fail, including duplicates between sheets. The existing WP-04 builder owns canonical/global ID validation; no auto-repair or UUID generation is allowed.
- Construct exactly the contract's raw field mapping, in registry order, for each active row; absent/excluded fields are explicit `None`. Header requirements are not business requiredness: do not invent new required amount/date/name/code rules beyond the accepted source/canonical validation.
- Only after all four sheets succeed, return WP-04's complete snapshot. Unknown parties/items, financial codes, RS pairing, opening transfers, fiscal years and current/archive selection are outside this reader.

### E. Streaming, safety and typing

- Stream selected worksheet/shared-string parts and release completed XML subtrees/preceding siblings. Avoid full worksheet DOMs, `read()` of entire worksheet parts and full shared-string materialization; retain only entries potentially needed by source/header/ID candidates and discard unused ones from the result.
- Bounded multiple streaming passes are allowed for formula coverage and shared-string selection. Record the pass strategy. It must not scan a string table per row or all formula ranges per cell; complexity must be linear in bytes scanned plus indexed lookup/sorting, not quadratic in rows/groups.
- Configure XML parsing explicitly: `load_dtd=False`, `dtd_validation=False`, `attribute_defaults=False`, `resolve_entities=False`, `no_network=True`, `recover=False`, `huge_tree=False`. Reject DTD/entity constructs in consumed XML and do not invoke XInclude. Keep text whitespace.
- Read required members fully so truncation/CRC/XML failures cannot be hidden behind an early return. Never swallow parse errors and replace a broken sheet with an empty one. Close handles on both success and error.
- Use the existing frozen dependencies, including `lxml 6.1.2`. It has no packaged typing files; a single focused private typed lxml boundary and justified per-import `# type: ignore[import-untyped]` are allowed. Do not relax project-wide mypy or add dependencies/stubs without a new Codex decision.
- Do not introduce workload/deletion approval limits. Performance measurements and Excel/format-validity checks are not user-confirmation rules.

## In scope

1. Focused reader/helper/result/error modules under `apps/local_agent/src/accounting_local_agent/` and intentional exports.
2. Local-agent README describing the API, decoding/blank-row rules, supported profile and the remaining pre-Commit checks.
3. Synthetic XML/ZIP fixture builders and reader/integration/property tests under `tests/`; a small synthetic benchmark helper there is allowed.
4. Handoff, Acceptance Matrix and test-results in the exact path above.

## Out of scope and protected boundaries

- Opening, finding, copying or modifying any actual/reference Excel workbook or real-data copy, including the existing local prototype database.
- Excel/COM, Save/OneDrive monitoring, fixed-copy acquisition/cleanup in production, archive discovery, UUID writer/generator/repair, phone entry or identity activation.
- SQLite/PostgreSQL schema, persistence, prior-state repositories, `import_id`, Revision/Outbox writes, sync, financial/ledger rules, report generation or Telegram.
- Changes to `packages/contracts`, `packages/domain`, accepted Golden Vectors/versions, dependencies/lockfile, CI configuration, `ROADMAP.md`, ADRs, issued/accepted work packages or the skill.
- Real fixtures, committed XLSX/SQLite/PDF files, secrets, host/IP configuration, live infrastructure, direct `main` changes, Push, PR, Merge or deployment.

Synthetic fixtures are the explicit exception to the ban on using workbooks: generate them from code into memory or an isolated temporary test directory, never by reading/copying a real file. Commit only their generators and tests, not generated archives. Synthetic phone fields use unmistakable placeholders such as `SYNTHETIC-PHONE-001`.

## Required acceptance evidence

Capture exact commands, exit codes and actual results. Organize the matrix by these identifiers so missing evidence is visible.

| ID | Required evidence |
|---|---|
| XR-01 | Both namespace families; relationship-based part resolution with reordered ZIP/sheet declarations and nonstandard worksheet filenames; extra report sheets ignored; all four source sheets returned |
| XR-02 | Every registry-required Persian and technical header checked; missing/wrong/formula-backed headers rejected; optional-header columns accepted; no duplicated production sheet/column registry |
| XR-03 | Sparse cells, explicit empty text versus missing values, rich text/whitespace/OOXML escapes, large exact numbers, Decimal scale, XML exponent versus textual exponent, numeric auxiliary text, numeric financial-date rejection and Boolean/error rejection |
| XR-04 | Normal/shared/array/data-table formula cases, covered cells without `f`, anchors outside Raw, literal shared-range overrides, malformed excluded caches and literal `=` text; only actual literal changes affect hashes |
| XR-05 | Date/ID/formula/style-only rows skipped; zero retained; whitespace preserved on active rows; inactive-date/ID cases; active missing/duplicate/non-v7 ID fails; clearing activity inputs omits exactly that identity |
| XR-06 | Hidden/filtered rows retained, misleading dimensions ignored, distant physical rows read, row/cell order changes harmless and duplicate/mismatched/out-of-range references rejected |
| XR-07 | Invalid/truncated ZIP/XML, missing source part, duplicate source sheet/target/member, wrong part kind, invalid needed shared-string index, invalid coverage, external/root-escaping target and DTD/entity cases fail with no partial snapshot |
| XR-08 | Independent literal fixture expectations equal the WP-04 builder result and WP-03 hashes. All four sheets participate. Pass parsed results into the unchanged planner to exercise Insert/Edit/Void/Unchanged/reactivation and repeat after state advancement with no fresh event |
| XR-09 | Property tests build independent XLSX byte packages for permutations, shared-string index reorder/remapping, equivalent inline/shared representation, physical row reorder and excluded formula/cache-only changes; compare snapshots/hashes and complete plan fields, excluding only location metadata when positions actually change |
| XR-10 | Direct result/location construction rejects inconsistent IDs/sheets/positions/version; mutating caller collections cannot mutate a result; result survives stream closure; no untyped public API or import-time Office/network/database dependency |
| XR-11 | Reader leaves the synthetic input bytes/hash unchanged on success and failure, opens only required members, performs no network/write and releases file handles (including Windows rename/open checks). An unread malformed helper sheet, unrelated external-link relationship or invalid derived-cell cache cannot block valid Raw |
| XR-12 | Reproducible 15,000-active-row synthetic streaming benchmark plus formula/formatting tails and unrelated shared strings; exact counts/hashes checked, timing and actual peak process memory printed, no quadratic scan strategy |
| XR-13 | All existing 89 tests remain green; complete suite, lock/frozen sync, Ruff format/lint, strict mypy, diff/asset/sensitive scans and Handoff validator pass; both GitHub CI jobs must pass after Codex independently reviews and publishes the branch |

### Benchmark protocol

- Generate a reproducible workbook with 15,000 active rows distributed across all four sheets, preassigned synthetic UUIDv7 values, numeric and textual inputs, at least 5,000 inactive/formula-only tail rows and unused shared-string entries. Include misleading large worksheet dimensions without expanding them into work.
- Generate the fixture outside the measured reader invocation. In a fresh process measure the whole public reader call, including ZIP/XML parsing, decoding, WP-04 construction and hashing. Print `rows`, `read_build_seconds` and actual `peak_rss_mib`/Windows peak working set; record Python/lxml versions, OS, exact command and exit code.
- The synthetic reader target is under 15 seconds and under 128 MiB peak process memory. Measure resident/working-set memory, not only `tracemalloc` Python allocations, which omit native XML buffers. Use standard-library/platform instrumentation or existing OS tooling, without a new package dependency.
- Record Linux and Windows results; use CI output for the platform unavailable to the implementer, and mark that evidence pending Codex CI rather than inventing a measurement. A threshold failure is a review issue, not permission to raise the target or remove the test.
- Include the benchmark in the normal pytest suite and make its measurement line visible even on a successful run with the existing CI capture settings (for example, print the verified subprocess metrics inside `capsys.disabled()`). Do not require a CI configuration change or a separate unexecuted Windows command to obtain this evidence.
- The fixture is not the actual Excel file. These results cannot close the real-file performance or full G1 criteria.

### Quality and handoff commands

Run narrow reader tests first, then at least:

```text
uv lock --check
uv sync --frozen --all-packages --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run pytest -v
git diff --check origin/main...HEAD
python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-05-streaming-xlsx-source-reader/
```

Record a concrete benchmark command with uncaptured printed measurements and failure-on-match asset/sensitive scans. A scan command must fail when it finds a forbidden file/value and must not mask tool errors with `|| true` or unconditional success. Do not weaken the existing Windows/Linux workflow or import architecture guard.

## Preconditions, stop conditions and handoff

Start only in `/srv/accounting-bot/workspace`, with a clean `main` equal to `origin/main` containing this package and ADR-0008. Activate the repository `accounting-bot-implementer` skill and create the exact implementation branch above.

Stop for Codex if the baseline is dirty/divergent, an accepted rule conflicts with the supported XLSX representation, reliable exclusion needs an unapproved formula/encoding rule, a dependency/contract change is needed, the benchmark cannot meet its target, or progress appears to require real data or any out-of-scope action. Do not substitute a permissive decoder, partial snapshot or formula cache to make tests pass.

Complete the skill's `handoff.md`, `acceptance-matrix.md` and `test-results.txt` templates, run the validator, commit only in-scope implementation/evidence, and report the exact baseline/HEAD, changed files, public API, decoding/formula/pass strategy, test/benchmark commands and results, remaining risks and protected-asset check. Stop for independent Codex review. Do not Push, create a PR, Merge, deploy, edit Roadmap or start WP-06.

## Independent acceptance record

Codex Project Manager accepted this package after independently reviewing candidate `6684dda192a7cac9b66fcb9442b377e04b6e77db` against baseline `de6c475c1c6a96036e8962f633c8825e6ec3cf20`, validating the successive Reader, raw-preservation, selective-SST, constructor/property/lifecycle, performance and cross-platform RSS corrections, and merging [PR #19](https://github.com/mohsenbarari/accounting-bot-public/pull/19).

- All 200 tests passed independently on the development server and in both Linux and Windows jobs of [CI Run 33505486610](https://github.com/mohsenbarari/accounting-bot-public/actions/runs/33505486610).
- Final CI measured the contract-complete 15,000-active-row benchmark at `2.9061s / 58.96 MiB` on Linux and `4.6764s / 59.34 MiB` on Windows, below the unchanged limits of 15 seconds and 128 MiB absolute call-window RSS.
- Independent server replay passed strict native and `--platform win32` Mypy, six focused RSS/Sampler/VmRSS tests, the complete suite and three benchmark repetitions: `10.5314s / 59.04 MiB`, `10.1004s / 59.12 MiB` and `10.5874s / 59.13 MiB`.
- Generated ZIP/XML fixtures cover both namespace families, relationship and part resolution, exact Persian/technical headers, sparse/rich/shared text, Decimal/raw preservation, strict cell coordinates, formula/cache and array/data-table exclusion, row activity, UUIDv7 identity, all-or-nothing failure and read-only file integrity.
- Parsed results were compared with independent WP-03/WP-04 oracles and exercised complete Insert/Edit/Void/Unchanged/reactivation planning, idempotency, randomized physical/string representations and late fourth-sheet failure.
- Lock/frozen sync, Ruff format/lint, strict cross-platform Mypy, Handoff validation, diff checks and failure-on-match prohibited-asset/sensitive-pattern scans passed. No reference workbook, real data, database, identity, secret or production asset was accessed or modified.

Acceptance is limited to the read-only physical XLSX-to-validated-source boundary proved with generated synthetic workbooks. Stable-copy acquisition, Save/OneDrive monitoring, Excel COM/UUID writing, fiscal/archive selection, business requiredness, real-workbook reconciliation, SQLite/import commit, Outbox and end-to-end G1 evidence remain outside this package. G1 remains open.
