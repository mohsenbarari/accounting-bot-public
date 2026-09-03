# ADR-0017: Preserve Raw row values in a versioned storage payload

- Status: Accepted technical design by Codex Project Manager; implementation evidence pending
- Date: 2026-09-03
- Phase / gate: Phase 1 / G1 remains OPEN / IN PROGRESS
- Decision: O-79 in [ROADMAP.md](../../ROADMAP.md)
- Work package: [WP-14](../work-packages/phase-01/WP-14-source-raw-codec.md)
- Contract version: `source-raw-codec.v1`

## Context and decision

Roadmap 4.3 and 5.2 require immutable historical Raw and an atomic future import
of Raw/revisions/membership/change_events. [ADR-0006](ADR-0006-canonical-source-hashing.md)
intentionally normalizes equivalent numbers and dates for change detection;
its hash preimage cannot reconstruct their original Raw representations.
[ADR-0007](ADR-0007-full-snapshot-change-plan.md) retains those representations
in `ValidatedSourceRow`, but defines no durable encoding. The persistence package
is still a scaffold. [ADR-0016](ADR-0016-source-identity-projection.md) defines
prior scope and identity continuity, not storage serialization.

Define one deterministic, lossless codec for a validated Raw row before designing
the SQLite transaction that will store it. It returns bytes or a validated row,
performs no I/O and does not create revisions, events, memberships or an import.
The payload is an internal Raw component, not the complete signed Sync envelope
or the definition of `payload_hash` in Roadmap 15.3.

