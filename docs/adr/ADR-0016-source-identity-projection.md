# ADR-0016: Project source membership against permanent identity history

- Status: Accepted technical design by Codex Project Manager; implementation evidence pending
- Date: 2026-09-03
- Phase / gate: Phase 1 / G1 remains OPEN / IN PROGRESS
- Decision: O-78 in [ROADMAP.md](../../ROADMAP.md)
- Work package: [WP-13](../work-packages/phase-01/WP-13-source-identity-projection.md)
- Contract version: `source-identity-projection.v1`

## Context

[ADR-0007](ADR-0007-full-snapshot-change-plan.md) compares a complete current Raw
snapshot with a prior registry. Unknown UUIDs become INSERT revision 1; absent
active prior UUIDs become VOID. Its caller must supply the correct prior scope.
[ADR-0014](ADR-0014-source-binding.md) selects the last committed membership of
one registered active source, including tombstones. It intentionally leaves
global home-sheet/revision continuity and cross-source transaction ownership
for a later contract. [ADR-0015](ADR-0015-xlsx-source-identity.md) associates the
source key and Raw with the same leased file; it does not construct prior state.

Two shortcuts are unsafe. Concatenating annual registries makes an empty new
year void identities belonging only to archives. Using an empty new-year prior
alone makes a previously known party appear new at revision 1. Reusing an
archived transaction's UUID in a new source also risks reopening or duplicating
the same financial event, contrary to Roadmap 4.3, 5.3 and O-58.

This decision supplies a pure, indexed projection over trusted committed
membership records. It does not implement storage or claim that supplied
records are durable, complete or authentic merely because they validate.

## Decision and identity rules

1. A row UUID has one permanent home sheet across all registered sources,
   including voided identities. Source UUIDs remain a separate kind of identity.
2. A transaction UUID (any of the three transaction sheets) belongs to exactly
   one source for its lifetime. Even an identical or voided transaction from an
   archive cannot be reused in another source. Reject the conflict; never mint
   a replacement UUID, borrow its prior or choose by transaction date. This is
   the cross-source ownership rule deferred by ADR-0014, not an edit/deletion
   threshold or a new financial calculation rule.
3. A `party_id` in «لیست کسبه» may be a member of several annual sources. Its
   latest revision continues globally. Historical source memberships retain
   their original immutable revision/hash/lifecycle views.
4. Membership means that a UUID has been committed to that source at least once;
   it remains recorded after VOID. It is not just the set of currently visible
   rows. An identity's greatest recorded revision is its global head, provided
   every represented UUID/revision pair has one consistent state. An active
   source's existing members must already be at their global heads; a stale
   active view is inconsistent input.
5. Only previously committed members of the selected active source can be
   considered missing and hence become VOID. A known party appearing in its
   first snapshot for a new source can borrow its global prior for comparison.
   An absent nonmember is never borrowed. Unknown present UUIDs stay absent
   from the projected prior and follow the existing INSERT revision 1 rule.

## Public API

Add `accounting_contracts.source_identity_projection` and these five exports
through `accounting_contracts.__init__`:

| Symbol | Contract |
|---|---|
| `SOURCE_IDENTITY_PROJECTION_VERSION` | Exact string `source-identity-projection.v1`. |
| `SourceIdentityProjectionReason` | StrEnum with the five exact reasons below. |
| `SourceIdentityProjectionError` | ContractError with an exact reason enum and fixed safe message. |
| `SourceIdentityCatalog` | Frozen/slotted validated catalog constructed from one SourceBindingRegistry. |
| `project_source_prior` | Pure function returning a new WP-04 PriorIdentityRegistry for an exact active source key and complete current snapshot. |

Exact callable signatures:

```python
SourceIdentityCatalog(source_registry: SourceBindingRegistry)

project_source_prior(
    key: SourceBindingKey,
    snapshot: ValidatedSourceWorkbookSnapshot,
    catalog: SourceIdentityCatalog,
) -> PriorIdentityRegistry
```

No new dependency, public builder, result wrapper, approval flag, manual index,
caller-supplied head/revision override or state-mutating method is introduced.
Existing nested contracts are reused without changing their signatures,
validation, equality, representation or resolver/Planner behavior.

### Catalog construction

The only constructor input is `source_registry`, retained by object identity.
Public data comprise `source_registry` (excluded from repr), fixed `version`
and derived `identity_count`. Version/count and internal indexes are not init
parameters. Keep immutable private global-head and transaction-owner indexes;
do not expose a global prior registry that looks ready to feed to the Planner.

Accept an existing `SourceBindingRegistry`, including empty or archive-only
registries. Its records already enforce unique source IDs/years and at most one
active source. During construction, validate and derive:

