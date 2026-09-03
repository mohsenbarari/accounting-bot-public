"""Isolated call-window measurement; the caller creates the synthetic fixture."""

import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

from accounting_local_agent import read_identified_xlsx_source
from test_xlsx_source_reader import _CallWindowRssSampler


def main() -> None:
    root = Path(sys.argv[1])
    source = root / "benchmark.xlsx"
    before = source.read_bytes()
    expected_hash = hashlib.sha256(before).hexdigest()
    expected_size = len(before)
    del before
    leases = root / "leases"
    leases.mkdir()
    sampler = _CallWindowRssSampler()
    sampler.start()
    started = time.perf_counter()
    try:
        result = read_identified_xlsx_source(
            source, snapshot_root=leases, observation_interval_seconds=0.001
        )
        duration = time.perf_counter() - started
    finally:
        _, peak = sampler.stop_and_get_peak()
    assert result.key.source_id == uuid.UUID("00000000-0000-7000-8000-0000000003e7")
    assert result.key.fiscal_year == 1405
    assert result.file_sha256 == expected_hash and result.byte_count == expected_size
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected_hash
    assert list(leases.iterdir()) == []
    expected = {
        uuid.UUID(int=(7 << 76) | (2 << 62) | (1 + sheet * 100_000 + row))
        for sheet in range(4)
        for row in range(3750)
    }
    snapshot = result.read_result.snapshot
    assert set(snapshot.all_rows_by_id) == expected
    assert snapshot.total_row_count == 15000
    for row in snapshot.all_rows_by_id.values():
        if row.sheet_name == "خرید-فروش":
            assert row.raw_values["unit_price_toman_raw"] == "1500000"
        elif row.sheet_name == "دریافت-پرداخت":
            assert row.raw_values["amount_toman_raw"] == "50000000"
        elif row.sheet_name == "ورود-خروج":
            assert row.raw_values["quantity_raw"] == "100.5"
        else:
            assert row.raw_values["party_name_raw"] == "فروشگاه نمونه"
    assert duration < 15.0 and peak < 128.0
    print(
        json.dumps(
            {
                "rows": snapshot.total_row_count,
                "seconds": duration,
                "peak_rss_mib": peak,
                "fixture_seconds": float(sys.argv[2]),
            }
        )
    )


if __name__ == "__main__":
    main()
