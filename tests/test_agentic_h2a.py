"""H2a: Coordinator (manager-agent) + handoff + StyleForgeHarness.

Covers the frozen contracts:
  #5   Coordinator only maintains TaskState and decides the next agent; it
       never touches the candidate chain (which the Main Graph owns).
  #14  need_plan_update → exactly one update_plan call → back to Coordinator →
       NEXT turn hands off (never update_plan + handoff in one turn), and the
       single tool must be update_plan.
  #16  Coordinator three-state machine: need_user / need_plan_update / handoff
       are mutually exclusive, enforced by check_decision_contract + the
       AgentRuntime tool-count check.
  #9   The Coordinator's trajectory stays private; only TaskState + goal + the
       envelope cross to the parent.
  #20  NEED_USER returns an AgentHandoffResult; the Main Graph owns the
       ClarificationNode.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


from styleforge.agentic.agents.coordinator.graph import (
    MAX_COORDINATOR_STEPS,
    build_coordinator_subgraph,
)
from styleforge.agentic.environment import Draft, Environment
from styleforge.agentic.graph.main import build_h2a_main_graph
from styleforge.agentic.harness import StyleForgeHarness
from styleforge.agentic.runtime.agent_runtime import AgentRuntime
from styleforge.agentic.runtime.capability_registry import CapabilityRegistry
from styleforge.agentic.tools.local_tools import register_local_tools
from styleforge.models.agentic_contract import EnvironmentFacts, OutfitSnapshot

from tests.helpers import make_item
from tests.llm.fake_llm import FakeLlm

_INSTRUCTIONS = Path(__file__).resolve().parent.parent / "apps/api/styleforge/agentic/instructions"

_MODIFY_TOP_1 = {
    "name": "modify_outfit",
    "arguments": {"plan": {"ops": [{"action": "add", "item_id": "top-1"}]}},
}
_UPDATE_PLAN = {
    "name": "update_plan",
    "arguments": {
        "plan": {
            "objective": "下周看剧穿搭",
            "missing_information": ["演出场地着装要求"],
            "next_steps": ["调研场地"],
        }
    },
}
_OK = {"approved": True, "issues": [], "feedback": ""}


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


# ── Coordinator subgraph ────────────────────────────────────────────────────


def test_coordinator_handoff_writes_taskstate_and_completes() -> None:
    llm = FakeLlm([{"decision_summary": "直接交接", "goal": "换双皮鞋", "next_agent": "STYLIST"}])
    subgraph = build_coordinator_subgraph(_runtime(llm))
    out = subgraph.invoke({"request": "换双鞋", "goal": ""})

    assert out["handoff_result"].status == "COMPLETED"
    assert out["handoff_result"].clarification is None
    assert out["task_state"].next_agent == "STYLIST"
    assert out["task_state"].goal == "换双皮鞋"
    assert out["goal"] == "换双皮鞋"
    assert out["handoff_result"].trace_summary["agent"] == "coordinator"


def test_coordinator_need_user_returns_needs_clarification() -> None:
    llm = FakeLlm(
        [
            {
                "decision_summary": "信息不足",
                "goal": "看剧穿搭",
                "need_user": True,
                "clarification": {"question": "穿什么场合的？", "reason": "缺少场地信息"},
            }
        ]
    )
    subgraph = build_coordinator_subgraph(_runtime(llm))
    out = subgraph.invoke({"request": "看剧穿什么", "goal": ""})

    assert out["handoff_result"].status == "NEEDS_CLARIFICATION"
    assert out["handoff_result"].clarification.question == "穿什么场合的？"
    assert "trajectory_done" in out


def test_coordinator_plan_update_loop_then_handoff() -> None:
    # need_plan_update → one update_plan call → back to the Coordinator →
    # the NEXT turn hands off (frozen #14: never update_plan + handoff together).
    llm = FakeLlm(
        [
            (
                {"decision_summary": "先建计划", "goal": "看剧穿搭", "need_plan_update": True},
                [_UPDATE_PLAN],
            ),
            {"decision_summary": "计划好了，交接", "goal": "看剧穿搭", "next_agent": "RESEARCH"},
        ]
    )
    subgraph = build_coordinator_subgraph(_runtime(llm))
    out = subgraph.invoke({"request": "下周看剧穿什么", "goal": ""})

    assert out["handoff_result"].status == "COMPLETED"
    assert out["task_state"].next_agent == "RESEARCH"
    # update_plan wrote the new plan back through the shared channel.
    assert out["plan"] is not None
    assert out["plan"].objective == "下周看剧穿搭"
    assert out["handoff_result"].trace_summary["turns"] == 2


def test_coordinator_handoff_without_next_agent_recovers() -> None:
    # Handoff mode requires next_agent; a missing one is a recoverable protocol
    # error — re-entered once with the observation, then the Agent recovers.
    llm = FakeLlm(
        [
            {"decision_summary": "忘记交接", "goal": "换鞋"},  # contract violation
            {"decision_summary": "补上交接", "goal": "换鞋", "next_agent": "STYLIST"},
        ]
    )
    subgraph = build_coordinator_subgraph(_runtime(llm))
    out = subgraph.invoke({"request": "换鞋", "goal": ""})

    assert out["handoff_result"].status == "COMPLETED"
    assert out["task_state"].next_agent == "STYLIST"
    protocol_obs = [o for o in out["tool_observations"] if o["tool"] == "__protocol__"]
    assert len(protocol_obs) == 1
    assert "requires next_agent" in protocol_obs[0]["observation"]


def test_coordinator_plan_update_with_wrong_tool_is_protocol_error() -> None:
    # need_plan_update must be the update_plan tool, never a wardrobe mutation.
    llm = FakeLlm(
        [
            (
                {"decision_summary": "错用工具", "goal": "看剧", "need_plan_update": True},
                [_MODIFY_TOP_1],
            ),
            {"decision_summary": "改正", "goal": "看剧", "next_agent": "STYLIST"},
        ]
    )
    subgraph = build_coordinator_subgraph(_runtime(llm))
    out = subgraph.invoke({"request": "看剧穿什么", "goal": ""})

    assert out["handoff_result"].status == "COMPLETED"  # recovered after re-entry
    protocol_obs = [o for o in out["tool_observations"] if o["tool"] == "__protocol__"]
    assert len(protocol_obs) == 1
    assert "无权调用" in protocol_obs[0]["observation"]
    assert protocol_obs[0]["error_code"] == "TOOL_NOT_AUTHORIZED"


def test_coordinator_protocol_error_cap() -> None:
    bad = [{"decision_summary": "x", "goal": "g"}]  # missing next_agent → violation
    # 6 protocol errors (each call consumes 2 script entries: initial + in-call
    # retry), then the consecutive-failure cap (> 5) closes the subgraph.
    llm = FakeLlm(bad * 12)
    subgraph = build_coordinator_subgraph(_runtime(llm))
    out = subgraph.invoke({"request": "x", "goal": ""})

    assert out["handoff_result"].status == "PROTOCOL_ERROR"


def test_coordinator_step_cap_returns_protocol_error() -> None:
    # MAX_COORDINATOR_STEPS plan-update turns then the cap closes the subgraph.
    llm = FakeLlm(
        [
            (
                {"decision_summary": "继续改计划", "goal": "g", "need_plan_update": True},
                [_UPDATE_PLAN],
            )
        ]
        * MAX_COORDINATOR_STEPS
    )
    subgraph = build_coordinator_subgraph(_runtime(llm))
    out = subgraph.invoke({"request": "x", "goal": ""})

    assert out["handoff_result"].status == "PROTOCOL_ERROR"
    assert out["trajectory_step_count"] == MAX_COORDINATOR_STEPS


# ── Main Graph with Coordinator ─────────────────────────────────────────────


def test_h2a_plain_modify_goes_straight_to_stylist() -> None:
    # Coordinator hands off to STYLIST with zero tools; the graph runs the
    # Stylist chain once and finishes. No RESEARCH node is ever visited.
    llm = FakeLlm(
        [
            {"decision_summary": "普通修改", "goal": "换双皮鞋", "next_agent": "STYLIST"},
            ({"decision_summary": "换鞋", "control": "CONTINUE"}, [_MODIFY_TOP_1]),
            {"decision_summary": "完成", "control": "CANDIDATE_READY"},
            _OK,
        ]
    )
    graph = build_h2a_main_graph(_runtime(llm), environment=_FakeEnvironment(), target_candidates=1)
    out = graph.invoke({"run_id": "r", "request": "换双鞋", "base_draft": _base_draft()})

    assert out["status"] == "done"
    assert len(out["candidates"]) == 1
    # The Coordinator's confirmed goal reached the Stylist's prompt (D layer).
    assert "已确认目标：换双皮鞋" in llm.calls[1]["user"]


def test_h2a_coordinator_clarification_reaches_main_node() -> None:
    llm = FakeLlm(
        [
            {
                "decision_summary": "要问",
                "goal": "看剧穿搭",
                "need_user": True,
                "clarification": {"question": "哪里的演出？", "reason": "缺地点"},
            }
        ]
    )
    graph = build_h2a_main_graph(_runtime(llm), environment=_FakeEnvironment(), target_candidates=3)
    out = graph.invoke({"run_id": "r", "request": "看剧穿什么", "base_draft": _base_draft()})

    assert out["status"] == "needs_clarification"
    assert out["clarification_question"] == "哪里的演出？"


def test_h2a_plan_update_then_handoff_then_stylist() -> None:
    # Full chain: Coordinator builds a plan (update_plan), hands off to STYLIST,
    # the chain produces one candidate. The plan survives into the final state.
    llm = FakeLlm(
        [
            (
                {
                    "decision_summary": "先计划",
                    "goal": "看剧穿搭",
                    "intent": {
                        "message": "下周看剧穿什么",
                        "goal": "为下周看剧准备合适穿搭",
                        "requirements": ["结合演出场景和时间"],
                    },
                    "need_plan_update": True,
                },
                [_UPDATE_PLAN],
            ),
            {"decision_summary": "交接", "goal": "看剧穿搭", "next_agent": "STYLIST"},
            ({"decision_summary": "搭配", "control": "CONTINUE"}, [_MODIFY_TOP_1]),
            {"decision_summary": "完成", "control": "CANDIDATE_READY"},
            _OK,
        ]
    )
    graph = build_h2a_main_graph(_runtime(llm), environment=_FakeEnvironment(), target_candidates=1)
    out = graph.invoke({"run_id": "r", "request": "下周看剧穿什么", "base_draft": _base_draft()})

    assert out["status"] == "done"
    assert out["plan"].objective == "下周看剧穿搭"
    assert out["user_intent"].goal == "为下周看剧准备合适穿搭"
    assert len(out["candidates"]) == 1
    # The plan survives the subgraph boundary and is re-assembled into the
    # Dynamic plan data is visible to the Stylist but never elevated to system.
    assert "下周看剧穿搭" not in llm.calls[2]["system"]
    assert "下周看剧穿搭" in llm.calls[2]["user"]
    assert "【统一语义理解】" in llm.calls[2]["user"]
    assert "结合演出场景和时间" in llm.calls[2]["user"]


def test_h2a_harness_invoke_assembles_and_runs() -> None:
    items = [
        make_item("top-1", "top", "白衬衫", "white"),
        make_item("pants-1", "pants", "黑西裤", "black"),
    ]
    environment = Environment(connection=None, wardrobe_items=items, facts=EnvironmentFacts())
    llm = FakeLlm(
        [
            {"decision_summary": "普通修改", "goal": "换双皮鞋", "next_agent": "STYLIST"},
            ({"decision_summary": "换鞋", "control": "CONTINUE"}, [_MODIFY_TOP_1]),
            {"decision_summary": "完成", "control": "CANDIDATE_READY"},
            _OK,
        ]
    )
    harness = StyleForgeHarness(llm=llm, environment=environment, target_candidates=1)
    out = harness.invoke({"run_id": "harness-1", "request": "换双鞋", "base_draft": _base_draft()})

    assert out["status"] == "done"
    assert len(out["candidates"]) == 1
    assert out["candidates"][0]["run_id"] == "harness-1"
