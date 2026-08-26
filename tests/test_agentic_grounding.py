# -*- coding: utf-8 -*-
"""H3a-3: GroundingResolver (Context → Search → Materiality → Ask).

Covers the frozen search-before-ask contracts:

  - ``GroundingDecision``: READY / SEARCH_FIRST / NEED_USER, decided
    deterministically from *which* missing kinds a deployed capability can
    resolve (缺口 4) — a weather capability cannot resolve a missing event
    location, so "去看音乐剧" + weather-only → NEED_USER, never SEARCH_FIRST.
  - location_source / date_source / explicit_date / approximate_time from the
    legacy LocationResolver + resolve_temporal_expression.
  - cross-turn continuity (缺口 1): round-1 "下半年去看风声音乐剧怎么穿" →
    thread_grounding{activity, approximate_time}; a "哪个城市？" clarification
    sets pending_field=destination_city; round-2 bare "上海" (no 去X看
    structure) is that field's answer and the session stays sensitive.
  - the Main-Graph clarification_node writes pending_field into
    thread_context, and api.py's NEED_USER path persists it to the session
    cache so the next turn sees it.
"""

from __future__ import annotations

import importlib
import sys
from datetime import date
from pathlib import Path
from typing import Any

from styleforge.agentic.context.assembler import ContextAssembler
from styleforge.agentic.context.grounding import (
    GroundingDecision,
    GroundingResolver,
    pending_field_for_question,
    thread_grounding_to_prompt,
    update_thread_grounding,
)
from styleforge.agentic.context.prompt_assembler import PromptAssembler, _format_grounding
from styleforge.agentic.environment import Draft, Environment
from styleforge.agentic.graph.main import build_h2a_main_graph
from styleforge.agentic.runtime.agent_runtime import AgentRuntime
from styleforge.agentic.runtime.capability_registry import CapabilityRegistry
from styleforge.agentic.tools.local_tools import (
    AGENT_STYLIST,
    CAP_WEATHER,
    CAP_WEB_SEARCH,
    register_local_tools,
)
from styleforge.models.agentic_contract import EnvironmentFacts, OutfitSnapshot
from styleforge.models.task import TaskExecutionInput
from styleforge.tools.weather.schemas import DeviceLocationContext

from tests.helpers import make_item
from tests.llm.fake_llm import FakeLlm

_INSTRUCTIONS = Path(__file__).resolve().parent.parent / "apps/api/styleforge/agentic/instructions"
_TODAY = date(2026, 8, 19)
_NOW = "2026-08-19T00:00:00+00:00"


def _resolver(**kwargs) -> GroundingResolver:
    return GroundingResolver(today_provider=lambda: _TODAY, **kwargs)


# ── decision determinism (missing_kinds → capability) ───────────────────────

def test_not_sensitive_is_ready_no_event_context() -> None:
    ctx = _resolver().resolve("帮我搭一套", capabilities=frozenset())
    assert ctx.decision == GroundingDecision.READY
    assert ctx.reason == "no_event_context"
    assert ctx.current_date == "2026-08-19"
    assert ctx.missing == []


def test_fully_grounded_is_ready() -> None:
    ctx = _resolver().resolve(
        "下周去北京看音乐剧",
        capabilities=frozenset({CAP_WEB_SEARCH}),
    )
    assert ctx.decision == GroundingDecision.READY
    assert ctx.reason == "fully_grounded"
    assert ctx.destination_city == "北京"
    assert ctx.date_expression == "下周"
    assert ctx.approximate_time == "下周"


def test_event_with_web_capability_is_search_first() -> None:
    # destination missing but search_web can resolve it → SEARCH_FIRST.
    ctx = _resolver().resolve(
        "下周去看音乐剧", capabilities=frozenset({CAP_WEB_SEARCH})
    )
    assert ctx.decision == GroundingDecision.SEARCH_FIRST
    assert "event_location" in ctx.missing
    assert "weather" in ctx.missing  # no location → weather gap tracked too
    assert ctx.reason == "resolvable_missing=['event_location']"
    # the resolvable date is present, so no event_date gap
    assert "event_date" not in ctx.missing
    assert ctx.destination_city is None


def test_event_without_tools_is_need_user() -> None:
    ctx = _resolver().resolve("下周去看音乐剧", capabilities=frozenset())
    assert ctx.decision == GroundingDecision.NEED_USER
    assert ctx.reason == "unresolvable_missing=['event_location', 'weather']"


