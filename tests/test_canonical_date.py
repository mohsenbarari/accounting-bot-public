"""Deterministic tests for canonical Jalali date parsing and Iran timezone conversion.

Verifies persiantools calendar validation, leap years, month boundaries,
Persian/Arabic digit normalization, raw preservation and Asia/Tehran timezone handling.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from accounting_contracts.canonical_date import (
    IRAN_TIMEZONE,
    JALALI_DATE_VERSION,
    CanonicalJalaliDate,
    InvalidDateError,
    InvalidTimezoneError,
    normalize_digits,
    parse_canonical_jalali_date,
    to_iran_time,
)
from persiantools.jdatetime import JalaliDate


def test_jalali_date_version_constant() -> None:
    """Verify standard Jalali date calculation version constant."""
    assert JALALI_DATE_VERSION == "jalali-date.v1"


def test_digit_normalization() -> None:
    """Verify conversion of Persian and Arabic digits to standard ASCII."""
    assert normalize_digits("۰۱۲۳۴۵۶۷۸۹") == "0123456789"
    assert normalize_digits("٠١٢٣٤٥٦٧٨٩") == "0123456789"
    assert normalize_digits("۱۴۰۳/۰۵/۱۵") == "1403/05/15"


def test_nowruz_parsing() -> None:
    """Verify Nowruz 1403/01/01 canonical values and Gregorian conversion."""
    result = parse_canonical_jalali_date("1403/01/01")
    assert isinstance(result, CanonicalJalaliDate)
    assert result.raw_text == "1403/01/01"
    assert result.canonical_date == "1403-01-01"
    assert result.gregorian_date == date(2024, 3, 20)
    assert result.year == 1403
    assert result.month == 1
    assert result.day == 1
    assert result.fiscal_year == 1403
    assert result.calculation_version == "jalali-date.v1"


def test_leap_and_non_leap_esfand_30() -> None:
    """Verify Esfand 30 is accepted only in leap years and rejected in non-leap."""
    # 1403 is a leap year in Jalali calendar (366 days, Esfand has 30 days)
    leap_result = parse_canonical_jalali_date("1403/12/30")
    assert isinstance(leap_result, CanonicalJalaliDate)
    assert leap_result.canonical_date == "1403-12-30"
    assert leap_result.gregorian_date == date(2025, 3, 20)

    # 1402 is a non-leap year (Esfand has 29 days)
    with pytest.raises(InvalidDateError) as exc_info:
        parse_canonical_jalali_date("1402/12/30")
    assert "1402/12/30" in str(exc_info.value)

    # 1399 is a leap year
    leap_1399 = parse_canonical_jalali_date("1399/12/30")
    assert isinstance(leap_1399, CanonicalJalaliDate)
    assert leap_1399.canonical_date == "1399-12-30"


def test_all_month_end_families() -> None:
    """Verify day 31 is valid for months 1-6 and invalid for months 7-12."""
    # Months 1-6 (Farvardin through Shahrivar have 31 days)
    for m in range(1, 7):
        parsed = parse_canonical_jalali_date(f"1403/{m:02d}/31")
        assert parsed is not None
        assert parsed.day == 31

    # Months 7-11 (Mehr through Bahman have 30 days; day 31 must be rejected)
    for m in range(7, 12):
        valid_30 = parse_canonical_jalali_date(f"1403/{m:02d}/30")
        assert valid_30 is not None
        assert valid_30.day == 30

        with pytest.raises(InvalidDateError):
            parse_canonical_jalali_date(f"1403/{m:02d}/31")


def test_round_trip_gregorian_conversion() -> None:
    """Verify round-trip conversion between Jalali and Gregorian dates."""
    test_dates = [
        "1403-01-01",
        "1403-06-31",
        "1403-07-01",
        "1403-12-30",
        "1399-12-30",
        "1400-01-01",
    ]
    for date_str in test_dates:
        parsed = parse_canonical_jalali_date(date_str)
        assert parsed is not None
        greg_date = parsed.gregorian_date
        converted_back = JalaliDate.to_jalali(greg_date)  # type: ignore[no-untyped-call]
        assert converted_back.year == parsed.year
        assert converted_back.month == parsed.month
        assert converted_back.day == parsed.day


def test_accepted_digit_and_separator_variants() -> None:
    """Verify accepted Persian/Arabic digits, dash separator and whitespace."""
    # Persian digits with slash
    p1 = parse_canonical_jalali_date("۱۴۰۳/۰۵/۱۵")
    assert p1 is not None and p1.canonical_date == "1403-05-15"
    assert p1.raw_text == "۱۴۰۳/۰۵/۱۵"

    # Arabic digits with dash
    p2 = parse_canonical_jalali_date("١٤٠٣-٠٥-١٥")
    assert p2 is not None and p2.canonical_date == "1403-05-15"

    # Single digit month/day
    p3 = parse_canonical_jalali_date("1403-5-9")
    assert p3 is not None and p3.canonical_date == "1403-05-09"

    # Outer whitespace
    p4 = parse_canonical_jalali_date("   1403/05/15   ")
    assert p4 is not None and p4.canonical_date == "1403-05-15"
    assert p4.raw_text == "   1403/05/15   "


def test_none_input_handling() -> None:
    """Verify that None input returns None without error."""
    assert parse_canonical_jalali_date(None) is None


def test_invalid_dates_rejected() -> None:
    """Verify that malformed or calendar-violating strings are rejected."""
    invalid_inputs = [
        "1403/13/01",  # Month 13
        "1403/00/01",  # Month 0
        "1403/01/00",  # Day 0
        "1403/01/32",  # Day 32
        "1403-1-32",
        "1403/01",  # Missing day
        "1403",  # Missing month/day
        "",  # Empty string
        "   ",  # Whitespace only
        "not-a-date",
        "1403/01/01 extra",  # Extra tokens
        "1403.01.01",  # Dot separator not accepted
        "1403/01-01",  # Mixed separator / and -
        "1403-01/01",  # Mixed separator - and /
        "१४०३/०۵/۱۵",  # Devanagari digits rejected
        "１４０۳/０۵/۱۵",  # Full-width digits rejected
    ]
    for inp in invalid_inputs:
        with pytest.raises(InvalidDateError):
            parse_canonical_jalali_date(inp)

    # Non-string types
    with pytest.raises(InvalidDateError):
        parse_canonical_jalali_date(14030101)


def test_to_iran_time_conversion() -> None:
    """Verify timezone conversion to Asia/Tehran and rejection of naive datetimes."""
    # 1. Reject naive datetime
    naive_dt = datetime(2026, 8, 30, 12, 0, 0)
    with pytest.raises(InvalidTimezoneError) as exc_info:
        to_iran_time(naive_dt)
    assert "naive" in str(exc_info.value)

    # 2. Reject non-datetime object
    with pytest.raises(InvalidTimezoneError):
        to_iran_time("2026-08-30T12:00:00Z")  # type: ignore[arg-type]

    # 3. Convert aware UTC datetime (2026-08-30 20:30 UTC -> 2026-08-31 00:00 Tehran)
    utc_instant_before = datetime(2026, 8, 30, 20, 29, 0, tzinfo=UTC)
    tehran_before = to_iran_time(utc_instant_before)
    assert tehran_before.tzinfo == IRAN_TIMEZONE
    assert tehran_before.year == 2026
    assert tehran_before.month == 8
    assert tehran_before.day == 30
    assert tehran_before.hour == 23
    assert tehran_before.minute == 59

    utc_instant_after = datetime(2026, 8, 30, 20, 31, 0, tzinfo=UTC)
    tehran_after = to_iran_time(utc_instant_after)
    assert tehran_after.year == 2026
    assert tehran_after.month == 8
    assert tehran_after.day == 31
    assert tehran_after.hour == 0
    assert tehran_after.minute == 1

    # 4. Financial date parsing independence from execution instant
    fin_date = parse_canonical_jalali_date("1403/05/15")
    assert fin_date is not None
    assert fin_date.canonical_date == "1403-05-15"
