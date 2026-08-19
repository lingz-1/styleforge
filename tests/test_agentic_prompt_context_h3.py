# -*- coding: utf-8 -*-
"""H3a end-to-end prompt contract + ContextGuard budget.

The 262 KB item dump (which blew the 40 K budget and gave the Stylist a
truncated wardrobe) is gone. A full C-layer bundle — thread + layered memories +
grounding + wardrobe capability index — must:

  - carry 【环境定位】 and the layered 【偏好上下文】 headers, in the frozen
    C-layer tail order (thread → memories → grounding);
  - never dump the full memory_profile (no 记忆画像);
  - stay under ContextGuard's 40 K budget with a 2080-item wardrobe.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from styleforge.agentic.context.assembler import ContextAssembler
from styleforge.agentic.context.grounding import GroundingResolver
from styleforge.agentic.context.guard import CONTEXT_OK, ContextGuard
from styleforge.agentic.context.memory_context import PreferenceRetriever
from styleforge.agentic.context.prompt_assembler import PromptAssembler
from styleforge.agentic.context.wardrobe_index import build_wardrobe_index
from styleforge.agentic.runtime.capability_registry import CapabilityRegistry
from styleforge.agentic.tools.local_tools import (
    AGENT_STYLIST,
    CAP_WEB_SEARCH,
    register_local_tools,
)
from styleforge.models.agentic_contract import EnvironmentFacts

_INSTRUCTIONS = Path(__file__).resolve().parent.parent / "apps/api/styleforge/agentic/instructions"
_NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)


def _big_wardrobe_items(total: int = 2080) -> list[SimpleNamespace]:
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
                SimpleNamespace(
                    item_id=f"it_{idx:05d}",
                    name=f"{slot}{idx}",
                    item_type=slot,
                    color=colors[idx % len(colors)],
                )
            )
            idx += 1
    return items[:total]


def _pref(**overrides: object) -> dict:
    base = {
        "active": True,
        "confidence": 0.9,
        "polarity": "positive",
        "lifecycle": "long_term",
        "dimension": "color",
        "value": "黑色",
        "scope": {},
        "last_observed_at": "2026-08-10T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def _facts(request: str) -> EnvironmentFacts:
    return EnvironmentFacts(
        wardrobe_summary=build_wardrobe_index(_big_wardrobe_items(), request=request)
    )


def _bundle_for(state: dict) -> tuple[PromptAssembler, object]:
    """(assembler, bundle) for a full C-layer stylist prompt."""
    retriever = PreferenceRetriever(now=_NOW)
    context = ContextAssembler(memory_retriever=retriever).assemble(AGENT_STYLIST, state)
    registry = CapabilityRegistry()
    register_local_tools(registry, object())
    tools = registry.runtime_available(AGENT_STYLIST, frozenset({CAP_WEB_SEARCH}))
    assembler = PromptAssembler(instructions_root=_INSTRUCTIONS)
    return assembler, assembler.build(AGENT_STYLIST, context, tools)


def _full_state() -> dict:
    grounding = GroundingResolver().resolve(
        "下周去看风声音乐剧怎么穿",
        capabilities=frozenset({CAP_WEB_SEARCH}),
    ).model_dump(mode="json")
    return {
        "request": "下周去看风声音乐剧怎么穿",
        "thread_context": {
            "current_outfit_id": "",
            "current_item_ids": [],
            "thread_grounding": {"activity": "观看《风声》"},
        },
        "recalled_memories": ["legacy-fallback"],  # retriever injected → ignored
        "raw_preferences": [
            _pref(value="黑色"),
            _pref(polarity="negative", confidence=0.8, value="运动鞋"),
        ],
        "grounding_context": grounding,
        "environment_facts": _facts("下周去看风声音乐剧怎么穿"),
    }


def test_full_stylist_bundle_has_all_h3a_sections() -> None:
    _, bundle = _bundle_for(_full_state())
    text = bundle.system_text
    assert "【环境定位】" in text
    assert "【偏好上下文】" in text
    assert "【长期偏好】" in text
    assert "【避免】" in text
    assert "【对话上下文】" in text
    assert "【环境事实】" in text
    assert "衣橱：" in text
    assert "共 2080 件" in text
    # the full memory_profile dump is gone from the facts render
    assert "记忆画像" not in text
    # C-layer tail order: thread → memories → grounding (decision read last)
    assert text.index("【对话上下文】") < text.index("【偏好上下文】")
    assert text.index("【偏好上下文】") < text.index("【环境定位】")


def test_full_bundle_stays_under_guard_budget() -> None:
    _, bundle = _bundle_for(_full_state())
    assert len(bundle.system_text) < 40_000  # the old wardrobe dump was ~262 K
    result = ContextGuard(char_budget=40_000).check(bundle)
    assert result.status == CONTEXT_OK


def test_wardrobe_segment_stays_tiny() -> None:
    # the quantified root-cause fix: ~262 K item dump → ~1 K capability index
    _, bundle = _bundle_for(_full_state())
    text = bundle.system_text
    start = text.index("衣橱：")
    end = text.find("\n", start)
    segment = text[start:end] if end != -1 else text[start:]
    assert len(segment) < 1000


def test_layered_preferences_from_retriever_render_in_order() -> None:
    state = _full_state()
    state["raw_preferences"] = [
        _pref(lifecycle="short_term", confidence=0.5, value="休闲"),
        _pref(value="黑色"),
        _pref(polarity="negative", confidence=0.8, value="运动鞋"),
    ]
    _, bundle = _bundle_for(state)
    text = bundle.system_text
    assert text.index("【短期偏好】") < text.index("【长期偏好】")
    assert text.index("【长期偏好】") < text.index("【避免】")
