"""AgentLoop: the bounded ReAct loop with the program-forced finish gate.

The Agent decides when it *thinks* it is done; the program decides whether it
is *allowed* to be done. The finish gate is forced even if the Agent never
calls check_environment itself: check_environment PASS AND review_outfit PASS
before SUCCESS, everything else is an observation the Agent replans on.

This test drives the loop with a ScriptedExtensionLlm (no DB needed — the
environment's wardrobe is fully resolved from in-memory items).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.request import Request

from styleforge.agentic.agent import RECOMMEND_SYSTEM_PROMPT, AgentLoop, LoopConfig
from styleforge.agentic.environment import Environment
from styleforge.agentic.structure import structure_for
from styleforge.models.agentic_contract import (
    EnvironmentFacts,
    InteractionContext,
    ItemSnapshot,
    OutfitSnapshot,
)
from styleforge.tools.weather.schemas import ResolvedLocation, WeatherDay, WeatherFacts
from styleforge.tools.web_search import TavilySearchProvider

from tests.extension_llm import ScriptedExtensionLlm
from tests.helpers import make_item


def _item_snapshot(item: Any) -> ItemSnapshot:
    return ItemSnapshot(
        item_id=item.item_id,
        name=item.name,
        item_type=item.item_type,
        color=item.color,
        structure=structure_for(item.item_type),
    )


WARDROBE = [
    make_item("shirt_a", "top", "White shirt", "white"),
    make_item("pants_b", "pants", "Black trousers", "black"),
    make_item("skirt_c", "skirt", "Blue skirt", "blue"),
    make_item("boots_c", "shoes", "Brown boots", "brown"),
    make_item("sneakers_d", "shoes", "White sneakers", "white"),
    make_item("coat_e", "outwear", "Gray coat", "gray"),
]


def _env(*, active_ids: list[str] | None = None) -> Environment:
    active_ids = active_ids or ["shirt_a", "pants_b", "boots_c"]
    by_id = {item.item_id: item for item in WARDROBE}
    active_items = [_item_snapshot(by_id[item_id]) for item_id in active_ids]
    facts = EnvironmentFacts(
        interaction=InteractionContext(active_outfit_id="o1"),
        active_outfit=OutfitSnapshot(
            outfit_id="o1",
            item_ids=list(active_ids),
            items=active_items,
        ),
        wardrobe_summary={},
    )
    return Environment(connection=None, wardrobe_items=WARDROBE, facts=facts)


def _step(action: str, **kw: Any) -> dict[str, Any]:
    step: dict[str, Any] = {
        "thought": "（原始推理，绝不出现在 trace 中）",
        "goal": "换双鞋",
        "requirements": ["要休闲些"],
        "action": action,
        "query": "",
        "outfit_id": "",
        "location": "",
        "date": "",
        "plan": None,
        "plan_state": None,
        "question": "",
    }
    step.update(kw)
    return step


def _modify_plan(**ops: dict[str, Any]) -> dict[str, Any]:
    return {"ops": [ops], "reasoning": "换双鞋"}


# ── happy path ───────────────────────────────────────────────────────


def test_happy_path_replace_then_finish_succeeds() -> None:
    llm = ScriptedExtensionLlm(
        [
            _step(
                "modify_outfit",
                plan=_modify_plan(
                    action="replace",
                    item_id="boots_c",
                    replacement_item_id="sneakers_d",
                    placement={"region": "feet", "layer": "base"},
                ),
            ),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env(), "换双休闲点的鞋").run()

    assert outcome["status"] == "success"
    assert outcome["candidate"]["item_ids"] == ["shirt_a", "pants_b", "sneakers_d"]
    assert outcome["review"]["approved"] is True
    assert outcome["llm_call_count"] == 3  # modify + finish + reviewer


def test_trace_never_leaks_raw_thought() -> None:
    llm = ScriptedExtensionLlm(
        [
            _step("search_wardrobe", query="boots"),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env(), "换双鞋").run()

    assert outcome["status"] == "success"
    for step in outcome["steps"]:
        assert set(step.keys()) == {
            "step",
            "action",
            "args",
            "observation",
            "plan_state",
            "external_context_decision",
        }
        assert "thought" not in str(step)


# ── finish gate ──────────────────────────────────────────────────────


def test_finish_forced_check_fails_then_replan_succeeds() -> None:
    # The active outfit already carries two pairs of shoes ((feet,base) conflict).
    # The Agent calls finish without ever checking — the program forces the gate,
    # returns the failure as an observation, and the Agent replans.
    llm = ScriptedExtensionLlm(
        [
            _step("finish"),
            _step(
                "modify_outfit",
                plan=_modify_plan(action="remove", item_id="sneakers_d"),
            ),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    env = _env(active_ids=["shirt_a", "pants_b", "boots_c", "sneakers_d"])
    outcome = AgentLoop(llm, env, "只要一双鞋").run()

    assert outcome["status"] == "success"
    assert outcome["candidate"]["item_ids"] == ["shirt_a", "pants_b", "boots_c"]
    actions = [step["action"] for step in outcome["steps"]]
    assert "check_environment" in actions  # forced by the program, not the Agent
    assert any("物理校验未通过" in step["observation"] for step in outcome["steps"])


def test_finish_review_fails_then_replan_succeeds() -> None:
    # Physical check passes, but the Reviewer rejects the result (semantic
    # intent fidelity). Its feedback returns to the Agent as an observation.
    llm = ScriptedExtensionLlm(
        [
            _step(
                "modify_outfit",
                plan=_modify_plan(
                    action="replace",
                    item_id="boots_c",
                    replacement_item_id="sneakers_d",
                    placement={"region": "feet", "layer": "base"},
                ),
            ),
            _step("finish"),
            {"approved": False, "issues": ["不够正式"], "feedback": "用户要正式场合，运动鞋不合适"},
            _step(
                "modify_outfit",
                plan=_modify_plan(
                    action="replace",
                    item_id="sneakers_d",
                    replacement_item_id="boots_c",
                    placement={"region": "feet", "layer": "base"},
                ),
            ),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env(), "换双正式点的鞋").run()

    assert outcome["status"] == "success"
    assert outcome["candidate"]["item_ids"] == ["shirt_a", "pants_b", "boots_c"]
    assert any("review_outfit" in step["action"] for step in outcome["steps"])
    assert any("运动鞋不合适" in step["observation"] for step in outcome["steps"])
    assert outcome["llm_call_count"] == 6  # 4 agent + 2 reviewer calls


# ── suspension / bounds ──────────────────────────────────────────────


def test_ask_user_suspends_the_loop() -> None:
    llm = ScriptedExtensionLlm(
        [_step("ask_user", question="衣橱里没有黑色皮鞋，要换双棕色靴子吗？")]
    )
    outcome = AgentLoop(llm, _env(), "换双皮鞋").run()

    assert outcome["status"] == "ask_user"
    assert outcome["ask_user"]["question"]
    # Draft is the original outfit — nothing was committed.
    assert outcome["candidate"]["item_ids"] == ["shirt_a", "pants_b", "boots_c"]
    assert outcome["steps"] == []


def test_timeout_bounded_loop() -> None:
    llm = ScriptedExtensionLlm(
        [_step("search_wardrobe", query="leather") for _ in range(3)]
    )
    outcome = AgentLoop(llm, _env(), "换双鞋", config=LoopConfig(max_steps=3)).run()

    assert outcome["status"] == "timeout"
    assert len(outcome["steps"]) == 3
    assert all(step["action"] == "search_wardrobe" for step in outcome["steps"])


def test_repeated_identical_action_is_flagged() -> None:
    # C4 regressed: with no active outfit the model re-inspected "active" 8×
    # and timed out instead of asking. The program must surface the stall so
    # the model changes strategy.
    llm = ScriptedExtensionLlm(
        [_step("inspect_outfit", outfit_id="active") for _ in range(4)]
        + [_step("ask_user", question="要修改哪一套？")]
    )
    outcome = AgentLoop(llm, _env(), "鞋换一下").run()

    assert outcome["status"] == "ask_user"
    flagged = [s for s in outcome["steps"] if "连续 3 步执行相同操作" in s["observation"]]
    assert len(flagged) >= 1


def test_inspect_active_returns_current_draft() -> None:
    # A real model says outfit_id="active" to mean "the outfit I am editing".
    # The dispatcher must return the *draft* (post-modification), not look up a
    # stored id — C2 in the real smoke stalled on "未找到搭配 active".
    llm = ScriptedExtensionLlm(
        [
            _step(
                "modify_outfit",
                plan=_modify_plan(
                    action="replace",
                    item_id="boots_c",
                    replacement_item_id="sneakers_d",
                    placement={"region": "feet", "layer": "base"},
                ),
            ),
            _step("inspect_outfit", outfit_id="active"),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env(), "换双鞋").run()

    assert outcome["status"] == "success"
    inspect_steps = [s for s in outcome["steps"] if s["action"] == "inspect_outfit"]
    assert len(inspect_steps) == 1
    assert "正在编辑" in inspect_steps[0]["observation"]
    assert "sneakers_d" in inspect_steps[0]["observation"]  # the draft, not o1


def test_natural_language_placement_is_normalised_to_enums() -> None:
    # A real model says region="legs", layer="bottom" instead of the ontology
    # values. The program translates the aliases deterministically before the
    # plan is validated and executed.
    llm = ScriptedExtensionLlm(
        [
            _step(
                "modify_outfit",
                plan={
                    "ops": [
                        {
                            "action": "replace",
                            "item_id": "pants_b",
                            "replacement_item_id": "skirt_c",
                            "placement": {"region": "legs", "layer": "bottom"},
                        }
                    ],
                    "reasoning": "换裙子",
                },
            ),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env(), "把裤子换成裙子").run()

    assert outcome["status"] == "success"
    assert outcome["candidate"]["item_ids"] == ["shirt_a", "skirt_c", "boots_c"]


def test_blank_placement_fields_drop_to_structure() -> None:
    # The prompt tells the model it may leave a field blank; a real model then
    # emits layer="" (empty string, not null). "" means "not provided" — the
    # environment fills it from the garment's structure. Third smoke crash.
    llm = ScriptedExtensionLlm(
        [
            _step(
                "modify_outfit",
                plan={
                    "ops": [
                        {
                            "action": "replace",
                            "item_id": "boots_c",
                            "replacement_item_id": "sneakers_d",
                            "placement": {"region": "", "layer": ""},
                        }
                    ],
                    "reasoning": "换双运动鞋",
                },
            ),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env(), "鞋换双运动鞋").run()

    assert outcome["status"] == "success"
    assert outcome["candidate"]["item_ids"] == ["shirt_a", "pants_b", "sneakers_d"]


def test_item_type_word_in_layer_slot_drops_to_structure() -> None:
    # A real model wrote layer="shoes" (an item type) where the layer belongs.
    # The layer is a decision, so the program drops it and the environment
    # derives the garment's effective layer — the second smoke crash in C2.
    llm = ScriptedExtensionLlm(
        [
            _step(
                "modify_outfit",
                plan={
                    "ops": [
                        {
                            "action": "replace",
                            "item_id": "boots_c",
                            "replacement_item_id": "sneakers_d",
                            "placement": {"region": "feet", "layer": "shoes"},
                        }
                    ],
                    "reasoning": "换双运动鞋",
                },
            ),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env(), "鞋换双运动鞋").run()

    assert outcome["status"] == "success"
    assert outcome["candidate"]["item_ids"] == ["shirt_a", "pants_b", "sneakers_d"]


def test_item_type_word_as_region_is_normalised() -> None:
    # A real model writes region="shoes" (the item type) instead of the body
    # region "feet", and omits the layer. Both are translated deterministically.
    llm = ScriptedExtensionLlm(
        [
            _step(
                "modify_outfit",
                plan={
                    "ops": [
                        {
                            "action": "replace",
                            "item_id": "boots_c",
                            "replacement_item_id": "sneakers_d",
                            "placement": {"region": "shoes"},
                        }
                    ],
                    "reasoning": "换双运动鞋",
                },
            ),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env(), "鞋换双运动鞋").run()

    assert outcome["status"] == "success"
    assert outcome["candidate"]["item_ids"] == ["shirt_a", "pants_b", "sneakers_d"]


def test_null_optional_fields_from_real_model_are_tolerated() -> None:
    # A real JSON-mode model fills unused fields with null rather than omitting
    # them. The dispatcher must normalise None -> "" at the tool boundary.
    llm = ScriptedExtensionLlm(
        [
            _step("search_wardrobe", query="boots", outfit_id=None, question=None),
            _step("finish", outfit_id=None, question=None),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env(), "换双鞋").run()

    assert outcome["status"] == "success"
    assert any(step["action"] == "search_wardrobe" for step in outcome["steps"])


def test_intent_is_carried_from_first_decision() -> None:
    llm = ScriptedExtensionLlm(
        [
            _step(
                "modify_outfit",
                goal="把靴子换成更轻便的运动鞋",
                requirements=["不要高跟", "浅色优先"],
                plan=_modify_plan(
                    action="replace",
                    item_id="boots_c",
                    replacement_item_id="sneakers_d",
                    placement={"region": "feet", "layer": "base"},
                ),
            ),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env(), "把靴子换成运动鞋，不要高跟").run()

    assert outcome["status"] == "success"
    assert outcome["intent"]["goal"] == "把靴子换成更轻便的运动鞋"
    assert outcome["intent"]["requirements"] == ["不要高跟", "浅色优先"]


# ── search_web (web search beyond the wardrobe) ──────────────────────


def _env_with_web(provider: Any) -> Environment:
    active_ids = ["shirt_a", "pants_b", "boots_c"]
    by_id = {item.item_id: item for item in WARDROBE}
    active_items = [_item_snapshot(by_id[item_id]) for item_id in active_ids]
    facts = EnvironmentFacts(
        interaction=InteractionContext(active_outfit_id="o1"),
        active_outfit=OutfitSnapshot(
            outfit_id="o1",
            item_ids=list(active_ids),
            items=active_items,
        ),
        wardrobe_summary={},
    )
    return Environment(
        connection=None,
        wardrobe_items=WARDROBE,
        facts=facts,
        web_search_provider=provider,
    )


def _web_hits() -> dict[str, Any]:
    return {
        "results": [
            {
                "title": "商务晚宴着装指南",
                "content": "正式场合建议西装皮鞋，避免运动鞋",
                "url": "https://style.example/1",
            },
            {
                "title": "海边度假穿搭",
                "content": "速干短裤加凉鞋",
                "url": "https://style.example/2",
            },
        ]
    }


def _web_provider() -> TavilySearchProvider:
    def _transport(request: Request, timeout: float) -> dict[str, Any]:
        return _web_hits()

    return TavilySearchProvider(api_key="tvly-test", transport=_transport)


def test_search_web_observation_enters_steps() -> None:
    # A successful web search lands its formatted observation in the trace, so
    # the user can see what external knowledge the Agent consulted.
    llm = ScriptedExtensionLlm(
        [
            _step("search_web", query="商务晚宴着装"),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env_with_web(_web_provider()), "帮我看看晚宴穿什么").run()

    assert outcome["status"] == "success"
    web_steps = [s for s in outcome["steps"] if s["action"] == "search_web"]
    assert len(web_steps) == 1
    assert "商务晚宴着装指南" in web_steps[0]["observation"]
    assert "仅供知识参考" in web_steps[0]["observation"]


def test_search_web_then_modify_uses_wardrobe_id() -> None:
    # Web results are knowledge references only: modify_outfit must still use
    # ids from the wardrobe, never an id fabricated from search content.
    llm = ScriptedExtensionLlm(
        [
            _step("search_web", query="正式场合穿什么"),
            _step(
                "modify_outfit",
                plan=_modify_plan(
                    action="replace",
                    item_id="boots_c",
                    replacement_item_id="sneakers_d",
                    placement={"region": "feet", "layer": "base"},
                ),
            ),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env_with_web(_web_provider()), "换双鞋").run()

    assert outcome["status"] == "success"
    assert outcome["candidate"]["item_ids"] == ["shirt_a", "pants_b", "sneakers_d"]


def test_search_web_no_key_degrades_without_raising() -> None:
    # No provider (the default): search_web returns an "unconfigured" observation
    # and the loop continues to a successful finish.
    llm = ScriptedExtensionLlm(
        [
            _step("search_web", query="海边穿搭"),
            _step(
                "modify_outfit",
                plan=_modify_plan(
                    action="replace",
                    item_id="boots_c",
                    replacement_item_id="sneakers_d",
                    placement={"region": "feet", "layer": "base"},
                ),
            ),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env(), "换双鞋").run()

    assert outcome["status"] == "success"
    web_steps = [s for s in outcome["steps"] if s["action"] == "search_web"]
    assert len(web_steps) == 1
    assert "未配置" in web_steps[0]["observation"]


def test_external_context_decision_is_recorded_in_trace() -> None:
    # The Agent's judgement on whether external facts were needed lands in the
    # trace as lightweight advisory metadata (for tool-use appropriateness
    # evaluation) — it never gates behaviour.
    llm = ScriptedExtensionLlm(
        [
            _step(
                "search_web",
                query="北京茶博会活动穿什么",
                external_context_decision={
                    "needed": True,
                    "reason": "茶博会的地点和形式可能影响穿搭",
                },
            ),
            _step(
                "modify_outfit",
                plan=_modify_plan(
                    action="replace",
                    item_id="boots_c",
                    replacement_item_id="sneakers_d",
                    placement={"region": "feet", "layer": "base"},
                ),
                external_context_decision={
                    "needed": False,
                    "reason": "换鞋是已有穿搭的局部调整，无需外部事实",
                },
            ),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env_with_web(_web_provider()), "下周去茶博会，帮我换双鞋").run()

    assert outcome["status"] == "success"
    # Only executed tools enter the trace (finish is a commit action, not a
    # step), so exactly two decisions are recorded here.
    decisions = [s["external_context_decision"] for s in outcome["steps"]]
    assert len(decisions) == 2
    assert decisions[0] == {
        "needed": True,
        "reason": "茶博会的地点和形式可能影响穿搭",
    }
    assert decisions[1] == {
        "needed": False,
        "reason": "换鞋是已有穿搭的局部调整，无需外部事实",
    }


# ── Plan State ─────────────────────────────────────────────────────


def test_plan_state_recorded_in_trace_without_thought() -> None:
    # A complex task's structured plan lands in the trace as a decision summary
    # (objective / missing_information / next_steps ...) — never raw thought.
    llm = ScriptedExtensionLlm(
        [
            _step(
                "search_web",
                query="北京 莫里哀音乐剧 演出时间",
                plan_state={
                    "objective": "下周去北京看莫里哀音乐剧的穿搭",
                    "missing_information": ["演出日期", "场馆", "主题"],
                    "next_steps": ["search_web 查演出信息", "查天气"],
                    "completed_steps": [],
                    "remaining_steps": ["组合完整搭配"],
                },
            ),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env(), "下周去北京看莫里哀音乐剧穿什么").run()

    assert outcome["status"] == "success"
    recorded = [step["plan_state"] for step in outcome["steps"] if step["plan_state"]]
    assert len(recorded) == 1
    assert recorded[0]["objective"] == "下周去北京看莫里哀音乐剧的穿搭"
    assert recorded[0]["missing_information"] == ["演出日期", "场馆", "主题"]
    assert recorded[0]["remaining_steps"] == ["组合完整搭配"]
    assert "thought" not in str(outcome["steps"])


# ── Skill system ───────────────────────────────────────────────────


def _env_with_skill(tmp_path: Path) -> Environment:
    env = _env()
    skill_dir = tmp_path / "tasks" / "event_outfit_planning"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# Event Outfit Planning\n1. 识别活动实体\n2. search_web 查事实\n",
        encoding="utf-8",
    )
    env.skills_root = tmp_path
    return env


def test_load_skill_observation_enters_steps(tmp_path: Path) -> None:
    llm = ScriptedExtensionLlm(
        [
            _step("load_skill", query="event_outfit_planning"),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env_with_skill(tmp_path), "下周去北京看音乐剧穿什么").run()

    assert outcome["status"] == "success"
    skill_steps = [step for step in outcome["steps"] if step["action"] == "load_skill"]
    assert len(skill_steps) == 1
    assert skill_steps[0]["args"] == {"skill_name": "event_outfit_planning"}
    assert "已加载技能" in skill_steps[0]["observation"]
    assert "Event Outfit Planning" in skill_steps[0]["observation"]


def test_load_skill_missing_degrades_gracefully() -> None:
    # No skills_root configured: the tool is a fact the Agent works around.
    llm = ScriptedExtensionLlm(
        [
            _step("load_skill", query="event_outfit_planning"),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, _env(), "下周去北京看音乐剧穿什么").run()

    assert outcome["status"] == "success"
    skill_steps = [step for step in outcome["steps"] if step["action"] == "load_skill"]
    assert len(skill_steps) == 1
    assert "技能不可用" in skill_steps[0]["observation"]


class _FakeWeatherProvider:
    """WeatherProvider stand-in: every named location resolves with one day."""

    def resolve_location(self, query: str):
        return ResolvedLocation(
            name=query, country="CN", latitude=39.9, longitude=116.4
        )

    def forecast_range(
        self,
        location: ResolvedLocation,
        start_date: Any,
        end_date: Any,
        *,
        granularity: str = "daily",
        period: str | None = None,
    ) -> WeatherFacts:
        day = WeatherDay(
            date=str(start_date),
            temperature_min_c=18,
            temperature_max_c=26,
            condition="晴",
        )
        return WeatherFacts(
            status="available",
            requested_location=location.display_name,
            resolved_location=location,
            start_date=str(start_date),
            end_date=str(end_date),
            days=[day],
        )


def test_get_weather_observation_enters_steps() -> None:
    env = _env()
    env.weather_provider = _FakeWeatherProvider()
    llm = ScriptedExtensionLlm(
        [
            _step("get_weather", location="北京", date="2026-08-20"),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(llm, env, "下周去北京穿什么").run()

    assert outcome["status"] == "success"
    weather_steps = [step for step in outcome["steps"] if step["action"] == "get_weather"]
    assert len(weather_steps) == 1
    assert weather_steps[0]["args"] == {"location": "北京", "date": "2026-08-20"}
    assert "北京, CN" in weather_steps[0]["observation"]
    assert "晴" in weather_steps[0]["observation"]


# ── Recommend mode (from-scratch generation) ───────────────────────


def test_recommend_prompt_builds_outfit_from_empty_active() -> None:
    # No active outfit; the recommend prompt must generate a complete outfit
    # from the empty draft instead of asking which outfit to edit.
    facts = EnvironmentFacts(
        interaction=InteractionContext(),
        active_outfit=None,
        wardrobe_summary={},
    )
    env = Environment(connection=None, wardrobe_items=WARDROBE, facts=facts)
    llm = ScriptedExtensionLlm(
        [
            _step(
                "modify_outfit",
                plan=_modify_plan(
                    action="add",
                    item_id="shirt_a",
                    placement={"region": "upper_body", "layer": "base"},
                ),
            ),
            _step(
                "modify_outfit",
                plan=_modify_plan(
                    action="add",
                    item_id="pants_b",
                    placement={"region": "lower_body", "layer": "base"},
                ),
            ),
            _step(
                "modify_outfit",
                plan=_modify_plan(
                    action="add",
                    item_id="sneakers_d",
                    placement={"region": "feet", "layer": "base"},
                ),
            ),
            _step("finish"),
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    outcome = AgentLoop(
        llm,
        env,
        "通勤穿搭",
        system_prompt=RECOMMEND_SYSTEM_PROMPT,
        is_recommend=True,
    ).run()

    assert outcome["status"] == "success"
    assert set(outcome["candidate"]["item_ids"]) == {"shirt_a", "pants_b", "sneakers_d"}
    assert outcome["llm_call_count"] == 5  # 3 modify + finish + reviewer
