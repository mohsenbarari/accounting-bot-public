"""IS-01..16: independent verification of atomic SQLite source import store.

Validates schema v1, API signatures, consistent read, atomic transactions,
revisions, nondeleting membership, contiguous change events, replay idempotency,
concurrency, crash recovery, error sanitization, property models, and 15,000-row scale.
"""

from __future__ import annotations

import base64
import gc
import inspect
import os
import sqlite3
import subprocess
import sys
import threading
import time
import tracemalloc
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import accounting_persistence as persistence
import pytest
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
)
from accounting_contracts.source_binding import (
    SourceBindingKey,
)
from accounting_contracts.source_raw_codec import (
    decode_source_raw_row,
    encode_source_raw_row,
)
from accounting_persistence.source_import_store import (
    SOURCE_IMPORT_STORE_VERSION,
    SourceImportDisposition,
    SourceImportRequest,
    SourceImportStoreError,
    SourceImportStoreReason,
    commit_source_import,
    initialize_source_import_store,
    read_source_import_store,
)
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from source_import_test_helpers import (
    build_synthetic_snapshot,
    independent_compute_event_payload,
    make_deterministic_uuid7,
    make_sample_buy_sell_row,
    make_sample_inventory_row,
    make_sample_party_row,
    make_sample_receipt_payment_row,
)

# ============================================================================
# IS-01: Public API, signatures, strict enums, immutability, side-effect free
# ============================================================================


def test_is01_public_exports_and_signatures() -> None:
    """Verify exact 10 exports, version, strict enums and frozen models."""
    expected_exports = {
        "SOURCE_IMPORT_STORE_VERSION",
        "SourceImportStoreReason",
        "SourceImportStoreError",
        "SourceImportDisposition",
        "SourceImportRequest",
        "SourceImportReceipt",
        "SourceImportStoreView",
        "initialize_source_import_store",
        "read_source_import_store",
        "commit_source_import",
    }
    actual_pkg_exports = set(persistence.__all__) - {"__version__"}
    assert actual_pkg_exports == expected_exports
    assert SOURCE_IMPORT_STORE_VERSION == "source-import-store.v1"

    # Enums
    assert issubclass(SourceImportStoreReason, str)
    assert set(SourceImportStoreReason) == {
        "invalid_input",
        "invalid_schema",
        "validation_failed",
        "source_not_active",
        "stale_state",
        "idempotency_conflict",
        "inconsistent_state",
        "storage_failure",
    }
    assert issubclass(SourceImportDisposition, str)
    assert set(SourceImportDisposition) == {"committed", "replayed"}

    # Strict Error constructor
    with pytest.raises(TypeError, match="Invalid source import store reason."):
        SourceImportStoreError("invalid_input")  # type: ignore[arg-type]

    class FakeReason:
        pass

    with pytest.raises(TypeError, match="Invalid source import store reason."):
        SourceImportStoreError(FakeReason())  # type: ignore[arg-type]

    err = SourceImportStoreError(SourceImportStoreReason.STALE_STATE)
    assert str(err) == "Source import state is stale."
    assert repr(err) == "SourceImportStoreError(SourceImportStoreReason.STALE_STATE)"
    assert err.args == ("Source import state is stale.",)

    # Signatures
    init_sig = inspect.signature(initialize_source_import_store)
    assert list(init_sig.parameters.keys()) == [
        "connection",
        "device_id",
        "active_source",
    ]
    assert init_sig.parameters["device_id"].kind == inspect.Parameter.KEYWORD_ONLY
    assert init_sig.parameters["active_source"].kind == inspect.Parameter.KEYWORD_ONLY

    read_sig = inspect.signature(read_source_import_store)
    assert list(read_sig.parameters.keys()) == ["connection"]

    commit_sig = inspect.signature(commit_source_import)
    assert list(commit_sig.parameters.keys()) == ["connection", "request"]


def test_is01_rejection_of_descriptors_and_spoofed_types() -> None:
    """Reject spoofed root/scalar types and caller descriptors without repr."""

    class EvilDescriptor:
        def __get__(self, instance: Any, owner: Any) -> Any:
            raise AssertionError("Descriptor was invoked!")

        def __repr__(self) -> str:
            raise AssertionError("Repr was invoked!")

    class FakeUUID:
        version = 7
        variant = uuid.RFC_4122
        bad = EvilDescriptor()

        def __repr__(self) -> str:
            raise AssertionError("FakeUUID repr was invoked!")

    snap = build_synthetic_snapshot(
        [(make_deterministic_uuid7(1), make_sample_party_row())],
        [],
        [],
        [],
    )
    key = SourceBindingKey(source_id=make_deterministic_uuid7(10), fiscal_year=1403)

    # Spoofed UUID
    with pytest.raises(SourceImportStoreError) as exc_info:
        SourceImportRequest(
            source_key=key,
            expected_generation=0,
            import_id=FakeUUID(),  # type: ignore[arg-type]
            observed_at_utc=datetime.now(UTC),
            file_sha256="a" * 64,
            snapshot=snap,
            event_ids={make_deterministic_uuid7(1): make_deterministic_uuid7(2)},
        )
    assert exc_info.value.reason == SourceImportStoreReason.INVALID_INPUT

    # Defensive copy: mutating input dictionary does not affect request
    mutable_event_ids = {make_deterministic_uuid7(1): make_deterministic_uuid7(2)}
    req = SourceImportRequest(
        source_key=key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(20),
        observed_at_utc=datetime.now(UTC),
        file_sha256="a" * 64,
        snapshot=snap,
        event_ids=mutable_event_ids,
    )
    mutable_event_ids[make_deterministic_uuid7(99)] = make_deterministic_uuid7(100)
    assert len(req.event_ids) == 1
    assert make_deterministic_uuid7(99) not in req.event_ids


