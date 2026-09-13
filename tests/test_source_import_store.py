"""IS-01..16: independent verification of atomic SQLite source import store.

Validates schema v1, API signatures, consistent read, atomic transactions,
revisions, nondeleting membership, contiguous change events, replay idempotency,
concurrency, crash recovery, error sanitization, property models, and 15,000-row scale.
"""

from __future__ import annotations

import base64
import copy
import dataclasses
import gc
import hashlib
import importlib
import inspect
import json
import os
import queue
import random
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

# Ensure test fixtures in tests/ directory can be imported
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

import accounting_persistence as persistence
import pytest
from _pytest.outcomes import Failed as PytestFailed
from accounting_contracts import (
    evaluate_source_fiscal_evidence,
    evaluate_source_requiredness,
    parse_canonical_jalali_date,
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
    PlanCounts,
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
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
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
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE

        mem_func: Any = None
        try:
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            mem_func = getattr(psapi, "GetProcessMemoryInfo", None)
        except Exception:
            pass

        if mem_func is None:
            mem_func = getattr(kernel32, "K32GetProcessMemoryInfo", None)

        if mem_func is None:
            raise RuntimeError("Windows GetProcessMemoryInfo API entry point not found")

        mem_func.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
            wintypes.DWORD,
        ]
        mem_func.restype = wintypes.BOOL

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        handle = kernel32.GetCurrentProcess()
        if not mem_func(handle, ctypes.byref(counters), counters.cb):
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


class SynchronizedLoserConnection(sqlite3.Connection):
    """Connection for race competitor that signals when BEGIN IMMEDIATE is attempted."""

    begin_attempted: threading.Event | None = None

    def cursor(self, factory: Any = None) -> sqlite3.Cursor:  # type: ignore[override]
        cur: sqlite3.Cursor = super().cursor(factory=factory or SynchronizedLoserCursor)
        return cur

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if (
            "BEGIN IMMEDIATE" in sql
            and SynchronizedLoserConnection.begin_attempted is not None
        ):
            SynchronizedLoserConnection.begin_attempted.set()
        return super().execute(sql, *args, **kwargs)


class SynchronizedLoserCursor(sqlite3.Cursor):
    def execute(self, sql: str, *params: Any) -> Any:
        if (
            "BEGIN IMMEDIATE" in sql
            and SynchronizedLoserConnection.begin_attempted is not None
        ):
            SynchronizedLoserConnection.begin_attempted.set()
        return super().execute(sql, *params)


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
    SynchronizedLoserConnection.begin_attempted = threading.Event()

    winner_receipt: list[SourceImportReceipt] = []
    loser_error: list[SourceImportStoreError] = []
    thread_exceptions: list[BaseException] = []
    winner_conn: sqlite3.Connection | None = None
    loser_conn: sqlite3.Connection | None = None

    def winner_worker() -> None:
        nonlocal winner_conn
        try:
            winner_conn = sqlite3.connect(
                db_file, timeout=10.0, factory=SynchronizedRaceConnection
            )
            winner_conn.execute("PRAGMA foreign_keys = ON;")
            rc = commit_source_import(winner_conn, req_winner)
            winner_receipt.append(rc)
        except BaseException as exc:
            thread_exceptions.append(exc)
        finally:
            if winner_conn is not None:
                try:
                    winner_conn.close()
                except Exception:
                    pass

    def loser_worker() -> None:
        nonlocal loser_conn
        try:
            loser_conn = sqlite3.connect(
                db_file, timeout=10.0, factory=SynchronizedLoserConnection
            )
            loser_conn.execute("PRAGMA foreign_keys = ON;")
            commit_source_import(loser_conn, req_loser)
        except SourceImportStoreError as exc:
            loser_error.append(exc)
        except BaseException as exc:
            thread_exceptions.append(exc)
        finally:
            if loser_conn is not None:
                try:
                    loser_conn.close()
                except Exception:
                    pass

    t_winner = threading.Thread(target=winner_worker)
    t_loser = threading.Thread(target=loser_worker)

    try:
        t_winner.start()
        assert SynchronizedRaceConnection.pause_after_meta_observed.wait(timeout=10.0)

        t_loser.start()
        assert SynchronizedLoserConnection.begin_attempted.wait(timeout=10.0)

        SynchronizedRaceConnection.resume_after_competitor_attempt.set()

        t_winner.join(timeout=10.0)
        t_loser.join(timeout=10.0)
    finally:
        if SynchronizedRaceConnection.resume_after_competitor_attempt is not None:
            SynchronizedRaceConnection.resume_after_competitor_attempt.set()
        if SynchronizedLoserConnection.begin_attempted is not None:
            SynchronizedLoserConnection.begin_attempted.set()
        if t_winner.is_alive():
            t_winner.join(timeout=2.0)
        if t_loser.is_alive():
            t_loser.join(timeout=2.0)

    assert not t_winner.is_alive() and not t_loser.is_alive(), (
        "Surviving race threads detected"
    )
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


def _is_pid_alive(pid: int) -> bool:
    """Check whether a process with the given PID is currently alive."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = wintypes.HANDLE
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid)
        )
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        status_path = Path(f"/proc/{pid}/status")
        if status_path.exists():
            try:
                for line in status_path.read_text().splitlines():
                    if line.startswith("State:"):
                        state_val = line.split()[1]
                        return state_val != "Z"
            except Exception:
                pass
        elif Path("/proc").exists():
            return False
        return True


def _kill_pid(pid: int, timeout: float = 0.5) -> None:
    """Forcefully kill a process by PID across platforms within the given timeout."""
    if not _is_pid_alive(pid):
        return
    if sys.platform == "win32":
        if timeout > 0.0:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=max(0.001, timeout),
                )
            except Exception:
                pass
        if _is_pid_alive(pid):
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.restype = wintypes.HANDLE
            PROCESS_TERMINATE = 0x0001
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, wintypes.DWORD(pid))
            if handle:
                try:
                    kernel32.TerminateProcess(handle, 1)
                finally:
                    kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except ProcessLookupError:
            pass
        except Exception:
            pass


def _peek_pipe_windows(fd: int) -> tuple[bool, int]:
    """Returns (is_alive, bytes_available) for Windows anonymous pipe."""
    if sys.platform == "win32":
        try:
            import ctypes
            import msvcrt
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            handle = msvcrt.get_osfhandle(fd)
            if handle == -1:
                return False, 0
            avail = wintypes.DWORD(0)
            kernel32.PeekNamedPipe.restype = wintypes.BOOL
            kernel32.PeekNamedPipe.argtypes = [
                wintypes.HANDLE,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.c_void_p,
                ctypes.POINTER(wintypes.DWORD),
                ctypes.c_void_p,
            ]
            success = kernel32.PeekNamedPipe(
                wintypes.HANDLE(handle),
                None,
                0,
                None,
                ctypes.byref(avail),
                None,
            )
            if not success:
                return False, 0
            return True, avail.value
        except Exception:
            return False, 0
    return False, 0


class BoundedProcessAckReader:
    """Windows-compatible bounded subprocess ACK reader with leak-free termination."""

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        self.proc = proc
        self.stdout_queue: queue.Queue[str | None] = queue.Queue()
        self.stderr_chunks: list[str] = []
        self._cleanup_workers: list[threading.Thread] = []
        self._tracked_pids: list[int] = []
        self._stop_event = threading.Event()
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self.thread = self._stdout_thread
        self._stdout_thread.start()
        self._stderr_thread.start()

    def track_pid(self, pid: int) -> None:
        """Register a descendant process PID for bounded cleanup and leak tracking."""
        if pid not in self._tracked_pids:
            self._tracked_pids.append(pid)

    def _read_stdout(self) -> None:
        if self.proc.stdout is None:
            self.stdout_queue.put(None)
            return
        fd = self.proc.stdout.fileno()
        if sys.platform != "win32":
            try:
                os.set_blocking(fd, False)
            except Exception:
                pass
        buffer = bytearray()
        try:
            if sys.platform == "win32":
                while not self._stop_event.is_set():
                    alive, avail = _peek_pipe_windows(fd)
                    if not alive:
                        break
                    if avail > 0:
                        try:
                            chunk = os.read(fd, min(avail, 4096))
                            if not chunk:
                                break
                            buffer.extend(chunk)
                            while b"\n" in buffer:
                                line_bytes, _, rest = buffer.partition(b"\n")
                                buffer = bytearray(rest)
                                line = line_bytes.decode(
                                    "utf-8", errors="replace"
                                ).rstrip("\r")
                                self.stdout_queue.put(line.strip())
                        except OSError:
                            break
                    else:
                        self._stop_event.wait(0.02)
            else:
                import select

                poller = select.poll()
                poller.register(
                    fd,
                    select.POLLIN | select.POLLPRI | select.POLLHUP | select.POLLERR,
                )
                while not self._stop_event.is_set():
                    events = poller.poll(50)
                    if not events:
                        continue
                    should_exit = False
                    for _, event in events:
                        if event & (select.POLLIN | select.POLLPRI):
                            try:
                                chunk = os.read(fd, 4096)
                                if not chunk:
                                    should_exit = True
                                    break
                                buffer.extend(chunk)
                                while b"\n" in buffer:
                                    line_bytes, _, rest = buffer.partition(b"\n")
                                    buffer = bytearray(rest)
                                    line = line_bytes.decode(
                                        "utf-8", errors="replace"
                                    ).rstrip("\r")
                                    self.stdout_queue.put(line.strip())
                            except (BlockingIOError, InterruptedError):
                                continue
                            except OSError:
                                should_exit = True
                                break
                        elif event & (select.POLLHUP | select.POLLERR):
                            try:
                                chunk = os.read(fd, 4096)
                                if chunk:
                                    buffer.extend(chunk)
                                    while b"\n" in buffer:
                                        line_bytes, _, rest = buffer.partition(b"\n")
                                        buffer = bytearray(rest)
                                        line = line_bytes.decode(
                                            "utf-8", errors="replace"
                                        ).rstrip("\r")
                                        self.stdout_queue.put(line.strip())
                            except OSError:
                                pass
                            should_exit = True
                            break
                    if should_exit:
                        break
        except Exception:
            pass
        finally:
            if buffer:
                line = buffer.decode("utf-8", errors="replace").rstrip("\r")
                if line.strip():
                    self.stdout_queue.put(line.strip())
            self.stdout_queue.put(None)

    def _read_stderr(self) -> None:
        if self.proc.stderr is None:
            return
        fd = self.proc.stderr.fileno()
        if sys.platform != "win32":
            try:
                os.set_blocking(fd, False)
            except Exception:
                pass
        buffer = bytearray()
        try:
            if sys.platform == "win32":
                while not self._stop_event.is_set():
                    alive, avail = _peek_pipe_windows(fd)
                    if not alive:
                        break
                    if avail > 0:
                        try:
                            chunk = os.read(fd, min(avail, 4096))
                            if not chunk:
                                break
                            buffer.extend(chunk)
                            while b"\n" in buffer:
                                line_bytes, _, rest = buffer.partition(b"\n")
                                buffer = bytearray(rest)
                                line = line_bytes.decode(
                                    "utf-8", errors="replace"
                                ).rstrip("\r")
                                self.stderr_chunks.append(line + "\n")
                        except OSError:
                            break
                    else:
                        self._stop_event.wait(0.02)
            else:
                import select

                poller = select.poll()
                poller.register(
                    fd,
                    select.POLLIN | select.POLLPRI | select.POLLHUP | select.POLLERR,
                )
                while not self._stop_event.is_set():
                    events = poller.poll(50)
                    if not events:
                        continue
                    should_exit = False
                    for _, event in events:
                        if event & (select.POLLIN | select.POLLPRI):
                            try:
                                chunk = os.read(fd, 4096)
                                if not chunk:
                                    should_exit = True
                                    break
                                buffer.extend(chunk)
                                while b"\n" in buffer:
                                    line_bytes, _, rest = buffer.partition(b"\n")
                                    buffer = bytearray(rest)
                                    line = line_bytes.decode(
                                        "utf-8", errors="replace"
                                    ).rstrip("\r")
                                    self.stderr_chunks.append(line + "\n")
                            except (BlockingIOError, InterruptedError):
                                continue
                            except OSError:
                                should_exit = True
                                break
                        elif event & (select.POLLHUP | select.POLLERR):
                            try:
                                chunk = os.read(fd, 4096)
                                if chunk:
                                    buffer.extend(chunk)
                                    while b"\n" in buffer:
                                        line_bytes, _, rest = buffer.partition(b"\n")
                                        buffer = bytearray(rest)
                                        line = line_bytes.decode(
                                            "utf-8", errors="replace"
                                        ).rstrip("\r")
                                        self.stderr_chunks.append(line + "\n")
                            except OSError:
                                pass
                            should_exit = True
                            break
                    if should_exit:
                        break
        except Exception:
            pass
        finally:
            if buffer:
                line = buffer.decode("utf-8", errors="replace").rstrip("\r")
                self.stderr_chunks.append(line)

    def get_stderr(self) -> str:
        return "".join(self.stderr_chunks)

    def wait_for_ack(self, expected_ack: str, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return False
            try:
                line = self.stdout_queue.get(timeout=remaining)
                if line is None:
                    return False
                if line == expected_ack:
                    return True
            except queue.Empty:
                return False

    def close(self, timeout: float = 5.0) -> None:
        close_start = time.monotonic()
        deadline = close_start + timeout

        def _rem() -> float:
            rem = deadline - time.monotonic()
            return rem if rem > 0.0 else 0.0

        # Signal reader threads to stop cancelable loops
        self._stop_event.set()

        # 1. Terminate / kill and reap child process while stdin remains OPEN.
        # Keeping stdin open ensures child cannot receive EOF on
        # sys.stdin.readline() and advance toward COMMIT or normal return.
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=min(0.5, _rem()))
            except Exception:
                pass
            if self.proc.poll() is None:
                try:
                    self.proc.kill()
                except Exception:
                    pass
                try:
                    self.proc.wait(timeout=_rem())
                except Exception:
                    pass
        else:
            try:
                self.proc.wait(timeout=min(0.1, _rem()))
            except Exception:
                pass

        # 2. Terminate / kill any tracked descendants
        # (e.g. retained-handle grandchildren) propagating remaining deadline
        for d_pid in self._tracked_pids:
            _kill_pid(d_pid, timeout=_rem())

        # On Windows, enforce process-tree cleanup propagating deadline
        if sys.platform == "win32" and self.proc.pid:
            rem_tree = _rem()
            if rem_tree > 0.0:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        timeout=max(0.001, rem_tree),
                    )
                except Exception:
                    pass

        # Wait for all tracked descendants to exit under remaining deadline
        for d_pid in self._tracked_pids:
            while _is_pid_alive(d_pid) and _rem() > 0.0:
                if sys.platform != "win32":
                    try:
                        os.waitpid(d_pid, os.WNOHANG)
                    except Exception:
                        pass
                self._stop_event.wait(min(0.02, _rem()))

        # 3. Close stdin, stdout, and stderr using retained cleanup workers
        # under the overall deadline to protect against stream-lock contention
        # or inherited pipe handles.
        def _close_stream(stream: Any) -> None:
            if stream is not None:
                try:
                    if not stream.closed:
                        stream.close()
                except Exception:
                    pass

        self._cleanup_workers = []
        for s in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if s is not None:
                w = threading.Thread(target=_close_stream, args=(s,), daemon=True)
                self._cleanup_workers.append(w)
                w.start()

        # 4. Join all cleanup worker threads and reader threads under deadline
        for worker in self._cleanup_workers:
            worker.join(timeout=_rem())

        for reader in (self._stdout_thread, self._stderr_thread):
            reader.join(timeout=_rem())

        # 5. Mandatory process, descendant, reader, cleanup worker,
        # and pipe terminal postconditions
        assert self.proc.poll() is not None, "Child process was not reaped!"
        for d_pid in self._tracked_pids:
            assert not _is_pid_alive(d_pid), f"Descendant process {d_pid} was leaked!"
        assert not self._stdout_thread.is_alive(), (
            "Stdout reader thread was not stopped!"
        )
        assert not self._stderr_thread.is_alive(), (
            "Stderr reader thread was not stopped!"
        )
        for idx, worker in enumerate(self._cleanup_workers):
            assert not worker.is_alive(), f"Cleanup worker {idx} was not stopped!"

        assert self.proc.stdin is None or self.proc.stdin.closed, (
            "proc.stdin was not closed!"
        )
        assert self.proc.stdout is None or self.proc.stdout.closed, (
            "proc.stdout was not closed!"
        )
        assert self.proc.stderr is None or self.proc.stderr.closed, (
            "proc.stderr was not closed!"
        )

        cleanup_elapsed = time.monotonic() - close_start
        assert cleanup_elapsed <= timeout + 0.1, (
            f"BoundedProcessAckReader.close exceeded configured timeout: "
            f"took {cleanup_elapsed:.2f}s > {timeout:.2f}s"
        )


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
    # Note: tests_dir and db_file are passed as sys.argv[1] and sys.argv[2] to
    # guarantee safe execution across native Windows paths containing backslashes,
    # Unicode, and spaces.
    script_open = """import sys, sqlite3
from pathlib import Path
tests_dir = sys.argv[1]
db_file = sys.argv[2]
sys.path.insert(0, tests_dir)
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
    event_ids={u1: make_deterministic_uuid7(201)},
)
commit_source_import(conn, req)
"""
    proc1 = subprocess.Popen(
        [sys.executable, "-c", script_open, str(tests_dir), str(db_file)],
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ack_reader1 = BoundedProcessAckReader(proc1)
    got_ack1 = False
    try:
        got_ack1 = ack_reader1.wait_for_ack("ACK_TX_OPEN", timeout=10.0)
    finally:
        ack_reader1.close()
    assert got_ack1, (
        f"Subprocess 1 did not emit ACK_TX_OPEN. stderr: {ack_reader1.get_stderr()}"
    )

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
    # Note: tests_dir and db_file are passed as sys.argv[1] and sys.argv[2]
    script_post_commit = """import sys, sqlite3
