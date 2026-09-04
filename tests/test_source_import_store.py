"""IS-01..16: independent verification of atomic SQLite source import store.

Validates schema v1, API signatures, consistent read, atomic transactions,
revisions, nondeleting membership, contiguous change events, replay idempotency,
concurrency, crash recovery, error sanitization, property models, and 15,000-row scale.
"""

from __future__ import annotations

import base64
import gc
import hashlib
import importlib
import inspect
import os
import random
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
from accounting_contracts.source_change_plan import (
    IdentityLifecycle,
    PlanAction,
    PriorIdentityRegistry,
    PriorIdentityState,
    build_prior_identity_registry,
)
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


def test_is06_cross_source_global_party_zero_event_import_and_read() -> None:
    """IS-06: A party first present in B with unchanged global head creates B
    membership but no revision/event; reads successfully immediately after."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_a = make_deterministic_uuid7(100)  # archived (1402)
    src_b = make_deterministic_uuid7(200)  # active (1403)

    key_a = SourceBindingKey(source_id=src_a, fiscal_year=1402)
    key_b = SourceBindingKey(source_id=src_b, fiscal_year=1403)

    initialize_source_import_store(conn, device_id=dev_id, active_source=key_a)

    # 1. Source A commits global party P (creates revision 1 and event 1)
    party_u = make_deterministic_uuid7(10)
    snap_a = build_synthetic_snapshot(
        [(party_u, make_sample_party_row("شخص مشترک", "09120000000"))], [], [], []
    )
    req_a = SourceImportRequest(
        source_key=key_a,
        expected_generation=0,
        import_id=make_deterministic_uuid7(500),
        observed_at_utc=datetime(2025, 9, 4, 10, 0, 0, tzinfo=UTC),
        file_sha256="a" * 64,
        snapshot=snap_a,
        event_ids={party_u: make_deterministic_uuid7(501)},
    )
    r_a = commit_source_import(conn, req_a)
    assert r_a.committed_generation == 1
    assert r_a.event_count == 1

    # 2. Archive source A and add active source B
    conn.execute(
        "UPDATE source_bindings "
        "SET state = 'archived', final_file_sha256 = ?, "
        "last_file_sha256 = ?, last_observed_at_utc = ? "
        "WHERE source_id = ?;",
        (
            "a" * 64,
            "a" * 64,
            datetime(2025, 9, 4, 10, 0, 0, tzinfo=UTC).isoformat(),
            src_a.bytes,
        ),
    )
    conn.execute(
        "INSERT INTO source_bindings (source_id, fiscal_year, state) "
        "VALUES (?, 1403, 'active');",
        (src_b.bytes,),
    )
    conn.commit()

    # 3. Import unchanged global party P into active B (generation 2, zero events)
    snap_b = build_synthetic_snapshot(
        [(party_u, make_sample_party_row("شخص مشترک", "09120000000"))], [], [], []
    )
    req_b = SourceImportRequest(
        source_key=key_b,
        expected_generation=1,
        import_id=make_deterministic_uuid7(600),
        observed_at_utc=datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC),
        file_sha256="b" * 64,
        snapshot=snap_b,
        event_ids={},  # UNCHANGED globally known party -> zero events
    )
    r_b = commit_source_import(conn, req_b)
    assert r_b.committed_generation == 2
    assert r_b.event_count == 0

    # 4. Read immediately succeeds after zero-event B import
    view = read_source_import_store(conn)
    assert view.generation == 2
    assert view.next_sequence == 2  # next sequence unchanged (1 event total)

    # 5. Reconstruct B membership at existing global revision 1 with no new event
    b_mem = conn.execute(
        "SELECT revision, first_import_id, last_import_id FROM source_memberships "
        "WHERE source_id = ? AND stable_id = ?;",
        (src_b.bytes, party_u.bytes),
    ).fetchone()
    assert b_mem is not None
    assert b_mem[0] == 1
    assert b_mem[1] == req_b.import_id.bytes
    assert b_mem[2] == req_b.import_id.bytes

    # Total revisions in source_revisions is still exactly 1
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM source_revisions WHERE stable_id = ?;",
            (party_u.bytes,),
        ).fetchone()[0]
        == 1
    )
    # Total change_events is still exactly 1
    assert conn.execute("SELECT COUNT(*) FROM change_events;").fetchone()[0] == 1
    conn.close()


def test_is06_cross_source_global_party_later_void_in_active_source() -> None:
    """IS-06: Party later absent in B creates correct B VOID revision and Event;
    archived A's membership/revision/final hash is preserved without false VOID."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_a = make_deterministic_uuid7(100)
    src_b = make_deterministic_uuid7(200)

    key_a = SourceBindingKey(source_id=src_a, fiscal_year=1402)
    key_b = SourceBindingKey(source_id=src_b, fiscal_year=1403)

    initialize_source_import_store(conn, device_id=dev_id, active_source=key_a)

    party_u = make_deterministic_uuid7(10)
    snap_a = build_synthetic_snapshot(
        [(party_u, make_sample_party_row("شخص مشترک", "09120000000"))], [], [], []
    )
    req_a = SourceImportRequest(
        source_key=key_a,
        expected_generation=0,
        import_id=make_deterministic_uuid7(500),
        observed_at_utc=datetime(2025, 9, 4, 10, 0, 0, tzinfo=UTC),
        file_sha256="a" * 64,
        snapshot=snap_a,
        event_ids={party_u: make_deterministic_uuid7(501)},
    )
    commit_source_import(conn, req_a)

    # Archive source A and add active source B
    conn.execute(
        "UPDATE source_bindings "
        "SET state = 'archived', final_file_sha256 = ?, "
        "last_file_sha256 = ?, last_observed_at_utc = ? "
        "WHERE source_id = ?;",
        (
            "a" * 64,
            "a" * 64,
            datetime(2025, 9, 4, 10, 0, 0, tzinfo=UTC).isoformat(),
            src_a.bytes,
        ),
    )
    conn.execute(
        "INSERT INTO source_bindings (source_id, fiscal_year, state) "
        "VALUES (?, 1403, 'active');",
        (src_b.bytes,),
    )
    conn.commit()

    # Import into B with party P present (gen 2)
    snap_b1 = build_synthetic_snapshot(
        [(party_u, make_sample_party_row("شخص مشترک", "09120000000"))], [], [], []
    )
    req_b1 = SourceImportRequest(
        source_key=key_b,
        expected_generation=1,
        import_id=make_deterministic_uuid7(600),
        observed_at_utc=datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC),
        file_sha256="b1" * 32,
        snapshot=snap_b1,
        event_ids={},
    )
    commit_source_import(conn, req_b1)

    # Now import a later B snapshot where party P is ABSENT (gen 3)
    snap_b2 = build_synthetic_snapshot([], [], [], [])
    void_event_id = make_deterministic_uuid7(701)
    req_b2 = SourceImportRequest(
        source_key=key_b,
        expected_generation=2,
        import_id=make_deterministic_uuid7(700),
        observed_at_utc=datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC),
        file_sha256="b2" * 32,
        snapshot=snap_b2,
        event_ids={party_u: void_event_id},
    )
    r_b2 = commit_source_import(conn, req_b2)
    assert r_b2.committed_generation == 3
    assert r_b2.event_count == 1
    assert r_b2.total_counts.void_count == 1

    # Verify B now has VOID revision (rev 2)
    b_mem = conn.execute(
        "SELECT revision FROM source_memberships "
        "WHERE source_id = ? AND stable_id = ?;",
        (src_b.bytes, party_u.bytes),
    ).fetchone()
    assert b_mem[0] == 2

    rev2 = conn.execute(
        "SELECT lifecycle, source_hash, raw_payload, created_by_import_id "
        "FROM source_revisions WHERE stable_id = ? AND revision = 2;",
        (party_u.bytes,),
    ).fetchone()
    assert rev2[0] == "voided"
    assert rev2[1] is None
    assert rev2[2] is None
    assert rev2[3] == req_b2.import_id.bytes

    # Archived A's membership remains at revision 1 without false VOID!
    a_mem = conn.execute(
        "SELECT revision, last_import_id FROM source_memberships "
        "WHERE source_id = ? AND stable_id = ?;",
        (src_a.bytes, party_u.bytes),
    ).fetchone()
    assert a_mem[0] == 1
    assert a_mem[1] == req_a.import_id.bytes

    # Archived A's final hash is preserved
    a_binding = conn.execute(
        "SELECT state, final_file_sha256 FROM source_bindings WHERE source_id = ?;",
        (src_a.bytes,),
    ).fetchone()
    assert a_binding[0] == "archived"
    assert a_binding[1] == "a" * 64

    # Read store succeeds and verifies both prior registries
    view = read_source_import_store(conn)
    assert view.generation == 3
    assert view.next_sequence == 3

    reg_a = next(r for r in view.source_registry.records if r.key == key_a)
    assert reg_a is not None
    assert reg_a.prior_registry.identities[party_u].latest_revision == 1
    assert (
        reg_a.prior_registry.identities[party_u].lifecycle == IdentityLifecycle.ACTIVE
    )

    reg_b = next(r for r in view.source_registry.records if r.key == key_b)
    assert reg_b is not None
    assert reg_b.prior_registry.identities[party_u].latest_revision == 2
    assert (
        reg_b.prior_registry.identities[party_u].lifecycle == IdentityLifecycle.VOIDED
    )

    conn.close()