- A single home sheet per row UUID across every membership.
- For transaction UUIDs, a single owning `SourceBindingKey`, including tombstones.
- The greatest `latest_revision` per UUID; all states tied at any represented
  revision must agree on home sheet, lifecycle and source hash, even when a
  greater head revision is also present. Compare these fields, not Python object
  identity. Equal-but-distinct immutable objects are
  valid; a greater revision may have the same hash after a revert or reactivation.
- Every state in the active record's prior must agree with its global head on
  revision and those fields. Reject an older active state rather than silently
  upgrading committed membership. Lower historical archive revisions stay valid.

Do not select heads by fiscal year, UUID timestamp, input order or hash value.
For semantically equal maximum-revision heads, retain the object encountered
first in the registry's canonical record order as the deterministic index value.
The selected active record's own prior objects are retained when projecting its
members, including when another equivalent head object exists in an archive.

Do not synthesize missing intermediate revisions. Full revision-chain validity,
Raw/hash provenance, completeness of the registered source set, and truthful
association of each prior with its source remain trusted repository obligations.
A single metadata snapshot cannot prove these historical facts. Using a registry
that omitted an archive could hide an ownership conflict; no `is_complete` flag
or successful catalog construction establishes completeness.

Copy/freeze derived mappings defensively; their entries refer to existing
immutable states/keys. No mutation of a caller's binding/prior objects occurs.
Do not traverse Raw values, recompute hashes or perform I/O during construction.

### Projection algorithm

1. Validate the three input types and call the existing exact-key binding
   resolver on `catalog.source_registry`. Only ACTIVE continues. ARCHIVED and
   UNREGISTERED fail explicitly; they never return an empty operational prior.
   A known source ID with a wrong declared year remains an input error, never a
   fallback by year, filename, observed dates or another source's active record.
2. Start with every state from that active record's prior, including VOIDED.
3. Visit current rows in the existing canonical sheet order and UUID-byte order.
   Look up each UUID in the catalog's private global index:
   - Unknown UUID: do not add a prior entry.
   - Known UUID with a different home sheet: fail IDENTITY_RELOCATION first.
   - Known transaction owned by another source: fail TRANSACTION_SOURCE_CONFLICT,
     even if hash/lifecycle matches, it is voided, or its source is archived.
   - Known party already in the selected membership: keep its selected prior.
   - Known party absent from selected membership but present in the current
     snapshot: add its global head, including a VOIDED head.
4. Return a new `PriorIdentityRegistry` with keys inserted in UUID-byte order and
   retained immutable state objects. No snapshot, membership, archive or catalog
   is changed. Any failure returns no partial registry.

The caller may then explicitly run `plan_source_changes(snapshot, projected)`.
The projection itself must not call the Planner, requiredness/fiscal evaluators,
reader, source resolver alternatives, clock, UUID generation, persistence or
runtime. Calling WP-11's exact resolver in step 1 is intentional.

### Required results with the unchanged WP-04 Planner

| Situation | Projected prior / Planner result |
|---|---|
| First-ever present UUID | No prior; INSERT revision 1. |
| Existing active-source member, unchanged / changed | Same prior; UNCHANGED / EDIT at prior revision + 1. |
| Existing active-source member disappears | Active prior becomes VOID at +1; already voided prior yields no item. |
| New-year present party, global ACTIVE revision 7, same hash | Borrow revision 7; UNCHANGED, no fabricated revision. Membership still needs recording on later commit. |
| Same party with changed hash | Borrow revision 7; EDIT revision 8. |
| New-year present party, global VOIDED revision 8 | Borrow tombstone; EDIT/reactivation revision 9. |
| Known party or transaction belongs only to an archive and is absent now | Excluded; no false VOID and no plan item. |
| Archived transaction appears under the active source | Typed ownership error before any Planner call; no second INSERT. |
| UUID moves between any of the four sheets | Typed relocation error, not VOID plus INSERT. |
| Empty complete active snapshot | Only active-source members are compared for VOID; archive-only members remain untouched. |

A party-list VOID denotes the existing Raw revision transition. It does not
delete the permanent person, historical transactions, access memberships or
balances. This component neither calculates nor commits financial effects.

### Errors, representation and purity

Exact reason members and string values:

| Reason | Value | Use |
|---|---|---|
| `INVALID_INPUT` | `invalid_input` | Invalid root arguments or known source ID with contradictory year. |
| `INCONSISTENT_CATALOG` | `inconsistent_catalog` | Conflicting homes/transaction owners/head states or stale active membership. |
| `SOURCE_NOT_ACTIVE` | `source_not_active` | Exact requested source is archived or unregistered. |
| `IDENTITY_RELOCATION` | `identity_relocation` | Current row's known global home differs from its current sheet. |
| `TRANSACTION_SOURCE_CONFLICT` | `transaction_source_conflict` | Current transaction UUID has a different owning source. |