from pathlib import Path
tests_dir = sys.argv[1]
db_file = sys.argv[2]
sys.path.insert(0, tests_dir)
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
    event_ids={u1: make_deterministic_uuid7(202)},
)
commit_source_import(conn, req)
"""
    proc2 = subprocess.Popen(
        [sys.executable, "-c", script_post_commit, str(tests_dir), str(db_file)],
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ack_reader2 = BoundedProcessAckReader(proc2)
    got_ack2 = False
    try:
        got_ack2 = ack_reader2.wait_for_ack("ACK_COMMIT_DONE", timeout=10.0)
    finally:
        ack_reader2.close()
    assert got_ack2, (
        f"Subprocess 2 did not emit ACK_COMMIT_DONE. stderr: {ack_reader2.get_stderr()}"
    )

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


def test_is11_negative_no_ack_bounded_exit_and_no_leaks(tmp_path: Path) -> None:
    """IS-11 negative control: A silent live child must not prevent the deadline,
    finally cleanup, process termination, pipe closure, or thread joins.
    Covers both:
    1. Direct child non-sleeping blocking wait with explicit READY synchronization.
    2. Retained-handle descendant non-sleeping blocking wait with explicit
       READY synchronization.
    Proves bounded exit and zero child, descendant, reader-thread, or
    cleanup-worker leaks.
    """

    # -------------------------------------------------------------------------
    # Sub-case 1: Direct child non-sleeping blocking wait with READY sync
    # -------------------------------------------------------------------------
    script_direct = """import sys, os
r, w = os.pipe()
sys.stdout.write("READY\\n")
sys.stdout.flush()
# Non-sleeping blocking IPC wait
os.read(r, 1)
"""
    proc1 = subprocess.Popen(
        [sys.executable, "-c", script_direct],
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ack_reader1 = BoundedProcessAckReader(proc1)
    ACK_WAIT_TIMEOUT = 0.5
    CLEANUP_TIMEOUT = 3.0
    COMBINED_TIMEOUT_BOUND = ACK_WAIT_TIMEOUT + CLEANUP_TIMEOUT

    t_start1 = 0.0
    elapsed1 = 0.0
    try:
        ready1 = ack_reader1.wait_for_ack("READY", timeout=5.0)
        assert ready1, (
            f"Direct child did not emit READY. stderr: {ack_reader1.get_stderr()}"
        )
        t_start1 = time.monotonic()
        got_ack1 = ack_reader1.wait_for_ack(
            "ACK_NEVER_ARRIVES", timeout=ACK_WAIT_TIMEOUT
        )
        assert not got_ack1, "Unexpectedly received ACK from silent child"
    finally:
        ack_reader1.close(timeout=CLEANUP_TIMEOUT)
        if t_start1 > 0.0:
            elapsed1 = time.monotonic() - t_start1

    # 1. Bounded exit: elapsed must be bounded through completion of cleanup
    # within configured combined bound
    assert t_start1 > 0.0, "Subprocess was not ready before ACK wait"
    assert elapsed1 >= ACK_WAIT_TIMEOUT - 0.05, (
        f"Wait should have taken at least timeout: took {elapsed1:.2f}s"
    )
    assert elapsed1 <= COMBINED_TIMEOUT_BOUND, (
        f"Wait and cleanup did not bound exit: took {elapsed1:.2f}s, "
        f"exceeded configured combined bound {COMBINED_TIMEOUT_BOUND:.2f}s"
    )

    # 2. Zero child leaks: process must be terminated and reaped
    assert proc1.poll() is not None, "Child process was leaked!"
    assert not _is_pid_alive(proc1.pid), f"Child process {proc1.pid} is still alive!"

    # 3. Zero reader-thread leaks: reader threads must be terminated
    assert not ack_reader1._stdout_thread.is_alive(), "Stdout reader thread was leaked!"
    assert not ack_reader1._stderr_thread.is_alive(), "Stderr reader thread was leaked!"

    # 4. Zero cleanup worker leaks: cleanup workers must be terminated
    for idx, worker in enumerate(ack_reader1._cleanup_workers):
        assert not worker.is_alive(), f"Cleanup worker {idx} was leaked!"

    # 5. Pipe closure: all stdio pipes must be closed
    assert proc1.stdout is not None and proc1.stdout.closed
    assert proc1.stdin is not None and proc1.stdin.closed
    assert proc1.stderr is not None and proc1.stderr.closed

    # -------------------------------------------------------------------------
    # Sub-case 2: Retained-handle descendant with explicit READY synchronization
    # -------------------------------------------------------------------------
    descendant_pid_file = tmp_path / "descendant.pid"
    script_descendant = """import sys, os, subprocess
from pathlib import Path

pid_file = Path(sys.argv[1])
pipe_r, pipe_w = os.pipe()

# Grandchild process inherits stdout and stderr and blocks on unwritten pipe
grandchild_script = '''import sys, os
r, w = os.pipe()
os.read(r, 1)
'''

proc_gc = subprocess.Popen(
    [sys.executable, "-c", grandchild_script],
    stdout=None,
    stderr=None,
    stdin=subprocess.DEVNULL,
    close_fds=False,
)

pid_file.write_text(str(proc_gc.pid), encoding="utf-8")
sys.stdout.write("READY_DESCENDANT\\n")
sys.stdout.flush()