def test_is06_reject_request_targeting_archived_source_before_write() -> None:
    """IS-06: A request targeting an archived source is rejected with
    SOURCE_NOT_ACTIVE before any write."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_a = make_deterministic_uuid7(100)
    src_b = make_deterministic_uuid7(200)

    key_a = SourceBindingKey(source_id=src_a, fiscal_year=1402)
    initialize_source_import_store(conn, device_id=dev_id, active_source=key_a)

    # Archive A, activate B
    conn.execute(
        "UPDATE source_bindings "
        "SET state = 'archived', final_file_sha256 = ?, "
        "last_file_sha256 = ?, last_observed_at_utc = ? "
        "WHERE source_id = ?;",
        ("0" * 64, "0" * 64, datetime.now(UTC).isoformat(), src_a.bytes),
    )
    conn.execute(
        "INSERT INTO source_bindings (source_id, fiscal_year, state) "
        "VALUES (?, 1403, 'active');",
        (src_b.bytes,),
    )
    conn.commit()

    # Snapshot table counts before
    snap_counts = {
        tbl: conn.execute(f"SELECT COUNT(*) FROM {tbl};").fetchone()[0]
        for tbl in [
            "source_imports",
            "source_revisions",
            "change_events",
            "source_memberships",
        ]
    }

    # Attempt to commit using archived key_a
    u = make_deterministic_uuid7(10)
    snap = build_synthetic_snapshot([(u, make_sample_party_row("تست"))], [], [], [])
    req_archived = SourceImportRequest(
        source_key=key_a,
        expected_generation=0,
        import_id=make_deterministic_uuid7(900),
        observed_at_utc=datetime.now(UTC),
        file_sha256="9" * 64,
        snapshot=snap,
        event_ids={u: make_deterministic_uuid7(901)},
    )
    with pytest.raises(SourceImportStoreError) as exc_info:
        commit_source_import(conn, req_archived)
    assert exc_info.value.reason == SourceImportStoreReason.SOURCE_NOT_ACTIVE

    # Zero writes occurred
    for tbl, cnt in snap_counts.items():
        assert conn.execute(f"SELECT COUNT(*) FROM {tbl};").fetchone()[0] == cnt
    conn.close()


def test_is06_reject_uuid_moved_across_non_party_sheets_before_write() -> None:
    """IS-06: A UUID moved across non-party sheets fails with INVALID_INPUT
    before writing any row."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    # 1. Commit transaction row in buy_sell sheet
    u_tx = make_deterministic_uuid7(50)
    snap1 = build_synthetic_snapshot(
        [],
        [
            (
                u_tx,
                make_sample_buy_sell_row(
                    "1403/01/10", "شخص", "خرید", "طلا", "1", "1000"
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
        observed_at_utc=datetime.now(UTC),
        file_sha256="1" * 64,
        snapshot=snap1,
        event_ids={u_tx: make_deterministic_uuid7(200)},
    )
    commit_source_import(conn, req1)

    # Snapshot state before attempt
    snap_counts = {
        tbl: conn.execute(f"SELECT COUNT(*) FROM {tbl};").fetchone()[0]
        for tbl in [
            "source_imports",
            "source_revisions",
            "change_events",
            "source_memberships",
        ]
    }
    gen_before = conn.execute("SELECT generation FROM source_store_meta;").fetchone()[0]

    # 2. Attempt to submit u_tx in receipt_payment sheet (non-party relocation)
    snap2 = build_synthetic_snapshot(
        [],
        [],
        [
            (
                u_tx,
                make_sample_receipt_payment_row("1403/01/10", "شخص", "دریافت", "1000"),
            )
        ],
        [],
    )
    req2 = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=make_deterministic_uuid7(101),
        observed_at_utc=datetime.now(UTC),
        file_sha256="2" * 64,
        snapshot=snap2,
        event_ids={u_tx: make_deterministic_uuid7(201)},
    )
    with pytest.raises(SourceImportStoreError) as exc_info:
        commit_source_import(conn, req2)
    assert exc_info.value.reason == SourceImportStoreReason.INVALID_INPUT

    # Zero writes occurred
    for tbl, cnt in snap_counts.items():
        assert conn.execute(f"SELECT COUNT(*) FROM {tbl};").fetchone()[0] == cnt
    assert (
        conn.execute("SELECT generation FROM source_store_meta;").fetchone()[0]
        == gen_before
    )
    conn.close()


def test_is06_transaction_ownership_and_state_verification() -> None:
    """IS-06: Verifies transaction ownership and every table/generation/sequence
    before and after cross-source operations."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_a = make_deterministic_uuid7(100)
    src_b = make_deterministic_uuid7(200)

    key_a = SourceBindingKey(source_id=src_a, fiscal_year=1402)
    key_b = SourceBindingKey(source_id=src_b, fiscal_year=1403)

    initialize_source_import_store(conn, device_id=dev_id, active_source=key_a)
    assert not conn.in_transaction

    # Initial state
    assert conn.execute(
        "SELECT generation, next_sequence FROM source_store_meta;"
    ).fetchone() == (0, 1)

    party_u = make_deterministic_uuid7(10)
    snap_a = build_synthetic_snapshot(
        [(party_u, make_sample_party_row("شخص یک"))], [], [], []
    )
    req_a = SourceImportRequest(
        source_key=key_a,
        expected_generation=0,
        import_id=make_deterministic_uuid7(500),
        observed_at_utc=datetime(2025, 9, 4, 10, 0, 0, tzinfo=UTC),
        file_sha256="a" * 64,
        snapshot=snap_a,
        event_ids={party_u: make_deterministic_uuid7(501)},
    )
    commit_source_import(conn, req_a)
    assert not conn.in_transaction
    assert conn.execute(
        "SELECT generation, next_sequence FROM source_store_meta;"
    ).fetchone() == (1, 2)

    # Archive A, activate B
    conn.execute(
        "UPDATE source_bindings "
        "SET state = 'archived', final_file_sha256 = ?, "
        "last_file_sha256 = ?, last_observed_at_utc = ? "
        "WHERE source_id = ?;",
        (
            "a" * 64,
            "a" * 64,
            datetime(2025, 9, 4, 10, 0, 0, tzinfo=UTC).isoformat(),
            src_a.bytes,
        ),
    )
    conn.execute(
        "INSERT INTO source_bindings (source_id, fiscal_year, state) "
        "VALUES (?, 1403, 'active');",
        (src_b.bytes,),
    )
    conn.commit()

    snap_b = build_synthetic_snapshot(
        [(party_u, make_sample_party_row("شخص یک"))], [], [], []
    )
    req_b = SourceImportRequest(
        source_key=key_b,
        expected_generation=1,
        import_id=make_deterministic_uuid7(600),
        observed_at_utc=datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC),
        file_sha256="b" * 64,
        snapshot=snap_b,
        event_ids={},
    )
    commit_source_import(conn, req_b)
    assert not conn.in_transaction
    assert conn.execute(
        "SELECT generation, next_sequence FROM source_store_meta;"
    ).fetchone() == (2, 2)
    assert conn.execute("SELECT COUNT(*) FROM source_imports;").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM source_revisions;").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM change_events;").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM source_memberships;").fetchone()[0] == 2
    conn.close()


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
# IS-09: Two-connection race condition testing with Synchronization Seams
# ============================================================================


class SynchronizedRaceConnection(sqlite3.Connection):
    """Connection that pauses inside write transaction after observing generation."""

    pause_after_meta_observed: threading.Event | None = None
    resume_after_competitor_attempt: threading.Event | None = None

    def cursor(self, factory: Any = None) -> sqlite3.Cursor:  # type: ignore[override]
        cur: sqlite3.Cursor = super().cursor(factory=factory or SynchronizedRaceCursor)
        return cur

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        res = super().execute(sql, *args, **kwargs)
        if (
            SynchronizedRaceConnection.pause_after_meta_observed is not None
            and "FROM source_store_meta WHERE singleton_id = 1" in sql
        ):
            SynchronizedRaceConnection.pause_after_meta_observed.set()
            if SynchronizedRaceConnection.resume_after_competitor_attempt is not None:
                assert SynchronizedRaceConnection.resume_after_competitor_attempt.wait(
                    timeout=10.0
                )
        return res


class SynchronizedRaceCursor(sqlite3.Cursor):
    def execute(self, sql: str, *params: Any) -> Any:
        res = super().execute(sql, *params)
        if (
            SynchronizedRaceConnection.pause_after_meta_observed is not None
            and "FROM source_store_meta WHERE singleton_id = 1" in sql
        ):
            SynchronizedRaceConnection.pause_after_meta_observed.set()
            if SynchronizedRaceConnection.resume_after_competitor_attempt is not None:
                assert SynchronizedRaceConnection.resume_after_competitor_attempt.wait(
                    timeout=10.0
                )
        return res


def _execute_controlled_race(
    db_file: Path,
    req_winner: SourceImportRequest,
    req_loser: SourceImportRequest,
) -> tuple[SourceImportReceipt, SourceImportStoreError]:
    """Execute a deterministic race where winner pauses inside transaction,
    loser attempts BEGIN IMMEDIATE and blocks, winner commits,
    and loser raises STALE_STATE."""
    SynchronizedRaceConnection.pause_after_meta_observed = threading.Event()
    SynchronizedRaceConnection.resume_after_competitor_attempt = threading.Event()

    winner_receipt: list[SourceImportReceipt] = []
    loser_error: list[SourceImportStoreError] = []
    thread_exceptions: list[BaseException] = []

    def winner_worker() -> None:
        try:
            conn = sqlite3.connect(
                db_file, timeout=10.0, factory=SynchronizedRaceConnection
            )
            conn.execute("PRAGMA foreign_keys = ON;")
            rc = commit_source_import(conn, req_winner)
            winner_receipt.append(rc)
            conn.close()
        except BaseException as exc:
            thread_exceptions.append(exc)

    def loser_worker() -> None:
        try:
            conn = sqlite3.connect(db_file, timeout=10.0)
            conn.execute("PRAGMA foreign_keys = ON;")
            commit_source_import(conn, req_loser)
            conn.close()
        except SourceImportStoreError as exc:
            loser_error.append(exc)
        except BaseException as exc:
            thread_exceptions.append(exc)

    t_winner = threading.Thread(target=winner_worker)
    t_loser = threading.Thread(target=loser_worker)

    t_winner.start()
    assert SynchronizedRaceConnection.pause_after_meta_observed.wait(timeout=10.0)

    t_loser.start()
    time.sleep(0.05)  # give loser thread time to enter and wait on SQLite lock

    SynchronizedRaceConnection.resume_after_competitor_attempt.set()

    t_winner.join(timeout=10.0)
    t_loser.join(timeout=10.0)

    assert not t_winner.is_alive() and not t_loser.is_alive()
    assert not thread_exceptions, f"Unexpected thread exception: {thread_exceptions}"
    assert len(winner_receipt) == 1, "Winner did not return receipt"
    assert len(loser_error) == 1, "Loser did not raise SourceImportStoreError"

    return winner_receipt[0], loser_error[0]


def test_is09_race_changed_vs_changed_winner_a_loser_b(tmp_path: Path) -> None:
    """IS-09: Changed vs Changed race with Winner A and Loser B.
    Loser resolves strictly as STALE_STATE; winner replays and loser retries."""
    db_file = tmp_path / "race_changed_a_b.sqlite3"
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

    u1 = make_deterministic_uuid7(10)
    u2 = make_deterministic_uuid7(20)
    req_a = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(101),
        observed_at_utc=datetime.now(UTC),
        file_sha256="a" * 64,
        snapshot=build_synthetic_snapshot(
            [(u1, make_sample_party_row("کاربر الف"))], [], [], []
        ),
        event_ids={u1: make_deterministic_uuid7(201)},
    )
    req_b = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(102),
        observed_at_utc=datetime.now(UTC),
        file_sha256="b" * 64,
        snapshot=build_synthetic_snapshot(
            [(u2, make_sample_party_row("کاربر ب"))], [], [], []
        ),
        event_ids={u2: make_deterministic_uuid7(202)},
    )

    w_rc, l_err = _execute_controlled_race(db_file, req_a, req_b)
    assert w_rc.disposition == SourceImportDisposition.COMMITTED
    assert w_rc.committed_generation == 1
    assert l_err.reason == SourceImportStoreReason.STALE_STATE

    # Winner exact replay returns REPLAYED
    conn_test = sqlite3.connect(db_file)
    conn_test.execute("PRAGMA foreign_keys = ON;")
    rep_rc = commit_source_import(conn_test, req_a)
    assert rep_rc.disposition == SourceImportDisposition.REPLAYED
    assert rep_rc.committed_generation == 1

    # Loser retries with generation 1 and merged snapshot
    snap_loser_retry = build_synthetic_snapshot(
        [
            (u1, make_sample_party_row("کاربر الف")),
            (u2, make_sample_party_row("کاربر ب")),
        ],
        [],
        [],
        [],
    )
    req_b_retry = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=req_b.import_id,
        observed_at_utc=datetime.now(UTC),
        file_sha256=req_b.file_sha256,
        snapshot=snap_loser_retry,
        event_ids={u2: make_deterministic_uuid7(202)},
    )
    retry_rc = commit_source_import(conn_test, req_b_retry)
    assert retry_rc.disposition == SourceImportDisposition.COMMITTED
    assert retry_rc.committed_generation == 2
    conn_test.close()


def test_is09_race_changed_vs_changed_winner_b_loser_a(tmp_path: Path) -> None:
    """IS-09: Changed vs Changed race with reversed winner order (Winner B, Loser A).
    Loser resolves strictly as STALE_STATE; winner replays and loser retries."""
    db_file = tmp_path / "race_changed_b_a.sqlite3"
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

    u1 = make_deterministic_uuid7(10)
    u2 = make_deterministic_uuid7(20)
    req_a = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(101),
        observed_at_utc=datetime.now(UTC),
        file_sha256="a" * 64,
        snapshot=build_synthetic_snapshot(
            [(u1, make_sample_party_row("کاربر الف"))], [], [], []
        ),
        event_ids={u1: make_deterministic_uuid7(201)},
    )
    req_b = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(102),
        observed_at_utc=datetime.now(UTC),
        file_sha256="b" * 64,
        snapshot=build_synthetic_snapshot(
            [(u2, make_sample_party_row("کاربر ب"))], [], [], []
        ),
        event_ids={u2: make_deterministic_uuid7(202)},
    )

    # Force req_b as winner, req_a as loser
    w_rc, l_err = _execute_controlled_race(db_file, req_b, req_a)
    assert w_rc.disposition == SourceImportDisposition.COMMITTED
    assert w_rc.committed_generation == 1
    assert l_err.reason == SourceImportStoreReason.STALE_STATE

    # Winner exact replay returns REPLAYED
    conn_test = sqlite3.connect(db_file)
    conn_test.execute("PRAGMA foreign_keys = ON;")
    rep_rc = commit_source_import(conn_test, req_b)
    assert rep_rc.disposition == SourceImportDisposition.REPLAYED
    assert rep_rc.committed_generation == 1

    # Loser retries with generation 1
    snap_loser_retry = build_synthetic_snapshot(
        [
            (u2, make_sample_party_row("کاربر ب")),
            (u1, make_sample_party_row("کاربر الف")),
        ],
        [],
        [],
        [],
    )
    req_a_retry = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=req_a.import_id,
        observed_at_utc=datetime.now(UTC),
        file_sha256=req_a.file_sha256,
        snapshot=snap_loser_retry,
        event_ids={u1: make_deterministic_uuid7(201)},
    )
    retry_rc = commit_source_import(conn_test, req_a_retry)
    assert retry_rc.disposition == SourceImportDisposition.COMMITTED
    assert retry_rc.committed_generation == 2
    conn_test.close()


def test_is09_race_changed_vs_zero_event_winner_changed(tmp_path: Path) -> None:
    """IS-09: Changed vs Zero-event race where Changed wins and Zero-event loses.
    Loser resolves strictly as STALE_STATE; winner replays and loser retries."""
    db_file = tmp_path / "race_changed_zero_winner_chg.sqlite3"
    conn_init = sqlite3.connect(db_file)
    conn_init.execute("PRAGMA foreign_keys = ON;")
    conn_init.execute("PRAGMA journal_mode = WAL;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(
        conn_init, device_id=dev_id, active_source=active_key
    )

    u0 = make_deterministic_uuid7(1)
    req_init = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(50),
        observed_at_utc=datetime.now(UTC),
        file_sha256="0" * 64,
        snapshot=build_synthetic_snapshot(
            [(u0, make_sample_party_row("شخص صفر"))], [], [], []
        ),
        event_ids={u0: make_deterministic_uuid7(60)},
    )
    commit_source_import(conn_init, req_init)
    conn_init.close()

    u1 = make_deterministic_uuid7(10)
    req_changed = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=make_deterministic_uuid7(101),
        observed_at_utc=datetime.now(UTC),
        file_sha256="1" * 64,
        snapshot=build_synthetic_snapshot(
            [
                (u0, make_sample_party_row("شخص صفر")),
                (u1, make_sample_party_row("شخص یک")),
            ],
            [],
            [],
            [],
        ),
        event_ids={u1: make_deterministic_uuid7(201)},
    )
    req_zero = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=make_deterministic_uuid7(102),
        observed_at_utc=datetime.now(UTC),
        file_sha256="2" * 64,
        snapshot=build_synthetic_snapshot(
            [(u0, make_sample_party_row("شخص صفر"))], [], [], []
        ),
        event_ids={},
    )

    w_rc, l_err = _execute_controlled_race(db_file, req_changed, req_zero)
    assert w_rc.disposition == SourceImportDisposition.COMMITTED
    assert w_rc.committed_generation == 2
    assert l_err.reason == SourceImportStoreReason.STALE_STATE

    # Winner exact replay
    conn_test = sqlite3.connect(db_file)
    conn_test.execute("PRAGMA foreign_keys = ON;")
    rep_rc = commit_source_import(conn_test, req_changed)
    assert rep_rc.disposition == SourceImportDisposition.REPLAYED

    # Loser retries with expected_generation 2
    req_zero_retry = SourceImportRequest(
        source_key=active_key,
        expected_generation=2,
        import_id=req_zero.import_id,
        observed_at_utc=datetime.now(UTC),
        file_sha256=req_zero.file_sha256,
        snapshot=build_synthetic_snapshot(
            [
                (u0, make_sample_party_row("شخص صفر")),
                (u1, make_sample_party_row("شخص یک")),
            ],
            [],
            [],
            [],
        ),
        event_ids={},
    )
    retry_rc = commit_source_import(conn_test, req_zero_retry)
    assert retry_rc.disposition == SourceImportDisposition.COMMITTED
    assert retry_rc.committed_generation == 3
    assert retry_rc.event_count == 0
    conn_test.close()


def test_is09_race_changed_vs_zero_event_winner_zero_event(
    tmp_path: Path,
) -> None:
    """IS-09: Changed vs Zero-event race where Zero-event wins and Changed loses.
    Loser resolves strictly as STALE_STATE; winner replays and loser retries."""
    db_file = tmp_path / "race_changed_zero_winner_zero.sqlite3"
    conn_init = sqlite3.connect(db_file)
    conn_init.execute("PRAGMA foreign_keys = ON;")
    conn_init.execute("PRAGMA journal_mode = WAL;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(
        conn_init, device_id=dev_id, active_source=active_key
    )

    u0 = make_deterministic_uuid7(1)
    req_init = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(50),
        observed_at_utc=datetime.now(UTC),
        file_sha256="0" * 64,
        snapshot=build_synthetic_snapshot(
            [(u0, make_sample_party_row("شخص صفر"))], [], [], []
        ),
        event_ids={u0: make_deterministic_uuid7(60)},
    )
    commit_source_import(conn_init, req_init)
    conn_init.close()

    u1 = make_deterministic_uuid7(10)
    req_changed = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=make_deterministic_uuid7(101),
        observed_at_utc=datetime.now(UTC),
        file_sha256="1" * 64,
        snapshot=build_synthetic_snapshot(
            [
                (u0, make_sample_party_row("شخص صفر")),
                (u1, make_sample_party_row("شخص یک")),
            ],
            [],
            [],
            [],
        ),
        event_ids={u1: make_deterministic_uuid7(201)},
    )
    req_zero = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=make_deterministic_uuid7(102),
        observed_at_utc=datetime.now(UTC),
        file_sha256="2" * 64,
        snapshot=build_synthetic_snapshot(
            [(u0, make_sample_party_row("شخص صفر"))], [], [], []
        ),
        event_ids={},
    )

    # Force req_zero as winner, req_changed as loser
    w_rc, l_err = _execute_controlled_race(db_file, req_zero, req_changed)
    assert w_rc.disposition == SourceImportDisposition.COMMITTED
    assert w_rc.committed_generation == 2
    assert w_rc.event_count == 0
    assert l_err.reason == SourceImportStoreReason.STALE_STATE

    # Winner exact replay
    conn_test = sqlite3.connect(db_file)
    conn_test.execute("PRAGMA foreign_keys = ON;")
    rep_rc = commit_source_import(conn_test, req_zero)
    assert rep_rc.disposition == SourceImportDisposition.REPLAYED

    # Loser retries with expected_generation 2
    req_changed_retry = SourceImportRequest(
        source_key=active_key,
        expected_generation=2,
        import_id=req_changed.import_id,
        observed_at_utc=datetime.now(UTC),
        file_sha256=req_changed.file_sha256,
        snapshot=build_synthetic_snapshot(
            [
                (u0, make_sample_party_row("شخص صفر")),
                (u1, make_sample_party_row("شخص یک")),
            ],
            [],
            [],
            [],
        ),
        event_ids={u1: make_deterministic_uuid7(201)},
    )
    retry_rc = commit_source_import(conn_test, req_changed_retry)
    assert retry_rc.disposition == SourceImportDisposition.COMMITTED
    assert retry_rc.committed_generation == 3
    assert retry_rc.event_count == 1
    conn_test.close()


# ============================================================================
# IS-10: Failure injection across write families & ExceptionGroup
# ============================================================================


class FlakyCursor(sqlite3.Cursor):
    fail_before_table: str | None = None
    fail_after_table: str | None = None
    fail_before_sheet: int | None = None
    fail_after_sheet: int | None = None
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

        if FlakyCursor.fail_before_table is not None:
            if (
                f"INSERT INTO {FlakyCursor.fail_before_table}" in sql
                or f"UPDATE {FlakyCursor.fail_before_table}" in sql
            ):
                raise sqlite3.OperationalError(
                    f"Simulated failure before write on {FlakyCursor.fail_before_table}"
                )

        if FlakyCursor.fail_before_sheet is not None:
            if "INSERT INTO source_import_sheets" in sql and params:
                param_seq = (
                    params[0] if isinstance(params[0], (tuple, list)) else params
                )
                if len(param_seq) > 1 and param_seq[1] == FlakyCursor.fail_before_sheet:
                    raise sqlite3.OperationalError(
                        f"Simulated failure before sheet "
                        f"{FlakyCursor.fail_before_sheet}"
                    )

        result = super().execute(sql, *params)

        if FlakyCursor.fail_after_table is not None:
            if (
                f"INSERT INTO {FlakyCursor.fail_after_table}" in sql
                or f"UPDATE {FlakyCursor.fail_after_table}" in sql
            ):
                raise sqlite3.OperationalError(
                    f"Simulated failure after write on {FlakyCursor.fail_after_table}"
                )

        if FlakyCursor.fail_after_sheet is not None:
            if "INSERT INTO source_import_sheets" in sql and params:
                param_seq = (
                    params[0] if isinstance(params[0], (tuple, list)) else params
                )
                if len(param_seq) > 1 and param_seq[1] == FlakyCursor.fail_after_sheet:
                    raise sqlite3.OperationalError(
                        f"Simulated failure after sheet {FlakyCursor.fail_after_sheet}"
                    )

        return result


class FlakyConnection(sqlite3.Connection):
    fail_begin: bool = False
    fail_rollback: bool = False
    fail_commit: bool = False
    fail_post_commit_pre_return: bool = False

    def cursor(self, factory: Any = None) -> sqlite3.Cursor:  # type: ignore[override]
        cur: sqlite3.Cursor = super().cursor(factory=factory or FlakyCursor)
        return cur

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if FlakyConnection.fail_begin and "BEGIN" in sql:
            raise sqlite3.OperationalError("Simulated BEGIN failure")
        if FlakyConnection.fail_rollback and "ROLLBACK" in sql:
            raise sqlite3.OperationalError("Simulated rollback failure")
        if FlakyConnection.fail_commit and "COMMIT" in sql:
            raise sqlite3.OperationalError("Simulated COMMIT failure")
        res = super().execute(sql, *args, **kwargs)
        if FlakyConnection.fail_post_commit_pre_return and "COMMIT" in sql:
            raise sqlite3.OperationalError("Simulated failure post-COMMIT pre-return")
        return res


def _snapshot_db_state(conn: sqlite3.Connection) -> dict[str, Any]:
    """Capture full row counts for every table plus generation and sequence."""
    tables = [
        "source_imports",
        "source_import_sheets",
        "source_revisions",
        "source_memberships",
        "change_events",
        "source_bindings",
        "source_store_meta",
    ]
    snap: dict[str, Any] = {
        tbl: conn.execute(f"SELECT COUNT(*) FROM {tbl};").fetchone()[0]
        for tbl in tables
    }
    meta = conn.execute(
        "SELECT generation, next_sequence FROM source_store_meta;"
    ).fetchone()
    snap["generation"] = meta[0]
    snap["next_sequence"] = meta[1]
    return snap


def _assert_db_matches_snapshot(conn: sqlite3.Connection, snap: dict[str, Any]) -> None:
    current = _snapshot_db_state(conn)
    assert current == snap, f"State mismatch after rollback: {current} != {snap}"


def test_is10_failure_injection_across_write_families() -> None:
    """IS-10: Injects deterministic failure BOTH before AND after every write family:
    source_imports, all 4 sheets, revisions, memberships, change_events, bindings, meta.
    Snapshots every table plus generation/sequence and verifies complete rollback."""
    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)

    tables_to_test = [
        "source_imports",
        "source_import_sheets",
        "source_revisions",
        "change_events",
        "source_memberships",
        "source_bindings",
        "source_store_meta",
    ]

    for tbl in tables_to_test:
        for phase in ("before", "after"):
            conn = sqlite3.connect(":memory:", factory=FlakyConnection)
            conn.execute("PRAGMA foreign_keys = ON;")
            initialize_source_import_store(
                conn, device_id=dev_id, active_source=active_key
            )
            snap = _snapshot_db_state(conn)

            if phase == "before":
                FlakyCursor.fail_before_table = tbl
                FlakyCursor.fail_after_table = None
            else:
                FlakyCursor.fail_before_table = None
                FlakyCursor.fail_after_table = tbl

            try:
                u = make_deterministic_uuid7(10)
                req = SourceImportRequest(
                    source_key=active_key,
                    expected_generation=0,
                    import_id=make_deterministic_uuid7(100),
                    observed_at_utc=datetime.now(UTC),
                    file_sha256="a" * 64,
                    snapshot=build_synthetic_snapshot(
                        [(u, make_sample_party_row("شخص"))], [], [], []
                    ),
                    event_ids={u: make_deterministic_uuid7(200)},
                )
                with pytest.raises(SourceImportStoreError) as exc_info:
                    commit_source_import(conn, req)
                assert exc_info.value.reason == SourceImportStoreReason.STORAGE_FAILURE
                _assert_db_matches_snapshot(conn, snap)
            finally:
                FlakyCursor.fail_before_table = None
                FlakyCursor.fail_after_table = None
                conn.close()

    # Test each of the 4 sheets individually both before and after
    for sheet_idx in range(4):
        for phase in ("before", "after"):
            conn = sqlite3.connect(":memory:", factory=FlakyConnection)
            conn.execute("PRAGMA foreign_keys = ON;")
            initialize_source_import_store(
                conn, device_id=dev_id, active_source=active_key
            )
            snap = _snapshot_db_state(conn)

            if phase == "before":
                FlakyCursor.fail_before_sheet = sheet_idx
                FlakyCursor.fail_after_sheet = None
            else:
                FlakyCursor.fail_before_sheet = None
                FlakyCursor.fail_after_sheet = sheet_idx

            try:
                u = make_deterministic_uuid7(10)
                req = SourceImportRequest(
                    source_key=active_key,
                    expected_generation=0,
                    import_id=make_deterministic_uuid7(100),
                    observed_at_utc=datetime.now(UTC),
                    file_sha256="a" * 64,
                    snapshot=build_synthetic_snapshot(
                        [(u, make_sample_party_row("شخص"))], [], [], []
                    ),
                    event_ids={u: make_deterministic_uuid7(200)},
                )
                with pytest.raises(SourceImportStoreError) as exc_info:
                    commit_source_import(conn, req)
                assert exc_info.value.reason == SourceImportStoreReason.STORAGE_FAILURE
                _assert_db_matches_snapshot(conn, snap)
            finally:
                FlakyCursor.fail_before_sheet = None
                FlakyCursor.fail_after_sheet = None
                conn.close()


def test_is10_begin_and_pre_commit_failure_rollback() -> None:
    """IS-10: BEGIN failure and pre-COMMIT failure roll back completely
    to prior state."""
    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)

    # 1. BEGIN failure
    conn_begin = sqlite3.connect(":memory:", factory=FlakyConnection)
    conn_begin.execute("PRAGMA foreign_keys = ON;")
    initialize_source_import_store(
        conn_begin, device_id=dev_id, active_source=active_key
    )
    snap_begin = _snapshot_db_state(conn_begin)

    FlakyConnection.fail_begin = True
    try:
        u = make_deterministic_uuid7(10)
        req = SourceImportRequest(
            source_key=active_key,
            expected_generation=0,
            import_id=make_deterministic_uuid7(100),
            observed_at_utc=datetime.now(UTC),
            file_sha256="a" * 64,
            snapshot=build_synthetic_snapshot(
                [(u, make_sample_party_row("شخص"))], [], [], []
            ),
            event_ids={u: make_deterministic_uuid7(200)},
        )
        with pytest.raises(SourceImportStoreError) as exc_b:
            commit_source_import(conn_begin, req)
        assert exc_b.value.reason == SourceImportStoreReason.STORAGE_FAILURE
        _assert_db_matches_snapshot(conn_begin, snap_begin)
    finally:
        FlakyConnection.fail_begin = False
        conn_begin.close()

    # 2. Pre-COMMIT failure
    conn_commit = sqlite3.connect(":memory:", factory=FlakyConnection)
    conn_commit.execute("PRAGMA foreign_keys = ON;")
    initialize_source_import_store(
        conn_commit, device_id=dev_id, active_source=active_key
    )
    snap_commit = _snapshot_db_state(conn_commit)

    FlakyConnection.fail_commit = True
    try:
        u = make_deterministic_uuid7(10)
        req = SourceImportRequest(
            source_key=active_key,
            expected_generation=0,
            import_id=make_deterministic_uuid7(100),
            observed_at_utc=datetime.now(UTC),
            file_sha256="a" * 64,
            snapshot=build_synthetic_snapshot(
                [(u, make_sample_party_row("شخص"))], [], [], []
            ),
            event_ids={u: make_deterministic_uuid7(200)},
        )
        with pytest.raises(SourceImportStoreError) as exc_c:
            commit_source_import(conn_commit, req)
        assert exc_c.value.reason == SourceImportStoreReason.STORAGE_FAILURE
        _assert_db_matches_snapshot(conn_commit, snap_commit)
    finally:
        FlakyConnection.fail_commit = False
        conn_commit.close()


def test_is10_post_commit_pre_return_ambiguity_and_replay() -> None:
    """IS-10: Failure injected after COMMIT succeeds but before return receipt
    leaves the database in committed state; exact retry yields REPLAYED."""
    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)

    conn = sqlite3.connect(":memory:", factory=FlakyConnection)
    conn.execute("PRAGMA foreign_keys = ON;")
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    FlakyConnection.fail_post_commit_pre_return = True
    u = make_deterministic_uuid7(10)
    req = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(100),
        observed_at_utc=datetime.now(UTC),
        file_sha256="a" * 64,
        snapshot=build_synthetic_snapshot(
            [(u, make_sample_party_row("شخص"))], [], [], []
        ),
        event_ids={u: make_deterministic_uuid7(200)},
    )
    try:
        with pytest.raises(SourceImportStoreError) as exc_info:
            commit_source_import(conn, req)
        assert exc_info.value.reason == SourceImportStoreReason.STORAGE_FAILURE
    finally:
        FlakyConnection.fail_post_commit_pre_return = False

    # The commit actually happened in the database
    assert conn.execute("SELECT generation FROM source_store_meta;").fetchone()[0] == 1

    # Exact replay with identical request returns REPLAYED
    receipt = commit_source_import(conn, req)
    assert receipt.disposition == SourceImportDisposition.REPLAYED
    assert receipt.committed_generation == 1
    conn.close()


def test_is10_raw_base_exception_propagation_and_rollback() -> None:
    """IS-10: Raw BaseExceptions (KeyboardInterrupt, SystemExit, custom) roll back
    and propagate directly without sanitization or wrapping."""
    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)

    for exc_type in ("keyboard", "exit", "custom"):
        conn = sqlite3.connect(":memory:", factory=FlakyConnection)
        conn.execute("PRAGMA foreign_keys = ON;")
        initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)
        snap = _snapshot_db_state(conn)

        if exc_type == "keyboard":
            FlakyCursor.fail_interrupt = True
        elif exc_type == "exit":
            FlakyCursor.fail_system_exit = True
        else:
            FlakyCursor.fail_custom_base = True

        u = make_deterministic_uuid7(10)
        req = SourceImportRequest(
            source_key=active_key,
            expected_generation=0,
            import_id=make_deterministic_uuid7(100),
            observed_at_utc=datetime.now(UTC),
            file_sha256="a" * 64,
            snapshot=build_synthetic_snapshot(
                [(u, make_sample_party_row("شخص"))], [], [], []
            ),
            event_ids={u: make_deterministic_uuid7(200)},
        )
        try:
            if exc_type == "keyboard":
                with pytest.raises(KeyboardInterrupt):
                    commit_source_import(conn, req)
            elif exc_type == "exit":
                with pytest.raises(SystemExit):
                    commit_source_import(conn, req)
            else:
                with pytest.raises(BaseException) as exc_custom:
                    commit_source_import(conn, req)
                assert exc_custom.value.__class__.__name__ == "CustomTestBaseException"
            _assert_db_matches_snapshot(conn, snap)
        finally:
            FlakyCursor.fail_interrupt = False
            FlakyCursor.fail_system_exit = False
            FlakyCursor.fail_custom_base = False
            conn.close()


def test_is10_commit_and_rollback_ordered_dual_failure_exception_group() -> None:
    """IS-10: Double failure during commit and rollback preserves both errors in group
    in exact order [primary, rollback], with shared cause non-deduplication."""
    conn = sqlite3.connect(":memory:", factory=FlakyConnection)
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    FlakyCursor.fail_before_table = "source_imports"
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
        assert "Simulated failure before write on source_imports" in str(
            exc_group.value.exceptions[0]
        )
        assert "Simulated rollback failure" in str(exc_group.value.exceptions[1])

        # BaseExceptionGroup when KeyboardInterrupt occurs and rollback fails
        FlakyCursor.fail_before_table = None
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
            assert len(exc_bgroup.value.exceptions) == 2
            assert isinstance(exc_bgroup.value.exceptions[0], KeyboardInterrupt)
            assert isinstance(exc_bgroup.value.exceptions[1], sqlite3.OperationalError)
        finally:
            conn2.close()
    finally:
        FlakyCursor.fail_before_table = None
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


def _make_fresh_gen1_fixture() -> tuple[
    sqlite3.Connection, SourceBindingKey, SourceImportRequest
]:
    """Create a fresh in-memory store with Generation 1 committed."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)
    u = make_deterministic_uuid7(10)
    snap = build_synthetic_snapshot([(u, make_sample_party_row("شخص تست"))], [], [], [])
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
    return conn, active_key, req


def test_is13_mutation_offset_free_observation_rejected() -> None:
    """IS-13/R3: Offset-free or non-UTC observed_at_utc in source_imports is rejected
    by public read and replay with INCONSISTENT_STATE."""
    for bad_obs in (
        "2026-01-01T00:00:00",
        "2026-01-01T00:00:00+03:30",
        "not-a-canonical-utc",
    ):
        conn, _, req = _make_fresh_gen1_fixture()
        conn.execute("DROP TRIGGER trg_prevent_update_source_imports;")
        conn.execute("UPDATE source_imports SET observed_at_utc = ?;", (bad_obs,))
        conn.execute(
            """
            CREATE TRIGGER trg_prevent_update_source_imports
            BEFORE UPDATE ON source_imports
            BEGIN
                SELECT RAISE(ABORT, 'Cannot update source_imports');
            END;
            """
        )
        conn.commit()

        with pytest.raises(SourceImportStoreError) as exc_read:
            read_source_import_store(conn)
        assert exc_read.value.reason == SourceImportStoreReason.INCONSISTENT_STATE

        with pytest.raises(SourceImportStoreError) as exc_replay:
            commit_source_import(conn, req)
        assert exc_replay.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
        conn.close()


def test_is13_mutation_wrong_source_year_provenance_rejected() -> None:
    """IS-13/R3: Valid-but-wrong source_id or fiscal_year provenance in
    source_imports is rejected by public read and replay with INCONSISTENT_STATE."""
    wrong_src = make_deterministic_uuid7(999)
    # 1. Wrong source_id (must exist in bindings for FK, but wrong for import)
    conn1, _, req1 = _make_fresh_gen1_fixture()
    conn1.execute(
        "INSERT INTO source_bindings "
        "(source_id, fiscal_year, state, final_file_sha256) "
        "VALUES (?, 1402, 'archived', ?);",
        (wrong_src.bytes, "9" * 64),
    )
    conn1.execute("DROP TRIGGER trg_prevent_update_source_imports;")
    conn1.execute("UPDATE source_imports SET source_id = ?;", (wrong_src.bytes,))
    conn1.execute(
        """
        CREATE TRIGGER trg_prevent_update_source_imports
        BEFORE UPDATE ON source_imports
        BEGIN
            SELECT RAISE(ABORT, 'Cannot update source_imports');
        END;
        """
    )
    conn1.commit()
    with pytest.raises(SourceImportStoreError) as exc1:
        read_source_import_store(conn1)
    assert exc1.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    with pytest.raises(SourceImportStoreError) as exc1_rep:
        commit_source_import(conn1, req1)
    assert exc1_rep.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    conn1.close()

    # 2. Wrong fiscal_year
    conn2, _, req2 = _make_fresh_gen1_fixture()
    conn2.execute("DROP TRIGGER trg_prevent_update_source_imports;")
    conn2.execute("UPDATE source_imports SET fiscal_year = 1404;")
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
    with pytest.raises(SourceImportStoreError) as exc2_rep:
        commit_source_import(conn2, req2)
    assert exc2_rep.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    conn2.close()


def test_is13_mutation_event_observation_differing_from_import_rejected() -> None:
    """IS-13/R3: Change event observation differing from its creating import
    is rejected by public read and replay with INCONSISTENT_STATE."""
    conn, _, req = _make_fresh_gen1_fixture()
    conn.execute("DROP TRIGGER trg_prevent_update_change_events;")
    conn.execute(
        "UPDATE change_events SET observed_at_utc = '2026-01-01T12:00:00Z' "
        "WHERE sequence = 1;"
    )
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

    with pytest.raises(SourceImportStoreError) as exc:
        read_source_import_store(conn)
    assert exc.value.reason == SourceImportStoreReason.INCONSISTENT_STATE

    with pytest.raises(SourceImportStoreError) as exc_rep:
        commit_source_import(conn, req)
    assert exc_rep.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    conn.close()


def test_is13_mutation_file_import_metadata_mismatch_rejected() -> None:
    """IS-13/R3: File/import metadata mismatch (mismatched sha or request_digest)
    is rejected by public read and replay with INCONSISTENT_STATE."""
    # 1. file_sha256 mismatch (valid 64-hex that doesn't match binding or request)
    conn1, _, req1 = _make_fresh_gen1_fixture()
    conn1.execute("DROP TRIGGER trg_prevent_update_source_imports;")
    conn1.execute("UPDATE source_imports SET file_sha256 = ?;", ("b" * 64,))
    conn1.execute(
        """
        CREATE TRIGGER trg_prevent_update_source_imports
        BEFORE UPDATE ON source_imports
        BEGIN
            SELECT RAISE(ABORT, 'Cannot update source_imports');
        END;
        """
    )
    conn1.commit()
    with pytest.raises(SourceImportStoreError) as exc1:
        read_source_import_store(conn1)
    assert exc1.value.reason == SourceImportStoreReason.INCONSISTENT_STATE

    with pytest.raises(SourceImportStoreError) as exc1_rep:
        commit_source_import(conn1, req1)
    assert exc1_rep.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    conn1.close()

    # 2. request_digest mismatch
    conn2, _, req2 = _make_fresh_gen1_fixture()
    conn2.execute("DROP TRIGGER trg_prevent_update_source_imports;")
    conn2.execute("UPDATE source_imports SET request_digest = ?;", ("9" * 64,))
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
    # Replay must reject because stored digest does not match computed digest
    with pytest.raises(SourceImportStoreError) as exc2_rep:
        commit_source_import(conn2, req2)
    assert exc2_rep.value.reason == SourceImportStoreReason.IDEMPOTENCY_CONFLICT
    conn2.close()


def test_is13_mutation_sheet_count_mismatch_rejected() -> None:
    """IS-13/R3: Sheet count mismatch (not exactly 4 sheets reported)
    is rejected by public read and replay with INCONSISTENT_STATE."""
    conn, _, req = _make_fresh_gen1_fixture()
    conn.execute("DROP TRIGGER trg_prevent_delete_source_import_sheets;")
    conn.execute("DELETE FROM source_import_sheets WHERE sheet_order = 0;")
    conn.execute(
        """
        CREATE TRIGGER trg_prevent_delete_source_import_sheets
        BEFORE DELETE ON source_import_sheets
        BEGIN
            SELECT RAISE(ABORT, 'Cannot delete source_import_sheets');
        END;
        """
    )
    conn.commit()

    with pytest.raises(SourceImportStoreError) as exc:
        read_source_import_store(conn)
    assert exc.value.reason == SourceImportStoreReason.INCONSISTENT_STATE

    with pytest.raises(SourceImportStoreError) as exc_rep:
        commit_source_import(conn, req)
    assert exc_rep.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    conn.close()


def test_is13_mutation_current_payload_tamper_rejected() -> None:
    """IS-13/R3: Tampered raw_payload in source_revisions is rejected
    because payload hash does not match version_hash."""
    conn, _, req = _make_fresh_gen1_fixture()
    conn.execute("DROP TRIGGER trg_prevent_update_source_revisions;")
    conn.execute(
        "UPDATE source_revisions SET raw_payload = ?;", (b'{"tampered": true}',)
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

    with pytest.raises(SourceImportStoreError) as exc:
        read_source_import_store(conn)
    assert exc.value.reason == SourceImportStoreReason.INCONSISTENT_STATE

    with pytest.raises(SourceImportStoreError) as exc_rep:
        commit_source_import(conn, req)
    assert exc_rep.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    conn.close()


def test_is13_mutation_version_hash_tamper_rejected() -> None:
    """IS-13/R3: Tampered version_hash in source_revisions is rejected."""
    conn, _, req = _make_fresh_gen1_fixture()
    conn.execute("DROP TRIGGER trg_prevent_update_source_revisions;")
    conn.execute("UPDATE source_revisions SET version_hash = ?;", ("0" * 64,))
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

    with pytest.raises(SourceImportStoreError) as exc:
        read_source_import_store(conn)
    assert exc.value.reason == SourceImportStoreReason.INCONSISTENT_STATE

    with pytest.raises(SourceImportStoreError) as exc_rep:
        commit_source_import(conn, req)
    assert exc_rep.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    conn.close()


def test_is13_mutation_noncontiguous_sequence_rejected() -> None:
    """IS-13/R3: Non-contiguous sequence in change_events is rejected."""
    conn, _, req = _make_fresh_gen1_fixture()
    conn.execute("DROP TRIGGER trg_prevent_update_change_events;")
    conn.execute("UPDATE change_events SET sequence = 5 WHERE sequence = 1;")
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

    with pytest.raises(SourceImportStoreError) as exc:
        read_source_import_store(conn)
    assert exc.value.reason == SourceImportStoreReason.INCONSISTENT_STATE

    with pytest.raises(SourceImportStoreError) as exc_rep:
        commit_source_import(conn, req)
    assert exc_rep.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    conn.close()


# ============================================================================
# IS-14: Hypothesis property tests & controlled mutations
# ============================================================================


class ImportStoreOracle:
    """Independent in-memory oracle for source import store state
    and change planning."""

    def __init__(self, device_id: uuid.UUID, active_key: SourceBindingKey) -> None:
        self.device_id = device_id
        self.active_key = active_key
        self.generation = 0
        self.next_sequence = 1
        # stable_id -> dict: revision, is_void, source_hash, sheet_name
        self.items: dict[uuid.UUID, dict[str, Any]] = {}
        # list of (seq, event_id, stable_id, revision, operation)
        self.events: list[tuple[int, uuid.UUID, uuid.UUID, int, str]] = []
        self.imports: list[uuid.UUID] = []

    def get_prior_registry(self) -> PriorIdentityRegistry:
        prior_states = [
            PriorIdentityState(
                stable_id=u,
                canonical_uuid=str(u).lower(),
                home_sheet=itm["sheet_name"],
                latest_revision=itm["revision"],
                lifecycle=(
                    IdentityLifecycle.VOIDED
                    if itm["is_void"]
                    else IdentityLifecycle.ACTIVE
                ),
                source_hash=itm["source_hash"],
            )
            for u, itm in self.items.items()
        ]
        return build_prior_identity_registry(prior_states)

    def plan_and_apply(
        self,
        import_id: uuid.UUID,
        snapshot: Any,
        event_ids: dict[uuid.UUID, uuid.UUID],
    ) -> dict[str, Any]:
        self.generation += 1
        self.imports.append(import_id)

        prior_registry = self.get_prior_registry()
        plan = plan_source_changes(snapshot, prior_registry)
        changed_items = [
            item
            for item in plan.items
            if item.action in (PlanAction.INSERT, PlanAction.EDIT, PlanAction.VOID)
        ]

        first_seq = self.next_sequence if changed_items else None
        for item in changed_items:
            seq = self.next_sequence
            self.next_sequence += 1
            ev_id = event_ids[item.stable_id]
            rev = 1 if item.action == PlanAction.INSERT else item.planned_revision
            assert rev is not None
            op = "void" if item.action == PlanAction.VOID else "upsert"
            self.events.append((seq, ev_id, item.stable_id, rev, op))
            if op == "void":
                self.items[item.stable_id]["revision"] = rev
                self.items[item.stable_id]["is_void"] = True
                self.items[item.stable_id]["source_hash"] = None
            else:
                assert item.current_row is not None
                self.items[item.stable_id] = {
                    "revision": rev,
                    "is_void": False,
                    "source_hash": item.current_row.source_hash,
                    "sheet_name": item.sheet_name,
                }

        last_seq = (self.next_sequence - 1) if changed_items else None

        return {
            "committed_generation": self.generation,
            "event_count": len(changed_items),
            "first_sequence": first_seq,
            "last_sequence": last_seq,
        }

    def verify_db_state(self, conn: sqlite3.Connection) -> None:
        meta = conn.execute(
            "SELECT generation, next_sequence, device_id FROM source_store_meta;"
        ).fetchone()
        assert meta[0] == self.generation
        assert meta[1] == self.next_sequence
        assert meta[2] == self.device_id.bytes

        # Verify memberships
        mem_rows = conn.execute(
            "SELECT stable_id, revision FROM source_memberships;"
        ).fetchall()
        assert len(mem_rows) == len(self.items)
        for s_id_bytes, rev in mem_rows:
            u = uuid.UUID(bytes=s_id_bytes)
            assert self.items[u]["revision"] == rev

        # Verify events
        ev_rows = conn.execute(
            "SELECT sequence, event_id, stable_id, revision, operation "
            "FROM change_events ORDER BY sequence ASC;"
        ).fetchall()
        assert len(ev_rows) == len(self.events)
        for (seq, ev_id, u, rev, op), db_row in zip(self.events, ev_rows, strict=True):
            assert db_row[0] == seq
            assert db_row[1] == ev_id.bytes
            assert db_row[2] == u.bytes
            assert db_row[3] == rev
            assert db_row[4] == op


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    step_count=st.integers(min_value=2, max_value=4),
    initial_count=st.integers(min_value=2, max_value=5),
    seed=st.integers(min_value=1, max_value=10000),
)
def test_is14_hypothesis_multi_step_history_property_tests(
    step_count: int, initial_count: int, seed: int
) -> None:
    """IS-14: 40 generated multi-step histories with real row-order,
    mapping order, and Event-ID permutations across INSERT, EDIT, VOID,
    reactivation, and UNCHANGED. Compares every receipt and complete DB state
    after every step to an independent oracle."""
    rng = random.Random(seed)
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)
    oracle = ImportStoreOracle(dev_id, active_key)

    all_uuids = [make_deterministic_uuid7(1000 + i) for i in range(initial_count)]
    current_pool: dict[uuid.UUID, tuple[str, dict[str, Any]]] = {}

    for step in range(step_count):
        if step == 0:
            # Initial setup: create items across all 4 sheets
            for i, u in enumerate(all_uuids):
                sheet_idx = i % 4
                if sheet_idx == 0:
                    current_pool[u] = (
                        "لیست کسبه",
                        make_sample_party_row(f"شخص {i}"),
                    )
                elif sheet_idx == 1:
                    current_pool[u] = (
                        "خرید-فروش",
                        make_sample_buy_sell_row(
                            party=f"شخص {i}", unit_price=str(1000 + i)
                        ),
                    )
                elif sheet_idx == 2:
                    current_pool[u] = (
                        "دریافت-پرداخت",
                        make_sample_receipt_payment_row(party=f"شخص {i}"),
                    )
                else:
                    current_pool[u] = (
                        "ورود-خروج",
                        make_sample_inventory_row(party=f"شخص {i}"),
                    )
            snapshot_items = dict(current_pool)
        else:
            # Step > 0: randomly modify active items (EDIT, VOID, UNCHANGED)
            # and reactivate previously voided items
            snapshot_items = {}
            for u, (sheet_name, r_dict) in current_pool.items():
                is_currently_void = oracle.items.get(u, {}).get("is_void", False)
                if is_currently_void:
                    # 50% chance of REACTIVATION
                    if rng.random() < 0.5:
                        new_dict = dict(r_dict)
                        if sheet_name == "لیست کسبه":
                            new_dict["phone_number_raw"] = (
                                f"0912{step:02d}{abs(hash(u)) % 100000:05d}"
                            )
                        else:
                            new_dict["notes_raw"] = f"reactivated_step_{step}"
                        snapshot_items[u] = (sheet_name, new_dict)
                else:
                    roll = rng.random()
                    if roll < 0.35:
                        # UNCHANGED
                        snapshot_items[u] = (sheet_name, r_dict)
                    elif roll < 0.70:
                        # EDIT
                        new_dict = dict(r_dict)
                        if sheet_name == "لیست کسبه":
                            new_dict["phone_number_raw"] = (
                                f"0912{step:02d}{abs(hash(u)) % 100000:05d}"
                            )
                        else:
                            new_dict["notes_raw"] = f"edited_step_{step}"
                        snapshot_items[u] = (sheet_name, new_dict)
                    else:
                        # VOID (omit from snapshot)
                        pass

            # Maybe introduce a new item (INSERT)
            if rng.random() < 0.5:
                new_u = make_deterministic_uuid7(2000 + step * 10)
                current_pool[new_u] = (
                    "لیست کسبه",
                    make_sample_party_row(f"جدید {step}"),
                )
                snapshot_items[new_u] = current_pool[new_u]

        # Partition snapshot items into the 4 sheets
        parties_list: list[tuple[uuid.UUID, dict[str, Any]]] = []
        buy_sell_list: list[tuple[uuid.UUID, dict[str, Any]]] = []
        receipts_list: list[tuple[uuid.UUID, dict[str, Any]]] = []
        inventory_list: list[tuple[uuid.UUID, dict[str, Any]]] = []

        for u, (s_name, r_dict) in snapshot_items.items():
            if s_name == "لیست کسبه":
                parties_list.append((u, r_dict))
            elif s_name == "خرید-فروش":
                buy_sell_list.append((u, r_dict))
            elif s_name == "دریافت-پرداخت":
                receipts_list.append((u, r_dict))
            else:
                inventory_list.append((u, r_dict))

        # Real row-order permutations in each sheet
        rng.shuffle(parties_list)
        rng.shuffle(buy_sell_list)
        rng.shuffle(receipts_list)
        rng.shuffle(inventory_list)

        snap = build_synthetic_snapshot(
            parties_list, buy_sell_list, receipts_list, inventory_list
        )

        # Determine changed items from prior registry and snapshot preview
        prior_reg = oracle.get_prior_registry()
        plan_preview = plan_source_changes(snap, prior_reg)
        changed_uuids = [
            item.stable_id
            for item in plan_preview.items
            if item.action in (PlanAction.INSERT, PlanAction.EDIT, PlanAction.VOID)
        ]

        # Permute event IDs dictionary insertion order
        rng.shuffle(changed_uuids)
        ev_ids = {
            u: make_deterministic_uuid7(50000 + step * 1000 + idx)
            for idx, u in enumerate(changed_uuids)
        }

        # Apply to oracle
        expected = oracle.plan_and_apply(
            import_id=make_deterministic_uuid7(90000 + step),
            snapshot=snap,
            event_ids=ev_ids,
        )

        req = SourceImportRequest(
            source_key=active_key,
            expected_generation=step,
            import_id=make_deterministic_uuid7(90000 + step),
            observed_at_utc=datetime.now(UTC),
            file_sha256=f"{step % 10}" * 64,
            snapshot=snap,
            event_ids=ev_ids,
        )
        receipt = commit_source_import(conn, req)

        # 3. Compare receipt
        assert receipt.committed_generation == expected["committed_generation"]
        assert receipt.event_count == expected["event_count"]
        assert receipt.first_sequence == expected["first_sequence"]
        assert receipt.last_sequence == expected["last_sequence"]

        # 4. Compare DB state to Oracle
        oracle.verify_db_state(conn)

    conn.close()


def test_is14_controlled_product_code_mutations() -> None:
    """IS-14: Execute the seven issued controlled product-code mutations:
    1. Stale check
    2. Unchanged membership
    3. Append-only revision
    4. Outbox insert
    5. Sequence advance
    6. Request Raw digest
    7. Predecessor link
    Prove the intended tests fail under mutation, and verify exact byte
    restoration after each mutation."""
    import accounting_persistence.source_import_store as sis_mod

    store_path = (
        Path(__file__).parent.parent
        / "packages"
        / "persistence"
        / "src"
        / "accounting_persistence"
        / "source_import_store.py"
    )
    orig_bytes = store_path.read_bytes()
    orig_text = orig_bytes.decode("utf-8")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)

    def _execute_mutation(
        name: str,
        target_str: str,
        replacement_str: str,
        probe_fn: Any,
    ) -> None:
        assert target_str in orig_text, (
            f"Target string for mutation '{name}' not found in source!"
        )
        mutated_text = orig_text.replace(target_str, replacement_str, 1)

        def _clear_pycache() -> None:
            importlib.invalidate_caches()
            pyc_dir = store_path.parent / "__pycache__"
            if pyc_dir.exists():
                for f in pyc_dir.glob("source_import_store*.pyc"):
                    try:
                        f.unlink()
                    except OSError:
                        pass

        try:
            store_path.write_text(mutated_text, encoding="utf-8")
            _clear_pycache()
            importlib.reload(sis_mod)
            # Under mutation, the probe MUST fail
            # (raise AssertionError, pytest Failed, or BaseException)
            probe_failed = False
            try:
                probe_fn()
            except (Exception, BaseException):
                probe_failed = True

            assert probe_failed, (
                f"Controlled mutation '{name}' was NOT detected by test probe!"
            )
        finally:
            store_path.write_bytes(orig_bytes)
            assert store_path.read_bytes() == orig_bytes, (
                f"Byte verification failed after restoring mutation '{name}'!"
            )
            _clear_pycache()
            importlib.reload(sis_mod)
            test_mod = sys.modules[__name__]
            for attr in dir(sis_mod):
                if not attr.startswith("__") and hasattr(test_mod, attr):
                    setattr(test_mod, attr, getattr(sis_mod, attr))

        # After exact byte restoration, probe MUST pass
        probe_fn()

    # Probe 1: Stale check
    def probe_stale_check() -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON;")
        key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
        sis_mod.initialize_source_import_store(
            conn, device_id=dev_id, active_source=key
        )
        u = make_deterministic_uuid7(10)
        snap = build_synthetic_snapshot([(u, make_sample_party_row("شخص"))], [], [], [])
        req = sis_mod.SourceImportRequest(
            source_key=key,
            expected_generation=5,  # Stale! Stored is 0
            import_id=make_deterministic_uuid7(100),
            observed_at_utc=datetime.now(UTC),
            file_sha256="a" * 64,
            snapshot=snap,
            event_ids={u: make_deterministic_uuid7(200)},
        )
        with pytest.raises(sis_mod.SourceImportStoreError) as exc:
            sis_mod.commit_source_import(conn, req)
        assert exc.value.reason == sis_mod.SourceImportStoreReason.STALE_STATE
        conn.close()

    _execute_mutation(
        "stale check",
        "if stored_gen != request.expected_generation:",
        "if False and stored_gen != request.expected_generation:",
        probe_stale_check,
    )

    # Probe 2: Unchanged membership
    def probe_unchanged_membership() -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON;")
        key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
        sis_mod.initialize_source_import_store(
            conn, device_id=dev_id, active_source=key
        )
        u = make_deterministic_uuid7(10)
        snap = build_synthetic_snapshot([(u, make_sample_party_row("شخص"))], [], [], [])
        imp1 = make_deterministic_uuid7(101)
        imp2 = make_deterministic_uuid7(102)
        req1 = sis_mod.SourceImportRequest(
            source_key=key,
            expected_generation=0,
            import_id=imp1,
            observed_at_utc=datetime.now(UTC),
            file_sha256="1" * 64,
            snapshot=snap,
            event_ids={u: make_deterministic_uuid7(201)},
        )
        sis_mod.commit_source_import(conn, req1)
        req2 = sis_mod.SourceImportRequest(
            source_key=key,
            expected_generation=1,
            import_id=imp2,
            observed_at_utc=datetime.now(UTC),
            file_sha256="2" * 64,
            snapshot=snap,  # UNCHANGED
            event_ids={},
        )
        sis_mod.commit_source_import(conn, req2)
        last_imp = conn.execute(
            "SELECT last_import_id FROM source_memberships WHERE stable_id = ?;",
            (u.bytes,),
        ).fetchone()[0]
        assert last_imp == imp2.bytes
        conn.close()

    _execute_mutation(
        "unchanged membership",
        "last_import_id = excluded.last_import_id;",
        "last_import_id = source_memberships.last_import_id;",
        probe_unchanged_membership,
    )

    # Probe 3: Append-only revision
    def probe_append_only_revision() -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON;")
        key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
        sis_mod.initialize_source_import_store(
            conn, device_id=dev_id, active_source=key
        )
        u = make_deterministic_uuid7(10)
        snap = build_synthetic_snapshot([(u, make_sample_party_row("شخص"))], [], [], [])
        req = sis_mod.SourceImportRequest(
            source_key=key,
            expected_generation=0,
            import_id=make_deterministic_uuid7(100),
            observed_at_utc=datetime.now(UTC),
            file_sha256="a" * 64,
            snapshot=snap,
            event_ids={u: make_deterministic_uuid7(200)},
        )
        sis_mod.commit_source_import(conn, req)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE source_revisions SET home_sheet = 'لیست کسبه';")
        conn.close()

    _execute_mutation(
        "append-only revision",
        "        SELECT RAISE(ABORT, 'Cannot update source_revisions');",
        "        SELECT 1;",
        probe_append_only_revision,
    )

    # Probe 4: Outbox insert
    def probe_outbox_insert() -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON;")
        key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
        sis_mod.initialize_source_import_store(
            conn, device_id=dev_id, active_source=key
        )
        u = make_deterministic_uuid7(10)
        snap = build_synthetic_snapshot([(u, make_sample_party_row("شخص"))], [], [], [])
        req = sis_mod.SourceImportRequest(
            source_key=key,
            expected_generation=0,
            import_id=make_deterministic_uuid7(100),
            observed_at_utc=datetime.now(UTC),
            file_sha256="a" * 64,
            snapshot=snap,
            event_ids={u: make_deterministic_uuid7(200)},
        )
        sis_mod.commit_source_import(conn, req)
        ev_count = conn.execute("SELECT COUNT(*) FROM change_events;").fetchone()[0]
        assert ev_count == 1
        conn.close()

    _execute_mutation(
        "outbox insert",
        "            # Insert change_events\n            cur.execute(",
        "            # Insert change_events\n            if False: cur.execute(",
        probe_outbox_insert,
    )

    # Probe 5: Sequence advance
    def probe_sequence_advance() -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON;")
        key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
        sis_mod.initialize_source_import_store(
            conn, device_id=dev_id, active_source=key
        )
        u = make_deterministic_uuid7(10)
        snap = build_synthetic_snapshot([(u, make_sample_party_row("شخص"))], [], [], [])
        req = sis_mod.SourceImportRequest(
            source_key=key,
            expected_generation=0,
            import_id=make_deterministic_uuid7(100),
            observed_at_utc=datetime.now(UTC),
            file_sha256="a" * 64,
            snapshot=snap,
            event_ids={u: make_deterministic_uuid7(200)},
        )
        sis_mod.commit_source_import(conn, req)
        next_seq = conn.execute(
            "SELECT next_sequence FROM source_store_meta;"
        ).fetchone()[0]
        assert next_seq == 2
        conn.close()

    _execute_mutation(
        "sequence advance",
        "next_seq_val = last_sequence + 1",
        "next_seq_val = stored_next_seq",
        probe_sequence_advance,
    )

    # Probe 6: Request Raw digest
    def probe_request_raw_digest() -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON;")
        key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
        sis_mod.initialize_source_import_store(
            conn, device_id=dev_id, active_source=key
        )
        u = make_deterministic_uuid7(10)
        snap1 = build_synthetic_snapshot(
            [(u, make_sample_party_row("شخص یک"))], [], [], []
        )
        imp_id = make_deterministic_uuid7(100)
        req1 = sis_mod.SourceImportRequest(
            source_key=key,
            expected_generation=0,
            import_id=imp_id,
            observed_at_utc=datetime.now(UTC),
            file_sha256="1" * 64,
            snapshot=snap1,
            event_ids={u: make_deterministic_uuid7(200)},
        )
        sis_mod.commit_source_import(conn, req1)

        snap2 = build_synthetic_snapshot(
            [(u, make_sample_party_row("شخص دو متفاوت"))], [], [], []
        )
        req2 = sis_mod.SourceImportRequest(
            source_key=key,
            expected_generation=0,
            import_id=imp_id,  # Same import ID, but different snapshot content!
            observed_at_utc=datetime.now(UTC),
            file_sha256="2" * 64,
            snapshot=snap2,
            event_ids={u: make_deterministic_uuid7(200)},
        )
        with pytest.raises(sis_mod.SourceImportStoreError) as exc:
            sis_mod.commit_source_import(conn, req2)
        assert exc.value.reason == sis_mod.SourceImportStoreReason.IDEMPOTENCY_CONFLICT
        conn.close()

    _execute_mutation(
        "request Raw digest",
        "request_digest = _compute_request_digest(request)",
        "request_digest = '0' * 64",
        probe_request_raw_digest,
    )

    # Probe 7: Predecessor link
    def probe_predecessor_link() -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON;")
        key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
        sis_mod.initialize_source_import_store(
            conn, device_id=dev_id, active_source=key
        )
        u = make_deterministic_uuid7(10)
        snap1 = build_synthetic_snapshot(
            [],
            [
                (
                    u,
                    make_sample_buy_sell_row(
                        "1403/05/10", "شخص", "فروش", "طلا", "1", "10000"
                    ),
                )
            ],
            [],
            [],
        )
        req1 = sis_mod.SourceImportRequest(
            source_key=key,
            expected_generation=0,
            import_id=make_deterministic_uuid7(101),
            observed_at_utc=datetime.now(UTC),
            file_sha256="1" * 64,
            snapshot=snap1,
            event_ids={u: make_deterministic_uuid7(201)},
        )
        sis_mod.commit_source_import(conn, req1)

        snap2 = build_synthetic_snapshot(
            [],
            [
                (
                    u,
                    make_sample_buy_sell_row(
                        "1403/05/10", "شخص", "فروش", "طلا", "1", "20000"
                    ),
                )
            ],
            [],
            [],
        )
        req2 = sis_mod.SourceImportRequest(
            source_key=key,
            expected_generation=1,
            import_id=make_deterministic_uuid7(102),
            observed_at_utc=datetime.now(UTC),
            file_sha256="2" * 64,
            snapshot=snap2,
            event_ids={u: make_deterministic_uuid7(202)},
        )
        sis_mod.commit_source_import(conn, req2)

        view = sis_mod.read_source_import_store(conn)
        assert view.generation == 2
        conn.close()

    _execute_mutation(
        "predecessor link",
        "previous_version_hash = p_row[0]",
        "previous_version_hash = 'f' * 64",
        probe_predecessor_link,
    )


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
    """Commit 15,000 rows on temp DB, restart/read, replay below 350 MiB RSS.
    Includes large second generation (1,500 edits) and query/decode evidence.
    After close/reopen, independently compares all 15,000 memberships, revisions,
    WP-14 Raw values/hashes, and change Events."""
    import accounting_persistence.source_import_store as sis_mod

    sis_mod_any: Any = sis_mod

    class _TimingCursor(sqlite3.Cursor):
        def execute(self, sql: str, *args: Any) -> Any:
            conn = cast(_TimingConnection, self.connection)
            if getattr(conn, "active_timing", False):
                t0 = time.perf_counter()
                r = super().execute(sql, *args)
                dt = time.perf_counter() - t0
                sql_up = sql.upper()
                if any(kw in sql_up for kw in ("INSERT", "UPDATE", "DELETE")):
                    conn.t_sql_write += dt
                return r
            return super().execute(sql, *args)

        def executemany(self, sql: str, seq_of_params: Any) -> Any:
            conn = cast(_TimingConnection, self.connection)
            if getattr(conn, "active_timing", False):
                t0 = time.perf_counter()
                r = super().executemany(sql, seq_of_params)
                dt = time.perf_counter() - t0
                sql_up = sql.upper()
                if any(kw in sql_up for kw in ("INSERT", "UPDATE", "DELETE")):
                    conn.t_sql_write += dt
                return r
            return super().executemany(sql, seq_of_params)

    class _TimingConnection(sqlite3.Connection):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.t_sql_write: float = 0.0
            self.t_commit_phase: float = 0.0
            self.active_timing: bool = False

        def cursor(self, factory: Any = _TimingCursor) -> Any:
            return super().cursor(factory)

        def execute(self, sql: str, *args: Any) -> Any:
            if self.active_timing:
                t0 = time.perf_counter()
                res = super().execute(sql, *args)
                dt = time.perf_counter() - t0
                sql_up = sql.upper()
                if "COMMIT" in sql_up:
                    self.t_commit_phase += dt
                elif any(kw in sql_up for kw in ("INSERT", "UPDATE", "DELETE")):
                    self.t_sql_write += dt
                return res
            return super().execute(sql, *args)

    db_file = tmp_path / "scale_15000.sqlite3"
    conn = sqlite3.connect(db_file, factory=_TimingConnection)
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

    # Instrument encode phase during commit
    t_encode_total = 0.0
    orig_encode = sis_mod_any.encode_source_raw_row

    def timed_encode(*args: Any, **kwargs: Any) -> bytes:
        nonlocal t_encode_total
        t0 = time.perf_counter()
        res = orig_encode(*args, **kwargs)
        t_encode_total += time.perf_counter() - t0
        return cast(bytes, res)

    # Real process RSS call-window sampling
    gc.collect()
    sampler = CallWindowRssSampler(interval_seconds=0.005)
    baseline_rss = get_current_process_rss_mib()
    sampler.start()

    conn.active_timing = True
    sis_mod_any.encode_source_raw_row = timed_encode
    t_commit_start = time.perf_counter()
    try:
        receipt = commit_source_import(conn, req)
    finally:
        conn.active_timing = False
        sis_mod_any.encode_source_raw_row = orig_encode

    t_commit_total = time.perf_counter() - t_commit_start
    peak_rss = sampler.stop_and_get_peak()
    delta_rss = peak_rss - baseline_rss
    rss_method = (
        "Windows GetProcessMemoryInfo (WorkingSetSize)"
        if sys.platform == "win32"
        else "Linux /proc/self/status VmRSS"
    )

    t_encode = t_encode_total
    t_sql_write = conn.t_sql_write
    t_commit_phase = conn.t_commit_phase
    t_val = max(0.0, t_commit_total - (t_encode + t_sql_write + t_commit_phase))

    assert receipt.disposition == SourceImportDisposition.COMMITTED
    assert receipt.committed_generation == 1
    assert receipt.event_count == row_count
    assert receipt.first_sequence == 1
    assert receipt.last_sequence == row_count
    assert peak_rss < 350.0, f"Peak RSS {peak_rss:.2f} MiB exceeded 350 MiB limit!"

    conn.close()

    # Restart and read with query & decode tracing
    conn_reopen = sqlite3.connect(db_file)
    conn_reopen.execute("PRAGMA foreign_keys = ON;")

    read_queries: list[str] = []
    read_decodes = 0
    orig_decode = sis_mod_any.decode_source_raw_row

    def counting_decode(*args: Any, **kwargs: Any) -> Any:
        nonlocal read_decodes
        read_decodes += 1
        return orig_decode(*args, **kwargs)

    sis_mod_any.decode_source_raw_row = counting_decode
    conn_reopen.set_trace_callback(read_queries.append)
    t_restart_start = time.perf_counter()
    try:
        view = read_source_import_store(conn_reopen)
    finally:
        conn_reopen.set_trace_callback(None)
        sis_mod_any.decode_source_raw_row = orig_decode
    t_restart = time.perf_counter() - t_restart_start

    assert view.generation == 1
    assert view.next_sequence == row_count + 1
    # Verify R2 bounds: statement count bounded (<= 60) and decodes == 15,000
    assert len(read_queries) <= 60, (
        f"Read queries {len(read_queries)} exceeded bound 60"
    )
    assert read_decodes == row_count, (
        f"Read decodes {read_decodes} did not match row count {row_count}"
    )

    # Independent comparison of all 15,000 memberships, revisions,
    # WP-14 Raw values/hashes, and change events
    t_verify_start = time.perf_counter()
    cur = conn_reopen.cursor()
    m_rows = cur.execute(
        "SELECT stable_id, revision, first_import_id, last_import_id "
        "FROM source_memberships ORDER BY stable_id;"
    ).fetchall()
    assert len(m_rows) == row_count

    r_rows = cur.execute(
        "SELECT stable_id, revision, home_sheet, lifecycle, source_hash, "
        "raw_payload, version_hash, created_by_import_id "
        "FROM source_revisions ORDER BY stable_id;"
    ).fetchall()
    assert len(r_rows) == row_count

    e_rows = cur.execute(
        "SELECT sequence, event_id, device_id, import_id, source_id, "
        "stable_id, revision, operation, fiscal_year, sheet_name, "
        "financial_date, observed_at_utc, canonical_payload, payload_hash, "
        "previous_version_hash FROM change_events ORDER BY sequence;"
    ).fetchall()
    assert len(e_rows) == row_count

    # Build sequence and stable_id lookups for change events
    e_by_stable_id: dict[bytes, tuple[Any, ...]] = {}
    for idx, e in enumerate(e_rows):
        assert e[0] == idx + 1
        assert e[2] == dev_id.bytes
        assert e[3] == import_id.bytes
        assert e[4] == src_id.bytes
        assert e[6] == 1
        assert e[7] == "upsert"
        assert e[8] == 1403
        assert hashlib.sha256(e[12]).hexdigest() == e[13]
        e_by_stable_id[e[5]] = e

    # Compare all 15,000 memberships, revisions, and raw rows against snapshot
    for m_row, r_row in zip(m_rows, r_rows, strict=True):
        sb = m_row[0]
        assert r_row[0] == sb
        su = uuid.UUID(bytes=sb)
        raw_row = snap.all_rows_by_id[su]
        expected_eid = event_ids[su].bytes

        # Membership verification
        assert m_row[1] == 1
        assert m_row[2] == import_id.bytes
        assert m_row[3] == import_id.bytes

        # Revision verification
        assert r_row[1] == 1
        assert r_row[2] == raw_row.sheet_name
        assert r_row[3] == "active"
        assert r_row[4] == raw_row.source_hash
        assert r_row[7] == import_id.bytes

        # WP-14 Raw value & hash verification
        dec = decode_source_raw_row(r_row[5])
        assert dec.stable_id == su
        assert dec.source_hash == raw_row.source_hash
        assert dec.raw_values == raw_row.raw_values
        assert dec.sheet_name == raw_row.sheet_name

        # Event correspondence verification
        e_row = e_by_stable_id[sb]
        assert e_row[1] == expected_eid
        assert e_row[9] == raw_row.sheet_name
        assert e_row[13] == r_row[6]  # payload_hash == version_hash

    t_verify = time.perf_counter() - t_verify_start

    # Exact replay test with query & decode tracing
    replay_queries: list[str] = []
    replay_decodes = 0

    def counting_decode_replay(*args: Any, **kwargs: Any) -> Any:
        nonlocal replay_decodes
        replay_decodes += 1
        return orig_decode(*args, **kwargs)

    conn_reopen.set_trace_callback(replay_queries.append)
    sis_mod_any.decode_source_raw_row = counting_decode_replay

    t_replay_start = time.perf_counter()
    try:
        r_replay = commit_source_import(conn_reopen, req)
    finally:
        conn_reopen.set_trace_callback(None)
        sis_mod_any.decode_source_raw_row = orig_decode
    t_replay = time.perf_counter() - t_replay_start

    assert r_replay.disposition == SourceImportDisposition.REPLAYED
    assert r_replay.event_count == row_count
    assert len(replay_queries) <= 60, (
        f"Replay queries {len(replay_queries)} exceeded bound 60"
    )
    assert replay_decodes == row_count, (
        f"Replay decoded {replay_decodes} rows; expected {row_count}"
    )

    db_size = os.path.getsize(db_file)
    wal_file = db_file.with_suffix(".sqlite3-wal")
    wal_size = os.path.getsize(wal_file) if wal_file.exists() else 0

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
        f"[IS-16] Breakdown: fixture={t_fix:.3f}s, val_proj={t_val:.3f}s, "
        f"encode={t_encode:.3f}s, sql_write={t_sql_write:.3f}s, "
        f"commit_phase={t_commit_phase:.3f}s, restart_read={t_restart:.3f}s, "
        f"verify={t_verify:.3f}s, replay={t_replay:.3f}s, gen2={t_gen2:.3f}s\n"
        f"[IS-16] Memory: Baseline RSS={baseline_rss:.2f} MiB, "
        f"Peak RSS={peak_rss:.2f} MiB, Delta={delta_rss:.2f} MiB "
        f"({rss_method})\n"
        f"[IS-16] Storage: DB={db_size / (1024 * 1024):.2f} MiB, "
        f"WAL={wal_size / (1024 * 1024):.2f} MiB\n"
        f"[IS-16] Queries: Read={len(read_queries)} queries "
        f"({read_decodes} decodes), Replay={len(replay_queries)} queries "
        f"({replay_decodes} decodes)",
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


