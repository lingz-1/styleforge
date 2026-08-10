"""Pure resolution of Chinese relative/calendar time expressions to date windows.

``resolve_temporal_expression`` is a side-effect-free function: ``today`` is
injected, so every branch is offline-testable. It resolves to a daily range
(``start_date``/``end_date``) or, for time-of-day expressions, to a single-day
hourly window (``start_at``/``end_at``) that never crosses midnight.

Parsing order (short-circuits): compound period -> festival (incl. explicit
year) -> 后天 -> 今天/明天/ISO -> weekday (本周X/下周X/bare 周X) -> weekend
(周末/本周末/下周末) -> 下周 -> 下个月 -> unsupported. A lone period token
(下午) resolves to today.

Boundary rules (locked by tests): bare "周X" whose weekday is today -> delta 0
(today); "本周X" already passed -> same day next week (+7); "本周末" is the
next upcoming Sat+Sun (on Sunday that is next weekend); "下周" is next ISO week
Mon-Sun; "下个月" is next month day 1 .. end of month; 昨天/昨晚 resolve to a
historical date which the weather tool later rejects as ``invalid_date``.
"""

from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from styleforge.tools.calendar.festivals import find_festival, is_festival_name
from styleforge.tools.calendar.periods import (
    PERIOD_COMPOUNDS,
    PERIOD_SYNONYMS,
    PERIOD_WINDOWS,
    period_label_cn,
)

_WEEKDAY_INDEX = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
#: Accepts 周五 / 周周五 / 星期三 / 礼拜三 / 星期天 etc.
_WEEKDAY_TAIL = re.compile(r"^(?:星期|礼拜|周)?(周)?(一|二|三|四|五|六|日|天)$")
_WEEKDAY_DIRECTION = re.compile(r"^(下|本|这)(.+)$")

_DAY_ALIASES: dict[str, str] = {
    "今天": "今天", "今日": "今天", "today": "今天",
    "明天": "明天", "明日": "明天", "tomorrow": "明天",
}

#: 月份表达式：可选年份前缀 + 1-2 位数字 + 月（份）。bare "9月" 用今年，
#: 该月已完全过去则取明年。
_MONTH_RE = re.compile(r"^(今年|明年|后年)?\s*(\d{1,2})月(份)?$")
_YEAR_WORD_RE = re.compile(r"^(今年|明年|后年)$")
_YEAR_ISO_RE = re.compile(r"^(\d{4})年$")
#: 月份子段：上旬/月初(1-10)、中旬(11-20)、下旬/月底(21-月末)。
#: 该段已完全过去则推进到下月同段。
_MONTH_PART_RE = re.compile(r"^(上旬|中旬|下旬|月初|月底)$")
_MONTH_PART_DAYS: dict[str, tuple[int, int | None]] = {
    "上旬": (1, 10),
    "月初": (1, 10),
    "中旬": (11, 20),
    "下旬": (21, None),
    "月底": (21, None),
}
#: 季节代表起始月（冬跨年：12月-次年2月）。完整季节为 3 个月，
#: 初/盛(仲)/深(末) 前缀分别落在该季第 1/2/3 个月。
_SEASON_START_MONTH = {"春": 3, "夏": 6, "秋": 9, "冬": 12}
_SEASON_SUFFIX_OFFSET = {"初": 0, "盛": 1, "仲": 1, "深": 2, "末": 2}
_SEASON_ALIASES: dict[str, str] = {
    "春天": "春", "春季": "春",
    "夏天": "夏", "夏季": "夏",
    "秋天": "秋", "秋季": "秋",
    "冬天": "冬", "冬季": "冬",
}


@dataclass(frozen=True, slots=True)
class TemporalResolution:
    """Resolved date window for one temporal expression."""

    status: Literal["resolved", "unsupported"]
    expression: str = ""
    start_date: str = ""
    end_date: str = ""
    start_at: str = ""  # naive local "YYYY-MM-DDTHH:MM:SS", period windows only
    end_at: str = ""
    precision: str = ""  # day|period_day|weekday|weekend|week|month|festival
    granularity: Literal["daily", "hourly"] = "daily"
    period: str = ""
    period_label: str = ""
    timezone: str = "auto"
    default_applied: bool = False
    resolution_basis: str = ""  # relative_to_request_time|festival_table|weekday_calendar|unsupported_expression
    error_code: str = ""  # unsupported_expression|festival_out_of_table
    #: True for qualitative time expressions (9月/夏天/明年) whose resolved range
    #: is an approximation: exact weather may be out of the forecast window, so
    #: agents should fall back to seasonal common-sense knowledge.
    approximate: bool = False


