"""Native OS observer integration tests for source-watch-runtime.v1.

Covers acceptance criteria WR-15 through WR-17:
- WR-15: Native startup read of preexisting workbook, in-place Save, replace
- WR-16: Native absent source, delete/recreate, move into/away, thread cleanup
- WR-17: End-to-end integration with independent WP-04 Planner oracle
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from accounting_contracts import (
    IdentityLifecycle,
    PlanAction,
    PriorIdentityState,
    plan_source_changes,
)
from accounting_local_agent import (
    SourceWatchRuntime,
    SourceWatchRuntimeState,
    XlsxSourceReadResult,
)
from test_xlsx_source_reader import (
    SyntheticXlsxBuilder,
    _make_uuid7,
    _sample_business_parties_row_data,
    _sample_buy_sell_row_data,
    _sample_inventory_movements_row_data,
    _sample_receipts_payments_row_data,
)


def _build_synthetic_workbook(
    u1_seed: bytes = b"u1_gen1",
    amount: str = "1000",
    *,
    row_order: list[int] | None = None,
) -> bytes:
    """Helper building a synthetic 4-sheet workbook with custom seed and values."""
    builder = SyntheticXlsxBuilder()
    u1 = _make_uuid7(u1_seed)
    row_buy_sell = _sample_buy_sell_row_data(u1, 2)
    row_buy_sell["G"] = amount
    builder.add_sheet_rows("خرید-فروش", [row_buy_sell])

    u2 = _make_uuid7(b"sheet2_row2_uuid")
    builder.add_sheet_rows("دریافت-پرداخت", [_sample_receipts_payments_row_data(u2, 2)])

    u3 = _make_uuid7(b"sheet3_row2_uuid")
    builder.add_sheet_rows("ورود-خروج", [_sample_inventory_movements_row_data(u3, 2)])

    u4 = _make_uuid7(b"sheet4_row2_uuid")
    builder.add_sheet_rows("لیست کسبه", [_sample_business_parties_row_data(u4, 2)])
    return builder.build_bytes()


def _wait_for_snapshot(
    results: list[XlsxSourceReadResult],
    lock: threading.Lock,
    event: threading.Event,
    predicate: Callable[[XlsxSourceReadResult], bool],
    timeout: float = 10.0,
    *,
    cursor: int = 0,
) -> XlsxSourceReadResult:
    """Poll for a snapshot meeting content predicate within bounded deadline.

    Only examines results delivered strictly at or after the provided cursor index,
    guaranteeing that stale results cannot satisfy fresh generation expectations.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with lock:
            if len(results) > cursor:
                for r in reversed(results[cursor:]):
                    if predicate(r):
                        return r
        rem = max(0.01, deadline - time.monotonic())
        event.wait(timeout=min(0.2, rem))
        event.clear()
    with lock:
        if len(results) > cursor:
            for r in reversed(results[cursor:]):
                if predicate(r):
                    return r
    raise TimeoutError(
        f"Timed out waiting for expected snapshot content after cursor {cursor}"
    )


# ---------------------------------------------------------------------------
# WR-15: Native Startup Read, In-Place Save, and Atomic Replacement
# ---------------------------------------------------------------------------


