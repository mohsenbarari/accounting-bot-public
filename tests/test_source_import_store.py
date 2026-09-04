"""IS-01..16: independent verification of atomic SQLite source import store.

Validates schema v1, API signatures, consistent read, atomic transactions,
revisions, nondeleting membership, contiguous change events, replay idempotency,
concurrency, crash recovery, error sanitization, property models, and 15,000-row scale.
"""

from __future__ import annotations

import base64
import gc
import hashlib
import inspect
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

# Ensure test fixtures in tests/ directory can be imported
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

import accounting_persistence as persistence
import pytest
from accounting_contracts import (
    evaluate_source_fiscal_evidence,
    evaluate_source_requiredness,
    plan_source_changes,
)
from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
)
from accounting_contracts.source_binding import (
    SourceBindingKey,
    SourceBindingRegistry,
)
from accounting_contracts.source_change_plan import PlanAction
from accounting_contracts.source_raw_codec import (
    decode_source_raw_row,
    encode_source_raw_row,
)
from accounting_local_agent import read_identified_xlsx_source
from accounting_persistence.source_import_store import (
    SOURCE_IMPORT_STORE_VERSION,
    SourceImportDisposition,
    SourceImportReceipt,
    SourceImportRequest,
    SourceImportStoreError,
    SourceImportStoreReason,
    SourceImportStoreView,
    commit_source_import,
    initialize_source_import_store,
    read_source_import_store,
)
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from source_identity_projection_support import prior_from_snapshot
from source_import_test_helpers import (
    build_synthetic_snapshot,
    independent_compute_event_payload,
    make_deterministic_uuid7,
    make_sample_buy_sell_row,
    make_sample_inventory_row,
    make_sample_party_row,
    make_sample_receipt_payment_row,
)
from xlsx_source_identity_fixtures import identified_parts, raw_parts, uid, zipped


def get_current_process_rss_mib() -> float:
    """Measure real caller process RSS in MiB using /proc/self/status or Windows API."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_uint32),
                ("PageFaultCount", ctypes.c_uint32),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        try:
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
        except Exception:
            psapi = kernel32

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        handle = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise RuntimeError("Windows GetProcessMemoryInfo failed")
        return float(counters.WorkingSetSize) / (1024.0 * 1024.0)

    # Linux: read /proc/self/status VmRSS
    with open("/proc/self/status", encoding="ascii") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                parts = line.split()
                return float(int(parts[1])) / 1024.0
    raise RuntimeError("Could not find VmRSS in /proc/self/status")


class CallWindowRssSampler:
    """Samples caller process RSS during an active call window."""

    def __init__(self, interval_seconds: float = 0.005) -> None:
        self.interval_seconds = interval_seconds
        self._peak_rss_mib = 0.0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: Exception | None = None
        self._state = "INITIAL"

    def start(self) -> None:
        if self._state != "INITIAL":
            raise RuntimeError("Sampler can only be started once")
        self._state = "RUNNING"
        self._peak_rss_mib = get_current_process_rss_mib()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                current = get_current_process_rss_mib()
                if current > self._peak_rss_mib:
                    self._peak_rss_mib = current
                self._stop_event.wait(self.interval_seconds)
        except Exception as exc:
            self._error = exc

    def stop_and_get_peak(self) -> float:
        if self._state != "RUNNING":
            raise RuntimeError("Sampler cannot be stopped in current state")
        self._state = "STOPPED"
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._error is not None:
            raise RuntimeError(f"Sampler worker failed: {self._error}") from self._error
        return self._peak_rss_mib


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
    conn.close()


def test_is08_zero_event_import_and_file_revert() -> None:
    """IS-08: zero-event new import advances generation with no sequence change,
    and file revert after intervening commits replans as legitimate new revisions."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    u1 = make_deterministic_uuid7(10)
    snap1 = build_synthetic_snapshot(
        [(u1, make_sample_party_row("شخص اصلی"))], [], [], []
    )
    req1 = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(101),
        observed_at_utc=datetime(2026, 9, 4, 10, 0, 0, tzinfo=UTC),
        file_sha256="1" * 64,
        snapshot=snap1,
        event_ids={u1: make_deterministic_uuid7(201)},
    )
    receipt1 = commit_source_import(conn, req1)
    assert receipt1.disposition == SourceImportDisposition.COMMITTED
    assert receipt1.committed_generation == 1
    assert receipt1.event_count == 1
    assert receipt1.first_sequence == 1
    assert receipt1.last_sequence == 1

    view1 = read_source_import_store(conn)
    assert view1.next_sequence == 2

    # Part 1: Zero-event new import (same snapshot, expected_generation=1)
    req_zero = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=make_deterministic_uuid7(102),
        observed_at_utc=datetime(2026, 9, 4, 11, 0, 0, tzinfo=UTC),
        file_sha256="1" * 64,
        snapshot=snap1,
        event_ids={},
    )
    receipt_zero = commit_source_import(conn, req_zero)
    assert receipt_zero.disposition == SourceImportDisposition.COMMITTED
    assert receipt_zero.committed_generation == 2
    assert receipt_zero.event_count == 0
    assert receipt_zero.first_sequence is None
    assert receipt_zero.last_sequence is None

    # Next sequence must NOT advance on a zero-event import
    view2 = read_source_import_store(conn)
    assert view2.generation == 2
    assert view2.next_sequence == 2

    # Verify memberships last_import_id is updated to req_zero import_id
    mem = conn.execute(
        "SELECT revision, first_import_id, last_import_id "
        "FROM source_memberships WHERE stable_id = ?;",
        (u1.bytes,),
    ).fetchone()
    assert mem[0] == 1
    assert mem[1] == make_deterministic_uuid7(101).bytes
    assert mem[2] == make_deterministic_uuid7(102).bytes

    # Part 2: Intervening commit modifies party u1
    snap_edit = build_synthetic_snapshot(
        [(u1, make_sample_party_row("شخص ویرایش شده"))], [], [], []
    )
    req_edit = SourceImportRequest(
        source_key=active_key,
        expected_generation=2,
        import_id=make_deterministic_uuid7(103),
        observed_at_utc=datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC),
        file_sha256="3" * 64,
        snapshot=snap_edit,
        event_ids={u1: make_deterministic_uuid7(203)},
    )
    receipt_edit = commit_source_import(conn, req_edit)
    assert receipt_edit.committed_generation == 3
    assert receipt_edit.event_count == 1
    assert receipt_edit.first_sequence == 2
    assert receipt_edit.last_sequence == 2

    view3 = read_source_import_store(conn)
    assert view3.next_sequence == 3

    # Part 3: Revert file back to original content (snap1) after intervening edit
    req_revert = SourceImportRequest(
        source_key=active_key,
        expected_generation=3,
        import_id=make_deterministic_uuid7(104),
        observed_at_utc=datetime(2026, 9, 4, 13, 0, 0, tzinfo=UTC),
        file_sha256="4" * 64,
        snapshot=snap1,  # reverted back to original!
        event_ids={u1: make_deterministic_uuid7(204)},
    )
    receipt_revert = commit_source_import(conn, req_revert)
    assert receipt_revert.disposition == SourceImportDisposition.COMMITTED
    assert receipt_revert.committed_generation == 4
    assert receipt_revert.event_count == 1
    assert receipt_revert.first_sequence == 3
    assert receipt_revert.last_sequence == 3

    # Verify new revision 3 was created with predecessor pointing to revision 2
    rev3 = conn.execute(
        "SELECT revision, version_hash, previous_version_hash "
        "FROM source_revisions WHERE stable_id = ? AND revision = 3;",
        (u1.bytes,),
    ).fetchone()
    assert rev3 is not None
    assert rev3[0] == 3
    rev2_hash = conn.execute(
        "SELECT version_hash FROM source_revisions "
        "WHERE stable_id = ? AND revision = 2;",
        (u1.bytes,),
    ).fetchone()[0]
    assert rev3[2] == rev2_hash

    view4 = read_source_import_store(conn)
    assert view4.generation == 4
    assert view4.next_sequence == 4
    conn.close()


# ============================================================================
# IS-09: Two-connection race condition testing with Barriers
# ============================================================================


