# ADR-0014: Annual source binding and prior-state selection

- Status: Accepted
- Date: 2026-09-03
- Work Package: Phase 1 — WP-11
- Decision owner: Codex Project Manager under O-46/O-49

## Context

[ADR-0013](ADR-0013-source-fiscal-evidence.md) observes years in raw transaction
dates. It cannot identify an annual source after all dated rows are deleted, or
distinguish an archive copied over the operational path. Roadmap 5.3 permits the
same filename for successive annual workbooks. Choosing a maximum/majority date,
current machine year or filename would invent authority the observations lack.

[ADR-0007](ADR-0007-full-snapshot-change-plan.md) compares a complete snapshot
against the prior registry supplied by its caller. Supplying the previous year's
registry for a new annual source can turn unrelated absences into false voids.
There is currently no explicit contract for selecting a source's prior registry.

The next prerequisite is a pure binding registry and resolver. Its output is a
routing result over supplied metadata, not proof of physical workbook identity,
successful final import, durable activation or permission to commit a plan.

## Constraints and alternatives

- O-07: final archives are immutable and never enter ordinary operational import.
- O-06: an empty operational workbook remains valid; there is no minimum row count,
  deletion threshold, confirmation or quarantine based on change volume.
- O-03/O-69: stable row/party UUIDs and permanent home sheets retain their meaning.
- O-75: raw date observations cannot automatically select, change or reject a
  binding. A transaction date can differ from its annual source's declared year.
- Only one annual workbook is operational at a time. Paths and byte hashes are
  observations, not permanent source identifiers.

| Option | Assessment |
|---|---|
| Derive a source key from filename, date majority, UUID timestamp or file hash | Rejected: fails empty/mixed snapshots or changes identity on ordinary saves. |
| Explicit logical source key plus a registered lifecycle and scoped prior state | Selected: supports empty files and deterministic routing without altering the accepted Planner. Requires later physical and durable integration. |
| Implement marker I/O, enrollment, rollover and SQLite immediately | Deferred: needs same-acquisition marker/snapshot provenance, durable final-import evidence, identity projections and crash recovery. A pure resolver cannot establish those facts. |

## Decision: source-binding.v1

Add a pure module at
`packages/contracts/src/accounting_contracts/source_binding.py`, with these nine
public exports and no dependencies beyond the existing contracts/standard library:

| Symbol | Contract |
|---|---|
| `SOURCE_BINDING_VERSION` | Exact string `source-binding.v1` |
| `SourceBindingInputError` | `ContractError` subclass with fixed public messages |
| `SourceBindingKey` | Frozen/slotted `source_id: UUID`, `fiscal_year: int` |
| `SourceBindingState` | Exact enum members `ACTIVE="active"`, `ARCHIVED="archived"` |
| `SourceBindingRecord` | Frozen/slotted `key`, `state`, `prior_registry`, `final_file_sha256` as specified below |
| `SourceBindingRegistry` | Frozen/slotted registry whose only constructor input is an iterable of records named `records` |
| `SourceBindingDisposition` | Exact enum members `ACTIVE="active"`, `ARCHIVED="archived"`, `UNREGISTERED="unregistered"` |
| `SourceBindingResolution` | Frozen/slotted computed result; constructor inputs only `key`, `registry` |
| `resolve_source_binding(key, registry)` | Same result contract as direct resolution construction |

The table is the exact export list; do not add an approval flag or transition
command.

### Logical key and authority boundary

`source_id` is a UUIDv7 value, distinct in purpose from row UUIDs. Accept a UUID
object only, validate its RFC variant/version using the existing rules, and retain
it by identity. Do not generate IDs or accept/coerce UUID text at this API boundary.
`fiscal_year` is an exact int, not bool/text, validated by the accepted Jalali
parser on its zero-padded first day, as in ADR-0013. Do not impose the current year,
a 1405 lower bound, or derive a year from UUID creation time.

The key is the logical content of a future approved internal workbook marker:
one permanent annual source ID and its declared annual year. Ordinary edits,
sorts, path changes and replacement saves retain the key. A newly enrolled annual
source receives a new ID; an archived ID is never reassigned or reactivated.
The declared year scopes the source and is distinct from each row's date year.

This ADR chooses those logical semantics only. No XLSX property/cell/name, byte
serialization or marker reader/writer is introduced. A caller constructs the key
from trusted test metadata here. This constructor does not attest that a key was
read from a file, paired with a particular snapshot, or authorized by an operator.
The resolver deliberately takes no snapshot, path or fiscal-evidence report.

Before operational use, a later adapter contract must read marker and raw sheets
from the same WP-06 stable acquisition, validate both, and retain their association
through commit. Missing/ambiguous marker handling and initial enrollment must be
specified there, without selecting a year from empty/mixed dates. Actual changes
to a real workbook or its copy require separate target-specific Owner authority.

### Registry records and invariants

Each record contains:

- `key: SourceBindingKey`;
- `state: SourceBindingState`, checked as the exact enum type, never a string or
  an unrelated StrEnum with an equal value;
- `prior_registry: PriorIdentityRegistry`, the existing immutable WP-04 type,
  retained by identity and excluded from repr;
- `final_file_sha256: str | None`, required as an explicit constructor argument.

ACTIVE requires `final_file_sha256 is None`. ARCHIVED requires an exact string of
64 lowercase hexadecimal characters, recording the final imported file's byte
hash. An archived empty prior registry is valid; empty final imports are possible.
This hash is metadata for later integrity/backup checks. The resolver does not
verify file bytes or confuse this hash with row/sheet hashes.

