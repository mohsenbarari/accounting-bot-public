# WP-11: Annual source binding and prior-state selection

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 source-routing prerequisite; cannot close G1
- Status: Issued — implementation may start after the planning PR merges
- Issued by: Codex Project Manager
- Issued on: 2026-09-03
- Implementation workflow: `accounting-bot-implementer`; non-author review before acceptance
- Implementation branch: `codex/phase-01-source-binding`
- Planning baseline: `d06b970eba271d66a04673b683d78119a9fc070a`
- Execution baseline: latest clean `origin/main` containing this WP and ADR-0014;
  record the actual SHA before coding
- Handoff path: `handoffs/phase-01/wp-11-source-binding/`

## Objective and traceability

Implement `source-binding.v1` from
[ADR-0014](../../adr/ADR-0014-source-binding.md). Model explicit annual source
keys, registered active/archive records and deterministic selection of the exact
active source's prior registry. An archive or unknown source selects no operational
prior state. A same-year unknown source never borrows another source's registry.

Roadmap 5.3/O-07 requires identity independent of filename and archive exclusion;
O-06 preserves valid deletion, including all rows. O-69 supplies the immutable
prior registry and Planner; O-75 supplies observations without source authority.
O-76 records this bounded technical contract. G1 remains OPEN / IN PROGRESS.

WP-11 models supplied, trusted metadata. It neither authenticates a file nor
associates that metadata with actual XLSX bytes. No snapshot, path, date-evidence
report or caller approval Boolean is passed to the resolver. All evidence uses
synthetic keys, registries and snapshots; no real workbook or real-data copy may
be accessed.

## Implementation boundary

Read ADR-0014 in full. Its nine exports, constructor inputs, exact enum/type
validation, ordering, lifecycle/hash combinations, routing table and trust
boundaries are normative. Keep WP-03..10 public APIs and behavior unchanged.

The only new implementation module is `source_binding.py` in accounting-contracts.
Use the accepted UUIDv7 rules and Jalali parser; add no dependency. Use an immutable
tuple and private source-ID index for registry records; retain nested prior
registries by identity rather than scanning, cloning or merging their rows.

Direct `SourceBindingResolution(key, registry)` and the function must derive all
result fields and be equivalent. ACTIVE selects the exact matching prior object;
ARCHIVED and UNREGISTERED select None. The function must not call the Planner or
update any source's state. Archive disposition is not an empty successful plan.

The key's declared annual year can differ from raw transaction years; neither
empty/mixed dates nor deletion volume affects resolution. The registry accepts
zero or one ACTIVE record, one record per source ID and annual year, and immutable
archived records with a final byte-hash string. It does not perform enrollment,
final import, rollover or integrity verification.

Shared permanent party IDs can exist in more than one annual prior view. Do not
rewrite IDs, impose cross-record UUID disjointness, reset global revisions, invent
source-local revision semantics, or seed a new year's identities. Operational
membership projection and global identity/revision continuity need a later
persistence contract. Tests compose only deliberately prepared prior views and
must not claim that this resolves the deferred projection or commit problem.

## Acceptance matrix

Map every row to explicit test node IDs and raw output. Expected routing, object
selection and change actions must be declared independently of production helpers.