def resolve_temporal_expression(
    expression: str,
    *,
    today: date,
) -> TemporalResolution:
    """Resolve a Chinese temporal expression to a date window (pure function)."""
    expr = expression.strip()
    if not expr:
        return _unsupported("", "unsupported_expression")

    phrase, period = _parse_period(expr)
    result = _resolve_single_or_range(phrase, today)
    if result is None:
        if period is None:
            if is_festival_name(phrase):
                return _unsupported(expr, "festival_out_of_table")
            return _unsupported(expr, "unsupported_expression")
        # A lone period token (e.g. 下午) means today.
        result = (today, today, "period_day", "relative_to_request_time")

    start, end, precision, basis = result
    approximate = _is_approximate(precision, start, today)
    if period is not None:
        start_at, end_at = _period_window(start, period)
        # A period window is always a single natural day; a range phrase
        # combined with a period (周末晚上) uses the range's start day.
        return TemporalResolution(
            status="resolved",
            expression=expr,
            start_date=start.isoformat(),
            end_date=start.isoformat(),
            start_at=start_at,
            end_at=end_at,
            precision="period_day",
            granularity="hourly",
            period=period,
            period_label=period_label_cn(period),
            resolution_basis=basis,
            approximate=approximate,
        )
    return TemporalResolution(
        status="resolved",
        expression=expr,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        precision=precision,
        granularity="daily",
        resolution_basis=basis,
        approximate=approximate,
    )


def _parse_period(expr: str) -> tuple[str, str | None]:
    """Split a compound/time-of-day token off ``expr``.

    Returns ``(remaining_date_phrase, canonical_period_or_None)``. Compound
    phrases carry their own date (明早 -> 明天 morning); plain synonyms leave
    the rest of the expression untouched (明天下午 -> 明天 afternoon).
    """
    for phrase, (date_phrase, period) in PERIOD_COMPOUNDS.items():
        if phrase in expr:
            rest = expr.replace(phrase, "", 1).strip()
            if rest:
                # Prefer the compound's date unless the remaining text is itself a date word.
                return (rest, period) if _is_date_word(rest) else (date_phrase, period)
            return date_phrase, period
    for token, canonical in sorted(PERIOD_SYNONYMS.items(), key=lambda kv: -len(kv[0])):
        if token in expr:
            rest = expr.replace(token, "", 1).strip()
            return rest, canonical
    return expr, None


def _is_date_word(text: str) -> bool:
    return text in _DAY_ALIASES or _looks_iso(text)


def _looks_iso(text: str) -> bool:
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


#: open-meteo 最多预报今天起 16 天；落在更远的精确日期（如圣诞）查不到
#: 天气，统一标记 approximate 让 Agent 走季节常识 fallback。
_FORECAST_HORIZON_DAYS = 15


def _is_approximate(precision: str, start: date, today: date) -> bool:
    """A resolved window is approximate when it is qualitative or too far out.

    Qualitative expressions (9月/夏天/明年/月底) never map to a real forecast
    day; precise far-future dates (圣诞 beyond the forecast horizon) resolve
    but the weather tool honestly returns ``invalid_date``, so both fall back
    to the same seasonal common-sense rule.
    """
    if precision in {"month", "season", "year", "month_part"}:
        return True
    if precision == "festival":
        return start > today + timedelta(days=_FORECAST_HORIZON_DAYS)
    return False


