"""Pure fiscal-year observations from a complete raw source snapshot (ADR-0013)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from accounting_contracts.canonical_date import (
    CanonicalDateError,
    parse_canonical_jalali_date,
)
from accounting_contracts.raw_input_contracts import (
    BUSINESS_PARTIES_CONTRACT,
    BUY_SELL_CONTRACT,
    INVENTORY_MOVEMENTS_CONTRACT,
    RAW_CONTRACT_REGISTRY,
    RECEIPTS_PAYMENTS_CONTRACT,
    ContractError,
)
from accounting_contracts.source_change_plan import ValidatedSourceWorkbookSnapshot

SOURCE_FISCAL_EVIDENCE_VERSION = "source-fiscal-evidence.v1"

_TRANSACTION_SHEETS = frozenset(
    (
        BUY_SELL_CONTRACT.sheet_name,
        RECEIPTS_PAYMENTS_CONTRACT.sheet_name,
        INVENTORY_MOVEMENTS_CONTRACT.sheet_name,
    )
)

__all__ = [
    "SOURCE_FISCAL_EVIDENCE_VERSION",
    "SourceFiscalEvidenceInputError",
    "SourceFiscalEvidenceReport",
    "SourceFiscalRowEvidence",
    "SourceFiscalYearCount",
    "evaluate_source_fiscal_evidence",
]


class SourceFiscalEvidenceInputError(ContractError):
    """Invalid public fiscal-evidence input or metadata."""


def _validate_year(year: int) -> None:
    if type(year) is not int:
        raise SourceFiscalEvidenceInputError("Invalid fiscal year.")
    try:
        parsed = parse_canonical_jalali_date(f"{year:04d}/01/01")
    except (CanonicalDateError, ValueError, OverflowError):
        raise SourceFiscalEvidenceInputError("Invalid fiscal year.") from None
    if parsed is None or parsed.fiscal_year != year:
        raise SourceFiscalEvidenceInputError("Invalid fiscal year.")


@dataclass(frozen=True, slots=True)
class SourceFiscalRowEvidence:
    """One transaction identity and its observed year, or an absent date."""

    sheet_name: str
    stable_id: uuid.UUID
    fiscal_year: int | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sheet_name, str)
            or self.sheet_name not in _TRANSACTION_SHEETS
        ):
            raise SourceFiscalEvidenceInputError("Invalid transaction sheet.")
        if not isinstance(self.stable_id, uuid.UUID) or self.stable_id.version != 7:
            raise SourceFiscalEvidenceInputError("Invalid stable ID.")
        if self.fiscal_year is not None:
            _validate_year(self.fiscal_year)


@dataclass(frozen=True, slots=True)
class SourceFiscalYearCount:
    """Positive count for one observed Jalali fiscal year."""

    fiscal_year: int
    row_count: int

    def __post_init__(self) -> None:
        _validate_year(self.fiscal_year)
        if type(self.row_count) is not int or self.row_count <= 0:
            raise SourceFiscalEvidenceInputError("Invalid fiscal year row count.")


@dataclass(frozen=True, slots=True, init=False)
class SourceFiscalEvidenceReport:
    """Computed observations, without operational-year selection or admission."""

    snapshot: ValidatedSourceWorkbookSnapshot = field(repr=False)
    rows: tuple[SourceFiscalRowEvidence, ...]
    year_counts: tuple[SourceFiscalYearCount, ...]
    observed_years: tuple[int, ...]
    transaction_row_count: int
    dated_row_count: int
    undated_row_count: int
    non_transaction_row_count: int

    def __init__(self, snapshot: ValidatedSourceWorkbookSnapshot) -> None:
        if not isinstance(snapshot, ValidatedSourceWorkbookSnapshot):
            raise SourceFiscalEvidenceInputError("Invalid fiscal evidence snapshot.")

        rows: list[SourceFiscalRowEvidence] = []
        counts: dict[int, int] = {}
        non_transaction_count = 0
        for sheet_name in RAW_CONTRACT_REGISTRY.sheets:
            sheet = snapshot.sheets[sheet_name]
            if sheet_name == BUSINESS_PARTIES_CONTRACT.sheet_name:
                non_transaction_count = len(sheet.rows)
                continue
            for row in sorted(sheet.rows, key=lambda item: item.stable_id.bytes):
                raw_date = row.raw_values["date_raw"]
                year = None
                if raw_date is not None:
                    parsed = parse_canonical_jalali_date(raw_date)
                    if parsed is None:
                        raise RuntimeError("Non-null source date produced no evidence.")
                    year = parsed.fiscal_year
                    counts[year] = counts.get(year, 0) + 1
                rows.append(SourceFiscalRowEvidence(sheet_name, row.stable_id, year))

        year_counts = tuple(
            SourceFiscalYearCount(year, counts[year]) for year in sorted(counts)
        )
        dated_count = sum(item.row_count for item in year_counts)
        object.__setattr__(self, "snapshot", snapshot)
        object.__setattr__(self, "rows", tuple(rows))
        object.__setattr__(self, "year_counts", year_counts)
        object.__setattr__(
            self, "observed_years", tuple(item.fiscal_year for item in year_counts)
        )
        object.__setattr__(self, "transaction_row_count", len(rows))
        object.__setattr__(self, "dated_row_count", dated_count)
        object.__setattr__(self, "undated_row_count", len(rows) - dated_count)
        object.__setattr__(self, "non_transaction_row_count", non_transaction_count)


def evaluate_source_fiscal_evidence(
    snapshot: ValidatedSourceWorkbookSnapshot,
) -> SourceFiscalEvidenceReport:
    """Observe source fiscal years while retaining the complete snapshot."""
    return SourceFiscalEvidenceReport(snapshot)
