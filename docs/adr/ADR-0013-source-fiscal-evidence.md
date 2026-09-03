# ADR-0013: Fiscal-year evidence from raw source dates

- Status: Accepted
- Date: 2026-09-03
- Work Package: Phase 1 — WP-10
- Decision owner: Codex Project Manager under O-46/O-49

## Context

WP-09 establishes required-field presence. It deliberately does not establish
fiscal/archive eligibility. Roadmap 5.3 states that a new annual workbook can have
the same filename, so the filename cannot identify its fiscal year. O-07 excludes
archived files from ordinary operational import. WP-03 already parses source
Jalali dates and returns a fiscal-year key, but no existing contract aggregates
that evidence across a complete WP-04 workbook snapshot.

Date observations must not become an invented admission rule. A source can have
no dated transaction rows after valid deletions. A single observed year does not
prove that the file is operational, and mixed years do not establish an archive
transition or justify rejecting or filtering the snapshot. A later source-binding
and fiscal/archive policy needs explicit authority and evidence beyond this report.

## Constraints

- Roadmap 5.3/O-07: determine fiscal context from content or an approved internal
  marker, not a filename; ordinary sync never revises archived data.
- Roadmap 4.3/5.1/O-06: no row minimum, deletion threshold, confirmation flow or
  quarantine based on change volume. Empty snapshots remain structurally valid.
- O-42/O-68: use `jalali-date.v1` and its fiscal-year result. UUID creation time,
  Gregorian year and the machine's current date are not fiscal-source evidence.
- O-69/O-70/O-74: preserve the complete snapshot, raw values, identifiers and hashes.
  Requiredness, structural validity and fiscal observation are separate facts.
- No workbook marker has been approved or exposed by the current Reader. WP-10
  introduces no cell/property convention, marker write, source binding or I/O.

## Options considered

### A — Infer an operational year and reject other snapshots

This appears to provide immediate admission but needs source identity, activation,
archive state and annual-transition rules. Choosing a filename, maximum/majority
date year, machine year or caller flag can misclassify an old copy. Requiring one
year or at least one date can also obstruct valid deletions. Rejected for WP-10.

### B — Pure, deterministic observation of existing date fields

This reuses the accepted parser, makes incomplete and mixed evidence explicit and
preserves all source data. It is a small prerequisite that can be verified without
choosing activation, archival or opening-balance behavior. Its cost is an O(n)
metadata report and a separate later eligibility decision. Selected.

### C — Add an internal workbook marker and durable source registry now

This can support empty files and durable annual transitions, but requires choosing
marker semantics, Reader behavior, source identity and persistence. Real workbook
changes also require target-specific Owner authority. Deferred to a later ADR/WP.

## Decision: source-fiscal-evidence.v1

Add a pure module at
`packages/contracts/src/accounting_contracts/source_fiscal_evidence.py`.
Use only the standard library and existing contracts. Export exactly these new
symbols through the package's public exports:

| Symbol | Contract |
|---|---|
| `SOURCE_FISCAL_EVIDENCE_VERSION` | Exact string `source-fiscal-evidence.v1` |
| `SourceFiscalEvidenceInputError` | Subclass of `ContractError`; fixed messages for invalid public API/metadata input |
| `SourceFiscalRowEvidence` | Frozen/slotted metadata: `sheet_name: str`, `stable_id: UUID`, `fiscal_year: int \| None` |
| `SourceFiscalYearCount` | Frozen/slotted metadata: `fiscal_year: int`, `row_count: int` |
| `SourceFiscalEvidenceReport` | Frozen/slotted report; the only constructor input is `snapshot: ValidatedSourceWorkbookSnapshot` |
| `evaluate_source_fiscal_evidence(snapshot)` | Returns the same computed report contract as direct report construction |

### Source selection and interpretation

1. Use every row in the three existing transaction contracts: buy/sell,
   receipts/payments and inventory movements. For each, read only `date_raw`.
   Obtain sheet names/order from the existing registry/constants; do not duplicate
   headers or column mappings.
2. Parse each non-null date with `parse_canonical_jalali_date` and use its
   `fiscal_year`. Retain the original text and all other raw values unchanged.
   The existing accepted date grammar remains authoritative.
3. A null `date_raw` produces row evidence with `fiscal_year=None`. It is not an
   exception or a fabricated year. WP-09 independently reports its requiredness.
4. Business-party rows contribute only to `non_transaction_row_count`. Date-like
   names, notes, phone numbers, formulas/caches, UUID timestamps, path text and
   other fields cannot contribute a year.