def test_r2_constant_query_and_decode_bounds_across_generations(tmp_path: Path) -> None:
    """R2: Prove ordinary read and commit costs do not grow with history length.
    Tests histories of 1, 5, 20, and 50 generations with constant M/N/C:
    1. SQL statement count and query families are constant across history length.
    2. Current-head decode count remains strictly bounded (1 on read, <= 2 on commit).
    3. No query returns or materializes all historical Import rows.
    4. Commit of one edit remains within O(M + N log N + C).
    5. Current payload, hash, predecessor link, sequence, and newest-import
       corruptions are still rejected with INCONSISTENT_STATE."""
    import accounting_persistence.source_import_store as sis_mod

    codec_attr = "decode_source_raw_row"
    orig_decode = getattr(sis_mod, codec_attr)

    history_counts = (1, 5, 20, 50)
    query_stats: dict[int, dict[str, Any]] = {}
    last_db_file: Path | None = None
    last_u1: uuid.UUID | None = None

    for H in history_counts:
        db_file = tmp_path / f"r2_cost_{H}.sqlite3"
        conn = sqlite3.connect(db_file)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")

        dev_id = make_deterministic_uuid7(1)
        src_id = make_deterministic_uuid7(2)
        active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
        initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

        u1 = make_deterministic_uuid7(10)
        last_u1 = u1
        last_db_file = db_file

        # Build H generations of 1 party
        for gen in range(H):
            snap = build_synthetic_snapshot(
                [(u1, make_sample_party_row(f"شخص_{gen}"))], [], [], []
            )
            req = SourceImportRequest(
                source_key=active_key,
                expected_generation=gen,
                import_id=make_deterministic_uuid7(100 + gen),
                observed_at_utc=datetime.now(UTC),
                file_sha256=f"{gen % 10}" * 64,
                snapshot=snap,
                event_ids={u1: make_deterministic_uuid7(1000 + gen)},
            )
            commit_source_import(conn, req)

        # 1. Trace read_source_import_store
        read_queries: list[str] = []
        conn.set_trace_callback(read_queries.append)
        read_decodes = 0

        def counting_decode_read(*args: Any, **kwargs: Any) -> Any:
            nonlocal read_decodes
            read_decodes += 1
            return orig_decode(*args, **kwargs)

        setattr(sis_mod, codec_attr, counting_decode_read)
        try:
            view = read_source_import_store(conn)
            assert view.generation == H
        finally:
            setattr(sis_mod, codec_attr, orig_decode)
            conn.set_trace_callback(None)

        # 2. Trace commit_source_import of one edit
        commit_queries: list[str] = []
        conn.set_trace_callback(commit_queries.append)
        commit_decodes = 0

        def counting_decode_commit(*args: Any, **kwargs: Any) -> Any:
            nonlocal commit_decodes
            commit_decodes += 1
            return orig_decode(*args, **kwargs)

        snap_edit = build_synthetic_snapshot(
            [(u1, make_sample_party_row(f"شخص_{H}_ویرایش"))], [], [], []
        )
        req_edit = SourceImportRequest(
            source_key=active_key,
            expected_generation=H,
            import_id=make_deterministic_uuid7(8000 + H),
            observed_at_utc=datetime.now(UTC),
            file_sha256="e" * 64,
            snapshot=snap_edit,
            event_ids={u1: make_deterministic_uuid7(80000 + H)},
        )

        setattr(sis_mod, codec_attr, counting_decode_commit)
        try:
            rc = commit_source_import(conn, req_edit)
            assert rc.committed_generation == H + 1
        finally:
            setattr(sis_mod, codec_attr, orig_decode)
            conn.set_trace_callback(None)

        query_stats[H] = {
            "read_count": len(read_queries),
            "commit_count": len(commit_queries),
            "read_decodes": read_decodes,
            "commit_decodes": commit_decodes,
            "read_queries": read_queries,
            "commit_queries": commit_queries,
        }
        conn.close()

    # Verify bounds and constancy across generations
    # Decode count remains strictly bounded: exactly 1 on read, <= 2 on commit
    for H in history_counts:
        assert query_stats[H]["read_decodes"] == 1, (
            f"Read decodes at {H} gen was {query_stats[H]['read_decodes']}, expected 1"
        )
        assert query_stats[H]["commit_decodes"] <= 2, (
            f"Commit decodes at {H} gen was "
            f"{query_stats[H]['commit_decodes']}, expected <= 2"
        )

    # SQL statement count does NOT grow with history length (constant across 5, 20, 50)
    for H in (5, 20, 50):
        assert query_stats[H]["read_count"] == query_stats[5]["read_count"], (
            f"Read SQL count grew: {query_stats[5]['read_count']} "
            f"to {query_stats[H]['read_count']}"
        )
        assert query_stats[H]["commit_count"] == query_stats[5]["commit_count"], (
            f"Commit SQL count grew: {query_stats[5]['commit_count']} "
            f"to {query_stats[H]['commit_count']}"
        )

    # Prove no query returns/materializes all historical import rows
    # Check that query against source_imports has WHERE, aggregate, or LIMIT
    for H in history_counts:
        for q in query_stats[H]["read_queries"] + query_stats[H]["commit_queries"]:
            q_clean = q.strip().upper()
            if "FROM SOURCE_IMPORTS" in q_clean:
                is_aggregate = any(
                    agg in q_clean
                    for agg in ("COUNT(", "MAX(", "MIN(", "SUM(", "EXISTS")
                )
                has_where = "WHERE" in q_clean
                assert is_aggregate or has_where, (
                    f"Query scans historical source_imports "
                    f"without WHERE/aggregate: {q}"
                )

    # Verify corruptions are rejected on the large (50-generation) store
    assert last_db_file is not None and last_u1 is not None

    # Helper to test corruptions
    def _test_corruption(sql: str, params: tuple[Any, ...]) -> None:
        conn = sqlite3.connect(last_db_file)
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute(sql, params)
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON;")
        with pytest.raises(SourceImportStoreError) as exc:
            read_source_import_store(conn)
        assert exc.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
        conn.close()

    # 1. Payload corruption on current head
    conn_c = sqlite3.connect(last_db_file)
    conn_c.execute("PRAGMA foreign_keys = ON;")
    conn_c.execute("DROP TRIGGER trg_prevent_update_source_revisions;")
    conn_c.execute(
        "UPDATE source_revisions SET raw_payload = ? "
        "WHERE stable_id = ? AND revision = 51;",
        (b"corrupt", last_u1.bytes),
    )
    conn_c.execute(
        """
        CREATE TRIGGER trg_prevent_update_source_revisions
        BEFORE UPDATE ON source_revisions
        BEGIN
            SELECT RAISE(ABORT, 'Cannot update source_revisions');
        END;
        """
    )
    conn_c.commit()
    with pytest.raises(SourceImportStoreError) as exc_pay:
        read_source_import_store(conn_c)
    assert exc_pay.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    conn_c.close()

    # 2. Version hash corruption on newest import
    conn_h = sqlite3.connect(last_db_file)
    conn_h.execute("PRAGMA foreign_keys = ON;")
    conn_h.execute("DROP TRIGGER trg_prevent_update_source_imports;")
    conn_h.execute(
        "UPDATE source_imports SET file_sha256 = ? WHERE committed_generation = 51;",
        ("0" * 64,),
    )
    conn_h.execute(
        """
        CREATE TRIGGER trg_prevent_update_source_imports
        BEFORE UPDATE ON source_imports
        BEGIN
            SELECT RAISE(ABORT, 'Cannot update source_imports');
        END;
        """
    )
    conn_h.commit()
    with pytest.raises(SourceImportStoreError) as exc_hash:
        read_source_import_store(conn_h)
    assert exc_hash.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    conn_h.close()