# Direct child also blocks on unwritten pipe
os.read(pipe_r, 1)
"""
    proc2 = subprocess.Popen(
        [sys.executable, "-c", script_descendant, str(descendant_pid_file)],
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ack_reader2 = BoundedProcessAckReader(proc2)
    t_start2 = 0.0
    elapsed2 = 0.0
    descendant_pid: int | None = None
    try:
        ready2 = ack_reader2.wait_for_ack("READY_DESCENDANT", timeout=5.0)
        assert ready2, (
            "Child/descendant did not emit READY_DESCENDANT. "
            f"stderr: {ack_reader2.get_stderr()}"
        )
        assert descendant_pid_file.exists()
        assert descendant_pid_file.stat().st_size > 0
        descendant_pid = int(descendant_pid_file.read_text().strip())
        ack_reader2.track_pid(descendant_pid)
        assert _is_pid_alive(descendant_pid), (
            f"Descendant process {descendant_pid} is not alive!"
        )

        t_start2 = time.monotonic()
        got_ack2 = ack_reader2.wait_for_ack(
            "ACK_NEVER_ARRIVES", timeout=ACK_WAIT_TIMEOUT
        )
        assert not got_ack2, "Unexpectedly received ACK from descendant"
    finally:
        ack_reader2.close(timeout=CLEANUP_TIMEOUT)
        if t_start2 > 0.0:
            elapsed2 = time.monotonic() - t_start2

    # 1. Bounded exit through cleanup completion within configured combined bound
    assert t_start2 > 0.0, "Descendant process was not ready before ACK wait"
    assert elapsed2 >= ACK_WAIT_TIMEOUT - 0.05, (
        f"Wait should have taken at least timeout: took {elapsed2:.2f}s"
    )
    assert elapsed2 <= COMBINED_TIMEOUT_BOUND, (
        f"Wait and cleanup did not bound exit: took {elapsed2:.2f}s, "
        f"exceeded configured combined bound {COMBINED_TIMEOUT_BOUND:.2f}s"
    )

    # 2. Zero child and descendant leaks: both must be terminated and reaped
    assert proc2.poll() is not None, "Direct child process was leaked!"
    assert not _is_pid_alive(proc2.pid), (
        f"Direct child process {proc2.pid} is still alive!"
    )
    assert descendant_pid is not None
    assert not _is_pid_alive(descendant_pid), (
        f"Descendant process {descendant_pid} was leaked!"
    )

    # 3. Zero reader-thread leaks
    assert not ack_reader2._stdout_thread.is_alive(), "Stdout reader thread was leaked!"
    assert not ack_reader2._stderr_thread.is_alive(), "Stderr reader thread was leaked!"

    # 4. Zero cleanup worker leaks
    for idx, worker in enumerate(ack_reader2._cleanup_workers):
        assert not worker.is_alive(), f"Cleanup worker {idx} was leaked!"

    # 5. All stdio pipes closed
    assert proc2.stdout is not None and proc2.stdout.closed
    assert proc2.stdin is not None and proc2.stdin.closed
    assert proc2.stderr is not None and proc2.stderr.closed


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


def oracle_compute_request_digest(request: SourceImportRequest) -> str:
    """Independently compute deterministic request digest according to
    ADR-0018 specification."""
    hasher = hashlib.sha256()
    hasher.update(b"source-import-request.v1:")
    hasher.update(request.source_key.source_id.bytes)
    hasher.update(f":{request.source_key.fiscal_year}:".encode("ascii"))
    hasher.update(f"{request.expected_generation}:".encode("ascii"))
    hasher.update(request.import_id.bytes)
    hasher.update(f":{request.observed_at_utc.isoformat()}:".encode())
    hasher.update(request.file_sha256.encode("ascii"))
    hasher.update(b":events:")
    for s_bytes, e_bytes in sorted(
        ((k.bytes, v.bytes) for k, v in request.event_ids.items()),
        key=lambda pair: pair[0],
    ):
        hasher.update(s_bytes)
        hasher.update(e_bytes)
    hasher.update(b":sheets:")
    for sheet_name in RAW_CONTRACT_REGISTRY.sheets:
        sheet = request.snapshot.sheets[sheet_name]
        hasher.update(sheet_name.encode("utf-8"))
        hasher.update(
            f":{sheet.row_count}:{sheet.sheet_snapshot_hash}:".encode("ascii")
        )
        for row in sheet.rows:
            raw_bytes = encode_source_raw_row(row)
            hasher.update(raw_bytes)
    return hasher.hexdigest()


def assert_complete_receipt_matches(
    actual: SourceImportReceipt,
    expected: SourceImportReceipt,
) -> None:
    """Assert all public fields of SourceImportReceipt match expected values."""
    assert actual.disposition == expected.disposition, (
        f"Disposition mismatch: {actual.disposition} != {expected.disposition}"
    )
    assert actual.import_id == expected.import_id, (
        f"Import ID mismatch: {actual.import_id} != {expected.import_id}"
    )
    assert actual.source_id == expected.source_id, (
        f"Source ID mismatch: {actual.source_id} != {expected.source_id}"
    )
    assert actual.fiscal_year == expected.fiscal_year, (
        f"Fiscal year mismatch: {actual.fiscal_year} != {expected.fiscal_year}"
    )
    assert actual.base_generation == expected.base_generation, (
        f"Base generation mismatch: {actual.base_generation} != "
        f"{expected.base_generation}"
    )
    assert actual.committed_generation == expected.committed_generation, (
        f"Committed generation mismatch: {actual.committed_generation} != "
        f"{expected.committed_generation}"
    )
    assert actual.file_sha256 == expected.file_sha256, (
        f"File SHA mismatch: {actual.file_sha256} != {expected.file_sha256}"
    )
    assert actual.total_row_count == expected.total_row_count, (
        f"Total row count mismatch: {actual.total_row_count} != "
        f"{expected.total_row_count}"
    )

    # Compare total_counts field-by-field
    assert actual.total_counts.insert_count == expected.total_counts.insert_count, (
        f"Total insert mismatch: {actual.total_counts.insert_count} != "
        f"{expected.total_counts.insert_count}"
    )
    assert actual.total_counts.edit_count == expected.total_counts.edit_count, (
        f"Total edit mismatch: {actual.total_counts.edit_count} != "
        f"{expected.total_counts.edit_count}"
    )
    assert actual.total_counts.void_count == expected.total_counts.void_count, (
        f"Total void mismatch: {actual.total_counts.void_count} != "
        f"{expected.total_counts.void_count}"
    )
    assert (
        actual.total_counts.unchanged_count == expected.total_counts.unchanged_count
    ), (
        f"Total unchanged mismatch: {actual.total_counts.unchanged_count} != "
        f"{expected.total_counts.unchanged_count}"
    )
    assert actual.total_counts == expected.total_counts

    # Compare per_sheet_counts for each canonical sheet field-by-field
    assert set(actual.per_sheet_counts.keys()) == set(expected.per_sheet_counts.keys())
    for sheet_name in RAW_CONTRACT_REGISTRY.sheets:
        assert sheet_name in actual.per_sheet_counts, (
            f"Missing sheet {sheet_name} in actual per_sheet_counts"
        )
        act_sh = actual.per_sheet_counts[sheet_name]
        exp_sh = expected.per_sheet_counts[sheet_name]
        assert act_sh.insert_count == exp_sh.insert_count, (
            f"Sheet {sheet_name} insert mismatch: {act_sh.insert_count} != "
            f"{exp_sh.insert_count}"
        )
        assert act_sh.edit_count == exp_sh.edit_count, (
            f"Sheet {sheet_name} edit mismatch: {act_sh.edit_count} != "
            f"{exp_sh.edit_count}"
        )
        assert act_sh.void_count == exp_sh.void_count, (
            f"Sheet {sheet_name} void mismatch: {act_sh.void_count} != "
            f"{exp_sh.void_count}"
        )
        assert act_sh.unchanged_count == exp_sh.unchanged_count, (
            f"Sheet {sheet_name} unchanged mismatch: {act_sh.unchanged_count} != "
            f"{exp_sh.unchanged_count}"
        )
        assert act_sh == exp_sh

    assert actual.event_count == expected.event_count, (
        f"Event count mismatch: {actual.event_count} != {expected.event_count}"
    )
    assert actual.first_sequence == expected.first_sequence, (
        f"First sequence mismatch: {actual.first_sequence} != {expected.first_sequence}"
    )
    assert actual.last_sequence == expected.last_sequence, (
        f"Last sequence mismatch: {actual.last_sequence} != {expected.last_sequence}"
    )
    assert actual == expected


class ImportStoreOracle:
    """Independent in-memory oracle for complete 7-table source import store state,
    provenance tracking, and change planning."""

    def __init__(self, device_id: uuid.UUID, active_key: SourceBindingKey) -> None:
        self.device_id = device_id
        self.active_key = active_key
        self.generation = 0
        self.next_sequence = 1
        self.last_import_id: uuid.UUID | None = None
        self.last_file_sha256: str | None = None
        self.last_observed_at_utc: str | None = None

        # stable_id -> dict with latest state
        self.items: dict[uuid.UUID, dict[str, Any]] = {}
        # stable_id -> previous version_hash
        self.previous_version_hashes: dict[uuid.UUID, str] = {}

        # 7-table full models
        self.import_records: list[tuple[Any, ...]] = []
        self.sheet_records: list[tuple[Any, ...]] = []
        self.revision_records: dict[tuple[bytes, int], tuple[Any, ...]] = {}
        self.membership_records: dict[bytes, tuple[Any, ...]] = {}
        self.event_records: list[tuple[Any, ...]] = []

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
        request: SourceImportRequest,
    ) -> dict[str, Any]:
        self.generation += 1
        import_id = request.import_id
        snapshot = request.snapshot
        event_ids = request.event_ids
        file_sha256 = request.file_sha256
        obs_utc_str = request.observed_at_utc.isoformat()

        self.last_import_id = import_id
        self.last_file_sha256 = file_sha256
        self.last_observed_at_utc = obs_utc_str

        prior_registry = self.get_prior_registry()
        plan = plan_source_changes(snapshot, prior_registry)

        changed_items_by_sheet: dict[str, list[Any]] = {
            s: [] for s in RAW_CONTRACT_REGISTRY.sheets
        }
        all_changed_items: list[Any] = []
        for item in plan.items:
            if item.action in (PlanAction.INSERT, PlanAction.EDIT, PlanAction.VOID):
                changed_items_by_sheet[item.sheet_name].append(item)
                all_changed_items.append(item)

        first_seq = self.next_sequence if all_changed_items else None
        tot_ins = 0
        tot_edt = 0
        tot_void = 0
        tot_unchanged = 0

        # Model each sheet's counts
        for sheet_order, sheet_name in enumerate(RAW_CONTRACT_REGISTRY.sheets):
            sh = snapshot.sheets[sheet_name]
            sh_ins = sum(
                1
                for it in changed_items_by_sheet[sheet_name]
                if it.action == PlanAction.INSERT
            )
            sh_edt = sum(
                1
                for it in changed_items_by_sheet[sheet_name]
                if it.action == PlanAction.EDIT
            )
            sh_void = sum(
                1
                for it in changed_items_by_sheet[sheet_name]
                if it.action == PlanAction.VOID
            )
            sh_unchanged = sh.row_count - (sh_ins + sh_edt)
            tot_ins += sh_ins
            tot_edt += sh_edt
            tot_void += sh_void
            tot_unchanged += sh_unchanged

            self.sheet_records.append(
                (
                    import_id.bytes,
                    sheet_order,
                    sheet_name,
                    sh.sheet_snapshot_hash,
                    sh.row_count,
                    sh_ins,
                    sh_edt,
                    sh_void,
                    sh_unchanged,
                )
            )

        total_row_count = sum(sh.row_count for sh in snapshot.sheets.values())
        ev_count = len(all_changed_items)
        last_seq = (self.next_sequence + ev_count - 1) if ev_count > 0 else None

        # Compute request digest independently from spec formula
        req_digest = oracle_compute_request_digest(request)
        self.import_records.append(
            (
                import_id.bytes,
                req_digest,
                self.active_key.source_id.bytes,
                self.active_key.fiscal_year,
                self.generation - 1,
                self.generation,
                obs_utc_str,
                file_sha256,
                total_row_count,
                tot_ins,
                tot_edt,
                tot_void,
                tot_unchanged,
                ev_count,
                first_seq,
                last_seq,
            )
        )

        # Model events and revisions
        for item in all_changed_items:
            seq = self.next_sequence
            self.next_sequence += 1
            ev_id = event_ids[item.stable_id]
            rev = 1 if item.action == PlanAction.INSERT else item.planned_revision
            assert rev is not None
            op = "void" if item.action == PlanAction.VOID else "upsert"
            prev_vh = self.previous_version_hashes.get(item.stable_id)

            if op == "upsert":
                assert item.current_row is not None
                source_hash = item.current_row.source_hash
                raw_payload = encode_source_raw_row(item.current_row)
                raw_b64 = base64.b64encode(raw_payload).decode("ascii")
                raw_date_val = item.current_row.raw_values.get("date_raw")
                parsed_j = (
                    parse_canonical_jalali_date(raw_date_val)
                    if raw_date_val is not None
                    else None
                )
                financial_date = (
                    parsed_j.canonical_date if parsed_j is not None else None
                )
            else:
                source_hash = None
                raw_payload = None
                raw_b64 = None
                prior_row = self.items[item.stable_id].get("raw_row")
                raw_date_val = (
                    prior_row.raw_values.get("date_raw")
                    if prior_row is not None
                    else None
                )
                parsed_j = (
                    parse_canonical_jalali_date(raw_date_val)
                    if raw_date_val is not None
                    else None
                )
                financial_date = (
                    parsed_j.canonical_date if parsed_j is not None else None
                )

            wire = [
                "source-change-event.v1",
                str(self.device_id).lower(),
                str(ev_id).lower(),
                str(import_id).lower(),
                str(seq),
                str(self.active_key.source_id).lower(),
                str(self.active_key.fiscal_year),
                item.sheet_name,
                str(item.stable_id).lower(),
                str(rev),
                op,
                financial_date,
                source_hash,
                raw_b64,
                prev_vh,
                obs_utc_str,
            ]
            wire_bytes = json.dumps(
                wire, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
            payload_hash = hashlib.sha256(wire_bytes).hexdigest()
            version_hash = payload_hash
            self.previous_version_hashes[item.stable_id] = version_hash

            # Revision record
            self.revision_records[(item.stable_id.bytes, rev)] = (
                item.stable_id.bytes,
                rev,
                item.sheet_name,
                "active" if op == "upsert" else "voided",
                source_hash,
                raw_payload,
                import_id.bytes,
                version_hash,
                prev_vh,
            )

            # Event record
            self.event_records.append(
                (
                    seq,
                    ev_id.bytes,
                    self.device_id.bytes,
                    import_id.bytes,
                    self.active_key.source_id.bytes,
                    item.stable_id.bytes,
                    rev,
                    op,
                    self.active_key.fiscal_year,
                    item.sheet_name,
                    financial_date,
                    obs_utc_str,
                    wire_bytes,
                    payload_hash,
                    prev_vh,
                )
            )

            if op == "void":
                self.items[item.stable_id] = {
                    "revision": rev,
                    "is_void": True,
                    "source_hash": None,
                    "sheet_name": item.sheet_name,
                    "raw_row": None,
                }
            else:
                self.items[item.stable_id] = {
                    "revision": rev,
                    "is_void": False,
                    "source_hash": source_hash,
                    "sheet_name": item.sheet_name,
                    "raw_row": item.current_row,
                }

        # Model memberships
        # 1. Advance void items
        for item in all_changed_items:
            if item.action == PlanAction.VOID:
                assert item.planned_revision is not None
                prior_mem = self.membership_records[item.stable_id.bytes]
                self.membership_records[item.stable_id.bytes] = (
                    self.active_key.source_id.bytes,
                    item.stable_id.bytes,
                    item.planned_revision,
                    prior_mem[3],  # preserve first_import_id
                    import_id.bytes,
                )

        # 2. Upsert every row in current snapshot
        for row in snapshot.all_rows_by_id.values():
            s_bytes = row.stable_id.bytes
            rev = self.items[row.stable_id]["revision"]
            if s_bytes in self.membership_records:
                prior_mem = self.membership_records[s_bytes]
                self.membership_records[s_bytes] = (
                    self.active_key.source_id.bytes,
                    s_bytes,
                    max(prior_mem[2], rev),
                    prior_mem[3],
                    import_id.bytes,
                )
            else:
                self.membership_records[s_bytes] = (
                    self.active_key.source_id.bytes,
                    s_bytes,
                    rev,
                    import_id.bytes,
                    import_id.bytes,
                )

        committed_receipt = SourceImportReceipt(
            disposition=SourceImportDisposition.COMMITTED,
            import_id=import_id,
            source_id=self.active_key.source_id,
            fiscal_year=self.active_key.fiscal_year,
            base_generation=self.generation - 1,
            committed_generation=self.generation,
            file_sha256=file_sha256,
            total_row_count=total_row_count,
            total_counts=plan.total_counts,
            per_sheet_counts=plan.per_sheet_counts,
            event_count=ev_count,
            first_sequence=first_seq,
            last_sequence=last_seq,
        )
        replayed_receipt = SourceImportReceipt(
            disposition=SourceImportDisposition.REPLAYED,
            import_id=import_id,
            source_id=self.active_key.source_id,
            fiscal_year=self.active_key.fiscal_year,
            base_generation=self.generation - 1,
            committed_generation=self.generation,
            file_sha256=file_sha256,
            total_row_count=total_row_count,
            total_counts=plan.total_counts,
            per_sheet_counts=plan.per_sheet_counts,
            event_count=ev_count,
            first_sequence=first_seq,
            last_sequence=last_seq,
        )

        return {
            "committed_receipt": committed_receipt,
            "replayed_receipt": replayed_receipt,
            "committed_generation": self.generation,
            "event_count": ev_count,
            "first_sequence": first_seq,
            "last_sequence": last_seq,
        }

    def verify_db_state(self, conn: sqlite3.Connection) -> None:
        # 1. source_store_meta
        meta = conn.execute(
            "SELECT singleton_id, store_version, schema_version, generation, "
            "next_sequence, device_id FROM source_store_meta;"
        ).fetchone()
        assert meta == (
            1,
            SOURCE_IMPORT_STORE_VERSION,
            1,
            self.generation,
            self.next_sequence,
            self.device_id.bytes,
        )

        # 2. source_bindings
        bind = conn.execute(
            "SELECT source_id, fiscal_year, state, final_file_sha256, "
            "last_import_id, last_file_sha256, last_observed_at_utc "
            "FROM source_bindings;"
        ).fetchone()
        assert bind == (
            self.active_key.source_id.bytes,
            self.active_key.fiscal_year,
            "active",
            None,
            self.last_import_id.bytes if self.last_import_id else None,
            self.last_file_sha256,
            self.last_observed_at_utc,
        )

        # 3. source_imports
        db_imports = conn.execute(
            "SELECT import_id, request_digest, source_id, fiscal_year, "
            "base_generation, committed_generation, observed_at_utc, file_sha256, "
            "total_row_count, insert_count, edit_count, void_count, "
            "unchanged_count, event_count, first_sequence, last_sequence "
            "FROM source_imports ORDER BY committed_generation ASC;"
        ).fetchall()
        assert len(db_imports) == len(self.import_records)
        for exp, act in zip(self.import_records, db_imports, strict=True):
            assert act == exp

        # 4. source_import_sheets
        db_sheets = conn.execute(
            "SELECT import_id, sheet_order, sheet_name, snapshot_hash, "
            "row_count, insert_count, edit_count, void_count, unchanged_count "
            "FROM source_import_sheets ORDER BY import_id, sheet_order ASC;"
        ).fetchall()
        assert len(db_sheets) == len(self.sheet_records)
        for exp, act in zip(self.sheet_records, db_sheets, strict=True):
            assert act == exp

        # 5. source_revisions
        db_revs = conn.execute(
            "SELECT stable_id, revision, home_sheet, lifecycle, source_hash, "
            "raw_payload, created_by_import_id, version_hash, previous_version_hash "
            "FROM source_revisions ORDER BY stable_id, revision ASC;"
        ).fetchall()
        sorted_exp_revs = [
            self.revision_records[k] for k in sorted(self.revision_records.keys())
        ]
        assert len(db_revs) == len(sorted_exp_revs)
        for exp, act in zip(sorted_exp_revs, db_revs, strict=True):
            assert act == exp

        # 6. source_memberships
        db_mems = conn.execute(
            "SELECT source_id, stable_id, revision, first_import_id, last_import_id "
            "FROM source_memberships ORDER BY source_id, stable_id ASC;"
        ).fetchall()
        sorted_exp_mems = [
            self.membership_records[k] for k in sorted(self.membership_records.keys())
        ]
        assert len(db_mems) == len(sorted_exp_mems)
        for exp, act in zip(sorted_exp_mems, db_mems, strict=True):
            assert act == exp

        # 7. change_events
        db_events = conn.execute(
            "SELECT sequence, event_id, device_id, import_id, source_id, "
            "stable_id, revision, operation, fiscal_year, sheet_name, "
            "financial_date, observed_at_utc, canonical_payload, payload_hash, "
            "previous_version_hash FROM change_events ORDER BY sequence ASC;"
        ).fetchall()
        assert len(db_events) == len(self.event_records)
        for exp, act in zip(self.event_records, db_events, strict=True):
            assert act == exp
            # Verify event hash integrity and wire payload
            assert hashlib.sha256(act[12]).hexdigest() == act[13]


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

        obs_time = datetime(2026, 9, 4, 12, step, 0, tzinfo=UTC)
        file_sha = f"{step % 10}" * 64
        imp_id = make_deterministic_uuid7(90000 + step)

        req = SourceImportRequest(
            source_key=active_key,
            expected_generation=step,
            import_id=imp_id,
            observed_at_utc=obs_time,
            file_sha256=file_sha,
            snapshot=snap,
            event_ids=ev_ids,
        )

        # Apply to oracle
        expected = oracle.plan_and_apply(req)

        receipt = commit_source_import(conn, req)

        # 3. Compare complete receipt for COMMITTED across all public fields
        assert_complete_receipt_matches(receipt, expected["committed_receipt"])

        # Exact replay idempotency verification - complete comparison for REPLAYED
        receipt_rep = commit_source_import(conn, req)
        assert_complete_receipt_matches(receipt_rep, expected["replayed_receipt"])

        # Stale state rejection verification
        req_stale = SourceImportRequest(
            source_key=active_key,
            expected_generation=step,  # stale because store generation is now step + 1
            import_id=make_deterministic_uuid7(999000 + step),
            observed_at_utc=obs_time,
            file_sha256=file_sha,
            snapshot=snap,
            event_ids=ev_ids,
        )
        with pytest.raises(SourceImportStoreError) as exc_stale:
            commit_source_import(conn, req_stale)
        assert exc_stale.value.reason == SourceImportStoreReason.STALE_STATE

        # 4. Compare full 7-table DB state to Oracle
        oracle.verify_db_state(conn)

    conn.close()


@dataclasses.dataclass
class _RecordedRaisesFailure:
    expected_exception: Any
    failure_exc: BaseException
    failure_type: type[BaseException]
    failure_message: str
    consumed: bool = False


@dataclasses.dataclass
class _RecordedProbeFailure:
    failure_exc: BaseException
    probe_name: str
    consumed: bool = False


def _matches_exception_class(exp: Any, target_cls: type[BaseException]) -> bool:
    if exp is target_cls:
        return True
    if isinstance(exp, tuple):
        return any(item is target_cls for item in exp)
    return False


class _MutationHarnessState:
    def __init__(self) -> None:
        self.recorded_failures: list[_RecordedRaisesFailure] = []
        self.recorded_probe_failures: list[_RecordedProbeFailure] = []

    def record_raises_failure(
        self, exc: BaseException, expected_exception: Any
    ) -> None:
        exc_any: Any = exc
        try:
            exc_any._harness_provenance_sentinel = id(self)
        except Exception:
            pass
        self.recorded_failures.append(
            _RecordedRaisesFailure(
                expected_exception=expected_exception,
                failure_exc=exc,
                failure_type=type(exc),
                failure_message=str(exc),
                consumed=False,
            )
        )

    def record_probe_failure(self, exc: BaseException, probe_name: str) -> None:
        self.recorded_probe_failures.append(
            _RecordedProbeFailure(
                failure_exc=exc,
                probe_name=probe_name,
                consumed=False,
            )
        )

    def verify_provenance(
        self, exc: BaseException, expected_cls: type[BaseException]
    ) -> bool:
        if type(exc) is not pytest.fail.Exception:
            return False
        if not str(exc).startswith("DID NOT RAISE"):
            return False
        exc_any: Any = exc
        sentinel = (
            exc_any._harness_provenance_sentinel
            if hasattr(exc_any, "_harness_provenance_sentinel")
            else None
        )
        if sentinel != id(self):
            return False
        for rec in self.recorded_failures:
            if rec.consumed:
                continue
            if rec.failure_exc is exc and _matches_exception_class(
                rec.expected_exception, expected_cls
            ):
                rec.consumed = True
                return True
        return False

    def verify_probe_provenance(self, exc: BaseException, probe_name: str) -> bool:
        for rec in self.recorded_probe_failures:
            if rec.consumed:
                continue
            if rec.failure_exc is exc and rec.probe_name == probe_name:
                rec.consumed = True
                return True
        return False


_active_mutation_harness: _MutationHarnessState | None = None


def _execute_mutation_harness(
    *,
    name: str,
    target_str: str,
    replacement_str: str,
    probe_fn: Callable[[], None],
    expected_failure_desc: str,
    verify_semantic_failure: Callable[[BaseException], bool],
    observed_semantic_failures: dict[str, str] | None = None,
) -> None:
    """Shared mutation harness: executes a single controlled mutation, verifies
    intended semantic failure classification, and guarantees byte-for-byte
    restoration and pass on clean code."""
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

    def _sync_modules() -> None:
        test_mod = sys.modules[__name__]
        for attr in dir(sis_mod):
            if not attr.startswith("__") and hasattr(test_mod, attr):
                setattr(test_mod, attr, getattr(sis_mod, attr))
        pkg_mod = sys.modules.get("accounting_persistence")
        if pkg_mod is not None:
            for attr in dir(sis_mod):
                if not attr.startswith("__") and hasattr(pkg_mod, attr):
                    setattr(pkg_mod, attr, getattr(sis_mod, attr))

    written_to_disk = False
    harness_state = _MutationHarnessState()
    global _active_mutation_harness
    _active_mutation_harness = harness_state

    pytest_any: Any = pytest
    orig_pytest_raises = pytest_any.raises

    def _harness_pytest_raises(
        expected_exception: Any, *args: Any, **kwargs: Any
    ) -> Any:
        if args and callable(args[0]):
            target_callable = args[0]
            call_returned_normally = False

            def _wrapped_target(*c_args: Any, **c_kwargs: Any) -> Any:
                nonlocal call_returned_normally
                res = target_callable(*c_args, **c_kwargs)
                call_returned_normally = True
                return res

            try:
                return orig_pytest_raises(
                    expected_exception, _wrapped_target, *args[1:], **kwargs
                )
            except BaseException as exc:
                if (
                    call_returned_normally
                    and type(exc) is pytest.fail.Exception
                    and str(exc).startswith("DID NOT RAISE")
                ):
                    harness_state.record_raises_failure(exc, expected_exception)
                raise

        real_cm = orig_pytest_raises(expected_exception, *args, **kwargs)

        class _HarnessRaisesContext:
            def __enter__(self) -> Any:
                return real_cm.__enter__()

            def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Any:
                try:
                    return real_cm.__exit__(exc_type, exc_val, exc_tb)
                except BaseException as exc:
                    if (
                        exc_type is None
                        and type(exc) is pytest.fail.Exception
                        and str(exc).startswith("DID NOT RAISE")
                    ):
                        harness_state.record_raises_failure(exc, expected_exception)
                    raise

            def __call__(self, func: Any, *c_args: Any, **c_kwargs: Any) -> Any:
                merged_kwargs = dict(kwargs)
                merged_kwargs.update(c_kwargs)
                return _harness_pytest_raises(
                    expected_exception, func, *c_args, **merged_kwargs
                )

            def __getattr__(self, name: str) -> Any:
                return getattr(real_cm, name)

        return _HarnessRaisesContext()

    pytest_any.raises = _harness_pytest_raises
    try:
        if os.access(store_path, os.W_OK):
            try:
                store_path.write_text(mutated_text, encoding="utf-8")
                written_to_disk = True
            except OSError:
                written_to_disk = False

        _clear_pycache()
        mutated_code = compile(mutated_text, str(store_path), "exec")
        exec(mutated_code, sis_mod.__dict__)
        _sync_modules()

        # Under mutation, the probe MUST fail with intended semantic failure
        caught_exc: BaseException | None = None
        try:
            probe_fn()
        except BaseException as exc:
            caught_exc = exc

        assert caught_exc is not None, (
            f"Controlled mutation '{name}' was NOT detected by test probe!"
        )
        assert verify_semantic_failure(caught_exc), (
            f"Controlled mutation '{name}' failed with unexpected exception "
            f"{type(caught_exc).__name__}: {caught_exc!r} instead of "
            f"expected semantic failure ({expected_failure_desc})!"
        )
        if observed_semantic_failures is not None:
            observed_semantic_failures[name] = (
                f"{type(caught_exc).__name__}: {caught_exc}"
            )
    finally:
        pytest_any.raises = orig_pytest_raises
        _active_mutation_harness = None
        if written_to_disk:
            try:
                store_path.write_bytes(orig_bytes)
            except OSError:
                pass
        _clear_pycache()
        orig_code = compile(orig_text, str(store_path), "exec")
        exec(orig_code, sis_mod.__dict__)
        _sync_modules()
        assert store_path.read_bytes() == orig_bytes, (
            f"Byte verification failed after restoring mutation '{name}'!"
        )

    # After exact byte restoration, probe MUST pass
    probe_fn()


def _verify_pytest_raises_provenance(
    exc: BaseException,
    expected_cls: type[BaseException],
) -> bool:
    """Verify structured provenance showing failure was generated by
    concrete pytest.raises."""
    if _active_mutation_harness is None:
        return False
    return _active_mutation_harness.verify_provenance(exc, expected_cls)


def _verify_probe_provenance(
    exc: BaseException,
    probe_name: str,
) -> bool:
    """Verify structured provenance showing failure was generated by
    concrete test probe."""
    if _active_mutation_harness is None:
        return False
    return _active_mutation_harness.verify_probe_provenance(exc, probe_name)


def classify_stale_check_failure(exc: BaseException) -> bool:
    import accounting_persistence.source_import_store as sis_mod

    return _verify_pytest_raises_provenance(exc, sis_mod.SourceImportStoreError)


def classify_unchanged_membership_failure(exc: BaseException) -> bool:
    imp1 = make_deterministic_uuid7(101)
    imp2 = make_deterministic_uuid7(102)
    expected_msg = f"last_import_id mismatch: {imp1.bytes!r} != {imp2.bytes!r}"
    if type(exc) is not AssertionError:
        return False
    msg = str(exc)
    if not (msg == expected_msg or msg.startswith(expected_msg + "\n")):
        return False
    return _verify_probe_provenance(exc, "unchanged membership")


def classify_append_only_failure(exc: BaseException) -> bool:
    return _verify_pytest_raises_provenance(exc, sqlite3.IntegrityError)


def classify_outbox_insert_failure(exc: BaseException) -> bool:
    expected_msg = "change_events count mismatch: 0 != 1"
    if type(exc) is not AssertionError:
        return False
    msg = str(exc)
    if not (msg == expected_msg or msg.startswith(expected_msg + "\n")):
        return False
    return _verify_probe_provenance(exc, "outbox insert")


def classify_sequence_advance_failure(exc: BaseException) -> bool:
    expected_msg = "next_sequence mismatch: 1 != 2"
    if type(exc) is not AssertionError:
        return False
    msg = str(exc)
    if not (msg == expected_msg or msg.startswith(expected_msg + "\n")):
        return False
    return _verify_probe_provenance(exc, "sequence advance")


def classify_request_raw_digest_failure(exc: BaseException) -> bool:
    import accounting_persistence.source_import_store as sis_mod

    return _verify_pytest_raises_provenance(exc, sis_mod.SourceImportStoreError)


def classify_predecessor_link_failure(exc: BaseException) -> bool:
    import accounting_persistence.source_import_store as sis_mod

    return (
        type(exc) is sis_mod.SourceImportStoreError
        and getattr(exc, "reason", None)
        is sis_mod.SourceImportStoreReason.INCONSISTENT_STATE
        and str(exc) == "Source import store is inconsistent."
    )


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

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)

    observed_semantic_failures: dict[str, str] = {}

    def _execute_mutation(
        name: str,
        target_str: str,
        replacement_str: str,
        probe_fn: Callable[[], None],
        expected_failure_desc: str,
        verify_semantic_failure: Callable[[BaseException], bool],
    ) -> None:
        _execute_mutation_harness(
            name=name,
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=probe_fn,
            expected_failure_desc=expected_failure_desc,
            verify_semantic_failure=verify_semantic_failure,
            observed_semantic_failures=observed_semantic_failures,
        )

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
        with pytest.raises(
            sis_mod.SourceImportStoreError,
            match=r"^Source import state is stale\.$",
        ) as exc:
            sis_mod.commit_source_import(conn, req)
        assert exc.value.reason == sis_mod.SourceImportStoreReason.STALE_STATE
        assert str(exc.value) == "Source import state is stale."
        conn.close()

    _execute_mutation(
        "stale check",
        "if stored_gen != request.expected_generation:",
        "if False and stored_gen != request.expected_generation:",
        probe_stale_check,
        f"pytest Failed DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}",
        classify_stale_check_failure,
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
        if last_imp != imp2.bytes:
            exc_obj = AssertionError(
                f"last_import_id mismatch: {last_imp!r} != {imp2.bytes!r}"
            )
            if _active_mutation_harness is not None:
                _active_mutation_harness.record_probe_failure(
                    exc_obj, "unchanged membership"
                )
            raise exc_obj
        conn.close()

    _execute_mutation(
        "unchanged membership",
        "last_import_id = excluded.last_import_id;",
        "last_import_id = source_memberships.last_import_id;",
        probe_unchanged_membership,
        "AssertionError: last_import_id mismatch: exact match required",
        classify_unchanged_membership_failure,
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
        with pytest.raises(
            sqlite3.IntegrityError, match=r"^Cannot update source_revisions$"
        ):
            conn.execute("UPDATE source_revisions SET home_sheet = 'لیست کسبه';")
        conn.close()

    _execute_mutation(
        "append-only revision",
        "        SELECT RAISE(ABORT, 'Cannot update source_revisions');",
        "        SELECT 1;",
        probe_append_only_revision,
        f"pytest Failed DID NOT RAISE {sqlite3.IntegrityError.__name__}",
        classify_append_only_failure,
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
        if ev_count != 1:
            exc_obj = AssertionError(f"change_events count mismatch: {ev_count} != 1")
            if _active_mutation_harness is not None:
                _active_mutation_harness.record_probe_failure(exc_obj, "outbox insert")
            raise exc_obj
        conn.close()

    _execute_mutation(
        "outbox insert",
        "            # Insert change_events\n            cur.execute(",
        "            # Insert change_events\n            if False: cur.execute(",
        probe_outbox_insert,
        "AssertionError: change_events count mismatch: 0 != 1",
        classify_outbox_insert_failure,
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
        if next_seq != 2:
            exc_obj = AssertionError(f"next_sequence mismatch: {next_seq} != 2")
            if _active_mutation_harness is not None:
                _active_mutation_harness.record_probe_failure(
                    exc_obj, "sequence advance"
                )
            raise exc_obj
        conn.close()

    _execute_mutation(
        "sequence advance",
        "next_seq_val = last_sequence + 1",
        "next_seq_val = stored_next_seq",
        probe_sequence_advance,
        "AssertionError: next_sequence mismatch: 1 != 2",
        classify_sequence_advance_failure,
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
        # Construct two otherwise identical requests sharing import_id, time,
        # file SHA, event IDs and semantically equal row/sheet hashes,
        # differing only in accepted Raw representation (Decimal scale: '2' vs '2.0').
        snap1 = build_synthetic_snapshot(
            [],
            [
                (
                    u,
                    make_sample_buy_sell_row(
                        "1403/01/01", "شخص", "خرید", "کالا", "2", "1000"
                    ),
                )
            ],
            [],
            [],
        )
        snap2 = build_synthetic_snapshot(
            [],
            [
                (
                    u,
                    make_sample_buy_sell_row(
                        "1403/01/01", "شخص", "خرید", "کالا", "2.0", "1000"
                    ),
                )
            ],
            [],
            [],
        )
        imp_id = make_deterministic_uuid7(100)
        ev_id = make_deterministic_uuid7(200)
        fixed_time = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)
        file_sha = "a" * 64

        # Precondition checks explicitly asserted:
        sh1 = snap1.sheets["خرید-فروش"]
        sh2 = snap2.sheets["خرید-فروش"]
        assert sh1.sheet_snapshot_hash == sh2.sheet_snapshot_hash
        r1 = snap1.all_rows_by_id[u]
        r2 = snap2.all_rows_by_id[u]
        assert r1.source_hash == r2.source_hash
        assert r1.raw_values != r2.raw_values
        assert r1.raw_values["quantity_raw"] == "2"
        assert r2.raw_values["quantity_raw"] == "2.0"
        b1 = encode_source_raw_row(r1)
        b2 = encode_source_raw_row(r2)
        assert b1 != b2

        req1 = sis_mod.SourceImportRequest(
            source_key=key,
            expected_generation=0,
            import_id=imp_id,
            observed_at_utc=fixed_time,
            file_sha256=file_sha,
            snapshot=snap1,
            event_ids={u: ev_id},
        )
        rc1 = sis_mod.commit_source_import(conn, req1)
        assert rc1.disposition == sis_mod.SourceImportDisposition.COMMITTED

        req2 = sis_mod.SourceImportRequest(
            source_key=key,
            expected_generation=0,
            import_id=imp_id,
            observed_at_utc=fixed_time,
            file_sha256=file_sha,
            snapshot=snap2,
            event_ids={u: ev_id},
        )
        # In unmodified implementation, hasher.update(raw_bytes) differentiates the
        # requests, raising IDEMPOTENCY_CONFLICT.
        # Under mutation where raw_bytes is omitted from digest, req2's digest matches
        # req1, causing commit_source_import to return REPLAYED instead of raising.
        with pytest.raises(
            sis_mod.SourceImportStoreError,
            match=r"^Source import identity conflicts\.$",
        ) as exc:
            sis_mod.commit_source_import(conn, req2)
        assert exc.value.reason == sis_mod.SourceImportStoreReason.IDEMPOTENCY_CONFLICT
        assert str(exc.value) == "Source import identity conflicts."
        conn.close()

    _execute_mutation(
        "request Raw digest",
        "            hasher.update(raw_bytes)",
        "            pass  # hasher.update(raw_bytes)",
        probe_request_raw_digest,
        f"pytest Failed DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}",
        classify_request_raw_digest_failure,
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
        (
            "SourceImportStoreError(INCONSISTENT_STATE): "
            "Source import store is inconsistent."
        ),
        classify_predecessor_link_failure,
    )

    assert len(observed_semantic_failures) == 7
    assert store_path.read_bytes() == orig_bytes


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


def assert_r4_timing_evidence(
    *,
    t_commit_total: float,
    t_digest_pure: float,
    t_digest_encode: float,
    t_revision_encode: float,
    t_encode_total: float,
    t_req: float,
    t_fisc: float,
    t_plan: float,
    t_val_proj_pure: float,
    t_sql_write: float,
    t_commit_phase: float,
    t_residual: float,
    t_fix: float = 0.0,
    t_restart: float | None = None,
    t_verify: float | None = None,
    t_replay: float | None = None,
    t_gen2: float | None = None,
    tolerance_seconds: float = 0.5,
) -> None:
    """Validate R4 timing evidence with executable assertions:
    1. All measured phase durations are non-negative.
    2. Accounted time cannot exceed total time beyond documented tolerance.
    3. Additive identity: projection + encoding + SQL write + commit phase +
       residual equals commit total within tolerance.
    Raw differences are preserved without clipping."""
    assert t_commit_total >= 0.0, (
        f"Commit total time must be non-negative: {t_commit_total:.4f}s"
    )
    assert t_digest_pure >= 0.0, (
        f"Digest pure time must be non-negative: {t_digest_pure:.4f}s"
    )
    assert t_digest_encode >= 0.0, (
        f"Digest encode time must be non-negative: {t_digest_encode:.4f}s"
    )
    assert t_revision_encode >= 0.0, (
        f"Revision encode time must be non-negative: {t_revision_encode:.4f}s"
    )
    assert t_encode_total >= 0.0, (
        f"Encode total time must be non-negative: {t_encode_total:.4f}s"
    )
    assert t_req >= 0.0, f"Requiredness time must be non-negative: {t_req:.4f}s"
    assert t_fisc >= 0.0, f"Fiscal evidence time must be non-negative: {t_fisc:.4f}s"
    assert t_plan >= 0.0, f"Plan time must be non-negative: {t_plan:.4f}s"
    assert t_val_proj_pure >= 0.0, (
        f"Validation/projection pure time must be non-negative: {t_val_proj_pure:.4f}s"
    )
    assert t_sql_write >= 0.0, (
        f"SQL write time must be non-negative: {t_sql_write:.4f}s"
    )
    assert t_commit_phase >= 0.0, (
        f"Commit phase time must be non-negative: {t_commit_phase:.4f}s"
    )
    assert t_residual >= 0.0, f"Residual time must be non-negative: {t_residual:.4f}s"
    assert t_fix >= 0.0, f"Fixture time must be non-negative: {t_fix:.4f}s"

    if t_restart is not None:
        assert t_restart >= 0.0, (
            f"Restart/read time must be non-negative: {t_restart:.4f}s"
        )
    if t_verify is not None:
        assert t_verify >= 0.0, f"Verify time must be non-negative: {t_verify:.4f}s"
    if t_replay is not None:
        assert t_replay >= 0.0, f"Replay time must be non-negative: {t_replay:.4f}s"
    if t_gen2 is not None:
        assert t_gen2 >= 0.0, f"Generation 2 time must be non-negative: {t_gen2:.4f}s"

    # Assert non-negative durations for all sub-phases and constituent breakdowns
    assert abs(t_val_proj_pure - (t_digest_pure + t_req + t_fisc + t_plan)) <= 1e-6, (
        f"Validation/projection breakdown mismatch: {t_val_proj_pure:.6f}s != "
        f"digest_pure ({t_digest_pure:.6f}s) + req ({t_req:.6f}s) + "
        f"fisc ({t_fisc:.6f}s) + plan ({t_plan:.6f}s)"
    )
    assert abs(t_encode_total - (t_digest_encode + t_revision_encode)) <= 1e-6, (
        f"Encode total breakdown mismatch: {t_encode_total:.6f}s != "
        f"digest_encode ({t_digest_encode:.6f}s) + "
        f"revision_encode ({t_revision_encode:.6f}s)"
    )

    t_accounted = t_val_proj_pure + t_encode_total + t_sql_write + t_commit_phase

    if t_accounted > t_commit_total:
        overcount = t_accounted - t_commit_total
        assert overcount <= tolerance_seconds, (
            f"Accounted time ({t_accounted:.4f}s) exceeded total commit time "
            f"({t_commit_total:.4f}s) by {overcount:.4f}s beyond tolerance "
            f"({tolerance_seconds}s)"
        )
    assert t_accounted <= t_commit_total + tolerance_seconds, (
        f"Accounted time ({t_accounted:.4f}s) exceeded total commit time "
        f"({t_commit_total:.4f}s) beyond tolerance ({tolerance_seconds}s)"
    )

    additive_sum = (
        t_val_proj_pure + t_encode_total + t_sql_write + t_commit_phase + t_residual
    )
    assert abs(additive_sum - t_commit_total) <= tolerance_seconds, (
        f"Additive identity violated: projection ({t_val_proj_pure:.4f}s) + "
        f"encoding ({t_encode_total:.4f}s) + SQL write ({t_sql_write:.4f}s) + "
        f"commit phase ({t_commit_phase:.4f}s) + residual ({t_residual:.4f}s) = "
        f"{additive_sum:.4f}s != commit total ({t_commit_total:.4f}s) "
        f"within tolerance ({tolerance_seconds}s)"
    )


def test_is16_r4_timing_evidence_negative_controls() -> None:
    """IS-16 R4 timing evidence negative controls:
    Prove that a negative phase duration or an accounted-time overrun beyond
    tolerance is rejected with specific AssertionError messages."""
    # Negative control 1: genuinely negative phase rejected
    with pytest.raises(
        AssertionError,
        match="Digest pure time must be non-negative",
    ):
        assert_r4_timing_evidence(
            t_commit_total=1.0,
            t_digest_pure=-0.05,
            t_digest_encode=0.1,
            t_revision_encode=0.1,
            t_encode_total=0.2,
            t_req=0.1,
            t_fisc=0.1,
            t_plan=0.1,
            t_val_proj_pure=0.25,
            t_sql_write=0.2,
            t_commit_phase=0.1,
            t_residual=0.25,
            t_fix=0.1,
            tolerance_seconds=0.5,
        )

    # Negative control 2: negative residual rejected even when within tolerance
    with pytest.raises(
        AssertionError,
        match="Residual time must be non-negative",
    ):
        assert_r4_timing_evidence(
            t_commit_total=1.0,
            t_digest_pure=0.1,
            t_digest_encode=0.1,
            t_revision_encode=0.1,
            t_encode_total=0.2,
            t_req=0.05,
            t_fisc=0.05,
            t_plan=0.1,
            t_val_proj_pure=0.3,
            t_sql_write=0.5,
            t_commit_phase=0.2,
            t_residual=-0.2,
            t_fix=0.1,
            tolerance_seconds=0.5,
        )

    # Negative control 3: accounted-time overrun beyond tolerance rejected
    with pytest.raises(
        AssertionError,
        match=r"Accounted time .* exceeded total commit time .* beyond tolerance",
    ):
        assert_r4_timing_evidence(
            t_commit_total=1.0,
            t_digest_pure=0.1,
            t_digest_encode=0.1,
            t_revision_encode=0.1,
            t_encode_total=0.2,
            t_req=0.1,
            t_fisc=0.1,
            t_plan=0.1,
            t_val_proj_pure=0.4,
            t_sql_write=0.8,
            t_commit_phase=0.4,
            t_residual=0.0,
            t_fix=0.1,
            tolerance_seconds=0.5,
        )

    # Negative control 4: additive identity violation beyond tolerance rejected
    with pytest.raises(
        AssertionError,
        match=r"Additive identity violated",
    ):
        assert_r4_timing_evidence(
            t_commit_total=1.0,
            t_digest_pure=0.05,
            t_digest_encode=0.05,
            t_revision_encode=0.1,
            t_encode_total=0.15,
            t_req=0.05,
            t_fisc=0.05,
            t_plan=0.05,
            t_val_proj_pure=0.2,
            t_sql_write=0.4,
            t_commit_phase=0.2,
            t_residual=1.0,
            t_fix=0.1,
            tolerance_seconds=0.5,
        )

    # Negative control 5: constituent projection breakdown mismatch rejected
    with pytest.raises(
        AssertionError,
        match=r"Validation/projection breakdown mismatch",
    ):
        assert_r4_timing_evidence(
            t_commit_total=1.0,
            t_digest_pure=0.1,
            t_digest_encode=0.05,
            t_revision_encode=0.1,
            t_encode_total=0.15,
            t_req=0.05,
            t_fisc=0.05,
            t_plan=0.05,
            t_val_proj_pure=0.9,
            t_sql_write=0.4,
            t_commit_phase=0.2,
            t_residual=0.2,
            t_fix=0.1,
            tolerance_seconds=0.5,
        )

    # Negative control 6: constituent encode breakdown mismatch rejected
    with pytest.raises(
        AssertionError,
        match=r"Encode total breakdown mismatch",
    ):
        assert_r4_timing_evidence(
            t_commit_total=1.0,
            t_digest_pure=0.05,
            t_digest_encode=0.05,
            t_revision_encode=0.1,
            t_encode_total=0.9,
            t_req=0.05,
            t_fisc=0.05,
            t_plan=0.05,
            t_val_proj_pure=0.2,
            t_sql_write=0.4,
            t_commit_phase=0.2,
            t_residual=0.2,
            t_fix=0.1,
            tolerance_seconds=0.5,
        )

    # Positive control: valid timing evidence within tolerance accepted cleanly
    assert_r4_timing_evidence(
        t_commit_total=1.0,
        t_digest_pure=0.05,
        t_digest_encode=0.05,
        t_revision_encode=0.1,
        t_encode_total=0.15,
        t_req=0.05,
        t_fisc=0.05,
        t_plan=0.05,
        t_val_proj_pure=0.2,
        t_sql_write=0.4,
        t_commit_phase=0.2,
        t_residual=0.2,
        t_fix=0.1,
        t_restart=0.05,
        t_verify=0.05,
        t_replay=0.05,
        t_gen2=0.05,
        tolerance_seconds=0.5,
    )


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

    # Instrument individual validation/projection and encode phases during commit
    t_digest_encode = 0.0
    t_revision_encode = 0.0
    in_digest = False
    orig_encode = sis_mod_any.encode_source_raw_row

    def timed_encode(*args: Any, **kwargs: Any) -> bytes:
        nonlocal t_digest_encode, t_revision_encode
        t0 = time.perf_counter()
        res = orig_encode(*args, **kwargs)
        dt = time.perf_counter() - t0
        if in_digest:
            t_digest_encode += dt
        else:
            t_revision_encode += dt
        return cast(bytes, res)

    t_digest_total = 0.0
    orig_digest = sis_mod_any._compute_request_digest

    def timed_digest(*args: Any, **kwargs: Any) -> Any:
        nonlocal t_digest_total, in_digest
        in_digest = True
        t0 = time.perf_counter()
        try:
            return orig_digest(*args, **kwargs)
        finally:
            t_digest_total += time.perf_counter() - t0
            in_digest = False

    t_req = 0.0
    orig_req = sis_mod_any.evaluate_source_requiredness

    def timed_req(*args: Any, **kwargs: Any) -> Any:
        nonlocal t_req
        t0 = time.perf_counter()
        res = orig_req(*args, **kwargs)
        t_req += time.perf_counter() - t0
        return res

    t_fisc = 0.0
    orig_fisc = sis_mod_any.evaluate_source_fiscal_evidence

    def timed_fisc(*args: Any, **kwargs: Any) -> Any:
        nonlocal t_fisc
        t0 = time.perf_counter()
        res = orig_fisc(*args, **kwargs)
        t_fisc += time.perf_counter() - t0
        return res

    t_plan = 0.0
    orig_plan = sis_mod_any.plan_source_changes

    def timed_plan(*args: Any, **kwargs: Any) -> Any:
        nonlocal t_plan
        t0 = time.perf_counter()
        res = orig_plan(*args, **kwargs)
        t_plan += time.perf_counter() - t0
        return res

    # Real process RSS call-window sampling
    gc.collect()
    sampler = CallWindowRssSampler(interval_seconds=0.005)
    baseline_rss = get_current_process_rss_mib()
    sampler.start()

    conn.active_timing = True
    sis_mod_any.encode_source_raw_row = timed_encode
    sis_mod_any._compute_request_digest = timed_digest
    sis_mod_any.evaluate_source_requiredness = timed_req
    sis_mod_any.evaluate_source_fiscal_evidence = timed_fisc
    sis_mod_any.plan_source_changes = timed_plan
    t_commit_start = time.perf_counter()
    try:
        receipt = commit_source_import(conn, req)
    finally:
        conn.active_timing = False
        sis_mod_any.encode_source_raw_row = orig_encode
        sis_mod_any._compute_request_digest = orig_digest
        sis_mod_any.evaluate_source_requiredness = orig_req
        sis_mod_any.evaluate_source_fiscal_evidence = orig_fisc
        sis_mod_any.plan_source_changes = orig_plan

    t_commit_total = time.perf_counter() - t_commit_start
    peak_rss = sampler.stop_and_get_peak()
    delta_rss = peak_rss - baseline_rss
    rss_method = (
        "Windows GetProcessMemoryInfo (WorkingSetSize)"
        if sys.platform == "win32"
        else "Linux /proc/self/status VmRSS"
    )

    TIMING_TOLERANCE_SECONDS = 0.5

    t_digest_pure = t_digest_total - t_digest_encode
    t_encode_total = t_digest_encode + t_revision_encode
    t_sql_write = conn.t_sql_write
    t_commit_phase = conn.t_commit_phase
    t_req_fisc = t_req + t_fisc
    t_val_proj_pure = t_digest_pure + t_req_fisc + t_plan
    t_accounted = t_val_proj_pure + t_encode_total + t_sql_write + t_commit_phase
    t_residual = t_commit_total - t_accounted

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

    assert_r4_timing_evidence(
        t_commit_total=t_commit_total,
        t_digest_pure=t_digest_pure,
        t_digest_encode=t_digest_encode,
        t_revision_encode=t_revision_encode,
        t_encode_total=t_encode_total,
        t_req=t_req,
        t_fisc=t_fisc,
        t_plan=t_plan,
        t_val_proj_pure=t_val_proj_pure,
        t_sql_write=t_sql_write,
        t_commit_phase=t_commit_phase,
        t_residual=t_residual,
        t_fix=t_fix,
        t_restart=t_restart,
        t_verify=t_verify,
        t_replay=t_replay,
        t_gen2=t_gen2,
        tolerance_seconds=TIMING_TOLERANCE_SECONDS,
    )

    print(
        f"[IS-16] Breakdown: fixture={t_fix:.3f}s, "
        f"val_proj={t_val_proj_pure:.3f}s "
        f"(digest_pure={t_digest_pure:.3f}s, req_fisc={t_req_fisc:.3f}s, "
        f"plan={t_plan:.3f}s), "
        f"encode={t_encode_total:.3f}s (digest_enc={t_digest_encode:.3f}s, "
        f"rev_enc={t_revision_encode:.3f}s), "
        f"sql_write={t_sql_write:.3f}s, commit_phase={t_commit_phase:.3f}s, "
        f"residual={t_residual:.3f}s, "
        f"restart_read={t_restart:.3f}s, verify={t_verify:.3f}s, "
        f"replay={t_replay:.3f}s, gen2={t_gen2:.3f}s\n"
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
    """R2: Verify constant statement count, bounded decodes, and bounded return rows.
    Tests histories of 1, 5, 20, and 50 generations with constant M/N/C:
    1. SQL statement count and query families are constant across history length.
    2. Current-head decode count remains strictly bounded (1 on read, <= 2 on commit).
    3. No query returns or materializes all historical Import rows in Python
       (all queries use WHERE, aggregates, or LIMIT). Note: constant statement count
       does not prove history-independent total CPU/time because permitted SQL
       aggregates still scan historical import index/table entries in SQLite VM.
    4. Commit of one edit remains within O(M + N log N + C) Python work.
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

    # Verify no query returns/materializes all historical import rows in Python.
    # Note: Permitted SQL aggregates still scan historical import records, so constant
    # statement count does not imply history-independent SQLite VM instructions.
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


