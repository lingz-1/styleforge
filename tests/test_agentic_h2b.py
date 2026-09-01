"""H2b: Research Subgraph + Evidence Synthesizer + the ResearchEvidence contract.

Covers the frozen contracts:
  #6   Research → Stylist is a formal ResearchEvidence with mandatory-spirited
       ``uncertainties``; the Synthesizer never invents facts.
  #18  ResearchState (raw_evidence / tool_observations / trace) is private — the
       parent receives only ResearchEvidence + the envelope.
  #22  research_synthesizer has its own prompt profile: goal + raw evidence only,
       no tools, never continues research.
  #17  The Synthesizer takes its bundle from the shared PromptAssembler (chat_json,
       like the Critic) — no hand-built prompt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from styleforge.agentic.agents.research.graph import (
    MAX_RESEARCH_STEPS,
    build_research_subgraph,
)
from styleforge.agentic.agents.research.synthesize import make_evidence_synthesizer
from styleforge.agentic.context.evidence_store import EvidenceStore
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

_SEARCH_WEB = {
    "name": "search_web",
    "arguments": {"query": "北京 莫里哀音乐剧 着装要求"},
}
_MODIFY_TOP_1 = {
    "name": "modify_outfit",
    "arguments": {"plan": {"ops": [{"action": "add", "item_id": "top-1"}]}},
}
_MODIFY_TOP_2 = {
    "name": "modify_outfit",
    "arguments": {"plan": {"ops": [{"action": "add", "item_id": "top-2"}]}},
}
_MODIFY_PANTS_1 = {
    "name": "modify_outfit",
    "arguments": {"plan": {"ops": [{"action": "add", "item_id": "pants-1"}]}},
}
_OK = {"approved": True, "issues": [], "feedback": ""}

_EVIDENCE = {
    "event": {"name": "莫里哀音乐剧", "description": "北京巡演"},
    "venue": {"name": "北京保利剧院", "location": "北京东城区", "indoor": True},
    "weather": {"location": "北京", "temperature_c": "24°C", "condition": "晴"},
    "dress_context": ["观剧礼仪，正式偏休闲"],
    "practical_requirements": ["室内观演"],
    "restrictions": [],
    "theme_elements": ["法式优雅"],
    "sources": [{"kind": "web", "title": "保利剧院官网", "url": "https://example.com/poly", "snippet": ""}],
    "uncertainties": ["未找到官方着装要求"],
}


def _runtime(llm: FakeLlm, *, capabilities: frozenset[str] = frozenset()) -> AgentRuntime:
    items = [
        make_item("top-1", "top", "白衬衫", "white"),
        make_item("top-2", "top", "藏青针织衫", "navy"),
        make_item("pants-1", "pants", "黑西裤", "black"),
    ]
    env = Environment(connection=None, wardrobe_items=items, facts=EnvironmentFacts())
    registry = CapabilityRegistry()
    register_local_tools(registry, env)
    return AgentRuntime(
        llm=llm,
        registry=registry,
        instructions_root=_INSTRUCTIONS,
        runtime_capabilities=capabilities,
    )


def _base_draft() -> Draft:
    return Draft(outfit=OutfitSnapshot(outfit_id="draft", item_ids=[], items=[]), layers={})


class _FakeEnvironment:
    def check_environment(self, draft: Any) -> Any:
        return type("R", (), {"valid": True, "issues": []})()


# ── Evidence Synthesizer (fixed node, frozen #22/#17) ───────────────────────

def test_synthesizer_uses_own_profile_and_returns_evidence() -> None:
    llm = FakeLlm([_EVIDENCE])
    node = make_evidence_synthesizer(_runtime(llm))
    out = node(
        {
            "goal": "看剧穿搭",
            "raw_evidence": [
                {"source": {"kind": "web", "title": "", "url": "", "snippet": ""}, "content": "保利剧院演出信息"}
            ],
            "tool_observations": [{"tool": "search_web", "observation": "演出信息"}],
        }
    )

    evidence = out["research_evidence"]
    assert evidence.event.name == "莫里哀音乐剧"
    assert evidence.venue.name == "北京保利剧院"
    assert evidence.uncertainties == ["未找到官方着装要求"]
    assert evidence.sources[0].kind == "web"
    # Own profile (frozen #22): stable instructions stay in system; untrusted
    # research evidence and tool observations are scoped to the user message.
    system = llm.calls[0]["system"]
    assert "【研究原始证据】" not in system
    assert "保利剧院演出信息" not in system
    user = llm.calls[0]["user"]
    assert "已确认目标：看剧穿搭" in user
    assert "【研究原始证据】" in user
    assert "保利剧院演出信息" in user
    assert "【最近工具观察】" in user
    assert "search_web：演出信息" in user
    assert "看剧穿什么" not in user


# ── Research subgraph ───────────────────────────────────────────────────────

def test_research_roundtrip_produces_evidence() -> None:
    llm = FakeLlm(
        [
            ({"decision_summary": "查一下演出信息", "control": "CONTINUE"}, [_SEARCH_WEB]),
            {"decision_summary": "信息够了", "control": "RESEARCH_COMPLETE"},
            _EVIDENCE,
        ]
    )
    subgraph = build_research_subgraph(_runtime(llm, capabilities=frozenset({"web_search"})))
    out = subgraph.invoke({"request": "下周看剧穿什么", "goal": "看剧穿搭"})

    assert out["handoff_result"].status == "COMPLETED"
    assert out["handoff_result"].trace_summary["agent"] == "research"
    assert out["research_evidence"].event.name == "莫里哀音乐剧"
    # The private buffer accumulated the web finding and never left the subgraph.
    assert len(out["raw_evidence"]) == 1
    assert out["raw_evidence"][0]["source"]["kind"] == "web"


def test_research_need_user_returns_clarification() -> None:
    llm = FakeLlm(
        [
            {
                "decision_summary": "缺关键信息",
                "control": "NEED_USER",
                "clarification": {"question": "演出具体地点是哪里？", "reason": "需要地点才能查天气"},
            }
        ]
    )
    subgraph = build_research_subgraph(_runtime(llm))
    out = subgraph.invoke({"request": "看剧穿什么", "goal": "看剧穿搭"})

    assert out["handoff_result"].status == "NEEDS_CLARIFICATION"
    assert out["handoff_result"].clarification.question == "演出具体地点是哪里？"


def test_research_protocol_error_recovers_then_completes() -> None:
    # RESEARCH_COMPLETE with a tool call violates the control/tool contract;
    # the observation is fed back and the Agent recovers on its second try.
    llm = FakeLlm(
        [
            ({"decision_summary": "错，还带了工具", "control": "RESEARCH_COMPLETE"}, [_SEARCH_WEB]),
            {"decision_summary": "这次正常", "control": "RESEARCH_COMPLETE"},
            _EVIDENCE,
        ]
    )
    subgraph = build_research_subgraph(_runtime(llm))
    out = subgraph.invoke({"request": "x", "goal": "g"})

    assert out["handoff_result"].status == "COMPLETED"
    protocol_obs = [o for o in out["tool_observations"] if o["tool"] == "__protocol__"]
    assert len(protocol_obs) == 1


def test_research_protocol_error_cap() -> None:
    bad = [{"decision_summary": "x", "control": "RESEARCH_COMPLETE", "clarification": {"question": "q"}}]
    # bad: clarification with RESEARCH_COMPLETE → contract violation. 6 protocol
    # errors (each call consumes 2 script entries: initial + in-call retry),
    # then the consecutive-failure cap (> 5) closes the subgraph.
    llm = FakeLlm(bad * 12)
    subgraph = build_research_subgraph(_runtime(llm))
    out = subgraph.invoke({"request": "x", "goal": "g"})

    assert out["handoff_result"].status == "PROTOCOL_ERROR"


def test_research_step_cap_forces_a_closing_synthesize() -> None:
    # When the Agent never emits RESEARCH_COMPLETE, the step budget is the real
    # stop: the evidence already gathered is wrapped up by the Synthesizer
    # (never a fabricated decision, never a protocol error).
    llm = FakeLlm(
        [
            ({"decision_summary": "继续查", "control": "CONTINUE"}, [_SEARCH_WEB]),
        ]
        * MAX_RESEARCH_STEPS
        + [_EVIDENCE]
    )
    subgraph = build_research_subgraph(_runtime(llm, capabilities=frozenset({"web_search"})))
    out = subgraph.invoke({"request": "x", "goal": "g"})

    assert out["handoff_result"].status == "COMPLETED"
    assert out["research_evidence"] is not None
    assert out["research_evidence"].event.name == "莫里哀音乐剧"


# ── EvidenceStore ───────────────────────────────────────────────────────────

def test_evidence_store_journal() -> None:
    store = EvidenceStore()
    assert store.count() == 0
    store.save(_EVIDENCE_MODEL(), run_id="r1")
    assert store.count() == 1
    assert store.latest().event.name == "莫里哀音乐剧"
    assert len(store.sources_for("r1")) == 1
    assert store.sources_for("other") == []


def _EVIDENCE_MODEL():
    from styleforge.agentic.agentic_contract import ResearchEvidence

    return ResearchEvidence.model_validate(_EVIDENCE)


# ── Main Graph E2E ──────────────────────────────────────────────────────────

def test_h2b_research_then_stylist_full_chain() -> None:
    llm = FakeLlm(
        [
            {"decision_summary": "需要调研", "goal": "看剧穿搭", "next_agent": "RESEARCH"},
            ({"decision_summary": "查演出", "control": "CONTINUE"}, [_SEARCH_WEB]),
            {"decision_summary": "查够了", "control": "RESEARCH_COMPLETE"},
            _EVIDENCE,
            {"decision_summary": "证据有了，交接", "goal": "看剧穿搭", "next_agent": "STYLIST"},
            ({"decision_summary": "搭配", "control": "CONTINUE"}, [_MODIFY_TOP_1]),
            {"decision_summary": "完成", "control": "CANDIDATE_READY"},
            _OK,
        ]
    )
    graph = build_h2a_main_graph(
        _runtime(llm, capabilities=frozenset({"web_search"})),
        environment=_FakeEnvironment(),
        target_candidates=1,
    )
    out = graph.invoke({"run_id": "r", "request": "下周去北京看莫里哀音乐剧穿什么", "base_draft": _base_draft()})

    assert out["status"] == "done"
    assert out["research_evidence"].event.name == "莫里哀音乐剧"
    assert len(out["candidates"]) == 1
    # Evidence crossed the subgraph boundary as untrusted runtime data, never
    # as a system instruction.
    assert "莫里哀音乐剧" not in llm.calls[5]["system"]
    assert "莫里哀音乐剧" in llm.calls[5]["user"]
    # Private research trajectory never leaked into the parent state (frozen #18).
    assert "raw_evidence" not in out
    assert "trajectory_step_count" not in out


def test_h2b_research_runs_once_shared_across_candidates() -> None:
    # Research/weather run ONCE; all three candidates share the same evidence.
    llm = FakeLlm(
        [
            {"decision_summary": "调研", "goal": "看剧", "next_agent": "RESEARCH"},
            ({"decision_summary": "查", "control": "CONTINUE"}, [_SEARCH_WEB]),
            {"decision_summary": "够", "control": "RESEARCH_COMPLETE"},
            _EVIDENCE,
            {"decision_summary": "交接", "goal": "看剧", "next_agent": "STYLIST"},
            ({"decision_summary": "c1", "control": "CONTINUE"}, [_MODIFY_TOP_1]),
            {"decision_summary": "done", "control": "CANDIDATE_READY"},
            _OK,
            ({"decision_summary": "c2", "control": "CONTINUE"}, [_MODIFY_TOP_2]),
            {"decision_summary": "done", "control": "CANDIDATE_READY"},
            _OK,
            ({"decision_summary": "c3", "control": "CONTINUE"}, [_MODIFY_PANTS_1]),
            {"decision_summary": "done", "control": "CANDIDATE_READY"},
            _OK,
        ]
    )
    graph = build_h2a_main_graph(
        _runtime(llm, capabilities=frozenset({"web_search"})),
        environment=_FakeEnvironment(),
        target_candidates=3,
    )
    out = graph.invoke({"run_id": "r", "request": "看剧穿什么", "base_draft": _base_draft()})

    assert out["status"] == "done"
    assert len(out["candidates"]) == 3
    assert [c["item_ids"] for c in out["candidates"]] == [["top-1"], ["top-2"], ["pants-1"]]
    # The evidence was synthesized exactly once (one research pass, shared);
    # the only other chat_json calls are the per-candidate Critic passes.
    synth_calls = [
        c for c in llm.calls if "dress_context" in str(c.get("json_schema") or "")
    ]
    assert len(synth_calls) == 1
    # Evidence reached every Stylist turn as scoped untrusted data.
    assert "莫里哀音乐剧" not in llm.calls[5]["system"]
    assert "莫里哀音乐剧" not in llm.calls[8]["system"]
    assert "莫里哀音乐剧" in llm.calls[5]["user"]
    assert "莫里哀音乐剧" in llm.calls[8]["user"]


def test_h2b_harness_journals_evidence() -> None:
    items = [
        make_item("top-1", "top", "白衬衫", "white"),
        make_item("pants-1", "pants", "黑西裤", "black"),
    ]
    environment = Environment(connection=None, wardrobe_items=items, facts=EnvironmentFacts())
    llm = FakeLlm(
        [
            {"decision_summary": "调研", "goal": "看剧", "next_agent": "RESEARCH"},
            ({"decision_summary": "查", "control": "CONTINUE"}, [_SEARCH_WEB]),
            {"decision_summary": "够", "control": "RESEARCH_COMPLETE"},
            _EVIDENCE,
            {"decision_summary": "交接", "goal": "看剧", "next_agent": "STYLIST"},
            ({"decision_summary": "搭配", "control": "CONTINUE"}, [_MODIFY_TOP_1]),
            {"decision_summary": "完成", "control": "CANDIDATE_READY"},
            _OK,
        ]
    )
    harness = StyleForgeHarness(
        llm=llm,
        environment=environment,
        runtime_capabilities=frozenset({"web_search"}),
        target_candidates=1,
    )
    out = harness.invoke({"run_id": "harness-r", "request": "看剧穿什么", "base_draft": _base_draft()})

    assert out["status"] == "done"
    assert harness.evidence_store.count() == 1
    assert harness.evidence_store.latest().event.name == "莫里哀音乐剧"
    assert harness.evidence_store.sources_for("harness-r")[0].kind == "web"