def test_wr15_native_startup_read_and_inplace_and_atomic_save(
    tmp_path: Path,
) -> None:
    """WR-15: Native Observer handles startup read, in-place edit, replace."""
    src_dir = tmp_path / "watched_dir"
    src_dir.mkdir()
    src = src_dir / "target.xlsx"
    src.write_bytes(_build_synthetic_workbook(b"u1_seed_g1", "1000"))

    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    results: list[XlsxSourceReadResult] = []
    result_event = threading.Event()
    lock = threading.Lock()

    def consumer(res: XlsxSourceReadResult) -> None:
        with lock:
            results.append(res)
            result_event.set()

    runtime = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.01,
    )

    runner_err: BaseException | None = None

    def runner() -> None:
        nonlocal runner_err
        try:
            runtime.run(consumer)
        except BaseException as e:
            runner_err = e

    with lock:
        cursor1 = len(results)

    run_thread = threading.Thread(target=runner)
    run_thread.start()

    try:
        # 1. Generation 1: Preexisting file read on startup (via initial MODIFIED hint)
        res1 = _wait_for_snapshot(
            results,
            lock,
            result_event,
            lambda r: (
                r.snapshot.sheets["خرید-فروش"]
                .rows[0]
                .raw_values.get("unit_price_toman_raw")
                == "1000"
            ),
            cursor=cursor1,
            timeout=10.0,
        )
        assert (
            res1.snapshot.sheets["خرید-فروش"].rows[0].raw_values["unit_price_toman_raw"]
            == "1000"
        )

        # 2. Generation 2: In-place Save (direct file overwrite)
        time.sleep(0.5)
        with lock:
            cursor2 = len(results)
        src.write_bytes(_build_synthetic_workbook(b"u1_seed_g1", "2000"))
        res2 = _wait_for_snapshot(
            results,
            lock,
            result_event,
            lambda r: (
                r.snapshot.sheets["خرید-فروش"]
                .rows[0]
                .raw_values.get("unit_price_toman_raw")
                == "2000"
            ),
            cursor=cursor2,
            timeout=10.0,
        )
        assert (
            res2.snapshot.sheets["خرید-فروش"].rows[0].raw_values["unit_price_toman_raw"]
            == "2000"
        )

        # 3. Generation 3: Atomic file replacement (write temp file and move to target)
        time.sleep(0.5)
        with lock:
            cursor3 = len(results)
        temp_file = src_dir / "temp_atomic.part"
        temp_file.write_bytes(_build_synthetic_workbook(b"u1_seed_g1", "3000"))
        os.replace(str(temp_file), str(src))

        res3 = _wait_for_snapshot(
            results,
            lock,
            result_event,
            lambda r: (
                r.snapshot.sheets["خرید-فروش"]
                .rows[0]
                .raw_values.get("unit_price_toman_raw")
                == "3000"
            ),
            cursor=cursor3,
            timeout=10.0,
        )
        assert (
            res3.snapshot.sheets["خرید-فروش"].rows[0].raw_values["unit_price_toman_raw"]
            == "3000"
        )

        # 4. Snapshot storage clean: active lease dirs cleaned up
        quarantine_dirs = [
            d for d in snap_root.iterdir() if d.is_dir() and d.name.startswith(".qdir-")
        ]
        assert len(quarantine_dirs) == 0

        runtime.request_stop()
        run_thread.join(timeout=5.0)
        assert not run_thread.is_alive()
        assert runner_err is None
        assert runtime.view().state == SourceWatchRuntimeState.STOPPED
    finally:
        runtime.request_stop()
        run_thread.join(timeout=5.0)
        assert not run_thread.is_alive()


# ---------------------------------------------------------------------------
# WR-16: Native Absent Source, Lifecycle, and Thread Cleanup
# ---------------------------------------------------------------------------


