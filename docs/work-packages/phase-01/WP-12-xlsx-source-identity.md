# WP-12: Identify a source from the same leased XLSX as its raw rows

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 same-acquisition identity prerequisite; cannot close G1
- Status: Issued — implementation may start after the planning PR merges
- Issued by: Codex Project Manager
- Issued on: 2026-09-03
- Implementation workflow: `accounting-bot-implementer`; non-author review required
- Implementation branch: `codex/phase-01-xlsx-source-identity`
- Planning baseline: `31c4d48280ea5e1562982443ca69e8ffe69b3612`
- Execution baseline: latest clean `origin/main` containing this WP and ADR-0015;
  record its actual SHA before coding
- Handoff path: `handoffs/phase-01/wp-12-xlsx-source-identity/`

## Objective and traceability

Implement `xlsx-source-identity.v1` from
[ADR-0015](../../adr/ADR-0015-xlsx-source-identity.md). Read the versioned, unlinked
`AccountingBot.SourceIdentity` custom property and the existing four-sheet Raw
result from one ZIP object inside one WP-06 stable lease. Return the association
with its acquisition byte hash/count only after ZIP close, lease revalidation
and cleanup succeed.

Roadmap 5.1/5.2 require complete, stable, read-only acquisition; 5.3/O-07 requires
annual identity independent of filename and ordinary archive exclusion. O-69 and
O-74/75 preserve Planner/requiredness/fiscal behavior. O-76 selects prior state;
O-77/ADR-0015 now defines how the corresponding key is read from XLSX. A copied
marker is not new enrollment, authentication or proof of a durable prior registry.
G1 remains OPEN / IN PROGRESS.

All workbook inputs must be generated synthetic fixtures. Do not inspect or
modify a real workbook, its real-data copy, production state or credentials.
Do not add VBA, COM automation, marker writing or a new production command.

## Implementation boundary

Read ADR-0015 in full. Its seven exports, exact signature, marker wire grammar,
property/link rules, package discovery, namespace families, metadata limits,
error taxonomy and return-after-cleanup behavior are normative.

Use the existing `SourceBindingKey` and `XlsxSourceReadResult` as nested objects;
retain them by identity. The new result has no path, lease handle, caller-supplied
approval flag or implicit prior. A constructor validates representation only;
the complete adapter function supplies the controlled I/O lifecycle guarantee.

Acquire using the existing WP-06 context manager. The one open ZIP object inside
the lease supplies both marker parsing and existing
`xlsx_source_reader._read_xlsx_from_zip` Raw parsing. That internal dependency is
intentional. Do not copy/rewrite Raw parsing or change the standalone Reader API.
The existing relationship helper filters candidates, so it cannot be the new
marker's cardinality oracle. Do not turn an external/invalid duplicate into an
apparently unique valid relationship.

Capture all independent read/close/lease failures without masking their original
objects or causes. Result delivery occurs after all cleanup, never by callback or
yield within the context. An error is not a partial snapshot, an empty workbook,
MISSING_MARKER fallback, UNREGISTERED success or automatic retry command.

The metadata cap applies to three small XML members only, including actual
decompressed bytes. It imposes no raw row count or change-volume restriction.
Do not buffer or extract the workbook to make shared package reading easier.

## Acceptance matrix

Provide explicit test node IDs and raw output for every row. Use distinct test
functions/parameterized cases for independent failure paths, not a single large
test containing unrelated scenarios. Expected keys, Raw hashes, actions and
revisions must be declared independently of the production parser/selector.

