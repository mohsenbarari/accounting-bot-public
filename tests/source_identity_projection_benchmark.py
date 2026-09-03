"""IP-14 isolated measurement with a complete independently declared scope oracle."""

import json
import time

from accounting_contracts import (
    SourceBindingRegistry,
    SourceIdentityCatalog,
    plan_source_changes,
    project_source_prior,
)
from source_identity_projection_support import (
    SHEETS,
    Model,
    expected_plan,
    plan_view,
    prior_from_snapshot,
    record,
    snapshot,
    state,
    uid,
    view,
)
from test_xlsx_source_reader import _CallWindowRssSampler


def main() -> None:
    started = time.perf_counter()
    specs = [((n - 1) % 4, n, "base") for n in range(1, 15001)]
    complete = snapshot(specs)
    prior = prior_from_snapshot(complete)
    archive_entries = [
        entry
        for n in range(1, 15001)
        if n > 10000 or (n - 1) % 4 == 3
        for entry in [prior.identities[uid(n)]]
    ]
    old_entries = [
        state(3, n, 3, digest=prior.identities[uid(n)].source_hash or "a" * 64)
        for n in range(4, 15001, 4)
    ]
    archives = [record(1403, old_entries), record(1404, archive_entries)]
    active = record(
        1405, [prior.identities[uid(n)] for n in range(1, 10001)], active=True
    )
    current = snapshot([row for row in specs if row[1] <= 10000 or row[0] == 3])
    expected: Model = {
        uid(n): (
            SHEETS[(n - 1) % 4],
            7,
            "active",
            complete.all_rows_by_id[uid(n)].source_hash,
        )
        for n in range(1, 15001)
        if n <= 10000 or (n - 1) % 4 == 3
    }
    registry = SourceBindingRegistry([active, *reversed(archives)])
    fixture_seconds = time.perf_counter() - started
    sampler = _CallWindowRssSampler()
    sampler.start()
    try:
        started = time.perf_counter()
        catalog = SourceIdentityCatalog(registry)
        catalog_seconds = time.perf_counter() - started
        started = time.perf_counter()
        projected = project_source_prior(active.key, current, catalog)
        projection_seconds = time.perf_counter() - started
        started = time.perf_counter()
        plan = plan_source_changes(current, projected)
        planner_seconds = time.perf_counter() - started
    finally:
        _, peak = sampler.stop_and_get_peak()
    assert catalog.identity_count == 15000
    assert view(projected) == expected and len(expected) == 11250
    assert plan_view(plan) == expected_plan(current, expected)
    assert all(
        item.action.value == "unchanged" and item.prior_revision == 7
        for item in plan.items
    )
    assert all(
        item.latest_revision == 3
        for item in archives[0].prior_registry.identities.values()
    )
    print(
        json.dumps(
            {
                "global_identities": 15000,
                "projected_identities": len(projected.identities),
                "archive_memberships": sum(
                    len(a.prior_registry.identities) for a in archives
                ),
                "planner_items_checked": len(plan.items),
                "fixture_seconds": fixture_seconds,
                "catalog_seconds": catalog_seconds,
                "projection_seconds": projection_seconds,
                "planner_seconds": planner_seconds,
                "peak_rss_mib": peak,
            }
        )
    )


if __name__ == "__main__":
    main()