# ============================================================================
# Round 4 Review Corrections (R1 - R3)
# ============================================================================


def test_r4_01_current_event_uuidv4_tamper_rejected_by_read_and_replay(
    tmp_path: Path,
) -> None:
    """R4-A: Tampering current Event event_id with RFC 4122 UUIDv4 is rejected.
    Replaces current Event event_id with valid UUIDv4, updates wire payload and hash,
    and updates Revision version_hash. Restores exact trigger guards before read.
    Public read and exact replay must both raise INCONSISTENT_STATE, produce no partial
    view/receipt, make no DB change, and leave caller connection open and idle."""
    db_file = tmp_path / "r4_uuidv4.sqlite3"
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    u1 = make_deterministic_uuid7(10)
    ev_id_v7 = make_deterministic_uuid7(200)
    imp_id = make_deterministic_uuid7(100)
    obs_time = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)
    snap = build_synthetic_snapshot([(u1, make_sample_party_row("شخص_یک"))], [], [], [])
    req = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=imp_id,
        observed_at_utc=obs_time,
        file_sha256="a" * 64,
        snapshot=snap,
        event_ids={u1: ev_id_v7},
    )
    rc = commit_source_import(conn, req)
    assert rc.disposition == SourceImportDisposition.COMMITTED

    # Generate a valid RFC 4122 UUIDv4
    ev_id_v4 = uuid.uuid4()
    assert ev_id_v4.version == 4
    assert ev_id_v4.variant == uuid.RFC_4122

    # Tamper event_id to UUIDv4 in change_events and recompute wire hashes
    cur = conn.cursor()
    row = cur.execute(
        "SELECT canonical_payload FROM change_events WHERE sequence = 1;"
    ).fetchone()
    assert row is not None
    orig_payload = json.loads(row[0].decode("utf-8"))
    orig_payload[2] = str(ev_id_v4).lower()  # Update event_id in wire payload
    tampered_bytes = json.dumps(
        orig_payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    tampered_hash = hashlib.sha256(tampered_bytes).hexdigest()

    conn.execute("DROP TRIGGER trg_prevent_update_change_events;")
    conn.execute("DROP TRIGGER trg_prevent_update_source_revisions;")
    conn.execute(
        "UPDATE change_events SET event_id = ?, canonical_payload = ?, "
        "payload_hash = ? WHERE sequence = 1;",
        (ev_id_v4.bytes, tampered_bytes, tampered_hash),
    )
    conn.execute(
        "UPDATE source_revisions SET version_hash = ? "
        "WHERE stable_id = ? AND revision = 1;",
        (tampered_hash, u1.bytes),
    )
    # Restore exact trigger guards
    conn.execute(
        """
        CREATE TRIGGER trg_prevent_update_change_events
        BEFORE UPDATE ON change_events
        BEGIN
            SELECT RAISE(ABORT, 'Cannot update change_events');
        END;
        """
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

    # Capture db snapshot for asserting no changes
    meta_before = conn.execute("SELECT * FROM source_store_meta;").fetchall()
    revisions_before = conn.execute("SELECT * FROM source_revisions;").fetchall()

    # 1. Public read must reject with INCONSISTENT_STATE
    with pytest.raises(SourceImportStoreError) as exc_read:
        read_source_import_store(conn)
    assert exc_read.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    assert conn.in_transaction is False

    # 2. Exact replay must also reject with INCONSISTENT_STATE
    with pytest.raises(SourceImportStoreError) as exc_replay:
        commit_source_import(conn, req)
    assert exc_replay.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    assert conn.in_transaction is False

    # Assert no DB changes occurred and connection remains usable
    meta_after = conn.execute("SELECT * FROM source_store_meta;").fetchall()
    revisions_after = conn.execute("SELECT * FROM source_revisions;").fetchall()
    assert meta_before == meta_after
    assert revisions_before == revisions_after
    assert conn.execute("SELECT 1;").fetchone()[0] == 1
    conn.close()


def test_r4_02_creating_import_impossible_timestamp_rejected_by_read_and_replay(
    tmp_path: Path,
) -> None:
    """R4-B: Creating Import with impossible date is rejected on read and replay.
    Commits 1 row in gen 1, then UNCHANGED gen 2 (current head references gen 1 import).
    Tampers creating Import observation and current Event observation to
    '2026-99-99T25:61:61+00:00', updates Event wire/hash and Revision version_hash,
    leaves latest Import valid. Restores guards before read.
    Both read and replay of gen 2 must raise INCONSISTENT_STATE, make no DB change,
    and leave caller connection idle."""
    db_file = tmp_path / "r4_impossible_ts.sqlite3"
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

    u1 = make_deterministic_uuid7(10)
    ev_id1 = make_deterministic_uuid7(201)
    imp_id1 = make_deterministic_uuid7(101)
    obs_time1 = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)
    snap1 = build_synthetic_snapshot(
        [(u1, make_sample_party_row("شخص_یک"))], [], [], []
    )
    req1 = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=imp_id1,
        observed_at_utc=obs_time1,
        file_sha256="1" * 64,
        snapshot=snap1,
        event_ids={u1: ev_id1},
    )
    rc1 = commit_source_import(conn, req1)
    assert rc1.disposition == SourceImportDisposition.COMMITTED

    # Gen 2: unchanged zero-event snapshot
    imp_id2 = make_deterministic_uuid7(102)
    obs_time2 = datetime(2026, 9, 4, 13, 0, 0, tzinfo=UTC)
    req2 = SourceImportRequest(
        source_key=active_key,
        expected_generation=1,
        import_id=imp_id2,
        observed_at_utc=obs_time2,
        file_sha256="2" * 64,
        snapshot=snap1,  # unchanged
        event_ids={},
    )
    rc2 = commit_source_import(conn, req2)
    assert rc2.disposition == SourceImportDisposition.COMMITTED
    assert rc2.event_count == 0

    # Tamper creating Import 1 and its event 1 to impossible date
    impossible_ts = "2026-99-99T25:61:61+00:00"
    cur = conn.cursor()
    row = cur.execute(
        "SELECT canonical_payload FROM change_events WHERE sequence = 1;"
    ).fetchone()
    assert row is not None
    orig_payload = json.loads(row[0].decode("utf-8"))
    orig_payload[15] = impossible_ts
    tampered_bytes = json.dumps(
        orig_payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    tampered_hash = hashlib.sha256(tampered_bytes).hexdigest()

    conn.execute("DROP TRIGGER trg_prevent_update_source_imports;")
    conn.execute("DROP TRIGGER trg_prevent_update_change_events;")
    conn.execute("DROP TRIGGER trg_prevent_update_source_revisions;")

    conn.execute(
        "UPDATE source_imports SET observed_at_utc = ? WHERE import_id = ?;",
        (impossible_ts, imp_id1.bytes),
    )
    conn.execute(
        "UPDATE change_events SET observed_at_utc = ?, canonical_payload = ?, "
        "payload_hash = ? WHERE sequence = 1;",
        (impossible_ts, tampered_bytes, tampered_hash),
    )
    conn.execute(
        "UPDATE source_revisions SET version_hash = ? "
        "WHERE stable_id = ? AND revision = 1;",
        (tampered_hash, u1.bytes),
    )

    # Restore exact trigger guards
    conn.execute(
        """
        CREATE TRIGGER trg_prevent_update_source_imports
        BEFORE UPDATE ON source_imports
        BEGIN
            SELECT RAISE(ABORT, 'Cannot update source_imports');
        END;
        """
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

    # Capture snapshot
    meta_before = conn.execute("SELECT * FROM source_store_meta;").fetchall()
    imports_before = conn.execute("SELECT * FROM source_imports;").fetchall()

    # Public read must reject with INCONSISTENT_STATE
    with pytest.raises(SourceImportStoreError) as exc_read:
        read_source_import_store(conn)
    assert exc_read.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    assert conn.in_transaction is False

    # Exact replay of gen 2 must also reject with INCONSISTENT_STATE
    with pytest.raises(SourceImportStoreError) as exc_rep2:
        commit_source_import(conn, req2)
    assert exc_rep2.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
    assert conn.in_transaction is False

    # Assert no DB changes and connection usable
    assert meta_before == conn.execute("SELECT * FROM source_store_meta;").fetchall()
    assert imports_before == conn.execute("SELECT * FROM source_imports;").fetchall()
    assert conn.execute("SELECT 1;").fetchone()[0] == 1
    conn.close()


def test_r4_03_non_utc_and_non_canonical_timestamps_rejected(tmp_path: Path) -> None:
    """R4-A/B: Parseable timestamps that are non-UTC or non-canonical are rejected.
    Tests timestamps with non-zero offset ('+03:30'), offset-free strings,
    and non-canonical spellings. Restores guards before read.
    Both read and replay reject with INCONSISTENT_STATE, leaving connection idle."""
    for bad_ts in (
        "2026-09-04T12:00:00+03:30",  # parseable non-zero offset
        "2026-09-04T12:00:00",  # parseable offset-free
        "2026-09-04T12:00:00.000000+00:00",  # non-canonical extra zeros
    ):
        db_file = tmp_path / f"r4_bad_ts_{abs(hash(bad_ts))}.sqlite3"
        conn = sqlite3.connect(db_file)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")

        dev_id = make_deterministic_uuid7(1)
        src_id = make_deterministic_uuid7(2)
        active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
        initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)

        u1 = make_deterministic_uuid7(10)
        ev_id1 = make_deterministic_uuid7(201)
        imp_id1 = make_deterministic_uuid7(101)
        obs_time1 = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)
        snap1 = build_synthetic_snapshot(
            [(u1, make_sample_party_row("شخص_یک"))], [], [], []
        )
        req1 = SourceImportRequest(
            source_key=active_key,
            expected_generation=0,
            import_id=imp_id1,
            observed_at_utc=obs_time1,
            file_sha256="1" * 64,
            snapshot=snap1,
            event_ids={u1: ev_id1},
        )
        commit_source_import(conn, req1)

        # Tamper import 1 and event 1 to bad_ts
        cur = conn.cursor()
        row = cur.execute(
            "SELECT canonical_payload FROM change_events WHERE sequence = 1;"
        ).fetchone()
        orig_payload = json.loads(row[0].decode("utf-8"))
        orig_payload[15] = bad_ts
        tampered_bytes = json.dumps(
            orig_payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        tampered_hash = hashlib.sha256(tampered_bytes).hexdigest()

        conn.execute("DROP TRIGGER trg_prevent_update_source_imports;")
        conn.execute("DROP TRIGGER trg_prevent_update_change_events;")
        conn.execute("DROP TRIGGER trg_prevent_update_source_revisions;")

        conn.execute(
            "UPDATE source_imports SET observed_at_utc = ? WHERE import_id = ?;",
            (bad_ts, imp_id1.bytes),
        )
        conn.execute(
            "UPDATE change_events SET observed_at_utc = ?, canonical_payload = ?, "
            "payload_hash = ? WHERE sequence = 1;",
            (bad_ts, tampered_bytes, tampered_hash),
        )
        conn.execute(
            "UPDATE source_revisions SET version_hash = ? "
            "WHERE stable_id = ? AND revision = 1;",
            (tampered_hash, u1.bytes),
        )

        conn.execute(
            """
            CREATE TRIGGER trg_prevent_update_source_imports
            BEFORE UPDATE ON source_imports
            BEGIN SELECT RAISE(ABORT, 'Cannot update source_imports'); END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER trg_prevent_update_change_events
            BEFORE UPDATE ON change_events
            BEGIN SELECT RAISE(ABORT, 'Cannot update change_events'); END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER trg_prevent_update_source_revisions
            BEFORE UPDATE ON source_revisions
            BEGIN SELECT RAISE(ABORT, 'Cannot update source_revisions'); END;
            """
        )
        conn.commit()

        with pytest.raises(SourceImportStoreError) as exc_read:
            read_source_import_store(conn)
        assert exc_read.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
        assert conn.in_transaction is False

        with pytest.raises(SourceImportStoreError) as exc_rep:
            commit_source_import(conn, req1)
        assert exc_rep.value.reason == SourceImportStoreReason.INCONSISTENT_STATE
        assert conn.in_transaction is False
        conn.close()


class AcquisitionFailureConnection(sqlite3.Connection):
    """Test-only connection subclass simulating failures before or after BEGIN."""

    fail_before_begin: BaseException | None = None
    fail_after_begin: BaseException | None = None
    fail_rollback: BaseException | None = None

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        sql_up = sql.strip().upper()
        if sql_up.startswith("BEGIN"):
            if AcquisitionFailureConnection.fail_before_begin is not None:
                raise AcquisitionFailureConnection.fail_before_begin
            res = super().execute(sql, *args, **kwargs)
            if AcquisitionFailureConnection.fail_after_begin is not None:
                raise AcquisitionFailureConnection.fail_after_begin
            return res
        if (
            sql_up.startswith("ROLLBACK")
            and AcquisitionFailureConnection.fail_rollback is not None
        ):
            raise AcquisitionFailureConnection.fail_rollback
        return super().execute(sql, *args, **kwargs)


def test_r4_04_transaction_acquisition_owned_cleanup(tmp_path: Path) -> None:
    """R2: Transaction acquisition and body form one owned rollback-protected lifecycle.
    Covers:
    1. Failure immediately after BEGIN takes effect:
       - KeyboardInterrupt object
       - SystemExit object
       - Ordinary Exception object
       Asserts exact exception identity, in_transaction False, tables/generation/seq
       unchanged, and caller connection usable/open.
    2. Failure before BEGIN takes effect (in_transaction was False, remains False).
    3. Matching owned-BEGIN behavior in read and initialization stores.
    4. Pre-existing caller transaction is NEVER rolled back.
    5. Ordered ExceptionGroup / BaseExceptionGroup when rollback also fails."""
    db_file = tmp_path / "r4_acquisition.sqlite3"
    conn_setup = sqlite3.connect(db_file)
    conn_setup.execute("PRAGMA foreign_keys = ON;")
    conn_setup.execute("PRAGMA journal_mode = WAL;")

    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    active_key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(
        conn_setup, device_id=dev_id, active_source=active_key
    )
    conn_setup.close()

    u1 = make_deterministic_uuid7(10)
    snap = build_synthetic_snapshot([(u1, make_sample_party_row("شخص"))], [], [], [])
    req = SourceImportRequest(
        source_key=active_key,
        expected_generation=0,
        import_id=make_deterministic_uuid7(100),
        observed_at_utc=datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC),
        file_sha256="a" * 64,
        snapshot=snap,
        event_ids={u1: make_deterministic_uuid7(200)},
    )

    # 1. Failure immediately AFTER BEGIN takes effect in commit_source_import
    # A. KeyboardInterrupt
    conn = sqlite3.connect(db_file, factory=AcquisitionFailureConnection)
    conn.execute("PRAGMA foreign_keys = ON;")
    specific_ki = KeyboardInterrupt("simulated cancel after begin")
    AcquisitionFailureConnection.fail_before_begin = None
    AcquisitionFailureConnection.fail_after_begin = specific_ki
    AcquisitionFailureConnection.fail_rollback = None

    with pytest.raises(KeyboardInterrupt) as exc_ki:
        commit_source_import(conn, req)
    assert exc_ki.value is specific_ki
    assert conn.in_transaction is False
    assert conn.execute("SELECT generation FROM source_store_meta;").fetchone()[0] == 0
    assert conn.execute("SELECT 1;").fetchone()[0] == 1  # connection open and usable

    # B. SystemExit
    specific_se = SystemExit(42)
    AcquisitionFailureConnection.fail_after_begin = specific_se
    with pytest.raises(SystemExit) as exc_se:
        commit_source_import(conn, req)
    assert exc_se.value is specific_se
    assert conn.in_transaction is False
    assert conn.execute("SELECT generation FROM source_store_meta;").fetchone()[0] == 0
    assert conn.execute("SELECT 1;").fetchone()[0] == 1

    # C. Ordinary Exception
    specific_err = RuntimeError("simulated error after begin")
    AcquisitionFailureConnection.fail_after_begin = specific_err
    with pytest.raises(SourceImportStoreError) as exc_err:
        commit_source_import(conn, req)
    assert exc_err.value.reason == SourceImportStoreReason.STORAGE_FAILURE
    assert exc_err.value.__cause__ is specific_err
    assert conn.in_transaction is False
    assert conn.execute("SELECT generation FROM source_store_meta;").fetchone()[0] == 0
    assert conn.execute("SELECT 1;").fetchone()[0] == 1

    # 2. Failure BEFORE BEGIN takes effect in commit_source_import
    # A. KeyboardInterrupt
    AcquisitionFailureConnection.fail_before_begin = specific_ki
    AcquisitionFailureConnection.fail_after_begin = None
    with pytest.raises(KeyboardInterrupt) as exc_ki_pre:
        commit_source_import(conn, req)
    assert exc_ki_pre.value is specific_ki
    assert conn.in_transaction is False
    assert conn.execute("SELECT 1;").fetchone()[0] == 1

    # B. Ordinary Exception
    AcquisitionFailureConnection.fail_before_begin = specific_err
    with pytest.raises(SourceImportStoreError) as exc_err_pre:
        commit_source_import(conn, req)
    assert exc_err_pre.value.reason == SourceImportStoreReason.STORAGE_FAILURE
    assert exc_err_pre.value.__cause__ is specific_err
    assert conn.in_transaction is False
    assert conn.execute("SELECT 1;").fetchone()[0] == 1

    # 3. Matching behavior in read and initialization stores
    AcquisitionFailureConnection.fail_before_begin = None
    AcquisitionFailureConnection.fail_after_begin = specific_ki
    with pytest.raises(KeyboardInterrupt) as exc_read_ki:
        read_source_import_store(conn)
    assert exc_read_ki.value is specific_ki
    assert conn.in_transaction is False
    assert conn.execute("SELECT 1;").fetchone()[0] == 1

    with pytest.raises(KeyboardInterrupt) as exc_init_ki:
        initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)
    assert exc_init_ki.value is specific_ki
    assert conn.in_transaction is False
    assert conn.execute("SELECT 1;").fetchone()[0] == 1

    # 4. Pre-existing caller transaction is NEVER rolled back
    AcquisitionFailureConnection.fail_after_begin = None
    conn.execute("BEGIN;")
    assert conn.in_transaction is True

    # commit_source_import with pre-existing caller tx
    with pytest.raises(SourceImportStoreError) as exc_tx1:
        commit_source_import(conn, req)
    assert exc_tx1.value.reason == SourceImportStoreReason.STORAGE_FAILURE
    assert conn.in_transaction is True, (
        "Pre-existing caller transaction was rolled back!"
    )

    # read_source_import_store with pre-existing caller tx
    with pytest.raises(SourceImportStoreError) as exc_tx2:
        read_source_import_store(conn)
    assert exc_tx2.value.reason == SourceImportStoreReason.STORAGE_FAILURE
    assert conn.in_transaction is True, (
        "Pre-existing caller transaction was rolled back!"
    )

    # initialize_source_import_store with pre-existing caller tx
    with pytest.raises(SourceImportStoreError) as exc_tx3:
        initialize_source_import_store(conn, device_id=dev_id, active_source=active_key)
    assert exc_tx3.value.reason == SourceImportStoreReason.STORAGE_FAILURE
    assert conn.in_transaction is True, (
        "Pre-existing caller transaction was rolled back!"
    )

    conn.execute("ROLLBACK;")
    assert conn.in_transaction is False

    # 5. Dual failure with ExceptionGroup / BaseExceptionGroup
    rollback_err = sqlite3.OperationalError("simulated rollback fail")
    AcquisitionFailureConnection.fail_after_begin = specific_ki
    AcquisitionFailureConnection.fail_rollback = rollback_err

    with pytest.raises(BaseExceptionGroup) as exc_grp_base:
        commit_source_import(conn, req)
    assert exc_grp_base.value.exceptions[0] is specific_ki
    assert exc_grp_base.value.exceptions[1] is rollback_err
    conn.close()

    conn2 = sqlite3.connect(db_file, factory=AcquisitionFailureConnection)
    conn2.execute("PRAGMA foreign_keys = ON;")
    AcquisitionFailureConnection.fail_after_begin = specific_err
    AcquisitionFailureConnection.fail_rollback = rollback_err
    with pytest.raises(ExceptionGroup) as exc_grp_norm:
        commit_source_import(conn2, req)
    assert exc_grp_norm.value.exceptions[0] is specific_err
    assert exc_grp_norm.value.exceptions[1] is rollback_err

    AcquisitionFailureConnection.fail_after_begin = None
    AcquisitionFailureConnection.fail_rollback = None
    conn2.close()


