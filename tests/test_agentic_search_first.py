# -*- coding: utf-8 -*-
"""H3a-5: search-before-ask — SEARCH_FIRST runtime gate + instruction policy.

Verifies the frozen lifecycle contract (评审缺口 3 / 原则 a):

  - ``grounding_progress_for_tool``: one *successful* tool execution →
    ``(attempted, resolved)``. attempted = kinds the query explicitly targets;
    resolved = kinds the result actually carries. A failed tool is never
    attempted.
  - ``required_searchable_kinds``: missing ∩ deployable capability.
  - AgentRuntime gate: under ``decision=search_first``, research may NOT
    RESEARCH_COMPLETE / NEED_USER before every required kind is attempted.
    Checked-but-empty (attempted, no resolved) still passes — no dead loop.
  - The two-state progress crosses the research subgraph boundary back to the
    Main Graph.
  - instructions carry the policy (research.md search-before-ask; stylist.md
    capability-index wardrobe; harness_core.md grounding priority).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from styleforge.agentic.agentic_contract import ResearchDecision
from styleforge.agentic.agents.research.graph import build_research_subgraph
from styleforge.agentic.context.grounding import (
    grounding_progress_for_tool,
    required_searchable_kinds,
)
from styleforge.agentic.environment import Environment
from styleforge.agentic.runtime.agent_runtime import AgentRuntime
from styleforge.agentic.runtime.capability_registry import CapabilityRegistry
from styleforge.agentic.tools.local_tools import register_local_tools
from styleforge.models.agentic_contract import (
    EnvironmentFacts,
    WebSearchHit,
    WebSearchResult,
)

from tests.helpers import make_item
from tests.llm.fake_llm import FakeLlm

_INSTRUCTIONS = Path(__file__).resolve().parent.parent / "apps/api/styleforge/agentic/instructions"

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


def _search_first_state(**overrides: Any) -> dict[str, Any]:
    state = {
        "request": "下周去北京看音乐剧穿什么",
        "goal": "看剧穿搭",
        "grounding_context": {
            "decision": "search_first",
            "current_date": "2026-08-19",
            "missing": ["event_location", "event_date"],
            "reason": "resolvable_missing=['event_date', 'event_location']",
        },
    }
    state.update(overrides)
    return state


class _WebEnvironment(Environment):
    """Environment whose search_web returns a fixed text (for resolved tests)."""

    def __init__(self, results_text: str) -> None:
        super().__init__(
            connection=None,
            wardrobe_items=[make_item("top-1", "top", "白衬衫", "white")],
            facts=EnvironmentFacts(),
        )
        self._text = results_text

    def search_web(self, query: str) -> WebSearchResult:
        return WebSearchResult(
            query=query,
            available=True,
            results=[WebSearchHit(title="搜索结果", url="", content=self._text)],
        )


def _runtime(llm: FakeLlm, *, env: Any = None, capabilities: frozenset[str] = frozenset()) -> AgentRuntime:
    env = env or Environment(
        connection=None,
        wardrobe_items=[make_item("top-1", "top", "白衬衫", "white")],
        facts=EnvironmentFacts(),
    )
    registry = CapabilityRegistry()
    register_local_tools(registry, env)
    return AgentRuntime(
        llm=llm,
        registry=registry,
        instructions_root=_INSTRUCTIONS,
        runtime_capabilities=capabilities,
    )


# ── grounding_progress_for_tool (pure) ──────────────────────────────────────

def test_progress_search_web_no_target_word() -> None:
    attempted, resolved = grounding_progress_for_tool(
        "search_web", {"query": "剧情介绍"}, "莫里哀音乐剧的剧情"
    )
    assert attempted == set()
    assert resolved == set()


def test_progress_search_web_city_targets_location() -> None:
    attempted, resolved = grounding_progress_for_tool(
        "search_web", {"query": "北京 演出"}, "联网搜索未可用"
    )
    assert attempted == {"event_location"}
    assert resolved == set()  # 查过但结果无城市实体


def test_progress_search_web_city_plus_date_targets_both() -> None:
    attempted, resolved = grounding_progress_for_tool(
        "search_web", {"query": "下周 场次"}, "搜索未配置"
    )
    assert attempted == {"event_location", "event_date"}


def test_progress_search_web_resolved_from_observation() -> None:
    attempted, resolved = grounding_progress_for_tool(
        "search_web", {"query": "北京 下周 场次"}, "北京保利剧院 下周 19:30 场次"
    )
    assert attempted == {"event_location", "event_date"}
    assert resolved == {"event_location", "event_date"}


def test_progress_get_weather() -> None:
    attempted, resolved = grounding_progress_for_tool(
        "get_weather", {"location": "北京"}, "天气（北京）：\n2026-08-19：24~31°C；晴"
    )
    assert attempted == {"weather"}
    assert resolved == {"weather"}


def test_progress_get_weather_empty_result_not_resolved() -> None:
    attempted, resolved = grounding_progress_for_tool(
        "get_weather", {"location": ""}, "天气查询未可用：未配置"
    )
    assert attempted == {"weather"}
    assert resolved == set()


def test_progress_other_tool_is_neutral() -> None:
    attempted, resolved = grounding_progress_for_tool(
        "modify_outfit", {"plan": {}}, "搭配已更新"
    )
    assert (attempted, resolved) == (set(), set())


# ── required_searchable_kinds ───────────────────────────────────────────────

def test_required_searchable_filters_by_capability() -> None:
    missing = ["event_location", "event_date", "weather"]
    assert required_searchable_kinds(missing, frozenset({"web_search"})) == {
        "event_location",
        "event_date",
    }
    assert required_searchable_kinds(missing, frozenset({"weather"})) == {"weather"}
    assert required_searchable_kinds(missing, frozenset()) == set()
    assert required_searchable_kinds([], frozenset({"web_search"})) == set()


# ── SEARCH_FIRST runtime gate (AgentRuntime.call) ───────────────────────────

def test_search_first_gate_rejects_research_complete_without_attempt() -> None:
    llm = FakeLlm([{"decision_summary": "没查过就收尾", "control": "RESEARCH_COMPLETE"}])
    result = _runtime(llm, capabilities=frozenset({"web_search"})).call(
        "research", _search_first_state(), decision_model=ResearchDecision
    )
    assert result.decision is not None
    assert result.protocol_error is not None
    assert "Grounding requires verification" in result.protocol_error
    assert "event_date" in result.protocol_error


def test_search_first_gate_rejects_need_user_without_attempt() -> None:
    llm = FakeLlm(
        [
            {
                "decision_summary": "缺信息直接问",
                "control": "NEED_USER",
                "clarification": {"question": "演出在哪个城市？", "reason": "需要地点"},
            }
        ]
    )
    result = _runtime(llm, capabilities=frozenset({"web_search"})).call(
        "research", _search_first_state(), decision_model=ResearchDecision
    )
    assert result.protocol_error is not None
    assert "Grounding requires verification" in result.protocol_error


def test_search_first_gate_passes_when_required_attempted() -> None:
    llm = FakeLlm([{"decision_summary": "查证完成", "control": "RESEARCH_COMPLETE"}])
    state = _search_first_state(grounding_attempted_kinds=["event_date", "event_location"])
    result = _runtime(llm, capabilities=frozenset({"web_search"})).call(
        "research", state, decision_model=ResearchDecision
    )
    assert result.protocol_error is None
    assert result.decision.control == "RESEARCH_COMPLETE"


def test_search_first_gate_reads_grounding_from_state() -> None:
    # Non-search_first decision → gate not armed (zero regression).
    llm = FakeLlm([{"decision_summary": "收尾", "control": "RESEARCH_COMPLETE"}])
    state = _search_first_state(grounding_context={"decision": "ready", "missing": []})
    result = _runtime(llm, capabilities=frozenset({"web_search"})).call(
        "research", state, decision_model=ResearchDecision
    )
    assert result.protocol_error is None


# ── SEARCH_FIRST through the research subgraph (integration) ────────────────

def test_research_subgraph_requires_every_missing_kind() -> None:
    llm = FakeLlm(
        [
            ({"decision_summary": "先查城市", "control": "CONTINUE"}, [{"name": "search_web", "arguments": {"query": "北京 演出"}}]),
            {"decision_summary": "收尾", "control": "RESEARCH_COMPLETE"},  # event_date 未 attempted → gate 打回
            ({"decision_summary": "再查日期", "control": "CONTINUE"}, [{"name": "search_web", "arguments": {"query": "下周 场次"}}]),
            {"decision_summary": "查证完成", "control": "RESEARCH_COMPLETE"},
            _EVIDENCE,
        ]
    )
    subgraph = build_research_subgraph(_runtime(llm, capabilities=frozenset({"web_search"})))
    out = subgraph.invoke(_search_first_state())

    assert out["handoff_result"].status == "COMPLETED"
    assert out["grounding_attempted_kinds"] == ["event_date", "event_location"]
    # The default test env's search_web is unconfigured → 查过但没查到 → resolved 空
    assert out["grounding_resolved_kinds"] == []
    # Gate re-entry observed (the RESEARCH_COMPLETE before both kinds were
    # attempted was rejected and fed back).
    protocol_obs = [o for o in out["tool_observations"] if o["tool"] == "__protocol__"]
    assert len(protocol_obs) == 1
    assert "Grounding requires verification" in protocol_obs[0]["observation"]
    assert out["research_evidence"].event.name == "莫里哀音乐剧"


def test_research_subgraph_checked_but_empty_passes_no_dead_loop() -> None:
    # event_date is the only required kind; a search that targets it but finds
    # nothing still counts as attempted → RESEARCH_COMPLETE passes (no loop).
    llm = FakeLlm(
        [
            ({"decision_summary": "查日期", "control": "CONTINUE"}, [{"name": "search_web", "arguments": {"query": "下周 场次"}}]),
            {"decision_summary": "查过了，没查到官方场次", "control": "RESEARCH_COMPLETE"},
            _EVIDENCE,
        ]
    )
    subgraph = build_research_subgraph(_runtime(llm, capabilities=frozenset({"web_search"})))
    state = _search_first_state(
        grounding_context={
            "decision": "search_first",
            "current_date": "2026-08-19",
            "missing": ["event_date"],
            "reason": "resolvable_missing=['event_date']",
        }
    )
    out = subgraph.invoke(state)

    assert out["handoff_result"].status == "COMPLETED"
    assert out["grounding_attempted_kinds"] == ["event_date", "event_location"]
    assert out["grounding_resolved_kinds"] == []  # 查过但没查到 → 不卡死


def test_research_subgraph_records_resolved_facts() -> None:
    llm = FakeLlm(
        [
            ({"decision_summary": "查城市日期", "control": "CONTINUE"}, [{"name": "search_web", "arguments": {"query": "北京 下周 场次"}}]),
            {"decision_summary": "查证完成", "control": "RESEARCH_COMPLETE"},
            _EVIDENCE,
        ]
    )
    env = _WebEnvironment("北京保利剧院 下周 19:30 场次")
    subgraph = build_research_subgraph(_runtime(llm, env=env, capabilities=frozenset({"web_search"})))
    out = subgraph.invoke(_search_first_state())

    assert out["handoff_result"].status == "COMPLETED"
    assert out["grounding_attempted_kinds"] == ["event_date", "event_location"]
    assert out["grounding_resolved_kinds"] == ["event_date", "event_location"]


def test_research_subgraph_failed_tool_not_attempted() -> None:
    # An unregistered tool fails execution (status=error) → not attempted →
    # the gate still refuses RESEARCH_COMPLETE; 6 consecutive protocol errors
    # hit the frozen #21 cap and the subgraph closes PROTOCOL_ERROR.
    bad = [
        ({"decision_summary": "调一个不存在的工具", "control": "CONTINUE"}, [{"name": "not_a_tool", "arguments": {}}]),
    ]
    llm = FakeLlm(bad + [{"decision_summary": "收尾", "control": "RESEARCH_COMPLETE"}] * 6)
    subgraph = build_research_subgraph(_runtime(llm, capabilities=frozenset({"web_search"})))
    out = subgraph.invoke(_search_first_state())

    assert out["handoff_result"].status == "PROTOCOL_ERROR"
    assert not out.get("grounding_attempted_kinds")  # 失败工具不计入 attempted


# ── instructions carry the policy ───────────────────────────────────────────

def test_research_instruction_carries_search_before_ask() -> None:
    text = (_INSTRUCTIONS / "research.md").read_text(encoding="utf-8")
    assert "先搜索再询问" in text
    assert "SEARCH_FIRST" in text
    assert "必须至少完成一次有效查证" in text


def test_stylist_instruction_carries_capability_index_wardrobe() -> None:
    text = (_INSTRUCTIONS / "stylist.md").read_text(encoding="utf-8")
    assert "衣橱能力索引与本轮候选" in text
    assert "本轮预取候选" in text
    assert "绝不臆造 id" in text
    assert "推荐模式第一次检索" in text
    assert "衣橱信息已就绪" not in text
    assert "全部单品 id" not in text


def test_harness_core_instruction_carries_grounding_priority() -> None:
    text = (_INSTRUCTIONS / "shared" / "harness_core.md").read_text(encoding="utf-8")
    assert "环境定位（日期 / 城市 / 活动）" in text