def test_missing_location_with_weather_only_capability_is_need_user() -> None:
    # 冻结缺口 4: a weather capability cannot resolve a missing event location.
    ctx = _resolver().resolve("去看音乐剧", capabilities=frozenset({CAP_WEATHER}))
    assert ctx.decision == GroundingDecision.NEED_USER
    assert "event_location" in ctx.missing
    assert "weather" in ctx.missing
    assert ctx.reason == (
        "unresolvable_missing=['event_date', 'event_location', 'weather']"
    )


def test_event_noun_is_never_a_destination() -> None:
    # "去婚礼的" is an activity, not a place — no false has_destination.
    ctx = _resolver().resolve("帮我搭一套去婚礼的", capabilities=frozenset({CAP_WEATHER}))
    assert ctx.destination_city is None
    assert ctx.decision == GroundingDecision.NEED_USER


def test_weather_missing_never_forces_search_first_without_location() -> None:
    # even with both capabilities, the weather gap (no location to query) never
    # enters required_searchable — only event_location is resolvable (web).
    ctx = _resolver().resolve(
        "下周去看音乐剧", capabilities=frozenset({CAP_WEB_SEARCH, CAP_WEATHER})
    )
    assert ctx.decision == GroundingDecision.SEARCH_FIRST
    assert "weather" in ctx.missing
    assert ctx.reason == "resolvable_missing=['event_location']"


# ── extraction / sources / explicit dates ───────────────────────────────────

def test_tomorrow_shanghai_business_trip_fully_grounded() -> None:
    ctx = _resolver().resolve("明天去上海出差", capabilities=frozenset())
    assert ctx.destination_city == "上海"
    assert ctx.location_source == "request"
    assert ctx.explicit_date == "2026-08-20"  # today+1, precision=day
    assert ctx.approximate_time == "明天"
    assert ctx.date_source == "request"
    assert ctx.decision == GroundingDecision.READY
    assert ctx.reason == "fully_grounded"


def test_half_year_is_approximate() -> None:
    ctx = _resolver().resolve("下半年", capabilities=frozenset())
    assert ctx.date_expression == "下半年"
    assert ctx.approximate_time == "下半年"
    assert ctx.explicit_date is None
    assert ctx.decision == GroundingDecision.READY


def test_profile_default_city() -> None:
    ctx = _resolver().resolve(
        "帮我搭一套", environment_profile={"default_city": "杭州"}, capabilities=frozenset()
    )
    assert ctx.current_city == "杭州"
    assert ctx.location_source == "profile"


def test_global_default_city() -> None:
    ctx = _resolver(default_location="北京").resolve("帮我搭一套", capabilities=frozenset())
    assert ctx.current_city == "北京"
    assert ctx.location_source == "global"


def test_device_coordinates_give_no_reverse_geocode() -> None:
    device = DeviceLocationContext(
        latitude=31.23,
        longitude=121.47,
        accuracy_m=50.0,
        captured_at="2026-08-19T00:00:00+00:00",
        consent_granted=True,
    )
    ctx = _resolver().resolve("帮我搭一套", location_context=device, capabilities=frozenset())
    # coordinates with no reverse geocoding → no city name, source=device
    assert ctx.current_city is None
    assert ctx.location_source == "device"


def test_today_provider_is_injectable() -> None:
    ctx = _resolver().resolve("帮我搭一套", capabilities=frozenset())
    assert ctx.current_date == "2026-08-19"


# ── cross-turn continuity (Question → Answer → Grounding) ───────────────────

def test_thread_grounding_continuity_via_pending_field() -> None:
    resolver = _resolver()
    # round 1
    round1 = update_thread_grounding({}, "下半年去看风声音乐剧怎么穿", now=_NOW)
    assert round1["activity"] == "观看《风声》"
    assert round1["approximate_time"] == "下半年"
    assert round1["pending_field"] is None
    # clarification question deterministically names the field
    assert pending_field_for_question("你准备在哪个城市看？") == "destination_city"
    assert pending_field_for_question("打算什么时候去？") == "date"
    # round 2: bare "上海" is that field's answer — no 去X看 structure
    round2 = update_thread_grounding(round1, "上海", now=_NOW)
    assert round2["destination_city"] == "上海"
    assert round2["pending_field"] is None
    assert round2["activity"] == "观看《风声》"  # thread activity survives
    # the resolver stays sensitive via the thread's activity, and reuses the
    # thread facts instead of re-deriving them from a bare reply.
    ctx = resolver.resolve(
        "上海",
        thread_context={"thread_grounding": round2},
        capabilities=frozenset({CAP_WEB_SEARCH}),
    )
    assert ctx.decision == GroundingDecision.READY
    assert ctx.destination_city == "上海"
    assert ctx.activity == "观看《风声》"
    assert ctx.date_expression == "下半年"
    assert ctx.approximate_time == "下半年"
    assert ctx.date_source == "thread"