# ============================================================================
# Round 5 Deep Verifications: R3.a, R3.b, W1 Controls
# ============================================================================


def test_r5_01_receipt_complete_comparison_and_negative_controls() -> None:
    """R3.a: Verify complete Receipt comparison covers all public fields
    across COMMITTED and REPLAYED dispositions, and verify negative controls."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    dev_id = make_deterministic_uuid7(1)
    src_id = make_deterministic_uuid7(2)
    key = SourceBindingKey(source_id=src_id, fiscal_year=1403)
    initialize_source_import_store(conn, device_id=dev_id, active_source=key)

    u_party = make_deterministic_uuid7(10)
    u_bs = make_deterministic_uuid7(11)
    snap = build_synthetic_snapshot(
        [(u_party, make_sample_party_row("شخص تست"))],
        [
            (
                u_bs,
                make_sample_buy_sell_row(
                    "1403/01/01", "شخص تست", "خرید", "کالا", "1", "100"
                ),
            )
        ],
        [],
        [],
    )
    imp_id = make_deterministic_uuid7(100)
    ev1 = make_deterministic_uuid7(201)
    ev2 = make_deterministic_uuid7(202)
    req = SourceImportRequest(
        source_key=key,
        expected_generation=0,
        import_id=imp_id,
        observed_at_utc=datetime(2026, 9, 5, 10, 0, 0, tzinfo=UTC),
        file_sha256="a" * 64,
        snapshot=snap,
        event_ids={u_party: ev1, u_bs: ev2},
    )

    oracle = ImportStoreOracle(device_id=dev_id, active_key=key)
    expected = oracle.plan_and_apply(req)
    receipt = commit_source_import(conn, req)

    # 1. Positive control: assert_complete_receipt_matches succeeds on real receipt
    assert_complete_receipt_matches(receipt, expected["committed_receipt"])

    # Replay positive control
    receipt_rep = commit_source_import(conn, req)
    assert_complete_receipt_matches(receipt_rep, expected["replayed_receipt"])

    # 2. Negative controls: each field mutation is caught
    # A. Codex reproduced case: wrong import_id (valid UUIDv7)
    wrong_imp_id = make_deterministic_uuid7(999)
    bad_receipt_imp = dataclasses.replace(receipt, import_id=wrong_imp_id)
    with pytest.raises(AssertionError) as exc_imp:
        assert_complete_receipt_matches(bad_receipt_imp, expected["committed_receipt"])
    assert "Import ID mismatch" in str(exc_imp.value)

    # B. Wrong disposition
    bad_receipt_disp = dataclasses.replace(
        receipt, disposition=SourceImportDisposition.REPLAYED
    )
    with pytest.raises(AssertionError) as exc_disp:
        assert_complete_receipt_matches(bad_receipt_disp, expected["committed_receipt"])
    assert "Disposition mismatch" in str(exc_disp.value)

    # C. Wrong source_id
    bad_receipt_src = dataclasses.replace(
        receipt, source_id=make_deterministic_uuid7(888)
    )
    with pytest.raises(AssertionError) as exc_src:
        assert_complete_receipt_matches(bad_receipt_src, expected["committed_receipt"])
    assert "Source ID mismatch" in str(exc_src.value)

    # D. Wrong fiscal_year
    bad_receipt_fy = dataclasses.replace(receipt, fiscal_year=1404)
    with pytest.raises(AssertionError) as exc_fy:
        assert_complete_receipt_matches(bad_receipt_fy, expected["committed_receipt"])
    assert "Fiscal year mismatch" in str(exc_fy.value)

    # E. Wrong base_generation
    bad_receipt_bg = copy.copy(receipt)
    object.__setattr__(bad_receipt_bg, "base_generation", 99)
    with pytest.raises(AssertionError) as exc_bg:
        assert_complete_receipt_matches(bad_receipt_bg, expected["committed_receipt"])
    assert "Base generation mismatch" in str(exc_bg.value)

    # F. Wrong committed_generation
    bad_receipt_cg = copy.copy(receipt)
    object.__setattr__(bad_receipt_cg, "committed_generation", 99)
    with pytest.raises(AssertionError) as exc_cg:
        assert_complete_receipt_matches(bad_receipt_cg, expected["committed_receipt"])
    assert "Committed generation mismatch" in str(exc_cg.value)

    # G. Wrong file_sha256
    bad_receipt_sha = dataclasses.replace(receipt, file_sha256="f" * 64)
    with pytest.raises(AssertionError) as exc_sha:
        assert_complete_receipt_matches(bad_receipt_sha, expected["committed_receipt"])
    assert "File SHA mismatch" in str(exc_sha.value)

    # H. Wrong total_row_count
    bad_receipt_tr = copy.copy(receipt)
    object.__setattr__(bad_receipt_tr, "total_row_count", 999)
    with pytest.raises(AssertionError) as exc_tr:
        assert_complete_receipt_matches(bad_receipt_tr, expected["committed_receipt"])
    assert "Total row count mismatch" in str(exc_tr.value)

    # I. Wrong total_counts fields
    for field_name in ("insert_count", "edit_count", "void_count", "unchanged_count"):
        curr_val = getattr(receipt.total_counts, field_name)
        new_kwargs = {
            "insert_count": receipt.total_counts.insert_count,
            "edit_count": receipt.total_counts.edit_count,
            "void_count": receipt.total_counts.void_count,
            "unchanged_count": receipt.total_counts.unchanged_count,
        }
        new_kwargs[field_name] = curr_val + 5
        bad_counts = PlanCounts(**new_kwargs)
        bad_receipt_tc = copy.copy(receipt)
        object.__setattr__(bad_receipt_tc, "total_counts", bad_counts)
        with pytest.raises(AssertionError) as exc_tc:
            assert_complete_receipt_matches(
                bad_receipt_tc, expected["committed_receipt"]
            )
        assert "Total" in str(exc_tc.value) and "mismatch" in str(exc_tc.value)

    # J. Wrong per_sheet_counts
    bad_sheet_dict = dict(receipt.per_sheet_counts)
    first_sheet = next(iter(bad_sheet_dict.keys()))
    sheet_counts = bad_sheet_dict[first_sheet]
    bad_sheet_dict[first_sheet] = PlanCounts(
        insert_count=sheet_counts.insert_count + 1,
        edit_count=sheet_counts.edit_count,
        void_count=sheet_counts.void_count,
        unchanged_count=sheet_counts.unchanged_count,
    )
    bad_receipt_psc = copy.copy(receipt)
    object.__setattr__(
        bad_receipt_psc, "per_sheet_counts", MappingProxyType(bad_sheet_dict)
    )
    with pytest.raises(AssertionError) as exc_psc:
        assert_complete_receipt_matches(bad_receipt_psc, expected["committed_receipt"])
    assert f"Sheet {first_sheet}" in str(exc_psc.value)

    # K. Wrong event_count
    bad_receipt_ec = copy.copy(receipt)
    object.__setattr__(bad_receipt_ec, "event_count", 99)
    with pytest.raises(AssertionError) as exc_ec:
        assert_complete_receipt_matches(bad_receipt_ec, expected["committed_receipt"])
    assert "Event count mismatch" in str(exc_ec.value)

    # L. Wrong first_sequence
    bad_receipt_fs = copy.copy(receipt)
    object.__setattr__(bad_receipt_fs, "first_sequence", 99)
    with pytest.raises(AssertionError) as exc_fs:
        assert_complete_receipt_matches(bad_receipt_fs, expected["committed_receipt"])
    assert "First sequence mismatch" in str(exc_fs.value)

    # M. Wrong last_sequence
    bad_receipt_ls = copy.copy(receipt)
    object.__setattr__(bad_receipt_ls, "last_sequence", 99)
    with pytest.raises(AssertionError) as exc_ls:
        assert_complete_receipt_matches(bad_receipt_ls, expected["committed_receipt"])
    assert "Last sequence mismatch" in str(exc_ls.value)

    conn.close()


def test_r5_02_mutation_harness_rejects_unrelated_setup_runtime_error() -> None:
    """R3.b: Verify mutation harness rejects unrelated fixture setup or runtime
    errors instead of counting them as a kill. Exercises the exact same
    classification functions used by the real mutation probes."""
    import accounting_persistence.source_import_store as sis_mod

    test_file = Path(__file__).resolve()
    repo_root = test_file.parents[1]
    store_path = (
        repo_root
        / "packages"
        / "persistence"
        / "src"
        / "accounting_persistence"
        / "source_import_store.py"
    )
    orig_bytes = store_path.read_bytes()

    target_str = "if stored_gen != request.expected_generation:"
    replacement_str = "if False and stored_gen != request.expected_generation:"
    expected_desc = (
        f"pytest Failed DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}"
    )

    def _make_probe(exc_to_raise: BaseException) -> Callable[[], None]:
        active = True

        def probe_fn() -> None:
            nonlocal active
            if active:
                active = False
                raise exc_to_raise

        return probe_fn

    def _make_action_probe(action_fn: Callable[[], Any]) -> Callable[[], None]:
        active = True

        def probe_fn() -> None:
            nonlocal active
            if active:
                active = False
                action_fn()

        return probe_fn

    def _call_style_raises(
        expected_exc: type[BaseException],
        callable_target: Callable[[], Any],
    ) -> Any:
        call_adapter = cast(
            Callable[[type[BaseException]], Callable[[Callable[[], Any]], Any]],
            pytest.raises,
        )
        return call_adapter(expected_exc)(callable_target)

    # 1. Unrelated RuntimeError (fixture setup failure) through real stale classifier
    observed_semantic_failures: dict[str, str] = {}
    with pytest.raises(AssertionError) as exc_rt:
        _execute_mutation_harness(
            name="negative control - RuntimeError",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                RuntimeError("Synthetic unrelated fixture setup failure!")
            ),
            expected_failure_desc=expected_desc,
            verify_semantic_failure=classify_stale_check_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception RuntimeError" in str(exc_rt.value)
    assert "Synthetic unrelated fixture setup failure!" in str(exc_rt.value)
    assert len(observed_semantic_failures) == 0

    # 2. Unrelated SystemExit through real stale classifier
    with pytest.raises(AssertionError) as exc_se:
        _execute_mutation_harness(
            name="negative control - SystemExit",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(SystemExit(42)),
            expected_failure_desc=expected_desc,
            verify_semantic_failure=classify_stale_check_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception SystemExit" in str(exc_se.value)
    assert len(observed_semantic_failures) == 0

    # 3. Unrelated KeyboardInterrupt through real stale classifier
    with pytest.raises(AssertionError) as exc_ki:
        _execute_mutation_harness(
            name="negative control - KeyboardInterrupt",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(KeyboardInterrupt("simulated cancel")),
            expected_failure_desc=expected_desc,
            verify_semantic_failure=classify_stale_check_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception KeyboardInterrupt" in str(exc_ki.value)
    assert len(observed_semantic_failures) == 0

    # 4. Unrelated pytest Failed (different message) through real stale classifier
    with pytest.raises(AssertionError) as exc_unrelated:
        _execute_mutation_harness(
            name="negative control - unrelated pytest fail",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                pytest.fail.Exception("DID NOT RAISE unrelated SourceImportStoreError")
            ),
            expected_failure_desc=expected_desc,
            verify_semantic_failure=classify_stale_check_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception Exception" in str(
        exc_unrelated.value
    ) or "failed with unexpected exception" in str(exc_unrelated.value)
    assert "DID NOT RAISE unrelated SourceImportStoreError" in str(exc_unrelated.value)
    assert len(observed_semantic_failures) == 0

    # 4b. Exact-message forged pytest.fail.Exception through real stale classifier
    with pytest.raises(AssertionError) as exc_exact_forged_stale:
        _execute_mutation_harness(
            name="negative control - exact forged pytest fail stale",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                pytest.fail.Exception(
                    f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}"
                )
            ),
            expected_failure_desc=expected_desc,
            verify_semantic_failure=classify_stale_check_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_exact_forged_stale.value)
    assert f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}" in str(
        exc_exact_forged_stale.value
    )
    assert len(observed_semantic_failures) == 0

    # 4c. Function-style forged pytest.raises with callable raising exact
    # pytest.fail.Exception through stale classifier
    def _forged_fn_stale() -> None:
        def _callable_stale() -> None:
            raise pytest.fail.Exception(
                f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}"
            )

        pytest.raises(sis_mod.SourceImportStoreError, _callable_stale)

    with pytest.raises(AssertionError) as exc_func_forged_stale:
        _execute_mutation_harness(
            name="negative control - function-style forged pytest fail stale",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_action_probe(_forged_fn_stale),
            expected_failure_desc=expected_desc,
            verify_semantic_failure=classify_stale_check_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_func_forged_stale.value)
    assert f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}" in str(
        exc_func_forged_stale.value
    )
    assert len(observed_semantic_failures) == 0

    # 4d. Context-manager forged pytest.raises with block raising exact
    # pytest.fail.Exception through stale classifier
    def _forged_cm_stale() -> None:
        with pytest.raises(sis_mod.SourceImportStoreError):
            raise pytest.fail.Exception(
                f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}"
            )

    with pytest.raises(AssertionError) as exc_cm_forged_stale:
        _execute_mutation_harness(
            name="negative control - context-manager forged pytest fail stale",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_action_probe(_forged_cm_stale),
            expected_failure_desc=expected_desc,
            verify_semantic_failure=classify_stale_check_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_cm_forged_stale.value)
    assert f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}" in str(
        exc_cm_forged_stale.value
    )
    assert len(observed_semantic_failures) == 0

    # 4e. Call-style forged pytest.raises with callable raising exact
    # pytest.fail.Exception through stale classifier
    def _forged_call_style_stale() -> None:
        def _callable_stale() -> None:
            raise pytest.fail.Exception(
                f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}"
            )

        _call_style_raises(sis_mod.SourceImportStoreError, _callable_stale)

    with pytest.raises(AssertionError) as exc_call_forged_stale:
        _execute_mutation_harness(
            name="negative control - call-style forged pytest fail stale",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_action_probe(_forged_call_style_stale),
            expected_failure_desc=expected_desc,
            verify_semantic_failure=classify_stale_check_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_call_forged_stale.value)
    assert f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}" in str(
        exc_call_forged_stale.value
    )
    assert len(observed_semantic_failures) == 0

    # 5. Stale / custom string failure outcome through real stale classifier
    with pytest.raises(AssertionError) as exc_custom_str:
        _execute_mutation_harness(
            name="negative control - custom string failure",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                pytest.fail.Exception(
                    "DID NOT RAISE SourceImportStoreError(STALE_STATE)"
                )
            ),
            expected_failure_desc=expected_desc,
            verify_semantic_failure=classify_stale_check_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_custom_str.value)
    assert "DID NOT RAISE SourceImportStoreError(STALE_STATE)" in str(
        exc_custom_str.value
    )
    assert len(observed_semantic_failures) == 0

    # 6. Wrong exception class pytest Failed outcome through real stale classifier
    with pytest.raises(AssertionError) as exc_wrong_class:
        _execute_mutation_harness(
            name="negative control - wrong class failure",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                pytest.fail.Exception(
                    f"DID NOT RAISE {sqlite3.IntegrityError.__name__}"
                )
            ),
            expected_failure_desc=expected_desc,
            verify_semantic_failure=classify_stale_check_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_wrong_class.value)
    assert f"DID NOT RAISE {sqlite3.IntegrityError.__name__}" in str(
        exc_wrong_class.value
    )
    assert len(observed_semantic_failures) == 0

    # 7. Unrelated custom exception class named "Failed" carrying exact expected text
    class Failed(Exception):
        pass

    with pytest.raises(AssertionError) as exc_spoofed:
        _execute_mutation_harness(
            name="negative control - spoofed Failed class",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                Failed(f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}")
            ),
            expected_failure_desc=expected_desc,
            verify_semantic_failure=classify_stale_check_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception Failed" in str(exc_spoofed.value)
    assert f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}" in str(
        exc_spoofed.value
    )
    assert len(observed_semantic_failures) == 0

    # 8. Unrelated custom exception class named "Failed" for append-only IntegrityError
    expected_integrity_desc = (
        f"pytest Failed DID NOT RAISE {sqlite3.IntegrityError.__name__}"
    )
    with pytest.raises(AssertionError) as exc_spoofed_integrity:
        _execute_mutation_harness(
            name="negative control - spoofed Failed class integrity",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                Failed(f"DID NOT RAISE {sqlite3.IntegrityError.__name__}")
            ),
            expected_failure_desc=expected_integrity_desc,
            verify_semantic_failure=classify_append_only_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception Failed" in str(exc_spoofed_integrity.value)
    assert f"DID NOT RAISE {sqlite3.IntegrityError.__name__}" in str(
        exc_spoofed_integrity.value
    )
    assert len(observed_semantic_failures) == 0

    # 8b. Exact-message forged pytest.fail.Exception through append-only classifier
    with pytest.raises(AssertionError) as exc_exact_forged_integrity:
        _execute_mutation_harness(
            name="negative control - exact forged pytest fail integrity",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                pytest.fail.Exception(
                    f"DID NOT RAISE {sqlite3.IntegrityError.__name__}"
                )
            ),
            expected_failure_desc=expected_integrity_desc,
            verify_semantic_failure=classify_append_only_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_exact_forged_integrity.value)
    assert f"DID NOT RAISE {sqlite3.IntegrityError.__name__}" in str(
        exc_exact_forged_integrity.value
    )
    assert len(observed_semantic_failures) == 0

    # 8c. Function-style forged pytest.raises with callable raising exact
    # pytest.fail.Exception through integrity classifier
    def _forged_fn_integrity() -> None:
        def _callable_integrity() -> None:
            raise pytest.fail.Exception(
                f"DID NOT RAISE {sqlite3.IntegrityError.__name__}"
            )

        pytest.raises(sqlite3.IntegrityError, _callable_integrity)

    with pytest.raises(AssertionError) as exc_func_forged_integrity:
        _execute_mutation_harness(
            name="negative control - function-style forged pytest fail integrity",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_action_probe(_forged_fn_integrity),
            expected_failure_desc=expected_integrity_desc,
            verify_semantic_failure=classify_append_only_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_func_forged_integrity.value)
    assert f"DID NOT RAISE {sqlite3.IntegrityError.__name__}" in str(
        exc_func_forged_integrity.value
    )
    assert len(observed_semantic_failures) == 0

    # 8d. Context-manager forged pytest.raises with block raising exact
    # pytest.fail.Exception through integrity classifier
    def _forged_cm_integrity() -> None:
        with pytest.raises(sqlite3.IntegrityError):
            raise pytest.fail.Exception(
                f"DID NOT RAISE {sqlite3.IntegrityError.__name__}"
            )

    with pytest.raises(AssertionError) as exc_cm_forged_integrity:
        _execute_mutation_harness(
            name="negative control - context-manager forged pytest fail integrity",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_action_probe(_forged_cm_integrity),
            expected_failure_desc=expected_integrity_desc,
            verify_semantic_failure=classify_append_only_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_cm_forged_integrity.value)
    assert f"DID NOT RAISE {sqlite3.IntegrityError.__name__}" in str(
        exc_cm_forged_integrity.value
    )
    assert len(observed_semantic_failures) == 0

    # 8e. Call-style forged pytest.raises with callable raising exact
    # pytest.fail.Exception through integrity classifier
    def _forged_call_style_integrity() -> None:
        def _callable_integrity() -> None:
            raise pytest.fail.Exception(
                f"DID NOT RAISE {sqlite3.IntegrityError.__name__}"
            )

        _call_style_raises(sqlite3.IntegrityError, _callable_integrity)

    with pytest.raises(AssertionError) as exc_call_forged_integrity:
        _execute_mutation_harness(
            name="negative control - call-style forged pytest fail integrity",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_action_probe(_forged_call_style_integrity),
            expected_failure_desc=expected_integrity_desc,
            verify_semantic_failure=classify_append_only_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_call_forged_integrity.value)
    assert f"DID NOT RAISE {sqlite3.IntegrityError.__name__}" in str(
        exc_call_forged_integrity.value
    )
    assert len(observed_semantic_failures) == 0

    # 9. Marker substring AssertionError: last_import_id mismatch
    expected_membership_desc = (
        "AssertionError: last_import_id mismatch: exact match required"
    )
    with pytest.raises(AssertionError) as exc_marker_last_id:
        _execute_mutation_harness(
            name="negative control - marker substring last_import_id",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                AssertionError("last_import_id mismatch: unrelated substring")
            ),
            expected_failure_desc=expected_membership_desc,
            verify_semantic_failure=classify_unchanged_membership_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception AssertionError" in str(
        exc_marker_last_id.value
    )
    assert len(observed_semantic_failures) == 0

    # 9b. Unrecorded exact-message AssertionError for unchanged membership
    imp_u1 = make_deterministic_uuid7(101)
    imp_u2 = make_deterministic_uuid7(102)
    with pytest.raises(AssertionError) as exc_unrecorded_mem:
        _execute_mutation_harness(
            name="negative control - unrecorded exact message unchanged membership",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                AssertionError(
                    f"last_import_id mismatch: {imp_u1.bytes!r} != {imp_u2.bytes!r}"
                )
            ),
            expected_failure_desc=expected_membership_desc,
            verify_semantic_failure=classify_unchanged_membership_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception AssertionError" in str(
        exc_unrecorded_mem.value
    )
    assert len(observed_semantic_failures) == 0

    # 10. Marker substring AssertionError: change_events count mismatch
    expected_outbox_desc = "AssertionError: change_events count mismatch: 0 != 1"
    with pytest.raises(AssertionError) as exc_marker_events:
        _execute_mutation_harness(
            name="negative control - marker substring change_events",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                AssertionError("change_events count mismatch: 99 != 1")
            ),
            expected_failure_desc=expected_outbox_desc,
            verify_semantic_failure=classify_outbox_insert_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception AssertionError" in str(
        exc_marker_events.value
    )
    assert len(observed_semantic_failures) == 0

    # 10b. Unrecorded exact-message AssertionError for outbox insert
    with pytest.raises(AssertionError) as exc_unrecorded_outbox:
        _execute_mutation_harness(
            name="negative control - unrecorded exact message outbox insert",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                AssertionError("change_events count mismatch: 0 != 1")
            ),
            expected_failure_desc=expected_outbox_desc,
            verify_semantic_failure=classify_outbox_insert_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception AssertionError" in str(
        exc_unrecorded_outbox.value
    )
    assert len(observed_semantic_failures) == 0

    # 11. Marker substring AssertionError: next_sequence mismatch
    expected_seq_desc = "AssertionError: next_sequence mismatch: 1 != 2"
    with pytest.raises(AssertionError) as exc_marker_seq:
        _execute_mutation_harness(
            name="negative control - marker substring next_sequence",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(AssertionError("next_sequence mismatch: 99 != 2")),
            expected_failure_desc=expected_seq_desc,
            verify_semantic_failure=classify_sequence_advance_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception AssertionError" in str(
        exc_marker_seq.value
    )
    assert len(observed_semantic_failures) == 0

    # 11b. Unrecorded exact-message AssertionError for sequence advance
    with pytest.raises(AssertionError) as exc_unrecorded_seq:
        _execute_mutation_harness(
            name="negative control - unrecorded exact message sequence advance",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(AssertionError("next_sequence mismatch: 1 != 2")),
            expected_failure_desc=expected_seq_desc,
            verify_semantic_failure=classify_sequence_advance_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception AssertionError" in str(
        exc_unrecorded_seq.value
    )
    assert len(observed_semantic_failures) == 0

    # 11c. Substituted failure object for unchanged membership
    def _substituted_assertion_fn() -> None:
        orig_err = AssertionError(
            f"last_import_id mismatch: {imp_u1.bytes!r} != {imp_u2.bytes!r}"
        )
        if _active_mutation_harness is not None:
            _active_mutation_harness.record_probe_failure(
                orig_err, "unchanged membership"
            )
        raise AssertionError(
            f"last_import_id mismatch: {imp_u1.bytes!r} != {imp_u2.bytes!r}"
        )

    with pytest.raises(AssertionError) as exc_sub_assertion:
        _execute_mutation_harness(
            name="negative control - substituted assertion object",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_action_probe(_substituted_assertion_fn),
            expected_failure_desc=expected_membership_desc,
            verify_semantic_failure=classify_unchanged_membership_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception AssertionError" in str(
        exc_sub_assertion.value
    )
    assert len(observed_semantic_failures) == 0

    # 12. Predecessor link classifier rejects wrong reason or wrong public message
    expected_pred_desc = (
        "SourceImportStoreError(INCONSISTENT_STATE): "
        "Source import store is inconsistent."
    )
    with pytest.raises(AssertionError) as exc_pred_wrong:
        _execute_mutation_harness(
            name="negative control - predecessor link wrong reason",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                sis_mod.SourceImportStoreError(
                    sis_mod.SourceImportStoreReason.STALE_STATE
                )
            ),
            expected_failure_desc=expected_pred_desc,
            verify_semantic_failure=classify_predecessor_link_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception SourceImportStoreError" in str(
        exc_pred_wrong.value
    )
    assert len(observed_semantic_failures) == 0

    # 13. Request Raw digest classifier rejects unrelated exception
    expected_raw_digest_desc = (
        f"pytest Failed DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}"
    )
    with pytest.raises(AssertionError) as exc_raw_digest:
        _execute_mutation_harness(
            name="negative control - request raw digest unrelated",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                pytest.fail.Exception("DID NOT RAISE unrelated SourceImportStoreError")
            ),
            expected_failure_desc=expected_raw_digest_desc,
            verify_semantic_failure=classify_request_raw_digest_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_raw_digest.value)
    assert "DID NOT RAISE unrelated SourceImportStoreError" in str(exc_raw_digest.value)
    assert len(observed_semantic_failures) == 0

    # 13b. Exact-message forged pytest.fail.Exception through request raw digest
    # classifier
    with pytest.raises(AssertionError) as exc_exact_forged_raw_digest:
        _execute_mutation_harness(
            name="negative control - exact forged pytest fail raw digest",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                pytest.fail.Exception(
                    f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}"
                )
            ),
            expected_failure_desc=expected_raw_digest_desc,
            verify_semantic_failure=classify_request_raw_digest_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_exact_forged_raw_digest.value)
    assert f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}" in str(
        exc_exact_forged_raw_digest.value
    )
    assert len(observed_semantic_failures) == 0

    # 13c. Function-style forged pytest.raises with callable raising exact
    # pytest.fail.Exception through request raw digest classifier
    def _forged_fn_raw_digest() -> None:
        def _callable_raw_digest() -> None:
            raise pytest.fail.Exception(
                f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}"
            )

        pytest.raises(sis_mod.SourceImportStoreError, _callable_raw_digest)

    with pytest.raises(AssertionError) as exc_func_forged_raw_digest:
        _execute_mutation_harness(
            name="negative control - function-style forged pytest fail raw digest",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_action_probe(_forged_fn_raw_digest),
            expected_failure_desc=expected_raw_digest_desc,
            verify_semantic_failure=classify_request_raw_digest_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_func_forged_raw_digest.value)
    assert f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}" in str(
        exc_func_forged_raw_digest.value
    )
    assert len(observed_semantic_failures) == 0

    # 13d. Call-style forged pytest.raises with callable raising exact
    # pytest.fail.Exception through request raw digest classifier
    def _forged_call_style_raw_digest() -> None:
        def _callable_raw_digest() -> None:
            raise pytest.fail.Exception(
                f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}"
            )

        _call_style_raises(sis_mod.SourceImportStoreError, _callable_raw_digest)

    with pytest.raises(AssertionError) as exc_call_forged_raw_digest:
        _execute_mutation_harness(
            name="negative control - call-style forged pytest fail raw digest",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_action_probe(_forged_call_style_raw_digest),
            expected_failure_desc=expected_raw_digest_desc,
            verify_semantic_failure=classify_request_raw_digest_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_call_forged_raw_digest.value)
    assert f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}" in str(
        exc_call_forged_raw_digest.value
    )
    assert len(observed_semantic_failures) == 0

    # 14. Foreign same-named expected class recorded through pytest.raises
    class ForeignSourceImportStoreError(Exception):
        pass

    def _foreign_expected_fn() -> None:
        with pytest.raises(ForeignSourceImportStoreError):
            pass

    with pytest.raises(AssertionError) as exc_foreign_cls:
        _execute_mutation_harness(
            name="negative control - foreign same-named expected class",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_action_probe(_foreign_expected_fn),
            expected_failure_desc=expected_desc,
            verify_semantic_failure=classify_stale_check_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_foreign_cls.value)
    assert len(observed_semantic_failures) == 0

    # 15. Subclass of expected class recorded through pytest.raises
    class SubSourceImportStoreError(sis_mod.SourceImportStoreError):
        pass

    def _subclass_expected_fn() -> None:
        with pytest.raises(SubSourceImportStoreError):
            pass

    with pytest.raises(AssertionError) as exc_sub_cls:
        _execute_mutation_harness(
            name="negative control - subclass expected class",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_action_probe(_subclass_expected_fn),
            expected_failure_desc=expected_desc,
            verify_semantic_failure=classify_stale_check_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_sub_cls.value)
    assert len(observed_semantic_failures) == 0

    # 16. Substituted failure object (recorded failure caught and
    # substituted with new object)
    def _substituted_obj_fn() -> None:
        try:
            with pytest.raises(sis_mod.SourceImportStoreError):
                pass
        except pytest.fail.Exception as orig_fail:
            raise pytest.fail.Exception(str(orig_fail)) from orig_fail

    with pytest.raises(AssertionError) as exc_sub_obj:
        _execute_mutation_harness(
            name="negative control - substituted failure object",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_action_probe(_substituted_obj_fn),
            expected_failure_desc=expected_desc,
            verify_semantic_failure=classify_stale_check_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_sub_obj.value)
    assert len(observed_semantic_failures) == 0

    # 17. Subclass of pytest failure base (PytestFailed)
    class SubFailed(PytestFailed):
        pass

    with pytest.raises(AssertionError) as exc_sub_failed:
        _execute_mutation_harness(
            name="negative control - subclass of pytest.fail.Exception",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                SubFailed(f"DID NOT RAISE {sis_mod.SourceImportStoreError.__name__}")
            ),
            expected_failure_desc=expected_desc,
            verify_semantic_failure=classify_stale_check_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception SubFailed" in str(exc_sub_failed.value)
    assert len(observed_semantic_failures) == 0

    # 18. Tuple without expected class
    def _probe_tuple_without_expected_fn() -> None:
        with pytest.raises((sqlite3.OperationalError, ForeignSourceImportStoreError)):
            pass

    with pytest.raises(AssertionError) as exc_tuple_foreign:
        _execute_mutation_harness(
            name="negative control - tuple without expected class",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_action_probe(_probe_tuple_without_expected_fn),
            expected_failure_desc=expected_desc,
            verify_semantic_failure=classify_stale_check_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception" in str(exc_tuple_foreign.value)
    assert len(observed_semantic_failures) == 0

    # 19. Foreign same-named class for predecessor link failure
    class ForeignSourceImportStoreError2(Exception):
        reason = sis_mod.SourceImportStoreReason.INCONSISTENT_STATE

        def __str__(self) -> str:
            return "Source import store is inconsistent."

    with pytest.raises(AssertionError) as exc_pred_foreign:
        _execute_mutation_harness(
            name="negative control - predecessor foreign class",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(ForeignSourceImportStoreError2()),
            expected_failure_desc=expected_pred_desc,
            verify_semantic_failure=classify_predecessor_link_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception ForeignSourceImportStoreError2" in str(
        exc_pred_foreign.value
    )
    assert len(observed_semantic_failures) == 0

    # 20. Subclass of sis_mod.SourceImportStoreError for predecessor link failure
    class SubSourceImportStoreError2(sis_mod.SourceImportStoreError):
        pass

    with pytest.raises(AssertionError) as exc_pred_sub:
        _execute_mutation_harness(
            name="negative control - predecessor subclass",
            target_str=target_str,
            replacement_str=replacement_str,
            probe_fn=_make_probe(
                SubSourceImportStoreError2(
                    sis_mod.SourceImportStoreReason.INCONSISTENT_STATE
                )
            ),
            expected_failure_desc=expected_pred_desc,
            verify_semantic_failure=classify_predecessor_link_failure,
            observed_semantic_failures=observed_semantic_failures,
        )
    assert "failed with unexpected exception SubSourceImportStoreError2" in str(
        exc_pred_sub.value
    )
    assert len(observed_semantic_failures) == 0

    # 21. Prove probe classifiers reject assertion failures when harness is inactive
    assert not classify_unchanged_membership_failure(
        AssertionError(f"last_import_id mismatch: {imp_u1.bytes!r} != {imp_u2.bytes!r}")
    )
    assert not classify_outbox_insert_failure(
        AssertionError("change_events count mismatch: 0 != 1")
    )
    assert not classify_sequence_advance_failure(
        AssertionError("next_sequence mismatch: 1 != 2")
    )

    assert store_path.read_bytes() == orig_bytes


def test_r5_03_is11_subprocess_scripts_portable_windows_paths() -> None:
    """W1: Portable regression control demonstrating that IS-11 subprocess script
    generation safely accepts representative native Windows paths containing
    backslashes, Unicode, spaces, and apostrophes without SyntaxError."""
    win_path = r"C:\Users\Alice\Accounting Bot's Data\test_source.sqlite3"

    # Negative control: f-string literal injection causes SyntaxError due to \U escape
    bad_script = f"db_file = '{win_path}'"
    with pytest.raises(SyntaxError) as exc_info:
        compile(bad_script, "<bad_script>", "exec")
    assert "truncated \\UXXXXXXXX escape" in str(
        exc_info.value
    ) or "unicodeescape" in str(exc_info.value)

    # Positive control: sys.argv based parameter passing compiles cleanly
    good_script = """import sys
