"""Calendar / time-expression resolution for weather context (V2.2).

Pure, offline-testable modules: ``periods`` defines canonical time-of-day
windows, ``festivals`` holds the static festival table, ``resolver`` turns a
Chinese temporal expression into a date window.
"""

from styleforge.tools.calendar.festivals import find_festival, is_festival_name
from styleforge.tools.calendar.periods import (
    PERIOD_COMPOUNDS,
    PERIOD_LABELS,
    PERIOD_SYNONYMS,
    PERIOD_WINDOWS,
    canonical_period,
    period_hours,
    period_label_cn,
)
from styleforge.tools.calendar.resolver import (
    TemporalResolution,
    resolve_temporal_expression,
)

__all__ = [
    "PERIOD_COMPOUNDS",
    "PERIOD_LABELS",
    "PERIOD_SYNONYMS",
    "PERIOD_WINDOWS",
    "TemporalResolution",
    "canonical_period",
    "find_festival",
    "is_festival_name",
    "period_hours",
    "period_label_cn",
    "resolve_temporal_expression",
]