def test_wr16_native_absent_source_lifecycle_and_thread_cleanup(
    tmp_path: Path,
) -> None:
    """WR-16: Absent source at start, delete/recreate, move into/away, clean stop."""
    src_dir = tmp_path / "watched_dir"
    src_dir.mkdir()
    src = src_dir / "target.xlsx"
    # File is absent initially

    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    results: list[XlsxSourceReadResult] = []
    result_event = threading.Event()
    lock = threading.Lock()

    def consumer(res: XlsxSourceReadResult) -> None:
        with lock:
            results.append(res)
            result_event.set()

    runtime = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.01,
    )

    runner_err: BaseException | None = None

    def runner() -> None:
        nonlocal runner_err
        try:
            runtime.run(consumer)
        except BaseException as e:
            runner_err = e

    run_thread = threading.Thread(target=runner)
    run_thread.start()

    try:
        # Initial wait: file is missing -> no results delivered
        time.sleep(0.5)
        with lock:
            assert len(results) == 0

        # 1. Create file now -> delivers generation 1
        with lock:
            cursor1 = len(results)
        src.write_bytes(_build_synthetic_workbook(b"u1_seed_g1", "5000"))
        res1 = _wait_for_snapshot(
            results,
            lock,
            result_event,
            lambda r: (
                r.snapshot.sheets["خرید-فروش"]
                .rows[0]
                .raw_values.get("unit_price_toman_raw")
                == "5000"
            ),
            cursor=cursor1,
            timeout=10.0,
        )
        assert (
            res1.snapshot.sheets["خرید-فروش"].rows[0].raw_values["unit_price_toman_raw"]
            == "5000"
        )

        # 2. Delete and recreate -> delivers generation 2
        time.sleep(0.5)
        with lock:
            cursor2 = len(results)
        src.unlink()
        time.sleep(0.2)
        src.write_bytes(_build_synthetic_workbook(b"u1_seed_g1", "6000"))
        res2 = _wait_for_snapshot(
            results,
            lock,
            result_event,
            lambda r: (
                r.snapshot.sheets["خرید-فروش"]
                .rows[0]
                .raw_values.get("unit_price_toman_raw")
                == "6000"
            ),
            cursor=cursor2,
            timeout=10.0,
        )
        assert (
            res2.snapshot.sheets["خرید-فروش"].rows[0].raw_values["unit_price_toman_raw"]
            == "6000"
        )

        # 3. Move away and move into target -> delivers generation 3
        time.sleep(0.5)
        with lock:
            cursor3 = len(results)
        os.replace(str(src), str(src_dir / "archived.xlsx"))
        time.sleep(0.2)
        new_source = src_dir / "new_generation.xlsx"
        new_source.write_bytes(_build_synthetic_workbook(b"u1_seed_g1", "7000"))
        os.replace(str(new_source), str(src))

        res3 = _wait_for_snapshot(
            results,
            lock,
            result_event,
            lambda r: (
                r.snapshot.sheets["خرید-فروش"]
                .rows[0]
                .raw_values.get("unit_price_toman_raw")
                == "7000"
            ),
            cursor=cursor3,
            timeout=10.0,
        )
        assert (
            res3.snapshot.sheets["خرید-فروش"].rows[0].raw_values["unit_price_toman_raw"]
            == "7000"
        )

        # 4. Irrelevant and lock files do not trigger delivery as source
        time.sleep(0.5)
        with lock:
            cursor4 = len(results)
        (src_dir / "unrelated.xlsx").write_bytes(
            _build_synthetic_workbook(b"u1_seed_other", "9999")
        )
        (src_dir / "~$target.xlsx").write_bytes(b"lock_data")
        time.sleep(0.5)
        with lock:
            for r in results[cursor4:]:
                assert not any(
                    row.raw_values.get("unit_price_toman_raw") == "9999"
                    for row in r.snapshot.sheets["خرید-فروش"].rows
                ), "Irrelevant files must never be delivered as source"

        # 5. Stop and verify thread cleanup
        runtime.request_stop()
        run_thread.join(timeout=5.0)
        assert not run_thread.is_alive()
        assert runner_err is None
        assert runtime.view().state == SourceWatchRuntimeState.STOPPED

        # Verify all expected worker threads terminated
        for t in runtime._expected_workers:
            assert not t.is_alive(), f"Thread {t.name} must be stopped"
    finally:
        runtime.request_stop()
        run_thread.join(timeout=5.0)
        assert not run_thread.is_alive()


# ---------------------------------------------------------------------------
# WR-17: End-to-End Composition with Independent WP-04 Planner Oracle
# ---------------------------------------------------------------------------