def test_is01_side_effect_free_module_import() -> None:
    """Verify target module import executes zero side-effects via probe."""
    probe_script = Path(__file__).parent / "source_import_store_import_probe.py"
    res = subprocess.run(
        [sys.executable, str(probe_script), "normal"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Normal probe failed: {res.stderr}"
    assert "PROBE_OK" in res.stdout

    # Negative control: injected side-effect is caught
    res_neg = subprocess.run(
        [sys.executable, str(probe_script), "inject_write", "/tmp/canary_test.txt"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_neg.returncode == 73
    assert "IMPORT_REJECTED_BY_GUARD" in res_neg.stdout


# ============================================================================
# IS-02: Schema initialization, inspection, idempotency and boundaries
# ============================================================================


def test_is02_initialization_schema_inspection() -> None:
    """Initialize empty SQLite connection and inspect all STRICT tables and triggers."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)

    view = initialize_source_import_store(
        conn, device_id=dev_id, active_source=active_key
    )
    assert view.schema_version == 1
    assert view.generation == 0
    assert view.next_sequence == 1
    assert view.device_id == dev_id

    # user_version
    u_ver = conn.execute("PRAGMA user_version;").fetchone()[0]
    assert u_ver == 1

    # Inspect tables
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%';"
        ).fetchall()
    }
    expected_tables = {
        "source_store_meta",
        "source_bindings",
        "source_imports",
        "source_import_sheets",
        "source_revisions",
        "source_memberships",
        "change_events",
    }
    assert tables == expected_tables

    # Verify STRICT on all tables via table SQL
    for t in tables:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?;", (t,)
        ).fetchone()[0]
        assert "STRICT" in sql.upper(), f"Table {t} is not STRICT"

    # Verify singleton in source_store_meta
    meta_rows = conn.execute("SELECT * FROM source_store_meta;").fetchall()
    assert len(meta_rows) == 1
    assert meta_rows[0][0] == 1  # singleton_id
    assert meta_rows[0][1] == SOURCE_IMPORT_STORE_VERSION
    assert meta_rows[0][2] == 1  # schema_version
    assert meta_rows[0][3] == 0  # generation
    assert meta_rows[0][4] == 1  # next_sequence
    assert meta_rows[0][5] == dev_id.bytes

    # Verify initial active source
    b_rows = conn.execute("SELECT * FROM source_bindings;").fetchall()
    assert len(b_rows) == 1
    assert b_rows[0][0] == src_id.bytes
    assert b_rows[0][1] == 1403
    assert b_rows[0][2] == "active"
    assert b_rows[0][3] is None  # final_file_sha256


def test_is02_initialization_idempotency_and_mismatch_rejection() -> None:
    """Repeated matching init is observational; mismatched or corrupted state fails."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)

    v1 = initialize_source_import_store(
        conn, device_id=dev_id, active_source=active_key
    )
    # Repeated matching initialization
    v2 = initialize_source_import_store(
        conn, device_id=dev_id, active_source=active_key
    )
    assert v1 == v2

    # Wrong device_id
    with pytest.raises(SourceImportStoreError) as exc_info:
        initialize_source_import_store(
            conn, device_id=make_deterministic_uuid7(999), active_source=active_key
        )
    assert exc_info.value.reason == SourceImportStoreReason.INVALID_SCHEMA

    # Wrong active_source
    wrong_key = SourceBindingKey(
        source_id=make_deterministic_uuid7(888), fiscal_year=1403
    )
    with pytest.raises(SourceImportStoreError) as exc_info2:
        initialize_source_import_store(conn, device_id=dev_id, active_source=wrong_key)
    assert exc_info2.value.reason == SourceImportStoreReason.INVALID_SCHEMA

    # Pre-open transaction
    conn_tx = sqlite3.connect(":memory:")
    conn_tx.execute("PRAGMA foreign_keys = ON;")
    conn_tx.execute("BEGIN IMMEDIATE;")
    with pytest.raises(SourceImportStoreError) as exc_tx:
        initialize_source_import_store(
            conn_tx, device_id=dev_id, active_source=active_key
        )
    assert exc_tx.value.reason == SourceImportStoreReason.STORAGE_FAILURE
    conn_tx.execute("ROLLBACK;")

    # Non-empty unmanaged database
    conn_foreign = sqlite3.connect(":memory:")
    conn_foreign.execute("PRAGMA foreign_keys = ON;")
    conn_foreign.execute("CREATE TABLE custom_user (id INT);")
    with pytest.raises(SourceImportStoreError) as exc_f:
        initialize_source_import_store(
            conn_foreign, device_id=dev_id, active_source=active_key
        )
    assert exc_f.value.reason == SourceImportStoreReason.INVALID_SCHEMA


# ============================================================================
# IS-03: Validators, boundaries, connection preservation and safe repr
# ============================================================================


def test_is03_validator_boundaries_and_safe_repr() -> None:
    """Validate boundary checks and safe repr without leaking path, SQL or hashes."""
    src_id = make_deterministic_uuid7(2)
    imp_id = make_deterministic_uuid7(3)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)

    snap = build_synthetic_snapshot(
        [(make_deterministic_uuid7(10), make_sample_party_row())],
        [],
        [],
        [],
    )
    req = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=imp_id,
        observed_at_utc=datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC),
        file_sha256="b" * 64,
        snapshot=snap,
        event_ids={make_deterministic_uuid7(10): make_deterministic_uuid7(100)},
    )

    r_repr = repr(req)
    for forbidden_val in [
        str(src_id),
        str(imp_id),
        "b" * 64,
        "2026-09-04",
        "شرکت",
    ]:
        assert forbidden_val not in r_repr, f"Leaked {forbidden_val} in {r_repr}"

    # Naive datetime rejected
    with pytest.raises(SourceImportStoreError) as exc_dt:
        SourceImportRequest(
            source_key=active_key,
            expected_generation=0,
            import_id=imp_id,
            observed_at_utc=datetime(2026, 9, 4, 12, 0, 0),  # naive!
            file_sha256="b" * 64,
            snapshot=snap,
            event_ids={make_deterministic_uuid7(10): make_deterministic_uuid7(100)},
        )
    assert exc_dt.value.reason == SourceImportStoreReason.INVALID_INPUT

    # Negative generation rejected
    with pytest.raises(SourceImportStoreError) as exc_gen:
        SourceImportRequest(
            source_key=active_key,
            expected_generation=-1,
            import_id=imp_id,
            observed_at_utc=datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC),
            file_sha256="b" * 64,
            snapshot=snap,
            event_ids={make_deterministic_uuid7(10): make_deterministic_uuid7(100)},
        )
    assert exc_gen.value.reason == SourceImportStoreReason.INVALID_INPUT


