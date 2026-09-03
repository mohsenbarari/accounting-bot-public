# ADR-0015: Read source identity and raw rows from one leased XLSX

- Status: Accepted
- Date: 2026-09-03
- Work Package: Phase 1 — WP-12
- Decision owner: Codex Project Manager under O-46/O-49

## Context and alternatives

[ADR-0014](ADR-0014-source-binding.md) selects an annual source's prior registry
from a logical key supplied by its caller. It does not establish that the key and
raw rows came from the same workbook. Reading metadata from the live path and raw
rows from an earlier snapshot could attach one generation to another source.

The accepted WP-06 acquisition verifies and owns a temporary snapshot until its
exit integrity/cleanup checks finish. WP-05 already has an internal ZIP reader
that can consume an open package. WP-12 joins these boundaries without changing
the standalone Reader, coordinator, runtime or pure binding resolver.

| Alternative | Decision |
|---|---|
| Infer identity from filename, dates, byte hash or a sidecar file | Reject: a rename, empty workbook or separate update can select the wrong source. |
| Store metadata in a hidden sheet/cell or defined name | Defer: adds worksheet structure or formula/name interpretation unnecessarily. |
| Use one unlinked custom document property and read it with Raw from one leased package | Select: a small explicit format independent of raw row count and recalculation. |
| Add COM writing, enrollment, SQLite and rollover together | Defer: separate protected-write authority and durable identity/transaction contracts are required. |

