"""RC-13 complete replay; retain one caller payload, never a workbook byte cache."""

import json
import time
from decimal import Decimal

from accounting_contracts import (
    decode_source_raw_row,
    encode_source_raw_row,
    plan_source_changes,
)
from source_identity_projection_support import (
    expected_plan,
    plan_view,
    prior_from_snapshot,
    view,
)
from source_raw_codec_support import SHEETS, make_row, row_view, snapshot, uid
from test_xlsx_source_reader import _CallWindowRssSampler


def main() -> None:
    started = time.perf_counter()
    rows = [make_row((n - 1) % 4, number=n) for n in range(1, 15001)]
    # Vary representation independently of the scalar tag implementation.
    for n in range(1, 15001, 4):
        rows[n - 1] = make_row(
            0,
            {
                "quantity_raw": Decimal((n % 2, (1, 2, 0, 0), -3)),
                "notes_raw": f"SYNTHETIC-{n}",
            },
            n,
        )
    original = snapshot(rows)
    del rows
    prior = prior_from_snapshot(original, 7)
    fixture_seconds = time.perf_counter() - started
    sampler = _CallWindowRssSampler()
    sampler.start()
    decoded = []
    encode_seconds = decode_seconds = 0.0
    byte_total = in_flight = max_in_flight = rows_checked = 0
    try:
        for row in original.all_rows_by_id.values():
            started = time.perf_counter()
            payload = encode_source_raw_row(row)
            encode_seconds += time.perf_counter() - started
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            byte_total += len(payload)
            started = time.perf_counter()
            restored = decode_source_raw_row(payload)
            decode_seconds += time.perf_counter() - started
            assert row_view(restored) == row_view(row)
            rows_checked += 1
            decoded.append(restored)
            del payload
            in_flight -= 1
        started = time.perf_counter()
        reconstructed = snapshot(decoded)
        snapshot_seconds = time.perf_counter() - started
        assert set(reconstructed.all_rows_by_id) == {uid(n) for n in range(1, 15001)}
        assert [row_view(row) for row in reconstructed.all_rows_by_id.values()] == [
            row_view(row) for row in original.all_rows_by_id.values()
        ]
        assert all(
            reconstructed.sheets[s].sheet_snapshot_hash
            == original.sheets[s].sheet_snapshot_hash
            for s in SHEETS
        )
        started = time.perf_counter()
        plan = plan_source_changes(reconstructed, prior)
        planner_seconds = time.perf_counter() - started
        assert plan_view(plan) == expected_plan(reconstructed, view(prior))
        assert [
            (i.stable_id, i.action.value, i.prior_revision, i.planned_revision)
            for i in plan.items
        ] == [
            (uid(n), "unchanged", 7, None)
            for sheet in range(4)
            for n in range(sheet + 1, 15001, 4)
        ]
    finally:
        _, peak = sampler.stop_and_get_peak()
    assert in_flight == 0
    print(
        json.dumps(
            {
                "rows_checked": rows_checked,
                "planner_items_checked": len(plan.items),
                "max_caller_payloads_retained": max_in_flight,
                "encoded_bytes": byte_total,
                "fixture_seconds": fixture_seconds,
                "encode_seconds": encode_seconds,
                "decode_seconds": decode_seconds,
                "snapshot_seconds": snapshot_seconds,
                "planner_seconds": planner_seconds,
                "peak_rss_mib": peak,
            }
        )
    )


if __name__ == "__main__":
    main()
