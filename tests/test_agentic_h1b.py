"""H1b: Stylist subgraph + Main Graph validation chain + Clarification.

Covers the frozen contracts:
  #8   ResetCandidateDraft resets to ``base_draft`` — never to a previous
       candidate (the second candidate reflects ONLY its own run).
  #9   Subgraph-private trajectory (tool_observations / trace / step counters)
       never reaches the Main State; each candidate starts a fresh trajectory.
  #17  The Critic takes its bundle from the PromptAssembler and speaks
       ``chat_json`` (no hand-built prompt).
  #19  StageCandidate: ``status=STAGED`` + ``run_id``, never persisted here.
  #20  NEED_USER arrives at the parent as ``AgentHandoffResult{NEEDS_...}``;
       the subgraph never jumps to a parent node.
  #21  AGENT_PROTOCOL_ERROR hard cap: parse retry ≤ 1 + re-entry ≤ 1, then a
       PROTOCOL_ERROR envelope. A single recoverable error re-enters cleanly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


from styleforge.agentic.agents.stylist.graph import (
    FRESH_RESEARCH_CAP,
    MAX_REVISION_STEPS,
    MAX_STYLIST_STEPS,
    build_stylist_subgraph,
)
from styleforge.agentic.environment import Draft, Environment
from styleforge.agentic.graph.main import build_h1b_main_graph
from styleforge.agentic.runtime.agent_runtime import AgentRuntime
from styleforge.agentic.runtime.capability_registry import CapabilityRegistry
from styleforge.agentic.tools.local_tools import register_local_tools
from styleforge.models.agentic_contract import (
    CheckEnvironmentResult,
    EnvironmentFacts,
    OutfitSnapshot,
)

from tests.helpers import make_item
from tests.llm.fake_llm import FakeLlm, fake_diagnostics

_INSTRUCTIONS = Path(__file__).resolve().parent.parent / "apps/api/styleforge/agentic/instructions"

_MODIFY_TOP_1 = {
    "name": "modify_outfit",
    "arguments": {"plan": {"ops": [{"action": "add", "item_id": "top-1"}]}},
}
_MODIFY_TOP_2 = {
    "name": "modify_outfit",
    "arguments": {"plan": {"ops": [{"action": "add", "item_id": "top-2"}]}},
}
_INSPECT = {
    "name": "inspect_outfit",
    "arguments": {"outfit_id": "active"},
}
_OK = {"approved": True, "issues": [], "feedback": ""}


def _runtime(llm: FakeLlm) -> AgentRuntime:
    """An AgentRuntime over a real Environment (3 wardrobe items) + local tools.

    ``runtime_capabilities`` stays empty so no capability-gated tool leaks in;
    the Stylist only needs modify_outfit for these tests.
    """
    items = [
        make_item("top-1", "top", "白衬衫", "white"),
        make_item("top-2", "top", "黑衬衫", "black"),
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
    """A controllable stand-in for the deterministic Environment gate.

    ``fail_first`` fails the first N ``check_environment`` calls (structural
    failure, the gate only routes on ``valid``), then always passes.
    """

    def __init__(self, *, fail_first: int = 0) -> None:
        self.fail_first = fail_first
        self.failures = 0
        self.checked_drafts: list[Any] = []

    def check_environment(self, draft: Any) -> CheckEnvironmentResult:
        self.checked_drafts.append(draft)
        if self.failures < self.fail_first:
            self.failures += 1
            return CheckEnvironmentResult(valid=False, issues=["fake structure issue"])
        return CheckEnvironmentResult(valid=True, issues=[])


class _FakeEnvironmentWithFacts:
    """A gate stand-in that also carries Environment facts (frozen #15).

    The real Environment exposes ``facts``; bootstrap seeds them into the
    Execution State. This fake proves that seeding happens on the Main Graph.
    """

    def __init__(self, facts: EnvironmentFacts) -> None:
        self.facts = facts

    def check_environment(self, draft: Any) -> CheckEnvironmentResult:
        return CheckEnvironmentResult(valid=True, issues=[])


# ── Stylist subgraph ────────────────────────────────────────────────────────


def test_stylist_modify_roundtrip_returns_completed() -> None:
    llm = FakeLlm(
        [
            ({"decision_summary": "加件白衬衫", "control": "CONTINUE"}, [_MODIFY_TOP_1]),
            {"decision_summary": "这套可以了", "control": "CANDIDATE_READY"},
        ]
    )
    subgraph = build_stylist_subgraph(_runtime(llm))
    out = subgraph.invoke(
        {
            "request": "下周看剧穿什么",
            "goal": "看剧",
            "base_draft": _base_draft(),
            "working_draft": _base_draft(),
        }
    )

    assert out["handoff_result"].status == "COMPLETED"
    # The stateful modify_outfit wrote the new working draft back through the
    # shared channel (frozen #2: single state source, no instance stash).
    assert "top-1" in out["working_draft"].outfit.item_ids
    assert out["handoff_result"].trace_summary["agent"] == "stylist"
    assert out["handoff_result"].trace_summary["turns"] == 2


def test_stylist_need_user_returns_needs_clarification_envelope() -> None:
    llm = FakeLlm(
        [
            {
                "decision_summary": "不确定场地",
                "control": "NEED_USER",
                "clarification": {"question": "请问着装要求是什么？", "reason": "信息不足"},
            }
        ]
    )
    subgraph = build_stylist_subgraph(_runtime(llm))
    out = subgraph.invoke(
        {
            "request": "看剧",
            "goal": "看剧",
            "base_draft": _base_draft(),
            "working_draft": _base_draft(),
        }
    )

    # Frozen #20: the subgraph only RETURNs the envelope; it never jumps to a
    # parent ClarificationNode.
    assert out["handoff_result"].status == "NEEDS_CLARIFICATION"
    assert out["handoff_result"].clarification.question == "请问着装要求是什么？"


def test_stylist_six_consecutive_parse_failures_are_protocol_error() -> None:
    # Each AgentRuntime.call parses with one in-call retry (frozen #21: parse
    # retry ≤ 1), so two script entries are consumed per call; call #7
    # short-circuits on trajectory_protocol_errors > 5. The entries are valid
    # JSON that still fails the StylistDecision schema (missing decision_summary).
    # A valid turn in between would reset the counter — only CONSECUTIVE
    # failures accumulate to the cap (real-model prose tolerance, no loops).
    bad = [{"control": "CONTINUE"}, {"control": "CANDIDATE_READY"}]
    llm = FakeLlm(bad * 6)
    subgraph = build_stylist_subgraph(_runtime(llm))
    out = subgraph.invoke(
        {"request": "x", "goal": "g", "base_draft": _base_draft(), "working_draft": _base_draft()}
    )

    assert out["handoff_result"].status == "PROTOCOL_ERROR"
    assert out["handoff_result"].clarification is None
    # The error observation re-entered the Agent five times before the cap closed it.
    protocol_obs = [o for o in out["tool_observations"] if o["tool"] == "__protocol__"]
    assert len(protocol_obs) == 6


def test_stylist_candidate_ready_with_tool_recovers_after_reentry() -> None:
    # CANDIDATE_READY with a tool call violates the frozen tool-count contract
    # (must be 0); the runtime returns a recoverable error observation and the
    # Agent re-enters once, then finishes cleanly (frozen #21 self-healing).
    llm = FakeLlm(
        [
            ({"decision_summary": "好了", "control": "CANDIDATE_READY"}, [_MODIFY_TOP_1]),
            {"decision_summary": "好了", "control": "CANDIDATE_READY"},
        ]
    )
    subgraph = build_stylist_subgraph(_runtime(llm))
    out = subgraph.invoke(
        {"request": "x", "goal": "g", "base_draft": _base_draft(), "working_draft": _base_draft()}
    )

    assert out["handoff_result"].status == "COMPLETED"
    protocol_obs = [o for o in out["tool_observations"] if o["tool"] == "__protocol__"]
    assert len(protocol_obs) == 1
    # The tool-count mismatch re-entry message is the two-way Chinese instruction
    # (tool call OR CANDIDATE_READY), never the raw English contract error.
    assert "两个选择" in protocol_obs[0]["observation"]
    assert "0 tool call" in protocol_obs[0]["observation"]


def test_stylist_step_cap_force_submits_draft_with_items() -> None:
    # Real-model "piacon" regression: a fresh candidate that kept editing /
    # searching without submitting hits MAX_STYLIST_STEPS with a (legal) draft in
    # hand. The cap now FORCES a CANDIDATE_READY instead of discarding it as
    # PROTOCOL_ERROR — the Main Graph's gates validate whatever was built.
    llm = FakeLlm(
        [({"decision_summary": "继续", "control": "CONTINUE"}, [_MODIFY_TOP_1])] * MAX_STYLIST_STEPS
    )
    subgraph = build_stylist_subgraph(_runtime(llm))
    out = subgraph.invoke(
        {"request": "x", "goal": "g", "base_draft": _base_draft(), "working_draft": _base_draft()}
    )

    assert out["handoff_result"].status == "COMPLETED"  # forced submit, not PROTOCOL_ERROR
    assert out["trajectory_step_count"] == MAX_STYLIST_STEPS
    assert out["handoff_result"].trace_summary is not None
    assert "强制提交" in str(out["handoff_result"].trace_summary)


def test_stylist_step_cap_protocol_error_on_empty_draft() -> None:
    # The cap only PROTOCOL_ERRORs when nothing was ever built (inspect_outfit
    # never writes a draft) — a genuinely empty handed stop, never a fabricated
    # candidate. The fresh-research nudge fires once mid-run but the script keeps
    # ignoring it, so the cap closes the subgraph.
    llm = FakeLlm(
        [({"decision_summary": "查看", "control": "CONTINUE"}, [_INSPECT])] * MAX_STYLIST_STEPS
    )
    subgraph = build_stylist_subgraph(_runtime(llm))
    out = subgraph.invoke(
        {"request": "x", "goal": "g", "base_draft": _base_draft(), "working_draft": _base_draft()}
    )

    assert out["handoff_result"].status == "PROTOCOL_ERROR"
    assert out["trajectory_step_count"] == MAX_STYLIST_STEPS
    nudges = [o for o in out["tool_observations"] if o["tool"] == "__protocol__"]
    assert len(nudges) == 1  # one-shot nudge, never repeated


def test_stylist_fresh_research_nudge_forces_build() -> None:
    # Real-model "piacon" regression: a fresh candidate re-searches facts
    # forever without ever calling modify_outfit, burning the step cap. At
    # FRESH_RESEARCH_CAP tool turns a "__protocol__" nudge (no model call) is
    # injected telling it to compose a complete outfit; the script proves the
    # nudge was FREE by only supplying the pre-nudge searches + one build + one
    # submit — a model call spent on the nudge would exhaust the script.
    script: list[Any] = [
        ({"decision_summary": "查证", "control": "CONTINUE"}, [_INSPECT])
        for _ in range(FRESH_RESEARCH_CAP)
    ]
    script.append(({"decision_summary": "组合", "control": "CONTINUE"}, [_MODIFY_TOP_1]))
    script.append({"decision_summary": "完成", "control": "CANDIDATE_READY"})
    llm = FakeLlm(script)
    subgraph = build_stylist_subgraph(_runtime(llm))
    out = subgraph.invoke(
        {"request": "x", "goal": "g", "base_draft": _base_draft(), "working_draft": _base_draft()}
    )

    assert out["handoff_result"].status == "COMPLETED"
    assert out["fresh_research_nudged"] is True
    assert out["stylist_has_built"] is True
    nudges = [o for o in out["tool_observations"] if o["tool"] == "__protocol__"]
    assert len(nudges) == 1
    assert "只查证" in nudges[0]["observation"]
    assert "modify_outfit" in nudges[0]["observation"]


def test_stylist_continue_without_tool_gets_two_way_observation() -> None:
    # Real-model regression: the model describes what it's about to do in
    # decision_summary but emits NO tool call. The re-entry observation must be
    # the two-way instruction (call a tool OR CANDIDATE_READY), not the raw
    # English contract error, or the model replays the same prose forever.
    llm = FakeLlm(
        [
            ({"decision_summary": "我接下来准备换一件休闲外套…", "control": "CONTINUE"}, []),
            {"decision_summary": "好了", "control": "CANDIDATE_READY"},
        ]
    )
    subgraph = build_stylist_subgraph(_runtime(llm))
    out = subgraph.invoke(
        {"request": "x", "goal": "g", "base_draft": _base_draft(), "working_draft": _base_draft()}
    )

    assert out["handoff_result"].status == "COMPLETED"
    protocol_obs = [o for o in out["tool_observations"] if o["tool"] == "__protocol__"]
    assert len(protocol_obs) == 1
    assert "两个选择" in protocol_obs[0]["observation"]
    assert "modify_outfit" in protocol_obs[0]["observation"]
    assert "CANDIDATE_READY" in protocol_obs[0]["observation"]


def test_stylist_revision_budget_forces_resubmit() -> None:
    # Real-model deadlock regression: after a Critic rejection (gate_feedback
    # set) the model keeps editing forever because it never finds a distinct
    # enough direction. The bounded-revision budget lets it edit at most
    # MAX_REVISION_STEPS turns, then FORCES a CANDIDATE_READY re-submit — the
    # script only supplies MAX_REVISION_STEPS turns, so a 4th model call (which
    # never happens) would raise.
    script = [
        ({"decision_summary": "换外套", "control": "CONTINUE"}, [_MODIFY_TOP_1]),
        ({"decision_summary": "换鞋", "control": "CONTINUE"}, [_MODIFY_TOP_2]),
        ({"decision_summary": "再调整", "control": "CONTINUE"}, [_MODIFY_TOP_1]),
    ]
    llm = FakeLlm(script)
    subgraph = build_stylist_subgraph(_runtime(llm))
    out = subgraph.invoke(
        {
            "request": "x",
            "goal": "g",
            "base_draft": _base_draft(),
            "working_draft": _base_draft(),
            "gate_feedback": "与已存候选方向重复，请换一个方向",
        }
    )

    assert out["handoff_result"].status == "COMPLETED"  # forced re-submit, not PROTOCOL_ERROR
    assert out["revision_steps"] == MAX_REVISION_STEPS
    assert len(out["tool_observations"]) == MAX_REVISION_STEPS
    # The forced re-submit shows up in the trace so the caller can tell it from a
    # genuine CANDIDATE_READY decision.
    assert out["handoff_result"].trace_summary is not None
    assert "强制提交" in str(out["handoff_result"].trace_summary)


# ── Main Graph validation chain ─────────────────────────────────────────────


def test_main_happy_path_stages_three_candidates() -> None:
    script: list[Any] = []
    for index in range(3):
        script.append(
            ({"decision_summary": f"第{index + 1}套", "control": "CONTINUE"}, [_MODIFY_TOP_1])
        )
        script.append({"decision_summary": f"第{index + 1}套完成", "control": "CANDIDATE_READY"})
        script.append(_OK)
    llm = FakeLlm(script)
    graph = build_h1b_main_graph(_runtime(llm), environment=_FakeEnvironment(), target_candidates=3)
    out = graph.invoke(
        {
            "run_id": "run-happy",
            "request": "下周看剧穿什么",
            "goal": "看剧",
            "base_draft": _base_draft(),
        }
    )

    assert out["status"] == "done"
    assert len(out["candidates"]) == 3
    # Frozen #19: StageCandidate marks STAGED + run_id; nothing is persisted.
    assert all(candidate["status"] == "STAGED" for candidate in out["candidates"])
    assert all(candidate["run_id"] == "run-happy" for candidate in out["candidates"])


def test_main_bootstrap_seeds_wardrobe_index_into_stylist_prompt() -> None:
    # Frozen #15: BootstrapContext seeds the Environment's pre-legacy facts into
    # the Execution State. Without them the Stylist prompt carries no wardrobe
    # index and falls back to blind search_wardrobe probing — the regression
    # that burned whole real-model runs. The Stylist's FIRST model call must see
    # the wardrobe index (facts are seeded); concrete ids arrive only via
    # search_wardrobe (H3a-1: the 262 KB item dump is gone).
    facts = EnvironmentFacts(
        wardrobe_summary={
            "item_count": 2,
            "categories": {"top": 2},
            "colors": [],
            "candidate_pool": {"top": 2},
            "top": {"count": 2, "sample_colors": []},
        }
    )
    env = _FakeEnvironmentWithFacts(facts)
    llm = FakeLlm(
        [
            ({"decision_summary": "a", "control": "CONTINUE"}, [_MODIFY_TOP_1]),
            {"decision_summary": "b", "control": "CANDIDATE_READY"},
            _OK,
        ]
    )
    graph = build_h1b_main_graph(_runtime(llm), environment=env, target_candidates=1)
    out = graph.invoke({"run_id": "r", "request": "x", "goal": "g", "base_draft": _base_draft()})

    assert out["status"] == "done"
    assert len(out["candidates"]) == 1
    system = llm.calls[0]["system"]
    user = llm.calls[0]["user"]
    # The Stylist sees facts inside an untrusted runtime-data boundary, never
    # in the system role.
    assert "衣橱：" not in system
    assert "衣橱：" in user
    assert "共 2 件" in user
    assert "品类 top 2" in user
    # Concrete ids no longer ride in the prompt; search_wardrobe fetches them.
    assert "top-1" not in user
    assert "top-2" not in user


def test_main_reset_to_base_draft_not_previous_candidate() -> None:
    # The second candidate adds top-2 to an EMPTY base. If reset had failed and
    # the second modify ran on the first candidate (which already holds top-1),
    # it would hit an upper_body/base conflict and the final working draft would
    # still be {top-1}. Only a true reset to base produces {top-2}.
    llm = FakeLlm(
        [
            ({"decision_summary": "第一套", "control": "CONTINUE"}, [_MODIFY_TOP_1]),
            {"decision_summary": "第一套完成", "control": "CANDIDATE_READY"},
            _OK,
            ({"decision_summary": "第二套", "control": "CONTINUE"}, [_MODIFY_TOP_2]),
            {"decision_summary": "第二套完成", "control": "CANDIDATE_READY"},
            _OK,
        ]
    )
    graph = build_h1b_main_graph(_runtime(llm), environment=_FakeEnvironment(), target_candidates=2)
    out = graph.invoke({"run_id": "r", "request": "x", "goal": "g", "base_draft": _base_draft()})

    assert len(out["candidates"]) == 2
    assert set(out["working_draft"].outfit.item_ids) == {"top-2"}


def test_main_environment_fail_reenters_stylist_with_feedback() -> None:
    llm = FakeLlm(
        [
            ({"decision_summary": "1a", "control": "CONTINUE"}, [_MODIFY_TOP_1]),
            {"decision_summary": "1b", "control": "CANDIDATE_READY"},  # env fails here
            ({"decision_summary": "2a", "control": "CONTINUE"}, [_MODIFY_TOP_1]),
            {"decision_summary": "2b", "control": "CANDIDATE_READY"},  # env passes here
            _OK,
        ]
    )
    environment = _FakeEnvironment(fail_first=1)
    graph = build_h1b_main_graph(_runtime(llm), environment=environment, target_candidates=1)
    out = graph.invoke({"run_id": "r", "request": "x", "goal": "g", "base_draft": _base_draft()})

    assert out["status"] == "done"
    assert len(out["candidates"]) == 1
    assert len(environment.checked_drafts) == 2  # first rejected, second accepted
    # The failed gate wrote gate_feedback that the next Stylist call saw (D layer).
    assert "物理校验未通过" in llm.calls[2]["user"]


def test_main_critic_fail_feeds_back_and_reenters_stylist() -> None:
    script: list[Any] = [
        ({"decision_summary": "1a", "control": "CONTINUE"}, [_MODIFY_TOP_1]),
        {"decision_summary": "1b", "control": "CANDIDATE_READY"},
        {"approved": False, "issues": ["与已存候选方向重复"], "feedback": "换一个方向"},
    ]
    for index in range(3):
        script.append(
            ({"decision_summary": f"{index + 2}a", "control": "CONTINUE"}, [_MODIFY_TOP_1])
        )
        script.append({"decision_summary": f"{index + 2}b", "control": "CANDIDATE_READY"})
        script.append(_OK)
    llm = FakeLlm(script)
    graph = build_h1b_main_graph(_runtime(llm), environment=_FakeEnvironment(), target_candidates=3)
    out = graph.invoke({"run_id": "r", "request": "x", "goal": "g", "base_draft": _base_draft()})

    assert out["status"] == "done"
    assert len(out["candidates"]) == 3  # the failed first attempt was never staged
    # The 4th call is cycle-2's first Stylist turn; it re-assembled the feedback
    # into its prompt (frozen #15: re-assemble before every model call).
    assert "换一个方向" in llm.calls[3]["user"]


def test_main_critic_replan_budget_accepts_degraded_candidate() -> None:
    # Frozen #7 bounded diversity: after MAX_CRITIC_RETRIES consecutive
    # rejections the gate accepts the physically-valid candidate instead of
    # looping forever (a finite wardrobe cannot always produce a third distinct
    # direction). The degraded accept is flagged in gate_feedback so callers can
    # tell a genuinely-diverse set from a bounded one.
    script: list[Any] = []
    for _ in range(3):
        script.append(({"decision_summary": "a", "control": "CONTINUE"}, [_MODIFY_TOP_1]))
        script.append({"decision_summary": "b", "control": "CANDIDATE_READY"})
        script.append({"approved": False, "issues": ["与已存候选重复"], "feedback": "换个方向"})
    llm = FakeLlm(script)
    graph = build_h1b_main_graph(_runtime(llm), environment=_FakeEnvironment(), target_candidates=1)
    out = graph.invoke({"run_id": "r", "request": "x", "goal": "g", "base_draft": _base_draft()})

    assert out["status"] == "done"
    assert len(out["candidates"]) == 1  # accepted despite 3 rejections
    assert "重试上限" in out["gate_feedback"]
    # The forced accept is logged honestly — the staged entry is
    # DEGRADED_ACCEPTED, never a fabricated PASS.
    assert out["candidates"][0]["status"] == "DEGRADED_ACCEPTED"
    assert out["candidates"][0]["environment_valid"] is True
    assert out["candidates"][0]["review"]["approved"] is False


def test_main_clarification_sets_status_and_question() -> None:
    llm = FakeLlm(
        [
            {
                "decision_summary": "需要问",
                "control": "NEED_USER",
                "clarification": {"question": "对穿着要求有偏好吗？", "reason": "信息不足"},
            }
        ]
    )
    graph = build_h1b_main_graph(_runtime(llm), environment=_FakeEnvironment(), target_candidates=3)
    out = graph.invoke({"run_id": "r", "request": "x", "goal": "g", "base_draft": _base_draft()})

    # Frozen #20: the ClarificationNode is a MAIN-Graph node; the question
    # comes from AgentHandoffResult.clarification.question.
    assert out["status"] == "needs_clarification"
    assert out["clarification_question"] == "对穿着要求有偏好吗？"


def test_main_protocol_error_ends_with_agent_protocol_error() -> None:
    # Six bad calls × two in-call retries → re-entry → sixth protocol error →
    # the consecutive-failure cap (> 5) closes the subgraph and the Main Graph stops.
    llm = FakeLlm([{"control": "CONTINUE"}] * 12)
    graph = build_h1b_main_graph(_runtime(llm), environment=_FakeEnvironment(), target_candidates=3)
    out = graph.invoke({"run_id": "r", "request": "x", "goal": "g", "base_draft": _base_draft()})

    assert out["status"] == "agent_protocol_error"
    assert out["handoff_result"].status == "PROTOCOL_ERROR"


def test_main_state_has_no_private_trajectory() -> None:
    # Frozen #9: cross-agent products only — the parent never holds the
    # subgraph's tool observations / trace / step counters / pending tool.
    llm = FakeLlm(
        [
            ({"decision_summary": "a", "control": "CONTINUE"}, [_MODIFY_TOP_1]),
            {"decision_summary": "b", "control": "CANDIDATE_READY"},
            _OK,
        ]
    )
    graph = build_h1b_main_graph(_runtime(llm), environment=_FakeEnvironment(), target_candidates=1)
    out = graph.invoke({"run_id": "r", "request": "x", "goal": "g", "base_draft": _base_draft()})

    assert "tool_observations" not in out
    assert "trace" not in out
    assert "pending_tools" not in out
    assert "trajectory_step_count" not in out
    assert "trajectory_protocol_errors" not in out
    assert "handoff_result" in out  # the envelope product does cross


def test_critic_speaks_chat_json_with_assembled_bundle() -> None:
    # Frozen #17: the Critic does not hand-build a prompt — its bundle comes
    # from the PromptAssembler (same stable prefix + visibility as the other
    # agents), and it speaks chat_json with the frozen ReviewResult schema.
    llm = FakeLlm(
        [
            ({"decision_summary": "a", "control": "CONTINUE"}, [_MODIFY_TOP_1]),
            {"decision_summary": "b", "control": "CANDIDATE_READY"},
            {"approved": True, "issues": [], "feedback": ""},
        ]
    )
    graph = build_h1b_main_graph(_runtime(llm), environment=_FakeEnvironment(), target_candidates=1)
    graph.invoke(
        {
            "run_id": "r",
            "request": "下周看剧 DYNFACT_SHOW",
            "goal": "看剧",
            "base_draft": _base_draft(),
        }
    )

    json_calls = [call for call in llm.calls if "json_schema" in call]
    assert len(json_calls) == 1
    assert "DYNFACT_SHOW" in json_calls[0]["user"]  # request flows into the bundle
    assert json_calls[0]["json_schema"]["properties"]["approved"]["type"] == "boolean"
    # The Critic's stable prefix comes from the shared instructions files.
    assert "批评" in json_calls[0]["system"] or "审校" in json_calls[0]["system"]


class _ProseLlm:
    """A scripted chat_tools fake whose entries may carry raw prose + tool calls.

    FakeLlm always JSON-serialises the decision dict, so it cannot reproduce the
    real DeepSeek narration slip where the assistant emits natural language
    alongside a genuine tool call (the "pia 演唱会" regression). A tuple entry
    ``(prose, [tool_specs])`` returns the prose verbatim with the tools — the
    AgentRuntime must read the tool call as the CONTINUE signal and execute it,
    not drop the whole turn as a protocol error. A dict entry behaves like a
    clean zero-tool decision.
    """

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[str] = []

    def chat_tools(
        self,
        *,
        system: str,
        user: str,
        tools: list[Any],
        tool_choice: str = "auto",
        temperature: float = 0.2,
    ) -> tuple[str, list[Any], Any]:
        import json

        from styleforge.llm.client import ToolUseBlock

        self.calls.append(user)
        entry = self.script[0]
        self.script = self.script[1:]
        if isinstance(entry, tuple):
            prose, tool_specs = entry
            blocks = [
                ToolUseBlock(
                    name=spec["name"],
                    arguments=spec.get("arguments", {}),
                    arg_json=json.dumps(spec.get("arguments", {}), ensure_ascii=False),
                )
                for spec in tool_specs
            ]
            return prose, blocks, fake_diagnostics()
        return json.dumps(entry, ensure_ascii=False), [], fake_diagnostics()


def test_stylist_prose_with_tool_runs_the_tool_not_protocol_error() -> None:
    # Real-model "pia 演唱会" regression: the model narrated its intent in prose
    # and attached a genuine modify_outfit call. Prose-with-tool is the same
    # "keep working" signal as an empty text with a tool — the call must execute
    # (CONTINUE), not burn the whole turn as a protocol error (which never ran
    # the tool and never advanced the step budget, so the subgraph could never
    # reach its forced-submit cap).
    llm = _ProseLlm(
        [
            ("我需要结合当前衣橱再调整一下", [_MODIFY_TOP_1]),
            {"decision_summary": "完成", "control": "CANDIDATE_READY"},
        ]
    )
    subgraph = build_stylist_subgraph(_runtime(llm))
    out = subgraph.invoke(
        {"request": "x", "goal": "g", "base_draft": _base_draft(), "working_draft": _base_draft()}
    )

    assert out["handoff_result"].status == "COMPLETED"
    protocol_obs = [o for o in out["tool_observations"] if o["tool"] == "__protocol__"]
    assert len(protocol_obs) == 0  # the prose turn was NOT dropped
    assert out["trajectory_step_count"] == 1  # modify_outfit actually ran
    assert out["working_draft"].outfit.item_ids == ["top-1"]


def test_stylist_prose_without_tool_stays_protocol_error_reentry() -> None:
    # The mirror case: prose WITHOUT a tool call cannot be read as any signal —
    # a terminal decision (CANDIDATE_READY) must never be fabricated from
    # narration, so it stays a protocol-error re-entry (recoverable, one turn).
    llm = _ProseLlm(
        [
            "这套方案我觉得挺好了就定这套吧",
            {"decision_summary": "完成", "control": "CANDIDATE_READY"},
        ]
    )
    subgraph = build_stylist_subgraph(_runtime(llm))
    out = subgraph.invoke(
        {"request": "x", "goal": "g", "base_draft": _base_draft(), "working_draft": _base_draft()}
    )

    assert out["handoff_result"].status == "COMPLETED"  # self-healed on the retry
    # The prose turn was consumed by a protocol-error re-entry (script entry #1),
    # then entry #2 (CANDIDATE_READY) finished the run — both script entries are
    # gone, so the prose turn really did re-enter instead of being skipped.
    assert llm.script == []
