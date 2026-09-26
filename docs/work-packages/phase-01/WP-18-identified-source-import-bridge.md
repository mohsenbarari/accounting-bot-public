# WP-18: Bind an identified source result to one atomic import attempt

- Phase: 1 — source and data-model foundation
- Status: Issued and accepted by Codex PM after independent plan review; implementation evidence pending
- Issued on: 2026-09-26
- Planning source head: `0fe36d6eb06ffff90598353070ea13fc42addbc6` (completed WP-17 merge head, not an implementation-stage SHA)
- Decision: [ADR-0026](../../adr/ADR-0026-identified-source-import-bridge.md)
- Implementation branch: `antigravity/phase-01-identified-source-import-bridge`
- Handoff path: `handoffs/phase-01/wp-18-identified-source-import-bridge/`
- Gate state: G1 remains OPEN / IN PROGRESS

## Objective and authority

WP-17 delivers a marker, file digest and Raw snapshot from one verified lease. WP-15 commits a separately assembled `SourceImportRequest` atomically. Add a small persistence-side bridge that binds request evidence from one result and delegates to the accepted store. The caller supplies a stable import identity, expected generation, UTC observation time and event IDs. The bridge owns no watcher, database connection or retry journal.

This is ordinary Phase-1 work under existing Owner authority. Use only generated disposable workbooks and SQLite databases. A structural object or manually constructed `IdentifiedXlsxSource` is representation, not proof of acquisition. This bridge is a prerequisite for a separately reviewed durable runtime consumer; this WP does not install it as an operational consumer.

## Public API

Create `accounting_persistence.identified_source_import_bridge` and export exactly these six symbols from `accounting_persistence`:

- `IDENTIFIED_SOURCE_IMPORT_BRIDGE_VERSION = 'identified-source-import-bridge.v1'`.
- `IdentifiedSourceImportEvidence`, a read-only structural `Protocol` with `key: SourceBindingKey`, `read_result.snapshot: ValidatedSourceWorkbookSnapshot`, and `file_sha256: str`. Its nested read-result protocol may remain private. Persistence must not import `accounting_local_agent`.
- `IdentifiedSourceImportReason`, a string enum with exactly `INVALID_EVIDENCE = 'invalid_evidence'` and `INVALID_ATTEMPT = 'invalid_attempt'`.
- `IdentifiedSourceImportError`, a `ValueError` with `reason` and `reason_code`. Its public string is exactly `Invalid identified source evidence` or `Invalid identified import attempt` for the respective reason; its repr contains only the class and reason code, never supplied values.
- `build_identified_source_import_request(identified: IdentifiedSourceImportEvidence, *, import_id: uuid.UUID, expected_generation: int, observed_at_utc: datetime, event_ids: Mapping[uuid.UUID, uuid.UUID]) -> SourceImportRequest`.
- `commit_identified_source_import(connection: sqlite3.Connection, identified: IdentifiedSourceImportEvidence, *, import_id: uuid.UUID, expected_generation: int, observed_at_utc: datetime, event_ids: Mapping[uuid.UUID, uuid.UUID]) -> SourceImportReceipt`.

The mapping uses the accepted `SourceImportRequest.event_ids` shape: changed row UUIDv7 to caller-supplied event UUIDv7, exactly one for each planned INSERT, EDIT or VOID and none for UNCHANGED. No positional variants, defaults, path, separate key/snapshot/hash override, clock, random source, connection factory, request factory, callback, retry flag or approval flag are added. Existing persistence exports and signatures remain unchanged.

## Normative behavior and ownership