def test_wr17_end_to_end_wp04_planner_composition(tmp_path: Path) -> None:
    """WR-17: Results fed to WP-04 Planner verify sort, formula-only and raw edits."""
    src_dir = tmp_path / "watched_dir"
    src_dir.mkdir()
    src = src_dir / "target.xlsx"

    u1 = _make_uuid7(b"id_row_1")
    u2 = _make_uuid7(b"id_row_2")

    builder1 = SyntheticXlsxBuilder()
    row1 = _sample_buy_sell_row_data(u1, 2)
    row2 = _sample_buy_sell_row_data(u2, 3)
    row1["G"] = "100"
    row2["G"] = "200"
    builder1.add_sheet_rows("خرید-فروش", [row1, row2])
    builder1.add_sheet_rows(
        "دریافت-پرداخت",
        [_sample_receipts_payments_row_data(_make_uuid7(b"u_rp"), 2)],
    )
    builder1.add_sheet_rows(
        "ورود-خروج",
        [_sample_inventory_movements_row_data(_make_uuid7(b"u_im"), 2)],
    )
    builder1.add_sheet_rows(
        "لیست کسبه",
        [_sample_business_parties_row_data(_make_uuid7(b"u_bp"), 2)],
    )

    src.write_bytes(builder1.build_bytes())

    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()

    results: list[XlsxSourceReadResult] = []
    result_event = threading.Event()
    lock = threading.Lock()

    def consumer(res: XlsxSourceReadResult) -> None:
        with lock:
            results.append(res)
            result_event.set()

    runtime = SourceWatchRuntime(
        src,
        snapshot_root=snap_root,
        observation_interval_seconds=0.01,
    )

    runner_err: BaseException | None = None

    def runner() -> None:
        nonlocal runner_err
        try:
            runtime.run(consumer)
        except BaseException as e:
            runner_err = e

    with lock:
        cursor1 = len(results)

    run_thread = threading.Thread(target=runner)
    run_thread.start()

    try:
        # Generation 1: Initial startup read (u1 on row 2, u2 on row 3)
        res1 = _wait_for_snapshot(
            results,
            lock,
            result_event,
            lambda r: (
                r.locations_by_uuid.get(u1) is not None
                and r.locations_by_uuid[u1].physical_row_number == 2
                and r.locations_by_uuid[u2].physical_row_number == 3
            ),
            cursor=cursor1,
            timeout=10.0,
        )
        snap1 = res1.snapshot

        # 1. Run WP-04 Change Planner from snap1 to establish prior identity states
        plan1 = plan_source_changes(snap1)
        prior_states = [
            PriorIdentityState(
                stable_id=p1_item.stable_id,
                canonical_uuid=p1_item.canonical_uuid,
                home_sheet=p1_item.sheet_name,
                latest_revision=1,
                lifecycle=IdentityLifecycle.ACTIVE,
                source_hash=p1_item.current_source_hash,
            )
            for p1_item in plan1.items
        ]

        # Generation 2: Row sort (physical rows swapped: u2 on row 2, u1 on row 3)
        time.sleep(0.5)
        with lock:
            cursor2 = len(results)
        row2_swapped = dict(row2)
        row2_swapped["__row_num__"] = 2
        row1_swapped = dict(row1)
        row1_swapped["__row_num__"] = 3

        builder2 = SyntheticXlsxBuilder()
        builder2.add_sheet_rows("خرید-فروش", [row2_swapped, row1_swapped])
        builder2.add_sheet_rows(
            "دریافت-پرداخت",
            [_sample_receipts_payments_row_data(_make_uuid7(b"u_rp"), 2)],
        )
        builder2.add_sheet_rows(
            "ورود-خروج",
            [_sample_inventory_movements_row_data(_make_uuid7(b"u_im"), 2)],
        )
        builder2.add_sheet_rows(
            "لیست کسبه",
            [_sample_business_parties_row_data(_make_uuid7(b"u_bp"), 2)],
        )
        src.write_bytes(builder2.build_bytes())

        res2 = _wait_for_snapshot(
            results,
            lock,
            result_event,
            lambda r: (
                r.locations_by_uuid.get(u1) is not None
                and r.locations_by_uuid[u1].physical_row_number == 3
                and r.locations_by_uuid[u2].physical_row_number == 2
            ),
            cursor=cursor2,
            timeout=10.0,
        )
        snap2 = res2.snapshot
        assert res2 is not res1
        assert snap2 is not snap1

        # 2. Plan changes from snap2 (row reordering) against snap1
        # prior state -> 0 changes
        plan_same = plan_source_changes(snap2, prior_states)
        assert plan_same.total_counts.unchanged_count == 5
        assert plan_same.total_counts.insert_count == 0
        assert plan_same.total_counts.edit_count == 0
        assert plan_same.total_counts.void_count == 0
        for p_item in plan_same.items:
            assert p_item.action == PlanAction.UNCHANGED

        # Generation 3: Formula and cache-only XML modification (identical raw values)
        # Rows restored: row 2 (u1) and row 3 (u2) with formula row at 10
        time.sleep(0.5)
        with lock:
            cursor3 = len(results)
        formula_row = {
            "__row_num__": 10,
            "F": {"f": "SUM(F2:F3)", "v": "300"},
        }
        builder3 = SyntheticXlsxBuilder()
        builder3.add_sheet_rows("خرید-فروش", [row1, row2, formula_row])
        builder3.add_sheet_rows(
            "دریافت-پرداخت",
            [_sample_receipts_payments_row_data(_make_uuid7(b"u_rp"), 2)],
        )
        builder3.add_sheet_rows(
            "ورود-خروج",
            [_sample_inventory_movements_row_data(_make_uuid7(b"u_im"), 2)],
        )
        builder3.add_sheet_rows(
            "لیست کسبه",
            [_sample_business_parties_row_data(_make_uuid7(b"u_bp"), 2)],
        )
        src.write_bytes(builder3.build_bytes())

        # Prove fresh Generation 3 delivery with bounded deadline and dynamic cursor
        res3 = _wait_for_snapshot(
            results,
            lock,
            result_event,
            lambda r: (
                r.locations_by_uuid.get(u1) is not None
                and r.locations_by_uuid[u1].physical_row_number == 2
                and r.locations_by_uuid[u2].physical_row_number == 3
            ),
            cursor=cursor3,
            timeout=10.0,
        )
        snap3 = res3.snapshot
        assert res3 is not res2
        assert snap3 is not snap2
        assert snap3 is not snap1

        # 3. Plan changes from snap3 (formula-only modification) against snap1
        # prior state: must be asserted BEFORE writing Generation 4!
        plan_formula = plan_source_changes(snap3, prior_states)
        assert plan_formula.total_counts.unchanged_count == 5
        assert plan_formula.total_counts.insert_count == 0
        assert plan_formula.total_counts.edit_count == 0
        assert plan_formula.total_counts.void_count == 0
        for p_item in plan_formula.items:
            assert p_item.action == PlanAction.UNCHANGED

        # Generation 4: Raw edit (modifying amount on row1 from 100 to 999)
        # Written only AFTER Generation 3 formula delivery is fully proven
        time.sleep(0.5)
        with lock:
            cursor4 = len(results)
        row1_edited = dict(row1)
        row1_edited["G"] = "999"
        builder4 = SyntheticXlsxBuilder()
        builder4.add_sheet_rows("خرید-فروش", [row1_edited, row2])
        builder4.add_sheet_rows(
            "دریافت-پرداخت",
            [_sample_receipts_payments_row_data(_make_uuid7(b"u_rp"), 2)],
        )
        builder4.add_sheet_rows(
            "ورود-خروج",
            [_sample_inventory_movements_row_data(_make_uuid7(b"u_im"), 2)],
        )
        builder4.add_sheet_rows(
            "لیست کسبه",
            [_sample_business_parties_row_data(_make_uuid7(b"u_bp"), 2)],
        )
        src.write_bytes(builder4.build_bytes())

        res4 = _wait_for_snapshot(
            results,
            lock,
            result_event,
            lambda r: any(
                row.raw_values.get("unit_price_toman_raw") == "999"
                for row in r.snapshot.sheets["خرید-فروش"].rows
            ),
            cursor=cursor4,
            timeout=10.0,
        )
        snap4 = res4.snapshot
        assert res4 is not res3
        assert snap4 is not snap3

        # 4. Plan changes from snap4 (raw edit on row 1) against snap1
        # prior state -> 1 EDIT, 4 UNCHANGED
        plan_edit = plan_source_changes(snap4, prior_states)
        assert plan_edit.total_counts.unchanged_count == 4
        assert plan_edit.total_counts.edit_count == 1
        assert plan_edit.total_counts.insert_count == 0
        assert plan_edit.total_counts.void_count == 0

        edited_items = [p for p in plan_edit.items if p.action == PlanAction.EDIT]
        assert len(edited_items) == 1
        assert str(edited_items[0].canonical_uuid) == str(u1)
        assert edited_items[0].current_source_hash != edited_items[0].prior_source_hash

        runtime.request_stop()
        run_thread.join(timeout=5.0)
        assert not run_thread.is_alive()
        assert runner_err is None
    finally:
        runtime.request_stop()
        run_thread.join(timeout=5.0)
        assert not run_thread.is_alive()