def test_thread_grounding_prompt_render() -> None:
    view = update_thread_grounding({}, "下半年去看风声音乐剧怎么穿", now=_NOW)
    text = thread_grounding_to_prompt(view)
    assert "观演城市" not in text  # no destination yet
    assert "时间：下半年" in text
    assert "活动：观看《风声》" in text


# ── Main-Graph clarification_node writes pending_field ──────────────────────

def _runtime(llm: FakeLlm) -> AgentRuntime:
    items = [
        make_item("top-1", "top", "白衬衫", "white"),
        make_item("pants-1", "pants", "黑西裤", "black"),
    ]
    env = Environment(connection=None, wardrobe_items=items, facts=EnvironmentFacts())
    registry = CapabilityRegistry()
    register_local_tools(registry, env)
    return AgentRuntime(
        llm=llm,
        registry=registry,
        instructions_root=_INSTRUCTIONS,
        runtime_capabilities=frozenset(),
    )


def _base_draft() -> Draft:
    return Draft(outfit=OutfitSnapshot(outfit_id="draft", item_ids=[], items=[]), layers={})


class _FakeEnvironment:
    def check_environment(self, draft: Any) -> Any:
        return type("R", (), {"valid": True, "issues": []})()


def test_clarification_node_writes_pending_field() -> None:
    llm = FakeLlm(
        [
            {
                "decision_summary": "问城市",
                "goal": "看剧穿搭",
                "need_user": True,
                "clarification": {"question": "你准备在哪个城市看？", "reason": "缺地点"},
            }
        ]
    )
    graph = build_h2a_main_graph(_runtime(llm), environment=_FakeEnvironment(), target_candidates=3)
    out = graph.invoke(
        {
            "run_id": "r",
            "request": "下半年去看风声音乐剧怎么穿",
            "base_draft": _base_draft(),
            "thread_context": {
                "current_outfit_id": "",
                "current_item_ids": [],
                "thread_grounding": {"activity": "观看《风声》"},
            },
        }
    )
    assert out["status"] == "needs_clarification"
    assert out["clarification_question"] == "你准备在哪个城市看？"
    assert out["thread_context"]["thread_grounding"]["pending_field"] == "destination_city"


def test_clarification_node_date_question_pending_field() -> None:
    llm = FakeLlm(
        [
            {
                "decision_summary": "问时间",
                "goal": "看剧穿搭",
                "need_user": True,
                "clarification": {"question": "打算什么时候去？", "reason": "缺日期"},
            }
        ]
    )
    graph = build_h2a_main_graph(_runtime(llm), environment=_FakeEnvironment(), target_candidates=3)
    out = graph.invoke(
        {"run_id": "r", "request": "看音乐剧穿什么", "base_draft": _base_draft()}
    )
    assert out["status"] == "needs_clarification"
    assert out["thread_context"]["thread_grounding"]["pending_field"] == "date"


def test_clarification_node_non_grounding_question_keeps_none() -> None:
    llm = FakeLlm(
        [
            {
                "decision_summary": "问别的",
                "goal": "搭配",
                "need_user": True,
                "clarification": {"question": "想换双什么鞋？", "reason": "缺信息"},
            }
        ]
    )
    graph = build_h2a_main_graph(_runtime(llm), environment=_FakeEnvironment(), target_candidates=3)
    out = graph.invoke(
        {
            "run_id": "r",
            "request": "帮我搭一套",
            "base_draft": _base_draft(),
            "thread_context": {
                "current_outfit_id": "",
                "current_item_ids": [],
                "thread_grounding": {},
            },
        }
    )
    assert out["status"] == "needs_clarification"
    assert out["thread_context"]["thread_grounding"].get("pending_field") is None


# ── prompt-level: 【环境定位】 C-layer section ──────────────────────────────

def test_format_grounding_renders_decision() -> None:
    ctx = _resolver().resolve(
        "下周去看音乐剧", capabilities=frozenset({CAP_WEB_SEARCH})
    )
    text = _format_grounding(ctx.model_dump(mode="json"))
    assert "【环境定位】" in text
    assert "当前日期：2026-08-19" in text
    assert "决策：先查证再作答" in text
    assert "event_location" in text


