"""WardrobeIndexSummary: a compact capability index of the wardrobe for prompts.

The old full item dump (~262 KB for 2080 items) was serialised into the Stylist
prompt and exceeded ContextGuard's 40 K budget, so the Stylist often saw a
truncated / broken wardrobe (a root cause of "only 1 outfit + repeated
search_wardrobe"). This module replaces it with a ~300-500 char index:

    - total item count
    - counts per slot (categories) and top colours
    - a request-scoped "candidate pool" (per-slot counts the task can draw on)

Concrete item ids are only ever retrieved via the ``search_wardrobe`` tool —
consistent with the existing design rule "Prompt advertises capability, Tool
fetches data".
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

# A request is a long Chinese sentence; without a segmenter, matching a raw
# contiguous run would yield one useless blob ("帮我搭一套连衣裙去婚礼"). So
# content tokens are only the known slot aliases found in the text plus ASCII
# tokens — exactly the terms that can size the candidate pool.
_ASCII_RE = re.compile(r"[a-z0-9]+")

_SLOT_ALIASES = {
    "外套": "outerwear", "大衣": "outerwear", "风衣": "outerwear", "西装": "outerwear",
    "夹克": "outerwear", "羽绒服": "outerwear", "牛仔外套": "outerwear",
    "上装": "top", "衬衫": "top", "毛衣": "top", "针织衫": "top", "卫衣": "top",
    "t恤": "top", "短袖": "top", "top": "top",
    "下装": "bottom", "裤子": "bottom", "牛仔裤": "bottom", "休闲裤": "bottom",
    "西裤": "bottom", "半身裙": "bottom", "短裙": "bottom", "皮裤": "bottom",
    "bottom": "bottom",
    "连衣裙": "dress", "裙子": "dress", "长裙": "dress", "礼服": "dress",
    "dress": "dress",
    "鞋": "shoes", "靴子": "shoes", "运动鞋": "shoes", "高跟鞋": "shoes",
    "乐福鞋": "shoes", "皮鞋": "shoes", "凉鞋": "shoes", "拖鞋": "shoes",
    "shoes": "shoes",
    "包": "accessory", "手袋": "accessory", "围巾": "accessory", "帽子": "accessory",
    "手套": "accessory", "配饰": "accessory", "腰带": "accessory",
    "accessory": "accessory",
}

# Longest-first so a request naming both "运动鞋" and "鞋" yields the more
# specific alias.
_SLOT_ALIASES_KEYS = sorted(_SLOT_ALIASES, key=len, reverse=True)


def content_tokens(request: str) -> list[str]:
    """Deterministic request tokens: known slot aliases found in the text plus
    ASCII tokens. Deterministic and dependency-free (no Chinese segmenter)."""
    text = (request or "").lower()
    tokens: list[str] = []
    for alias in _SLOT_ALIASES_KEYS:
        if alias in text and alias not in tokens:
            tokens.append(alias)
    for match in _ASCII_RE.findall(text):
        if match not in tokens:
            tokens.append(match)
    return tokens


def _candidate_pool(by_slot: dict[str, list[Any]], tokens: list[str]) -> dict[str, int]:
    """Per-slot counts the current task can draw on.

    When the request names a slot alias (外套/衬衫/连衣裙/鞋 …) the pool is
    those slots' counts; otherwise it falls back to the six largest slots as a
    "what is available" capability signal. The pool is a *count* hint only —
    precise ids come from ``search_wardrobe``.
    """
    wanted: set[str] = set()
    for token in tokens:
        slot = _SLOT_ALIASES.get(token)
        if slot is not None:
            wanted.add(slot)
    if wanted:
        pool = {slot: len(by_slot.get(slot, [])) for slot in sorted(wanted)}
    else:
        top = sorted(by_slot.items(), key=lambda kv: -len(kv[1]))[:6]
        pool = {slot: len(items) for slot, items in top}
    return dict(sorted(pool.items(), key=lambda kv: -kv[1]))


def build_wardrobe_index(items: list[Any], request: str = "") -> dict[str, Any]:
    """Build the compact wardrobe index.

    Items are ``ItemSnapshot``-like objects exposing ``item_id / name / item_type
    / color``. The per-slot ``{count, sample_colors}`` shape is kept for
    backward compatibility with ``test_agentic_environment.py``; the full item
    list is dropped (that is the whole point — never serialise it into a prompt).
    """
    by_slot: dict[str, list[Any]] = {}
    for item in items:
        by_slot.setdefault(item.item_type or "other", []).append(item)
    colors = Counter(
        (item.color or "").lower() for item in items if (item.color or "").strip()
    )
    index: dict[str, Any] = {
        "item_count": len(items),
        "categories": {slot: len(v) for slot, v in sorted(by_slot.items())},
        "colors": [color for color, _ in colors.most_common(8)],
        "candidate_pool": _candidate_pool(by_slot, content_tokens(request)),
    }
    for slot, slot_items in by_slot.items():
        index[slot] = {
            "count": len(slot_items),
            "sample_colors": sorted(
                {item.color for item in slot_items if (item.color or "").strip()}
            )[:8],
        }
    return index


def format_wardrobe_index(index: dict[str, Any]) -> str:
    """Compact single-line render for the prompt (a few hundred chars max)."""
    parts = [f"共 {index.get('item_count', 0)} 件"]
    categories = index.get("categories") or {}
    if categories:
        parts.append("品类 " + " / ".join(f"{s} {n}" for s, n in categories.items()))
    colors = index.get("colors") or []
    if colors:
        parts.append("色系 " + "/".join(colors))
    pool = index.get("candidate_pool") or {}
    if pool:
        parts.append(
            "当前任务候选池 " + " / ".join(f"{s} {n}" for s, n in pool.items())
        )
    return "衣橱：" + "；".join(parts)
