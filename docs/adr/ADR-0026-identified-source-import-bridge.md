# ADR-0026: Bind one identified XLSX result to a source import request

- Status: Issued and accepted by Codex PM after independent plan review; implementation evidence pending
- Date: 2026-09-26
- Phase / gate: Phase 1; G1 remains OPEN / IN PROGRESS
- Predecessor: completed WP-17 at merge head `0fe36d6eb06ffff90598353070ea13fc42addbc6`
- Work package: [WP-18](../work-packages/phase-01/WP-18-identified-source-import-bridge.md)
- Component version: `identified-source-import-bridge.v1`

## Context and decision

WP-15 supplies an atomic, idempotent SQLite library core. WP-17 supplies a synchronous identified result whose marker, digest and Raw came from one verified XLSX lease. A caller assembling `SourceImportRequest` directly could combine fields from different results. Add an adapter in `accounting_persistence` that binds evidence from one supplied result to one accepted request, then delegates exactly one commit to the existing store.

This is ordinary Phase-1 work under existing Owner authority. The adapter is a library call and does not make a runtime callback durable. Its input is a read-only structural protocol, so persistence has no `accounting_local_agent` import or dependency. Tests use actual generated identified results for compatibility. Structural conformance and manual construction do not authenticate a workbook.

## Public contract

`accounting_persistence.identified_source_import_bridge` and the persistence package root export `IDENTIFIED_SOURCE_IMPORT_BRIDGE_VERSION = 'identified-source-import-bridge.v1'`, `IdentifiedSourceImportEvidence`, `IdentifiedSourceImportReason`, `IdentifiedSourceImportError`, `build_identified_source_import_request`, and `commit_identified_source_import`. No existing export changes.

The protocol exposes read-only `key: SourceBindingKey`, `read_result.snapshot: ValidatedSourceWorkbookSnapshot` and `file_sha256: str`; a nested read-result protocol may be private. The builder signature is `build_identified_source_import_request(identified: IdentifiedSourceImportEvidence, *, import_id: uuid.UUID, expected_generation: int, observed_at_utc: datetime, event_ids: Mapping[uuid.UUID, uuid.UUID]) -> SourceImportRequest`. The commit signature is `commit_identified_source_import(connection: sqlite3.Connection, identified: IdentifiedSourceImportEvidence, *, import_id: uuid.UUID, expected_generation: int, observed_at_utc: datetime, event_ids: Mapping[uuid.UUID, uuid.UUID]) -> SourceImportReceipt`. Both have only these parameters.

`event_ids` uses the accepted request mapping from changed row UUIDv7 to caller-supplied event UUIDv7. It must provide one ID for every INSERT, EDIT and VOID, and none for UNCHANGED. The caller retains the identical mapping and full attempt tuple for retry. The bridge cannot determine the changed-row set because it has no prior state; the accepted request/store validates mapping shape and the authoritative plan. The bridge neither allocates nor replaces IDs.

`IdentifiedSourceImportReason` contains exactly `INVALID_EVIDENCE = 'invalid_evidence'` and `INVALID_ATTEMPT = 'invalid_attempt'`. `IdentifiedSourceImportError` is a `ValueError` whose `reason_code` is that value. Its public strings are exactly `Invalid identified source evidence` and `Invalid identified import attempt`; repr contains only class and reason code. Neither exposes supplied values. Request and store errors retain their accepted types and reasons.

## Binding, ordering and ownership

Read `key`, `read_result`, nested `snapshot`, and `file_sha256` once each. Require an actual accepted key and validated complete snapshot and a lowercase 64-hex digest. Pass the same snapshot object to `SourceImportRequest`; derive any separate source ID and year fields only from the key. Do not infer from a filename, date row, cached result or another object. Evidence access or shape failures become `INVALID_EVIDENCE` before request construction or store access; direct non-`Exception` cancellation propagates.

Require an exact UUIDv7 `uuid.UUID` import ID, an exact non-Boolean nonnegative `int` expected generation, and an exact `datetime` with `tzinfo is datetime.UTC`. A distinct aware zero-offset timezone and a datetime subclass are invalid. These failures are `INVALID_ATTEMPT` before request construction. Preserve the instant and precision without normalization or clock access. Pass the identical event-ID mapping to the accepted constructor, which retains its validation and copying behavior. The caller keeps the mapping stable during construction and for retry.

The builder performs no I/O and constructs exactly one request. The commit entry calls the builder once, calls `commit_source_import` once and returns the identical receipt. It performs no read-before-write check, connection setup, transaction, side write, automatic retry or catch-and-recommit. The store first performs Requiredness and fiscal preflight, then resolves the registered ACTIVE source, before checking an existing import ID. Its transaction owns subsequent generation freshness checks and atomic history, membership, event and generation writes. An undated transaction row is observable fiscal evidence but fails the store's required date check with `VALIDATION_FAILED` and no writes. Empty or mixed-year observations alone do not forbid a commit; every accepted required field and store check still applies.

Replay and conflict claims apply only to requests that pass preflight and resolve ACTIVE. A matching import ID, complete request digest and identical full caller tuple, including event IDs and UTC timestamp, replays without new events or generation. A different otherwise admissible request under that ID conflicts. An invalid changed request may fail preflight before ID lookup; an archived source yields `SOURCE_NOT_ACTIVE` before replay or conflict, even with an existing ID. A new ID against an old expected generation is stale after those earlier checks. The bridge preserves store precedence and error taxonomy. Store errors, exception groups, direct cancellation and independent rollback failures pass through without wrapper reclassification. Fixed bridge messages are safe public diagnostics; arbitrary causes and tracebacks are not promised safe to publish.

The caller owns the open connection, foreign-key configuration, initialized source binding, stable attempt tuple and recovery. The runtime owns watcher lifecycle and is unchanged. If a runtime consumer commits and then fails, the completed coordinator attempt is not requeued. After process loss this bridge cannot recreate an unpersisted result, event-ID mapping or attempt identity. A later reviewed composition must own durable intent, callback failure, shutdown and restart recovery before operational wiring.

## Evidence, cost and rollback

Independent synthetic tests must prove exact field provenance and event-ID binding, one access to each evidence property, one request construction and one store call with unchanged forwarding and exact receipt identity. Separate evidence covers strict UTC handling, malformed-input nonmutation, active binding and error order, Requiredness and fiscal behavior, revision and shared-party membership, admissible full-tuple replay/conflict, ambiguous commit retry, controlled two-connection race outcomes, rollback, error identity, a runtime callback and 15,000-row resource behavior. Call-boundary spies and negative controls are required because final database state can conceal an extra replaying call. The new module's import side-effect guard applies after declared dependencies have loaded, with a failing negative control; it does not assert inert fresh import of the entire persistence dependency graph because canonical-date initialization can load `ZoneInfo`.

The adapter adds O(1) metadata and no second workbook or Raw buffer. Existing acquisition and SQLite resource targets remain. No test is claimed as run during planning. Implementation uses only synthetic temporary XLSX and SQLite fixtures and changes no store schema, event wire format, runtime, financial rule, dependency manifest or protected asset.

Before operational use, rollback reverts the additive adapter, package exports, documentation and focused tests through reviewed commits in an isolated checkout. It does not delete SQLite history or modify a source workbook. Durable runtime delivery, enrollment, rollover, real-data validation, discrepancy reporting and end-to-end G1 evidence remain separate. G1 remains OPEN / IN PROGRESS.