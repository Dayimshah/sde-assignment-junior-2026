import pytest

from ledger.normalise import (
    normalise_date,
    normalise_decimal,
    normalise_header,
    normalise_phone,
    parse_timestamp,
)


@pytest.mark.parametrize("raw, expected", [
    ("+91 98765 43210", "+919876543210"),
    ("919876543210", "+919876543210"),
    ("09876543210", "+919876543210"),
    ("(+91) 9876543210", "+919876543210"),
    ("91-98765-43210", "+919876543210"),
    ("6463745499", "+916463745499"),
])
def test_valid_phones_are_canonicalised(raw, expected):
    assert normalise_phone(raw) == expected


@pytest.mark.parametrize("raw", [
    "5876543210",         # starts with 5
    "987654321",         # nine digits
    "022-2345-6789",      # landline digits
    "+1 415 555 0134",    # foreign number
    "N/A",
    "",
])
def test_invalid_phones_return_none(raw):
    assert normalise_phone(raw) is None


@pytest.mark.parametrize("raw, expected", [
    ("2026-10-31", "2026-10-31"),
    ("03/04/2026", "2026-04-03"),   # day first
    ("03-04-2026", "2026-04-03"),
    ("01-Dec-2026", "2026-12-01"),
    ("01-dec-2026", "2026-12-01"),  # month name is case-insensitive
])
def test_valid_dates_are_day_first(raw, expected):
    assert normalise_date(raw) == expected


@pytest.mark.parametrize("raw", ["31/02/2026", "2026-13-01", "soon", "TBD", "2026/10/31"])
def test_impossible_or_unknown_dates_are_invalid(raw):
    assert normalise_date(raw) is None


@pytest.mark.parametrize("raw, expected", [
    ("₹27,554", "27554.00"),
    ("Rs. 24787", "24787.00"),
    ("rs 1,200.5", "1200.50"),
    ("INR 25301.4", "25301.40"),
    ("4737.14", "4737.14"),
    ("12.345", "12.35"),
])
def test_amounts_strip_currency_and_round(raw, expected):
    assert normalise_decimal(raw) == expected


@pytest.mark.parametrize("raw", ["-5", "N/A", "twelve thousand", "-"])
def test_bad_amounts_return_none(raw):
    assert normalise_decimal(raw) is None


def test_headers_are_normalised_by_case_and_separators():
    assert normalise_header("  Policy__No ") == "policy no"
    assert normalise_header("Expiry\tDate") == "expiry date"


def test_naive_timestamps_are_ist():
    utc = parse_timestamp("2026-10-01 10:00:00")
    assert utc.isoformat() == "2026-10-01T04:30:00+00:00"


def test_offset_and_z_timestamps_are_converted_to_utc():
    assert parse_timestamp("2026-10-01T10:00:00+05:30").isoformat() == "2026-10-01T04:30:00+00:00"
    assert parse_timestamp("2026-10-01T04:30:00Z").isoformat() == "2026-10-01T04:30:00+00:00"
