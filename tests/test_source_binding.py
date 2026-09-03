"""SB-01..14: independent routing, source isolation, purity and scale evidence."""

from __future__ import annotations

import inspect
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import FrozenInstanceError, fields, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

import accounting_contracts as contracts
import accounting_contracts.source_binding as binding
import accounting_contracts.source_change_plan as planner
import accounting_contracts.source_fiscal_evidence as fiscal
import accounting_contracts.source_requiredness as requiredness
import accounting_local_agent.source_watch_runtime as watcher
import accounting_local_agent.xlsx_source_reader as reader
import pytest
from accounting_contracts import (
    ContractError,
    IdentityLifecycle,
    PriorIdentityRegistry,
    PriorIdentityState,
    SourceBindingDisposition,
    SourceBindingInputError,
    SourceBindingKey,
    SourceBindingRecord,
    SourceBindingRegistry,
    SourceBindingResolution,
    SourceBindingState,
    SourceSheetInput,
    ValidatedSourceWorkbookSnapshot,
    build_prior_identity_registry,
    build_source_workbook_snapshot,
    evaluate_source_fiscal_evidence,
    evaluate_source_requiredness,
    plan_source_changes,
    resolve_source_binding,
)
from hypothesis import given, settings
from hypothesis import strategies as st
from source_binding_import_probe import (
    PUBLIC_NAMES,
    ForbiddenSideEffect,
    deny_side_effects,
    forbidden,
)
from test_source_fiscal_evidence import RAW_TEMPLATES, SHEETS

ENTRY_POINTS = (SourceBindingResolution, resolve_source_binding)
type Resolver = Callable[
    [SourceBindingKey, SourceBindingRegistry], SourceBindingResolution
]


def _uid(index: int, timestamp: int = 0) -> uuid.UUID:
    return uuid.UUID(int=(timestamp << 80) | (7 << 76) | (2 << 62) | index)


def _snapshot(seed: int = 1) -> ValidatedSourceWorkbookSnapshot:
    sheets = []
    for i, name in enumerate(SHEETS):
        raw = dict(RAW_TEMPLATES[i])
        if i < 3:
            raw["date_raw"] = "1405/01/01"
        sheets.append(SourceSheetInput(name, [(_uid(seed + i), raw)]))
    return build_source_workbook_snapshot(sheets)


def _empty() -> ValidatedSourceWorkbookSnapshot:
    return build_source_workbook_snapshot([SourceSheetInput(s, []) for s in SHEETS])


def _prior(
    snapshot: ValidatedSourceWorkbookSnapshot, revision: int = 7
) -> PriorIdentityRegistry:
    return build_prior_identity_registry(
        [
            PriorIdentityState(
                row.stable_id,
                row.canonical_uuid,
                row.sheet_name,
                revision,
                IdentityLifecycle.ACTIVE,
                row.source_hash,
            )
            for row in snapshot.all_rows_by_id.values()
        ]
    )


def _record(
    number: int,
    year: int,
    state: SourceBindingState = SourceBindingState.ACTIVE,
    prior: PriorIdentityRegistry | None = None,
) -> SourceBindingRecord:
    return SourceBindingRecord(
        SourceBindingKey(_uid(number), year),
        state,
        build_prior_identity_registry([]) if prior is None else prior,
        None if state is SourceBindingState.ACTIVE else "d" * 64,
    )


