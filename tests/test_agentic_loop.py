"""AgentLoop: the bounded ReAct loop with the program-forced finish gate.

The Agent decides when it *thinks* it is done; the program decides whether it
is *allowed* to be done. The finish gate is forced even if the Agent never
calls check_environment itself: check_environment PASS AND review_outfit PASS
before SUCCESS, everything else is an observation the Agent replans on.

This test drives the loop with a ScriptedExtensionLlm (no DB needed — the
environment's wardrobe is fully resolved from in-memory items).
"""

from __future__ import annotations

from typing import Any

from styleforge.agentic.agent import AgentLoop, LoopConfig
from styleforge.agentic.environment import Environment
from styleforge.agentic.structure import structure_for
from styleforge.models.agentic_contract import (
    EnvironmentFacts,
    InteractionContext,
    ItemSnapshot,
    OutfitSnapshot,
)

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
        "plan": None,
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
        assert set(step.keys()) == {"step", "action", "args", "observation"}
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
