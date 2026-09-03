"""Required-field preflight for complete raw source snapshots.

Implements ADR-0012 for validating essential required raw input fields across the
four approved Excel sheets ('خرید-فروش', 'دریافت-پرداخت', 'ورود-خروج', 'لیست کسبه')
without mutating source values, resolving business entities, or committing imports.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from accounting_contracts.raw_input_contracts import (
    RAW_CONTRACT_REGISTRY,
    ContractError,
    ValueKind,
)
from accounting_contracts.source_change_plan import (
    ValidatedSourceWorkbookSnapshot,
)

SOURCE_REQUIREDNESS_VERSION: str = "source-requiredness.v1"


class SourceRequirednessInputError(ContractError):
    """Raised when source requiredness input or issue metadata is invalid."""


class SourceRequirednessIssueReason(StrEnum):
    """Reason for a required-field validation issue."""

    MISSING_VALUE = "missing_value"
    BLANK_TEXT = "blank_text"


# Authoritative required fields per sheet under ADR-0012 in exact raw-column order.
REQUIRED_FIELDS_BY_SHEET: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
    {
        "خرید-فروش": (
            "date_raw",
            "party_name_raw",
            "transaction_type_raw",
            "item_name_raw",
            "quantity_raw",
            "unit_price_toman_raw",
        ),
        "دریافت-پرداخت": (
            "date_raw",
            "party_name_raw",
            "entry_type_raw",
            "amount_toman_raw",
        ),
        "ورود-خروج": (
            "date_raw",
            "party_name_raw",
            "movement_type_raw",
            "item_name_raw",
            "quantity_raw",
        ),
        "لیست کسبه": ("party_name_raw",),
    }
)

# Set of required text fields per sheet for BLANK_TEXT validation.
_REQUIRED_TEXT_FIELDS_BY_SHEET: MappingProxyType[str, frozenset[str]] = (
    MappingProxyType(
        {
            sheet_name: frozenset(
                col.field_name
                for col in RAW_CONTRACT_REGISTRY.sheets[sheet_name].raw_columns
                if col.field_name in REQUIRED_FIELDS_BY_SHEET[sheet_name]
                and col.value_kind == ValueKind.RAW_TEXT
            )
            for sheet_name in REQUIRED_FIELDS_BY_SHEET
        }
    )
)


@dataclass(frozen=True, slots=True)
class SourceRequirednessIssue:
    """Metadata describing a single missing or blank required field in a source row."""

    sheet_name: str
    stable_id: uuid.UUID
    field_name: str
    reason: SourceRequirednessIssueReason

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sheet_name, str)
            or self.sheet_name not in REQUIRED_FIELDS_BY_SHEET
        ):
            raise SourceRequirednessInputError(
                "Invalid sheet name for requiredness issue."
            )

        if (
            isinstance(self.stable_id, bool)
            or not isinstance(self.stable_id, uuid.UUID)
            or self.stable_id.version != 7
        ):
            raise SourceRequirednessInputError(
                "Invalid stable ID for requiredness issue."
            )

        required_fields = REQUIRED_FIELDS_BY_SHEET[self.sheet_name]
        if (
            not isinstance(self.field_name, str)
            or self.field_name not in required_fields
        ):
            raise SourceRequirednessInputError(
                "Invalid required field name for requiredness issue."
            )

        if type(self.reason) is not SourceRequirednessIssueReason:
            raise SourceRequirednessInputError("Invalid reason for requiredness issue.")

        if self.reason == SourceRequirednessIssueReason.BLANK_TEXT:
            if self.field_name not in _REQUIRED_TEXT_FIELDS_BY_SHEET[self.sheet_name]:
                raise SourceRequirednessInputError(
                    "BLANK_TEXT reason is only valid for text fields."
                )


@dataclass(frozen=True, slots=True)
class SourceRequirednessReport:
    """Immutable report evaluating required fields across a full source snapshot."""

    snapshot: ValidatedSourceWorkbookSnapshot
    issues: tuple[SourceRequirednessIssue, ...]
    checked_row_count: int
    failed_row_count: int
    issue_count: int
    passes_requiredness: bool

    def __init__(
        self,
        snapshot: ValidatedSourceWorkbookSnapshot,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if args or kwargs:
            raise SourceRequirednessInputError(
                "SourceRequirednessReport only accepts a single snapshot argument."
            )
        if not isinstance(snapshot, ValidatedSourceWorkbookSnapshot):
            raise SourceRequirednessInputError(
                "Invalid snapshot type provided to SourceRequirednessReport."
            )

        computed_issues: list[SourceRequirednessIssue] = []
        failing_uuids: set[uuid.UUID] = set()

        # Iterate sheets in authoritative registry order
        for sheet_name in RAW_CONTRACT_REGISTRY.sheets:
            sheet_snapshot = snapshot.sheets.get(sheet_name)
            if sheet_snapshot is None:
                raise SourceRequirednessInputError(
                    "Workbook snapshot is missing required sheets."
                )

            req_fields = REQUIRED_FIELDS_BY_SHEET[sheet_name]
            text_fields = _REQUIRED_TEXT_FIELDS_BY_SHEET[sheet_name]

            # Rows in sheet_snapshot are sorted by stable_id.bytes by snapshot invariant
            for row in sheet_snapshot.rows:
                row_has_issue = False
                raw_vals = row.raw_values

                for field in req_fields:
                    val = raw_vals.get(field)
                    if val is None:
                        computed_issues.append(
                            SourceRequirednessIssue(
                                sheet_name=sheet_name,
                                stable_id=row.stable_id,
                                field_name=field,
                                reason=SourceRequirednessIssueReason.MISSING_VALUE,
                            )
                        )
                        row_has_issue = True
                    elif (
                        field in text_fields
                        and isinstance(val, str)
                        and val.strip() == ""
                    ):
                        computed_issues.append(
                            SourceRequirednessIssue(
                                sheet_name=sheet_name,
                                stable_id=row.stable_id,
                                field_name=field,
                                reason=SourceRequirednessIssueReason.BLANK_TEXT,
                            )
                        )
                        row_has_issue = True

                if row_has_issue:
                    failing_uuids.add(row.stable_id)

        issues_tuple = tuple(computed_issues)
        object.__setattr__(self, "snapshot", snapshot)
        object.__setattr__(self, "issues", issues_tuple)
        object.__setattr__(self, "checked_row_count", snapshot.total_row_count)
        object.__setattr__(self, "failed_row_count", len(failing_uuids))
        object.__setattr__(self, "issue_count", len(issues_tuple))
        object.__setattr__(self, "passes_requiredness", len(issues_tuple) == 0)

    def __repr__(self) -> str:
        return (
            f"SourceRequirednessReport(checked_row_count={self.checked_row_count}, "
            f"failed_row_count={self.failed_row_count}, "
            f"issue_count={self.issue_count}, "
            f"passes_requiredness={self.passes_requiredness}, "
            f"issues={self.issues!r})"
        )


def evaluate_source_requiredness(
    snapshot: ValidatedSourceWorkbookSnapshot,
) -> SourceRequirednessReport:
    """Evaluate required-field presence across all sheets in a snapshot."""
    if not isinstance(snapshot, ValidatedSourceWorkbookSnapshot):
        raise SourceRequirednessInputError(
            "Invalid snapshot type provided to evaluate_source_requiredness."
        )
    return SourceRequirednessReport(snapshot)
