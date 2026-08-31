# ADR-0008: Read-only streaming XLSX source extraction

- Status: Accepted
- Date: 2026-08-31
- Work Package: Phase 1 — WP-05
- Decision owner: Codex Project Manager under O-46/O-49

## Context

WP-02 defines the four source sheets and their columns; WP-03 defines canonical values and hashes; WP-04 validates a complete source snapshot and plans changes. The next missing boundary is converting physical XLSX cells into those contracts without importing a formula result, losing numeric precision, mistaking a template row for a transaction or treating an incomplete read as deletion.

This ADR resolves O-70. It refines the already accepted `zipfile` + `lxml.iterparse` reader in ADR-0002; it does not change the financial model, permit an actual import or authorize access to the reference workbook.

## Constraints

- Read only the caller-supplied, already stable snapshot. Snapshot creation, Save monitoring, archive/year selection and UUID writeback belong to later packages.
- Source data is limited to the authoritative WP-02 registry. A workbook may contain additional report/helper sheets; they are not passed to WP-04.
- No formula evaluation, cached result, formatted display value, row position or style enters a source hash.
- Use exact Decimal values and the unchanged WP-03 canonical rules. Never introduce binary Float into the source-value path.
- All implementation evidence is generated from synthetic XLSX packages. Reference files, real-data copies, identities, databases and production remain protected.
- G1 stays open until end-to-end import and the other G1 evidence are independently accepted.

## Options considered

### Option A — Streaming adapter into the accepted source contracts

- Benefits: preserves stored numeric precision, exposes formula provenance before conversion and reuses the approved contracts and pinned stack.
- Costs/risks: requires explicit package, string, formula-range and empty-row rules plus genuine XML/ZIP integration tests.
- Reversibility: high; it is isolated in the local-agent adapter and has no persistence or side effects.

### Option B — A general workbook object model or DataFrame importer

- Benefits: convenient tabular access and broad formatting support.
- Costs/risks: additional dependency/model overhead; automatic number/date conversion and cached-value modes can cross the approved source boundary.
- Reversibility: moderate; any coercion that reaches persisted history would require a separately reviewed reconciliation.

### Option C — Read live Excel through COM

- Benefits: convenient access to the open workbook.
- Costs/risks: couples extraction to a live changing document, Office availability and Windows; conflicts with the accepted fixed-snapshot boundary.
- Reversibility: moderate; COM remains appropriate for the separately authorized event/UUID adapter, not this reader.

## Recommendation and accepted decision

Option A is accepted. Implement `xlsx-source-reader.v1` inside `accounting-local-agent` using the existing locked `lxml 6.1.2` and standard-library `zipfile`. No dependency or lockfile change is needed.

### Package and completion boundary

Support ordinary unencrypted `.xlsx` OPC packages in the Transitional and Strict SpreadsheetML namespace families. Resolve the workbook and selected worksheet parts through package/workbook relationships, not sheet order, `sheetId`, a filename guess or fixed `sheet1.xml` names. Resolve internal package-relative targets safely; do not follow external relationships or extract ZIP members to the filesystem.

Require each approved sheet exactly once, distinct selected worksheet targets, structurally valid consumed parts, the exact required headers in row 1 and the technical ID header from the registry. Missing required parts/headers, ambiguous relationships, duplicate members or row/cell coordinates and malformed consumed XML fail the entire read. Read physical cells to EOF; worksheet dimensions, filters and hidden rows are not completeness indicators.

Return a result only after all four selected sheets and required shared-string content have been consumed successfully and `build_source_workbook_snapshot` succeeds. No public partial-success iterator, completeness flag or caller-supplied hash is permitted. Archive/helper contents outside the required source path are not interpreted or evaluated.

### Formula boundary

Classify before converting a value. A cell with an `f` element is excluded even when its formula text is empty, its cached value looks valid, or its cache contains an error or an invalid shared-string reference. Normal and shared formula cells therefore do not need calculation or formula expansion.

Array/data-table formula output ranges require coverage handling: a covered output cell can lack its own `f` element. Exclude that output rather than importing its cache. Discover ranges before finalizing values, including anchors outside the raw whitelist. Shared-formula ranges are different: do not blanket-exclude literal overrides merely because their coordinates lie inside a shared range. These distinctions follow the [Open XML formula representation](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.spreadsheet.cellformula?view=openxml-3.0.1).

Invalid/unsupported coverage metadata that prevents reliable source classification is a typed structural error, not a guessed literal value. A formula calculation error or stale cache alone is not such an error. A formula in a required header or an active row's ID cannot be replaced with its cache to satisfy the requirement.

Known derived/unlisted cells are excluded even if they contain literals. An excluded raw-column cell maps to `None`. Changing only excluded formula/cache content is a no-op; replacing an actual literal input with a formula removes a source value and is not a formula-only no-op.

### Literal and activity boundary

