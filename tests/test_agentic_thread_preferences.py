# -*- coding: utf-8 -*-
"""H3a-2: ThreadPreferenceView + ThreadGroundingView + memory scope gate.

Verifies the frozen Turn/Thread ≠ User Profile contract:

  - "这次想穿黑一点" lands in the session thread view and NEVER in long-term
    memory (iron rule: thread_preferences.py carries no apply_evidence /
    preference_model_repository symbols; the scope gate keeps turn-scoped
    requests away from the extractor).
  - cross-turn grounding continuity (Question → Answer): a bare "上海" reply
    after a destination_city clarification is deterministically that field's
    answer — no "去X看" structure required (评审缺口 1).
  - "我平时不穿运动鞋" still flows to the long-term chain.
"""

from __future__ import annotations

from pathlib import Path
from threading import Event, Lock

from styleforge.agentic.context.grounding import (
    pending_field_for_question,
    thread_grounding_to_prompt,
    update_thread_grounding,
)
from styleforge.agentic.context.prompt_assembler import (
    PromptAssembler,
    _format_thread,
)
from styleforge.agentic.context.thread_preferences import (
    parse_thread_preferences,
    thread_preferences_to_prompt,
    update_thread_preferences,
)
from styleforge.agentic.runtime.capability_registry import CapabilityRegistry
from styleforge.agentic.tools.local_tools import (
    AGENT_STYLIST,
    CAP_WEB_SEARCH,
    register_local_tools,
)
from styleforge.models.task import TaskExecutionInput
from styleforge.workflow.task_workflow import MultiTaskWorkflow, _scope_gate

_INSTRUCTIONS = Path(__file__).resolve().parent.parent / "apps/api/styleforge/agentic/instructions"
_NOW = "2026-08-19T00:00:00+00:00"


# ── ThreadPreferenceView extraction ─────────────────────────────────────────

def test_turn_preference_extracts_black() -> None:
    parsed = parse_thread_preferences("这次想穿黑一点")
    assert parsed["preferences"] == [
        {
            "attribute": "color",
            "value": "black",
            "polarity": "positive",
            "source_turn": "想穿黑一点",
        }
    ]
    assert parsed["constraints"] == []
    assert parsed["style_adjustments"] == []


def test_negative_constraint_not_re_promoted() -> None:
    parsed = parse_thread_preferences("不要红色")
    assert parsed["constraints"] == [
        {
            "attribute": "color",
            "value": "red",
            "polarity": "negative",
            "source_turn": "不要红色",
        }
    ]
    # the consumed phrase must not be re-read as a positive preference
    assert parsed["preferences"] == []


def test_directional_adjustment() -> None:
    parsed = parse_thread_preferences("更休闲一点")
    assert parsed["style_adjustments"] == [
        {
            "attribute": "formality",
            "value": "casual",
            "polarity": "directional",
            "source_turn": "更休闲一点",
        }
    ]


def test_update_same_key_overwrites() -> None:
    first = update_thread_preferences({}, "这次想穿黑一点", now=_NOW)
    merged = update_thread_preferences(first, "明天想穿黑色", now=_NOW)
    assert len(merged["preferences"]) == 1
    assert merged["preferences"][0]["value"] == "black"
    assert merged["preferences"][0]["source_turn"] == "想穿黑色"  # newest wins


def test_cap_drops_oldest_not_newest() -> None:
    prev = {
        "preferences": [
            {
                "attribute": f"a{i}",
                "value": f"v{i}",
                "polarity": "positive",
                "source_turn": f"s{i}",
            }
            for i in range(6)
        ]
    }
    merged = update_thread_preferences(prev, "想穿黑色", now=_NOW)
    assert len(merged["preferences"]) == 6
    assert all(entry.get("value") != "v0" for entry in merged["preferences"])  # oldest dropped
    assert merged["preferences"][-1]["value"] == "black"  # newest kept


def test_compact_render() -> None:
    view = update_thread_preferences({}, "这次想穿黑一点，不要红色", now=_NOW)
    text = thread_preferences_to_prompt(view)
    assert "【当前会话偏好】" in text
    assert "color=black" in text
    assert "color=red" in text
    assert len(text) < 500


def test_iron_rule_no_db_write_symbols() -> None:
    """铁律: ThreadPreferenceView never imports/calls the long-term chain."""
    import styleforge.agentic.context.thread_preferences as module
    # no module-level binding to the long-term write chain (docstrings may
    # mention them — the *name* must never be importable from the module)
    assert "apply_evidence" not in module.__dict__
    assert "preference_model_repository" not in module.__dict__


# ── Memory scope gate ───────────────────────────────────────────────────────

def test_scope_gate_turn_scoped_requests() -> None:
    assert _scope_gate("这次想穿黑一点") is True
    assert _scope_gate("刚才说要换双鞋") is True
    assert _scope_gate("今天去面试穿什么") is True
    assert _scope_gate("今晚看演出穿什么") is True


def test_scope_gate_durable_requests_pass() -> None:
    assert _scope_gate("我平时不穿运动鞋") is False
    assert _scope_gate("以后都别推荐运动鞋") is False