Python's [Decimal documentation](https://docs.python.org/3.13/library/decimal.html#decimal.Decimal.as_tuple)
describes sign, coefficient digits and exponent, including signed zero and
significant trailing zeros. Preserve that tuple without arithmetic. Python's
[JSON documentation](https://docs.python.org/3.13/library/json.html#standard-compliance-and-interoperability)
documents defaults that accept non-finite numbers and repeated object names.
Use an explicitly restricted array grammar; do not rely on permissive defaults.

## Public API

Add `accounting_contracts.source_raw_codec` and exactly these five exports:

| Symbol | Contract |
|---|---|
| `SOURCE_RAW_CODEC_VERSION` | Exact string `source-raw-codec.v1`. |
| `SourceRawCodecReason` | StrEnum with the two exact reasons below. |
| `SourceRawCodecError` | ContractError with an exact reason enum and fixed message. |
| `encode_source_raw_row` | Encode one validated row as immutable bytes. |
| `decode_source_raw_row` | Validate bytes and reconstruct a new ValidatedSourceRow. |

```python
encode_source_raw_row(row: ValidatedSourceRow) -> bytes
decode_source_raw_row(payload: bytes) -> ValidatedSourceRow
```

No options, generic JSON helpers, dataclass serializer, public payload wrapper,
file-like input, workbook codec or streaming/session API is added. Existing
contracts and their public behavior remain unchanged.

The encoder accepts `ValidatedSourceRow` instances, including valid subclasses.
Its scalar domain is exactly None, built-in str, built-in int and finite Decimal.
Booleans, floats and scalar subclasses are not serialized or coerced. Scalar
subclasses may already pass an upstream validator; this new wire boundary rejects
them explicitly rather than promising to preserve arbitrary Python class state.
All scalars produced by the accepted XLSX reader are within this domain. Row/UUID
subclass identity and caller mapping insertion order are not part of Raw storage.

## Exact bytes and grammar

The top-level value is a seven-element JSON array, in this order:

```text
[codec_version,raw_contract_version,source_hash_version,sheet_name,canonical_uuid,source_hash,fields]
```

The first three strings are exactly `source-raw-codec.v1`,
`raw-source-contract.v1`, and `source-hash.v1`. The sheet is one of the four
authoritative Raw sheets. The row UUID is canonical lowercase UUIDv7 with the
RFC variant; the stored source hash is lowercase 64-character hex and must
match the existing WP-03 calculation for this sheet and Raw.

`fields` contains exactly `[field_name, typed_value]` pairs, once each and in
the authoritative WP-02 `raw_columns` order. No extra, missing, duplicate or
reordered field is accepted. The UUID stays outside Raw fields. Formula/cache,
location, computed totals and other non-whitelist fields never enter the format.

| Python scalar | Exact typed_value |
|---|---|
| None | `["null",null]` |
| str | `["text",value]`, preserving every Unicode code point |
| int | `["int",integer_text]` |
| Decimal | `["decimal",[sign_text,coefficient_text,exponent_text]]` |

`integer_text` and `exponent_text` use ASCII grammar `0` or `-?[1-9][0-9]*`.
There is no plus sign, padding, whitespace, negative zero or exponent notation
in these integer strings. `sign_text` is exactly `"0"` or `"1"`.
`coefficient_text` is the exact `as_tuple().digits` joined as ASCII digits: `0`
or a nonzero digit followed by zero or more digits. Leading zeros are forbidden;
trailing zeros are retained. Decode through Decimal's tuple constructor, not
float, `normalize`, `quantize`, arithmetic or a context-rounded constructor.
The reconstructed tuple must equal the three encoded components.

For example, Decimal `1.00`, Decimal `-0.00` and Decimal `1E+3` encode respectively
as `["decimal",["0","100","-2"]]`, `["decimal",["1","0","-2"]]` and
`["decimal",["0","1","3"]]`. Built-in integer `1`, string `"01"` and Decimal
`1.0` remain three different Raw representations. The integer-toman field type
may contain an integral Decimal, whose tuple is preserved too. Field-level
admissibility remains governed by WP-02/03, not only by the scalar tag.

Serialize with Python JSON `ensure_ascii=False`, `separators=(",", ":")`,
`allow_nan=False`, then strict UTF-8. No BOM, optional whitespace, final newline
or Unicode normalization is allowed. Only arrays, strings and null appear;
JSON objects, numbers, booleans, NaN and infinities are invalid anywhere.
Reject numeric tokens during parsing rather than converting through float.
Reject an invalid UTF-8 sequence, lone surrogate, trailing content, unknown
version/tag or any wrong shape/type. Reject alternative encodings of the same
tree (extra spaces, escaped non-ASCII, escaped slash, etc.): valid input bytes
must equal the canonical re-encoding of the reconstructed row exactly.
This defines a format check, not a new source-change comparison.

The decoder accepts exactly bytes; text, bytearray, memoryview and bytes subclasses
fail as INVALID_INPUT. Empty or malformed bytes fail as INVALID_PAYLOAD. No
version guessing, migration, replacement decoding or partial result is permitted.
Numeric conversion and JSON parsing must not change process-wide interpreter
limits or decimal settings. Resource exhaustion is not solved by this format;
storage/transport size and concurrency controls belong to their later adapters.

## Validation, preservation and errors

Build the decoded result through the existing validated row constructor so that
UUID, Raw field types and recomputed source hash are checked. Reuse the accepted
registry/hashing implementation; do not copy or alter its validation rules.
The output has immutable Raw in registry order, equal UUID/sheet/hash and exact
base scalar types. Text is code-point-identical; int has equal value; Decimal
has identical `as_tuple()` including sign, coefficient zeros and exponent.
Ordinary numeric equality alone is insufficient evidence of losslessness.

Encoding preserves its input and decoding allocates a new valid row; neither
operation mutates the current decimal context, including flags, on success or
failure. Inert module initialization and calls perform no file/network/database,
clock, random/UUID allocation, threading or automatic evaluator/Planner activity.
Dependency loading may be outside a scoped import guard, disclosed honestly.
Valid nested contract invariants are trusted; deliberate mutation using
`object.__setattr__` is not an authenticity boundary.

| Reason enum | Value | Exact public message |
|---|---|---|
| `INVALID_INPUT` | `invalid_input` | `Invalid Raw codec input.` |
| `INVALID_PAYLOAD` | `invalid_payload` | `Invalid Raw row payload.` |

Invalid encoder input/scalars and wrong decoder argument types use INVALID_INPUT.
Malformed/unsupported bytes and decoded contract/hash failures use INVALID_PAYLOAD.
An ordinary parsing, encoding or contract exception is wrapped with the respective
reason and exact exception as cause; do not accidentally wrap an already classified
codec error again. Cancellation BaseExceptions propagate with their original
identity. Error construction accepts only the actual reason enum; strings and
foreign StrEnums raise fixed TypeError `Invalid Raw codec reason.`.
Typed public str/repr/args contain no row values, UUID, hash, path or supplied
object representation. Preserved causes can contain diagnostic data; this does
not promise safe publication of arbitrary tracebacks or encoded bytes.

## Identity, revisions and trust boundaries

Representation changes can produce different codec bytes with the same source
hash. WP-04 must still yield UNCHANGED for those values. A future import must not
use codec-byte inequality to create EDIT/revision/outbox entries or overwrite
immutable prior Raw. The last committed revision's Raw remains its original Raw;
new observations do not retroactively rewrite it. This decision does not require
persisting every no-op observation. New-source membership of an unchanged shared
party remains a separate atomic obligation from ADR-0016.

The embedded source hash detects disagreement with the accepted semantic hash;
it does not authenticate the row or detect a change to a canonically equivalent
Raw spelling, a different valid UUID, or a coherently replaced payload/hash.
Byte-integrity, source membership, revision, lifecycle, previous-version linkage,
device/import/event IDs and durable history must be supplied and checked by later
storage/Sync contracts. A decoded row is not a complete workbook, an active source,
a financially accepted snapshot, evidence of a commit, or permission to import.
No source key/year, revision, event envelope or `payload_hash` definition is added.

The next storage design must consume this Raw format with one consistent catalog
generation, stale-state rejection, immutable revision Raw, membership even after
UNCHANGED and atomic change_events/outbox. It also needs explicit enrollment,
validation eligibility and final import/archive/activation/crash rules. This
codec proves none of those operations or real Excel marker retention.

## Cost, alternatives and rollout

Work row by row. Additional memory is proportional to one row's encoded/decoded
size and its existing canonical hash computation, not the number of workbook
rows. Account for expanded canonical numeric representations in cost claims;
the codec does not remove WP-03's hashing costs or require a whole-workbook byte
buffer. No new row/value limit or arbitrary wall-clock threshold is introduced.
Use a synthetic 15,000-row replay with independent complete comparisons and
separate fixture/encode/decode/Planner timings and peak RSS.

- Reusing canonical hash bytes loses original numeric/date representations.
- Plain untyped JSON loses scalar types; decimal-to-float conversion loses data.
- Pickle or class-name dispatch introduces executable/class-dependent payloads.
- Encoding a complete workbook or full Sync event here would couple this small
  prerequisite to snapshot storage, event sequencing and transport decisions.
- Hiding an unversioned codec inside a first SQLite schema makes migration and
  replay requirements implicit. Establish this format before persisting it.

No schema, dependency or operational path changes. Before stored consumers exist,
rollback removes only this adapter/exports/docs through reviewed fixed-SHA reverts
on an isolated branch. After durable use, changing the grammar requires a new
version and explicit read/migration/replay support; never reinterpret old v1 bytes.

WP-14 defines RC-01 through RC-14 and preserves all 783 baseline collected tests,
all previous benchmark limits and native Windows/Linux CI. Implementation starts
only after this planning PR merges from its actual clean main SHA. Non-author
review and successful native CI precede PM evidence acceptance. Design acceptance
does not accept implementation evidence or close G1.