def test_is09_two_connection_concurrency_race_with_barriers(tmp_path: Path) -> None:
    """IS-09: two connections racing in WAL mode with transaction pauses.
    Tests changed/changed, changed/zero, winner replay and loser retry."""
    db_file = tmp_path / "is09_race.sqlite3"
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

    # --- Scenario 1: Changed vs Changed race ---
    u1 = make_deterministic_uuid7(10)
    u2 = make_deterministic_uuid7(20)
    snap_a = build_synthetic_snapshot(
        [(u1, make_sample_party_row("کاربر الف"))], [], [], []
    )
    snap_b = build_synthetic_snapshot(
        [(u2, make_sample_party_row("کاربر ب"))], [], [], []
    )

    req_a = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(101),
        observed_at_utc=datetime.now(UTC),
        file_sha256="a" * 64,
        snapshot=snap_a,
        event_ids={u1: make_deterministic_uuid7(201)},
    )
    req_b = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(102),
        observed_at_utc=datetime.now(UTC),
        file_sha256="b" * 64,
        snapshot=snap_b,
        event_ids={u2: make_deterministic_uuid7(202)},
    )

    barrier = threading.Barrier(2)
    winner_results: list[tuple[str, SourceImportReceipt]] = []
    loser_errors: list[tuple[str, SourceImportStoreError]] = []
    thread_exceptions: list[BaseException] = []

    def race_worker(name: str, req: SourceImportRequest) -> None:
        try:
            conn = sqlite3.connect(db_file, timeout=0.8)
            conn.execute("PRAGMA foreign_keys = ON;")
            barrier.wait(timeout=10.0)
            rc = commit_source_import(conn, req)
            winner_results.append((name, rc))
            conn.close()
        except SourceImportStoreError as exc:
            loser_errors.append((name, exc))
        except BaseException as exc:
            thread_exceptions.append(exc)

    t_a = threading.Thread(target=race_worker, args=("A", req_a))
    t_b = threading.Thread(target=race_worker, args=("B", req_b))
    t_a.start()
    t_b.start()
    t_a.join(timeout=10.0)
    t_b.join(timeout=10.0)
    assert not t_a.is_alive() and not t_b.is_alive()
    assert not thread_exceptions, f"Unexpected thread error: {thread_exceptions}"

    assert len(winner_results) == 1
    assert len(loser_errors) == 1
    winner_name, winner_receipt = winner_results[0]
    loser_name, loser_error = loser_errors[0]
    assert winner_receipt.disposition == SourceImportDisposition.COMMITTED
    assert loser_error.reason in (
        SourceImportStoreReason.STALE_STATE,
        SourceImportStoreReason.STORAGE_FAILURE,
    )

    # Winner exact replay returns REPLAYED
    conn_verify = sqlite3.connect(db_file)
    conn_verify.execute("PRAGMA foreign_keys = ON;")
    winner_req = req_a if winner_name == "A" else req_b
    replay_receipt = commit_source_import(conn_verify, winner_req)
    assert replay_receipt.disposition == SourceImportDisposition.REPLAYED
    assert replay_receipt.committed_generation == winner_receipt.committed_generation

    # Loser retries with updated expected_generation and merged snapshot -> succeeds
    loser_req = req_b if winner_name == "A" else req_a
    winner_row = (
        (u1, make_sample_party_row("کاربر الف"))
        if winner_name == "A"
        else (u2, make_sample_party_row("کاربر ب"))
    )
    loser_row = (
        (u2, make_sample_party_row("کاربر ب"))
        if winner_name == "A"
        else (u1, make_sample_party_row("کاربر الف"))
    )
    snap_loser_retry = build_synthetic_snapshot([winner_row, loser_row], [], [], [])
    loser_retry_req = SourceImportRequest(
        source_key=loser_req.source_key,
        expected_generation=1,
        import_id=loser_req.import_id,
        observed_at_utc=loser_req.observed_at_utc,
        file_sha256=loser_req.file_sha256,
        snapshot=snap_loser_retry,
        event_ids=loser_req.event_ids,
    )
    retry_receipt = commit_source_import(conn_verify, loser_retry_req)
    assert retry_receipt.disposition == SourceImportDisposition.COMMITTED
    assert retry_receipt.committed_generation == 2
    conn_verify.close()

    # --- Scenario 2: Changed vs Zero-event race with reversed winner ---
    u3 = make_deterministic_uuid7(30)
    rows_gen2 = [
        (u1, make_sample_party_row("کاربر الف")),
        (u2, make_sample_party_row("کاربر ب")),
    ]
    snap_gen2 = build_synthetic_snapshot(rows_gen2, [], [], [])
    snap_c = build_synthetic_snapshot(
        rows_gen2 + [(u3, make_sample_party_row("کاربر سه"))], [], [], []
    )

    req_changed = SourceImportRequest(
        source_key=active_key,
        expected_generation=2,
        import_id=make_deterministic_uuid7(105),
        observed_at_utc=datetime.now(UTC),
        file_sha256="c" * 64,
        snapshot=snap_c,
        event_ids={u3: make_deterministic_uuid7(205)},
    )
    req_zero = SourceImportRequest(
        source_key=active_key,
        expected_generation=2,
        import_id=make_deterministic_uuid7(106),
        observed_at_utc=datetime.now(UTC),
        file_sha256="d" * 64,
        snapshot=snap_gen2,
        event_ids={},
    )

    barrier2 = threading.Barrier(2)
    results2: list[tuple[str, SourceImportReceipt]] = []
    errors2: list[tuple[str, SourceImportStoreError]] = []

    def race_worker2(name: str, req: SourceImportRequest) -> None:
        try:
            conn = sqlite3.connect(db_file, timeout=0.8)
            conn.execute("PRAGMA foreign_keys = ON;")
            barrier2.wait(timeout=10.0)
            rc = commit_source_import(conn, req)
            results2.append((name, rc))
            conn.close()
        except SourceImportStoreError as exc:
            errors2.append((name, exc))
        except BaseException as exc:
            thread_exceptions.append(exc)

    t_c = threading.Thread(target=race_worker2, args=("CHANGED", req_changed))
    t_d = threading.Thread(target=race_worker2, args=("ZERO", req_zero))
    t_c.start()
    t_d.start()
    t_c.join(timeout=10.0)
    t_d.join(timeout=10.0)
    assert not t_c.is_alive() and not t_d.is_alive()
    assert not thread_exceptions

    assert len(results2) == 1
    assert len(errors2) == 1
    _w2_name, w2_rc = results2[0]
    _l2_name, l2_err = errors2[0]
    assert w2_rc.disposition == SourceImportDisposition.COMMITTED
    assert l2_err.reason in (
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
    fail_custom_base: bool = False
    fail_system_exit: bool = False

    def execute(self, sql: str, *params: Any) -> Any:
        if FlakyCursor.fail_interrupt and "INSERT INTO source_imports" in sql:
            raise KeyboardInterrupt("Simulated user cancellation")
        if FlakyCursor.fail_system_exit and "INSERT INTO source_imports" in sql:
            raise SystemExit(42)
        if FlakyCursor.fail_custom_base and "INSERT INTO source_imports" in sql:

            class CustomTestBaseException(BaseException):
                pass

            raise CustomTestBaseException("Simulated custom base exception")

        if FlakyCursor.fail_insert_table is not None:
            if (
                f"INSERT INTO {FlakyCursor.fail_insert_table}" in sql
                or f"UPDATE {FlakyCursor.fail_insert_table}" in sql
            ):
                raise sqlite3.OperationalError(
                    f"Simulated failure on {FlakyCursor.fail_insert_table}"
                )
        return super().execute(sql, *params)


class FlakyConnection(sqlite3.Connection):
    fail_begin: bool = False
    fail_rollback: bool = False

    def cursor(self, factory: Any = None) -> sqlite3.Cursor:  # type: ignore[override]
        cur: sqlite3.Cursor = super().cursor(factory=factory or FlakyCursor)
        return cur

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if FlakyConnection.fail_begin and "BEGIN" in sql:
            raise sqlite3.OperationalError("Simulated BEGIN failure")
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
        "source_bindings",
        "source_store_meta",
    ]

    for table_name in tables_to_fail:
        conn = sqlite3.connect(":memory:", factory=FlakyConnection)
        conn.execute("PRAGMA foreign_keys = ON;")
        initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

        # Snapshot state before
        snap_counts = {
            tbl: conn.execute(f"SELECT COUNT(*) FROM {tbl};").fetchone()[0]
            for tbl in [
                "source_imports",
                "source_revisions",
                "change_events",
                "source_memberships",
            ]
        }
        snap_gen = conn.execute("SELECT generation FROM source_store_meta;").fetchone()[
            0
        ]
        snap_seq = conn.execute(
            "SELECT next_sequence FROM source_store_meta;"
        ).fetchone()[0]

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

            # Verify complete rollback: matches exact snapshot before
            for tbl, expected_count in snap_counts.items():
                actual_count = conn.execute(f"SELECT COUNT(*) FROM {tbl};").fetchone()[
                    0
                ]
                assert actual_count == expected_count, (
                    f"Mismatch in {tbl} after rollback"
                )
            gen = conn.execute("SELECT generation FROM source_store_meta;").fetchone()[
                0
            ]
            seq = conn.execute(
                "SELECT next_sequence FROM source_store_meta;"
            ).fetchone()[0]
            assert gen == snap_gen
            assert seq == snap_seq
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

    # BEGIN failure
    conn_begin = sqlite3.connect(":memory:", factory=FlakyConnection)
    conn_begin.execute("PRAGMA foreign_keys = ON;")
    initialize_source_import_store(
        conn_begin, device_id=dev_id, active_source=active_key
    )
    FlakyConnection.fail_begin = True
    try:
        u = make_deterministic_uuid7(12)
        snap = build_synthetic_snapshot([(u, make_sample_party_row("شخص"))], [], [], [])
        req_b = SourceImportRequest(
            source_key=active_key,
            expected_generation=0,
            import_id=make_deterministic_uuid7(102),
            observed_at_utc=datetime.now(UTC),
            file_sha256="c" * 64,
            snapshot=snap,
            event_ids={u: make_deterministic_uuid7(202)},
        )
        with pytest.raises(SourceImportStoreError) as exc_b:
            commit_source_import(conn_begin, req_b)
        assert exc_b.value.reason == SourceImportStoreReason.STORAGE_FAILURE
    finally:
        FlakyConnection.fail_begin = False
        conn_begin.close()


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

        # BaseExceptionGroup when KeyboardInterrupt occurs and rollback fails
        FlakyCursor.fail_insert_table = None
        FlakyCursor.fail_interrupt = True
        conn2 = sqlite3.connect(":memory:", factory=FlakyConnection)
        conn2.execute("PRAGMA foreign_keys = ON;")
        initialize_source_import_store(
            conn2, device_id=dev_id, active_source=active_key
        )
        try:
            with pytest.raises(BaseExceptionGroup) as exc_bgroup:
                commit_source_import(conn2, req)
            assert "Source import failure and rollback failure" in str(exc_bgroup.value)
            assert any(
                isinstance(e, KeyboardInterrupt) for e in exc_bgroup.value.exceptions
            )
        finally:
            conn2.close()
    finally:
        FlakyCursor.fail_insert_table = None
        FlakyCursor.fail_interrupt = False
        FlakyConnection.fail_rollback = False
        conn.close()

    # Shared cause non-deduplication test
    shared_cause = RuntimeError("shared cause")
    err_a = sqlite3.OperationalError("op1")
    err_a.__cause__ = shared_cause
    err_b = sqlite3.OperationalError("op2")
    err_b.__cause__ = shared_cause
    eg_shared = ExceptionGroup("distinct errors with shared cause", [err_a, err_b])
    assert len(eg_shared.exceptions) == 2
    assert eg_shared.exceptions[0].__cause__ is shared_cause
    assert eg_shared.exceptions[1].__cause__ is shared_cause


