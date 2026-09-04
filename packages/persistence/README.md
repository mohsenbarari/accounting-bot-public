# accounting-persistence

Persistence layer, database session management, PostgreSQL schemas, Alembic migrations, and local SQLite store.

## Components

### 1. Local Source Import Store (`source-import-store.v1`)

Implements the local SQLite store for source imports under ADR-0018 and WP-15.

- **Connection and Ownership**: Operates entirely on caller-owned, already-open standard library `sqlite3.Connection` instances. The store does not open paths, toggle connection PRAGMAs, close connections, or manage outer connection lifecycles. Foreign keys must be enabled (`PRAGMA foreign_keys = ON;`).
- **Schema Version 1**: Uses `STRICT` SQLite tables, explicit check constraints, foreign-key relationships, and triggers defending against unauthorized deletion or mutation of immutable history:
  - `source_store_meta`: Singleton metadata tracking component version, schema version, generation, next sequence, and device UUID.
  - `source_bindings`: Annual active/archived source registry records with uniqueness and active-source constraints.
  - `source_imports`: Append-only import identities, domain-separated request digests, generation transitions, aggregate counts, and sequence ranges.
  - `source_import_sheets`: Append-only sheet-level snapshots and per-sheet action counts for each import.
  - `source_revisions`: Append-only row revision history with lifecycle state, source hashes, exact WP-14 encoded Raw bytes (for active items), version hashes, and previous-version hash chains.
  - `source_memberships`: Nondeleting source-to-row memberships linking active and voided rows to their visible revision.
  - `change_events`: Append-only local outbox event sequence with canonical `source-change-event.v1` JSON wire payloads.
- **Transaction and Idempotency**:
  - Each import executes within a single `BEGIN IMMEDIATE` transaction.
  - Exact `import_id` retry with matching request digest returns `SourceImportDisposition.REPLAYED` without allocating sequence or modifying database state.
  - Mismatched request digest on existing `import_id` raises `IDEMPOTENCY_CONFLICT`.
  - Mismatched `expected_generation` raises `STALE_STATE`.
  - Every pre-commit failure rolls back all writes cleanly.
- **Security and Boundary**: Designed for local synthetic and mirrored state; does not touch production assets, real databases, or network sync protocols.

### 2. Server Persistence

PostgreSQL schema models, Alembic migrations, and session management for server-side persistence (ADR-0003).
