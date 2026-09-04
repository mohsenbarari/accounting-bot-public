"""RC-08/09 compose the codec with independently expected Planner outcomes."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from accounting_contracts import (
    SourceBindingRegistry,
    SourceIdentityCatalog,
    decode_source_raw_row,
    encode_source_raw_row,
    plan_source_changes,
    project_source_prior,
)
from accounting_local_agent import read_identified_xlsx_source
from source_identity_projection_support import (
    Model,
    expected_plan,
    plan_view,
    prior_from_snapshot,
    record,
    view,
)
from source_raw_codec_support import SHEETS, make_row, row_view, snapshot, uid
from xlsx_source_identity_fixtures import identified_parts, raw_parts, zipped


def test_rc08_representation_changes_do_not_create_revisions_or_membership() -> None:
    old_rows = [make_row(sheet) for sheet in range(4)]
    current_rows = [
        make_row(
            0,
            {
                "date_raw": "1405-01-01",
                "quantity_raw": "001.000",
                "unit_price_toman_raw": Decimal("100.00"),
            },
        ),
        make_row(1, {"amount_toman_raw": "0"}),
        make_row(2, {"purity_raw": Decimal("0.7500")}),
        make_row(3),
    ]
    for index, (old, current_row) in enumerate(
        zip(old_rows, current_rows, strict=True)
    ):
        assert old.source_hash == current_row.source_hash
        if index < 3:
            assert encode_source_raw_row(old) != encode_source_raw_row(current_row)
    restored_rows = [
        decode_source_raw_row(encode_source_raw_row(row)) for row in current_rows
    ]
    assert [row_view(row) for row in restored_rows] == [
        row_view(row) for row in current_rows
    ]
    old_snapshot, current = snapshot(old_rows), snapshot(restored_rows)
    prior = prior_from_snapshot(old_snapshot, 7)
    plan = plan_source_changes(current, prior)
    assert plan_view(plan) == expected_plan(current, view(prior))
    assert [
        (i.stable_id, i.action.value, i.prior_revision, i.planned_revision)
        for i in plan.items
    ] == [(uid(i), "unchanged", 7, None) for i in range(1, 5)]

    # A global party at revision 7 is still UNCHANGED on first annual membership.
    archive = record(1404, prior=prior_from_snapshot(snapshot([old_rows[3]]), 7))
    active = record(1405, active=True)
    archive_before = view(archive.prior_registry)
    catalog = SourceIdentityCatalog(SourceBindingRegistry([archive, active]))
    projected = project_source_prior(active.key, current, catalog)
    expected: Model = {uid(4): (SHEETS[3], 7, "active", old_rows[3].source_hash)}
    assert view(projected) == expected
    result = plan_source_changes(current, projected)
    assert plan_view(result) == expected_plan(current, expected)
    assert [
        (i.stable_id, i.action.value, i.planned_revision) for i in result.items
    ] == [
        (uid(1), "insert", 1),
        (uid(2), "insert", 1),
        (uid(3), "insert", 1),
        (uid(4), "unchanged", None),
    ]
    assert not active.prior_registry.identities
    assert view(archive.prior_registry) == archive_before

    edited = snapshot([make_row(0, {"unit_price_toman_raw": 101}), *restored_rows[1:]])
    edit_plan = plan_source_changes(edited, prior)
    assert plan_view(edit_plan) == expected_plan(edited, view(prior))
    assert [
        (i.stable_id, i.action.value, i.planned_revision) for i in edit_plan.items
    ] == [
        (uid(1), "edit", 8),
        (uid(2), "unchanged", None),
        (uid(3), "unchanged", None),
        (uid(4), "unchanged", None),
    ]
    assert [row_view(row) for row in old_snapshot.all_rows_by_id.values()] == [
        row_view(row) for row in old_rows
    ]
    assert view(archive.prior_registry) == archive_before


def test_rc09_identified_xlsx_reorder_formula_cache_and_raw_edit(
    tmp_path: Path,
) -> None:
    source, leases = tmp_path / "SYNTHETIC.xlsx", tmp_path / "leases"
    leases.mkdir()
    prior = None
    original_views = None
    original = None
    for generation in range(4):
        parts = raw_parts(
            reorder=generation > 0,
            formula=3 if generation >= 2 else 0,
            edit=generation == 3,
        )
        content = zipped(identified_parts(raw=parts))
        source.write_bytes(content)
        try:
            result = read_identified_xlsx_source(
                source,
                snapshot_root=leases,
                observation_interval_seconds=0.001,
            )
        finally:
            assert source.read_bytes() == content and list(leases.iterdir()) == []
        assert result.key.source_id == uid(999) and result.key.fiscal_year == 1405
        current = result.read_result.snapshot
        decoded = [
            decode_source_raw_row(encode_source_raw_row(row))
            for row in current.all_rows_by_id.values()
        ]
        assert [row_view(row) for row in decoded] == [
            row_view(row) for row in current.all_rows_by_id.values()
        ]
        reconstructed = snapshot(decoded)
        assert set(reconstructed.all_rows_by_id) == {
            uid(n) for n in (1, 2, 100001, 200001, 300001)
        }
        assert reconstructed.total_row_count == 5
        for sheet in SHEETS:
            assert (
                reconstructed.sheets[sheet].sheet_snapshot_hash
                == current.sheets[sheet].sheet_snapshot_hash
            )
        if prior is None:
            original = current
            original_views = [row_view(row) for row in current.all_rows_by_id.values()]
            prior = prior_from_snapshot(current, 7)
        plan = plan_source_changes(reconstructed, prior)
        assert plan_view(plan) == expected_plan(reconstructed, view(prior))
        expected = [
            (
                uid(n),
                "edit" if generation == 3 and n == 1 else "unchanged",
                7,
                8 if generation == 3 and n == 1 else None,
            )
            for n in (1, 2, 100001, 200001, 300001)
        ]
        assert [
            (i.stable_id, i.action.value, i.prior_revision, i.planned_revision)
            for i in plan.items
        ] == expected
        assert original is not None
        assert [
            row_view(row) for row in original.all_rows_by_id.values()
        ] == original_views