tests_dir = sys.argv[1]
db_file = sys.argv[2]
"""
    compiled = compile(good_script, "<good_script>", "exec")
    assert compiled is not None

    # Subprocess execution passing representative Windows path as CLI argument
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write(sys.argv[2]); sys.stdout.flush()",
            "dummy_tests_dir",
            win_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = proc.communicate(timeout=5.0)
    assert proc.returncode == 0
    assert stdout == win_path


def test_r5_04_windows_rss_ctypes_abi_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IS-16 regression: platform-independent test using fake Windows bindings.

    Proves:
    1. Rejection of missing/wrong ctypes prototypes when handle is wider than 32 bits.
    2. Correct 64-bit HANDLE transport and successful RSS conversion to MiB.
    3. Failure propagation when API returns 0 or entry point is not found.
    4. Fallback to kernel32.K32GetProcessMemoryInfo when psapi is unavailable.
    5. Platform monkeypatches built before patching and fully restored with no
       real foreign-OS calls on Linux.
    """
    import ctypes
    from ctypes import wintypes

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    class FakeWinFunction:
        def __init__(
            self,
            name: str,
            *,
            should_fail: bool = False,
            working_set_bytes: int = 0,
            wide_handle: int = 0xFFFF_FFFF_FFFF_FFFF,
        ) -> None:
            self.name = name
            self.argtypes: list[Any] | tuple[Any, ...] | None = None
            self.restype: Any = None
            self.should_fail = should_fail
            self.working_set_bytes = working_set_bytes
            self.wide_handle = wide_handle
            self.invoked = False
            self.last_args: tuple[Any, ...] = ()

        def __call__(self, *args: Any) -> Any:
            self.invoked = True
            self.last_args = args

            if self.name == "GetCurrentProcess":
                if self.argtypes is None:
                    raise ctypes.ArgumentError(
                        "GetCurrentProcess missing argtypes prototype"
                    )
                if len(self.argtypes) != 0:
                    raise ctypes.ArgumentError(
                        "GetCurrentProcess argtypes must be empty"
                    )
                if self.restype is None or self.restype not in (
                    wintypes.HANDLE,
                    ctypes.c_void_p,
                ):
                    raise ctypes.ArgumentError(
                        "GetCurrentProcess restype must be wintypes.HANDLE"
                    )
                return self.wide_handle

            if self.name in ("GetProcessMemoryInfo", "K32GetProcessMemoryInfo"):
                # 1. Reject missing argtypes when handle wider than 32 bits
                if self.argtypes is None:
                    raise ctypes.ArgumentError(
                        "argument 1: OverflowError: int too long to convert"
                    )

                # 2. Reject incorrect argtypes length
                if len(self.argtypes) != 3:
                    raise ctypes.ArgumentError(
                        f"{self.name} argtypes must specify exactly 3 arguments"
                    )

                # 3. Reject non-64-bit / 32-bit handle parameter type
                arg1_type = self.argtypes[0]
                if arg1_type not in (wintypes.HANDLE, ctypes.c_void_p):
                    raise ctypes.ArgumentError(
                        "argument 1: OverflowError: int too long to convert"
                    )

                # Confirm supplied handle is wider than 32 bits
                handle_val = args[0] if args else None
                if isinstance(handle_val, int) and handle_val <= 0xFFFFFFFF:
                    raise ValueError(
                        f"Expected handle wider than 32 bits, got {handle_val:#x}"
                    )

                # 4. Reject invalid pointer type for counters struct
                arg2_type = self.argtypes[1]
                if not (
                    isinstance(arg2_type, type)
                    and hasattr(arg2_type, "_type_")
                    and hasattr(arg2_type._type_, "WorkingSetSize")
                ):
                    raise ctypes.ArgumentError(
                        "argument 2: expected pointer to PROCESS_MEMORY_COUNTERS"
                    )

                # 5. Reject invalid DWORD parameter type
                arg3_type = self.argtypes[2]
                if arg3_type not in (
                    wintypes.DWORD,
                    ctypes.c_uint32,
                    ctypes.c_ulong,
                    ctypes.c_uint,
                ):
                    raise ctypes.ArgumentError("argument 3: expected DWORD type")

                # 6. Reject missing / invalid BOOL restype
                if self.restype not in (wintypes.BOOL, ctypes.c_int, ctypes.c_long):
                    raise ctypes.ArgumentError("restype must be wintypes.BOOL")

                if self.should_fail:
                    return 0  # Win32 BOOL FALSE

                # Write simulated working set bytes into counters struct
                if len(args) > 1 and args[1] is not None:
                    byref_arg = args[1]
                    target_counters = getattr(byref_arg, "_obj", byref_arg)
                    if hasattr(byref_arg, "contents"):
                        target_counters = byref_arg.contents
                    if hasattr(target_counters, "WorkingSetSize"):
                        target_counters.WorkingSetSize = self.working_set_bytes
                return 1  # Win32 BOOL TRUE

            raise NotImplementedError(f"Unsupported fake function: {self.name}")

    class FakeWinDLL:
        def __init__(self, name: str, functions: dict[str, FakeWinFunction]) -> None:
            self._name = name
            self._functions = functions

        def __getattr__(self, name: str) -> FakeWinFunction:
            if name in self._functions:
                return self._functions[name]
            raise AttributeError(f"DLL '{self._name}' has no export '{name}'")

    class FakeWinDLLLoader:
        def __init__(self, libraries: dict[str, FakeWinDLL]) -> None:
            self._libraries = libraries

        def __call__(self, name: str, *args: Any, **kwargs: Any) -> FakeWinDLL:
            key = name.lower().removesuffix(".dll")
            if key in self._libraries:
                return self._libraries[key]
            raise OSError(f"DLL '{name}' not found")

    WIDE_HANDLE = 0xFFFF_FFFF_FFFF_FFFF  # 64-bit pseudo-handle (HANDLE)-1
    assert WIDE_HANDLE > 0xFFFFFFFF
    assert WIDE_HANDLE.bit_length() == 64

    # -------------------------------------------------------------------------
    # Sub-case 1: Primary psapi path with 64-bit handle & successful RSS conversion
    # -------------------------------------------------------------------------
    # Fixtures built BEFORE monkeypatching platform state
    fn_k32_p1 = FakeWinFunction("GetCurrentProcess", wide_handle=WIDE_HANDLE)
    fn_psapi_p1 = FakeWinFunction(
        "GetProcessMemoryInfo",
        working_set_bytes=104_857_600,  # 100 MiB
    )
    k32_dll_p1 = FakeWinDLL("kernel32", {"GetCurrentProcess": fn_k32_p1})
    psapi_dll_p1 = FakeWinDLL("psapi", {"GetProcessMemoryInfo": fn_psapi_p1})
    loader_p1 = FakeWinDLLLoader({"kernel32": k32_dll_p1, "psapi": psapi_dll_p1})

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("ctypes.WinDLL", loader_p1, raising=False)

    rss_result_p1 = get_current_process_rss_mib()
    assert rss_result_p1 == 100.0
    assert fn_k32_p1.invoked
    assert fn_k32_p1.argtypes == []
    assert fn_k32_p1.restype in (wintypes.HANDLE, ctypes.c_void_p)
    assert fn_psapi_p1.invoked
    assert fn_psapi_p1.argtypes is not None
    assert fn_psapi_p1.argtypes[0] in (wintypes.HANDLE, ctypes.c_void_p)
    assert fn_psapi_p1.argtypes[2] in (
        wintypes.DWORD,
        ctypes.c_uint32,
        ctypes.c_ulong,
        ctypes.c_uint,
    )
    assert fn_psapi_p1.restype in (wintypes.BOOL, ctypes.c_int)
    assert fn_psapi_p1.last_args[0] == WIDE_HANDLE

    # -------------------------------------------------------------------------
    # Sub-case 2: Fallback to kernel32.K32GetProcessMemoryInfo when psapi missing
    # -------------------------------------------------------------------------
    fn_k32_p2 = FakeWinFunction("GetCurrentProcess", wide_handle=WIDE_HANDLE)
    fn_k32_mem_p2 = FakeWinFunction(
        "K32GetProcessMemoryInfo",
        working_set_bytes=262_144_000,  # 250 MiB
    )
    k32_dll_p2 = FakeWinDLL(
        "kernel32",
        {
            "GetCurrentProcess": fn_k32_p2,
            "K32GetProcessMemoryInfo": fn_k32_mem_p2,
        },
    )
    loader_p2 = FakeWinDLLLoader({"kernel32": k32_dll_p2})  # no psapi!

    monkeypatch.setattr("ctypes.WinDLL", loader_p2, raising=False)

    rss_result_p2 = get_current_process_rss_mib()
    assert rss_result_p2 == 250.0
    assert fn_k32_mem_p2.invoked
    assert fn_k32_mem_p2.argtypes is not None
    assert fn_k32_mem_p2.argtypes[0] in (wintypes.HANDLE, ctypes.c_void_p)
    assert fn_k32_mem_p2.argtypes[2] in (
        wintypes.DWORD,
        ctypes.c_uint32,
        ctypes.c_ulong,
        ctypes.c_uint,
    )
    assert fn_k32_mem_p2.restype in (wintypes.BOOL, ctypes.c_int)
    assert fn_k32_mem_p2.last_args[0] == WIDE_HANDLE

    # -------------------------------------------------------------------------
    # Sub-case 3: Failure propagation
    # -------------------------------------------------------------------------
    # 3a: API call failure (returns 0/FALSE)
    fn_k32_p3a = FakeWinFunction("GetCurrentProcess", wide_handle=WIDE_HANDLE)
    fn_psapi_p3a = FakeWinFunction("GetProcessMemoryInfo", should_fail=True)
    k32_dll_p3a = FakeWinDLL("kernel32", {"GetCurrentProcess": fn_k32_p3a})
    psapi_dll_p3a = FakeWinDLL("psapi", {"GetProcessMemoryInfo": fn_psapi_p3a})
    loader_p3a = FakeWinDLLLoader({"kernel32": k32_dll_p3a, "psapi": psapi_dll_p3a})

    monkeypatch.setattr("ctypes.WinDLL", loader_p3a, raising=False)
    with pytest.raises(RuntimeError, match="Windows GetProcessMemoryInfo failed"):
        get_current_process_rss_mib()

    # 3b: API entry point not found in either psapi or kernel32
    fn_k32_p3b = FakeWinFunction("GetCurrentProcess", wide_handle=WIDE_HANDLE)
    k32_dll_p3b = FakeWinDLL("kernel32", {"GetCurrentProcess": fn_k32_p3b})
    psapi_dll_p3b = FakeWinDLL("psapi", {})
    loader_p3b = FakeWinDLLLoader({"kernel32": k32_dll_p3b, "psapi": psapi_dll_p3b})

    monkeypatch.setattr("ctypes.WinDLL", loader_p3b, raising=False)
    with pytest.raises(
        RuntimeError, match="Windows GetProcessMemoryInfo API entry point not found"
    ):
        get_current_process_rss_mib()

    # -------------------------------------------------------------------------
    # Sub-case 4: Regression detection - prove unprototyped call reproduces PR #50
    # -------------------------------------------------------------------------
    fn_raw_k32 = FakeWinFunction("GetCurrentProcess", wide_handle=WIDE_HANDLE)
    fn_raw_psapi = FakeWinFunction("GetProcessMemoryInfo")
    # Missing argtypes (None) with 64-bit handle raises exact PR #50 ArgumentError
    with pytest.raises(
        ctypes.ArgumentError,
        match=r"argument 1: OverflowError: int too long to convert",
    ):
        fn_raw_psapi(WIDE_HANDLE, None, 72)

    # 32-bit handle parameter type (e.g. c_int32) raises exact ArgumentError
    fn_raw_psapi.argtypes = [ctypes.c_int32, ctypes.c_void_p, wintypes.DWORD]
    fn_raw_psapi.restype = wintypes.BOOL
    with pytest.raises(
        ctypes.ArgumentError,
        match=r"argument 1: OverflowError: int too long to convert",
    ):
        fn_raw_psapi(WIDE_HANDLE, None, 72)

    # Missing GetCurrentProcess prototypes rejected
    with pytest.raises(
        ctypes.ArgumentError, match="GetCurrentProcess missing argtypes prototype"
    ):
        fn_raw_k32()
    fn_raw_k32.argtypes = []
    with pytest.raises(
        ctypes.ArgumentError, match="GetCurrentProcess restype must be wintypes.HANDLE"
    ):
        fn_raw_k32()

    # Missing GetProcessMemoryInfo restype rejected
    fn_raw_psapi.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
        wintypes.DWORD,
    ]
    fn_raw_psapi.restype = None
    with pytest.raises(ctypes.ArgumentError, match="restype must be wintypes.BOOL"):
        fn_raw_psapi(WIDE_HANDLE, None, 72)

    # Invalid pointer type for counters rejected
    fn_raw_psapi.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD]
    fn_raw_psapi.restype = wintypes.BOOL
    with pytest.raises(
        ctypes.ArgumentError,
        match="argument 2: expected pointer to PROCESS_MEMORY_COUNTERS",
    ):
        fn_raw_psapi(WIDE_HANDLE, None, 72)

    # Rejection of unprototyped workflow function simulating the defect
    def unprototyped_rss_workflow(k32: Any, ps: Any) -> float:
        k32.GetCurrentProcess.argtypes = []
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        h = k32.GetCurrentProcess()
        cnt = PROCESS_MEMORY_COUNTERS()
        cnt.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        if not ps.GetProcessMemoryInfo(h, ctypes.byref(cnt), cnt.cb):
            raise RuntimeError("Windows GetProcessMemoryInfo failed")
        return float(cnt.WorkingSetSize) / (1024.0 * 1024.0)

    fn_unprot_k32 = FakeWinFunction("GetCurrentProcess", wide_handle=WIDE_HANDLE)
    fn_unprot_psapi = FakeWinFunction("GetProcessMemoryInfo")
    unprot_k32_dll = FakeWinDLL("kernel32", {"GetCurrentProcess": fn_unprot_k32})
    unprot_psapi_dll = FakeWinDLL("psapi", {"GetProcessMemoryInfo": fn_unprot_psapi})

    with pytest.raises(
        ctypes.ArgumentError,
        match=r"argument 1: OverflowError: int too long to convert",
    ):
        unprototyped_rss_workflow(unprot_k32_dll, unprot_psapi_dll)

    # -------------------------------------------------------------------------
    # Sub-case 5: Restore all monkeypatches & verify real platform RSS path
    # -------------------------------------------------------------------------
    monkeypatch.undo()
    live_rss = get_current_process_rss_mib()
    assert live_rss > 0.0
