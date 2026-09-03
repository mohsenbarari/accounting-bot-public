"""FE-01..14: independent fiscal-year observations, purity and composition.

Expected years are declared alongside synthetic inputs, never calculated by the
production parser/report. Existing Reader/Planner fixtures and tests are unchanged.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
import time
import uuid
from collections import Counter
from dataclasses import FrozenInstanceError, fields
from decimal import Decimal
from pathlib import Path
from typing import Any

import accounting_contracts as contracts
import accounting_contracts.source_change_plan as planner_module
import accounting_contracts.source_fiscal_evidence as fiscal_module
import accounting_contracts.source_requiredness as requiredness_module
import pytest
from accounting_contracts import (
    SOURCE_FISCAL_EVIDENCE_VERSION,
    ContractError,
    IdentityLifecycle,
    PlanAction,
    PriorIdentityState,
    SourceFiscalEvidenceInputError,
    SourceFiscalEvidenceReport,
    SourceFiscalRowEvidence,
    SourceFiscalYearCount,
    SourceSheetInput,
    ValidatedSourceWorkbookSnapshot,
    build_source_workbook_snapshot,
    evaluate_source_fiscal_evidence,
    evaluate_source_requiredness,
    plan_source_changes,
)
from accounting_local_agent.xlsx_source_reader import read_xlsx_source_snapshot
from hypothesis import given, settings
from hypothesis import strategies as st
from source_fiscal_evidence_import_probe import (
    PUBLIC_NAMES,
    ForbiddenSideEffect,
    deny_side_effects,
    forbidden,
)
from test_xlsx_source_reader import (
    SyntheticXlsxBuilder,
    _sample_business_parties_row_data,
    _sample_buy_sell_row_data,
    _sample_inventory_movements_row_data,
    _sample_receipts_payments_row_data,
)

# Independent approved sheet order and raw fixture fields (no product fiscal map).
SHEETS = ("خرید-فروش", "دریافت-پرداخت", "ورود-خروج", "لیست کسبه")
RAW_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "date_raw": None,
        "party_name_raw": "SYNTHETIC-PARTY",
        "transaction_type_raw": "?",
        "item_name_raw": "SYNTHETIC-ITEM",
        "quantity_raw": Decimal("0"),
        "unit_price_toman_raw": -1400,
        "discount_toman_raw": None,
        "notes_raw": None,
    },
    {
        "date_raw": None,
        "party_name_raw": "SYNTHETIC-PARTY",
        "entry_type_raw": "RS",
        "amount_toman_raw": Decimal("-1399"),
        "notes_raw": None,
        "account_code_raw": None,
        "customer_flag_raw": None,
    },
    {
        "date_raw": None,
        "party_name_raw": "SYNTHETIC-PARTY",
        "movement_type_raw": "?",
        "item_name_raw": "SYNTHETIC-ITEM",
        "quantity_raw": "0",
        "purity_raw": None,
        "notes_raw": None,
        "customer_flag_raw": None,
    },
    {"party_name_raw": "SYNTHETIC-PARTY", "phone_number_raw": "SYNTHETIC-CONTACT-2099"},
)
type FixtureRow = tuple[uuid.UUID, dict[str, Any], int | None]
type Fixture = dict[str, list[FixtureRow]]


def _uid(index: int, timestamp: int = 0) -> uuid.UUID:
    # Deterministic RFC UUIDv7; its timestamp has no connection to raw date years.
    return uuid.UUID(int=(timestamp << 80) | (7 << 76) | (2 << 62) | index)


def _fixture(years: list[list[int | None]], parties: int = 2) -> Fixture:
    fixture: Fixture = {sheet: [] for sheet in SHEETS}
    index = 1
    for position, dates in enumerate(years):
        for year in dates:
            raw = dict(RAW_TEMPLATES[position])
            raw["date_raw"] = None if year is None else f"{year:04d}/01/01"
            fixture[SHEETS[position]].append((_uid(index), raw, year))
            index += 1
    for _ in range(parties):
        fixture[SHEETS[3]].append((_uid(index), dict(RAW_TEMPLATES[3]), None))
        index += 1
    return fixture


def _snapshot(fixture: Fixture) -> ValidatedSourceWorkbookSnapshot:
    return build_source_workbook_snapshot(
        [
            SourceSheetInput(sheet, [(uid, raw) for uid, raw, _ in rows])
            for sheet, rows in fixture.items()
        ]
    )


def _assert_report(report: SourceFiscalEvidenceReport, fixture: Fixture) -> None:
    expected = [
        (sheet, uid, year)
        for sheet in SHEETS[:3]
        for uid, _, year in sorted(fixture[sheet], key=lambda row: row[0].bytes)
    ]
    expected_counts = Counter(year for _, _, year in expected if year is not None)
    assert [(r.sheet_name, r.stable_id, r.fiscal_year) for r in report.rows] == expected
    assert [(c.fiscal_year, c.row_count) for c in report.year_counts] == sorted(
        expected_counts.items()
    )
    assert report.observed_years == tuple(sorted(expected_counts))
    assert report.transaction_row_count == len(expected)
    assert report.dated_row_count == sum(expected_counts.values())
    assert report.undated_row_count == sum(year is None for _, _, year in expected)
    assert report.non_transaction_row_count == len(fixture[SHEETS[3]])
    assert report.dated_row_count + report.undated_row_count == len(expected)
    assert len(expected) + len(fixture[SHEETS[3]]) == report.snapshot.total_row_count


def _capture(snapshot: ValidatedSourceWorkbookSnapshot) -> list[tuple[Any, ...]]:
    return [
        (
            sheet,
            sheet.rows,
            sheet.sheet_snapshot_hash,
            row,
            row.stable_id,
            row.raw_values,
            tuple(row.raw_values.items()),
            row.source_hash,
        )
        for sheet in snapshot.sheets.values()
        for row in sheet.rows
    ]


def _assert_retained(
    snapshot: ValidatedSourceWorkbookSnapshot, before: list[tuple[Any, ...]]
) -> None:
    after = _capture(snapshot)
    assert len(before) == len(after)
    for old, new in zip(before, after, strict=True):
        for index in (0, 1, 2, 3, 4, 5, 7):
            assert old[index] is new[index]
        assert old[6] == new[6]
        for (_, old_value), (_, new_value) in zip(old[6], new[6], strict=True):
            assert old_value is new_value


def test_fe01_public_contract_and_guarded_fresh_import(tmp_path: Path) -> None:
    assert SOURCE_FISCAL_EVIDENCE_VERSION == "source-fiscal-evidence.v1"
    assert issubclass(SourceFiscalEvidenceInputError, ContractError)
    assert set(fiscal_module.__all__) == set(PUBLIC_NAMES)
    assert {
        n
        for n in contracts.__all__
        if "Fiscal" in n or "FISCAL" in n or n == "evaluate_source_fiscal_evidence"
    } == set(PUBLIC_NAMES)
    for entry in (SourceFiscalEvidenceReport, evaluate_source_fiscal_evidence):
        assert tuple(inspect.signature(entry).parameters) == ("snapshot",)
    assert tuple(inspect.signature(SourceFiscalRowEvidence).parameters) == (
        "sheet_name",
        "stable_id",
        "fiscal_year",
    )
    assert tuple(inspect.signature(SourceFiscalYearCount).parameters) == (
        "fiscal_year",
        "row_count",
    )
    parent_exports = {name: getattr(contracts, name) for name in PUBLIC_NAMES}
    probe = Path(__file__).with_name("source_fiscal_evidence_import_probe.py")
    canary = tmp_path / "forbidden-canary.txt"
    try:
        normal = subprocess.run(
            [sys.executable, "-B", str(probe), "normal", str(canary)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert normal.returncode == 0, normal.stderr
        assert normal.stdout.splitlines() == [
            "IMPORT_ENTERED",
            "IMPORT_EXECUTED",
            "PROBE_OK",
        ]
        negative = subprocess.run(
            [sys.executable, "-B", str(probe), "inject_write", str(canary)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert negative.returncode == 73, negative.stderr
        assert negative.stdout.splitlines() == [
            "IMPORT_ENTERED",
            "IMPORT_REJECTED_BY_GUARD",
        ]
        assert not canary.exists()
    finally:
        for name, value in parent_exports.items():
            assert getattr(contracts, name) is value
            assert getattr(fiscal_module, name) is value
        assert sys.modules[fiscal_module.__name__] is fiscal_module


def test_fe02_one_year_all_transaction_sheets_both_entrypoints() -> None:
    fixture = _fixture([[1403, 1403], [1403], [1403]])
    snapshot = _snapshot(fixture)
    for entry in (SourceFiscalEvidenceReport, evaluate_source_fiscal_evidence):
        report = entry(snapshot)
        assert report.snapshot is snapshot
        _assert_report(report, fixture)
        assert report.year_counts == (SourceFiscalYearCount(1403, 4),)


def test_fe03_mixed_years_complete_ordered_evidence() -> None:
    fixture = _fixture([[1405, 1399], [1403, 1405], [1399, 1400]])
    report = evaluate_source_fiscal_evidence(_snapshot(fixture))
    _assert_report(report, fixture)
    assert report.observed_years == (1399, 1400, 1403, 1405)
    assert {f.name for f in fields(report)} == {
        "snapshot",
        "rows",
        "year_counts",
        "observed_years",
        "transaction_row_count",
        "dated_row_count",
        "undated_row_count",
        "non_transaction_row_count",
    }


@pytest.mark.parametrize(
    "years",
    [
        [[None, None], [None], [None]],
        [[None, 1403], [1405], [None]],
    ],
)
def test_fe04_missing_dates_remain_independent_of_requiredness(
    years: list[list[int | None]],
) -> None:
    fixture = _fixture(years)
    snapshot = _snapshot(fixture)
    report = evaluate_source_fiscal_evidence(snapshot)
    _assert_report(report, fixture)
    required = evaluate_source_requiredness(snapshot)
    assert not required.passes_requiredness
    assert {
        (i.sheet_name, i.stable_id)
        for i in required.issues
        if i.field_name == "date_raw"
    } == {
        (sheet, uid)
        for sheet in SHEETS[:3]
        for uid, _, year in fixture[sheet]
        if year is None
    }
    assert report.snapshot is required.snapshot is snapshot


def test_fe05_empty_party_only_and_complete_transaction_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_fixture = _fixture([[1399], [1403], [None]])
    previous = _snapshot(previous_fixture)
    prior = [
        PriorIdentityState(
            stable_id=r.stable_id,
            canonical_uuid=r.canonical_uuid,
            home_sheet=r.sheet_name,
            latest_revision=1,
            lifecycle=IdentityLifecycle.ACTIVE,
            source_hash=r.source_hash,
        )
        for s in previous.sheets.values()
        for r in s.rows
    ]
    for party_count in (0, 2):
        fixture = _fixture([[], [], []], parties=party_count)
        fixture[SHEETS[3]] = previous_fixture[SHEETS[3]][:party_count]
        snapshot = _snapshot(fixture)
        planned_before = plan_source_changes(snapshot, prior)
        with monkeypatch.context() as guard:
            guard.setattr(planner_module, "plan_source_changes", forbidden)
            guard.setattr(contracts, "plan_source_changes", forbidden)
            report = SourceFiscalEvidenceReport(snapshot)
        _assert_report(report, fixture)
        assert report.snapshot is snapshot
        assert report.rows == ()
        assert report.year_counts == ()
        assert report.observed_years == ()
        assert plan_source_changes(snapshot, prior) == planned_before
        assert planned_before.total_counts.void_count == (5 if party_count == 0 else 3)


@pytest.mark.parametrize(
    ("raw_date", "expected_year"),
    [
        ("1403/01/01", 1403),
        ("۱۴۰۳/۰۱/۰۱", 1403),
        ("١٤٠٣/٠١/٠١", 1403),
        ("  1403-01-01\t", 1403),
        ("\n۱۳۹۹/۱۲/۳۰ ", 1399),
        ("1403/12/30", 1403),
        ("1404/01/01", 1404),
        ("1402/12/29", 1402),
    ],
)
def test_fe06_canonical_date_vectors(raw_date: str, expected_year: int) -> None:
    fixture = _fixture([[expected_year], [expected_year], [expected_year]])
    for sheet in SHEETS[:3]:
        fixture[sheet][0][1]["date_raw"] = raw_date
    _assert_report(evaluate_source_fiscal_evidence(_snapshot(fixture)), fixture)


@pytest.mark.parametrize(
    "raw_date", ["1402/12/30", "1403/13/01", "", "2024-03-20T00:00:00", "bad-date"]
)
def test_fe06_invalid_dates_fail_upstream(raw_date: str) -> None:
    fixture = _fixture([[1403], [], []])
    fixture[SHEETS[0]][0][1]["date_raw"] = raw_date
    with pytest.raises(ContractError):
        _snapshot(fixture)


def test_fe07_non_date_fields_and_uuid_time_do_not_select_year() -> None:
    fixture = _fixture([[1399, None], [1403], [None]])
    for sheet in SHEETS:
        for _identity, raw, _ in fixture[sheet]:
            raw["party_name_raw"] = "SYNTHETIC-archive-2099-1405.xlsx"
            if "notes_raw" in raw:
                raw["notes_raw"] = "انتقال مانده افتتاحیه ۱۴۰۵ / 2099 / 2026"
    original = evaluate_source_fiscal_evidence(_snapshot(fixture))
    _assert_report(original, fixture)
    changed: Fixture = {
        sheet: [
            (_uid(uid.int & ((1 << 62) - 1), (1 << 48) - 1), raw, year)
            for uid, raw, year in rows
        ]
        for sheet, rows in fixture.items()
    }
    report = evaluate_source_fiscal_evidence(_snapshot(changed))
    _assert_report(report, changed)
    assert report.observed_years == original.observed_years == (1399, 1403)
    assert report.year_counts == original.year_counts
    assert report.undated_row_count == 2
    assert all(
        r.stable_id not in {old.stable_id for old in original.rows} for r in report.rows
    )


@pytest.mark.parametrize(
    "sheet", ["unknown-SYNTHETIC-MARKER", SHEETS[3], None, True, 1, []]
)
def test_fe08_invalid_metadata_sheets(sheet: Any) -> None:
    with pytest.raises(
        SourceFiscalEvidenceInputError, match="^Invalid transaction sheet\\.$"
    ):
        SourceFiscalRowEvidence(sheet, _uid(1), 1403)


@pytest.mark.parametrize(
    "identity",
    [
        None,
        True,
        "SYNTHETIC-ID",
        str(_uid(1)),
        uuid.UUID(int=0),
        uuid.UUID("00000000-0000-4000-8000-000000000001"),
    ],
)
def test_fe08_invalid_metadata_identities(identity: Any) -> None:
    with pytest.raises(SourceFiscalEvidenceInputError, match="^Invalid stable ID\\.$"):
        SourceFiscalRowEvidence(SHEETS[0], identity, 1403)


@pytest.mark.parametrize(
    "year",
    [True, False, "1403", "SYNTHETIC-MARKER", 1403.0, Decimal("1403"), -1, 0, 10000],
)
def test_fe08_invalid_metadata_years(year: Any) -> None:
    for construct in (
        lambda: SourceFiscalRowEvidence(SHEETS[0], _uid(1), year),
        lambda: SourceFiscalYearCount(year, 1),
    ):
        with pytest.raises(
            SourceFiscalEvidenceInputError, match="^Invalid fiscal year\\.$"
        ):
            construct()


@pytest.mark.parametrize("count", [None, True, False, "1", 1.0, 0, -1])
def test_fe08_invalid_metadata_counts(count: Any) -> None:
    with pytest.raises(
        SourceFiscalEvidenceInputError, match="^Invalid fiscal year row count\\.$"
    ):
        SourceFiscalYearCount(1403, count)


def test_fe08_valid_metadata_types_and_no_current_year_bounds() -> None:
    uid = _uid(1)
    for year in (1, 1399, 1403, 1405, 1500, 9377):
        row = SourceFiscalRowEvidence(SHEETS[0], uid, year)
        count = SourceFiscalYearCount(year, 2)
        assert row.stable_id is uid
        assert row.sheet_name is SHEETS[0]
        assert row.fiscal_year is year
        assert type(count.fiscal_year) is type(count.row_count) is int
        assert count.row_count == 2
    assert SourceFiscalRowEvidence(SHEETS[0], uid, None).fiscal_year is None
    with pytest.raises(
        SourceFiscalEvidenceInputError, match="^Invalid fiscal year\\.$"
    ):
        SourceFiscalYearCount(None, 1)  # type: ignore[arg-type]


def test_fe09_computed_constructor_immutability_and_private_repr() -> None:
    fixture = _fixture([[1403, None], [1399], [None]])
    marker = "SYNTHETIC-RAW-PRIVATE-MARKER"
    for rows in fixture.values():
        for _, raw, _ in rows:
            raw["party_name_raw"] = marker
    snapshot = _snapshot(fixture)
    report = SourceFiscalEvidenceReport(snapshot)
    _assert_report(report, fixture)
    assert report.snapshot is snapshot
    assert "snapshot=" not in repr(report)
    assert marker not in repr(report) + str(report) + repr(report.rows)
    for field in (
        "rows",
        "year_counts",
        "observed_years",
        "transaction_row_count",
        "dated_row_count",
        "undated_row_count",
        "non_transaction_row_count",
        "commit_allowed",
        "fiscal_year",
    ):
        with pytest.raises(TypeError):
            SourceFiscalEvidenceReport(snapshot, **{field: ()})
    for obj, attr in (
        (report, "rows"),
        (report.rows[0], "fiscal_year"),
        (report.year_counts[0], "row_count"),
    ):
        assert not hasattr(obj, "__dict__")
        with pytest.raises(FrozenInstanceError):
            setattr(obj, attr, 7)
    with pytest.raises(TypeError):
        report.rows[0] = report.rows[0]  # type: ignore[index]
    for bad in (None, True, marker, object(), snapshot.sheets):
        for entry in (SourceFiscalEvidenceReport, evaluate_source_fiscal_evidence):
            with pytest.raises(SourceFiscalEvidenceInputError) as error:
                entry(bad)  # type: ignore[arg-type]
            assert str(error.value) == "Invalid fiscal evidence snapshot."
            assert marker not in repr(error.value)


@settings(max_examples=35, deadline=None)
@given(
    st.lists(
        st.lists(st.sampled_from([None, 1399, 1403, 1405]), min_size=2, max_size=6),
        min_size=3,
        max_size=3,
    )
)
def test_fe10_property_oracle_real_permutations_and_date_transition(
    years: list[list[int | None]],
) -> None:
    fixture = _fixture(years)
    permuted: Fixture = {
        sheet: [
            (uid, dict(reversed(tuple(raw.items()))), year)
            for uid, raw, year in reversed(fixture[sheet])
        ]
        for sheet in reversed(SHEETS)
    }
    assert tuple(permuted) != tuple(fixture)
    for sheet in SHEETS:
        assert [uid for uid, _, _ in permuted[sheet]] != [
            uid for uid, _, _ in fixture[sheet]
        ]
        original = {uid: raw for uid, raw, _ in fixture[sheet]}
        for uid, raw, _ in permuted[sheet]:
            assert raw == original[uid]
            assert tuple(raw) != tuple(original[uid])
    first = evaluate_source_fiscal_evidence(_snapshot(fixture))
    other = evaluate_source_fiscal_evidence(_snapshot(permuted))
    _assert_report(first, fixture)
    _assert_report(other, fixture)
    assert first.rows == other.rows
    assert first.year_counts == other.year_counts
    uid, raw, old_year = fixture[SHEETS[1]][0]
    new_year = {None: 1399, 1399: 1405, 1405: None, 1403: None}[old_year]
    replacement = dict(raw, date_raw=None if new_year is None else f"{new_year}/01/01")
    fixture[SHEETS[1]][0] = (uid, replacement, new_year)
    changed = evaluate_source_fiscal_evidence(_snapshot(fixture))
    _assert_report(changed, fixture)
    differences = [
        (a, b) for a, b in zip(first.rows, changed.rows, strict=True) if a != b
    ]
    assert len(differences) == 1
    assert differences[0][0].stable_id is differences[0][1].stable_id is uid
    assert (differences[0][0].fiscal_year, differences[0][1].fiscal_year) == (
        old_year,
        new_year,
    )
    assert first.snapshot.total_row_count == changed.snapshot.total_row_count


def test_fe11_purity_repeatability_identity_and_guard_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(_fixture([[1399, None], [1403], [1405]]))
    before = _capture(snapshot)
    canary = tmp_path / "forbidden-evaluation.txt"
    with monkeypatch.context() as patch:
        for module, name in (
            (planner_module, "compute_source_hash"),
            (planner_module, "compute_sheet_snapshot_hash"),
            (planner_module, "plan_source_changes"),
            (requiredness_module, "evaluate_source_requiredness"),
            (contracts, "plan_source_changes"),
            (contracts, "evaluate_source_requiredness"),
        ):
            patch.setattr(module, name, forbidden)
        with deny_side_effects():
            first = SourceFiscalEvidenceReport(snapshot)
            second = evaluate_source_fiscal_evidence(snapshot)
            assert first == second
            assert first.snapshot is second.snapshot is snapshot
            _assert_retained(snapshot, before)
            identities = {
                r.stable_id: r.stable_id
                for s in snapshot.sheets.values()
                for r in s.rows
            }
            assert all(r.stable_id is identities[r.stable_id] for r in first.rows)
            try:
                canary.write_text("synthetic guard canary")
            except ForbiddenSideEffect as exc:
                assert str(exc) == "Fiscal evidence side-effect guard"
            else:
                raise AssertionError("Purity guard did not intercept the write")
    assert not canary.exists()


@pytest.mark.parametrize(
    "failure",
    [KeyboardInterrupt("synthetic cancel"), RuntimeError("synthetic failure")],
)
def test_fe11_unexpected_parser_failure_propagates_by_identity(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    snapshot = _snapshot(_fixture([[1403], [], []]))

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise failure

    monkeypatch.setattr(fiscal_module, "parse_canonical_jalali_date", fail)
    for entry in (SourceFiscalEvidenceReport, evaluate_source_fiscal_evidence):
        with pytest.raises(type(failure)) as raised:
            entry(snapshot)
        assert raised.value is failure


def test_fe12_synthetic_15000_rows_complete_oracle_and_retention() -> None:
    started = time.perf_counter()
    dates = [None, 1399, 1403, 1405] * 1125
    fixture = _fixture([dates, dates, dates], parties=1500)
    snapshot = _snapshot(fixture)
    construction = time.perf_counter() - started
    assert snapshot.total_row_count == 15000
    before = _capture(snapshot)
    started = time.perf_counter()
    report = evaluate_source_fiscal_evidence(snapshot)
    evaluation = time.perf_counter() - started
    _assert_report(report, fixture)
    _assert_retained(snapshot, before)
    assert report.snapshot is snapshot
    print(
        f"FE-12 fixture_seconds={construction:.6f} evaluation_seconds={evaluation:.6f} "
        f"transaction_rows={report.transaction_row_count} "
        f"parties={report.non_transaction_row_count}"
    )


def test_fe13_xlsx_generations_requiredness_and_independent_planner(
    tmp_path: Path,
) -> None:
    ids = [_uid(n) for n in range(1, 6)]
    row1 = _sample_buy_sell_row_data(ids[0], 2)
    row1["B"] = "1403/01/01"
    row2 = _sample_buy_sell_row_data(ids[1], 3)
    row2["B"] = {"f": '"1399/01/01"', "v": "1399/01/01"}
    receipts = _sample_receipts_payments_row_data(ids[2], 2)
    receipts.pop("B")
    inventory = _sample_inventory_movements_row_data(ids[3], 2)
    inventory["B"] = "1405/01/01"
    party = _sample_business_parties_row_data(ids[4], 2)
    fixture = _fixture([[1403, None], [None], [1405]], parties=1)
    prior: list[PriorIdentityState] = []
    first_rows: tuple[SourceFiscalRowEvidence, ...] = ()
    for generation in range(1, 5):
        first, second = dict(row1), dict(row2)
        if generation >= 2:
            first["__row_num__"], second["__row_num__"] = 3, 2
        if generation >= 3:
            second["B"] = {"f": '"1500/01/01"', "v": "1500/01/01"}
            first["I"] = {"f": "SUM(F2:F3)", "v": "999"}
        if generation == 4:
            first["B"] = "1399/01/01"
            uid, raw, _ = fixture[SHEETS[0]][0]
            fixture[SHEETS[0]][0] = (uid, dict(raw, date_raw="1399/01/01"), 1399)
        builder = SyntheticXlsxBuilder()
        builder.add_sheet_rows(
            SHEETS[0], [second, first] if generation >= 2 else [first, second]
        )
        builder.add_sheet_rows(SHEETS[1], [receipts])
        builder.add_sheet_rows(SHEETS[2], [inventory])
        builder.add_sheet_rows(SHEETS[3], [party])
        path = tmp_path / f"SYNTHETIC-archive-2099-generation-{generation}.xlsx"
        original_bytes = builder.build_bytes()
        path.write_bytes(original_bytes)
        result = read_xlsx_source_snapshot(path)
        snapshot = result.snapshot
        before = _capture(snapshot)
        planned_before = plan_source_changes(snapshot, prior)
        report = evaluate_source_fiscal_evidence(snapshot)
        _assert_report(report, fixture)
        _assert_retained(snapshot, before)
        assert path.read_bytes() == original_bytes
        assert plan_source_changes(snapshot, prior) == planned_before
        missing = evaluate_source_requiredness(snapshot)
        assert {(i.stable_id, i.field_name) for i in missing.issues} == {
            (ids[1], "date_raw"),
            (ids[2], "date_raw"),
        }
        assert result.locations_by_uuid[ids[0]].physical_row_number == (
            2 if generation == 1 else 3
        )
        assert result.locations_by_uuid[ids[1]].physical_row_number == (
            3 if generation == 1 else 2
        )
        if generation == 1:
            assert planned_before.total_counts.insert_count == 5
            first_rows = report.rows
            prior = [
                PriorIdentityState(
                    stable_id=r.stable_id,
                    canonical_uuid=r.canonical_uuid,
                    home_sheet=r.sheet_name,
                    latest_revision=1,
                    lifecycle=IdentityLifecycle.ACTIVE,
                    source_hash=r.source_hash,
                )
                for s in snapshot.sheets.values()
                for r in s.rows
            ]
        elif generation in (2, 3):
            assert report.rows == first_rows
            assert planned_before.total_counts.unchanged_count == 5
            assert all(
                item.action is PlanAction.UNCHANGED for item in planned_before.items
            )
        else:
            assert planned_before.total_counts.edit_count == 1
            assert planned_before.total_counts.unchanged_count == 4
            assert [
                item.stable_id
                for item in planned_before.items
                if item.action is PlanAction.EDIT
            ] == [ids[0]]
            assert [
                (a.stable_id, a.fiscal_year, b.fiscal_year)
                for a, b in zip(first_rows, report.rows, strict=True)
                if a != b
            ] == [(ids[0], 1403, 1399)]
    empty_builder = SyntheticXlsxBuilder()
    for sheet in SHEETS:
        empty_builder.add_sheet_rows(sheet, [])
    path = tmp_path / "SYNTHETIC-empty-2099.xlsx"
    empty_bytes = empty_builder.build_bytes()
    path.write_bytes(empty_bytes)
    empty = read_xlsx_source_snapshot(path).snapshot
    plan_before = plan_source_changes(empty, prior)
    _assert_report(evaluate_source_fiscal_evidence(empty), _fixture([[], [], []], 0))
    assert plan_source_changes(empty, prior) == plan_before
    assert plan_before.total_counts.void_count == 5
    assert path.read_bytes() == empty_bytes
