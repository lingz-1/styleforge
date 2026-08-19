"""ThreadPreferenceView: the current session's preference context (H3a-2).

The real-model smoke confirmed there is no persistent "current-session
preference model": everything the user said got folded into long-term memory,
so "这次想穿黑一点" was read as a global colour preference. ThreadPreferenceView
fixes that with a session-scoped view that holds *this conversation's* wants
and never promotes them to the user profile.

Iron rules (frozen with the user):
    1. "这次想穿黑一点" lands in ThreadPreferenceView, NOT in long-term memory.
       The turn-scoped memory gate (``_scope_gate`` in task_workflow.py) keeps
       the extractor away; this module only carries the session dict.
    2. This module NEVER imports or calls ``apply_evidence`` /
       ``preference_model_repository`` — the long-term write chain stays
       completely untouched (Turn/Thread ≠ User Profile).

The view lives in ``session_context["thread_preferences"]`` (Redis session
cache), never a database table. Extraction is deterministic — no LLM, no
Dependency — so a turn either yields a constraint or it doesn't.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

# Per-bucket cap: a long conversation keeps only the most recent direction per
# attribute, never an unbounded pile (frozen plan: PREF_CAP=6).
_PREF_CAP = 6

_COLOR_ALIASES = {
    "米白": "cream", "藏青": "navy", "卡其": "khaki", "驼色": "camel",
    "黑色": "black", "白色": "white", "红色": "red", "蓝色": "blue",
    "绿色": "green", "黄色": "yellow", "灰色": "grey", "棕色": "brown",
    "紫色": "purple", "粉色": "pink",
    "黑": "black", "白": "white", "红": "red", "蓝": "blue", "绿": "green",
    "黄": "yellow", "灰": "grey", "棕": "brown", "紫": "purple", "粉": "pink",
}
_COLOR_ALIASES_KEYS = sorted(_COLOR_ALIASES, key=len, reverse=True)

_FORMALITY_ALIASES = {"正式": "formal", "商务": "formal", "休闲": "casual", "通勤": "business"}
_STYLE_ALIASES = {
    "简约": "minimal", "复古": "retro", "浪漫": "romantic", "优雅": "elegant",
    "甜美": "sweet", "成熟": "mature", "温柔": "soft", "朋克": "punk",
}
_ITEM_ALIASES = {
    "运动鞋", "高跟鞋", "靴子", "皮鞋", "凉鞋", "帆布鞋", "乐福鞋",
    "连衣裙", "长裙", "短裙", "半身裙", "礼服",
    "衬衫", "毛衣", "卫衣", "针织衫",
    "牛仔裤", "休闲裤", "西裤",
    "外套", "大衣", "风衣", "西装", "夹克", "羽绒服",
    "包", "手袋", "帽子", "围巾", "腰带",
}

# negative wants: 不要红色 / 别穿运动鞋 / 避开白色 / 拒绝牛仔
_NEGATIVE_RE = re.compile(r"(?:不要|别穿|别|不穿|避开|拒绝|排斥)([一-龥a-zA-Z]+)")
# directional adjustments: 更休闲一点 / 再正式些 / 想简约一些
_DIRECTIONAL_RE = re.compile(r"(?:更|再|想|要)?(休闲|正式|商务|通勤|简约|复古|浪漫|优雅|甜美|成熟|温柔)(一点|一些|些|点)?")
# positive colour wants: 想穿黑一点 / 穿黑色的 / 要蓝色
_COLOR_PREF_RE = re.compile(r"(?:想穿|穿|要|换|来点|来|是|选)(黑|白|红|蓝|绿|黄|灰|棕|紫|粉|藏青|卡其|米白)(色)?(一点|的)?")


def _resolve_attribute_value(phrase: str) -> tuple[str | None, str | None]:
    """Map a bare phrase to ``(attribute, value)``; ``(None, None)`` unclassified."""
    phrase = phrase.strip()
    if phrase in _COLOR_ALIASES:
        return "color", _COLOR_ALIASES[phrase]
    if phrase in _FORMALITY_ALIASES:
        return "formality", _FORMALITY_ALIASES[phrase]
    if phrase in _STYLE_ALIASES:
        return "style", _STYLE_ALIASES[phrase]
    if phrase in _ITEM_ALIASES:
        return "item_type", phrase
    return None, None


def parse_thread_preferences(request: str) -> dict[str, list[dict[str, Any]]]:
    """Deterministic extraction of thread-scoped preferences.

    Returns three buckets:
        preferences       positive wants — '这次想穿黑一点' → color black
        constraints       negative wants — '不要红色'      → color red / negative
        style_adjustments directional   — '更休闲一点'     → formality casual

    ``source_turn`` keeps the verbatim text for traceability. Negative and
    directional phrases are consumed from the text first so a positive colour
    rule cannot re-read the same run.
    """
    text = (request or "").strip()
    buckets: dict[str, list[dict[str, Any]]] = {
        "preferences": [],
        "constraints": [],
        "style_adjustments": [],
    }
    if not text:
        return buckets

    for match in _NEGATIVE_RE.finditer(text):
        attribute, value = _resolve_attribute_value(match.group(1))
        if attribute is not None:
            buckets["constraints"].append(
                {
                    "attribute": attribute,
                    "value": value,
                    "polarity": "negative",
                    "source_turn": match.group(0),
                }
            )
            text = text.replace(match.group(0), " ", 1)

    for match in _DIRECTIONAL_RE.finditer(text):
        attribute, value = _resolve_attribute_value(match.group(1))
        if attribute is not None:
            buckets["style_adjustments"].append(
                {
                    "attribute": attribute,
                    "value": value,
                    "polarity": "directional",
                    "source_turn": match.group(0),
                }
            )
            text = text.replace(match.group(0), " ", 1)

    for match in _COLOR_PREF_RE.finditer(text):
        raw = match.group(1)
        value = _COLOR_ALIASES.get(raw, raw)
        buckets["preferences"].append(
            {
                "attribute": "color",
                "value": value,
                "polarity": "positive",
                "source_turn": match.group(0),
            }
        )
        text = text.replace(match.group(0), " ", 1)

    # bare style/formality words that survived the directional pass
    for alias, value in {**_FORMALITY_ALIASES, **_STYLE_ALIASES}.items():
        if alias in text:
            attribute = "formality" if alias in _FORMALITY_ALIASES else "style"
            buckets["preferences"].append(
                {
                    "attribute": attribute,
                    "value": value,
                    "polarity": "positive",
                    "source_turn": alias,
                }
            )
    return buckets


def update_thread_preferences(
    prev: dict[str, Any],
    request: str,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Merge one turn's parsed preferences into the session view.

    Same ``(attribute, value)`` key overwrites in place (keeps it newest);
    otherwise appended. Each bucket is capped at ``_PREF_CAP`` — overflow drops
    the oldest entry, never the newest direction. Returns the full view dict
    ready for ``session_context["thread_preferences"]``.
    """
    current = {key: list(prev.get(key) or []) for key in ("preferences", "constraints", "style_adjustments")}
    parsed = parse_thread_preferences(request)
    for bucket, entries in parsed.items():
        for entry in entries:
            key = (entry["attribute"], entry["value"])
            replaced = False
            for index, existing in enumerate(current[bucket]):
                if (existing.get("attribute"), existing.get("value")) == key:
                    current[bucket][index] = entry  # overwrite → newest
                    replaced = True
                    break
            if not replaced:
                current[bucket].append(entry)
        if len(current[bucket]) > _PREF_CAP:
            del current[bucket][: len(current[bucket]) - _PREF_CAP]  # drop oldest
    stamp = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {"preferences": current["preferences"], "constraints": current["constraints"],
            "style_adjustments": current["style_adjustments"], "updated_at": stamp}


def thread_preferences_to_prompt(view: dict[str, Any]) -> str:
    """Compact render for the C-layer thread section.

    Empty view renders the header with a marker so the model knows the session
    holds no thread preference yet (absence is a fact, not an error).
    """
    lines = ["【当前会话偏好】"]
    rendered = False
    for label, key in (
        ("偏好", "preferences"),
        ("约束", "constraints"),
        ("方向调整", "style_adjustments"),
    ):
        items = view.get(key) or []
        if not items:
            continue
        rendered = True
        parts = [f"{item.get('attribute')}={item.get('value')}" for item in items]
        lines.append(f"{label}：" + "、".join(parts))
    if not rendered:
        lines.append("（无）")
    return "\n".join(lines)
