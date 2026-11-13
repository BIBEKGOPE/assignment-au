"""Pure helpers for cleaning and parsing values used by the data pipeline."""

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Iterable

IST = timezone(timedelta(hours=5, minutes=30))


def norm_header(text: str) -> str:
    """Normalize a CSV header.

    Example: ``norm_header("Mobile_No")`` returns ``"mobile no"``.
    """
    return re.sub(r"[_\s]+", " ", text.strip().lower()).strip()


def norm_phone(raw: str) -> str | None:
    """Normalize a valid Indian mobile number to its canonical form.

    Example: ``norm_phone("91-98765-43210")`` returns ``"+919876543210"``.
    """
    digits = "".join(char for char in raw if char in "0123456789")

    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) != 10 or digits[0] not in "6789":
        return None
    return "+91" + digits


def parse_date(raw: str) -> date | None:
    """Parse one of the supported exact date formats.

    Example: ``parse_date("01-Dec-2026")`` returns ``date(2026, 12, 1)``.
    """
    iso_match = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", raw)
    day_first_match = re.fullmatch(r"([0-9]{2})/([0-9]{2})/([0-9]{4})", raw)
    dashed_match = re.fullmatch(r"([0-9]{2})-([0-9]{2})-([0-9]{4})", raw)
    named_match = re.fullmatch(
        r"([0-9]{2})-([A-Za-z]{3})-([0-9]{4})", raw
    )

    try:
        if iso_match:
            year, month, day = (int(value) for value in iso_match.groups())
        elif day_first_match or dashed_match:
            match = day_first_match or dashed_match
            day, month, year = (int(value) for value in match.groups())
        elif named_match:
            day_text, month_text, year_text = named_match.groups()
            month = {
                "jan": 1,
                "feb": 2,
                "mar": 3,
                "apr": 4,
                "may": 5,
                "jun": 6,
                "jul": 7,
                "aug": 8,
                "sep": 9,
                "oct": 10,
                "nov": 11,
                "dec": 12,
            }.get(month_text.lower())
            if month is None:
                return None
            day, year = int(day_text), int(year_text)
        else:
            return None

        return date(year, month, day)
    except ValueError:
        return None


def parse_decimal(raw: str) -> Decimal | None:
    """Parse a non-negative decimal amount and round it to two places.

    Example: ``parse_decimal("Rs. 1,234.565")`` returns ``Decimal("1234.57")``.
    """
    value = raw.strip()
    value = re.sub(
        r"^(?:₹|rs\.?|inr)\s*", "", value, count=1, flags=re.IGNORECASE
    )
    value = value.replace(",", "")

    if not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", value):
        return None

    amount = Decimal(value)
    with localcontext() as context:
        context.prec = max(context.prec, len(value.replace(".", "")) + 2)
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_enum(raw: str, allowed: Iterable[str]) -> str | None:
    """Match an allowed enum value without regard to case.

    Example: ``parse_enum(" EN ", ["en", "hi"])`` returns ``"en"``.
    """
    value = raw.strip().casefold()
    for candidate in allowed:
        if candidate.casefold() == value:
            return candidate.lower()
    return None


def parse_timestamp(raw: str) -> datetime | None:
    """Parse an ISO timestamp, interpreting naive timestamps as IST.

    Example: ``parse_timestamp("2026-10-03 10:01:00")`` returns an IST-aware datetime.
    """
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=IST)
    return moment


def to_ist(moment: datetime) -> datetime:
    """Convert an aware datetime to IST.

    Example: ``to_ist(datetime.fromisoformat("2026-10-05T13:20:21+00:00"))``
    returns ``2026-10-05 18:50:21+05:30``.
    """
    return moment.astimezone(IST)