| ID | Required evidence |
|---|---|
| XI-01 | Exact version/constants, seven exports, public signature and result fields. Inert fresh target-module execution has a scoped side-effect guard and injected-write negative control; describe code/dependency loading outside the guard honestly and preserve import identities. |
| XI-02 | Literal independent valid markers for both Transitional and Strict families, arbitrary prefixes, property/relationship ordering, non-default valid target path, root-relative/internal targets, UTF-8 and UTF-16 XML. Assert the exact UUIDv7/year and unchanged complete Raw result. |
| XI-03 | Missing custom relationship, orphan custom XML, and a related property part lacking the reserved property each fail as MISSING_MARKER. An absent marker on empty, undated or mixed-year workbooks never guesses a key. The standalone WP-05 reader continues to accept otherwise valid markerless fixtures. |
| XI-04 | Independent duplicates: two custom relationships (including one external or invalid), duplicate relation IDs, repeated or case-colliding reserved names, duplicate numeric property IDs, duplicate selected content-type Overrides, exact duplicate ZIP members and case-only metadata aliases. None selects the first/last candidate. Assert the specified identity/package error, no result and complete cleanup. |
| XI-05 | Invalid wire version, separators, extra fields, UUID text/variant/version/case, non-ASCII digits, whitespace and invalid years; valid declared boundaries retain the existing parser rules without a 1405/current-year floor. Reject wrong value type/namespace, nested/alternate values, wrong property-name case, bad fmtid/pid and linkTarget even when its cache looks valid. Unrelated property values and links remain ignored. |
| XI-06 | No external fetch or filesystem extraction. Reject external/escaping/noncanonical/percent-encoded targets, missing target/member/Override, wrong content type, malformed XML, DTD/entity payloads and marker wrappers. Test the cap at minus one, exactly the limit and plus one independently for all three metadata members using valid XML padding; instrument reads and a compressed oversized member to prove actual bounded decompression. No unlimited read is used by new metadata parsing. |
| XI-07 | Real acquisition with generated XLSX A; replace the original live path with B after the lease yields. The successful result keeps A's key, Raw, byte hash and count, never B's key with A's rows. Instrument ZIP object identity and prove one package open within the lease and no post-yield parsing of the live path. Preserve the caller's deliberate B replacement. |
| XI-08 | Separately tamper with/replace the leased snapshot between marker and Raw parsing, and after parsing before lease exit. Assert integrity failure and no returned object. Verify successful return is delayed until ZIP close and lease exit ACKs complete; test with controlled gates, not fixed sleeps. |
| XI-09 | Acquisition failure, identity failure, Raw failure, ZIP close failure, lease integrity/cleanup failure and concurrent independent combinations. Preserve exact error objects/causes, including shared-cause errors and KeyboardInterrupt/SystemExit in BaseExceptionGroup. No cleanup failure can become success or hide the read error; all streams/leases are collected on every path. |
| XI-10 | Generated generations with identical marker: physical row reorder, formula/cache-only edits and unrelated custom-property edits leave Raw/Planner results unchanged; one raw edit changes exactly its expected identity/action. Whole-file byte hash may change independently. Assert file bytes are never rewritten by the adapter. |
| XI-11 | Explicit composition with WP-11/04/09/10 using archive A, active B, unknown C and a known-ID wrong-year case at the same source filename. Independently check selected prior identity and exact Planner IDs/actions/revisions; empty complete B voids only B membership. Archive/unknown cases make no Planner call. Mixed/undated Raw dates do not change the marker key. The production adapter itself calls none of these evaluators/resolvers. |
| XI-12 | Frozen/slotted result retains key/read-result identity, fixed version, correct byte metadata and no live path/handle. Invalid constructors/types/hash/count and injected fields fail; bool is not a count. Synthetic raw/invalid-marker/path strings are absent from new public typed str/repr/args; diagnostic causes stay intact. Direct construction is not described as file attestation. |
| XI-13 | Property tests with at least 30 generated cases compare a declared independent marker/key model under actual ordering/prefix/target-path permutations and one key-field change. Verify changed marker bytes produce the expected changed key even when Raw is identical. A controlled constant-key parser mutation must be caught for an expected-value assertion, not an unrelated setup error; retain its diagnostic evidence without committing a mutation. |
| XI-14 | One combined 15,000-row benchmark through the complete new reader, with fixture time separate. Check key/hash/count, all expected identities and Raw outcomes, unchanged source bytes and finished cleanup. Retain the existing 15-second / 128-MiB combined boundary. No full-workbook buffer or second snapshot is introduced; record measured time/RSS without replacing the original benchmarks. |
| XI-15 | New adapter delegates existing path/root/observation policy; read-only source and unrelated files are intact. No marker creation, UUID generation for source identity, enrollment, registry mutation, COM, runtime startup, persistence, networking or notification occurs. Acquisition's existing private lease IDs and temporary writes remain allowed. Include a guard-specific negative control. |
| XI-16 | All 530 existing collected cases and their files remain unchanged. Frozen sync/lock, Ruff, full mypy plus win32 mypy, dedicated/full tests, both original benchmarks, Handoff validation, whitespace and public scans pass. Native Windows/Linux CI executes all four mandatory Windows symlink cases; local missing privileges are disclosed, never counted as passing coverage. |

