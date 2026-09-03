# WP-13: Preserve permanent identities while projecting active-source prior state

- Phase: 1 — source and data-model foundation
- Gate contribution: G1 identity/membership prerequisite; cannot close G1
- Status: Issued — implementation may start after the planning PR merges
- Issued by: Codex Project Manager
- Issued on: 2026-09-03
- Implementation workflow: `accounting-bot-implementer`; non-author review required
- Implementation branch: `codex/phase-01-source-identity-projection`
- Planning baseline: `ab0f07a00eabe526b642ebdde0e62516edee508e`
- Execution baseline: latest clean `origin/main` containing this WP and ADR-0016;
  record its actual SHA before coding
- Handoff path: `handoffs/phase-01/wp-13-source-identity-projection/`

## Objective and traceability

Implement `source-identity-projection.v1` from
[ADR-0016](../../adr/ADR-0016-source-identity-projection.md). Construct an immutable
catalog of global heads/transaction ownership from trusted source membership,
then project only the exact active source's prior plus globally known parties
that are present now but have not yet been committed to that source.

Roadmap 4.2/4.3 and O-03/O-58 require permanent identity and revision continuity;
5.3 freezes historical annual data. O-76 selects committed membership and O-77
binds the key to leased Raw. O-78 supplies the missing comparison scope without
changing WP-04's transition table. G1 remains OPEN / IN PROGRESS.

All data must be generated synthetic fixtures. Do not read/write a real workbook,
its real-data copy, production data or credentials. This is a pure contracts
package, with explicit test-only composition through already accepted adapters.

## Implementation boundary

Read ADR-0016 in full. Its five exports, exact signatures, catalog validation,
head/owner rules, projection/error ordering, safe representation, cost limits
and trust/commit boundaries are normative.

Reuse `SourceBindingRegistry`, its exact resolver, and the existing snapshot and
prior types. Preserve the selected source's prior objects; borrow only present
nonmember parties at their validated global heads. Do not flatten archives into
an operational prior, infer membership from raw dates, synthesize revisions or
rewrite identities. Active membership below its global head is an input conflict,
not permission to upgrade it silently.

The catalog contains complete caller-supplied historical membership, including
tombstones. Validation cannot prove omitted history or correct prior/source
association. Construction performs no storage read; projection performs no
membership write, financial validation, automatic Planner call or Commit.

New-source membership for an unchanged borrowed party must be demonstrated as a
future persistence obligation in the independent transition oracle. Do not
implement an ad hoc product commit/apply function to make the test pass.

## Acceptance matrix

Provide distinct test node IDs and raw output for every row. Expected ownership,
heads, scoped UUID sets, actions and revisions must come from literal fixtures
or an independent reference model, not from production indexes/projection code.

| ID | Required evidence |
|---|---|
| IP-01 | Exact version, five exports/signatures, frozen/slotted catalog, retained source_registry identity and derived count. Reject injected heads/count/version/index arguments. Inert target-module initialization uses a scoped side-effect guard with an injected-write negative control and an honest dependency-loading boundary. |
| IP-02 | Independent catalog oracle across all four sheets and multiple annual records. Correct greatest-revision heads and transaction owners, including tombstones; same hash at different revisions is valid. Empty and archive-only catalogs construct successfully. |
| IP-03 | Individually reject cross-source home-sheet disagreement, duplicate transaction ownership even at identical hash/revision or VOIDED state, conflicting states tied at any represented revision (including below a later head), and stale active membership. Accept equal-but-distinct head objects and lower immutable archive revisions; current-source prior object identity is retained. |
| IP-04 | Exact active key succeeds; archive, unknown ID (including same year as active), and known-ID wrong-year fail without an operational prior or Planner call. Assert the specified reason and exact resolver cause for wrong-year input. No fallback by year, filename or observed transaction date. |
| IP-05 | Literal UUID-set oracle: projection contains every active-source member/tombstone and only present nonmember parties; excludes all absent archive-only identities. Unknown present rows remain absent from prior. Empty current snapshot produces only the selected membership and only its expected VOID actions. |
| IP-06 | First membership of a globally active party: unchanged hash gives UNCHANGED at the existing prior revision; changed hash gives EDIT at +1; a globally voided party gives EDIT/reactivation at +1. No fresh UUID or revision reset. Compare exact IDs/actions/prior and planned revisions, not only counts. |
| IP-07 | Existing member transitions preserve the full WP-04 table: new row, edit, unchanged, deletion, repeated empty snapshot and reactivation. Include all four sheets and deletion of every active row without thresholds. The catalog/projection cannot suppress a valid selected-source VOID. |
| IP-08 | Current known UUID moved to each other sheet fails relocation, including a UUID known only in an archive; relocation precedes ownership checking for that row. Foreign-source transactions on their original sheet fail ownership for unchanged/edited/voided cases in each transaction sheet. Never generate replacement UUIDs or call Planner on failure. |
| IP-09 | Independent multi-year state sequence: active A, fixed archive A plus empty active B, B first imports shared party unchanged, B later edits/deletes/reactivates it, then fixed archive B plus empty active C. Build each committed metadata generation using a test-only independent model. Record membership even after UNCHANGED; compare every projected state and Planner item with a literal/reference oracle. Global party revisions never reset, archive objects/hashes never change, old transactions never become false VOID, and repeating the same uncommitted proposal changes no input state. This is synthetic metadata evolution, not durable rollover evidence. |
| IP-10 | Invalid root arguments to both entry points and strict reason enum construction (reject raw strings/foreign StrEnums). Public typed str/repr/args and catalog repr exclude synthetic raw/path/UUID/year/hash markers. Preserve exact resolver cause and cancellation objects. Snapshot, nested priors, mappings and archived final-file hashes remain unchanged on success and failure. |
| IP-11 | Explicit generated XLSX composition with WP-12, WP-11, WP-09/10 and WP-04. Old archive A plus new active B sharing a party; row reorder and formula/cache changes produce no false actions, one raw edit yields the exact revision change, mixed-year data does not select another source, and an archive/unknown key never reaches Planner. Preserve source bytes and acquisition cleanup; no product runtime is rewired. |
| IP-12 | Hypothesis with at least 30 generated valid histories compares all projected fields and Planner outcomes to an independent ownership/membership/head model. Actually permute source input, prior entry insertion order, sheet order and row order. Check deterministic mapping order and results; generate independent invalid catalogs as well. |
| IP-13 | Controlled isolated mutations must be caught by expected-value assertions: include an absent archive-only prior (false VOID), drop borrowing of a present known party (revision reset), and permit foreign transaction reuse. Retain raw targeted failures and show each is the intended semantic failure, not an import/setup exception. Restore product code and record the final clean tested-code SHA. |
| IP-14 | Build at least 15,000 distinct identities with additional shared-party membership in archives and an active source. Independently check the complete projected UUID/state set and all Planner results. Record fixture/catalog/projection/Planner timings separately and peak RSS without an invented new speed threshold. Instrument archive prior iteration to prove projection does not revisit it after catalog construction; changing archive size must not cause hidden scans per call. Preserve existing benchmark limits. |
| IP-15 | After fixtures are built, guard catalog construction/projection against file, network, clock, random/UUID, database, threads, evaluators and Planner side effects. Allow the explicit WP-11 resolver call; test-only integration is outside this pure guard. Include a negative control proving the guard fails on an injected side effect. No generation counter, auto-enrollment, mutation or commit flag appears. |
| IP-16 | Preserve all 678 existing collected test cases and all existing tests/helpers. Frozen sync/lock, Ruff, full native/win32 mypy, dedicated/full tests, original WP-05/06 and WP-12 combined benchmarks, Handoff validation, whitespace and public scans pass. Native Windows/Linux CI executes all four mandatory Windows symlink cases; static win32 typing and local privilege skips are disclosed separately. |