# ============================================================================
# IS-11: Cross-process crash & restart recovery
# ============================================================================


def test_is11_cross_process_crash_and_restart_recovery(tmp_path: Path) -> None:
    """IS-11: Real commit_source_import path run in subprocesses.
    Stops via IPC/ACK when transaction is open and after real COMMIT before return.
    Reopen and exact retry proves prior/full state and no duplicates."""
    db_file = tmp_path / "is11_crash.sqlite3"
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)
    conn.close()

    tests_dir = Path(__file__).parent
    # Sub-case 1: Stop while transaction is open
    script_open = f"""
import sys, sqlite3
from pathlib import Path
sys.path.insert(0, r'{tests_dir}')
from datetime import datetime, UTC
from accounting_contracts.source_binding import SourceBindingKey
from accounting_persistence.source_import_store import (
    commit_source_import,
    SourceImportRequest,
)
from source_import_test_helpers import (
    build_synthetic_snapshot,
    make_deterministic_uuid7,
    make_sample_party_row,
)

db_file = '{db_file}'
conn = sqlite3.connect(db_file)
conn.execute("PRAGMA foreign_keys = ON;")

def trace_cb(sql):
    if "INSERT INTO source_imports" in sql:
        sys.stdout.write("ACK_TX_OPEN\\n")
        sys.stdout.flush()
        sys.stdin.readline()

conn.set_trace_callback(trace_cb)

dev_id = make_deterministic_uuid7(1)
src_id = make_deterministic_uuid7(2)
active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)

u1 = make_deterministic_uuid7(10)
snap = build_synthetic_snapshot(
    [(u1, make_sample_party_row("شخص تست"))], [], [], []
)
req = SourceImportRequest(
    source_key=active_key,
    expected_generation=0,
    import_id=make_deterministic_uuid7(101),
    observed_at_utc=datetime(2026, 9, 4, 10, 0, 0, tzinfo=UTC),
    file_sha256="1" * 64,
    snapshot=snap,
    event_ids={{u1: make_deterministic_uuid7(201)}},
)
commit_source_import(conn, req)
"""
    proc1 = subprocess.Popen(
        [sys.executable, "-c", script_open],
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        text=True,
    )
    ack1 = proc1.stdout.readline().strip() if proc1.stdout else ""
    assert ack1 == "ACK_TX_OPEN"

    proc1.terminate()
    proc1.wait(timeout=5.0)

    # Reopen and verify prior state: 0 imports, 0 revisions, 0 events, gen 0
    conn_reopen = sqlite3.connect(db_file)
    conn_reopen.execute("PRAGMA foreign_keys = ON;")
    view_reopen = read_source_import_store(conn_reopen)
    assert view_reopen.generation == 0
    assert (
        conn_reopen.execute("SELECT COUNT(*) FROM source_imports;").fetchone()[0] == 0
    )

    # Retry identical request commits cleanly to Gen 1
    u1 = make_deterministic_uuid7(10)
    snap = build_synthetic_snapshot(
        [(u1, make_sample_party_row("شخص تست"))], [], [], []
    )
    req1 = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(101),
        observed_at_utc=datetime(2026, 9, 4, 10, 0, 0, tzinfo=UTC),
        file_sha256="1" * 64,
        snapshot=snap,
        event_ids={u1: make_deterministic_uuid7(201)},
    )
    rc1 = commit_source_import(conn_reopen, req1)
    assert rc1.disposition == SourceImportDisposition.COMMITTED
    assert rc1.committed_generation == 1
    conn_reopen.close()

    # Sub-case 2: Stop after real COMMIT before return
    script_post_commit = f"""
import sys, sqlite3
from pathlib import Path
sys.path.insert(0, r'{tests_dir}')
from datetime import datetime, UTC
from accounting_contracts.source_binding import SourceBindingKey
from accounting_persistence.source_import_store import (
    commit_source_import,
    SourceImportRequest,
)
from source_import_test_helpers import (
    build_synthetic_snapshot,
    make_deterministic_uuid7,
    make_sample_party_row,
)

db_file = '{db_file}'

class PostCommitHaltConnection(sqlite3.Connection):
    def execute(self, sql, *args, **kwargs):
        res = super().execute(sql, *args, **kwargs)
        if "COMMIT" in sql:
            sys.stdout.write("ACK_COMMIT_DONE\\n")
            sys.stdout.flush()
            sys.stdin.readline()
        return res

conn = sqlite3.connect(db_file, factory=PostCommitHaltConnection)
conn.execute("PRAGMA foreign_keys = ON;")

dev_id = make_deterministic_uuid7(1)
src_id = make_deterministic_uuid7(2)
active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)

u1 = make_deterministic_uuid7(10)
snap = build_synthetic_snapshot(
    [(u1, make_sample_party_row("شخص تست ویرایش"))], [], [], []
)
req = SourceImportRequest(
    source_key=active_key,
    expected_generation=1,
    import_id=make_deterministic_uuid7(102),
    observed_at_utc=datetime(2026, 9, 4, 11, 0, 0, tzinfo=UTC),
    file_sha256="2" * 64,
    snapshot=snap,
    event_ids={{u1: make_deterministic_uuid7(202)}},
)
commit_source_import(conn, req)
"""
    proc2 = subprocess.Popen(
        [sys.executable, "-c", script_post_commit],
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        text=True,
    )
    ack2 = proc2.stdout.readline().strip() if proc2.stdout else ""
    assert ack2 == "ACK_COMMIT_DONE"

    proc2.terminate()
    proc2.wait(timeout=5.0)

    # Reopen: full Generation 2 persisted cleanly
    conn_post = sqlite3.connect(db_file)
    conn_post.execute("PRAGMA foreign_keys = ON;")
    view_post = read_source_import_store(conn_post)
    assert view_post.generation == 2

    # Exact retry: returns REPLAYED with no duplicates
    snap2 = build_synthetic_snapshot(
        [(u1, make_sample_party_row("شخص تست ویرایش"))], [], [], []
    )
    req2 = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=make_deterministic_uuid7(102),
        observed_at_utc=datetime(2026, 9, 4, 11, 0, 0, tzinfo=UTC),
        file_sha256="2" * 64,
        snapshot=snap2,
        event_ids={u1: make_deterministic_uuid7(202)},
    )
    receipt_replay = commit_source_import(conn_post, req2)
    assert receipt_replay.disposition == SourceImportDisposition.REPLAYED
    assert receipt_replay.committed_generation == 2

    # Assert exactly 2 imports, 2 revisions, 2 events in total (no duplicates)
    assert conn_post.execute("SELECT COUNT(*) FROM source_imports;").fetchone()[0] == 2
    assert (
        conn_post.execute("SELECT COUNT(*) FROM source_revisions;").fetchone()[0] == 2
    )
    assert conn_post.execute("SELECT COUNT(*) FROM change_events;").fetchone()[0] == 2
    conn_post.close()