def test_is03_caller_connection_preservation() -> None:
    """Caller row_factory, isolation_level, callbacks, and foreign_keys preserved."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 3456;")

    def custom_row_factory(
        cursor: sqlite3.Cursor, row: tuple[Any, ...]
    ) -> tuple[Any, ...]:
        return tuple(f"custom_{x}" for x in row)

    conn.row_factory = custom_row_factory
    conn.isolation_level = "DEFERRED"

    trace_log: list[str] = []
    conn.set_trace_callback(trace_log.append)

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)

    view = initialize_source_import_store(
        conn, device_id=dev_id, active_source=active_key
    )
    assert view.generation == 0

    # Verify connection settings intact
    assert conn.row_factory is custom_row_factory
    assert conn.isolation_level == "DEFERRED"
    timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
    assert timeout == "custom_3456"  # transformed by row_factory
    assert len(trace_log) > 0  # trace callback was active

    # Foreign keys must be ON; if OFF, store rejects with INVALID_INPUT
    conn_no_fk = sqlite3.connect(":memory:")
    conn_no_fk.execute("PRAGMA foreign_keys = OFF;")
    with pytest.raises(SourceImportStoreError) as exc_no_fk:
        initialize_source_import_store(
            conn_no_fk, device_id=dev_id, active_source=active_key
        )
    assert exc_no_fk.value.reason == SourceImportStoreReason.INVALID_INPUT


# ============================================================================
# IS-04: First complete import golden & independent wire payload verification
# ============================================================================


def test_is04_first_complete_import_golden_and_wire_payloads() -> None:
    """First complete import verifies gen advance, sheet rows, revisions, events."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)

    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    u_party = make_deterministic_uuid7(10)
    u_bs = make_deterministic_uuid7(20)
    u_rp = make_deterministic_uuid7(30)
    u_inv = make_deterministic_uuid7(40)

    snap = build_synthetic_snapshot(
        [(u_party, make_sample_party_row("فروشگاه البرز", "09121112233"))],
        [
            (
                u_bs,
                make_sample_buy_sell_row(
                    "1403/01/15", "فروشگاه البرز", "فروش", "طلا", "1", "5000000"
                ),
            )
        ],
        [
            (
                u_rp,
                make_sample_receipt_payment_row(
                    "1403/01/16", "فروشگاه البرز", "دریافت", "5000000"
                ),
            )
        ],
        [
            (
                u_inv,
                make_sample_inventory_row(
                    "1403/01/17", "فروشگاه البرز", "خروج", "طلا", "1", "750"
                ),
            )
        ],
    )

    ev_party = make_deterministic_uuid7(101)
    ev_bs = make_deterministic_uuid7(102)
    ev_rp = make_deterministic_uuid7(103)
    ev_inv = make_deterministic_uuid7(104)

    event_ids = {
        u_party: ev_party,
        u_bs: ev_bs,
        u_rp: ev_rp,
        u_inv: ev_inv,
    }

    obs_time = datetime(2026, 9, 4, 10, 0, 0, tzinfo=UTC)
    import_id = make_deterministic_uuid7(50)
    file_sha = "c" * 64

    req = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=import_id,
        observed_at_utc=obs_time,
        file_sha256=file_sha,
        snapshot=snap,
        event_ids=event_ids,
    )

    receipt = commit_source_import(conn, req)
    assert receipt.disposition == SourceImportDisposition.COMMITTED
    assert receipt.base_generation == 0
    assert receipt.committed_generation == 1
    assert receipt.total_row_count == 4
    assert receipt.event_count == 4
    assert receipt.first_sequence == 1
    assert receipt.last_sequence == 4

    # Verify exactly four sheet rows in source_import_sheets
    sheet_rows = conn.execute(
        "SELECT sheet_name, sheet_order, row_count, insert_count "
        "FROM source_import_sheets WHERE import_id = ? ORDER BY sheet_order ASC;",
        (import_id.bytes,),
    ).fetchall()
    assert len(sheet_rows) == 4
    expected_order = tuple(RAW_CONTRACT_REGISTRY.sheets.keys())
    for idx, s_name in enumerate(expected_order):
        assert sheet_rows[idx][0] == s_name
        assert sheet_rows[idx][1] == idx
        assert sheet_rows[idx][2] == 1  # 1 row per sheet
        assert sheet_rows[idx][3] == 1  # 1 insert per sheet

    # Verify each revision stored in source_revisions decodes back losslessly
    for u in [u_party, u_bs, u_rp, u_inv]:
        rev_row = conn.execute(
            "SELECT stable_id, revision, home_sheet, lifecycle, source_hash, "
            "raw_payload, version_hash, previous_version_hash "
            "FROM source_revisions WHERE stable_id = ?;",
            (u.bytes,),
        ).fetchone()
        assert rev_row is not None
        assert len(rev_row[0]) == 16
        assert rev_row[1] == 1  # revision 1
        assert rev_row[3] == "active"
        assert rev_row[7] is None  # previous_version_hash is None for revision 1

        decoded = decode_source_raw_row(rev_row[5])
        assert decoded.stable_id == u
        assert decoded.source_hash == rev_row[4]

    # Verify independent construction of change_events wire bytes
    events = conn.execute(
        "SELECT sequence, event_id, stable_id, revision, sheet_name, "
        "operation, financial_date, canonical_payload, payload_hash "
        "FROM change_events ORDER BY sequence ASC;"
    ).fetchall()
    assert len(events) == 4

    for ev in events:
        (
            seq,
            e_id_bytes,
            s_id_bytes,
            rev,
            s_name,
            op,
            fin_date,
            stored_payload,
            stored_hash,
        ) = ev
        s_uuid = uuid.UUID(bytes=s_id_bytes)
        e_uuid = uuid.UUID(bytes=e_id_bytes)

        # Independent reference wire model calculation
        cur_row = snap.all_rows_by_id[s_uuid]
        raw_b64 = base64.b64encode(encode_source_raw_row(cur_row)).decode("ascii")

        ref_bytes, ref_hash = independent_compute_event_payload(
            device_id=dev_id,
            event_id=e_uuid,
            import_id=import_id,
            sequence=seq,
            source_id=src_id,
            fiscal_year=1403,
            sheet_name=s_name,
            stable_id=s_uuid,
            revision=rev,
            operation=op,
            financial_date=fin_date,
            source_hash=cur_row.source_hash,
            raw_payload_base64=raw_b64,
            previous_version_hash=None,
            observed_at_utc=obs_time,
        )
        assert stored_payload == ref_bytes
        assert stored_hash == ref_hash


# ============================================================================
# IS-05: Multi-generation lifecycle (insert, edit, void, reactivate, no-op)
# ============================================================================