This is a project-specific reading profile, not a claim to support every custom
property or OPC feature. Excel exposes workbook custom document properties and
limits string properties to 255 characters; the selected marker fits within that
limit. [Microsoft Excel reference](https://learn.microsoft.com/en-us/office/vba/api/excel.workbook.customdocumentproperties)

## Decision: xlsx-source-identity.v1

Add `apps/local_agent/src/accounting_local_agent/xlsx_source_identity.py` and
export exactly these seven symbols through the local-agent package:

| Symbol | Contract |
|---|---|
| `XLSX_SOURCE_IDENTITY_VERSION` | Exact string `xlsx-source-identity.v1` |
| `XLSX_SOURCE_IDENTITY_PROPERTY_NAME` | Exact string `AccountingBot.SourceIdentity` |
| `XLSX_SOURCE_IDENTITY_MAX_METADATA_BYTES` | Exact int `1_048_576`, per metadata XML part |
| `XlsxSourceIdentityReason` | Exact enum specified below |
| `XlsxSourceIdentityError` | `XlsxSourceReadError` subclass with fixed messages |
| `IdentifiedXlsxSource` | Frozen/slotted data result specified below |
| `read_identified_xlsx_source` | Complete acquire/read/cleanup operation specified below |

No new dependency, public marker-only reader, marker writer, approval flag,
registry mutation, automatic Planner call or runtime integration is introduced.

### Marker serialization

One custom document property, with exact case-sensitive name
`AccountingBot.SourceIdentity`, contains one plain `vt:lpwstr` value:

```text
xlsx-source-identity.v1|00000000-0000-7000-8000-000000000001|1405
```

The UUID above is synthetic. After ordinary XML entity decoding, the value must
contain exactly the version, one canonical lowercase hyphenated UUIDv7 and four
ASCII year digits, separated by two literal `|` characters. No trimming, Unicode
digit conversion, case folding, `_xHHHH_` decoding, extra fields or numeric-type
coercion applies to the value. Parse the UUID text only at this wire boundary,
then construct the existing `SourceBindingKey`; its accepted variant/version and
Jalali year rules remain authoritative. `0001` represents year 1; there is no 1405
or current-year floor. The declared year need not equal every raw transaction year.
The marker is technical source metadata, outside the financial Raw whitelist and
row/sheet hashes. Among these hashes, a marker-only change can affect only the
whole-file byte hash; a changed decoded key remains observable in the result.

The selected property has the standard format GUID
`{D5CDD505-2E9C-101B-9397-08002B2CF9AE}` (hex case may vary), a decimal property ID
in 2..2147483647, and exactly one text-only `lpwstr` child. The marker has no
`linkTarget` attribute, including an empty one, and no nested markup or alternate
value. A linked custom property can hold a cached value; rejecting it prevents
cell/formula-derived identity. Other properties' values are never evaluated.
[Microsoft property definition](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.customproperties.customdocumentproperty?view=openxml-3.0.1)

Only direct `property` children of the `Properties` root supply candidates.
Comments and processing instructions do not supply values. Property names and
IDs must be present; names are nonempty and decimal IDs in the range above are
unique across the part. IDs are compared numerically, so `2` and `02` collide.
Unrelated property values/types and their links are ignored after these structural
checks; they are not operational data. No extension/AlternateContent wrapper is
interpreted as a marker. A sole case-insensitive match with incorrect name casing
is invalid; more than one case-insensitive match is ambiguous even if values agree.

### Package discovery and bounded XML

Discover the property part through `_rels/.rels`, never by assuming
`docProps/custom.xml` or searching arbitrary sheets/XML members. There must be
exactly one package-root custom-properties relationship. Count all matching
relationships before filtering targets: an external or invalid duplicate cannot
disappear from the ambiguity check. Zero matching relationships means a missing
marker; an orphan custom XML part cannot supply it. Relationship IDs must be
present and globally unique in the root relationships part.

The selected relationship must have omitted TargetMode or exact `Internal`.
This v1 profile accepts an ASCII canonical package path, optionally starting with
one `/`, made of nonempty `[A-Za-z0-9_.-]+` segments. Reject `.`/`..` segments,
backslashes, percent escapes, schemes, query/fragment components, empty segments
and directory targets. The ZIP member must exist exactly once. Never extract
members to the filesystem or fetch an external target. Alternative valid part
paths are accepted; a filename is not identity.

`[Content_Types].xml` must contain exactly one matching Override whose PartName
equals `/` plus the selected canonical ZIP member name, without case folding,
percent decoding or path normalization. Its ContentType must be exactly
`application/vnd.openxmlformats-officedocument.custom-properties+xml`.
Duplicate matching Overrides fail even if their types agree. Exact duplicate ZIP
member names and case-only ZIP aliases for the three metadata members fail as
`XlsxPackageError` with the existing duplicate-ZIP-entry reason. Root
relationships/content-type roots use their standard OPC
namespaces. Custom-property namespaces and relationship types support these two
families; require one consistent family for the selected relationship/root/value:

| Component | Transitional | Strict |
|---|---|---|
| Relationship type | `http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties` | `http://purl.oclc.org/ooxml/officeDocument/relationships/customProperties` |
| Properties namespace | `http://schemas.openxmlformats.org/officeDocument/2006/custom-properties` | `http://purl.oclc.org/ooxml/officeDocument/customProperties` |
| Value namespace | `http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes` | `http://purl.oclc.org/ooxml/officeDocument/docPropsVTypes` |

Prefixes are arbitrary. This is a namespace comparison, not a text replacement.
The standard part metadata and namespace-family mappings are confirmed by the
[Open XML SDK part definition](https://github.com/dotnet/Open-XML-SDK/blob/main/data/parts/CustomFilePropertiesPart.json)
and [namespace resolver](https://github.com/dotnet/Open-XML-SDK/blob/main/src/DocumentFormat.OpenXml.Framework/Features/OpenXmlNamespaceResolver.cs).

Bound decompressed reads of each of `_rels/.rels`, `[Content_Types].xml` and the
selected custom-property part to at most 1,048,576 bytes plus one overflow-detection
byte. Check declared size and actual streamed bytes; a ZIP header alone is not
the limit. A valid part at the limit is accepted; one byte above fails. Use the
existing lxml dependency with DTD/entity expansion, network loading, XInclude,
recovery and huge-tree mode disabled; explicitly reject a DOCTYPE. Malformed or
unsafe XML fails without a result. Unknown metadata cannot cause network I/O.

The limit is a technical metadata profile boundary, not a worksheet row/deletion
limit. It does not alter WP-05's standalone behavior or impose a workbook-size
ceiling. A new use case exceeding it needs a reviewed profile change; do not
silently relax it or truncate data.

### One acquisition, one package, complete exit

The exact public signature is:

```python
def read_identified_xlsx_source(
    source_path: Path,
    *,
    snapshot_root: Path,
    observation_interval_seconds: float,
) -> IdentifiedXlsxSource: ...
```

Delegate source/root/stability policy to `open_stable_xlsx_snapshot`. Within its
lease, open exactly one `ZipFile` on the yielded `snapshot_path` in read mode.
Read the marker and Raw through that same ZIP object. Reuse the existing internal
`xlsx_source_reader._read_xlsx_from_zip` for Raw; do not duplicate its whitelist,
UUID, formula, location or hash rules. This deliberate internal adapter dependency
needs regression coverage and must not change the Reader's public API or code.

Do not reuse `_parse_relationships_file` for marker selection: that helper filters
external/invalid targets before returning its map. Marker discovery must retain
those candidates until cardinality and target checks have completed. The existing
Raw helper still applies its own full package/worksheet checks.

Read no marker or Raw from the original path after acquisition yields. Do not
accept a caller-supplied key, prior registry, existing lease descriptor, separate
Raw snapshot or claimed byte hash. Retain the acquisition's digest/count; never
hash a different path to pair it with the parsed result. No whole-workbook byte
buffer, extra snapshot copy or package extraction is allowed.

Close ZIP/member streams, then complete the WP-06 exit revalidation and cleanup
before returning any result. No callback, generator yield or downstream call can
publish a provisional success. Lease mutation/replacement or cleanup failure
must prevent return. This guarantee has the accepted controlled-lease trust
boundary; it is not cryptographic authentication against a hostile local process.

`IdentifiedXlsxSource(key, read_result, file_sha256, byte_count)` retains the exact
`SourceBindingKey` and `XlsxSourceReadResult` objects. Validate their types, an exact
64-lowercase-hex str hash, and an exact positive int count (never bool). Expose
`version` as the fixed version without a constructor argument. Hide `read_result`
from repr and retain no source/snapshot path or lease handle. Direct construction
validates data consistency only; the complete reader function supplies the I/O
lifecycle guarantee. Neither object construction nor a byte hash authenticates a
caller's manually assembled result or authorizes import.

### Errors and compatibility

`XlsxSourceIdentityReason` is a `StrEnum` with exactly `INVALID_INPUT="invalid_input"`,
`MISSING_MARKER="missing_marker"`, `AMBIGUOUS_MARKER="ambiguous_marker"`,
`INVALID_MARKER="invalid_marker"` and
`METADATA_LIMIT_EXCEEDED="metadata_limit_exceeded"`.
The error constructor takes only the exact enum as `reason` and retains it by
identity; strings and foreign enum members are invalid. Invalid reason arguments
and invalid result-constructor data raise `XlsxSourceIdentityError(INVALID_INPUT)`.
Its `reason_code` is a fixed mapping to
`XLSX_SOURCE_IDENTITY_` plus the uppercase enum name. str/repr/args use fixed
messages with no input text, paths, property values or prior data. Signature
misuse may raise TypeError. Unexpected internal failures and cancellation propagate.

Multiple marker relationships/name matches are AMBIGUOUS_MARKER. Missing root
custom relationship or no marker property is MISSING_MARKER. Invalid XML/profile,
namespace/target/content type, property ID duplication, wire version/type/value
or linked/cached marker is INVALID_MARKER. Limit overflow uses its specific reason.
Existing acquisition, ZIP/package and Raw errors keep their accepted taxonomy;
do not turn them into MISSING_MARKER or a successful unknown source. Expected new
ZIP corruption is an XlsxPackageError with the existing corrupt-ZIP reason.
Missing root relationships/content-types members and duplicate root relationship
IDs use the existing package reasons. Other new metadata-profile validation
failures use the identity reasons above. New ZIP I/O/close OSError failures use
a fixed XlsxSnapshotStorageError with the original cause.

Preserve simultaneous parsing and ZIP-close failures, then acquisition integrity/
cleanup failures, through ordered exception groups with original objects/causes.
Use BaseExceptionGroup if cancellation is present. Do not deduplicate independent
errors by shared cause, suppress cleanup failure, or return partial data. Public
typed messages are sanitized; retained diagnostic causes are not claimed to be
redacted tracebacks.

Standalone `read_xlsx_source_snapshot` remains compatible with markerless XLSX.
Missing/invalid markers fail only this new identified reader. WP-07/08 continue
their accepted behavior; wiring retry/lifecycle policy into runtime is later work.

## Evidence, rollout and remaining prerequisites

[WP-12](../work-packages/phase-01/WP-12-xlsx-source-identity.md) defines XI-01..16.
Use only generated synthetic XLSX fixtures. Prove that marker and Raw come from
the same leased package even if the live source changes, and that no failure can
escape as a result before cleanup. Independently compose the returned key with
WP-11 and its Raw with WP-04/09/10; neither composition is automatic in production.
Retain all 530 existing cases and the original native CI/symlink/benchmark gates.

The property is not secret or signed. Copying a workbook copies its logical key;
that does not enroll a new annual source. A later enrollment/write contract must
establish new keys with explicit operator intent. Marker reading does not prove
correct durable source membership, global UUID/home-sheet/revision continuity,
final-import commit, archived hash integrity, rollover or crash recovery.

No real workbook/copy is read or written by this work package. Marker persistence
through real Excel Save/SaveAs/OneDrive/COM remains an unexecuted evidence item,
requiring separately named Owner authority for real-data work. No new financial
rule, database/outbox schema, opening transfer or report behavior is introduced.

Implementation starts only after this planning PR merges. Require non-author
review and native Windows/Linux CI before acceptance. Verify fixed-SHA rollback
in an isolated clean checkout; do not roll back shared data. G1 remains open.