def test_extract_memories_skipped_for_turn_scoped_request(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr(
        "styleforge.workflow.task_workflow.extract_language_evidence",
        lambda *args: calls.append(args) or {"evidence": []},
    )
    workflow = MultiTaskWorkflow.__new__(MultiTaskWorkflow)
    workflow.llm_client = None
    workflow.database_path = "sqlite:///:memory:"
    workflow._extract_memories(TaskExecutionInput(user_id="u", request="这次想穿黑一点"))
    assert calls == []


def test_extract_memories_still_runs_for_durable_request(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr(
        "styleforge.workflow.task_workflow.extract_language_evidence",
        lambda *args: calls.append(args) or {"evidence": []},
    )
    workflow = MultiTaskWorkflow.__new__(MultiTaskWorkflow)
    workflow.llm_client = None  # extract_language_evidence is monkeypatched away
    workflow.database_path = "sqlite:///:memory:"
    workflow._extract_memories(TaskExecutionInput(user_id="u", request="我平时不穿运动鞋"))
    assert len(calls) == 1


def test_deferred_memory_extraction_does_not_block_response_path(monkeypatch) -> None:
    started = Event()
    release = Event()

    def extract(*_args):
        started.set()
        release.wait(timeout=2.0)
        return {"evidence": []}

    monkeypatch.setattr(
        "styleforge.workflow.task_workflow.extract_language_evidence",
        extract,
    )
    workflow = MultiTaskWorkflow.__new__(MultiTaskWorkflow)
    workflow.llm_client = object()
    workflow.database_path = "sqlite:///:memory:"
    workflow.defer_memory_extraction = True
    workflow._memory_futures = set()
    workflow._memory_futures_lock = Lock()

    scheduled = workflow._process_memories(
        TaskExecutionInput(user_id="u", request="我平时不穿运动鞋")
    )

    assert scheduled is True
    assert started.wait(timeout=1.0) is True
    assert workflow.wait_for_background_tasks(timeout=0.01) is False
    release.set()
    assert workflow.wait_for_background_tasks(timeout=2.0) is True


# ── ThreadGroundingView + pending_field ─────────────────────────────────────

def test_round1_extracts_activity_and_time() -> None:
    view = update_thread_grounding({}, "下半年去看风声音乐剧怎么穿", now=_NOW)
    assert view["activity"] == "观看《风声》"
    assert view["date_expression"] == "下半年"
    assert view["approximate_time"] == "下半年"
    assert view["pending_field"] is None


def test_bare_reply_answers_pending_field() -> None:
    # round 1 ended NEED_USER with pending_field=destination_city
    view = update_thread_grounding({"pending_field": "destination_city"}, "上海", now=_NOW)
    assert view["destination_city"] == "上海"
    assert view["pending_field"] is None
    # the whole request was the answer — no fresh activity re-parsed
    assert view["activity"] is None


def test_bare_reply_answers_pending_date() -> None:
    view = update_thread_grounding({"pending_field": "date"}, "下周六", now=_NOW)
    assert view["date_expression"] == "下周六"
    assert view["approximate_time"] == "下周"
    assert view["pending_field"] is None


def test_pending_field_for_question() -> None:
    assert pending_field_for_question("你准备在哪个城市看？") == "destination_city"
    assert pending_field_for_question("打算什么时候去？") == "date"
    assert pending_field_for_question("还想调整什么吗？") is None


def test_thread_grounding_prompt_render() -> None:
    view = update_thread_grounding(
        {}, "下半年去看风声音乐剧怎么穿", now=_NOW
    )
    text = thread_grounding_to_prompt(view)
    assert "【上轮已确认】" in text
    assert "时间：下半年" in text
    assert "活动：观看《风声》" in text


# ── prompt-level integration (the stylist actually sees the section) ────────

def test_stylist_prompt_renders_thread_sections() -> None:
    thread = {
        "current_outfit_id": "",
        "current_item_ids": [],
        "thread_preferences": {
            "preferences": [
                {"attribute": "color", "value": "black", "polarity": "positive", "source_turn": "想穿黑一点"}
            ],
            "constraints": [],
            "style_adjustments": [],
        },
        "thread_grounding": {
            "destination_city": "上海",
            "date_expression": "下半年",
            "approximate_time": "下半年",
            "activity": "观看《风声》",
            "pending_field": None,
            "updated_at": _NOW,
        },
    }
    text = _format_thread(thread)
    assert "【当前会话偏好】" in text
    assert "color=black" in text
    assert "【上轮已确认】" in text
    assert "观演城市：上海" in text
    assert "活动：观看《风声》" in text


def test_stylist_prompt_build_carries_thread_section() -> None:
    from styleforge.agentic.context.assembler import ContextAssembler

    thread = {"current_outfit_id": "", "current_item_ids": [], "thread_preferences": {
        "preferences": [{"attribute": "color", "value": "black", "polarity": "positive", "source_turn": "想穿黑一点"}],
        "constraints": [], "style_adjustments": [],
    }}
    context = ContextAssembler().assemble(
        AGENT_STYLIST, {"request": "帮我搭一套", "thread_context": thread}
    )
    registry = CapabilityRegistry()
    register_local_tools(registry, object())
    tools = registry.runtime_available(AGENT_STYLIST, frozenset({CAP_WEB_SEARCH}))
    bundle = PromptAssembler(instructions_root=_INSTRUCTIONS).build(AGENT_STYLIST, context, tools)
    assert "【当前会话偏好】" not in bundle.system_text
    assert "【当前会话偏好】" in bundle.model_user_message
    assert "color=black" in bundle.model_user_message