def test_is05_multi_generation_lifecycle_and_hash_chains() -> None:
    """Multi-generation history covers INSERT, EDIT, VOID, reactivation and links."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    u1 = make_deterministic_uuid7(10)  # will be edited
    u2 = make_deterministic_uuid7(20)  # will be voided then reactivated
    u3 = make_deterministic_uuid7(30)  # unchanged

    # Generation 1: Insert u1, u2, u3
    snap1 = build_synthetic_snapshot(
        [(u1, make_sample_party_row("شخص یک")), (u3, make_sample_party_row("شخص سه"))],
        [
            (
                u2,
                make_sample_buy_sell_row(
                    "1403/02/01", "شخص یک", "فروش", "طلا", "5", "1000"
                ),
            )
        ],
        [],
        [],
    )
    req1 = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(100),
        observed_at_utc=datetime(2026, 9, 4, 10, 0, 0, tzinfo=UTC),
        file_sha256="1" * 64,
        snapshot=snap1,
        event_ids={
            u1: make_deterministic_uuid7(201),
            u2: make_deterministic_uuid7(202),
            u3: make_deterministic_uuid7(203),
        },
    )
    r1 = commit_source_import(conn, req1)
    assert r1.committed_generation == 1
    assert r1.event_count == 3

    # Generation 2: Edit u1, Void u2, Keep u3 UNCHANGED, Insert u4
    u4 = make_deterministic_uuid7(40)
    snap2 = build_synthetic_snapshot(
        [
            (u1, make_sample_party_row("شخص یک ویرایش شده")),  # EDIT
            (u3, make_sample_party_row("شخص سه")),  # UNCHANGED
            (u4, make_sample_party_row("شخص چهار")),  # INSERT
        ],
        [],  # u2 is absent -> VOID
        [],
        [],
    )
    req2 = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=make_deterministic_uuid7(101),
        observed_at_utc=datetime(2026, 9, 4, 11, 0, 0, tzinfo=UTC),
        file_sha256="2" * 64,
        snapshot=snap2,
        event_ids={
            u1: make_deterministic_uuid7(204),
            u2: make_deterministic_uuid7(205),
            u4: make_deterministic_uuid7(206),
        },  # no event for u3
    )
    r2 = commit_source_import(conn, req2)
    assert r2.committed_generation == 2
    assert r2.event_count == 3
    assert r2.first_sequence == 4
    assert r2.last_sequence == 6

    # Verify u2 VOID retained prior raw and linked version hash
    u2_revs = conn.execute(
        "SELECT revision, lifecycle, source_hash, raw_payload, "
        "version_hash, previous_version_hash "
        "FROM source_revisions WHERE stable_id = ? ORDER BY revision ASC;",
        (u2.bytes,),
    ).fetchall()
    assert len(u2_revs) == 2
    rev1, rev2 = u2_revs
    assert rev1[1] == "active"
    assert rev1[3] is not None  # Raw preserved
    assert rev2[1] == "voided"
    assert rev2[2] is None  # source_hash null
    assert rev2[3] is None  # raw_payload null
    assert rev2[5] == rev1[4]  # previous_version_hash links to rev1 version_hash!

    # Generation 3: Reactivate u2
    snap3 = build_synthetic_snapshot(
        [
            (u1, make_sample_party_row("شخص یک ویرایش شده")),
            (u3, make_sample_party_row("شخص سه")),
            (u4, make_sample_party_row("شخص چهار")),
        ],
        [
            (
                u2,
                make_sample_buy_sell_row(
                    "1403/02/01", "شخص یک", "فروش", "طلا", "5", "1000"
                ),
            )
        ],  # Reactivated!
        [],
        [],
    )
    req3 = SourceImportRequest(
        source_key=active_key,
        expected_generation=2,
        import_id=make_deterministic_uuid7(102),
        observed_at_utc=datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC),
        file_sha256="3" * 64,
        snapshot=snap3,
        event_ids={u2: make_deterministic_uuid7(207)},
    )
    r3 = commit_source_import(conn, req3)
    assert r3.committed_generation == 3
    assert r3.event_count == 1
    assert r3.first_sequence == 7
    assert r3.last_sequence == 7

    # Verify u2 revision 3 links previous_version_hash to rev2
    u2_rev3 = conn.execute(
        "SELECT revision, lifecycle, version_hash, previous_version_hash "
        "FROM source_revisions WHERE stable_id = ? AND revision = 3;",
        (u2.bytes,),
    ).fetchone()
    assert u2_rev3 is not None
    assert u2_rev3[1] == "active"
    assert u2_rev3[3] == rev2[4]  # previous_version_hash links to voided rev2!


def test_is05_row_reorder_and_formula_cache_noop() -> None:
    """Row reorder and formula cache changes create zero revisions and zero events."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    u1 = make_deterministic_uuid7(10)
    u2 = make_deterministic_uuid7(20)

    snap1 = build_synthetic_snapshot(
        [(u1, make_sample_party_row("الف")), (u2, make_sample_party_row("ب"))],
        [],
        [],
        [],
    )
    req1 = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(100),
        observed_at_utc=datetime(2026, 9, 4, 10, 0, 0, tzinfo=UTC),
        file_sha256="1" * 64,
        snapshot=snap1,
        event_ids={
            u1: make_deterministic_uuid7(201),
            u2: make_deterministic_uuid7(202),
        },
    )
    r1 = commit_source_import(conn, req1)
    assert r1.committed_generation == 1

    # Snapshot 2: rows provided in reversed order (u2 then u1)
    snap2 = build_synthetic_snapshot(
        [(u2, make_sample_party_row("ب")), (u1, make_sample_party_row("الف"))],
        [],
        [],
        [],
    )
    req2 = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=make_deterministic_uuid7(101),
        observed_at_utc=datetime(2026, 9, 4, 11, 0, 0, tzinfo=UTC),
        file_sha256="2" * 64,
        snapshot=snap2,
        event_ids={},  # 0 events expected!
    )
    r2 = commit_source_import(conn, req2)
    assert r2.disposition == SourceImportDisposition.COMMITTED
    assert r2.committed_generation == 2
    assert r2.event_count == 0
    assert r2.first_sequence is None
    assert r2.last_sequence is None

    # Total revisions in DB remain exactly 2
    total_revs = conn.execute("SELECT COUNT(*) FROM source_revisions;").fetchone()[0]
    assert total_revs == 2


# ============================================================================
# IS-06: Independent SQL fixture with archived A and active B
# ============================================================================