def test_stylist_prompt_carries_grounding_after_thread_and_memories() -> None:
    grounding = _resolver().resolve(
        "下周去看音乐剧", capabilities=frozenset({CAP_WEB_SEARCH})
    ).model_dump(mode="json")
    context = ContextAssembler().assemble(
        AGENT_STYLIST,
        {
            "request": "下周去看音乐剧怎么穿",
            "thread_context": {
                "current_outfit_id": "",
                "current_item_ids": [],
                "thread_grounding": {"activity": "观看《风声》"},
            },
            "recalled_memories": ["喜欢深色系"],
            "grounding_context": grounding,
        },
    )
    registry = CapabilityRegistry()
    register_local_tools(registry, object())
    tools = registry.runtime_available(AGENT_STYLIST, frozenset({CAP_WEB_SEARCH}))
    bundle = PromptAssembler(instructions_root=_INSTRUCTIONS).build(AGENT_STYLIST, context, tools)
    text = bundle.system_text
    assert "【环境定位】" in text
    assert "【对话上下文】" in text
    assert "【相关记忆】" in text
    # C-layer tail order: thread → memories → grounding (decision read last).
    assert text.index("【对话上下文】") < text.index("【相关记忆】")
    assert text.index("【相关记忆】") < text.index("【环境定位】")


# ── api NEED_USER persistence of pending_field ──────────────────────────────

def _import_api(db_dsn: str, monkeypatch) -> dict:
    monkeypatch.setenv("STYLEFORGE_DATABASE_DSN", db_dsn)
    sys.modules.pop("styleforge.api", None)
    api = importlib.import_module("styleforge.api")
    from styleforge.repositories.database import initialize_database

    initialize_database(db_dsn)
    return {"api": api, "database_path": db_dsn}


def test_persist_clarification_pending_field_writes_cache(db_dsn, monkeypatch) -> None:
    context = _import_api(db_dsn, monkeypatch)
    api = context["api"]
    written: dict = {}
    monkeypatch.setattr(
        api,
        "get_session_outfit_cache",
        lambda client, sid: {"thread_grounding": {"activity": "观看《风声》"}},
    )
    monkeypatch.setattr(
        api, "set_session_outfit_cache", lambda client, sid, ctx, ttl: written.update(ctx)
    )
    payload = {
        "agentic_outcome": {
            "status": "needs_clarification",
            "thread_context": {
                "thread_grounding": {"pending_field": "destination_city"}
            },
        }
    }
    api._persist_clarification_pending_field(
        TaskExecutionInput(user_id="u", session_id="sess-1", request="下半年去看风声音乐剧怎么穿"),
        payload,
    )
    assert written["thread_grounding"]["pending_field"] == "destination_city"
    assert written["thread_grounding"]["activity"] == "观看《风声》"  # merged, not dropped


def test_persist_skips_completed_outcome(db_dsn, monkeypatch) -> None:
    context = _import_api(db_dsn, monkeypatch)
    api = context["api"]
    written: dict = {}
    monkeypatch.setattr(api, "get_session_outfit_cache", lambda client, sid: {})
    monkeypatch.setattr(
        api, "set_session_outfit_cache", lambda client, sid, ctx, ttl: written.update(ctx)
    )
    api._persist_clarification_pending_field(
        TaskExecutionInput(user_id="u", session_id="sess-1", request="帮我搭一套"),
        {"agentic_outcome": {"status": "completed"}},
    )
    assert written == {}


def test_persist_handles_list_outcome_modify_path(db_dsn, monkeypatch) -> None:
    context = _import_api(db_dsn, monkeypatch)
    api = context["api"]
    written: dict = {}
    monkeypatch.setattr(api, "get_session_outfit_cache", lambda client, sid: {})
    monkeypatch.setattr(
        api, "set_session_outfit_cache", lambda client, sid, ctx, ttl: written.update(ctx)
    )
    # modify batches outcomes in a list; only the first carries the envelope.
    payload = {
        "agentic_outcome": [
            {
                "status": "needs_clarification",
                "thread_context": {"thread_grounding": {"pending_field": "date"}},
            }
        ]
    }
    api._persist_clarification_pending_field(
        TaskExecutionInput(user_id="u", session_id="sess-1", request="什么时候去？"),
        payload,
    )
    assert written["thread_grounding"]["pending_field"] == "date"
