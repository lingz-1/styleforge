# -*- coding: utf-8 -*-
"""WardrobeIndexSummary tests.

The compact capability index replaces the 262 KB item dump (which blew
ContextGuard's 40 K budget and gave the Stylist a truncated wardrobe), stays
far under the budget, and keeps the per-slot ``{count, sample_colors}`` shape
that ``test_agentic_environment.py`` relies on. Concrete ids are left to the
``search_wardrobe`` tool.
"""
from types import SimpleNamespace

from styleforge.agentic.context.wardrobe_index import (
    build_wardrobe_index,
    content_tokens,
    format_wardrobe_index,
)


def _item(item_id: str, item_type: str, color: str = "", name: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        item_id=item_id, name=name, item_type=item_type, color=color
    )


def _big_wardrobe(total: int = 2080) -> list[SimpleNamespace]:
    """Deterministic large wardrobe with a realistic slot mix."""
    slots = ["top", "bottom", "dress", "outerwear", "shoes", "accessory"]
    weights = [412, 286, 93, 146, 121, 1022]
    colors = ["black", "white", "blue", "brown", "red", "grey"]
    items: list[SimpleNamespace] = []
    idx = 0
    for slot, weight in zip(slots, weights):
        for _ in range(weight):
            if idx >= total:
                break
            items.append(
                _item(f"it_{idx:05d}", slot, colors[idx % len(colors)], name=f"{slot}{idx}")
            )
            idx += 1
    return items[:total]


def test_build_index_counts_and_colors() -> None:
    items = _big_wardrobe()
    index = build_wardrobe_index(items)

    assert index["item_count"] == 2080
    assert index["categories"]["top"] == 412
    assert index["categories"]["shoes"] == 121
    assert "black" in index["colors"]
    assert len(index["colors"]) <= 8

    # backward-compatible per-slot shape (test_agentic_environment relies on it)
    assert index["top"]["count"] == 412
    assert index["shoes"]["count"] == 121
    assert set(index["shoes"]["sample_colors"]) <= {"black", "white", "blue", "brown", "red", "grey"}


def test_candidate_pool_matches_slot_alias() -> None:
    items = _big_wardrobe()
    index = build_wardrobe_index(items, request="帮我搭一套连衣裙去婚礼")
    pool = index["candidate_pool"]
    assert pool == {"dress": 93}


def test_candidate_pool_falls_back_to_largest_slots() -> None:
    items = _big_wardrobe()
    index = build_wardrobe_index(items, request="音乐剧穿什么")
    pool = index["candidate_pool"]
    # fallback = the six largest slots, as a "what is available" capability hint
    assert set(pool) == {"accessory", "top", "bottom", "dress", "outerwear", "shoes"}
    assert pool["accessory"] == 1022  # largest first
    assert pool["top"] == 412


def test_index_size_stays_tiny() -> None:
    import json

    items = _big_wardrobe()
    index = build_wardrobe_index(items, request="下半年去看风声音乐剧怎么穿")
    serialized = json.dumps(index, ensure_ascii=False)
    assert len(serialized) < 5000  # the old full dump was ~262 K chars
    rendered = format_wardrobe_index(index)
    assert len(rendered) < 1000
    assert "共 2080 件" in rendered


def test_format_render_mentions_pool_and_colors() -> None:
    index = build_wardrobe_index(_big_wardrobe(), request="看音乐剧穿衬衫")
    text = format_wardrobe_index(index)
    assert "衣橱：" in text
    assert "品类" in text
    assert "当前任务候选池" in text


def test_content_tokens_are_known_terms_only() -> None:
    # the request is one long Chinese run; only known aliases survive
    assert content_tokens("帮我搭一套连衣裙去婚礼") == ["连衣裙"]
    assert content_tokens("看音乐剧穿衬衫") == ["衬衫"]
    assert content_tokens("音乐剧穿什么") == []
    assert content_tokens("下半年去看风声音乐剧再怎么搭") == []
    # slot aliases are matched first, then ASCII tokens
    assert content_tokens("shoes and top") == ["shoes", "top", "and"]
