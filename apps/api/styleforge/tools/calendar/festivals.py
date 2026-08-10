"""Static festival table for the calendar resolver (2025-2030).

Gregorian festivals are computed at runtime; lunar festivals use a static
Gregorian-date mapping table anchored by year (each row hand-checked against
public lunar almanacs, e.g. 元宵 always falls exactly 14 days after 春节).
Years outside the table are treated honestly as ``unsupported`` rather than
guessed.
"""

from __future__ import annotations

import re
from datetime import date

#: Fixed-date festivals (month, day); resolved against the request year.
GREGORIAN_FESTIVALS: dict[str, tuple[int, int]] = {
    "元旦": (1, 1),
    "劳动节": (5, 1),
    "儿童节": (6, 1),
    "国庆": (10, 1),
    "圣诞": (12, 25),
}

#: User-facing aliases -> canonical festival name.
FESTIVAL_ALIASES: dict[str, str] = {
    # Gregorian
    "元旦": "元旦",
    "元旦节": "元旦",
    "劳动节": "劳动节",
    "五一": "劳动节",
    "五一劳动节": "劳动节",
    "儿童节": "儿童节",
    "六一": "儿童节",
    "国庆": "国庆",
    "国庆节": "国庆",
    "圣诞": "圣诞",
    "圣诞节": "圣诞",
    # Lunar / seasonal
    "春节": "春节",
    "过年": "春节",
    "元宵": "元宵",
    "元宵节": "元宵",
    "清明": "清明",
    "清明节": "清明",
    "端午": "端午",
    "端午节": "端午",
    "中秋": "中秋",
    "中秋节": "中秋",
    "重阳": "重阳",
    "重阳节": "重阳",
}

#: Lunar/seasonal festivals mapped to Gregorian dates per year.
LUNAR_FESTIVAL_DATES: dict[str, dict[int, str]] = {
    "春节": {
        2025: "2025-01-29", 2026: "2026-02-17", 2027: "2027-02-06",
        2028: "2028-01-26", 2029: "2029-02-13", 2030: "2030-02-03",
    },
    "元宵": {
        2025: "2025-02-12", 2026: "2026-03-03", 2027: "2027-02-20",
        2028: "2028-02-09", 2029: "2029-02-27", 2030: "2030-02-17",
    },
    "清明": {
        2025: "2025-04-04", 2026: "2026-04-05", 2027: "2027-04-05",
        2028: "2028-04-04", 2029: "2029-04-04", 2030: "2030-04-05",
    },
    "端午": {
        2025: "2025-05-31", 2026: "2026-06-19", 2027: "2027-06-09",
        2028: "2028-05-28", 2029: "2029-06-16", 2030: "2030-06-05",
    },
    "中秋": {
        2025: "2025-10-06", 2026: "2026-09-25", 2027: "2027-09-15",
        2028: "2028-10-03", 2029: "2029-09-22", 2030: "2030-09-12",
    },
    "重阳": {
        2025: "2025-10-29", 2026: "2026-10-18", 2027: "2027-10-08",
        2028: "2028-10-26", 2029: "2029-10-15", 2030: "2030-10-05",
    },
}

#: Years covered by the lunar table, ascending.
FESTIVAL_TABLE_YEARS: tuple[int, ...] = tuple(sorted(LUNAR_FESTIVAL_DATES["春节"]))

_YEAR_PREFIX = re.compile(r"^(\d{4})\s*(.*)$")


def is_festival_name(name: str) -> bool:
    """True when the token is a known festival, optionally with a year prefix."""
    cleaned = _strip_year_prefix(name.strip())
    return cleaned in FESTIVAL_ALIASES


def find_festival(name: str, today: date) -> tuple[str, date] | None:
    """Resolve a festival token to ``(canonical_name, date)``.

    A bare token resolves to this year's festival, or the next occurrence in
    the table when this year's date has already passed. An explicit ``YYYY``
    or ``今年``/``明年`` prefix pins the year. Returns ``None`` for unknown
    names or years outside the table.
    """
    text = name.strip()
    explicit_year: int | None = None
    match = _YEAR_PREFIX.match(text)
    if match and match.group(1):
        explicit_year = int(match.group(1))
        text = match.group(2).strip()
    if text.startswith("今年"):
        explicit_year = today.year
        text = text[2:].strip()
    elif text.startswith("明年"):
        explicit_year = today.year + 1
        text = text[2:].strip()

    canonical = FESTIVAL_ALIASES.get(text)
    if canonical is None:
        return None

    if canonical in GREGORIAN_FESTIVALS:
        month, day = GREGORIAN_FESTIVALS[canonical]
        if explicit_year is not None:
            return canonical, date(explicit_year, month, day)
        target = date(today.year, month, day)
        if target < today:
            target = date(today.year + 1, month, day)
        return canonical, target

    by_year = LUNAR_FESTIVAL_DATES.get(canonical, {})
    if explicit_year is not None:
        raw = by_year.get(explicit_year)
        return (canonical, date.fromisoformat(raw)) if raw else None
    for year in FESTIVAL_TABLE_YEARS:
        raw = by_year.get(year)
        if raw and date.fromisoformat(raw) >= today:
            return canonical, date.fromisoformat(raw)
    return None


def _strip_year_prefix(text: str) -> str:
    match = _YEAR_PREFIX.match(text)
    return match.group(2).strip() if match and match.group(1) else text