Race/failure tests must synchronize with Events/Barriers/ACKs, capture background
errors and stop/join any spawned test threads in finally. Instrument the adapter
seams without adding public test-only knobs or modifying existing fixtures/tests.
Distinguish a copied logical key from successful registration; never assert that
matching the current file's marker authenticates a person or permits Commit.

## Allowed changes

- New `apps/local_agent/src/accounting_local_agent/xlsx_source_identity.py`.
- Additive exports in `apps/local_agent/src/accounting_local_agent/__init__.py`.
- Usage/trust-boundary guidance in `apps/local_agent/README.md`.
- New `tests/test_xlsx_source_identity.py` and focused new synthetic helpers/probes
  under `tests/`; preserve all existing test/helper files.
- Completed documents in `handoffs/phase-01/wp-12-xlsx-source-identity/`.

No implementer changes to Roadmap/ADR/WP, existing Reader/acquisition/coordinator/
runtime code, contracts/persistence, manifests, dependencies/lockfile, CI or
benchmark thresholds. No marker writer, real workbook/copy access, metadata
repair/defaulting, physical source enrollment, global identity projection,
financial/RS policy, database/outbox, rollover, alert delivery or deployment.

If implementation reveals an actual contract conflict, report it with a minimal
reproduction for a bounded PM decision. Do not silently widen the scope or weaken
existing tests/profile checks to make the composition pass.

## Validation and delivery

Capture exact commands, raw stdout/stderr and exit status:

```text
uv sync --frozen --all-packages --all-groups
uv lock --check
uv run pytest tests/test_xlsx_source_identity.py -v
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run mypy --platform win32 .
uv run pytest --collect-only -q
uv run pytest -v
uv run pytest tests/test_xlsx_source_identity.py::test_xi14_combined_15000_row_benchmark -v -s
uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s
uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s
git diff --check origin/main...HEAD
python .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-12-xlsx-source-identity
```

Run every added test file explicitly. Preserve all 530 baseline test nodes and
record the new collected total. Distinguish local Windows/Linux, static win32
typing and native CI. Use original benchmark thresholds without load-dependent
relaxation. All generated workbooks/databases and logs containing fixture payloads
remain temporary/outside Git; published evidence is synthetic and scanned.

Deliver `handoff.md`, `acceptance-matrix.md` and `test-results.txt` with XI-01..16
mapping, exact planning/execution/tested-code/delivery identities, allowed-file
diff, raw command evidence, import/mutation guard limits and fixed-SHA rollback.
Verify rollback in a clean isolated checkout and distinguish code rollback from
later Handoff commits. Do not amend an unknown own SHA into self-referential docs.

The implementer stops for non-author review. Codex-authored implementation needs
a reviewer who did not author it. PM acceptance follows that review and successful
native Windows/Linux CI. Real Excel marker retention, enrollment and durable
identity/import/rollover remain unproved; G1 stays OPEN / IN PROGRESS.
