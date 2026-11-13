from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from solution.normalize import (
    IST,
    norm_header,
    norm_phone,
    parse_date,
    parse_decimal,
    parse_enum,
    parse_timestamp,
    to_ist,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Mobile_No", "mobile no"),
        ("  Policy   No ", "policy no"),
        ("customer_name", "customer name"),
        ("Customer Name", "customer name"),
        ("PREMIUM", "premium"),
        ("__policy__no__", "policy no"),
    ],
)
def test_norm_header_lowercases_and_collapses_separators(raw, expected):
    assert norm_header(raw) == expected


def test_norm_header_matches_schema_name_to_file_header():
    assert norm_header("customer_name") == norm_header("Customer Name")


@pytest.mark.parametrize(
    "raw",
    [
        "9876543210",
        "+91 98765 43210",
        "09876543210",
        "91-98765-43210",
        "(+91) 9876543210",
        "+919876543210",
        "  9876543210  ",
    ],
)
def test_norm_phone_accepts_valid_indian_mobile_formats(raw):
    assert norm_phone(raw) == "+919876543210"


@pytest.mark.parametrize(
    "raw",
    [
        "987654321",
        "5876543210",
        "022-2345-6789",
        "N/A",
        "+1 415 555 0134",
        "",
        "12345678901",
        "9198765432101",
        "٩٨٧٦٥٤٣٢١٠",
    ],
)
def test_norm_phone_rejects_invalid_numbers(raw):
    assert norm_phone(raw) is None


@pytest.mark.parametrize("prefix", ["6", "7", "8", "9"])
def test_norm_phone_accepts_each_valid_mobile_prefix(prefix):
    assert norm_phone(prefix + "876543210") == "+91" + prefix + "876543210"


@pytest.mark.parametrize(
    "raw",
    [
        "2026-12-01",
        "01/12/2026",
        "01-12-2026",
        "01-Dec-2026",
        "01-dec-2026",
        "01-DEC-2026",
    ],
)
def test_parse_date_accepts_supported_formats(raw):
    assert parse_date(raw) == date(2026, 12, 1)


def test_parse_date_interprets_numeric_dates_day_first():
    assert parse_date("03/04/2026") == date(2026, 4, 3)


@pytest.mark.parametrize(
    "raw",
    [
        "31/02/2026",
        "2026-13-01",
        "soon",
        "TBD",
        "",
        "32-01-2026",
        "01-Foo-2026",
        "1/2/2026",
    ],
)
def test_parse_date_rejects_invalid_or_unformatted_dates(raw):
    assert parse_date(raw) is None


def test_parse_date_accepts_only_real_leap_days():
    assert parse_date("29/02/2028") == date(2028, 2, 29)
    assert parse_date("29/02/2027") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12345", Decimal("12345.00")),
        ("12345.5", Decimal("12345.50")),
        ("₹12,345", Decimal("12345.00")),
        ("₹ 1,23,456", Decimal("123456.00")),
        ("Rs. 999", Decimal("999.00")),
        ("rs 999", Decimal("999.00")),
        ("RS.999", Decimal("999.00")),
        ("INR 1234.5", Decimal("1234.50")),
        ("inr1234", Decimal("1234.00")),
        ("0", Decimal("0.00")),
        ("2.345", Decimal("2.35")),
    ],
)
def test_parse_decimal_parses_and_rounds_nonnegative_amounts(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["N/A", "-", "twelve thousand", "-5", "₹-5", "", "1.2.3", "Rs."],
)
def test_parse_decimal_rejects_invalid_amounts(raw):
    assert parse_decimal(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("en", "en"),
        ("EN", "en"),
        (" hi ", "hi"),
        ("Ta", "ta"),
    ],
)
def test_parse_enum_matches_allowed_values_case_insensitively(raw, expected):
    assert parse_enum(raw, ["en", "hi", "ta"]) == expected


@pytest.mark.parametrize("raw", ["Hindi", "english", "tamil", "xx", ""])
def test_parse_enum_rejects_values_not_in_allowlist(raw):
    assert parse_enum(raw, ["en", "hi", "ta"]) is None


@pytest.mark.parametrize(
    ("raw", "expected_offset"),
    [
        ("2026-10-05T13:20:21Z", timedelta(0)),
        ("2026-10-05T18:50:21+05:30", timedelta(hours=5, minutes=30)),
        ("2026-10-05 18:50:21", timedelta(hours=5, minutes=30)),
    ],
)
def test_parse_timestamp_returns_aware_datetime_with_correct_offset(
    raw, expected_offset
):
    moment = parse_timestamp(raw)
    assert moment is not None
    assert moment.utcoffset() == expected_offset


def test_parse_timestamp_equates_utc_offset_and_naive_ist_spellings():
    spellings = [
        "2026-10-05T13:20:21Z",
        "2026-10-05T18:50:21+05:30",
        "2026-10-05 18:50:21",
    ]
    moments = [parse_timestamp(raw) for raw in spellings]

    assert moments[0] == moments[1] == moments[2]
    assert moments[2].tzinfo == IST


def test_parse_timestamp_rejects_unreadable_input():
    assert parse_timestamp("garbage") is None


def test_to_ist_converts_across_midnight():
    converted = to_ist(datetime.fromisoformat("2026-10-05T20:00:00+00:00"))

    assert converted.date() == date(2026, 10, 6)
    assert converted.isoformat() == "2026-10-06T01:30:00+05:30"
