"""Canonical time-of-day windows shared by the calendar resolver and provider.

Every window stays inside a single natural day so hourly aggregation never
crosses midnight. ``PERIOD_LABELS`` is the single source of truth for the
tool schema's ``period`` Literal.
"""

from __future__ import annotations

#: Canonical period label -> (start_hour, end_hour_exclusive, Chinese label).
PERIOD_WINDOWS: dict[str, tuple[int, int, str]] = {
    "morning": (6, 10, "早晨"),
    "afternoon": (12, 16, "下午"),
    "evening": (18, 22, "傍晚/晚上"),
    "night": (22, 24, "深夜"),
    "dawn": (0, 6, "凌晨"),
}

#: Compound phrases that also encode a date (明早 = 明天早晨).
PERIOD_COMPOUNDS: dict[str, tuple[str, str]] = {
    "明早": ("明天", "morning"),
    "今早": ("今天", "morning"),
    "明晚": ("明天", "evening"),
    "今晚": ("今天", "evening"),
    "昨晚": ("昨天", "evening"),
}

#: Plain aliases -> canonical period label.
PERIOD_SYNONYMS: dict[str, str] = {
    "早晨": "morning",
    "早上": "morning",
    "清晨": "morning",
    "上午": "morning",
    "中午": "afternoon",
    "下午": "afternoon",
    "傍晚": "evening",
    "晚上": "evening",
    "晚间": "evening",
    "深夜": "night",
    "夜间": "night",
    "凌晨": "dawn",
}

#: Ordered period labels, reused to build the tool schema's Literal.
PERIOD_LABELS: tuple[str, ...] = tuple(PERIOD_WINDOWS)


def canonical_period(token: str) -> str | None:
    """Map a plain alias to its canonical period label, or None."""
    return PERIOD_SYNONYMS.get(token.strip())


def period_hours(label: str) -> tuple[int, int] | None:
    """Return ``(start_hour, end_hour_exclusive)`` for a canonical label."""
    window = PERIOD_WINDOWS.get(label)
    return None if window is None else (window[0], window[1])


def period_label_cn(label: str) -> str:
    """Return the Chinese label for a canonical period label."""
    window = PERIOD_WINDOWS.get(label)
    return window[2] if window else ""