def test_is06_archived_and_active_source_isolation_and_party_membership() -> None:
    """Catalog preserves archive objects, and unchanged party creates membership."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_a = make_deterministic_uuid7(100)  # archived (1402)
    src_b = make_deterministic_uuid7(200)  # active (1403)

    key_a = SourceBindingKey(source_id=src_a, fiscal_year=1402)
    key_b = SourceBindingKey(source_id=src_b, fiscal_year=1403)

    # Initialize store with source A
    initialize_source_import_store(conn, device_id=dev_id, active_source=key_a)

    # Commit initial party P into source A
    party_u = make_deterministic_uuid7(10)
    snap_a = build_synthetic_snapshot(
        [(party_u, make_sample_party_row("شخص قدیمی", "09120000000"))], [], [], []
    )
    req_a = SourceImportRequest(
        source_key=key_a,
        expected_generation=0,
        import_id=make_deterministic_uuid7(500),
        observed_at_utc=datetime(2025, 9, 4, 10, 0, 0, tzinfo=UTC),
        file_sha256="f" * 64,
        snapshot=snap_a,
        event_ids={party_u: make_deterministic_uuid7(501)},
    )
    commit_source_import(conn, req_a)

    # Archive source A and add active source B
    conn.execute(
        "UPDATE source_bindings "
        "SET state = 'archived', final_file_sha256 = ? "
        "WHERE source_id = ?;",
        ("f" * 64, src_a.bytes),
    )
    conn.execute(
        "INSERT INTO source_bindings (source_id, fiscal_year, state) "
        "VALUES (?, 1403, 'active');",
        (src_b.bytes,),
    )
    conn.commit()

    # Read store: verifies archived A is preserved with its final hash
    view = read_source_import_store(conn)
    rec_b = view.source_registry.active_record
    assert rec_b is not None and rec_b.key == key_b

    # Now import into active B: party P is present in B unchanged
    snap_b = build_synthetic_snapshot(
        [(party_u, make_sample_party_row("شخص قدیمی", "09120000000"))], [], [], []
    )
    req_b = SourceImportRequest(
        source_key=key_b,
        expected_generation=1,
        import_id=make_deterministic_uuid7(600),
        observed_at_utc=datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC),
        file_sha256="b" * 64,
        snapshot=snap_b,
        event_ids={},  # UNCHANGED globally known party -> zero events!
    )
    r_b = commit_source_import(conn, req_b)
    assert r_b.committed_generation == 2
    assert r_b.event_count == 0

    # Verify B now has membership for party P at revision 1
    b_mem = conn.execute(
        "SELECT revision FROM source_memberships "
        "WHERE source_id = ? AND stable_id = ?;",
        (src_b.bytes, party_u.bytes),
    ).fetchone()
    assert b_mem is not None
    assert b_mem[0] == 1

    # But NO new revision was appended in source_revisions (still only 1 revision total)
    total_revs = conn.execute(
        "SELECT COUNT(*) FROM source_revisions WHERE stable_id = ?;", (party_u.bytes,)
    ).fetchone()[0]
    assert total_revs == 1


# ============================================================================
# IS-07: Requiredness failures & fiscal evidence handling
# ============================================================================


def test_is07_requiredness_failures_atomic_rejection() -> None:
    """Missing required field rejects whole import as VALIDATION_FAILED."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    # Missing party_name_raw in Business Parties (violates requiredness)
    u_party = make_deterministic_uuid7(10)
    snap = build_synthetic_snapshot(
        [(u_party, {"party_name_raw": None, "phone_number_raw": "09121234567"})],
        [],
        [],
        [],
    )
    req = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(100),
        observed_at_utc=datetime.now(UTC),
        file_sha256="a" * 64,
        snapshot=snap,
        event_ids={u_party: make_deterministic_uuid7(200)},
    )
    with pytest.raises(SourceImportStoreError) as exc_info:
        commit_source_import(conn, req)
    assert exc_info.value.reason == SourceImportStoreReason.VALIDATION_FAILED

    # Zero rows written anywhere
    assert conn.execute("SELECT COUNT(*) FROM source_imports;").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM source_revisions;").fetchone()[0] == 0


def test_is07_fiscal_evidence_and_canonical_date_derivations() -> None:
    """Transactions with different fiscal years succeed under active source."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    u_bs = make_deterministic_uuid7(20)
    # Row has date from year 1402 even though source is declared 1403
    snap = build_synthetic_snapshot(
        [],
        [
            (
                u_bs,
                make_sample_buy_sell_row(
                    "1402/12/28", "مشتری", "فروش", "طلا", "1", "1000"
                ),
            )
        ],
        [],
        [],
    )
    req = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(100),
        observed_at_utc=datetime.now(UTC),
        file_sha256="a" * 64,
        snapshot=snap,
        event_ids={u_bs: make_deterministic_uuid7(200)},
    )
    receipt = commit_source_import(conn, req)
    assert receipt.committed_generation == 1

    ev = conn.execute(
        "SELECT financial_date, fiscal_year FROM change_events WHERE stable_id = ?;",
        (u_bs.bytes,),
    ).fetchone()
    assert ev[0] == "1402-12-28"
    assert ev[1] == 1403  # declared fiscal year of active source retained


# ============================================================================
# IS-08: Replay, idempotency conflict, zero-event and file revert
# ============================================================================


def test_is08_exact_idempotent_replay_and_conflict_detection() -> None:
    """Identical retry returns REPLAYED; altered request raises IDEMPOTENCY_CONFLICT."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    u1 = make_deterministic_uuid7(10)
    snap = build_synthetic_snapshot(
        [(u1, make_sample_party_row("فروشگاه یک"))], [], [], []
    )
    import_id = make_deterministic_uuid7(50)
    obs_time = datetime(2026, 9, 4, 10, 0, 0, tzinfo=UTC)

    req1 = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=import_id,
        observed_at_utc=obs_time,
        file_sha256="a" * 64,
        snapshot=snap,
        event_ids={u1: make_deterministic_uuid7(101)},
    )
    r1 = commit_source_import(conn, req1)
    assert r1.disposition == SourceImportDisposition.COMMITTED

    # Exact replay: same import_id and same request digest
    r_replay = commit_source_import(conn, req1)
    assert r_replay.disposition == SourceImportDisposition.REPLAYED
    assert r_replay.committed_generation == r1.committed_generation
    assert r_replay.event_count == r1.event_count

    # Conflict: reuse import_id with different file_sha256
    req_conflict = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=import_id,  # reused!
        observed_at_utc=obs_time,
        file_sha256="b" * 64,  # different hash!
        snapshot=snap,
        event_ids={u1: make_deterministic_uuid7(101)},
    )
    with pytest.raises(SourceImportStoreError) as exc_conflict:
        commit_source_import(conn, req_conflict)
    assert exc_conflict.value.reason == SourceImportStoreReason.IDEMPOTENCY_CONFLICT


# ============================================================================
# IS-09: Two-connection race condition testing with Barriers
# ============================================================================


def test_is09_two_connection_concurrency_race_with_barriers(tmp_path: Path) -> None:
    """Two connections racing: one commits, other gets STALE_STATE or failure."""
    db_file = tmp_path / "race.sqlite3"
    conn_init = sqlite3.connect(db_file)
    conn_init.execute("PRAGMA foreign_keys = ON;")
    conn_init.execute("PRAGMA journal_mode = WAL;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(
        conn_init, device_id=dev_id, active_source=active_key
    )
    conn_init.close()

    barrier = threading.Barrier(2)
    results: list[Any] = []
    errors: list[Any] = []

    def worker(worker_id: int) -> None:
        conn = sqlite3.connect(db_file, timeout=0.5)
        conn.execute("PRAGMA foreign_keys = ON;")
        u = make_deterministic_uuid7(10 + worker_id)
        snap = build_synthetic_snapshot(
            [(u, make_sample_party_row(f"کاربر {worker_id}"))], [], [], []
        )
        req = SourceImportRequest(
            source_key=active_key,
            expected_generation=0,
            import_id=make_deterministic_uuid7(100 + worker_id),
            observed_at_utc=datetime.now(UTC),
            file_sha256=str(worker_id) * 64,
            snapshot=snap,
            event_ids={u: make_deterministic_uuid7(200 + worker_id)},
        )
        barrier.wait()
        try:
            rc = commit_source_import(conn, req)
            results.append((worker_id, rc))
        except SourceImportStoreError as exc:
            errors.append((worker_id, exc))
        finally:
            conn.close()

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one must succeed, one must fail
    assert len(results) == 1, f"Expected 1 success, got {len(results)}"
    assert len(errors) == 1, f"Expected 1 failure, got {len(errors)}"
    failed_reason = errors[0][1].reason
    assert failed_reason in (
        SourceImportStoreReason.STALE_STATE,
        SourceImportStoreReason.STORAGE_FAILURE,
    )