Use controlled Events/Barriers/ACKs for any integration concurrency; capture
background failures and clean up test threads/leases in finally. Do not add
public test knobs or weaken existing guard/benchmark/CI requirements.

## Allowed changes

- New `packages/contracts/src/accounting_contracts/source_identity_projection.py`.
- Additive exports in `packages/contracts/src/accounting_contracts/__init__.py`.
- Usage and trust-boundary guidance in `packages/contracts/README.md`.
- New `tests/test_source_identity_projection.py` and focused new synthetic
  helpers/probes under `tests/`; preserve all existing test/helper files.
- Completed documents in `handoffs/phase-01/wp-13-source-identity-projection/`.

No implementer changes to Roadmap/ADR/WP, existing contracts, Reader/acquisition/
coordinator/runtime, schema/persistence, manifests/dependencies/lockfile, CI or
benchmark thresholds. No source marker writer, real workbook access, UUID
allocation/repair, enrollment, commit/apply API, database/outbox, financial/RS
policy, automatic import, rollover, notification or deployment.

An actual contract conflict requires a minimal reproduction and a bounded PM
decision before widening this scope. Do not silently change a previous contract
or weaken an independent oracle to accept the new implementation.

## Validation and delivery

Capture exact commands, raw stdout/stderr and exit status:

```text
uv sync --frozen --all-packages --all-groups
uv lock --check
uv run pytest tests/test_source_identity_projection.py -v
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run mypy --platform win32 .
uv run pytest --collect-only -q
uv run pytest -v
uv run pytest tests/test_source_identity_projection.py -k ip14 -v -s
uv run pytest tests/test_xlsx_source_reader.py::test_xr12_synthetic_15000_row_benchmark -v -s
uv run pytest tests/test_xlsx_snapshot_acquisition.py::test_sa14_combined_15000_row_benchmark -v -s
uv run pytest tests/test_xlsx_source_identity.py::test_xi14_combined_15000_row_benchmark -v -s
git diff --check origin/main...HEAD
python .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-13-source-identity-projection
```

Run every added test file explicitly. Preserve the 678 baseline node IDs and
record the exact new collected total. Separate local Windows/Linux, win32 static
typing and native CI evidence. Existing Reader/Acquisition/WP-12 combined limits
remain 15 seconds / 128 MiB; no threshold is relaxed for local machine load.
Only synthetic, scanned evidence is published; generated data stays outside Git.

Deliver `handoff.md`, `acceptance-matrix.md` and `test-results.txt`, mapping
IP-01..16 to exact tests and retained evidence. Record planning/execution/tested-
code/delivery SHAs, allowed-file diff, model/mutation/guard limits and fixed-SHA
rollback. Verify rollback in a clean isolated checkout and distinguish code
commits from later documentation. Do not embed an unknown own SHA in a commit.

The implementer stops for non-author review. Codex-authored code requires a
reviewer who did not author it; PM acceptance follows independent review and
successful native Windows/Linux CI. Passing WP-13 proves synthetic projection
semantics only. Durable complete membership, atomic import/revision/outbox,
enrollment, real marker retention and rollover/crash recovery remain unproved;
G1 stays OPEN / IN PROGRESS.