1. Read `key`, `read_result`, its `snapshot`, and `file_sha256` once each into local variables. Require an actual `SourceBindingKey`, `ValidatedSourceWorkbookSnapshot` and lowercase 64-character hexadecimal file digest. Derive source ID and fiscal year only from that key; pass the exact snapshot object and digest to the accepted request constructor. Do not rebuild, filter, rehash or infer from filename or date rows. Malformed or inaccessible evidence is `INVALID_EVIDENCE` before request construction or database access. Ordinary property-access failures may be retained as causes; direct non-`Exception` cancellation propagates.
2. Before request construction, require an exact UUIDv7 `uuid.UUID` import ID, an exact non-Boolean nonnegative `int` generation, and an exact `datetime` whose `tzinfo is datetime.UTC`. Reject naive, other-zone, merely zero-offset and datetime-subclass objects as `INVALID_ATTEMPT`. Preserve the supplied instant and precision; do not normalize, round or read a clock.
3. Pass `event_ids` into the accepted `SourceImportRequest` unchanged, without generating, replacing or silently dropping IDs. The caller owns a stable mapping through construction and must retain the same full attempt tuple for an ambiguous-commit retry. The accepted request/store owns mapping shape, UUID validity, duplicate values and the exact changed-row set after authoritative planning; its existing errors pass through. The bridge does not read prior state or guess a plan. A caller may prepare IDs from a committed store view and proposed plan, but the store recomputes and verifies the plan; stale or incorrect preparation fails without automatic ID reallocation.
4. The builder performs no I/O and constructs one request using the accepted constructor fields. The commit entry calls the builder once and `commit_source_import(connection, request)` once, returning the identical receipt object. It performs no preliminary store read, separate transaction, side write, automatic retry or catch-and-recommit.
5. Preserve the store's actual order. After request construction, `commit_source_import` performs Requiredness and fiscal preflight, resolves the registered ACTIVE source, and only then checks an existing import ID; its transaction owns subsequent freshness checks and atomic writes. An undated transaction row remains observable as fiscal evidence but fails the store's required date validation with `VALIDATION_FAILED` and no writes. Empty and mixed-year snapshots are not rejected merely for those observations; they may commit only if all accepted required fields and store checks pass. No new admission rule is added.
6. Replay and conflict apply only after preflight passes and the source resolves ACTIVE. For the same `import_id` and identical complete request digest and full caller tuple, including event IDs and timestamp, the store returns `REPLAYED` without new events or generation. An otherwise admissible changed request under that ID reaches `IDEMPOTENCY_CONFLICT`. An invalid changed request may fail preflight first; an archived source yields `SOURCE_NOT_ACTIVE` before either ID outcome, even for an otherwise identical retry. A new ID against a stale generation retains `STALE_STATE` after those earlier checks. Do not alter store precedence or taxonomy.
7. Builder rejection precedes database access. Store errors, exception groups, direct cancellation, identities and causes pass through without bridge wrapping, including independent rollback failures. Fixed bridge messages and repr contain no path, marker, Raw, hash or timestamp; arbitrary diagnostic causes and tracebacks are not promised safe for publication.
8. The caller owns the open connection, foreign-key setting, initialized store, attempt tuple and later recovery. The runtime owns watcher lifecycle and is unchanged. A runtime consumer failure after a successful commit cannot undo the completed coordinator attempt or cause runtime redelivery. Process restart cannot recover an unpersisted result or attempt tuple under this WP.

## Acceptance matrix

Each criterion has a distinct Pytest node and an independent oracle: literal fields, a separately constructed control request, direct SQL, a spy at the delegated call boundary, or a separate state model. A bridge return value alone is insufficient evidence.