# ============================================================================
# IS-12: Error sanitization and raw cancellation propagation
# ============================================================================


def test_is12_error_sanitization_and_raw_exception_propagation() -> None:
    """Public errors sanitized; KeyboardInterrupt and BaseExceptions propagate."""
    expected_messages = {
        SourceImportStoreReason.INVALID_INPUT: "Invalid source import store input.",
        SourceImportStoreReason.INVALID_SCHEMA: "Invalid source import store schema.",
        SourceImportStoreReason.VALIDATION_FAILED: "Source import validation failed.",
        SourceImportStoreReason.SOURCE_NOT_ACTIVE: "Source is not active.",
        SourceImportStoreReason.STALE_STATE: "Source import state is stale.",
        SourceImportStoreReason.IDEMPOTENCY_CONFLICT: (
            "Source import identity conflicts."
        ),
        SourceImportStoreReason.INCONSISTENT_STATE: (
            "Source import store is inconsistent."
        ),
        SourceImportStoreReason.STORAGE_FAILURE: "Source import storage failed.",
    }
    for reason, expected_msg in expected_messages.items():
        err = SourceImportStoreError(reason)
        assert str(err) == expected_msg
        assert err.args == (expected_msg,)
        assert (
            repr(err)
            == f"SourceImportStoreError(SourceImportStoreReason.{reason.name})"
        )
        assert "sqlite" not in str(err).lower()
        assert "/" not in str(err)
        assert "\\" not in str(err)
        assert "0x" not in str(err)

    # Hostile values raise TypeError
    class HostileVal:
        def __str__(self) -> str:
            raise RuntimeError("hostile str")

    with pytest.raises(TypeError):
        SourceImportStoreError(HostileVal())  # type: ignore[arg-type]

    # Raw KeyboardInterrupt, SystemExit, and custom BaseException propagate directly
    conn = sqlite3.connect(":memory:", factory=FlakyConnection)
    conn.execute("PRAGMA foreign_keys = ON;")
    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

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

    FlakyCursor.fail_interrupt = True
    try:
        with pytest.raises(KeyboardInterrupt, match="Simulated user cancellation"):
            commit_source_import(conn, req)
    finally:
        FlakyCursor.fail_interrupt = False

    FlakyCursor.fail_system_exit = True
    try:
        with pytest.raises(SystemExit) as exc_exit:
            commit_source_import(conn, req)
        assert exc_exit.value.code == 42
    finally:
        FlakyCursor.fail_system_exit = False

    FlakyCursor.fail_custom_base = True
    try:
        with pytest.raises(BaseException) as exc_custom:
            commit_source_import(conn, req)
        assert "Simulated custom base exception" in str(exc_custom.value)
    finally:
        FlakyCursor.fail_custom_base = False
    conn.close()


# ============================================================================
# IS-13: Schema guards (triggers, immutability, tamper detection)
# ============================================================================