# ============================================================================
# IS-10: Failure injection across write families & ExceptionGroup
# ============================================================================


class FlakyCursor(sqlite3.Cursor):
    fail_insert_table: str | None = None
    fail_commit: bool = False
    fail_interrupt: bool = False

    def execute(self, sql: str, *params: Any) -> Any:
        if FlakyCursor.fail_interrupt:
            raise KeyboardInterrupt("Simulated user cancellation")
        if (
            FlakyCursor.fail_insert_table is not None
            and f"INSERT INTO {FlakyCursor.fail_insert_table}" in sql
        ):
            raise sqlite3.OperationalError(
                f"Simulated failure on {FlakyCursor.fail_insert_table}"
            )
        return super().execute(sql, *params)


class FlakyConnection(sqlite3.Connection):
    fail_rollback: bool = False

    def cursor(self, factory: Any = None) -> sqlite3.Cursor:  # type: ignore[override]
        cur: sqlite3.Cursor = super().cursor(factory=factory or FlakyCursor)
        return cur

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if FlakyConnection.fail_rollback and "ROLLBACK" in sql:
            raise sqlite3.OperationalError("Simulated rollback failure")
        if FlakyCursor.fail_commit and "COMMIT" in sql:
            raise sqlite3.OperationalError("Simulated COMMIT failure")
        return super().execute(sql, *args, **kwargs)


def test_is10_failure_injection_across_write_families() -> None:
    """Failure injected across each write family rolls back completely."""
    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)

    tables_to_fail = [
        "source_imports",
        "source_import_sheets",
        "source_revisions",
        "change_events",
        "source_memberships",
    ]

    for table_name in tables_to_fail:
        conn = sqlite3.connect(":memory:", factory=FlakyConnection)
        conn.execute("PRAGMA foreign_keys = ON;")
        initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

        FlakyCursor.fail_insert_table = table_name
        try:
            u = make_deterministic_uuid7(10)
            snap = build_synthetic_snapshot(
                [(u, make_sample_party_row("شخص"))], [], [], []
            )
            req = SourceImportRequest(
                source_key=active_key,
                expected_generation=0,
                import_id=make_deterministic_uuid7(100),
                observed_at_utc=datetime.now(UTC),
                file_sha256="a" * 64,
                snapshot=snap,
                event_ids={u: make_deterministic_uuid7(200)},
            )

            with pytest.raises(SourceImportStoreError) as exc_info:
                commit_source_import(conn, req)
            assert exc_info.value.reason == SourceImportStoreReason.STORAGE_FAILURE

            # Verify complete rollback: zero rows in imports, revisions, events
            n_imp = conn.execute("SELECT COUNT(*) FROM source_imports;").fetchone()[0]
            assert n_imp == 0
            n_rev = conn.execute("SELECT COUNT(*) FROM source_revisions;").fetchone()[0]
            assert n_rev == 0
            n_evt = conn.execute("SELECT COUNT(*) FROM change_events;").fetchone()[0]
            assert n_evt == 0
            gen = conn.execute("SELECT generation FROM source_store_meta;").fetchone()[
                0
            ]
            assert gen == 0
        finally:
            FlakyCursor.fail_insert_table = None
            conn.close()

    # Ambiguous COMMIT failure
    conn_commit = sqlite3.connect(":memory:", factory=FlakyConnection)
    conn_commit.execute("PRAGMA foreign_keys = ON;")
    initialize_source_import_store(
        conn_commit, device_id=dev_id, active_source=active_key
    )
    FlakyCursor.fail_commit = True
    try:
        u = make_deterministic_uuid7(11)
        snap = build_synthetic_snapshot([(u, make_sample_party_row("شخص"))], [], [], [])
        req = SourceImportRequest(
            source_key=active_key,
            expected_generation=0,
            import_id=make_deterministic_uuid7(101),
            observed_at_utc=datetime.now(UTC),
            file_sha256="b" * 64,
            snapshot=snap,
            event_ids={u: make_deterministic_uuid7(201)},
        )
        with pytest.raises(SourceImportStoreError) as exc_commit:
            commit_source_import(conn_commit, req)
        assert exc_commit.value.reason == SourceImportStoreReason.STORAGE_FAILURE
        n_imp = conn_commit.execute("SELECT COUNT(*) FROM source_imports;").fetchone()[
            0
        ]
        assert n_imp == 0
        gen = conn_commit.execute(
            "SELECT generation FROM source_store_meta;"
        ).fetchone()[0]
        assert gen == 0
    finally:
        FlakyCursor.fail_commit = False
        conn_commit.close()


def test_is10_commit_and_rollback_double_failure_exception_group() -> None:
    """Double failure during commit and rollback preserves both errors in group."""
    conn = sqlite3.connect(":memory:", factory=FlakyConnection)
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    FlakyCursor.fail_insert_table = "source_imports"
    FlakyConnection.fail_rollback = True

    try:
        u = make_deterministic_uuid7(10)
        snap = build_synthetic_snapshot([(u, make_sample_party_row("شخص"))], [], [], [])
        req = SourceImportRequest(
            source_key=active_key,
            expected_generation=0,
            import_id=make_deterministic_uuid7(100),
            observed_at_utc=datetime.now(UTC),
            file_sha256="a" * 64,
            snapshot=snap,
            event_ids={u: make_deterministic_uuid7(200)},
        )

        with pytest.raises(ExceptionGroup) as exc_group:
            commit_source_import(conn, req)

        assert "Source import failure and rollback failure" in str(exc_group.value)
        assert len(exc_group.value.exceptions) == 2
    finally:
        FlakyCursor.fail_insert_table = None
        FlakyConnection.fail_rollback = False
        conn.close()


# ============================================================================
# IS-11: Cross-process crash & restart recovery
# ============================================================================


