# ADR-0012: Required-field preflight for complete raw source snapshots

- Status: Accepted
- Date: 2026-09-02
- Work Package: Phase 1 — WP-09
- Decision owner: Codex Project Manager under O-46/O-49

## Context

WP-08 now delivers an immutable, complete four-sheet WP-04 snapshot through the
accepted WP-05 Reader. That type proves the structural and canonical source
contract, including UUIDs, field keys, non-null value types and hashes. It
deliberately permits null raw values and does not prove that an active transaction
has its required date, party, amount or quantity. Writing such a snapshot directly
to an operational importer would bypass Roadmap 5.2 step 3.

The next bounded prerequisite is a pure, deterministic required-field preflight.
It must preserve the original snapshot, report every missing required value, and
distinguish a real zero from missing input. This resolves O-74. It is not the full
financial validator or a commit-authorization type. Fiscal/archive eligibility,
code/direction semantics, identity/item resolution, RS validation, affected domains,
SQLite, import IDs and Outbox remain separate work.

## Constraints

- Roadmap 5.2/5.4 rejects incomplete essential raw data before an operational commit.
  Sections 6/7 and O-18/O-33 require amount, price, item and quantity to retain their
  meaning; real zero is not null, and no implicit currency conversion is allowed.
- O-60 permits blank C/D notes. O-04 permits blank H/HA/HS notes. Phone/contact
  details are not a prerequisite for retaining an accounting party's raw row.
- O-16/O-17/O-32 preserve unresolved raw rows. A nonblank unknown name/item/code
  must not be misclassified as a missing field by this layer.
- The existing Raw registry, WP-03 canonicalization and WP-04 constructors remain
  authoritative. Do not modify their accepted behavior, activity rules or hashes.
- Only generated synthetic values/files are used. No reference workbook, real-data
  copy, COM, UUID writeback, watched real folder, database, network or deployment.

## Options considered

### A — Pure required-field preflight over a complete typed snapshot

Benefits: exposes the next missing prerequisite without mixing file decoding,
business resolution or storage. It can aggregate precise issues and be tested with
independent presence oracles. It preserves existing accepted contracts.

Cost: a passing report proves only this check. A future complete admission boundary
must also enforce the other prerequisites before persistence.

### B — Add required-field rejection directly to the XLSX Reader

Benefits: fewer public modules. Costs: changes the accepted extraction contract,
mixes source decoding with requiredness and makes aggregate diagnostics harder.

### C — Add full validation, SQLite, Outbox and watcher commit integration together

Benefits: a larger operational slice. Costs: combines unresolved fiscal/RS/resolution
contracts with irreversible storage behavior and obscures which validation permits
a commit. This is too broad for the next independent handoff.

## Decision

Choose A. Add `source-requiredness.v1` in `accounting-contracts`; no new dependency
or package relationship is needed. The module is
`packages/contracts/src/accounting_contracts/source_requiredness.py`.

### Requiredness matrix

The following field names refer to the existing registry; no Excel column/header
registry is duplicated. Fields not listed as required remain optional for this
preflight. Iterate fields in the registry's raw-column order.

| Sheet | Required fields | Optional at this boundary |
|---|---|---|
| خرید-فروش | `date_raw`, `party_name_raw`, `transaction_type_raw`, `item_name_raw`, `quantity_raw`, `unit_price_toman_raw` | `discount_toman_raw`, `notes_raw` |
| دریافت-پرداخت | `date_raw`, `party_name_raw`, `entry_type_raw`, `amount_toman_raw` | `notes_raw`, `account_code_raw`, `customer_flag_raw` |
| ورود-خروج | `date_raw`, `party_name_raw`, `movement_type_raw`, `item_name_raw`, `quantity_raw` | `purity_raw`, `notes_raw`, `customer_flag_raw` |
| لیست کسبه | `party_name_raw` | `phone_number_raw` |

`purity_raw` is not universally required: whether an item requires purity depends
on the later approved Item Master. Passing this preflight does not waive that
conditional rule. Likewise this table does not choose a valid monetary direction,
an allowed transaction code, or a fiscal year.

### Exact presence rules

1. A required field whose value is `None` yields `MISSING_VALUE`.
2. A required text field whose value is a string and whose `str.strip()` result is
   empty yields `BLANK_TEXT`. Use only this predicate; never replace the raw value.
3. Every other value already admitted by WP-04 counts as present. In particular,
   numeric zero, negative values and accepted textual/Decimal numeric forms remain
   unchanged. This does not settle later sign, unit, precision or financial rules.
4. Date parsing and type validity remain WP-03/WP-04 responsibilities. A null date
   reaches this preflight and is missing. Invalid non-null dates, floats, Booleans,
   nonfinite numbers and absent mapping keys fail upstream; do not create a second
   parser or turn upstream failures into empty snapshots or passing reports.
5. Do not trim stored data, normalize Persian/Arabic letters, infer a name/code,
   replace null with zero, generate UUIDs, or derive input from formula/cache values.