- Decode shared, inline and direct text to cell text, preserving whitespace and Unicode. Preserve explicit empty text as `""`, distinct from a missing/blank cell (`None`). Join rich-text runs in order without phonetic annotations or formatting content; decode OOXML text escapes once without recursively interpreting an escaped literal token. The [shared-string representation](https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/working-with-the-shared-string-table) and [Excel string-escape rules](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-oe376/bd0aa042-434a-4ca7-b25f-4e1fd25a954d) define the container encoding, not new Raw normalization.
- For numeric XML cells in Integer/Decimal fields, construct `Decimal` directly from the finite ASCII numeric lexeme, including an XML exponent when present. WP-03 still enforces integral Toman values. Do not round, multiply by ten or reconstruct precision already lost by Excel.
- Numeric XML in an auxiliary/raw-text field is preserved as its numeric lexeme string without display formatting or padding. Financial `date_raw` and technical IDs require text; do not infer a date from an Excel serial or `t=d` cell. Text in numeric fields remains text and follows unchanged WP-03 rules, including rejection of textual scientific notation.
- Boolean, error and unsupported cell encodings in retained input are typed errors, not zero, text, Null or skipped rows. Excluded formula/cache values are never decoded for this validation.
- A data row is active only when a literal in the registry's `activity_columns` is nonblank: zero counts; `None`, empty text and whitespace-only text do not. Whitespace is checked only for activity and is never stripped from retained Raw. Dates, IDs, row numbering, derived cells, styles and formulas alone cannot activate a row. A malformed activity value is an error, not evidence that the row is blank.
- An inactive row is omitted, regardless of a leftover date or ID; its other values/ID do not create or reserve an identity. Thus clearing all activity inputs removes that row from the source collection. This is consistent with unrestricted valid edits/deletes, and does not introduce confirmation or deletion thresholds. Physical deletion/clearing can become a planned void only downstream after a complete valid read and the remaining mandatory validation.
- Every active row needs a valid UUIDv7. The reader never invents, repairs or writes an ID. All four sheets, including present-but-empty sheets with valid headers, are passed to WP-04 for global identity and canonical validation.

### Result, diagnostics and streaming

Return the existing immutable validated snapshot plus an immutable UUID-to-source-location mapping outside its hashes. A location identifies an approved sheet and physical data-row number, not financial identity. The mapping must match the snapshot's identities/sheets exactly and use defensive copies; its order follows UUID bytes. Reordering physical rows may change locations, but not the snapshot hashes or plan.

Exceptions expose a typed reason and sheet/cell location when known; default messages must not disclose raw values, workbook paths or formula payloads. The reader does not log, send a report or invoke the planner automatically.

Process selected worksheet/shared-string XML with incremental parsing and clear completed subtrees/preceding siblings. Keep only needed shared strings and compact formula coverage/row staging, never a full worksheet tree or caches from report sheets. Avoid per-row rescans of string tables and per-cell linear scans of all formula groups. Incremental parsing still constructs tree nodes unless they are released, as described in the [lxml parsing documentation](https://lxml.de/parsing.html).

Explicitly disable DTD loading/validation/default attributes, entity resolution, recovery and huge-tree mode; keep network access disabled and reject DTD/entity constructs in consumed XML. Do not invoke XInclude or external resolvers. All file/member handles close on success and failure.

The pinned lxml package has no bundled typing files. A private typed adapter with a narrowly scoped `import-untyped` suppression is allowed if necessary; project-wide mypy settings, strictness and accepted contract modules must not be weakened or rewritten.

## Roadmap and acceptance impact

- References: sections 4.2/4.3, 5.1/5.2/5.4, 15.6, 19.1 and Phase 1; O-03/O-06/O-25/O-26/O-53/O-68/O-69/O-70.
- WP-05 must prove real ZIP/XML extraction on generated fixtures, all-or-nothing completion, precise numeric/text behavior, formula/range exclusion, row-activity/ID handling, deterministic integration with WP-04 and bounded streaming behavior on 15,000 active synthetic rows.
- Required-field/business validation, RS resolution, fiscal-year/archive policy, live snapshot acquisition, persistence/Outbox and real-file performance remain separate prerequisites. A successful reader result is not permission to commit to SQLite or publish reports.
- The synthetic time/memory evidence does not close the real-workbook performance criteria, O-25 reconciliation or G1.

## Migration and rollback impact

No migration or persistent write. Reverting/disabling this reader removes only an unused adapter; accepted WP-02/WP-03/WP-04 contracts and their Golden Vectors remain unchanged. A future change to decoded source meaning requires a versioned decision and reconciliation before operational use, not silent reinterpretation of history.

## Reconsideration triggers

- An authorized real-file test exposes a source encoding or formula coverage not represented by this profile.
- Streaming benchmarks exceed the approved resource budgets or demonstrate quadratic behavior.
- A required fix would change an accepted Raw/hash contract, add a dependency or mix extraction with financial/identity resolution.

## Approval required

Codex Project Manager approves this bounded technical decision on the date above. Implementation is authorized only through WP-05 after these documents are merged. No reference Excel access, real-data operation, UUID writeback, deployment or new cost is authorized.