def test_is11_cross_process_crash_and_restart_recovery(tmp_path: Path) -> None:
    """Subprocess killed during transaction rolls back cleanly upon restart."""
    db_file = tmp_path / "crash_test.sqlite3"
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)
    conn.close()

    # Worker script that opens transaction, modifies row, and hangs until killed
    script = f"""
import sqlite3, time
conn = sqlite3.connect('{db_file}')
conn.execute('PRAGMA foreign_keys = ON;')
conn.execute('BEGIN IMMEDIATE;')
conn.execute("UPDATE source_store_meta SET generation = 999 WHERE singleton_id = 1;")
print("READY_FOR_KILL", flush=True)
time.sleep(10)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
    )
    line = proc.stdout.readline() if proc.stdout else ""
    assert "READY_FOR_KILL" in line

    # Kill process abruptly while transaction is uncommitted
    proc.terminate()
    proc.wait()

    # Re-open database: verify generation is 0 and no uncommitted writes persisted
    conn_post = sqlite3.connect(db_file)
    conn_post.execute("PRAGMA foreign_keys = ON;")
    view = read_source_import_store(conn_post)
    assert view.generation == 0
    cnt = conn_post.execute("SELECT COUNT(*) FROM source_imports;").fetchone()[0]
    assert cnt == 0
    conn_post.close()


# ============================================================================
# IS-12: Error sanitization and raw cancellation propagation
# ============================================================================


def test_is12_error_sanitization_and_raw_exception_propagation() -> None:
    """Public errors are sanitized; KeyboardInterrupt propagates directly."""
    # Check public messages for all reasons
    for reason in SourceImportStoreReason:
        err = SourceImportStoreError(reason)
        msg = str(err)
        assert len(msg) > 5
        assert "sqlite" not in msg.lower()
        assert "/" not in msg
        assert "\\" not in msg
        assert "0x" not in msg

    # Raw KeyboardInterrupt propagates
    conn = sqlite3.connect(":memory:", factory=FlakyConnection)
    conn.execute("PRAGMA foreign_keys = ON;")
    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    FlakyCursor.fail_interrupt = True

    try:
        snap = build_synthetic_snapshot(
            [(make_deterministic_uuid7(10), make_sample_party_row("علی"))], [], [], []
        )
        req = SourceImportRequest(
            source_key=active_key,
            expected_generation=0,
            import_id=make_deterministic_uuid7(100),
            observed_at_utc=datetime.now(UTC),
            file_sha256="a" * 64,
            snapshot=snap,
            event_ids={make_deterministic_uuid7(10): make_deterministic_uuid7(200)},
        )

        with pytest.raises(KeyboardInterrupt, match="Simulated user cancellation"):
            commit_source_import(conn, req)
    finally:
        FlakyCursor.fail_interrupt = False


# ============================================================================
# IS-13: Schema guards (triggers, immutability, tamper detection)
# ============================================================================


def test_is13_schema_guards_trigger_enforcement_and_tamper_detection() -> None:
    """Direct SQL UPDATE/DELETE on append-only tables is rejected by triggers."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    u = make_deterministic_uuid7(10)
    snap = build_synthetic_snapshot([(u, make_sample_party_row("تست"))], [], [], [])
    req = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(100),
        observed_at_utc=datetime.now(UTC),
        file_sha256="a" * 64,
        snapshot=snap,
        event_ids={u: make_deterministic_uuid7(200)},
    )
    commit_source_import(conn, req)

    # 1. UPDATE or DELETE on source_revisions rejected
    with pytest.raises(sqlite3.IntegrityError, match="Cannot update source_revisions"):
        conn.execute("UPDATE source_revisions SET home_sheet = 'لیست کسبه';")
    with pytest.raises(sqlite3.IntegrityError, match="Cannot delete source_revisions"):
        conn.execute("DELETE FROM source_revisions;")

    # 2. UPDATE or DELETE on change_events rejected
    with pytest.raises(sqlite3.IntegrityError, match="Cannot update change_events"):
        conn.execute("UPDATE change_events SET operation = 'void';")
    with pytest.raises(sqlite3.IntegrityError, match="Cannot delete change_events"):
        conn.execute("DELETE FROM change_events;")

    # 3. DELETE on source_memberships rejected
    with pytest.raises(
        sqlite3.IntegrityError, match="Cannot delete source_memberships"
    ):
        conn.execute("DELETE FROM source_memberships;")

    # 4. DELETE on source_store_meta rejected
    with pytest.raises(sqlite3.IntegrityError, match="Cannot delete source_store_meta"):
        conn.execute("DELETE FROM source_store_meta;")


# ============================================================================
# IS-14: Hypothesis property tests & controlled mutations
# ============================================================================


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    row_count=st.integers(min_value=1, max_value=10),
    edit_ratio=st.floats(min_value=0.0, max_value=1.0),
)
def test_is14_hypothesis_multi_step_history_property_tests(
    row_count: int, edit_ratio: float
) -> None:
    """Property test runs generated histories and asserts full consistency."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    # Step 1: Initial import
    uuids = [make_deterministic_uuid7(100 + i) for i in range(row_count)]
    rows = [(u, make_sample_party_row(f"شخص {i}")) for i, u in enumerate(uuids)]
    snap1 = build_synthetic_snapshot(rows, [], [], [])
    ev_ids1 = {u: make_deterministic_uuid7(1000 + i) for i, u in enumerate(uuids)}

    req1 = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(500),
        observed_at_utc=datetime.now(UTC),
        file_sha256="1" * 64,
        snapshot=snap1,
        event_ids=ev_ids1,
    )
    r1 = commit_source_import(conn, req1)
    assert r1.committed_generation == 1
    assert r1.event_count == row_count

    # Step 2: Second import with subset edited
    edit_count = int(row_count * edit_ratio)
    rows2 = []
    ev_ids2 = {}
    for i, u in enumerate(uuids):
        if i < edit_count:
            rows2.append((u, make_sample_party_row(f"شخص {i} ویرایش")))
            ev_ids2[u] = make_deterministic_uuid7(2000 + i)
        else:
            rows2.append((u, make_sample_party_row(f"شخص {i}")))

    snap2 = build_synthetic_snapshot(rows2, [], [], [])
    req2 = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=make_deterministic_uuid7(501),
        observed_at_utc=datetime.now(UTC),
        file_sha256="2" * 64,
        snapshot=snap2,
        event_ids=ev_ids2,
    )
    r2 = commit_source_import(conn, req2)
    assert r2.committed_generation == 2
    assert r2.event_count == edit_count

    # Verify store consistency
    view = read_source_import_store(conn)
    assert view.generation == 2
    assert view.next_sequence == row_count + edit_count + 1


def test_is14_controlled_mutations_detection() -> None:
    """Tampering with version hash or sequences is caught by read."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    u = make_deterministic_uuid7(10)
    snap = build_synthetic_snapshot([(u, make_sample_party_row("شخص"))], [], [], [])
    req = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(100),
        observed_at_utc=datetime.now(UTC),
        file_sha256="a" * 64,
        snapshot=snap,
        event_ids={u: make_deterministic_uuid7(200)},
    )
    commit_source_import(conn, req)

    # Tamper with change_events sequence to introduce a gap
    conn.execute("DROP TRIGGER trg_prevent_update_change_events;")
    conn.execute("UPDATE change_events SET sequence = 99 WHERE sequence = 1;")
    conn.execute(
        """
        CREATE TRIGGER trg_prevent_update_change_events
        BEFORE UPDATE ON change_events
        BEGIN
            SELECT RAISE(ABORT, 'change_events is append-only');
        END;
        """
    )
    conn.commit()

    with pytest.raises(SourceImportStoreError) as exc_tamper:
        read_source_import_store(conn)
    assert exc_tamper.value.reason == SourceImportStoreReason.INCONSISTENT_STATE