def _resolve_single_or_range(
    phrase: str,
    today: date,
) -> tuple[date, date, str, str] | None:
    """Resolve the date phrase to ``(start, end, precision, basis)`` or None."""
    festival = find_festival(phrase, today) if phrase else None
    if festival is not None:
        return festival[1], festival[1], "festival", "festival_table"

    if phrase in _DAY_ALIASES:
        target = _resolve_day_alias(phrase, today)
        return target, target, "day", "relative_to_request_time"
    if phrase in {"后天", "大后天"}:
        target = today + timedelta(days=3 if phrase == "大后天" else 2)
        return target, target, "day", "relative_to_request_time"
    if phrase in {"昨天", "昨日"}:
        target = today - timedelta(days=1)
        return target, target, "day", "relative_to_request_time"
    if _looks_iso(phrase):
        target = date.fromisoformat(phrase)
        return target, target, "day", "relative_to_request_time"

    weekday = _parse_weekday(phrase)
    if weekday is not None:
        kind, index = weekday
        if kind == "bare":
            target = today + timedelta(days=(index - today.weekday()) % 7)
        elif kind == "this":
            target = today - timedelta(days=today.weekday()) + timedelta(days=index)
            if target < today:
                target += timedelta(days=7)
        else:  # next
            target = (
                today - timedelta(days=today.weekday())
                + timedelta(days=7 + index)
            )
        return target, target, "weekday", "weekday_calendar"

    if phrase in {"周末", "本周末", "这周末"}:
        start, end = _weekend(today)
        return start, end, "weekend", "weekday_calendar"
    if phrase == "下周末":
        start, end = _next_weekend(today)
        return start, end, "weekend", "weekday_calendar"
    if phrase in {"下周", "下星期"}:
        start, end = _next_week(today)
        return start, end, "week", "weekday_calendar"
    if phrase == "下个月":
        start, end = _next_month(today)
        return start, end, "month", "weekday_calendar"

    month = _resolve_month(phrase, today)
    if month is not None:
        return month
    season = _resolve_season(phrase, today)
    if season is not None:
        return season
    year = _resolve_year(phrase, today)
    if year is not None:
        return year
    month_part = _resolve_month_part(phrase, today)
    if month_part is not None:
        return month_part
    return None


def _resolve_day_alias(phrase: str, today: date) -> date:
    canonical = _DAY_ALIASES[phrase]
    if canonical == "今天":
        return today
    if canonical == "明天":
        return today + timedelta(days=1)
    return today


def _parse_weekday(phrase: str) -> tuple[str, int] | None:
    """Return ``(kind, weekday_index 0=Mon)`` for a weekday expression."""
    kind = "bare"
    rest = phrase
    match = _WEEKDAY_DIRECTION.match(phrase)
    if match:
        kind = {"下": "next", "本": "this", "这": "this"}[match.group(1)]
        rest = match.group(2)
    index = _weekday_index(rest)
    if index is None:
        return None
    return kind, index


def _weekday_index(text: str) -> int | None:
    match = _WEEKDAY_TAIL.match(text)
    return _WEEKDAY_INDEX[match.group(2)] if match else None


def _weekend(today: date) -> tuple[date, date]:
    """Next upcoming Sat+Sun (today Sunday -> next weekend)."""
    saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    return saturday, saturday + timedelta(days=1)


def _next_weekend(today: date) -> tuple[date, date]:
    saturday = today + timedelta(days=(5 - today.weekday()) % 7 + 7)
    return saturday, saturday + timedelta(days=1)


def _next_week(today: date) -> tuple[date, date]:
    start = today - timedelta(days=today.weekday()) + timedelta(days=7)
    return start, start + timedelta(days=6)


def _next_month(today: date) -> tuple[date, date]:
    if today.month == 12:
        start = date(today.year + 1, 1, 1)
    else:
        start = date(today.year, today.month + 1, 1)
    if start.month == 12:
        end = date(start.year, 12, 31)
    else:
        end = date(start.year, start.month + 1, 1) - timedelta(days=1)
    return start, end


def _month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def _resolve_month(
    phrase: str, today: date
) -> tuple[date, date, str, str] | None:
    """Resolve 9月 / 今年3月 / 明年12月 to a full-month window."""
    match = _MONTH_RE.match(phrase)
    if not match:
        return None
    prefix, month_num = match.group(1), int(match.group(2))
    if not 1 <= month_num <= 12:
        return None
    if prefix == "明年":
        year = today.year + 1
    elif prefix == "后年":
        year = today.year + 2
    else:
        year = today.year
        # bare "9月" (no prefix): use this year unless the month has fully
        # passed, in which case roll forward to next year.
        if prefix is None and (today.year, today.month) > (year, month_num):
            year += 1
    start, end = _month_range(year, month_num)
    return start, end, "month", "relative_to_request_time"


