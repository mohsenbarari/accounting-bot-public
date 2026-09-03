# accounting-local-agent

Windows interactive user-session agent for monitoring Excel `.xlsx` saves, streaming literal source extraction, local SQLite mirroring and resilient outbox synchronization.

## Architecture and boundaries

- **Managed Source Watcher and Read Runtime (`source_watch_runtime.py` — WP-08 / ADR-0011):**
  - Public version: `SOURCE_WATCH_RUNTIME_VERSION = "source-watch-runtime.v1"`
  - Public API: `SourceWatchRuntime`, `SourceWatchRuntimeView`, `SourceWatchRuntimeState`, `SourceWatchRuntimeReason`, `SourceWatchRuntimeError`
  - Lifecycle: single-use `new -> running -> stopping -> stopped` (clean) or `failed` (on error).
  - Connects native `watchdog.observers.Observer` non-recursively to the source parent directory, maps mutating events to coordinator notifications, and enqueues an initial logical `MODIFIED` notice on successful start.
  - Derives waiting intervals from coordinator deadlines with an idle 1.0s liveness check for observer and emitter threads.
  - Executes serial `read_due_source` lock-free and delivers successful results synchronously to the caller-supplied `consumer` callback on the run thread.
  - Thread-safe, non-blocking `request_stop()` wakes the loop, closes event admission, and gracefully drains any admitted read.
  - Limitations: No process-wide lock or multi-instance coordination for the same file; liveness checks are bounded by the next loop iteration (blocked reader I/O or consumer delays liveness detection); normal shutdown waits for admitted I/O and backend joins without hard process termination.
  - Synthetic library usage:
    ```python
    from pathlib import Path
    from accounting_local_agent import SourceWatchRuntime

    runtime = SourceWatchRuntime(
        Path("/tmp/watched/synthetic_data.xlsx"),
        snapshot_root=Path("/tmp/snapshots"),
        observation_interval_seconds=0.05,
    )
    # runtime.run(lambda result: print("Received snapshot:", result.snapshot.version))
    ```

- **Save Debounce, Coalescing, and Coordination (`save_import_coordinator.py` — WP-07 / ADR-0010):**
  - Public version: `SAVE_IMPORT_COORDINATOR_VERSION = "save-import-coordinator.v1"`
  - Public API: `SaveImportCoordinator`, `SaveCoordinatorView`, `SourceReadAttempt`, `SaveEventKind`, `SaveCoordinatorState`, `SourceReadOutcome`, `read_due_source(...)`
  - Fixed debounce: `SAVE_DEBOUNCE_NS = 2_000_000_000` (2.0s quiet period)
  - Enforces exact-path matching with host-native lexical comparison (`normpath`/`normcase`), ignoring sibling `.tmp`, lock `~$`, conflict, archive, snapshot, directory, and read-only notices.
  - Coalesces notification bursts into one pending reservation; remembers at most one follow-up when a Save occurs during reading.
  - Thread-safe state transitions without holding locks across file I/O or parsing.
  - Synchronous `read_due_source` driver reserves an attempt, invokes `open_stable_xlsx_snapshot` and `read_xlsx_source_snapshot`, verifies clean context exit, completes attempt bookkeeping, and re-raises failures with original causes.

- **Stable XLSX Snapshot Acquisition (`xlsx_snapshot_acquisition.py` — WP-06 / ADR-0009):**
  - Public version: `XLSX_SNAPSHOT_ACQUISITION_VERSION = "xlsx-snapshot-acquisition.v1"`
  - Public API: `open_stable_xlsx_snapshot(source_path, snapshot_root, observation_interval_seconds) -> Iterator[StableXlsxSnapshot]`
  - Converts an exact caller-supplied `.xlsx` path into an immutable, verified temporary snapshot lease.
  - Takes two ordered observations separated by the interval, streams a bounded-memory copy calculating SHA-256 and byte count, independently reverifies the source and copy, checks the ZIP container marker, and promotes candidate `.part` to `.xlsx` atomically.
  - Post-lease verification checks digest and length on context exit before deterministic cleanup of managed artifacts.
  - Typed error taxonomy: `XlsxSourceNotReadyError` (retryable), `XlsxSourcePolicyError`, `XlsxSnapshotStorageError`, `XlsxSnapshotIntegrityError`, `XlsxSnapshotCleanupError`.

- **Read-Only XLSX Source Reader (`xlsx_source_reader.py` — WP-05 / ADR-0008):**
  - Public version: `XLSX_SOURCE_READER_VERSION = "xlsx-source-reader.v1"`
  - Public API: `read_xlsx_source_snapshot(path: Path | str) -> XlsxSourceReadResult`
  - Reuses authoritative contracts from `accounting_contracts` without duplicating registry or hashing rules.
  - Returns a validated `ValidatedSourceWorkbookSnapshot` (WP-04) alongside an immutable mapping of `UUIDv7 -> SourceRowLocation` (physical sheet and row number in `2..1,048,576`).
  - Locations do not enter source hashes, sheet snapshot hashes or change planning.

## Streaming execution strategy

To guarantee strict memory bounding (< 128 MiB peak RSS) and fast linear runtime (< 15s for 15,000 rows) without full DOM accumulation or whole-part reads:
1. **Pass 1 (Per Approved Sheet):**
   - Streams worksheet XML with `lxml.etree.iterparse` rejecting DTDs/external entities structurally across encodings.
   - Collects bounding boxes for `array` and `dataTable` formulas, indexing intervals per column for $O(\log K)$ binary search lookups.
   - Collects the exact set of referenced Shared String Table (SST) indices (`t="s"`).
   - Clears element subtrees and siblings continuously.