The registry consumes its iterable once, validates every record, defensively
copies it to a tuple sorted by `(fiscal_year, source_id.bytes)`, and exposes that
tuple as `records`. A read-only `active_record` is computed, or None. It rejects
duplicate source IDs, duplicate annual years, multiple ACTIVE records and invalid
element/container types. A registry can be empty or contain only archives.
No mutable backing index may escape. Construction cannot accept injected indexes,
an active record, precomputed counts or a claimed-valid flag.

These are trusted representations of repository facts, analogous to the accepted
WP-04 prior registry. Constructor validation establishes internal consistency,
not proof that an import committed. It cannot detect a caller deliberately
mislabeling prior state with another source key. Persistence must supply the
correct association and enforce it atomically; this remains a required dependency.

### Source scope and permanent identities

The selected prior registry must describe the last committed source membership of
the selected annual key, including its voided identities. Never concatenate annual
registries, partition rows by their transaction date, or borrow another source's
registry just because its year or path looks plausible. An empty current snapshot
is compared against its own complete prior membership without a deletion limit.

An annual source ID does not replace `record_id` or `party_id`. A party can appear
in successive annual workbooks with the same permanent party UUID. Therefore the
binding registry does not impose global disjointness on its nested UUID sets or
rewrite them. It treats each prior registry as an already prepared historical
view. It does not initialize a new year's prior rows, restart permanent identity
revisions, create a second party, or decide cross-source transaction reuse.

Before an operational Planner call is integrated, a later persistence/identity
contract must define the scoped membership projection alongside global UUID/home
sheet and revision continuity, especially for shared party IDs. Selecting the
right source is necessary but does not by itself solve that projection. WP-11
must not reinterpret WP-04 revision or lifecycle semantics to fill this gap.

### Resolution algorithm and derived fields

Both public entry points validate key/registry types and compute the result from
those two inputs. The result exposes `key`, `registry`, `disposition`, `record`
and `prior_registry`; registry/prior fields are excluded from repr. No derived
field is a caller constructor argument.

| Registered source ID | Year match | State | Disposition | `record` | Selected `prior_registry` |
|---|---|---|---|---|---|
| Absent | Any | Any | UNREGISTERED | None | None |
| Present | No | Any | Fixed `SourceBindingInputError` | No result | No result |
| Present | Yes | ACTIVE | ACTIVE | Exact registered object | Exact record's registry |
| Present | Yes | ARCHIVED | ARCHIVED | Exact registered object | None |

An unknown ID with a year equal to a registered year is still UNREGISTERED; there
is no fallback by year. ARCHIVED is an explicit ignored routing result, not an
empty successful change plan. Its stored record remains available for metadata
inspection, but no operational prior registry is selected. Consumers must branch
on disposition; the resolver never calls the Planner, Reader, watcher or WP-09/10.

UNREGISTERED does not create a binding, return an empty prior as a first import,
guess an active year or switch the active source. It is an enrollment/configuration
fact, not rejection of row content. ACTIVE means only that this key matches the
registry's active source. There is no `commit_allowed`, `passes_fiscal` or financial
validity claim, and no mutation of lifecycle or successful-import baselines.

### Lifecycle rules for later durable integration

Initial enrollment must associate a key, its source and a correct prior projection
before ordinary import. At annual rollover, final successful import of the old
source, preservation of its final byte hash and frozen source state, archival and
activation of the new registered source must use a durable atomic protocol with
crash/retry evidence. Neither a date crossing Nowruz nor a save/rename is that
protocol. No method in WP-11 claims to perform it.

One ACTIVE record is a registry-wide maximum, not a command to archive other
records. No consecutive-year requirement, skipped-year policy, administrative
reopening or automatic historical migration is introduced. Integrity changes to
archives cannot update the ledger. Their future alert path remains O-07's duty.

### Purity, errors and cost

Keep public objects frozen/slotted and nested collections immutable. Exclude
nested prior state from ordinary repr. Invalid input produces fixed typed messages
without raw data, supplied strings or paths. Signature misuse may be TypeError.
Do not catch cancellation or unexpected internal errors and return UNREGISTERED.
Ordinary iteration/internal exceptions propagate rather than imply an empty registry.

Evaluation and construction perform no filesystem/network/clock/random/UUID or
thread operations. Module import must have no application I/O side effects.
Registry construction is O(s log s) time/O(s) metadata for s annual records; use
an internal source-ID index for O(1) resolution. Do not traverse or copy nested
prior rows, recompute hashes or collect new fiscal evidence.

## Evidence, rollout and consequences

[WP-11](../work-packages/phase-01/WP-11-source-binding.md) defines SB-01..SB-14.
Independent tests must prove exact dispatch, unchanged nested identities, archive
exclusion, no fallback and safe explicit composition with WP-04 using synthetic,
correctly prepared per-source registries. Preserve all 459 baseline tests and
existing native CI/benchmark gates. No persistent schema or data migration occurs.

The cost is an explicit metadata boundary and deferred integration. Physical
marker provenance, enrollment, global identity projection, atomic final import/
rollover, archive integrity alerts and operational G1 acceptance remain unproved.
No caller should treat the library as an operational importer until those gates
are satisfied. Opening-transfer rules and financial validation are unchanged.

Implementation may begin after the planning PR merges. It requires non-author
review and native Windows/Linux CI before acceptance. Revert fixed reviewed
commits only in a clean isolated checkout to verify rollback; never remove real
data. G1 remains OPEN / IN PROGRESS.