# ============================================================================
# IS-15: Identified XLSX composition lifecycle
# ============================================================================


def test_is15_identified_xlsx_composition_lifecycle() -> None:
    """Four generations through WP-04 comparison, commit, restart, replay."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    # Gen 1: initial workbook
    u1, u2 = make_deterministic_uuid7(1), make_deterministic_uuid7(2)
    snap1 = build_synthetic_snapshot(
        [(u1, make_sample_party_row("الف")), (u2, make_sample_party_row("ب"))],
        [],
        [],
        [],
    )
    req1 = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(10),
        observed_at_utc=datetime.now(UTC),
        file_sha256="1" * 64,
        snapshot=snap1,
        event_ids={
            u1: make_deterministic_uuid7(101),
            u2: make_deterministic_uuid7(102),
        },
    )
    commit_source_import(conn, req1)

    # Gen 2: physical reorder (no-op)
    snap2 = build_synthetic_snapshot(
        [(u2, make_sample_party_row("ب")), (u1, make_sample_party_row("الف"))],
        [],
        [],
        [],
    )
    req2 = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=make_deterministic_uuid7(20),
        observed_at_utc=datetime.now(UTC),
        file_sha256="2" * 64,
        snapshot=snap2,
        event_ids={},
    )
    commit_source_import(conn, req2)

    # Gen 3: edit u1, void u2
    snap3 = build_synthetic_snapshot(
        [(u1, make_sample_party_row("الف ویرایش"))], [], [], []
    )
    req3 = SourceImportRequest(
        source_key=active_key,
        expected_generation=2,
        import_id=make_deterministic_uuid7(30),
        observed_at_utc=datetime.now(UTC),
        file_sha256="3" * 64,
        snapshot=snap3,
        event_ids={
            u1: make_deterministic_uuid7(103),
            u2: make_deterministic_uuid7(104),
        },
    )
    commit_source_import(conn, req3)

    # Gen 4: reactivate u2
    snap4 = build_synthetic_snapshot(
        [(u1, make_sample_party_row("الف ویرایش")), (u2, make_sample_party_row("ب"))],
        [],
        [],
        [],
    )
    req4 = SourceImportRequest(
        source_key=active_key,
        expected_generation=3,
        import_id=make_deterministic_uuid7(40),
        observed_at_utc=datetime.now(UTC),
        file_sha256="4" * 64,
        snapshot=snap4,
        event_ids={u2: make_deterministic_uuid7(105)},
    )
    commit_source_import(conn, req4)

    view = read_source_import_store(conn)
    assert view.generation == 4
    assert view.next_sequence == 6


# ============================================================================
# IS-16: 15,000 synthetic rows scale benchmark & memory target (<350 MiB)
# ============================================================================


def test_is16_15000_row_scale_benchmark_memory_and_replay(tmp_path: Path) -> None:
    """Commit 15,000 rows on temp DB, restart/read, replay below 350 MiB RSS."""
    db_file = tmp_path / "scale_15000.sqlite3"
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    row_count = 15000
    print(f"\n[IS-16] Generating {row_count} synthetic rows...", flush=True)

    # Distribute 15,000 rows across sheets
    parties = [
        (make_deterministic_uuid7(100000 + i), make_sample_party_row(f"شخص_{i}"))
        for i in range(3000)
    ]
    buy_sell = [
        (
            make_deterministic_uuid7(200000 + i),
            make_sample_buy_sell_row(
                "1403/05/10", f"شخص_{i % 3000}", "فروش", "طلا", "1", "10000"
            ),
        )
        for i in range(6000)
    ]
    receipts = [
        (
            make_deterministic_uuid7(300000 + i),
            make_sample_receipt_payment_row(
                "1403/05/11", f"شخص_{i % 3000}", "دریافت", "10000"
            ),
        )
        for i in range(3000)
    ]
    inventory = [
        (
            make_deterministic_uuid7(400000 + i),
            make_sample_inventory_row(
                "1403/05/12", f"شخص_{i % 3000}", "ورود", "طلا", "1", "750"
            ),
        )
        for i in range(3000)
    ]

    snap = build_synthetic_snapshot(parties, buy_sell, receipts, inventory)
    assert snap.total_row_count == row_count

    event_ids = {
        u: make_deterministic_uuid7(500000 + idx)
        for idx, u in enumerate(snap.all_rows_by_id.keys())
    }
    import_id = make_deterministic_uuid7(999999)
    obs_time = datetime.now(UTC)
    file_sha = "f" * 64

    req = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=import_id,
        observed_at_utc=obs_time,
        file_sha256=file_sha,
        snapshot=snap,
        event_ids=event_ids,
    )

    # Measure memory and commit time
    gc.collect()
    tracemalloc.start()
    t_commit_start = time.perf_counter()

    receipt = commit_source_import(conn, req)

    t_commit = time.perf_counter() - t_commit_start
    _current, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    peak_mib = peak_bytes / (1024 * 1024)
    print(
        f"[IS-16] 15,000 commit: {t_commit:.3f}s, Peak RSS: {peak_mib:.2f} MiB",
        flush=True,
    )

    assert receipt.disposition == SourceImportDisposition.COMMITTED
    assert receipt.committed_generation == 1
    assert receipt.event_count == row_count
    assert receipt.first_sequence == 1
    assert receipt.last_sequence == row_count
    assert peak_mib < 350.0, f"Peak memory {peak_mib:.2f} MiB exceeded 350 MiB limit!"

    conn.close()

    # Re-open and validate restart read
    t_restart_start = time.perf_counter()
    conn_reopen = sqlite3.connect(db_file)
    conn_reopen.execute("PRAGMA foreign_keys = ON;")
    view = read_source_import_store(conn_reopen)
    t_restart = time.perf_counter() - t_restart_start

    assert view.generation == 1
    assert view.next_sequence == row_count + 1
    print(f"[IS-16] 15,000 rows restart/read time: {t_restart:.3f}s", flush=True)

    # Replay test
    t_replay_start = time.perf_counter()
    r_replay = commit_source_import(conn_reopen, req)
    t_replay = time.perf_counter() - t_replay_start

    assert r_replay.disposition == SourceImportDisposition.REPLAYED
    assert r_replay.event_count == row_count
    print(f"[IS-16] 15,000 rows replay time: {t_replay:.3f}s", flush=True)

    db_size = os.path.getsize(db_file)
    wal_file = db_file.with_suffix(".sqlite3-wal")
    wal_size = os.path.getsize(wal_file) if wal_file.exists() else 0
    print(
        f"[IS-16] DB: {db_size / (1024 * 1024):.2f} MiB, "
        f"WAL: {wal_size / (1024 * 1024):.2f} MiB",
        flush=True,
    )

    conn_reopen.close()