2. **SST Pass (Single Pass over `sharedStrings.xml`):**
   - Streams `sharedStrings.xml` to EOF.
   - Selectively decodes only the referenced SST indices needed by active cells or headers.
   - Unreferenced entries are skipped without string allocation or decoding.
3. **Pass 2 (Per Approved Sheet):**
   - Validates row 1 Persian and technical headers against `RAW_CONTRACT_REGISTRY`.
   - Evaluates row activity from literal cells before decoding technical identifiers or inactive dates.
   - Inactive rows (template/formatting/whitespace) are omitted cleanly.
   - Active rows validate RFC 4122 UUIDv7 format, global uniqueness, and decode raw fields into `SourceRowInput`.
   - Clears row element subtrees and siblings continuously.

## Supported XLSX profile (ADR-0008)

1. **Package and relationships:**
   - Unencrypted standard `.xlsx` OPC ZIP packages in Transitional (`http://schemas.openxmlformats.org/spreadsheetml/2006/main`) and Strict (`http://purl.oclc.org/ooxml/spreadsheetml/main`) namespace families.
   - Dynamic package relationship traversal via `_rels/.rels` and `workbook.xml.rels` checking relationship ID uniqueness and rejecting ambiguous entries.
   - Exactly all four approved sheets (`خرید-فروش`, `دریافت-پرداخت`, `ورود-خروج`, `لیست کسبه`) must be declared and present; additional helper/report sheets are safely ignored without opening their XML.
2. **Formula and cache exclusion:**
   - Cells with `<f>` elements and cells within array/data-table formula `ref` ranges are excluded prior to value conversion; their values map to `None`.
   - Literal overrides in shared formula ranges are preserved.
   - Formula errors in excluded cells do not block ingestion.
3. **Literal decoding:**
   - `None` for missing cells, `""` for explicit empty text.
   - Direct `Decimal` parsing from numeric XML without binary float approximations.
   - Enforces strict ASCII numeric grammar (`^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$`).
   - Financial dates (`date_raw`) and UUIDs require text; numeric XML in these fields is rejected.
   - Booleans, errors, and invalid types in retained fields raise typed errors.
4. **Row activity and identity:**
   - Rows with literal non-blank values in `activity_columns` are active (numeric zero is active; empty/whitespace text is inactive).
   - Inactive rows (template, style-only, leftover date/ID) are omitted without creating identities or void events.
   - Active rows require a valid UUIDv7 in their designated technical ID column (`Z` for خرید-فروش, `P` for دریافت-پرداخت, `P` for ورود-خروج, `D` for لیست کسبه).

## Source identity from the acquired workbook (WP-12)

`read_identified_xlsx_source` implements
[ADR-0015](../../docs/adr/ADR-0015-xlsx-source-identity.md). It acquires one WP-06
snapshot, opens one ZIP inside the lease and reads both the source marker and
WP-05 Raw from that object. It returns only after ZIP/member close, lease integrity
checks and cleanup succeed. It does not invoke the binding resolver, Planner,
requiredness/fiscal evaluators or runtime.

The exact custom property `AccountingBot.SourceIdentity` contains an unlinked
plain text value, for example this synthetic key:

```text
xlsx-source-identity.v1|00000000-0000-7000-8000-0000000003e7|1405
```

The marker is discovered through the unique internal package relationship; its
filename is not assumed. Transitional/Strict namespace families are supported.
The three metadata parts each have a 1,048,576-byte decompressed limit and secure
XML parsing. Linked, ambiguous, malformed or missing markers are rejected;
worksheet row limits and standalone WP-05 compatibility are unchanged.

```python
from pathlib import Path
from accounting_local_agent import read_identified_xlsx_source

# Inputs here must be generated synthetic workbooks for WP-12 validation.
result = read_identified_xlsx_source(
    Path("synthetic/source.xlsx"),
    snapshot_root=Path("synthetic/leases"),
    observation_interval_seconds=0.01,
)
key = result.key
raw_snapshot = result.read_result.snapshot
acquired_hash = result.file_sha256
```

`IdentifiedXlsxSource` retains the exact key/reader-result objects, byte digest and
count; it holds no live path or lease. Its constructor validates representation
only. Neither a manually constructed result nor a copied marker authenticates a
workbook, registers a new annual source or authorizes import/commit.

`XlsxSourceIdentityError` exposes an exact `XlsxSourceIdentityReason` and fixed
`reason_code`; public messages contain no supplied marker/path/Raw. Acquisition
and Raw errors retain their existing taxonomy. For example, a missing
`[Content_Types].xml` can already fail acquisition as source-not-ready, before
marker parsing starts. Independent read/close/lease failures remain in ordered
exception groups, with `BaseExceptionGroup` for cancellation; diagnostic causes
remain available and are not a promise of redacted tracebacks.

WP-12 is read-only and uses synthetic fixtures. Marker writing/enrollment,
real Excel Save/SaveAs/OneDrive retention, durable identity/revision state,
runtime retry integration and rollover remain separate work. G1 remains open.

## Pre-commit and validation checks

```bash
uv lock --check
uv sync --frozen --all-packages --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run mypy --platform win32 .
uv run pytest -v
git diff --check origin/main...HEAD
python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-07-save-import-coordinator/
```
