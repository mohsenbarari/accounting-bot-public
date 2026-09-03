"""Independent IP-11/12/14 composition, generated histories and scale evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from accounting_contracts import (
    PriorIdentityRegistry,
    SourceBindingRegistry,
    SourceIdentityCatalog,
    SourceIdentityProjectionError,
    SourceIdentityProjectionReason,
    evaluate_source_fiscal_evidence,
    evaluate_source_requiredness,
    plan_source_changes,
    project_source_prior,
    resolve_source_binding,
)
from accounting_local_agent import read_identified_xlsx_source
from hypothesis import given, settings
from hypothesis import strategies as st
from source_identity_projection_support import (
    SHEETS,
    Model,
    RowSpec,
    committed_model,
    expected_plan,
    from_model,
    plan_view,
    prior_from_snapshot,
    record,
    snapshot,
    state,
    uid,
    view,
)
from xlsx_source_identity_fixtures import identified_parts, raw_parts, zipped


def test_ip11_identified_xlsx_multiyear_composition(tmp_path: Path) -> None:
    source, leases = tmp_path / "SYNTHETIC.xlsx", tmp_path / "leases"
    leases.mkdir()
    old_key, current_key = record(1404).key, record(1405).key

    def acquire(
        year: int, raw: dict[str, bytes], source_number: int | None = None
    ) -> Any:
        identity = (
            uid(1_000_000 + year) if source_number is None else uid(source_number)
        )
        marker = f"xlsx-source-identity.v1|{identity}|{year:04d}"
        content = zipped(identified_parts(value=marker, raw=raw))
        source.write_bytes(content)
        try:
            return read_identified_xlsx_source(
                source, snapshot_root=leases, observation_interval_seconds=0.001
            )
        finally:
            assert source.read_bytes() == content and list(leases.iterdir()) == []

    old = acquire(1404, raw_parts(seed=1))
    assert old.key == old_key
    archive = record(1404, prior=prior_from_snapshot(old.read_result.snapshot, 9))
    archive_before = view(archive.prior_registry)
    active = record(1405, active=True)
    current_model: Model = {}
    for generation in range(3):
        raw = raw_parts(
            seed=1001,
            mixed=True,
            reorder=generation > 0,
            formula=generation,
            edit=generation == 2,
        )
        sheet4 = "xl/worksheets/sheet4.xml"
        before, after = str(uid(301001)).encode(), str(uid(300001)).encode()
        assert raw[sheet4].count(before) == 1
        raw[sheet4] = raw[sheet4].replace(before, after)
        identified = acquire(1405, raw)
        assert identified.key == current_key
        current = identified.read_result.snapshot
        assert set(current.all_rows_by_id) == {
            uid(n) for n in (1001, 1002, 101001, 201001, 300001)
        }
        assert evaluate_source_requiredness(current).passes_requiredness
        # Existing raw factories declare 1403; mixed=True sets inventory to 1404.
        # Neither observed year is the marker's declared source year 1405.
        assert evaluate_source_fiscal_evidence(current).observed_years == (1403, 1404)
        registry = SourceBindingRegistry([active, archive])
        assert (
            resolve_source_binding(identified.key, registry).prior_registry
            is active.prior_registry
        )
        expected = dict(current_model)
        if generation == 0:
            expected[uid(300001)] = archive_before[uid(300001)]
        projected = project_source_prior(
            identified.key, current, SourceIdentityCatalog(registry)
        )
        assert view(projected) == expected
        actual = plan_source_changes(current, projected)
        assert plan_view(actual) == expected_plan(current, expected)
        actions = [
            (item.stable_id, item.action.value, item.planned_revision)
            for item in actual.items
        ]
        if generation == 0:
            assert actions == [
                (uid(n), "insert", 1) for n in (1001, 1002, 101001, 201001)
            ] + [(uid(300001), "unchanged", None)]
        elif generation == 1:
            assert all(action == "unchanged" for _, action, _ in actions)
        else:
            assert actions[0] == (uid(1001), "edit", 2)
            assert all(action == "unchanged" for _, action, _ in actions[1:])
        current_model = committed_model(current, expected)
        active = record(1405, active=True, prior=from_model(current_model))
        assert view(archive.prior_registry) == archive_before
    # These identified packages fail before the explicit pipeline can call Planner.
    for year, number in [(1404, None), (1405, 999999)]:
        identified = acquire(year, raw_parts(), number)
        catalog = SourceIdentityCatalog(SourceBindingRegistry([archive, active]))
        reached_planner = False
        with pytest.raises(SourceIdentityProjectionError) as caught:
            projected = project_source_prior(
                identified.key, identified.read_result.snapshot, catalog
            )
            reached_planner = True
            plan_source_changes(identified.read_result.snapshot, projected)
        assert caught.value.reason is SourceIdentityProjectionReason.SOURCE_NOT_ACTIVE
        assert not reached_planner


HISTORY_ROWS = st.lists(
    st.tuples(
        st.booleans(),
        st.booleans(),
        st.booleans(),
        st.booleans(),
        st.booleans(),
        st.integers(0, 12),
    ),
    min_size=1,
    max_size=24,
)


@settings(max_examples=60, deadline=None)
@given(
    rows=HISTORY_ROWS,
    source_order=st.permutations((0, 1, 2)),
    sheet_order=st.permutations((0, 1, 2, 3)),
    reverse=st.booleans(),
)
def test_ip12_generated_history_matches_independent_model(
    rows: list[tuple[bool, bool, bool, bool, bool, int]],
    source_order: list[int],
    sheet_order: list[int],
    reverse: bool,
) -> None:
    specs: list[RowSpec] = [
        (3 if row[0] else i % 3, i + 1, "base") for i, row in enumerate(rows)
    ]
    initial = snapshot(specs)
    archive_head, archive_old, active_entries = [], [], []
    current_specs: list[RowSpec] = []
    expected: Model = {}
    for i, (party, member, present, voided, edit, revision) in enumerate(rows):
        sheet, number, _ = specs[i]
        current_row = initial.all_rows_by_id[uid(number)]
        known = revision > 0
        present = present and (party or member or not known)
        if present:
            current_specs.append((sheet, number, "edit" if edit else "base"))
        if not known:
            continue
        old = state(
            sheet, number, revision, voided=voided, digest=current_row.source_hash
        )
        if member:
            active_entries.append(old)
        if party or not member:
            archive_head.append(old)
        if party and revision > 1:
            archive_old.append(state(sheet, number, revision - 1, digest="b" * 64))
        if member or (party and present):
            expected[uid(number)] = (
                SHEETS[sheet],
                revision,
                "voided" if voided else "active",
                None if voided else current_row.source_hash,
            )
    if reverse:
        archive_head.reverse()
        archive_old.reverse()
        active_entries.reverse()
        current_specs.reverse()
    records = [
        record(1403, archive_old),
        record(1404, archive_head),
        record(1405, active_entries, active=True),
    ]
    source = snapshot(current_specs, tuple(sheet_order))
    registry = SourceBindingRegistry([records[i] for i in source_order])
    catalog = SourceIdentityCatalog(registry)
    actual = project_source_prior(records[2].key, source, catalog)
    assert view(actual) == expected
    assert list(actual.identities) == sorted(
        expected, key=lambda identity: identity.bytes
    )
    assert plan_view(plan_source_changes(source, actual)) == expected_plan(
        source, expected
    )
    assert catalog.identity_count == sum(row[-1] > 0 for row in rows)
    assert records[2].prior_registry is registry.active_record.prior_registry  # type: ignore[union-attr]


@settings(max_examples=30, deadline=None)
@given(
    revision=st.integers(1, 1000),
    voided=st.booleans(),
    digest=st.sampled_from(("b", "c", "d")),
)
def test_ip12_generated_conflict_below_head_is_rejected(
    revision: int, voided: bool, digest: str
) -> None:
    first = state(3, 1, revision)
    inconsistent = state(3, 1, revision, voided=voided, digest=digest * 64)
    head = state(3, 1, revision + 1)
    registry = SourceBindingRegistry(
        [
            record(1405, [head], active=True),
            record(1403, [first]),
            record(1404, [inconsistent]),
        ]
    )
    with pytest.raises(SourceIdentityProjectionError) as caught:
        SourceIdentityCatalog(registry)
    assert caught.value.reason is SourceIdentityProjectionReason.INCONSISTENT_CATALOG


@pytest.mark.parametrize("archive_size", [1, 1000])
def test_ip14_projection_never_revisits_archive(
    monkeypatch: pytest.MonkeyPatch, archive_size: int
) -> None:
    archive = record(1404, [state(0, 100 + i) for i in range(archive_size)])
    current = snapshot([(3, 1, "new")])
    active = record(1405, active=True)
    registry = SourceBindingRegistry([active, archive])
    original = PriorIdentityRegistry.__getattribute__
    visits = 0
    block = False

    def monitor(self: PriorIdentityRegistry, name: str) -> Any:
        nonlocal visits
        if self is archive.prior_registry and name == "identities":
            visits += 1
            assert not block, "archive revisited after catalog construction"
        return original(self, name)

    monkeypatch.setattr(PriorIdentityRegistry, "__getattribute__", monitor)
    catalog = SourceIdentityCatalog(registry)
    assert visits == 1
    block = True
    for _ in range(3):
        projected = project_source_prior(active.key, current, catalog)
        assert not projected.identities
    assert visits == 1 and catalog.identity_count == archive_size


def test_ip14_15000_identity_projection_benchmark() -> None:
    script = Path(__file__).with_name("source_identity_projection_benchmark.py")
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=90
    )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["global_identities"] == 15000 and data["projected_identities"] == 11250
    assert (
        data["planner_items_checked"] == 11250 and data["archive_memberships"] == 11250
    )
    assert data["peak_rss_mib"] > 0
    assert all(
        data[name] >= 0
        for name in (
            "fixture_seconds",
            "catalog_seconds",
            "projection_seconds",
            "planner_seconds",
        )
    )
    print(json.dumps(data, sort_keys=True))