5. Include every transaction row, including unresolved names/codes and any
   opening-transfer-looking rows. Do not identify or remove opening balances.

### Derived report fields and invariants

The report retains `snapshot` by identity and derives all other fields itself:

- `rows`: tuple of `SourceFiscalRowEvidence`, ordered by registry sheet order then
  UUID bytes. Each transaction row appears exactly once, including null dates.
- `year_counts`: tuple of `SourceFiscalYearCount` for observed non-null years,
  strictly ascending by year; every count is positive.
- `observed_years`: tuple of the years in `year_counts`, with no duplicates.
- `transaction_row_count`: `len(rows)`.
- `dated_row_count`: sum of `year_counts.row_count`.
- `undated_row_count`: number of rows whose fiscal year is None.
- `non_transaction_row_count`: number of business-party rows.

`dated_row_count + undated_row_count == transaction_row_count`, and
`transaction_row_count + non_transaction_row_count == snapshot.total_row_count`.
There is no workbook-level `fiscal_year`, majority year, selected active year,
`passes_fiscal`, `commit_allowed` or analogous eligibility/permission flag.

Four empty sheets yield empty tuples and zero counts. A party-only workbook has
no observed years and a nonzero non-transaction count. All-null transaction dates
yield row metadata with None and no year counts. One or multiple observed years
are ordinary report data, without filtering, rejection or an automatic transition.

Direct metadata construction validates the existing transaction sheet names,
UUIDv7 identities and fiscal-year/count types. A fiscal year must be an exact int
(not bool or numeric text) whose zero-padded first day of the Jalali year
(`YYYY/01/01`) is accepted by the existing date parser and yields that same year. Counts must be
exact positive ints. A row year may additionally be None. Do not invent a 1405
lower bound, a current-year upper bound or a separate calendar implementation.

Direct report construction accepts only the snapshot and recomputes its evidence.
Callers cannot inject rows, years, counts or approval flags, or supply a report
containing forged metadata. Public frozen/slot invariants and tuple immutability
must hold; reflection-based mutation is not a security boundary being promised.

Exclude `snapshot` from ordinary report repr. Report metadata contains sheet,
UUID and observed year/count only, not raw dates, names, notes or contact data.
Invalid API and metadata input produce fixed `SourceFiscalEvidenceInputError`
messages; ordinary signature misuse may be TypeError. Do not catch cancellation
or unexpected internal errors and manufacture empty evidence. Existing snapshot
construction failures remain upstream failures, not a successful assessment.

### Purity and algorithm

No filesystem/network/clock/random calls, threads, UUID generation or source
mutation during evaluation. Normal Python dependency/module loading is distinct
from evaluation purity; a fresh-import test must state its exact guarded scope.

Use a direct row scan with a year-count index and bounded deterministic sorting:
O(n log n) time at most and O(n) additional metadata. Do not copy all raw mappings,
recompute source/sheet hashes or repeatedly scan the full snapshot for each row.

## Consequences and deferred decisions

This report is reusable evidence for future fiscal/archive eligibility. It is not
proof that a source is current, archived, authentic or complete for a particular
year. Missing or mixed date evidence neither authorizes nor forbids operational
deletion. A later ADR must define durable source binding, authoritative year
selection for empty sources, activation/archive transitions and prior-registry
partitioning before an operational importer uses those decisions. An archive or
new-year snapshot must never be fed against a prior registry of unrelated years
and interpreted as their deletions.

WP-10 does not call the Planner, WP-09, watcher or a database automatically. Tests
may compose the pure APIs explicitly. Requiredness, financial/RS validation,
identity/item resolution, opening transfers, persistence and G1 remain separate.

## Evidence, migration and authority

WP-10 defines FE-01 through FE-14 for independent year/row oracles, empty/null/mixed
data, constructor and privacy invariants, canonical date vectors, purity, actual
permutations, synthetic Reader composition and 15,000-row evidence. Preserve all
403 existing collected tests and the Reader/acquisition 15s / 128 MiB ceilings.

No persistent schema or data migration is introduced. Rollback is a reviewed
revert of fixed package commits in a clean isolated checkout, with the actual code
and documentation scope stated. No blind file removal or protected asset changes.

Codex approves this technical observation contract under the existing product
rules. Implementation may begin after this ADR and WP-10 merge. Acceptance needs
independent review of the implementation and native Windows/Linux CI. G1 remains
OPEN / IN PROGRESS.
