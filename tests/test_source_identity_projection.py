"""IP-01..16 contract, independent scope/transition and integration evidence."""

from __future__ import annotations

import inspect
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from dataclasses import FrozenInstanceError, fields, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import accounting_contracts as public
import accounting_contracts.source_change_plan as planner
import accounting_contracts.source_fiscal_evidence as fiscal
import accounting_contracts.source_identity_projection as projection
import accounting_contracts.source_requiredness as requiredness
import pytest
from accounting_contracts import (
    ContractError,
    SourceBindingInputError,
    SourceBindingKey,
    SourceBindingRegistry,
    SourceIdentityCatalog,
    SourceIdentityProjectionError,
    SourceIdentityProjectionReason,
    plan_source_changes,
)
from source_binding_import_probe import (
    ForbiddenSideEffect,
    deny_side_effects,
    forbidden,
)
from source_identity_projection_import_probe import PUBLIC_NAMES
from source_identity_projection_support import (
    SHEETS,
    Model,
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

Reason = SourceIdentityProjectionReason


def test_ip01_exports_constructors_and_immutability() -> None:
    assert (
        projection.SOURCE_IDENTITY_PROJECTION_VERSION == "source-identity-projection.v1"
    )
    assert tuple(projection.__all__) == PUBLIC_NAMES
    assert issubclass(SourceIdentityProjectionError, ContractError)
    for name in PUBLIC_NAMES:
        assert name in public.__all__
        assert getattr(public, name) is getattr(projection, name)
    assert tuple(inspect.signature(SourceIdentityCatalog).parameters) == (
        "source_registry",
    )
    assert tuple(inspect.signature(projection.project_source_prior).parameters) == (
        "key",
        "snapshot",
        "catalog",
    )
    assert [field.name for field in fields(SourceIdentityCatalog) if field.init] == [
        "source_registry"
    ]
    prior = prior_from_snapshot(snapshot([(3, 1, "base")]))
    a = record(1405, active=True, prior=prior)
    registry = SourceBindingRegistry([a])
    catalog = SourceIdentityCatalog(registry)
    assert catalog.source_registry is registry and catalog.identity_count == 1
    assert catalog.version == "source-identity-projection.v1" and not hasattr(
        catalog, "__dict__"
    )
    with pytest.raises(FrozenInstanceError):
        cast(Any, catalog).identity_count = 99
    for key in ("version", "identity_count", "_global_heads", "_transaction_owners"):
        with pytest.raises(TypeError):
            cast(Any, SourceIdentityCatalog)(registry, **{key: None})
    for index in (catalog._global_heads, catalog._transaction_owners):
        with pytest.raises(TypeError):
            cast(Any, index)[uid(1)] = None
    projected = projection.project_source_prior(a.key, snapshot(), catalog)
    assert (
        projected is not prior
        and projected.identities[uid(1)] is prior.identities[uid(1)]
    )
    with pytest.raises(TypeError):
        cast(Any, projected.identities)[uid(1)] = None


@pytest.mark.parametrize("mode,status", [("normal", 0), ("inject_write", 73)])
def test_ip01_guarded_fresh_import(tmp_path: Path, mode: str, status: int) -> None:
    probe = Path(__file__).with_name("source_identity_projection_import_probe.py")
    canary = tmp_path / "SYNTHETIC-canary"
    result = subprocess.run(
        [sys.executable, str(probe), mode, str(canary)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == status, result.stdout + result.stderr
    assert "IMPORT_ENTERED" in result.stdout
    assert ("PROBE_OK" if status == 0 else "IMPORT_REJECTED_BY_GUARD") in result.stdout
    assert not canary.exists()


def test_ip02_catalog_global_heads_ignore_year_and_preserve_equal_objects() -> None:
    party = state(3, 4, 9)
    earlier = record(1404, [party, state(0, 1), state(1, 2), state(2, 3, voided=True)])
    later = record(1406, [state(3, 4, 3, digest="b" * 64)])
    active_state = replace(party)
    assert active_state == party and active_state is not party
    active = record(1405, [active_state], active=True)
    catalog = SourceIdentityCatalog(SourceBindingRegistry([later, active, earlier]))
    assert catalog.identity_count == 4 and catalog._global_heads[uid(4)] is party
    assert {
        key: (value.home_sheet, value.latest_revision, value.lifecycle.value)
        for key, value in catalog._global_heads.items()
    } == {
        uid(1): (SHEETS[0], 7, "active"),
        uid(2): (SHEETS[1], 7, "active"),
        uid(3): (SHEETS[2], 7, "voided"),
        uid(4): (SHEETS[3], 9, "active"),
    }
    assert set(catalog._transaction_owners) == {uid(1), uid(2), uid(3)}
    assert all(owner is earlier.key for owner in catalog._transaction_owners.values())
    result = projection.project_source_prior(
        active.key, snapshot([(3, 4, "base")]), catalog
    )
    assert result.identities[uid(4)] is active_state
    assert later.prior_registry.identities[uid(4)].latest_revision == 3


@pytest.mark.parametrize("archive_only", [False, True])
def test_ip02_empty_and_archive_only_catalog(archive_only: bool) -> None:
    entries = [record(1404, [state(3, 1)])] if archive_only else []
    catalog = SourceIdentityCatalog(SourceBindingRegistry(entries))
    assert catalog.identity_count == int(archive_only)


@pytest.mark.parametrize(
    "case",
    [
        "home",
        "owner-active",
        "owner-voided",
        "hash-tie",
        "life-tie",
        "older-tie",
        "stale-active",
    ],
)
def test_ip03_inconsistent_catalogs(case: str) -> None:
    if case == "home":
        entries = [record(1403, [state(0, 1)]), record(1404, [state(3, 1, 8)])]
    elif case.startswith("owner-"):
        entry = state(1, 1, voided=case == "owner-voided")
        entries = [record(1403, [entry]), record(1404, [replace(entry)], active=True)]
    elif case == "stale-active":
        entries = [
            record(1403, [state(3, 1, 9)]),
            record(1404, [state(3, 1, 8)], active=True),
        ]
    else:
        first = state(3, 1, 2)
        other = state(3, 1, 2, voided=case == "life-tie", digest="b" * 64)
        entries = [record(1402, [first]), record(1403, [other])]
        if case == "older-tie":
            entries.append(record(1404, [state(3, 1, 10)]))
    registry = SourceBindingRegistry(entries)
    before = tuple(view(item.prior_registry) for item in entries)
    with pytest.raises(SourceIdentityProjectionError) as caught:
        SourceIdentityCatalog(registry)
    assert caught.value.reason is Reason.INCONSISTENT_CATALOG
    assert tuple(view(item.prior_registry) for item in entries) == before


@pytest.mark.parametrize(
    "route", ["archive", "unknown", "unknown-same-year", "wrong-year"]
)
def test_ip04_exact_key_routes_and_cause(
    monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    archive, active = record(1404), record(1405, [state(3, 1)], active=True)
    catalog = SourceIdentityCatalog(SourceBindingRegistry([active, archive]))
    keys = {
        "archive": archive.key,
        "unknown": SourceBindingKey(uid(9000), 1406),
        "unknown-same-year": SourceBindingKey(uid(9001), 1405),
        "wrong-year": SourceBindingKey(active.key.source_id, 1404),
    }
    caught_causes: list[SourceBindingInputError] = []
    real_resolve = public.resolve_source_binding

    def capture(*args: Any) -> Any:
        try:
            return real_resolve(*args)
        except SourceBindingInputError as exc:
            caught_causes.append(exc)
            raise

    monkeypatch.setattr(projection, "resolve_source_binding", capture)
    monkeypatch.setattr(planner, "plan_source_changes", forbidden)
    with pytest.raises(SourceIdentityProjectionError) as caught:
        projection.project_source_prior(keys[route], snapshot(), catalog)
    assert caught.value.reason is (
        Reason.INVALID_INPUT if route == "wrong-year" else Reason.SOURCE_NOT_ACTIVE
    )
    if route == "wrong-year":
        assert len(caught_causes) == 1 and caught.value.__cause__ is caught_causes[0]
    else:
        assert caught.value.__cause__ is None


def test_ip05_exact_projection_scope_and_no_archive_void() -> None:
    baseline = snapshot([(0, 1, "base"), (1, 2, "base"), (3, 4, "base")])
    known = prior_from_snapshot(baseline)
    a = record(1404, [known.identities[uid(4)], state(0, 90), state(3, 91)])
    b = record(
        1405,
        [known.identities[uid(2)], state(2, 3, voided=True), known.identities[uid(1)]],
        active=True,
    )
    current = snapshot([(3, 4, "base"), (0, 1, "base"), (2, 99, "new")])
    catalog = SourceIdentityCatalog(SourceBindingRegistry([b, a]))
    projected = projection.project_source_prior(b.key, current, catalog)
    expected = view(b.prior_registry) | {uid(4): view(a.prior_registry)[uid(4)]}
    assert list(projected.identities) == [uid(1), uid(2), uid(3), uid(4)]
    assert view(projected) == expected
    assert plan_view(plan_source_changes(current, projected)) == expected_plan(
        current, expected
    )
    emptied = projection.project_source_prior(b.key, snapshot(), catalog)
    assert view(emptied) == view(b.prior_registry)
    assert [
        (item.stable_id, item.action.value)
        for item in plan_source_changes(snapshot(), emptied).items
    ] == [(uid(1), "void"), (uid(2), "void")]


@pytest.mark.parametrize(
    "mode,action,revision",
    [("same", "unchanged", None), ("edit", "edit", 8), ("voided", "edit", 9)],
)
def test_ip06_new_source_party_borrows_existing_revision(
    mode: str, action: str, revision: int | None
) -> None:
    base = snapshot([(3, 1, "base")])
    old = prior_from_snapshot(base).identities[uid(1)]
    if mode == "voided":
        old = state(3, 1, 8, voided=True)
    a, b = record(1404, [old]), record(1405, active=True)
    current = snapshot([(3, 1, "edit" if mode == "edit" else "base")])
    catalog = SourceIdentityCatalog(SourceBindingRegistry([b, a]))
    projected = projection.project_source_prior(b.key, current, catalog)
    assert projected.identities[uid(1)] is old
    plan = plan_source_changes(current, projected)
    assert [
        (item.stable_id, item.action.value, item.prior_revision, item.planned_revision)
        for item in plan.items
    ] == [(uid(1), action, old.latest_revision, revision)]
    assert plan_view(plan) == expected_plan(current, view(a.prior_registry))
    assert view(b.prior_registry) == {} and a.prior_registry.identities[uid(1)] is old


@pytest.mark.parametrize("sheet", range(4))
@pytest.mark.parametrize(
    "mode", ["insert", "unchanged", "edit", "void", "settled", "reactivate"]
)
def test_ip07_existing_member_transition_table(sheet: int, mode: str) -> None:
    base = snapshot([(sheet, 1, "base")])
    old = prior_from_snapshot(base).identities[uid(1)]
    if mode in {"settled", "reactivate"}:
        old = state(sheet, 1, 7, voided=True)
    b = record(1405, [] if mode == "insert" else [old], active=True)
    current = (
        snapshot()
        if mode in {"void", "settled"}
        else snapshot([(sheet, 1, "edit" if mode == "edit" else "base")])
    )
    result = projection.project_source_prior(
        b.key, current, SourceIdentityCatalog(SourceBindingRegistry([b]))
    )
    plan = plan_source_changes(current, result)
    assert plan_view(plan) == expected_plan(current, view(b.prior_registry))
    expected = {
        "insert": ("insert", 1),
        "unchanged": ("unchanged", None),
        "edit": ("edit", 8),
        "void": ("void", 8),
        "reactivate": ("edit", 8),
    }
    assert [(p.action.value, p.planned_revision) for p in plan.items] == (
        [] if mode == "settled" else [expected[mode]]
    )


@pytest.mark.parametrize(
    "home,target", [(a, b) for a in range(4) for b in range(4) if a != b]
)
@pytest.mark.parametrize("archived", [False, True])
def test_ip08_relocation_precedes_owner_check(
    home: int, target: int, archived: bool
) -> None:
    old = state(home, 1)
    a = record(1404, [old] if archived else [])
    b = record(1405, [] if archived else [old], active=True)
    catalog = SourceIdentityCatalog(SourceBindingRegistry([b, a]))
    with pytest.raises(SourceIdentityProjectionError) as caught:
        projection.project_source_prior(b.key, snapshot([(target, 1, "base")]), catalog)
    assert caught.value.reason is Reason.IDENTITY_RELOCATION


@pytest.mark.parametrize("sheet", range(3))
@pytest.mark.parametrize("mode", ["same", "edit", "voided"])
def test_ip08_foreign_transaction_is_never_reused(sheet: int, mode: str) -> None:
    base = snapshot([(sheet, 1, "base")])
    old = (
        prior_from_snapshot(base).identities[uid(1)]
        if mode != "voided"
        else state(sheet, 1, voided=True)
    )
    a, b = record(1404, [old]), record(1405, active=True)
    current = snapshot([(sheet, 1, "edit" if mode == "edit" else "base")])
    with pytest.raises(SourceIdentityProjectionError) as caught:
        projection.project_source_prior(
            b.key, current, SourceIdentityCatalog(SourceBindingRegistry([a, b]))
        )
    assert caught.value.reason is Reason.TRANSACTION_SOURCE_CONFLICT
    assert not b.prior_registry.identities


def test_ip09_independent_multiyear_membership_evolution() -> None:
    initial = snapshot([(0, 1, "old-transaction"), (3, 2, "base")])
    a_prior = prior_from_snapshot(initial, 7)
    active_a = record(1404, active=True, prior=a_prior)
    a_catalog = SourceIdentityCatalog(SourceBindingRegistry([active_a]))
    assert plan_view(
        plan_source_changes(
            initial, projection.project_source_prior(active_a.key, initial, a_catalog)
        )
    ) == expected_plan(initial, view(a_prior))
    archive_a = record(1404, prior=a_prior)
    archive_before = view(a_prior)
    b_model: Model = {}
    for label, expected_revision, action in [
        ("base", 7, "unchanged"),
        ("edit", 8, "edit"),
        (None, 9, "void"),
        ("base", 10, "edit"),
    ]:
        b = record(1405, active=True, prior=from_model(b_model))
        current = snapshot([] if label is None else [(3, 2, label)])
        comparison = dict(b_model)
        if label is not None and uid(2) not in comparison:
            comparison[uid(2)] = archive_before[uid(2)]
        catalog = SourceIdentityCatalog(SourceBindingRegistry([b, archive_a]))
        projected = projection.project_source_prior(b.key, current, catalog)
        assert view(projected) == comparison
        plan = plan_source_changes(current, projected)
        assert plan_view(plan) == expected_plan(current, comparison)
        assert [(p.stable_id, p.action.value) for p in plan.items] == [(uid(2), action)]
        assert (
            view(projection.project_source_prior(b.key, current, catalog)) == comparison
        )
        assert view(b.prior_registry) == b_model and view(a_prior) == archive_before
        b_model = committed_model(current, comparison)
        assert b_model[uid(2)][1] == expected_revision
        assert uid(1) not in b_model
    archive_b = record(1405, prior=from_model(b_model))
    c = record(1406, active=True)
    current = snapshot([(3, 2, "base")])
    catalog = SourceIdentityCatalog(SourceBindingRegistry([c, archive_b, archive_a]))
    projected = projection.project_source_prior(c.key, current, catalog)
    assert view(projected) == {uid(2): b_model[uid(2)]}
    assert plan_view(plan_source_changes(current, projected)) == expected_plan(
        current, b_model
    )
    assert projected.identities[uid(2)] is archive_b.prior_registry.identities[uid(2)]
    assert archive_a.prior_registry is a_prior and view(a_prior) == archive_before
    assert archive_a.final_file_sha256 == archive_b.final_file_sha256 == "d" * 64


@pytest.mark.parametrize("invalid", [None, True, 1, "SYNTHETIC-SECRET", [], {}])
def test_ip10_invalid_roots_are_safe(invalid: Any) -> None:
    b = record(1405, active=True)
    catalog = SourceIdentityCatalog(SourceBindingRegistry([b]))
    current = snapshot([(3, 1, "SECRET-RAW")])
    calls: list[Callable[[], object]] = [
        lambda: SourceIdentityCatalog(invalid),
        lambda: projection.project_source_prior(invalid, current, catalog),
        lambda: projection.project_source_prior(b.key, invalid, catalog),
        lambda: projection.project_source_prior(b.key, current, invalid),
    ]
    for call in calls:
        with pytest.raises(SourceIdentityProjectionError) as caught:
            call()
        assert caught.value.reason is Reason.INVALID_INPUT
        assert caught.value.args == ("Invalid projection input.",)
        assert "SECRET" not in str(caught.value) + repr(caught.value)


def test_ip10_reason_strictness_and_representations() -> None:
    class Foreign(StrEnum):
        INVALID_INPUT = "invalid_input"

    assert [(item.name, item.value) for item in Reason] == [
        (name, name.lower())
        for name in (
            "INVALID_INPUT",
            "INCONSISTENT_CATALOG",
            "SOURCE_NOT_ACTIVE",
            "IDENTITY_RELOCATION",
            "TRANSACTION_SOURCE_CONFLICT",
        )
    ]
    for bad in ["invalid_input", Foreign.INVALID_INPUT, None, True]:
        with pytest.raises(
            TypeError, match="^Invalid source identity projection reason.$"
        ):
            SourceIdentityProjectionError(cast(Any, bad))
    for reason in Reason:
        error = SourceIdentityProjectionError(reason)
        assert error.reason is reason and len(error.args) == 1
    b = record(1405, [state(3, 1)], active=True)
    catalog = SourceIdentityCatalog(SourceBindingRegistry([b]))
    rendered = repr(catalog)
    assert (
        rendered == "SourceIdentityCatalog(version='source-identity-projection.v1', "
        "identity_count=1)"
    )
    assert all(
        marker not in rendered for marker in (str(uid(1)), "1405", "a" * 64, "d" * 64)
    )


@pytest.mark.parametrize(
    "exception", [KeyboardInterrupt("SYNTHETIC"), SystemExit("SYNTHETIC")]
)
def test_ip10_cancellation_identity(
    monkeypatch: pytest.MonkeyPatch, exception: BaseException
) -> None:
    b = record(1405, active=True)
    catalog = SourceIdentityCatalog(SourceBindingRegistry([b]))

    def cancel(*args: Any) -> Any:
        raise exception

    monkeypatch.setattr(projection, "resolve_source_binding", cancel)
    with pytest.raises(type(exception)) as caught:
        projection.project_source_prior(b.key, snapshot(), catalog)
    assert caught.value is exception


def test_ip10_resolver_preserves_existing_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    b = record(1405, active=True)
    catalog = SourceIdentityCatalog(SourceBindingRegistry([b]))
    original = SourceBindingInputError("SYNTHETIC-PRIVATE-DIAGNOSTIC")
    cause = OSError("SYNTHETIC-PATH")
    original.__cause__ = cause

    def fail(*args: Any) -> Any:
        raise original

    monkeypatch.setattr(projection, "resolve_source_binding", fail)
    with pytest.raises(SourceIdentityProjectionError) as caught:
        projection.project_source_prior(b.key, snapshot(), catalog)
    assert caught.value.__cause__ is original and original.__cause__ is cause
    assert "SYNTHETIC" not in str(caught.value) + repr(caught.value) + str(
        caught.value.args
    )


@pytest.mark.parametrize("case", ["false-void", "revision-reset", "foreign-owner"])
def test_ip13_semantic_mutation_oracles(case: str) -> None:
    current = snapshot([(3 if case != "foreign-owner" else 0, 1, "base")])
    archive = record(1404, prior=prior_from_snapshot(current, 7))
    active = record(1405, active=True)
    catalog = SourceIdentityCatalog(SourceBindingRegistry([archive, active]))
    if case == "false-void":
        current = snapshot()
    try:
        projected = projection.project_source_prior(active.key, current, catalog)
        plan = plan_source_changes(current, projected)
        outcome: object = [
            (item.stable_id, item.action.value, item.planned_revision)
            for item in plan.items
        ]
    except SourceIdentityProjectionError as exc:
        outcome = ("error", exc.reason.value)
    expected: dict[str, object] = {
        "false-void": [],
        "revision-reset": [(uid(1), "unchanged", None)],
        "foreign-owner": ("error", "transaction_source_conflict"),
    }
    assert outcome == expected[case]


@pytest.mark.parametrize(
    "error", [KeyboardInterrupt("SYNTHETIC"), SystemExit("SYNTHETIC")]
)
def test_ip10_catalog_cancellation_is_not_wrapped(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    registry = SourceBindingRegistry([record(1405, active=True)])
    original = SourceBindingRegistry.__getattribute__

    def cancel(self: SourceBindingRegistry, name: str) -> Any:
        if self is registry and name == "records":
            raise error
        return original(self, name)

    monkeypatch.setattr(SourceBindingRegistry, "__getattribute__", cancel)
    with pytest.raises(type(error)) as caught:
        SourceIdentityCatalog(registry)
    assert caught.value is error


def test_ip15_purity_and_injected_write_control(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    current = snapshot([(3, 1, "base")])
    a = record(1404, prior=prior_from_snapshot(current))
    b = record(1405, active=True)
    registry = SourceBindingRegistry([a, b])
    canary = tmp_path / "SYNTHETIC-canary"
    for module, name in [
        (planner, "plan_source_changes"),
        (fiscal, "evaluate_source_fiscal_evidence"),
        (requiredness, "evaluate_source_requiredness"),
        (sqlite3, "connect"),
    ]:
        monkeypatch.setattr(module, name, forbidden)
    real = public.resolve_source_binding
    calls = []

    def count(*args: Any) -> Any:
        calls.append(args)
        return real(*args)

    monkeypatch.setattr(projection, "resolve_source_binding", count)
    with deny_side_effects():
        catalog = SourceIdentityCatalog(registry)
        assert calls == []
        projected = projection.project_source_prior(b.key, current, catalog)
    assert calls == [(b.key, registry)]
    assert projected.identities[uid(1)] is a.prior_registry.identities[uid(1)]

    def inject(*args: Any) -> Any:
        canary.write_text("SYNTHETIC")
        return real(*args)

    monkeypatch.setattr(projection, "resolve_source_binding", inject)
    with deny_side_effects(), pytest.raises(ForbiddenSideEffect):
        projection.project_source_prior(b.key, current, catalog)
    assert not canary.exists() and not b.prior_registry.identities