def test_sb01_public_contract_and_guarded_fresh_import(tmp_path: Path) -> None:
    assert binding.SOURCE_BINDING_VERSION == "source-binding.v1"
    assert issubclass(SourceBindingInputError, ContractError)
    assert len(PUBLIC_NAMES) == 9
    assert set(binding.__all__) == set(PUBLIC_NAMES)
    assert {
        n
        for n in contracts.__all__
        if "Binding" in n or "binding" in n or n == "SOURCE_BINDING_VERSION"
    } == set(PUBLIC_NAMES)
    for name in PUBLIC_NAMES:
        assert getattr(contracts, name) is getattr(binding, name)
    signatures: tuple[tuple[Callable[..., Any], tuple[str, ...]], ...] = (
        (SourceBindingKey, ("source_id", "fiscal_year")),
        (SourceBindingRecord, ("key", "state", "prior_registry", "final_file_sha256")),
        (SourceBindingRegistry, ("records",)),
        *((entry, ("key", "registry")) for entry in ENTRY_POINTS),
    )
    for target, params in signatures:
        signature = inspect.signature(target)
        assert tuple(signature.parameters) == params
        assert all(
            p.default is inspect.Parameter.empty for p in signature.parameters.values()
        )
    assert [(v.name, v.value) for v in SourceBindingState] == [
        ("ACTIVE", "active"),
        ("ARCHIVED", "archived"),
    ]
    assert [(v.name, v.value) for v in SourceBindingDisposition] == [
        ("ACTIVE", "active"),
        ("ARCHIVED", "archived"),
        ("UNREGISTERED", "unregistered"),
    ]
    probe = Path(__file__).with_name("source_binding_import_probe.py")
    canary = tmp_path / "binding-canary.txt"
    for mode, expected_code, expected_lines in (
        ("normal", 0, ["IMPORT_ENTERED", "IMPORT_EXECUTED", "PROBE_OK"]),
        ("inject_write", 73, ["IMPORT_ENTERED", "IMPORT_REJECTED_BY_GUARD"]),
    ):
        result = subprocess.run(
            [sys.executable, "-B", str(probe), mode, str(canary)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == expected_code, result.stderr
        assert result.stdout.splitlines() == expected_lines
        assert not canary.exists()
    assert sys.modules[binding.__name__] is binding
    for name in PUBLIC_NAMES:
        assert getattr(contracts, name) is getattr(binding, name)


@pytest.mark.parametrize("year", [1, 999, 1399, 1405, 1406, 2000, 9377])
def test_sb02_valid_key_and_declared_year_boundaries(year: int) -> None:
    source_id = _uid(2, 0x123456789ABC)
    key = SourceBindingKey(source_id, year)
    assert key.source_id is source_id
    assert key.fiscal_year == year and type(key.fiscal_year) is int


@pytest.mark.parametrize(
    "bad",
    [
        None,
        True,
        7,
        "00000000-0000-7000-8000-000000000001",
        uuid.UUID(int=0),
        uuid.UUID(int=(4 << 76) | (2 << 62)),
        uuid.UUID(int=(7 << 76) | (3 << 62)),
    ],
)
def test_sb02_invalid_uuid_objects_and_types(bad: Any) -> None:
    with pytest.raises(SourceBindingInputError, match=r"^Invalid source ID\.$"):
        SourceBindingKey(bad, 1405)


@pytest.mark.parametrize("bad", [None, True, False, "1405", 1405.0, 0, -1, 9379, 10000])
def test_sb02_invalid_years(bad: Any) -> None:
    with pytest.raises(
        SourceBindingInputError, match=r"^Invalid source fiscal year\.$"
    ):
        SourceBindingKey(_uid(2), bad)


class ForeignState(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@pytest.mark.parametrize(
    "bad",
    ["active", "archived", ForeignState.ACTIVE, ForeignState.ARCHIVED, None, True],
)
def test_sb03_exact_state_enum(bad: Any) -> None:
    record = _record(1, 1405)
    with pytest.raises(
        SourceBindingInputError, match=r"^Invalid source binding state\.$"
    ):
        replace(record, state=bad)
    assert record.state is SourceBindingState.ACTIVE


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "d" * 63,
        "d" * 65,
        "D" * 64,
        "g" * 64,
        "0" * 63 + "\n",
        b"d" * 64,
        True,
    ],
)
def test_sb03_invalid_archive_hashes(bad: Any) -> None:
    record = _record(1, 1405, SourceBindingState.ARCHIVED)
    with pytest.raises(
        SourceBindingInputError, match=r"^Invalid archived source final hash\.$"
    ):
        replace(record, final_file_sha256=bad)


def test_sb03_record_type_and_lifecycle_invariants() -> None:
    record = _record(1, 1405)
    invalid_hashes: tuple[Any, ...] = ("", "d" * 64, False)
    for bad in invalid_hashes:
        with pytest.raises(SourceBindingInputError):
            replace(record, final_file_sha256=bad)
    invalid_fields: tuple[dict[str, Any], ...] = (
        {"key": "SYNTHETIC-SECRET"},
        {"prior_registry": {}},
        {"key": None},
        {"prior_registry": None},
    )
    for invalid in invalid_fields:
        with pytest.raises(SourceBindingInputError):
            replace(record, **invalid)

    class TextSubclass(str):
        pass

    with pytest.raises(SourceBindingInputError):
        replace(
            record,
            state=SourceBindingState.ARCHIVED,
            final_file_sha256=TextSubclass("d" * 64),
        )
    archived = replace(
        record,
        state=SourceBindingState.ARCHIVED,
        final_file_sha256="0123456789abcdef" * 4,
    )
    assert archived.key is record.key
    assert archived.prior_registry is record.prior_registry
    assert archived.state is SourceBindingState.ARCHIVED
    assert not archived.prior_registry.identities


@pytest.mark.parametrize(
    "bad",
    [
        None,
        True,
        1,
        "",
        "SYNTHETIC-SECRET",
        b"",
        {},
        {"invalid": None},
        [None],
        ["invalid"],
        [object()],
    ],
)
def test_sb04_invalid_registry_containers_and_elements(bad: Any) -> None:
    with pytest.raises(SourceBindingInputError):
        SourceBindingRegistry(bad)


def test_sb04_duplicates_active_limit_and_defensive_one_shot_copy() -> None:
    a = _record(50, 1404, SourceBindingState.ARCHIVED)
    b = _record(2, 1405)
    for records, message in (
        ([a, a], "Duplicate source ID."),
        ([a, _record(50, 1406)], "Duplicate source ID."),
        ([a, _record(4, 1404)], "Duplicate source fiscal year."),
        ([b, _record(4, 1406)], "Multiple active sources."),
    ):
        with pytest.raises(SourceBindingInputError) as caught:
            SourceBindingRegistry(records)
        assert str(caught.value) == message
    assert SourceBindingRegistry([]).records == ()
    assert SourceBindingRegistry([]).active_record is None
    assert SourceBindingRegistry([a]).active_record is None
    supplied = [b, a]
    calls = 0

    class Once:
        def __iter__(self) -> Iterator[SourceBindingRecord]:
            nonlocal calls
            calls += 1
            assert calls == 1
            yield from supplied

    registry = SourceBindingRegistry(Once())
    supplied.clear()
    assert calls == 1
    assert registry.records == (a, b)  # Year order, deliberately inverse UUID order.
    assert registry.records[0] is a and registry.records[1] is b
    assert registry.active_record is b


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_sb05_active_selects_exact_object(entry: Resolver) -> None:
    # Equal raw content and IDs do not imply equal annual registration.
    snap = _snapshot()
    prior_a, prior_b = _prior(snap, 2), _prior(snap, 11)
    a = _record(50, 1404, SourceBindingState.ARCHIVED, prior_a)
    b = _record(51, 1405, prior=prior_b)
    registry = SourceBindingRegistry([b, a])
    query = SourceBindingKey(uuid.UUID(str(b.key.source_id)), 1405)
    result = entry(query, registry)
    assert result.key is query and result.registry is registry
    assert result.disposition is SourceBindingDisposition.ACTIVE
    assert result.record is b and result.prior_registry is prior_b
    assert result.prior_registry is not prior_a


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_sb06_archives_do_not_invoke_operational_functions(
    entry: Resolver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = _record(50, 1404, SourceBindingState.ARCHIVED, _prior(_snapshot()))
    b = _record(51, 1405)
    registry = SourceBindingRegistry([a, b])
    with monkeypatch.context() as guard:
        for owner, name in (
            (contracts, "plan_source_changes"),
            (planner, "plan_source_changes"),
            (reader, "read_xlsx_source_snapshot"),
            (watcher, "read_due_source"),
            (fiscal, "evaluate_source_fiscal_evidence"),
            (requiredness, "evaluate_source_requiredness"),
        ):
            guard.setattr(owner, name, forbidden)
        with deny_side_effects():
            result = entry(a.key, registry)
    assert result.disposition is SourceBindingDisposition.ARCHIVED
    assert result.record is a and result.prior_registry is None
    assert a.final_file_sha256 == "d" * 64
    assert registry.active_record is b
    assert not any(
        hasattr(result, n) for n in ("commit_allowed", "passes_fiscal", "plan")
    )


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_sb07_unknown_and_year_mismatch(entry: Resolver) -> None:
    a = _record(1, 1404, SourceBindingState.ARCHIVED)
    b = _record(2, 1405)
    registry = SourceBindingRegistry([a, b])
    for year in (1404, 1405, 1406):
        query = SourceBindingKey(_uid(999), year)
        for selected in (registry, SourceBindingRegistry([])):
            result = entry(query, selected)
            assert result.disposition is SourceBindingDisposition.UNREGISTERED
            assert result.record is None and result.prior_registry is None
            assert result.key is query and result.registry is selected
    for record in (a, b):
        with pytest.raises(SourceBindingInputError) as caught:
            entry(SourceBindingKey(record.key.source_id, 1399), registry)
        assert str(caught.value) == "Registered source fiscal year mismatch."
    assert registry.records == (a, b) and registry.active_record is b


def test_sb08_computed_fields_immutability_and_private_prior_repr() -> None:
    hidden_hash = "c" * 64
    prior = build_prior_identity_registry(
        [
            PriorIdentityState(
                _uid(1),
                str(_uid(1)),
                SHEETS[0],
                7,
                IdentityLifecycle.ACTIVE,
                hidden_hash,
            )
        ]
    )
    record = _record(99, 1405, prior=prior)
    registry = SourceBindingRegistry([record])
    result = SourceBindingResolution(record.key, registry)
    assert result == resolve_source_binding(record.key, registry)
    for item in (record.key, record, registry, result):
        assert not hasattr(item, "__dict__")
        assert hidden_hash not in repr(item)
        for data_field in fields(item):
            with pytest.raises(FrozenInstanceError):
                setattr(item, data_field.name, None)
    for injected in ("disposition", "record", "prior_registry", "commit_allowed"):
        with pytest.raises(TypeError):
            SourceBindingResolution(record.key, registry, **{injected: True})
    for injected in ("active_record", "_by_source_id", "counts", "valid"):
        with pytest.raises(TypeError):
            SourceBindingRegistry([record], **{injected: True})
    with pytest.raises(TypeError):
        registry._by_source_id[record.key.source_id] = record  # type: ignore[index]
    with pytest.raises(TypeError):
        registry.records[0] = record  # type: ignore[index]
    with pytest.raises(TypeError):
        prior.identities[_uid(1)] = prior.identities[_uid(1)]  # type: ignore[index]


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_sb08_invalid_entry_inputs_have_fixed_private_errors(entry: Resolver) -> None:
    marker = "SYNTHETIC-PRIVATE-BINDING-PATH"
    key = SourceBindingKey(_uid(1), 1405)
    registry = SourceBindingRegistry([])
    for bad in (None, marker, True, {}, object()):
        for args, message in (
            ((bad, registry), "Invalid source binding key."),
            ((key, bad), "Invalid source binding registry."),
        ):
            with pytest.raises(SourceBindingInputError) as caught:
                entry(*args)
            assert str(caught.value) == message
            assert marker not in str(caught.value) + repr(caught.value)


def test_sb09_empty_party_undated_and_mixed_snapshots_are_independent() -> None:
    prior = _prior(_snapshot())
    record = _record(200, 1405, prior=prior)
    registry = SourceBindingRegistry([record])
    fixtures: list[
        tuple[ValidatedSourceWorkbookSnapshot, tuple[int, ...], int, bool]
    ] = [
        (_empty(), (), 0, True),
        (
            build_source_workbook_snapshot(
                [
                    SourceSheetInput(
                        s, [(_uid(9), dict(RAW_TEMPLATES[3]))] if i == 3 else []
                    )
                    for i, s in enumerate(SHEETS)
                ]
            ),
            (),
            0,
            True,
        ),
    ]
    for years, expected, undated in (
        ((None, None, None), (), 3),
        ((1399, 1406, None), (1399, 1406), 1),
        ((1406, 1398, 1406), (1398, 1406), 0),
    ):
        sheets = []
        for i, name in enumerate(SHEETS):
            raw = dict(RAW_TEMPLATES[i])
            if i < 3:
                raw["date_raw"] = None if years[i] is None else f"{years[i]}/01/01"
                raw["notes_raw"] = "SYNTHETIC-NOTE-2099"
            sheets.append(SourceSheetInput(name, [(_uid(i + 1, 999), raw)]))
        fixtures.append(
            (build_source_workbook_snapshot(sheets), expected, undated, undated == 0)
        )
    before = resolve_source_binding(record.key, registry)
    for snapshot, expected_years, undated, passes in fixtures:
        report = evaluate_source_fiscal_evidence(snapshot)
        assert report.observed_years == expected_years
        assert report.undated_row_count == undated
        assert evaluate_source_requiredness(snapshot).passes_requiredness is passes
        assert resolve_source_binding(record.key, registry) == before
        assert before.prior_registry is prior
        assert report.snapshot is snapshot


@settings(max_examples=60, deadline=None)
@given(
    st.lists(st.integers(1, 4000), min_size=1, max_size=12, unique=True),
    st.integers(-1, 30),
    st.data(),
)
def test_sb10_property_routing_oracle_and_real_permutations(
    years: list[int],
    active_seed: int,
    data: st.DataObject,
) -> None:
    active_index = None if active_seed == -1 else active_seed % len(years)
    expected_by_id = {}
    records = []
    for i, year in enumerate(years):
        state = (
            SourceBindingState.ACTIVE
            if i == active_index
            else SourceBindingState.ARCHIVED
        )
        record = _record(500 - i, year, state)
        records.append(record)
        expected_by_id[record.key.source_id] = (year, i == active_index, record)
    permutations = [list(reversed(records)), list(data.draw(st.permutations(records)))]
    if len(records) > 1:
        assert permutations[0] != records
    ordered = tuple(
        sorted(records, key=lambda r: (r.key.fiscal_year, r.key.source_id.bytes))
    )
    for permuted in permutations:
        registry = SourceBindingRegistry(permuted)
        assert registry.records == ordered
        for source_id, (year, is_active, original) in expected_by_id.items():
            for entry in ENTRY_POINTS:
                result = entry(SourceBindingKey(source_id, year), registry)
                assert result.disposition.value == (
                    "active" if is_active else "archived"
                )
                assert result.record is original
                assert result.prior_registry is (
                    original.prior_registry if is_active else None
                )
                with pytest.raises(SourceBindingInputError):
                    entry(SourceBindingKey(source_id, year + 1), registry)
        unknown = resolve_source_binding(
            SourceBindingKey(_uid(900), years[0]), registry
        )
        assert unknown.disposition.value == "unregistered" and unknown.record is None
        # Change one year; retain other record objects and all prior views.
        changed = replace(
            records[0], key=SourceBindingKey(records[0].key.source_id, 5000)
        )
        updated = SourceBindingRegistry([changed, *records[1:]])
        assert resolve_source_binding(changed.key, updated).record is changed
        for unchanged in records[1:]:
            result = resolve_source_binding(unchanged.key, updated)
            assert result.record is unchanged
        with pytest.raises(SourceBindingInputError):
            resolve_source_binding(records[0].key, updated)


def test_sb11_pure_construction_resolution_and_guard_control(tmp_path: Path) -> None:
    prior = _prior(_snapshot())
    items = tuple(prior.identities.items())
    source_id = _uid(90)
    canary = tmp_path / "forbidden-binding-write.txt"
    with deny_side_effects():
        for entry in ENTRY_POINTS:
            key = SourceBindingKey(source_id, 1405)
            record = SourceBindingRecord(key, SourceBindingState.ACTIVE, prior, None)
            registry = SourceBindingRegistry([record])
            before = entry(key, registry)
            for _ in range(10):
                assert entry(key, registry) == before
                assert entry(key, registry).prior_registry is prior
            try:
                canary.write_text("SYNTHETIC GUARD CONTROL")
            except ForbiddenSideEffect as exc:
                assert str(exc) == "Source binding side-effect guard"
            else:
                pytest.fail("Side-effect guard failed to intercept write")
    assert not canary.exists()
    assert tuple(prior.identities.items()) == items
    for identity, state in items:
        assert prior.identities[identity] is state
        assert state.stable_id is identity


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("synthetic"),
        TypeError("synthetic"),
        KeyboardInterrupt(),
        SystemExit(8),
    ],
)
def test_sb11_iteration_and_internal_errors_propagate(
    failure: BaseException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record(1, 1405)

    class Broken:
        def __iter__(self) -> Iterator[SourceBindingRecord]:
            yield record
            raise failure

    with pytest.raises(type(failure)) as caught:
        SourceBindingRegistry(Broken())
    assert caught.value is failure

    def broken_parser(*args: Any, **kwargs: Any) -> Any:
        raise failure

    with monkeypatch.context() as patch:
        patch.setattr(binding, "parse_canonical_jalali_date", broken_parser)
        with pytest.raises(type(failure)) as caught:
            SourceBindingKey(_uid(1), 1405)
    assert caught.value is failure

    class BrokenIndex:
        def get(self, source_id: uuid.UUID) -> Any:
            raise failure

    registry = SourceBindingRegistry([record])
    with monkeypatch.context() as patch:
        # Fault injection at the lookup seam; public constructors remain intact.
        patch.setattr(SourceBindingRegistry, "_by_source_id", BrokenIndex())
        for entry in ENTRY_POINTS:
            with pytest.raises(type(failure)) as caught:
                entry(record.key, registry)
            assert caught.value is failure


def test_sb12_explicit_planner_composition_never_targets_another_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap_a, snap_b = _snapshot(100), _snapshot(200)
    prior_a, prior_b = _prior(snap_a, 3), _prior(snap_b, 7)
    settled_id = _uid(299)
    settled = PriorIdentityState(
        settled_id, str(settled_id), SHEETS[0], 9, IdentityLifecycle.VOIDED, None
    )
    prior_b = build_prior_identity_registry([*prior_b.identities.values(), settled])
    a = _record(900, 1404, SourceBindingState.ARCHIVED, prior_a)
    b = _record(901, 1405, prior=prior_b)
    registry = SourceBindingRegistry([b, a])
    selected = resolve_source_binding(b.key, registry).prior_registry
    assert selected is prior_b
    baseline_plan = plan_source_changes(snap_b, selected)
    assert [
        (p.stable_id, p.action.value, p.planned_revision) for p in baseline_plan.items
    ] == [(_uid(i), "unchanged", None) for i in range(200, 204)]
    sheets = []
    for i, name in enumerate(SHEETS):
        row = snap_b.all_rows_by_id[_uid(200 + i)]
        raw = dict(row.raw_values)
        if i == 0:
            raw["notes_raw"] = "SYNTHETIC EDIT"
        sheets.append(SourceSheetInput(name, [] if i == 1 else [(row.stable_id, raw)]))
    edited = plan_source_changes(build_source_workbook_snapshot(sheets), selected)
    assert [
        (p.stable_id, p.action.value, p.planned_revision) for p in edited.items
    ] == [
        (_uid(200), "edit", 8),
        (_uid(201), "void", 8),
        (_uid(202), "unchanged", None),
        (_uid(203), "unchanged", None),
    ]
    emptied = plan_source_changes(_empty(), selected)
    assert [
        (p.stable_id, p.action.value, p.planned_revision) for p in emptied.items
    ] == [(_uid(i), "void", 8) for i in range(200, 204)]
    fresh_b = replace(b, prior_registry=build_prior_identity_registry([]))
    new_selected = resolve_source_binding(
        b.key, SourceBindingRegistry([a, fresh_b])
    ).prior_registry
    assert new_selected is not None
    first = plan_source_changes(snap_b, new_selected)
    assert [(p.stable_id, p.action.value, p.planned_revision) for p in first.items] == [
        (_uid(i), "insert", 1) for i in range(200, 204)
    ]
    for plan in (baseline_plan, edited, emptied, first):
        assert not {p.stable_id for p in plan.items} & prior_a.identities.keys()
        assert settled_id not in {p.stable_id for p in plan.items}
    with monkeypatch.context() as patch:
        patch.setattr(planner, "plan_source_changes", forbidden)
        for key in (a.key, SourceBindingKey(_uid(999), 1405)):
            result = resolve_source_binding(key, registry)
            assert result.prior_registry is None
            if result.disposition is SourceBindingDisposition.ACTIVE:
                assert result.prior_registry is not None
                planner.plan_source_changes(snap_b, result.prior_registry)
    assert a.prior_registry is prior_a and b.prior_registry is prior_b


def test_sb13_shared_party_keeps_independent_historical_revisions() -> None:
    party_id = _uid(800)
    views = []
    for revision in (3, 17):
        views.append(
            build_prior_identity_registry(
                [
                    PriorIdentityState(
                        party_id,
                        str(party_id),
                        SHEETS[3],
                        revision,
                        IdentityLifecycle.ACTIVE,
                        "a" * 64,
                    )
                ]
            )
        )
    a = _record(90, 1404, SourceBindingState.ARCHIVED, views[0])
    b = _record(91, 1405, prior=views[1])
    registry = SourceBindingRegistry([a, b])
    assert resolve_source_binding(b.key, registry).prior_registry is views[1]
    assert resolve_source_binding(a.key, registry).prior_registry is None
    for record, view, revision in zip((a, b), views, (3, 17), strict=True):
        assert record.prior_registry is view
        state = view.identities[party_id]
        assert state.stable_id is party_id
        assert state.latest_revision == revision and state.home_sheet == SHEETS[3]


def test_sb13_large_prior_is_never_traversed_or_copied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_start = time.perf_counter()
    prior = build_prior_identity_registry(
        [
            PriorIdentityState(
                _uid(i),
                str(_uid(i)),
                SHEETS[i % 4],
                7,
                IdentityLifecycle.ACTIVE,
                "a" * 64,
            )
            for i in range(1, 15001)
        ]
    )
    prior_map = prior.identities
    records = [
        _record(
            20000 + i,
            1300 + i,
            SourceBindingState.ACTIVE if i == 99 else SourceBindingState.ARCHIVED,
            prior,
        )
        for i in range(100)
    ]
    fixture_seconds = time.perf_counter() - fixture_start
    start = time.perf_counter()
    with monkeypatch.context() as patch:
        patch.setattr(PriorIdentityRegistry, "identities", property(forbidden))
        registry = SourceBindingRegistry(reversed(records))
        construction_seconds = time.perf_counter() - start
        # Detect an accidental per-resolution scan of annual records too.
        patch.setattr(SourceBindingRegistry, "records", property(forbidden))
        start = time.perf_counter()
        for _ in range(10):
            for record in records:
                result = resolve_source_binding(record.key, registry)
                assert result.record is record
                assert result.prior_registry is (
                    prior if record is records[-1] else None
                )
        resolution_seconds = time.perf_counter() - start
        with pytest.raises(
            ForbiddenSideEffect, match="Source binding side-effect guard"
        ):
            _ = prior.identities
    assert prior.identities is prior_map and len(prior_map) == 15000
    assert registry.records == tuple(records)
    assert all(record.prior_registry is prior for record in registry.records)
    print(
        f"SB-13 rows=15000 sources=100 lookups=1000 fixture={fixture_seconds:.6f}s "
        f"construction={construction_seconds:.6f}s resolution={resolution_seconds:.6f}s"
    )