def _resolve_season(
    phrase: str, today: date
) -> tuple[date, date, str, str] | None:
    """Resolve 夏天/秋季/盛夏/初春/秋末 to a seasonal window.

    Full seasons map to a 3-month window (冬 spans into next February); a
    初/盛/仲/深/末 prefix narrows to the first / middle / last month of the
    season. Windows already fully in the past roll forward to next year.
    """
    name = _SEASON_ALIASES.get(phrase, phrase)
    if len(name) == 1 and name in _SEASON_START_MONTH:
        base, suffix = name, None
    elif (
        len(name) == 2
        and name[0] in _SEASON_SUFFIX_OFFSET
        and name[1] in _SEASON_START_MONTH
    ):
        base, suffix = name[1], name[0]
    elif (
        len(name) == 2
        and name[0] in _SEASON_START_MONTH
        and name[1] in {"末", "尾"}
    ):
        # 后缀式：秋末/冬末 -> 该季第 3 个月（"末" 已有 offset 2）。
        base, suffix = name[0], "末"
    else:
        return None
    start_month = _SEASON_START_MONTH[base] + (
        _SEASON_SUFFIX_OFFSET[suffix] if suffix else 0
    )
    end_month = start_month if suffix else start_month + 2
    start_month = ((start_month - 1) % 12) + 1
    end_month = ((end_month - 1) % 12) + 1
    # 冬(12-2)跨年：结束月比起始月小时，结束年 = 起始年 + 1。
    year = today.year
    end_year = year + (1 if end_month < start_month else 0)
    end = _month_range(end_year, end_month)[1]
    if today > end:  # this year's window is fully past -> next year
        year += 1
        end_year = year + (1 if end_month < start_month else 0)
        end = _month_range(end_year, end_month)[1]
    start = date(year, start_month, 1)
    return start, end, "season", "relative_to_request_time"


def _resolve_year(
    phrase: str, today: date
) -> tuple[date, date, str, str] | None:
    """Resolve 今年 / 明年 / 后年 / 2028年 to a full-year window."""
    match = _YEAR_WORD_RE.match(phrase)
    if match:
        word = match.group(1)
        year = today.year + {"今年": 0, "明年": 1, "后年": 2}[word]
    else:
        iso = _YEAR_ISO_RE.match(phrase)
        if not iso:
            return None
        year = int(iso.group(1))
    return date(year, 1, 1), date(year, 12, 31), "year", "relative_to_request_time"


def _resolve_month_part(
    phrase: str, today: date
) -> tuple[date, date, str, str] | None:
    """Resolve 上旬/月初/中旬/下旬/月底 to a sub-month window.

    Bounds are 1-10 / 11-20 / 21-end-of-month. A segment already fully in the
    past rolls forward to the same segment of next month.
    """
    match = _MONTH_PART_RE.match(phrase)
    if not match:
        return None
    word = match.group(1)
    start_day, end_day = _MONTH_PART_DAYS[word]

    def _segment(year: int, month: int) -> tuple[date, date]:
        start = date(year, month, start_day)
        end_day_fixed = end_day if end_day is not None else monthrange(year, month)[1]
        return start, date(year, month, end_day_fixed)

    start, end = _segment(today.year, today.month)
    if end < today:
        next_year = today.year + (1 if today.month == 12 else 0)
        next_month = 1 if today.month == 12 else today.month + 1
        start, end = _segment(next_year, next_month)
    return start, end, "month_part", "relative_to_request_time"


def _period_window(target: date, label: str) -> tuple[str, str]:
    window = PERIOD_WINDOWS[label]
    start_hour, end_hour = window[0], window[1]
    start_at = f"{target.isoformat()}T{start_hour:02d}:00:00"
    if end_hour == 24:
        end_at = f"{target.isoformat()}T23:59:59"
    else:
        end_at = f"{target.isoformat()}T{end_hour - 1:02d}:59:59"
    return start_at, end_at


def _unsupported(expression: str, error_code: str) -> TemporalResolution:
    return TemporalResolution(
        status="unsupported",
        expression=expression,
        error_code=error_code,
        resolution_basis="unsupported_expression",
    )
