"""Normalising and validating individual values: headers, phones, dates, amounts, enums, timestamps."""
import re
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

IST = timezone(timedelta(hours=5, minutes=30))

_SEPARATORS = re.compile(r"[_\s]+")


def normalise_header(text):
    return _SEPARATORS.sub(" ", text.strip().lower()).strip()


def normalise_phone(raw):
    """Return the canonical '+91XXXXXXXXXX' form, or None if the number is not a valid Indian mobile."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return None


_DATE_FORMATS = (
    (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
    (r"\d{2}/\d{2}/\d{4}", "%d/%m/%Y"),
    (r"\d{2}-\d{2}-\d{4}", "%d-%m-%Y"),
    (r"\d{2}-[A-Za-z]{3}-\d{4}", "%d-%b-%Y"),
)


def normalise_date(raw):
    """Parse a day-first date into an ISO date string, or return None."""
    text = raw.strip()
    for pattern, fmt in _DATE_FORMATS:
        if re.fullmatch(pattern, text):
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                return None
    return None


_CURRENCY_PREFIX = re.compile(r"^(?:₹|rs\.?|inr)?\s*", re.IGNORECASE)
_PLAIN_AMOUNT = re.compile(r"\d+(?:\.\d+)?")


def normalise_decimal(raw):
    """Return the amount as a string with two decimal places, or None if it is not a non-negative number."""
    text = _CURRENCY_PREFIX.sub("", raw.strip(), count=1).replace(",", "")
    if not _PLAIN_AMOUNT.fullmatch(text):
        return None
    return str(Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def normalise_enum(raw, allowed):
    value = raw.strip().lower()
    return value if value in allowed else None


def parse_timestamp(raw):
    """Parse an event timestamp to an aware UTC datetime. Naive timestamps are IST."""
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=IST)
    return moment.astimezone(timezone.utc)


def utc_key(moment):
    """Fixed-width UTC text, so that string order matches time order."""
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def ist_date(moment):
    return moment.astimezone(IST).date().isoformat()


def ist_iso_seconds(moment):
    return moment.astimezone(IST).isoformat(timespec="seconds")
