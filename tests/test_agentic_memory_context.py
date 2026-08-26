# -*- coding: utf-8 -*-
"""H3a-4: PreferenceRetriever — layered Top-K recall of long-term preferences.

Verifies the frozen Memory read-chain contracts (原则 b):

  - render order = read-chain priority: short_term → contextual → stable →
    avoidances, capped per layer (total top_k = 8).
  - per-agent differentiation via AGENT_BUCKETS (research has no avoidances,
    critic sees only stable + avoidances, an unknown agent → []).
  - relevance ranking (a preference whose terms appear in the request first).
  - iron rule: the retriever is read-chain only — thread_preferences never
    appears in its output, and the module carries no apply_evidence /
    preference_model_repository symbols (Turn/Thread ≠ User Profile).
  - ContextAssembler wiring: injected retriever wins over recalled_memories;
    default None keeps the legacy fallback (zero regression).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from styleforge.agentic.context.assembler import ContextAssembler
from styleforge.agentic.context.memory_context import PreferenceRetriever
from styleforge.agentic.context.prompt_assembler import PromptAssembler, _format_memories
from styleforge.agentic.runtime.capability_registry import CapabilityRegistry
from styleforge.agentic.tools.local_tools import (
    AGENT_CRITIC,
    AGENT_STYLIST,
    CAP_WEB_SEARCH,
    register_local_tools,
)

_INSTRUCTIONS = Path(__file__).resolve().parent.parent / "apps/api/styleforge/agentic/instructions"
_NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)


def _pref(**overrides) -> dict:
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


def _retriever() -> PreferenceRetriever:
    return PreferenceRetriever(now=_NOW)


# ── layer render order + caps ───────────────────────────────────────────────

def test_stylist_renders_all_four_layers_in_read_chain_order() -> None:
    raw = [
        _pref(lifecycle="short_term", confidence=0.5, dimension="style", value="休闲"),
        _pref(confidence=0.9, dimension="color", value="黑色"),
        _pref(polarity="negative", confidence=0.8, dimension="鞋", value="运动鞋"),
        _pref(
            scope={"type": "contextual", "occasions": ["婚礼"]},
            confidence=0.9,
            dimension="formal",
            value="礼服",
            last_observed_at="2026-08-18T00:00:00+00:00",
        ),
    ]
    out = _retriever().retrieve("stylist", {"request": "帮我在婚礼上穿什么", "raw_preferences": raw})
    assert out  # every layer present
    indexes = {
        layer: [i for i, item in enumerate(out) if item["layer"] == layer][0]
        for layer in (
            "short_term_preferences",
            "contextual_preferences",
            "stable_preferences",
            "avoidances",
        )
    }
    assert (
        indexes["short_term_preferences"]
        < indexes["contextual_preferences"]
        < indexes["stable_preferences"]
        < indexes["avoidances"]
    )


def test_stable_layer_capped_and_total_top_k() -> None:
    stable_six = [_pref(value=f"v{i}", confidence=0.9) for i in range(6)]
    raw = stable_six + [
        _pref(lifecycle="short_term", confidence=0.5, value="s1"),
        _pref(lifecycle="short_term", confidence=0.5, value="s2"),
        _pref(lifecycle="short_term", confidence=0.5, value="s3"),
        _pref(lifecycle="short_term", confidence=0.5, value="s4"),
        _pref(polarity="negative", confidence=0.8, value="a1"),
        _pref(polarity="negative", confidence=0.8, value="a2"),
        _pref(polarity="negative", confidence=0.8, value="a3"),
    ]
    out = _retriever().retrieve("stylist", {"request": "", "raw_preferences": raw})
    assert len(out) == 8  # total top_k cap
    stable = [item for item in out if item["layer"] == "stable_preferences"]
    assert len(stable) == 4  # per-layer cap
    short = [item for item in out if item["layer"] == "short_term_preferences"]
    assert len(short) == 3


# ── agent differentiation ───────────────────────────────────────────────────

def test_research_has_no_avoidances() -> None:
    raw = [
        _pref(confidence=0.9, value="黑色"),
        _pref(polarity="negative", confidence=0.8, value="运动鞋"),
    ]
    out = _retriever().retrieve("research", {"request": "", "raw_preferences": raw})
    assert all(item["layer"] != "avoidances" for item in out)
    assert any(item["layer"] == "stable_preferences" for item in out)


def test_critic_only_stable_and_avoidances() -> None:
    raw = [
        _pref(lifecycle="short_term", confidence=0.5, value="s1"),
        _pref(confidence=0.9, value="黑色"),
        _pref(polarity="negative", confidence=0.8, value="运动鞋"),
    ]
    out = _retriever().retrieve(AGENT_CRITIC, {"request": "", "raw_preferences": raw})
    layers = {item["layer"] for item in out}
    assert layers == {"stable_preferences", "avoidances"}


def test_unknown_agent_returns_empty() -> None:
    out = _retriever().retrieve("ghost", {"request": "", "raw_preferences": [_pref()]})
    assert out == []


def test_empty_raw_returns_empty() -> None:
    assert _retriever().retrieve("stylist", {"request": "", "raw_preferences": []}) == []
    assert _retriever().retrieve("stylist", {"request": ""}) == []


# ── relevance ranking ───────────────────────────────────────────────────────

def test_request_matching_preference_ranks_first() -> None:
    raw = [
        _pref(confidence=0.9, dimension="color", value="白色"),
        _pref(confidence=0.9, dimension="color", value="黑色"),
    ]
    out = _retriever().retrieve(
        "stylist", {"request": "帮我搭一套黑色穿搭", "raw_preferences": raw}
    )
    stable = [item for item in out if item["layer"] == "stable_preferences"]
    assert stable[0]["value"] == "黑色"  # request mentions 黑色 → relevance 1.0


# ── iron rules (read-chain only) ────────────────────────────────────────────

def test_thread_preferences_never_in_retriever_output() -> None:
    raw = [_pref(lifecycle="short_term", confidence=0.5, value="休闲")]
    state = {
        "request": "",
        "raw_preferences": raw,
        "thread_context": {
            "thread_preferences": {
                "preferences": [
                    {"attribute": "color", "value": "black", "polarity": "positive", "source_turn": "想穿黑一点"}
                ]
            }
        },
    }
    out = _retriever().retrieve("stylist", state)
    # thread entries carry source_turn; they must never leak into the recall
    assert all("source_turn" not in item for item in out)


def test_iron_rule_no_db_write_symbols() -> None:
    import styleforge.agentic.context.memory_context as module
    assert "apply_evidence" not in module.__dict__
    assert "preference_model_repository" not in module.__dict__


# ── ContextAssembler wiring ─────────────────────────────────────────────────

def test_assembler_retriever_wins_over_recalled_memories() -> None:
    retriever = _retriever()
    raw = [_pref(confidence=0.9, value="黑色")]
    context = ContextAssembler(memory_retriever=retriever).assemble(
        AGENT_STYLIST,
        {"request": "", "recalled_memories": ["legacy-fallback"], "raw_preferences": raw},
    )
    assert len(context.memories) == 1
    assert context.memories[0]["layer"] == "stable_preferences"
    assert context.memories[0]["value"] == "黑色"


def test_assembler_without_retriever_uses_recalled_memories() -> None:
    context = ContextAssembler().assemble(
        AGENT_STYLIST,
        {"request": "", "recalled_memories": ["legacy-fallback"]},
    )
    assert context.memories == ["legacy-fallback"]


# ── prompt-level: 【偏好上下文】 layered rendering ─────────────────────────

def test_format_memories_groups_by_layer() -> None:
    raw = [
        _pref(confidence=0.9, value="黑色"),
        _pref(polarity="negative", confidence=0.8, value="运动鞋"),
    ]
    out = _retriever().retrieve("stylist", {"request": "", "raw_preferences": raw})
    text = _format_memories(out)
    assert "【偏好上下文】" in text
    assert "【长期偏好】" in text
    assert "【避免】" in text
    assert text.index("【长期偏好】") < text.index("【避免】")


def test_stylist_prompt_carries_layered_preferences() -> None:
    retriever = _retriever()
    raw = [_pref(confidence=0.9, value="黑色")]
    context = ContextAssembler(memory_retriever=retriever).assemble(
        AGENT_STYLIST, {"request": "帮我搭一套", "raw_preferences": raw}
    )
    registry = CapabilityRegistry()
    register_local_tools(registry, object())
    tools = registry.runtime_available(AGENT_STYLIST, frozenset({CAP_WEB_SEARCH}))
    bundle = PromptAssembler(instructions_root=_INSTRUCTIONS).build(AGENT_STYLIST, context, tools)
    assert "【偏好上下文】" in bundle.system_text
    assert "【长期偏好】" in bundle.system_text
    assert "黑色" in bundle.system_text