| ID | Nonoverlapping required evidence |
|---|---|
| BI-01 | Assert the exact version, six exports, two signatures and unchanged prior exports. After declared dependencies are loaded, import the new bridge module under failing I/O/time/random/thread traps with a failing negative control; report this guard scope explicitly. Do not claim blanket fresh persistence-package import inertness because canonical-date initialization can load `ZoneInfo`. Prove no local-agent import or undeclared dependency. |
| BI-02 | A generated valid four-sheet identified XLSX produces a request with the exact marker key, identical snapshot object, digest, caller import ID, generation, exact UTC instant and supplied event-ID mapping. Compare with a separately constructed control request and literal expected values. |
| BI-03 | Missing, malformed, wrong-typed or throwing evidence and invalid UUID, generation and datetime forms fail with the specified fixed bridge diagnostics before a cursor or store call. Use a custom distinct zero-offset `tzinfo` to prove the strict `datetime.UTC` identity rule. Invalid event-ID mapping retains the accepted request/store error without wrapping. |
| BI-04 | Commit only against the exact registered ACTIVE key. Unknown and contradictory-year keys retain accepted store rejection; an archived key yields `SOURCE_NOT_ACTIVE`, including when an import ID matches prior history. No case borrows another annual source. |
| BI-05 | Requiredness failure and an undated transaction row separately produce store `VALIDATION_FAILED` with no writes. Empty and mixed-year snapshots commit only when accepted required fields pass; fiscal observations are preserved without a new year-selection rule. |
| BI-06 | Synthetic generations cover insert, edit, void, reactivation and no-op. Supply exact event IDs for each changed-row plan and compare SQL revisions, events, Raw bytes, hash chains and receipt counts with independent expected values. |
| BI-07 | A shared unchanged party in a new source gains membership; archived-only absent identities remain untouched. Verify no history merge or global revision reset with direct SQL. |
| BI-08 | For an admissible ACTIVE-source request, retry the identical full tuple and assert `REPLAYED` with no new sequence. Change content, event IDs and timestamp separately while keeping each request otherwise admissible, and assert `IDEMPOTENCY_CONFLICT`; a new ID against a stale generation yields `STALE_STATE`. Independently prove that invalid preflight and archived-source cases take precedence over ID lookup. Inspect state after every case. |
| BI-09 | Simulate an ambiguous post-COMMIT return, reopen the disposable database and retry the identical full tuple. Prove one import and one event sequence; the bridge never allocates substitute IDs. |
| BI-10 | Inject failures before and during store writes. Confirm rollback leaves generation and tables intact; preserve simultaneous primary and rollback failures. |
| BI-11 | Race two independent connections from one expected generation with valid ACTIVE-source inputs and controlled contention. Distinct import IDs yield exactly one `COMMITTED` and one `STALE_STATE`; the same import ID with an identical full tuple yields one `COMMITTED` and one `REPLAYED`; the same ID with differing but otherwise admissible full tuples yields one `COMMITTED` and one `IDEMPOTENCY_CONFLICT`. Inspect SQL for one event sequence and no partial membership. |
| BI-12 | Store errors, grouped failures and direct non-`Exception` cancellation retain identity, type and cause; bridge public diagnostics omit supplied Raw, marker, path, hash and timestamp. |
| BI-13 | A synthetic WP-17 `run_identified` callback invokes the bridge synchronously on an initialized disposable store. Assert result object identity, one commit, callback thread ownership and completed lease cleanup. Prove watcher code itself makes no persistence call and callback failure causes no runtime redelivery. |
| BI-14 | Process 15,000 generated rows through identified acquisition and commit with independently counted rows and sequences. Measure acquisition/identity and store windows separately: preserve the accepted 15-second/128-MiB acquisition gate and 350-MiB store target without a second whole-workbook buffer. |
| BI-15 | Use counted, changing and throwing descriptors for each evidence property, including nested `read_result.snapshot`. Prove each is accessed exactly once and one result's key, snapshot and digest cannot be mixed with later property values. A deliberate double-read negative control must fail the oracle. |
| BI-16 | Spy on accepted `SourceImportRequest` construction and `commit_source_import`: exactly one constructor call and one store call, the identical snapshot and event-ID mapping at the constructor boundary, the constructed request at the store boundary, and the identical returned receipt. Include a deliberate duplicate-call negative control, because final database state alone can conceal a second replaying call. |

## Exact files and exclusions

Implementation changes exactly `packages/persistence/src/accounting_persistence/identified_source_import_bridge.py`, `packages/persistence/src/accounting_persistence/__init__.py`, `packages/persistence/README.md`, and `tests/test_identified_source_import_bridge.py`. Handoff changes exactly `handoffs/phase-01/wp-18-identified-source-import-bridge/handoff.md`, `acceptance-matrix.md`, and `test-results.txt` in that directory. Do not edit the existing store, runtime, contracts, tests, TOML, lockfile, CI, Roadmap or protected assets.

No schema or migration, enrollment, active-source registration, archive activation, rollover, opening balance, financial rule, discrepancy UI, server Sync/ACK, signature, production database, real workbook, Excel COM, OneDrive, deployment or new service is in scope. No durable callback acknowledgment, pending-attempt journal, automatic consumer retry, process restart recovery or G1 closure is claimed.

## Validation, handoff and rollback

No test is claimed as run during planning. The controller runs focused and full regression, Ruff, both Mypy targets and handoff validation for both stages. Native Windows/Linux CI, including mandatory Windows symlink cases, remains acceptance evidence; static win32 typing is not native Windows execution. Preserve all predecessor assertions and resource limits.

Handoff requires the successful controller receipt for the immediately preceding implementation stage with its actual tested commit, exact commands, exit codes and results. If absent or incomplete, stop without inventing evidence. Map BI-01 through BI-16 to exact nodes; record SHA-256 for this WP, ADR-0026 and all four implementation files; report platform scope and durable-delivery limits honestly. Never fabricate a current SHA.

Before operational use, rollback reverts only this additive module, exports, README text and focused tests through reviewed commits in an isolated checkout. Rehearse with generated disposable workbooks and databases. Do not delete a real database, rewrite store history or touch an XLSX source. Stop for independent non-author review after handoff. Existing Owner authority covers review, PR and green native-CI merge. G1 remains OPEN / IN PROGRESS.