`SourceIdentityProjectionError(reason)` accepts only the actual enum type;
strings and foreign StrEnums fail with a fixed TypeError. Public typed
str/repr/args and the catalog repr do not include UUIDs, source years, hashes,
paths, raw cell values or supplied invalid objects. Diagnostics may preserve
causes: wrap an existing resolver `SourceBindingInputError` as INVALID_INPUT
with its exact cause. This guarantee does not claim that an arbitrary chained
exception's own representation is sanitized. KeyboardInterrupt/SystemExit and
other cancellation BaseExceptions propagate unchanged; no blanket catch.

All successful outputs are deterministic and immutable for valid nested
contract instances. Deliberate bypass of frozen-model invariants through
`object.__setattr__` is not an authentication/security boundary. Construction,
evaluation and inert module initialization perform no file/network/database
access, time access, UUID generation, mutation of source artifacts or threading.
Tests must describe dependency loading outside an import guard honestly.

## Cost and integration boundary

Let S be registered sources, M total membership entries across their priors,
U distinct global UUIDs, A entries in the selected active prior, N current rows,
and K output entries. Catalog construction is O(S + M + U log U) at most, with
O(S + M) peak validation metadata and O(S + U) retained index metadata. The
temporary UUID/revision consistency index is discarded after validation;
historical priors are retained rather than copied.
Projection is O(A + N log N + K log K) at most, O(A + N) additional metadata.
It must not rescan or copy archive membership after catalog construction.
Account for any sorting/canonical iteration in measured claims. There is no new
row limit, financial-volume cap or arbitrary wall-clock acceptance threshold.

This is an additional pre-Planner adapter. WP-11 still returns exactly the
selected stored membership; it does not begin returning this expanded prior.
WP-04's transition table and all WP-05..12 runtime/reader contracts stay intact.
No existing operational path is silently rewired.

A future durable import contract must atomically read one complete committed
registry/catalog generation, bind the identified snapshot to it, validate it,
plan it, and commit all Raw/revision/membership/outbox updates with a stale-state
check. In particular, a borrowed party yielding UNCHANGED still becomes a member
of the new source on successful commit; skipping that membership write would
make its later deletion invisible. The new active prior must then reflect every
committed current member plus prior tombstones at the resulting revisions.
Archive views and their final-file hashes remain immutable. An unsuccessful
import must advance none of these states; a proposed projection/plan advances
nothing. WP-13 implements none of these writes or commit-success assertions.

Durable enrollment, append-only revision history, atomic final import/archive/
new-source activation, crash recovery, marker retention in real Excel and
protected source UUID writing remain separate G1 dependencies. No real
workbook/copy is read or written by this work package.

## Alternatives and rationale

- Concatenate every prior: rejected because missing archive-only identities
  become false deletions and duplicate party UUIDs lose their historical views.
- Use only selected membership: necessary for deletion scope but insufficient
  for first membership of a permanent party in a later annual source.
- Allocate fresh annual party/transaction identities: rejected because a party
  and a financial event are permanent identities, not annual row coordinates.
- Silently borrow archived transactions: rejected because archived data cannot
  be edited by the active source and the same event must not be imported twice.
- Introduce SQLite/rollover in this package: deferred; the membership semantics
  need an independent oracle before transactional storage can enforce them.

This additive API introduces no schema or migration. Before operational use,
rollback can remove its own code/exports/docs through fixed-SHA reviewed reverts
on an isolated branch. After a storage consumer records membership under these
rules, changing ownership or revision semantics requires another ADR, migration
and replay evidence; silently resetting a catalog would lose conflict detection.
The three-document planning change itself makes no data or product change.

## Evidence and rollout

WP-13 specifies IP-01 through IP-16, including independent cross-year transition
oracles, mutation checks for false VOID/revision reset/foreign ownership, exact
error and constructor behavior, no-I/O controls, indexed access evidence and
15,000-row projection. Preserve all 678 existing collected test cases and their
files, the 15-second / 128-MiB Reader/Acquisition and WP-12 combined boundaries,
and native Windows/Linux CI requirements. A local privilege skip is not proof
of the four mandatory Windows CI symlink cases.

Implementation may start only after this planning PR merges, from the actual
clean main SHA containing this ADR and WP-13. The implementer produces the
bounded handoff and stops for non-author review. Evidence acceptance is separate
from this technical design decision; G1 remains OPEN / IN PROGRESS.
