"""Deterministic rule-based extraction of long-term preference memories.

Reuses the color / occasion / subtype vocabularies from ``core.request_parser``
and adds small formality / style / habit lexicons.  Extraction is deliberately
conservative: every category caps its output and repeated observations raise
confidence in the memory repository.  There is intentionally no LLM here, so
the behavior is deterministic and unit-testable offline.
"""

from __future__ import annotations

import re
from typing import Any

from styleforge.core.request_parser import (
    COLOR_ALIASES,
    OCCASION_ALIASES,
    SUBTYPE_ALIASES,
    _is_negated,
)

# Each entry is ``(canonical, (aliases, ...))``; the matched alias becomes the
# memory content so the prompt and UI stay in natural Chinese.
FORMALITY_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("formal", ("正式", "正装", "正式场合")),
    ("casual", ("休闲", "随性", "放松")),
    ("business", ("商务", "职场", "办公")),
    ("smart_casual", ("商务休闲",)),
    ("minimal", ("简约", "极简")),
)

STYLE_WORD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("vintage", ("复古", "vintage")),
    ("street", ("街头", "嘻哈")),
    ("romantic", ("甜美", "浪漫", "碎花", "法式")),
    ("sporty", ("运动风", "运动")),
    ("cozy", ("慵懒", "温柔", "针织")),
    ("evening", ("晚宴风", "礼裙")),
    ("gorpcore", ("山系", "户外风")),
    ("cottagecore", ("田园风", "cottagecore")),
)

HABIT_MARKERS = ("每天", "经常", "总是", "通常", "日常都穿", "上班都穿")

_PER_CATEGORY_LIMIT = 3


def _find_terms(text: str, aliases: list[str]) -> list[tuple[int, int, str]]:
    """Scan non-overlapping term occurrences, preferring the longest phrase."""
    matches: list[tuple[int, int, str]] = []
    for alias in aliases:
        needle = alias.lower()
        pos = 0
        while True:
            start = text.find(needle, pos)
            if start < 0:
                break
            matches.append((start, start + len(needle), alias))
            pos = start + len(needle)
    accepted: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    for start, end, alias in sorted(
        matches, key=lambda value: (-(value[1] - value[0]), value[0])
    ):
        if any(start < other_end and end > other_start for other_start, other_end in occupied):
            continue
        accepted.append((start, end, alias))
        occupied.append((start, end))
    return sorted(accepted)


def _polarized(negated: bool, term: str) -> tuple[str, dict[str, Any]]:
    if negated:
        return f"避免{term}", {"polarity": "negative"}
    return term, {"polarity": "positive"}


def _extract_colors(text: str) -> list[dict[str, Any]]:
    extracts: list[dict[str, Any]] = []
    for _canonical, aliases in COLOR_ALIASES.items():
        for start, _end, alias in _find_terms(text, list(aliases)):
            negated = _is_negated(text, start)
            content, meta = _polarized(negated, alias)
            extracts.append(
                {"category": "color", "content": content, "meta": {**meta, "label": alias}}
            )
    return extracts[:_PER_CATEGORY_LIMIT]


def _extract_categories(text: str) -> list[dict[str, Any]]:
    alias_to_subtype: dict[str, tuple[str, str]] = {}
    for slot, subtype, aliases in SUBTYPE_ALIASES:
        for alias in aliases:
            alias_to_subtype[alias] = (slot, subtype)
    extracts: list[dict[str, Any]] = []
    for start, _end, alias in _find_terms(text, list(alias_to_subtype)):
        slot, subtype = alias_to_subtype[alias]
        negated = _is_negated(text, start)
        content, meta = _polarized(negated, alias)
        extracts.append(
            {
                "category": "category",
                "content": content,
                "meta": {**meta, "slot": slot, "subtype": subtype},
            }
        )
    return extracts[:_PER_CATEGORY_LIMIT]


def _extract_occasions(text: str) -> list[dict[str, Any]]:
    alias_to_occasion: dict[str, str] = {}
    for occasion, aliases in OCCASION_ALIASES:
        for alias in aliases:
            alias_to_occasion[alias] = occasion
    extracts: list[dict[str, Any]] = []
    for _start, _end, alias in _find_terms(text, list(alias_to_occasion)):
        extracts.append(
            {"category": "occasion", "content": alias, "meta": {"occasion": alias_to_occasion[alias]}}
        )
    return extracts[:2]


def _extract_formality(text: str) -> list[dict[str, Any]]:
    extracts: list[dict[str, Any]] = []
    for canonical, aliases in FORMALITY_ALIASES:
        for _start, _end, alias in _find_terms(text, list(aliases)):
            extracts.append(
                {"category": "formality", "content": alias, "meta": {"formality": canonical}}
            )
    return extracts[:2]


def _extract_style(text: str) -> list[dict[str, Any]]:
    extracts: list[dict[str, Any]] = []
    for tag, aliases in STYLE_WORD_RULES:
        for _start, _end, alias in _find_terms(text, list(aliases)):
            extracts.append(
                {"category": "style", "content": alias, "meta": {"style_tag": tag}}
            )
    return extracts[:2]


def _extract_habit(text: str, category_terms: list[str]) -> list[dict[str, Any]]:
    if not any(marker in text for marker in HABIT_MARKERS):
        return []
    for term in category_terms:
        return [
            {
                "category": "habit",
                "content": f"常穿{term}",
                "meta": {"habit": True, "category_term": term},
            }
        ]
    return []


def extract_memories(request: str) -> list[dict[str, Any]]:
    """Return ``{"category", "content", "meta"}`` preference extracts."""
    text = re.sub(r"\s+", " ", request.strip().lower())
    if not text:
        return []
    colors = _extract_colors(text)
    categories = _extract_categories(text)
    occasions = _extract_occasions(text)
    formality = _extract_formality(text)
    style = _extract_style(text)
    habit = _extract_habit(text, [value["content"] for value in categories])

    extracts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in (*colors, *categories, *occasions, *formality, *style, *habit):
        key = (item["category"], item["content"])
        if key in seen:
            continue
        seen.add(key)
        extracts.append(item)
    return extracts