| ID | Required evidence |
|---|---|
| SB-01 | Exact version, nine exports, public signatures and inert fresh target-module execution. State the import guard's scope; a controlled side-effect injection must fail for that guard-specific reason. Do not break package/submodule identity to pass the test. |
| SB-02 | Key validation covers UUIDv7 objects retained by identity; non-v7/variant-invalid UUID, text, bool and unrelated types fail. Year validation covers independently declared accepted boundaries and rejects bool/text/invalid years without a current-year or 1405 floor. |
| SB-03 | Record constructors validate the exact state enum, key and prior types; reject raw strings/equal foreign enums. ACTIVE requires None final hash; ARCHIVED requires exact lowercase 64-hex text. Empty archived prior is allowed. Invalid hash length/case/characters/types fail with fixed errors. |
| SB-04 | Registry rejects duplicate source IDs (same/different years), duplicate annual years, multiple ACTIVE records and invalid containers/elements. Empty and archive-only registries work. Records sort by declared year/UUID bytes; mutable input collection changes cannot affect the result. A one-shot iterable is consumed once. |
| SB-05 | For each public entry point, a matching ACTIVE key returns ACTIVE, the exact registered record and its exact prior registry. Use distinct keys/years with overlapping synthetic raw contents so content coincidence cannot hide incorrect selection. |
| SB-06 | Matching ARCHIVED keys return ARCHIVED and no selected prior even when another source is active. The immutable archive record/final hash remain available unchanged. Spy guards prove no Planner, Reader, watcher or persistence call. The result is not an empty change plan or permission flag. |
| SB-07 | Unknown ID yields UNREGISTERED/None/None for an empty registry, a populated registry and an ID claiming an existing annual year. A known ID with a mismatched year raises the typed error for ACTIVE and ARCHIVED. No year fallback, first-import default or implicit enrollment. |
| SB-08 | Direct result construction derives all fields. Injected disposition/record/prior/index/active-record arguments are rejected; public frozen/slot and nested immutability hold. Synthetic sensitive marker strings in invalid inputs or nested prior data are absent from repr and public error messages. |
| SB-09 | Snapshot/date independence is explicit: empty four-sheet, party-only, all-undated and mixed-year snapshots produce their ordinary WP-09/10 results alongside unchanged binding resolution. Change raw years/UUID timestamps/notes independently without altering a logical source key. No minimum row count, automatic source transition or fiscal rejection is inferred. |
| SB-10 | Property oracle covers record permutations and arbitrary valid active/archive layouts with unique IDs/years. Independently assert canonical order, all dispositions, exact object selection and preservation of every unrelated record. Inject unknown IDs and wrong declared years; changing one record affects only the expected routing facts. |
| SB-11 | After fixture creation, guard filesystem/network/clock/random/UUID/thread seams during construction/resolution. Include a guard-specific negative control. Repeated calls preserve all input records, nested prior rows, UUIDs and hashes. Cancellation and deliberate iterator/internal exceptions propagate; never manufacture an empty registry or UNREGISTERED success. |
| SB-12 | Prepare distinct synthetic A (archive) and B (active) prior views, then explicitly compose only B's selected prior with WP-04. Same snapshot gives zero changes; one edit and one deletion affect B only; an empty complete B snapshot voids exactly B's active membership. Archived A and unknown C yield no selected prior and no Planner invocation. Starting with a deliberately prepared empty B prior inserts B rows without voiding A. Oracle checks exact IDs/actions/revisions, not counts alone. |
| SB-13 | Retain a shared party UUID in A/B historical views with different declared prior revisions; resolution preserves both exact views and never merges, renumbers or creates a party. Pair 15,000-row synthetic prior state with many small annual records; instrument nested-row traversal/copy seams to fail if resolution scans them. Record construction/resolution time separately from fixture setup without adding an arbitrary timing gate. |
| SB-14 | Preserve all 459 existing collected tests unchanged. Frozen sync/lock check, Ruff, full mypy and win32 mypy, dedicated/full tests, original benchmarks, handoff validation, whitespace and public scans pass. Native Windows/Linux CI retains all four mandatory Windows symlink tests. |

SourceBindingRecord prior registries in fixtures must be built through the
existing WP-04 public APIs. A shared-party fixture tests preservation of supplied
historical views; it is not proof of global revision persistence or annual rollover.
Snapshot construction errors remain upstream errors. Resolver output cannot waive
requiredness, financial/RS or other import prerequisites.

## Allowed changes

- New `packages/contracts/src/accounting_contracts/source_binding.py`.
- Public exports in `packages/contracts/src/accounting_contracts/__init__.py`.
- Usage and limitations in `packages/contracts/README.md`.
- New `tests/test_source_binding.py` and, if needed, focused new synthetic helpers
  or integration test files under `tests/`; preserve existing tests/helpers.
- Completed files in the WP-11 handoff directory.

No implementer changes to Roadmap/ADR/WP, existing product modules except exports,
manifests, lockfile, CI or benchmark limits. No Excel marker layout/reader/writer,
physical source attestation, enrollment/rollover commands, database schema or
identity projection, archive mutation/alerts, opening balance, financial rules,
watcher integration, deployment, external notification or real data.

Report an actual contract conflict for a bounded PM decision. Do not silently
change accepted v1 behavior or turn missing downstream evidence into a permission
flag, guessed year or fabricated first-import baseline.

## Validation and delivery

Capture exact commands, raw output and exit status:

```text
uv sync --frozen --all-packages --all-groups
uv lock --check
uv run pytest tests/test_source_binding.py -v
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run mypy --platform win32 .
uv run pytest -v
uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s
uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s
git diff --check origin/main...HEAD
python .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-11-source-binding
```

Explicitly run each added integration file. Retain the 15,000-row Reader/acquisition
15-second/128-MiB gates. Distinguish local Windows, local Linux and native CI
execution; static win32 mypy is not native Windows evidence.

Deliver `handoff.md`, `acceptance-matrix.md`, `test-results.txt` with SB-01..14
node IDs, exact execution baseline/tested code/final delivery identities, retained
baseline tests, scope diff, scans, limitations and fixed-SHA rollback. Verify
rollback in a clean isolated checkout and distinguish code rollback from later
documentation. Do not make self-referential commits to record an unknown own SHA.

The implementer stops for non-author review. If Codex implements, its own review
cannot supply independent acceptance. PM acceptance follows independent review
and native Windows/Linux CI. This package contributes routing evidence only;
physical marker provenance, durable identity projection/rollover and G1 remain open.
