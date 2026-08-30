"""Canonical Jalali date parser and Iran-time utilities.

Provides deterministic Jalali calendar parsing via persiantools, Gregorian
conversion for database queries/indexing, fiscal-year key extraction, and
aware timezone conversion for Asia/Tehran.
"""

from __future__ import annotations

import re
import zoneinfo
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from persiantools.jdatetime import JalaliDate

from accounting_contracts.raw_input_contracts import ContractError

JALALI_DATE_VERSION: str = "jalali-date.v1"
IRAN_TIMEZONE = zoneinfo.ZoneInfo("Asia/Tehran")

PERSIAN_TO_ASCII_DIGITS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)

JALALI_DATE_REGEX = re.compile(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$")


class CanonicalDateError(ContractError):
    """Base exception for canonical date and timezone errors."""


class InvalidDateError(CanonicalDateError):
    """Raised when a date value cannot be parsed or violates calendar invariants."""


class InvalidTimezoneError(CanonicalDateError):
    """Raised when an instant is naive or cannot be converted to Iran timezone."""


def normalize_digits(text: str) -> str:
    """Convert Persian and Arabic digits to standard ASCII 0-9 digits."""
    return text.translate(PERSIAN_TO_ASCII_DIGITS)


def to_iran_time(dt: datetime) -> datetime:
    """Convert an aware datetime instant to Asia/Tehran timezone.

    Rejects naive datetimes to avoid ambiguous financial timestamp interpretations.
    """
    if not isinstance(dt, datetime):
        raise InvalidTimezoneError(f"Expected datetime object, got {type(dt).__name__}")
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        msg = (
            "Cannot convert naive datetime to Iran time. "
            "Datetime must be timezone-aware."
        )
        raise InvalidTimezoneError(msg)
    return dt.astimezone(IRAN_TIMEZONE)


@dataclass(frozen=True, slots=True)
class CanonicalJalaliDate:
    """Immutable parsed Jalali date result containing canonical and Gregorian values."""

    raw_text: str
    canonical_date: str
    gregorian_date: date
    year: int
    month: int
    day: int
    fiscal_year: int
    calculation_version: str = JALALI_DATE_VERSION


def parse_canonical_jalali_date(raw_value: Any) -> CanonicalJalaliDate | None:
    """Parse raw text into a CanonicalJalaliDate.

    Accepts strings with 4-digit year, 1-2 digit month and day, separated by / or -.
    Normalizes Persian/Arabic digits and outer whitespace.
    Returns None if raw_value is None.
    """
    if raw_value is None:
        return None

    if not isinstance(raw_value, str):
        msg = f"Jalali date input must be str or None, got {type(raw_value).__name__}"
        raise InvalidDateError(msg)

    cleaned = normalize_digits(raw_value.strip())
    match = JALALI_DATE_REGEX.match(cleaned)
    if not match:
        msg = (
            f"Invalid Jalali date format '{raw_value}'. "
            "Expected 'YYYY/MM/DD' or 'YYYY-MM-DD' with a 4-digit year."
        )
        raise InvalidDateError(msg)

    year_str, month_str, day_str = match.groups()
    year = int(year_str)
    month = int(month_str)
    day = int(day_str)

    try:
        jdate = JalaliDate(year, month, day)  # type: ignore[no-untyped-call]
    except (ValueError, TypeError) as exc:
        msg = f"Invalid Jalali calendar date '{raw_value}': {exc}"
        raise InvalidDateError(msg) from exc

    canonical_str = f"{jdate.year:04d}-{jdate.month:02d}-{jdate.day:02d}"
    gregorian_date_val = jdate.to_gregorian()

    return CanonicalJalaliDate(
        raw_text=raw_value,
        canonical_date=canonical_str,
        gregorian_date=gregorian_date_val,
        year=jdate.year,
        month=jdate.month,
        day=jdate.day,
        fiscal_year=jdate.year,
        calculation_version=JALALI_DATE_VERSION,
    )