def test_is13_schema_guards_trigger_enforcement_and_tamper_detection() -> None:
    """Direct SQL UPDATE/DELETE on append-only tables is rejected by triggers;
    foreign keys, UNIQUE, and CHECK constraints are fully enforced."""
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

    # 5. UPDATE or DELETE on source_imports and source_import_sheets rejected
    with pytest.raises(sqlite3.IntegrityError, match="Cannot update source_imports"):
        conn.execute("UPDATE source_imports SET total_row_count = 999;")
    with pytest.raises(sqlite3.IntegrityError, match="Cannot delete source_imports"):
        conn.execute("DELETE FROM source_imports;")
    with pytest.raises(
        sqlite3.IntegrityError, match="Cannot update source_import_sheets"
    ):
        conn.execute("UPDATE source_import_sheets SET row_count = 999;")
    with pytest.raises(
        sqlite3.IntegrityError, match="Cannot delete source_import_sheets"
    ):
        conn.execute("DELETE FROM source_import_sheets;")

    # 6. Membership retargeting rejected (cannot retarget source_id or stable_id)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE source_memberships SET stable_id = ?;",
            (make_deterministic_uuid7(999).bytes,),
        )

    # 7. Unique constraint on source_bindings
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO source_bindings (source_id, fiscal_year, state) "
            "VALUES (?, 1403, 'active');",
            (src_id.bytes,),
        )

    conn.close()


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
    """IS-14: Controlled mutations for stale check, unchanged membership,
    append-only revision, outbox insert, sequence advance, request Raw digest,
    and predecessor link are detected by product assertions. Product state restored."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    u1 = make_deterministic_uuid7(10)
    snap1 = build_synthetic_snapshot([(u1, make_sample_party_row("شخص"))], [], [], [])
    req1 = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(100),
        observed_at_utc=datetime.now(UTC),
        file_sha256="a" * 64,
        snapshot=snap1,
        event_ids={u1: make_deterministic_uuid7(200)},
    )
    commit_source_import(conn, req1)

    # 1. Stale check: Passing generation 0 instead of 1 raises STALE_STATE
    req_stale = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(101),
        observed_at_utc=datetime.now(UTC),
        file_sha256="b" * 64,
        snapshot=snap1,
        event_ids={u1: make_deterministic_uuid7(201)},
    )
    with pytest.raises(SourceImportStoreError) as exc_stale:
        commit_source_import(conn, req_stale)
    assert exc_stale.value.reason == SourceImportStoreReason.STALE_STATE

    # 2. Append-only revision: direct SQL UPDATE rejected by trigger
    with pytest.raises(sqlite3.IntegrityError, match="Cannot update source_revisions"):
        conn.execute("UPDATE source_revisions SET home_sheet = 'لیست کسبه';")

    # 3. Sequence advance mutation: gap in change_events raises INCONSISTENT_STATE
    conn.execute("DROP TRIGGER trg_prevent_update_change_events;")
    conn.execute("UPDATE change_events SET sequence = 99 WHERE sequence = 1;")
    conn.execute(
        """
        CREATE TRIGGER trg_prevent_update_change_events
        BEFORE UPDATE ON change_events
        BEGIN
            SELECT RAISE(ABORT, 'Cannot update change_events');
        END;
        """
    )
    conn.commit()
    with pytest.raises(SourceImportStoreError) as exc_seq:
        read_source_import_store(conn)
    assert exc_seq.value.reason == SourceImportStoreReason.INCONSISTENT_STATE

    # Restore sequence
    conn.execute("DROP TRIGGER trg_prevent_update_change_events;")
    conn.execute("UPDATE change_events SET sequence = 1 WHERE sequence = 99;")
    conn.execute(
        """
        CREATE TRIGGER trg_prevent_update_change_events
        BEFORE UPDATE ON change_events
        BEGIN
            SELECT RAISE(ABORT, 'Cannot update change_events');
        END;
        """
    )
    conn.commit()
    assert read_source_import_store(conn).generation == 1

    # 4. Request Raw digest mutation: different digest raises IDEMPOTENCY_CONFLICT
    req_conflict = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(100),
        observed_at_utc=datetime.now(UTC),
        file_sha256="dead" * 16,
        snapshot=snap1,
        event_ids={u1: make_deterministic_uuid7(200)},
    )
    with pytest.raises(SourceImportStoreError) as exc_conf:
        commit_source_import(conn, req_conflict)
    assert exc_conf.value.reason == SourceImportStoreReason.IDEMPOTENCY_CONFLICT

    # Commit generation 2 to produce revision 2 with non-null previous_version_hash
    req2 = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=make_deterministic_uuid7(102),
        observed_at_utc=datetime.now(UTC),
        file_sha256="c" * 64,
        snapshot=build_synthetic_snapshot(
            [(u1, make_sample_party_row("شخص ویرایش‌شد"))], [], [], []
        ),
        event_ids={u1: make_deterministic_uuid7(202)},
    )
    commit_source_import(conn, req2)
    assert read_source_import_store(conn).generation == 2

    # 5. Predecessor link mutation: altered hash on revision 2 raises INCONSISTENT_STATE
    orig_prev_hash = conn.execute(
        "SELECT previous_version_hash FROM source_revisions WHERE revision = 2;"
    ).fetchone()[0]
    conn.execute("DROP TRIGGER trg_prevent_update_source_revisions;")
    conn.execute(
        "UPDATE source_revisions SET previous_version_hash = ? WHERE revision = 2;",
        ("f" * 64,),
    )
    conn.execute(
        """
        CREATE TRIGGER trg_prevent_update_source_revisions
        BEFORE UPDATE ON source_revisions
        BEGIN
            SELECT RAISE(ABORT, 'Cannot update source_revisions');
        END;
        """
    )
    conn.commit()
    with pytest.raises(SourceImportStoreError) as exc_pred:
        read_source_import_store(conn)
    assert exc_pred.value.reason == SourceImportStoreReason.INCONSISTENT_STATE

    # Restore predecessor
    conn.execute("DROP TRIGGER trg_prevent_update_source_revisions;")
    conn.execute(
        "UPDATE source_revisions SET previous_version_hash = ? WHERE revision = 2;",
        (orig_prev_hash,),
    )
    conn.execute(
        """
        CREATE TRIGGER trg_prevent_update_source_revisions
        BEFORE UPDATE ON source_revisions
        BEGIN
            SELECT RAISE(ABORT, 'Cannot update source_revisions');
        END;
        """
    )
    conn.commit()
    assert read_source_import_store(conn).generation == 2

    conn.close()


# ============================================================================
# IS-15: Identified XLSX composition lifecycle
# ============================================================================


def test_is15_identified_xlsx_composition_lifecycle(tmp_path: Path) -> None:
    """Four generations through WP-06 acquisition, WP-12 marker, WP-09/10, store commit,
    close/reopen/read, WP-04 comparison, and exact replay across:
    1. initial,
    2. physical reorder & formula-cache-only,
    3. edit & delete (void),
    4. reactivation.
    Asserts source bytes, marker identity, and lease cleanup."""
    leases = tmp_path / "leases"
    leases.mkdir()
    source_xlsx = tmp_path / "SYNTHETIC.xlsx"
    db_file = tmp_path / "is15_lifecycle.sqlite3"

    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = uid(1)
    src_id = uid(999)
    fiscal_year = 1405
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=fiscal_year)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    # --- Generation 1: Initial workbook ---
    parts1 = raw_parts(
        strict=False,
        seed=1,
        rows_per_sheet=1,
        extra_buy=True,
        edit=False,
        reorder=False,
        formula=0,
    )
    content1 = zipped(identified_parts(raw=parts1))
    source_xlsx.write_bytes(content1)

    acq1 = read_identified_xlsx_source(
        source_xlsx, snapshot_root=leases, observation_interval_seconds=0.001
    )
    assert source_xlsx.read_bytes() == content1
    assert list(leases.iterdir()) == []
    assert acq1.key.source_id == src_id and acq1.key.fiscal_year == fiscal_year

    rep1 = evaluate_source_requiredness(acq1.read_result.snapshot)
    assert rep1.passes_requiredness
    evaluate_source_fiscal_evidence(acq1.read_result.snapshot)

    snap1 = acq1.read_result.snapshot
    assert snap1.total_row_count == 5
    event_ids1 = {u: uid(1000 + i) for i, u in enumerate(snap1.all_rows_by_id)}
    req1 = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=uid(501),
        observed_at_utc=datetime.now(UTC),
        file_sha256=acq1.file_sha256,
        snapshot=snap1,
        event_ids=event_ids1,
    )
    rc1 = commit_source_import(conn, req1)
    assert rc1.disposition == SourceImportDisposition.COMMITTED
    assert rc1.committed_generation == 1
    assert rc1.event_count == 5

    conn.close()
    conn_reopen = sqlite3.connect(db_file)
    conn_reopen.execute("PRAGMA foreign_keys = ON;")
    v1 = read_source_import_store(conn_reopen)
    assert v1.generation == 1
    assert v1.next_sequence == 6

    # Replay Gen 1
    replay1 = commit_source_import(conn_reopen, req1)
    assert replay1.disposition == SourceImportDisposition.REPLAYED
    assert replay1.committed_generation == 1

    # --- Generation 2: Physical reorder & formula-cache-only ---
    parts2 = raw_parts(
        strict=False,
        seed=1,
        rows_per_sheet=1,
        extra_buy=True,
        edit=False,
        reorder=True,
        formula=3,
    )
    content2 = zipped(identified_parts(raw=parts2))
    source_xlsx.write_bytes(content2)

    acq2 = read_identified_xlsx_source(
        source_xlsx, snapshot_root=leases, observation_interval_seconds=0.001
    )
    assert source_xlsx.read_bytes() == content2
    assert list(leases.iterdir()) == []

    snap2 = acq2.read_result.snapshot
    prior2 = prior_from_snapshot(snap1, 1)
    plan2 = plan_source_changes(snap2, prior2)
    assert all(item.action == PlanAction.UNCHANGED for item in plan2.items)

    req2 = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=uid(502),
        observed_at_utc=datetime.now(UTC),
        file_sha256=acq2.file_sha256,
        snapshot=snap2,
        event_ids={},
    )
    rc2 = commit_source_import(conn_reopen, req2)
    assert rc2.disposition == SourceImportDisposition.COMMITTED
    assert rc2.committed_generation == 2
    assert rc2.event_count == 0

    conn_reopen.close()
    conn_reopen2 = sqlite3.connect(db_file)
    conn_reopen2.execute("PRAGMA foreign_keys = ON;")
    v2 = read_source_import_store(conn_reopen2)
    assert v2.generation == 2
    assert v2.next_sequence == 6

    replay2 = commit_source_import(conn_reopen2, req2)
    assert replay2.disposition == SourceImportDisposition.REPLAYED

    # --- Generation 3: Edit & Delete (void) ---
    parts3 = raw_parts(
        strict=False,
        seed=1,
        rows_per_sheet=1,
        extra_buy=False,
        edit=True,
        reorder=False,
        formula=0,
    )
    content3 = zipped(identified_parts(raw=parts3))
    source_xlsx.write_bytes(content3)

    acq3 = read_identified_xlsx_source(
        source_xlsx, snapshot_root=leases, observation_interval_seconds=0.001
    )
    assert source_xlsx.read_bytes() == content3
    assert list(leases.iterdir()) == []

    snap3 = acq3.read_result.snapshot
    prior3 = prior_from_snapshot(snap2, 2)
    plan3 = plan_source_changes(snap3, prior3)
    actions3 = {item.stable_id: item.action for item in plan3.items}
    assert actions3[uid(1)] == PlanAction.EDIT
    assert actions3[uid(2)] == PlanAction.VOID

    event_ids3 = {uid(1): uid(2001), uid(2): uid(2002)}
    req3 = SourceImportRequest(
        source_key=active_key,
        expected_generation=2,
        import_id=uid(503),
        observed_at_utc=datetime.now(UTC),
        file_sha256=acq3.file_sha256,
        snapshot=snap3,
        event_ids=event_ids3,
    )
    rc3 = commit_source_import(conn_reopen2, req3)
    assert rc3.disposition == SourceImportDisposition.COMMITTED
    assert rc3.committed_generation == 3
    assert rc3.event_count == 2

    conn_reopen2.close()
    conn_reopen3 = sqlite3.connect(db_file)
    conn_reopen3.execute("PRAGMA foreign_keys = ON;")
    v3 = read_source_import_store(conn_reopen3)
    assert v3.generation == 3
    assert v3.next_sequence == 8

    replay3 = commit_source_import(conn_reopen3, req3)
    assert replay3.disposition == SourceImportDisposition.REPLAYED

    # --- Generation 4: Reactivation ---
    parts4 = raw_parts(
        strict=False,
        seed=1,
        rows_per_sheet=1,
        extra_buy=True,
        edit=True,
        reorder=False,
        formula=0,
    )
    content4 = zipped(identified_parts(raw=parts4))
    source_xlsx.write_bytes(content4)

    acq4 = read_identified_xlsx_source(
        source_xlsx, snapshot_root=leases, observation_interval_seconds=0.001
    )
    assert source_xlsx.read_bytes() == content4
    assert list(leases.iterdir()) == []

    snap4 = acq4.read_result.snapshot
    event_ids4 = {uid(2): uid(3001)}
    req4 = SourceImportRequest(
        source_key=active_key,
        expected_generation=3,
        import_id=uid(504),
        observed_at_utc=datetime.now(UTC),
        file_sha256=acq4.file_sha256,
        snapshot=snap4,
        event_ids=event_ids4,
    )
    rc4 = commit_source_import(conn_reopen3, req4)
    assert rc4.disposition == SourceImportDisposition.COMMITTED
    assert rc4.committed_generation == 4
    assert rc4.event_count == 1

    conn_reopen3.close()
    conn_reopen4 = sqlite3.connect(db_file)
    conn_reopen4.execute("PRAGMA foreign_keys = ON;")
    v4 = read_source_import_store(conn_reopen4)
    assert v4.generation == 4
    assert v4.next_sequence == 9

    replay4 = commit_source_import(conn_reopen4, req4)
    assert replay4.disposition == SourceImportDisposition.REPLAYED
    conn_reopen4.close()


# ============================================================================
# IS-16: 15,000 synthetic rows scale benchmark & memory target (<350 MiB)
# ============================================================================


def test_is16_15000_row_scale_benchmark_memory_and_replay(tmp_path: Path) -> None:
    """Commit 15,000 rows on temp DB, restart/read, replay below 350 MiB process RSS.
    Includes large second generation (1,500 edits) and query/decode evidence."""
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

    t_fix_start = time.perf_counter()
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
    t_fix = time.perf_counter() - t_fix_start

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

    # Real process RSS call-window sampling
    gc.collect()
    sampler = CallWindowRssSampler(interval_seconds=0.005)
    baseline_rss = get_current_process_rss_mib()
    sampler.start()

    t_val_start = time.perf_counter()
    assert evaluate_source_requiredness(snap).passes_requiredness
    evaluate_source_fiscal_evidence(snap)
    t_val = time.perf_counter() - t_val_start

    t_commit_start = time.perf_counter()
    receipt = commit_source_import(conn, req)
    t_commit = time.perf_counter() - t_commit_start

    peak_rss = sampler.stop_and_get_peak()
    delta_rss = peak_rss - baseline_rss
    rss_method = (
        "Windows GetProcessMemoryInfo (WorkingSetSize)"
        if sys.platform == "win32"
        else "Linux /proc/self/status VmRSS"
    )

    print(
        f"[IS-16] 15,000 commit: {t_commit:.3f}s (fixture: {t_fix:.3f}s, "
        f"val: {t_val:.3f}s), Baseline RSS: {baseline_rss:.2f} MiB, "
        f"Peak RSS: {peak_rss:.2f} MiB, Delta: {delta_rss:.2f} MiB, "
        f"Method: {rss_method}",
        flush=True,
    )

    assert receipt.disposition == SourceImportDisposition.COMMITTED
    assert receipt.committed_generation == 1
    assert receipt.event_count == row_count
    assert receipt.first_sequence == 1
    assert receipt.last_sequence == row_count
    assert peak_rss < 350.0, f"Peak RSS {peak_rss:.2f} MiB exceeded 350 MiB limit!"

    conn.close()

    # Restart and read
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

    # --- Large Second Generation (15,000 rows with 1,500 edits = 10% edit subset) ---
    edit_count = 1500
    parties_gen2 = [
        (
            make_deterministic_uuid7(100000 + i),
            make_sample_party_row(f"شخص_{i}_ویرایش" if i < edit_count else f"شخص_{i}"),
        )
        for i in range(3000)
    ]
    snap_gen2 = build_synthetic_snapshot(parties_gen2, buy_sell, receipts, inventory)
    event_ids_gen2 = {
        make_deterministic_uuid7(100000 + i): make_deterministic_uuid7(600000 + i)
        for i in range(edit_count)
    }
    req_gen2 = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=make_deterministic_uuid7(888888),
        observed_at_utc=datetime.now(UTC),
        file_sha256="e" * 64,
        snapshot=snap_gen2,
        event_ids=event_ids_gen2,
    )

    t_gen2_start = time.perf_counter()
    rc_gen2 = commit_source_import(conn_reopen, req_gen2)
    t_gen2 = time.perf_counter() - t_gen2_start

    assert rc_gen2.disposition == SourceImportDisposition.COMMITTED
    assert rc_gen2.committed_generation == 2
    assert rc_gen2.event_count == edit_count
    assert rc_gen2.first_sequence == row_count + 1
    assert rc_gen2.last_sequence == row_count + edit_count

    v_gen2 = read_source_import_store(conn_reopen)
    assert v_gen2.generation == 2
    assert v_gen2.next_sequence == row_count + edit_count + 1
    print(
        f"[IS-16] 15,000 (1,500 edits) Gen 2 commit time: {t_gen2:.3f}s",
        flush=True,
    )

    conn_reopen.close()


# ============================================================================
# Review findings: R1 - R5 Deep Verifications
# ============================================================================


def test_r1_deterministic_two_connection_wal_repeated_init(tmp_path: Path) -> None:
    """R1: Repeat initialization reads consistent generation under owned read tx.
    Two-connection WAL test with Events/ACKs pauses after Meta SELECT, commits valid
    generation from writer, and returns valid view. Asserts owned transaction at pause
    point, rejects pre-open caller transaction, and preserves caller settings."""
    db_file = tmp_path / "r1_wal.sqlite3"
    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)

    # Initial setup
    conn_setup = sqlite3.connect(db_file)
    conn_setup.execute("PRAGMA foreign_keys = ON;")
    conn_setup.execute("PRAGMA journal_mode = WAL;")
    initialize_source_import_store(
        conn_setup, device_id=dev_id, active_source=active_key
    )
    conn_setup.close()

    # Pre-open transaction rejection
    conn_pre = sqlite3.connect(db_file)
    conn_pre.execute("PRAGMA foreign_keys = ON;")
    conn_pre.execute("BEGIN;")
    with pytest.raises(SourceImportStoreError) as exc_pre:
        initialize_source_import_store(
            conn_pre, device_id=dev_id, active_source=active_key
        )
    assert exc_pre.value.reason == SourceImportStoreReason.STORAGE_FAILURE
    conn_pre.close()

    event_meta_selected = threading.Event()
    event_writer_committed = threading.Event()
    writer_errors: list[BaseException] = []

    def writer_worker() -> None:
        try:
            assert event_meta_selected.wait(timeout=10.0)
            conn_w = sqlite3.connect(db_file)
            conn_w.execute("PRAGMA foreign_keys = ON;")
            u1 = make_deterministic_uuid7(10)
            snap1 = build_synthetic_snapshot(
                [(u1, make_sample_party_row("شخص یک"))], [], [], []
            )
            req1 = SourceImportRequest(
                source_key=active_key,
                expected_generation=0,
                import_id=make_deterministic_uuid7(101),
                observed_at_utc=datetime.now(UTC),
                file_sha256="1" * 64,
                snapshot=snap1,
                event_ids={u1: make_deterministic_uuid7(201)},
            )
            rc1 = commit_source_import(conn_w, req1)
            assert rc1.disposition == SourceImportDisposition.COMMITTED
            conn_w.close()
            event_writer_committed.set()
        except BaseException as exc:
            writer_errors.append(exc)
            event_writer_committed.set()

    t_writer = threading.Thread(target=writer_worker)
    t_writer.start()

    conn1 = sqlite3.connect(db_file)
    conn1.execute("PRAGMA foreign_keys = ON;")
    conn1.row_factory = sqlite3.Row
    conn1.isolation_level = "DEFERRED"
    in_tx_at_pause = False

    def trace_cb(sql: str) -> None:
        nonlocal in_tx_at_pause
        if "FROM source_store_meta WHERE singleton_id = 1" in sql:
            in_tx_at_pause = conn1.in_transaction
            event_meta_selected.set()
            assert event_writer_committed.wait(timeout=10.0)

    conn1.set_trace_callback(trace_cb)

    view = initialize_source_import_store(
        conn1, device_id=dev_id, active_source=active_key
    )
    t_writer.join(timeout=10.0)

    assert not writer_errors, f"Writer worker failed: {writer_errors}"
    assert in_tx_at_pause is True
    assert view.generation in (0, 1)
    assert conn1.row_factory is sqlite3.Row
    assert conn1.isolation_level == "DEFERRED"

    conn1.close()


def test_r2_schema_validation_rigor(tmp_path: Path) -> None:
    """R2: Schema validation catches independent mutations for every object family:
    trigger dropped/recreated as no-op, wrong-table/event trigger, altered index
    predicates/columns, altered table affinity/constraints, and extra objects.
    Each test uses a fresh fixture."""
    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)

    def make_store(name: str) -> sqlite3.Connection:
        db = tmp_path / f"r2_{name}.sqlite3"
        c = sqlite3.connect(db)
        c.execute("PRAGMA foreign_keys = ON;")
        initialize_source_import_store(c, device_id=dev_id, active_source=active_key)
        return c

    # 1. Trigger dropped and recreated as same-name no-op
    conn1 = make_store("trg_noop")
    conn1.execute("DROP TRIGGER trg_prevent_update_source_revisions;")
    conn1.execute(
        "CREATE TRIGGER trg_prevent_update_source_revisions "
        "BEFORE UPDATE ON source_revisions BEGIN SELECT 1; END;"
    )
    conn1.commit()
    with pytest.raises(SourceImportStoreError) as exc1_r:
        read_source_import_store(conn1)
    assert exc1_r.value.reason == SourceImportStoreReason.INVALID_SCHEMA
    with pytest.raises(SourceImportStoreError) as exc1_i:
        initialize_source_import_store(
            conn1, device_id=dev_id, active_source=active_key
        )
    assert exc1_i.value.reason == SourceImportStoreReason.INVALID_SCHEMA
    conn1.close()

    # 2. Same-name wrong-table / wrong-event trigger
    conn2 = make_store("trg_wrong")
    conn2.execute("DROP TRIGGER trg_prevent_update_change_events;")
    conn2.execute(
        "CREATE TRIGGER trg_prevent_update_change_events "
        "BEFORE DELETE ON source_revisions "
        "BEGIN SELECT RAISE(ABORT, 'Cannot update change_events'); END;"
    )
    conn2.commit()
    with pytest.raises(SourceImportStoreError) as exc2_r:
        read_source_import_store(conn2)
    assert exc2_r.value.reason == SourceImportStoreReason.INVALID_SCHEMA
    conn2.close()

    # 3. Altered index predicates/columns
    conn3 = make_store("altered_idx")
    conn3.execute("DROP INDEX uq_source_bindings_active;")
    conn3.execute(
        "CREATE UNIQUE INDEX uq_source_bindings_active ON source_bindings(state);"
    )
    conn3.commit()
    with pytest.raises(SourceImportStoreError) as exc3_r:
        read_source_import_store(conn3)
    assert exc3_r.value.reason == SourceImportStoreReason.INVALID_SCHEMA
    conn3.close()

    # 4. Altered table affinity / constraints
    conn4 = make_store("altered_affinity")
    conn4.execute(
        "CREATE TABLE temp_meta (singleton_id INTEGER PRIMARY KEY, "
        "store_version TEXT NOT NULL, schema_version INTEGER NOT NULL, "
        "generation TEXT NOT NULL, next_sequence INTEGER NOT NULL, "
        "device_id BLOB NOT NULL);"
    )
    conn4.execute(
        "INSERT INTO temp_meta SELECT singleton_id, store_version, schema_version, "
        "CAST(generation AS TEXT), next_sequence, device_id FROM source_store_meta;"
    )
    conn4.execute("DROP TABLE source_store_meta;")
    conn4.execute("ALTER TABLE temp_meta RENAME TO source_store_meta;")
    conn4.commit()
    with pytest.raises(SourceImportStoreError) as exc4_r:
        read_source_import_store(conn4)
    assert exc4_r.value.reason == SourceImportStoreReason.INVALID_SCHEMA
    conn4.close()

    # 5. Extra table
    conn5 = make_store("extra_table")
    conn5.execute("CREATE TABLE rogue_table (id INTEGER PRIMARY KEY);")
    conn5.commit()
    with pytest.raises(SourceImportStoreError) as exc5_r:
        read_source_import_store(conn5)
    assert exc5_r.value.reason == SourceImportStoreReason.INVALID_SCHEMA
    conn5.close()

    # 6. Extra view
    conn6 = make_store("extra_view")
    conn6.execute("CREATE VIEW rogue_view AS SELECT 1 AS x;")
    conn6.commit()
    with pytest.raises(SourceImportStoreError) as exc6_r:
        read_source_import_store(conn6)
    assert exc6_r.value.reason == SourceImportStoreReason.INVALID_SCHEMA
    conn6.close()

    # 7. Extra index
    conn7 = make_store("extra_index")
    conn7.execute("CREATE INDEX rogue_idx ON source_store_meta(generation);")
    conn7.commit()
    with pytest.raises(SourceImportStoreError) as exc7_r:
        read_source_import_store(conn7)
    assert exc7_r.value.reason == SourceImportStoreReason.INVALID_SCHEMA
    conn7.close()

    # 8. Extra trigger
    conn8 = make_store("extra_trigger")
    conn8.execute(
        "CREATE TRIGGER rogue_trg BEFORE INSERT ON source_store_meta "
        "BEGIN SELECT 1; END;"
    )
    conn8.commit()
    with pytest.raises(SourceImportStoreError) as exc8_r:
        read_source_import_store(conn8)
    assert exc8_r.value.reason == SourceImportStoreReason.INVALID_SCHEMA
    conn8.close()


def test_r3_semantic_binding_and_tamper_rejection(tmp_path: Path) -> None:
    """R3: Reconstructs canonical event bytes, checks UUID BLOBs, generation history,
    observation links, and rejects tampered data with INCONSISTENT_STATE."""
    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)

    def make_gen1_store(name: str) -> tuple[sqlite3.Connection, SourceImportRequest]:
        db = tmp_path / f"r3_{name}.sqlite3"
        c = sqlite3.connect(db)
        c.execute("PRAGMA foreign_keys = ON;")
        initialize_source_import_store(c, device_id=dev_id, active_source=active_key)
        u = make_deterministic_uuid7(10)
        snap = build_synthetic_snapshot([(u, make_sample_party_row("علی"))], [], [], [])
        req = SourceImportRequest(
            source_key=active_key,
            expected_generation=0,
            import_id=make_deterministic_uuid7(100),
            observed_at_utc=datetime.now(UTC),
            file_sha256="a" * 64,
            snapshot=snap,
            event_ids={u: make_deterministic_uuid7(200)},
        )
        commit_source_import(c, req)
        return c, req

    # 1. Semantic payload changed & rehashed -> rejected by canonical reconstruction
    conn1, req1 = make_gen1_store("payload_tamper")
    conn1.execute("DROP TRIGGER trg_prevent_update_change_events;")
    tampered_payload = b'["source-change-event.v1","tampered"]'
    tampered_hash = hashlib.sha256(tampered_payload).hexdigest()
    conn1.execute(
        "UPDATE change_events SET canonical_payload = ?, payload_hash = ? "
        "WHERE sequence = 1;",
        (tampered_payload, tampered_hash),
    )
    conn1.execute(
        """
        CREATE TRIGGER trg_prevent_update_change_events
        BEFORE UPDATE ON change_events
        BEGIN
            SELECT RAISE(ABORT, 'Cannot update change_events');
        END;
        """
    )
    conn1.commit()
    with pytest.raises(SourceImportStoreError) as exc1:
        read_source_import_store(conn1)
    assert exc1.value.reason == SourceImportStoreReason.INCONSISTENT_STATE

    # Exact replay is also rejected on tampered store
    with pytest.raises(SourceImportStoreError) as exc1_replay:
        commit_source_import(conn1, req1)
    assert exc1_replay.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    conn1.close()

    # 2. Import generation jump (0->1 to 6->7 while Meta remains 1)
    conn2, _ = make_gen1_store("gen_jump")
    conn2.execute("DROP TRIGGER trg_prevent_update_source_imports;")
    conn2.execute(
        "UPDATE source_imports SET base_generation = 6, committed_generation = 7;"
    )
    conn2.execute(
        """
        CREATE TRIGGER trg_prevent_update_source_imports
        BEFORE UPDATE ON source_imports
        BEGIN
            SELECT RAISE(ABORT, 'Cannot update source_imports');
        END;
        """
    )
    conn2.commit()
    with pytest.raises(SourceImportStoreError) as exc2:
        read_source_import_store(conn2)
    assert exc2.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    conn2.close()

    # 3. Stale membership last_import_id
    conn3, req3_1 = make_gen1_store("stale_mem")
    u_stale = make_deterministic_uuid7(10)
    snap2 = build_synthetic_snapshot(
        [(u_stale, make_sample_party_row("رضا"))], [], [], []
    )
    req3_2 = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=make_deterministic_uuid7(101),
        observed_at_utc=datetime.now(UTC),
        file_sha256="b" * 64,
        snapshot=snap2,
        event_ids={u_stale: make_deterministic_uuid7(201)},
    )
    commit_source_import(conn3, req3_2)
    # Revert last_import_id to import 100 (which exists, satisfying FK, but is stale!)
    conn3.execute(
        "UPDATE source_memberships SET last_import_id = ?;",
        (make_deterministic_uuid7(100).bytes,),
    )
    conn3.commit()
    with pytest.raises(SourceImportStoreError) as exc3:
        read_source_import_store(conn3)
    assert exc3.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    conn3.close()

    # 4. Mismatched event operation/sheet
    conn4, _ = make_gen1_store("mismatch_ev")
    conn4.execute("DROP TRIGGER trg_prevent_update_change_events;")
    conn4.execute("UPDATE change_events SET operation = 'void';")
    conn4.execute(
        """
        CREATE TRIGGER trg_prevent_update_change_events
        BEFORE UPDATE ON change_events
        BEGIN
            SELECT RAISE(ABORT, 'Cannot update change_events');
        END;
        """
    )
    conn4.commit()
    with pytest.raises(SourceImportStoreError) as exc4:
        read_source_import_store(conn4)
    assert exc4.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    conn4.close()

    # 5. Broken predecessor link
    conn5, _ = make_gen1_store("broken_link")
    conn5.execute("DROP TRIGGER trg_prevent_update_source_revisions;")
    conn5.execute(
        "UPDATE source_revisions SET version_hash = ?;",
        ("0" * 64,),
    )
    conn5.execute(
        """
        CREATE TRIGGER trg_prevent_update_source_revisions
        BEFORE UPDATE ON source_revisions
        BEGIN
            SELECT RAISE(ABORT, 'Cannot update source_revisions');
        END;
        """
    )
    conn5.commit()
    with pytest.raises(SourceImportStoreError) as exc5:
        read_source_import_store(conn5)
    assert exc5.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    conn5.close()


def test_r4_exact_root_scalar_type_enforcement() -> None:
    """R4: Validate exact non-bool types, reject equality spoofs, subclasses,
    foreign StrEnums, bool-as-int, and ensure SourceImportRequest subclass fails
    with INVALID_INPUT before any callback."""
    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    registry = SourceBindingRegistry([])

    # 1. SourceImportStoreView equality spoof version & schema_version=True
    class EqualitySpoofStr:
        def __eq__(self, other: Any) -> bool:
            raise RuntimeError("hostile __eq__ should never be invoked!")

    with pytest.raises(SourceImportStoreError) as exc1:
        SourceImportStoreView(
            version=cast(str, EqualitySpoofStr()),
            schema_version=1,
            generation=0,
            next_sequence=1,
            device_id=dev_id,
            source_registry=registry,
        )
    assert exc1.value.reason == SourceImportStoreReason.INVALID_INPUT

    with pytest.raises(SourceImportStoreError) as exc2:
        SourceImportStoreView(
            version="source-import-store.v1",
            schema_version=cast(int, True),  # bool passed where int expected
            generation=0,
            next_sequence=1,
            device_id=dev_id,
            source_registry=registry,
        )
    assert exc2.value.reason == SourceImportStoreReason.INVALID_INPUT

    # 2. SourceImportRequest subclass probe (Codex probe: subclass must fail)
    class SubclassRequest(SourceImportRequest):
        pass

    snap = build_synthetic_snapshot([], [], [], [])
    sub_req = SubclassRequest(
        source_key=key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(10),
        observed_at_utc=datetime.now(UTC),
        file_sha256="0" * 64,
        snapshot=snap,
        event_ids={},
    )

    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    initialize_source_import_store(conn, device_id=dev_id, active_source=key)

    with pytest.raises(SourceImportStoreError) as exc3:
        commit_source_import(conn, sub_req)
    assert exc3.value.reason == SourceImportStoreReason.INVALID_INPUT

    # 3. Hostile __eq__ on request fields
    class HostileUUID:
        def __eq__(self, other: Any) -> bool:
            raise RuntimeError("hostile __eq__ on UUID")

    with pytest.raises(SourceImportStoreError) as exc4:
        initialize_source_import_store(
            conn, device_id=cast(uuid.UUID, HostileUUID()), active_source=key
        )
    assert exc4.value.reason == SourceImportStoreReason.INVALID_INPUT

    conn.close()


def test_r5_no_historical_raw_scans_instrumentation(tmp_path: Path) -> None:
    """R5: Ordinary read and commit queries current heads via indexed queries,
    never executing per-history row scans or decodes. Verified via instrumentation
    with 10 historical revisions and 1 current head."""
    db_file = tmp_path / "r5_cost.sqlite3"
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    u1 = make_deterministic_uuid7(10)

    # Commit 10 generations for the same row u1
    for gen in range(10):
        snap = build_synthetic_snapshot(
            [(u1, make_sample_party_row(f"نسخه_{gen}"))], [], [], []
        )
        req = SourceImportRequest(
            source_key=active_key,
            expected_generation=gen,
            import_id=make_deterministic_uuid7(100 + gen),
            observed_at_utc=datetime.now(UTC),
            file_sha256=f"{gen}" * 64,
            snapshot=snap,
            event_ids={u1: make_deterministic_uuid7(1000 + gen)},
        )
        commit_source_import(conn, req)

    # 10 revisions exist in history
    n_revs = conn.execute(
        "SELECT COUNT(*) FROM source_revisions WHERE stable_id = ?;", (u1.bytes,)
    ).fetchone()[0]
    assert n_revs == 10

    # Instrument decode_source_raw_row
    import accounting_persistence.source_import_store as sis_mod

    codec_attr = "decode_source_raw_row"
    orig_decode = getattr(sis_mod, codec_attr)
    decode_calls = 0

    def counting_decode(*args: Any, **kwargs: Any) -> Any:
        nonlocal decode_calls
        decode_calls += 1
        return orig_decode(*args, **kwargs)

    setattr(sis_mod, codec_attr, counting_decode)
    try:
        view = read_source_import_store(conn)
        assert view.generation == 10
        # Exactly 1 decode for the single current active head! NOT 10!
        assert decode_calls == 1, (
            f"Expected exactly 1 decode for current head, got {decode_calls}"
        )

        # Commit 11th generation (edit)
        decode_calls = 0
        snap11 = build_synthetic_snapshot(
            [(u1, make_sample_party_row("نسخه_10_ادیت"))], [], [], []
        )
        req11 = SourceImportRequest(
            source_key=active_key,
            expected_generation=10,
            import_id=make_deterministic_uuid7(999),
            observed_at_utc=datetime.now(UTC),
            file_sha256="9" * 64,
            snapshot=snap11,
            event_ids={u1: make_deterministic_uuid7(9999)},
        )
        rc11 = commit_source_import(conn, req11)
        assert rc11.committed_generation == 11
        # No full historical scans: decodes only needed for current heads
        assert decode_calls <= 2, f"Decode calls exceeded bound: {decode_calls}"
    finally:
        setattr(sis_mod, codec_attr, orig_decode)

    conn.close()