No row-count minimum or change-volume threshold is added. A complete workbook with
four present but empty sheets passes requiredness with zero checked rows/issues.
This preserves legitimate full deletion; structural completeness is still enforced
by the existing snapshot constructor. Date-only/template row activity remains the
Reader's accepted responsibility and is not reinterpreted here.

### Public API and invariants

Export these symbols through `accounting_contracts.__init__`:

- `SOURCE_REQUIREDNESS_VERSION = "source-requiredness.v1"`.
- `SourceRequirednessInputError(ContractError)`: invalid API input; fixed message
  without interpolating raw values, paths, names, amounts or contact details.
- `SourceRequirednessIssueReason(StrEnum)`: `MISSING_VALUE = "missing_value"` and
  `BLANK_TEXT = "blank_text"`.
- `SourceRequirednessIssue`: frozen/slotted metadata with `sheet_name`, `stable_id`
  (UUIDv7), `field_name`, and `reason`. Validate actual enum/type, known sheet and a
  field that is required for that sheet. A `BLANK_TEXT` issue is only legal for a
  text field. No raw cell value, row number or free-form message is stored.
- `SourceRequirednessReport`: frozen/slotted report constructed from only a
  `ValidatedSourceWorkbookSnapshot`. It retains that exact object as `snapshot`
  with the snapshot excluded from `repr`. Derive `issues` and all counts internally;
  callers cannot supply a claimed result, status, issues or counts at construction.
  Direct construction performs the same evaluation as the function below.
- `evaluate_source_requiredness(snapshot) -> SourceRequirednessReport`.

The report exposes an immutable tuple `issues`, `checked_row_count`,
`failed_row_count`, `issue_count`, and `passes_requiredness`. Counts include all
four sheets, count each failing UUID once, and count every missing required field.
`passes_requiredness` is derived from an empty issue tuple. Values cannot be
overridden to forge a passing report through supported constructors or setters.
Ordinary Python signature errors can remain `TypeError`; invalid supplied snapshot
or issue field values use `SourceRequirednessInputError`. Reflection-based bypass
of frozen Python objects is not a security boundary promised by this library.

Collect all issues, rather than stopping at the first row or field. Order them by
registry sheet order, UUID bytes, then registry raw-field order. Input mapping,
sheet or row iteration order cannot change issues/counts. Do not include time,
random identifiers, filesystem location or environment-dependent status.

Requiredness failures are report data, not exceptions. Invalid API input is an
exception. Do not catch unexpected internal failures and return a passing report.
The report's ordinary repr and input-error messages must not reveal raw values;
the explicitly retained snapshot remains available to a caller intentionally
inspecting source data. This is not a guarantee that arbitrary tracebacks redact
data held elsewhere by the caller.

### Boundary with downstream work

- No party or item lookup, alias/fuzzy matching, phone validation or code membership
  validation. Nonblank unresolved fields are retained. C/D/H/HA/HS blank notes are
  permitted, and RS structure/pairing is neither accepted nor rejected here.
- Do not expose `commit_allowed`, `import_valid`, a financial ACK or an
  `ImportValidatedSnapshot` type. Do not connect this report to a database, watcher
  callback, automatic Plan application or report publication in WP-09.
- This report is one future admission prerequisite. Even a passing report must
  still pass fiscal/archive eligibility, full financial/RS validation and durable
  transaction ownership before a later work package may commit an import.
- No input filtering or mutation: failing rows and their hashes remain present in
  `report.snapshot`. Unknown/resolution-pending data is not dropped.

## Evidence and acceptance impact

WP-09 specifies SR-01 through SR-14, including an independent required-field oracle,
null/blank/zero boundaries, constructor invariants, complete issue aggregation,
permutation invariance, raw preservation, synthetic Reader integration, and a
15,000-row scale case. Maintain the current 350 collected tests, full typing and
quality checks, native Windows/Linux CI and unchanged Reader/acquisition benchmark
limits. No new timing sleeps or background threads are needed for this pure layer.

Acceptance would prove only requiredness behavior on synthetic snapshots. Roadmap
5.2's complete two-level validation, real-file reconciliation, the operational
import transaction and G1 remain unproved by this package alone.

## Migration and rollback

No data schema or persistent state is introduced. Rollback is a reviewed revert of
the fixed package commits on a clean disposable branch; verify the resulting diff
against its recorded baseline before applying a chosen rollback elsewhere. State
whether the scope includes code only or also handoff documentation. Do not remove
files blindly, alter accepted source contracts or claim database rollback evidence.

## Reconsideration triggers and authority

A conflicting approved required-field rule, need for a condition based on Item
Master/RS/fiscal policy, or an existing contract incompatibility requires a bounded
Codex scope decision. Do not widen the package or change v1 semantics silently.
Codex approves this technical preflight within the existing product rules; its
implementation may begin only after this ADR and WP-09 are merged. G1 remains
OPEN / IN PROGRESS; protected owner assets remain outside this authorization.
