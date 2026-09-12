"""H1a-5/6/7/8: visibility matrix, context assembler, prompt assembler, guard.

Covers the frozen #15 (re-assemble before every Model Call), #13/#24
(prompt_profile_key vs stable_prefix_fingerprint), #11 (B layer is a one-line
manifest, never a JSON Schema), #17 (PromptAssembler serves both protocols),
#16 (ClarificationRequest / decision state machines), #20/#21 (AgentHandoffResult
envelope) and the ContextGuard budget contract (frozen: OK / OVER_BUDGET-truncate
/ CONTEXT_LIMIT — never faked completion).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from styleforge.agentic.agentic_contract import (
    AgentHandoffResult,
    ClarificationRequest,
    CoordinatorDecision,
    ExtensionDecision,
    ResearchDecision,
    ResearchEvidence,
    StylistDecision,
    TaskState,
    check_decision_contract,
)
from styleforge.agentic.context.assembler import ContextAssembler
from styleforge.agentic.context.guard import (
    CONTEXT_LIMIT,
    CONTEXT_OK,
    CONTEXT_OVER_BUDGET,
    ContextGuard,
)
from styleforge.agentic.context.prompt_assembler import (
    ContextStats,
    PromptAssembler,
    PromptBundle,
    _format_extension_facts,
)
from styleforge.agentic.context.visibility import ContextVisibilityPolicy
from styleforge.agentic.environment import Draft
from styleforge.agentic.runtime.capability_registry import CapabilityRegistry
from styleforge.agentic.tools.local_tools import (
    AGENT_COORDINATOR,
    AGENT_CRITIC,
    AGENT_RESEARCH,
    AGENT_RESEARCH_SYNTHESIZER,
    AGENT_STYLIST,
    CAP_WEB_SEARCH,
    register_local_tools,
)
from styleforge.models.agentic_contract import (
    EnvironmentFacts,
    ItemSnapshot,
    OutfitSnapshot,
    PlanState,
)

from tests.llm.fake_llm import FakeLlm

_INSTRUCTIONS = Path(__file__).resolve().parent.parent / "apps/api/styleforge/agentic/instructions"


def _assembler(**overrides) -> PromptAssembler:
    return PromptAssembler(instructions_root=_INSTRUCTIONS, **overrides)


def _stylist_tools(with_web: bool = True) -> list:
    registry = CapabilityRegistry()
    register_local_tools(registry, object())
    caps = frozenset({CAP_WEB_SEARCH}) if with_web else frozenset()
    return registry.runtime_available(AGENT_STYLIST, caps)


def _view(agent: str):
    return ContextVisibilityPolicy().view_for(agent)


def test_extension_prompt_renders_nested_deterministic_facts() -> None:
    text = _format_extension_facts(
        {
            "task_type": "wardrobe_compatibility",
            "intent_summary": "评估真实候选鞋",
            "resolved_target": {"candidate_slot": "footwear"},
            "facts": {
                "candidate_item": {
                    "item_id": "107132140",
                    "name": "Bourne Sabrina Shoe in Grey",
                    "item_type": "shoes",
                    "color": "gray",
                },
                "candidate_slot": "footwear",
                "compatible_items_by_slot": {
                    "one_piece": [
                        {
                            "item_id": "151616863",
                            "name": "Equipment Racquel Silk Slip Dress",
                            "item_type": "dress",
                            "color": "green",
                        }
                    ]
                },
            },
        }
    )

    assert "Bourne Sabrina Shoe in Grey" in text
    assert "Equipment Racquel Silk Slip Dress" in text
    assert "footwear" in text


# ── H1a-7: PromptBundle / profile key / fingerprint ─────────────────────────


def test_profile_key_is_agent_plus_versions_not_dynamic() -> None:
    assembler = _assembler()
    ctx_a = ContextAssembler().assemble(AGENT_STYLIST, {"request": "A", "goal": "g"})
    ctx_b = ContextAssembler().assemble(AGENT_STYLIST, {"request": "完全不同", "goal": "x"})
    bundle_a = assembler.build(AGENT_STYLIST, ctx_a, _stylist_tools())
    bundle_b = assembler.build(AGENT_STYLIST, ctx_b, _stylist_tools())

    # Human-readable version key; dynamic parts (request/goal/evidence) never
    # participate (frozen #13).
    assert bundle_a.prompt_profile_key == "stylist:v1:v1:v1:v1"
    assert bundle_a.prompt_profile_key == bundle_b.prompt_profile_key


def test_profile_key_reflects_instruction_version_override() -> None:
    assembler = _assembler(agent_instruction_versions={AGENT_STYLIST: "v3"})
    bundle = assembler.build(AGENT_STYLIST, ContextAssembler().assemble(AGENT_STYLIST, {}), [])
    assert bundle.prompt_profile_key == "stylist:v1:v3:v1:v1"


def test_stable_system_is_state_independent() -> None:
    assembler = _assembler()
    # Markers are English+dashes so they cannot collide with the Chinese
    # instruction files (e.g. stylist.md mentions 莫里哀 as an example).
    state_a = {
        "request": "下周去看 DYNFACT_SHOW 穿什么",
        "goal": "看剧",
        "research_evidence": ResearchEvidence(
            theme_elements=["DRAMA_THEME_ZQX"], uncertainties=["未找到官方着装要求"]
        ),
        "recalled_memories": ["NO_SNEAKERS_ZQX"],
    }
    state_b = {"request": "通勤穿搭", "goal": "通勤"}
    ctx_a = ContextAssembler().assemble(AGENT_STYLIST, state_a)
    ctx_b = ContextAssembler().assemble(AGENT_STYLIST, state_b)

    bundle_a = assembler.build(AGENT_STYLIST, ctx_a, _stylist_tools())
    bundle_b = assembler.build(AGENT_STYLIST, ctx_b, _stylist_tools())

    assert bundle_a.stable_system == bundle_b.stable_system
    # Dynamic facts never leak into the stable prefix (frozen #5).
    for marker in ("DYNFACT_SHOW", "DRAMA_THEME_ZQX", "NO_SNEAKERS_ZQX"):
        assert marker not in bundle_a.stable_system
        assert marker not in bundle_a.capability_context


def test_stable_system_cached_per_agent_and_ships_instructions() -> None:
    assembler = _assembler()
    stylist = assembler.build(AGENT_STYLIST, ContextAssembler().assemble(AGENT_STYLIST, {}), [])
    critic = assembler.build(AGENT_CRITIC, ContextAssembler().assemble(AGENT_CRITIC, {}), [])

    assert "Harness Core" in stylist.stable_system
    assert "Tool Protocol" in stylist.stable_system
    assert "Stylist" in stylist.stable_system
    assert "Critic" in critic.stable_system
    # Same-assembler cache: identical reads return the identical bytes.
    assert assembler._stable_system(AGENT_STYLIST) == stylist.stable_system


def test_research_synthesizer_has_its_own_profile() -> None:
    assembler = _assembler()
    synth = assembler.build(
        AGENT_RESEARCH_SYNTHESIZER,
        ContextAssembler().assemble(AGENT_RESEARCH_SYNTHESIZER, {}),
        [],
    )
    research = assembler.build(AGENT_RESEARCH, ContextAssembler().assemble(AGENT_RESEARCH, {}), [])

    # Frozen #22: the synthesizer is NOT the research agent — distinct md,
    # distinct profile key.
    assert "Evidence Synthesizer" in synth.stable_system
    assert "禁止继续研究" in synth.stable_system
    assert "Research" in research.stable_system
    assert synth.prompt_profile_key.startswith("research_synthesizer:")
    assert research.prompt_profile_key.startswith("research:")


def test_capability_layer_is_manifest_not_json_schema() -> None:
    tools = _stylist_tools()
    bundle = _assembler().build(
        AGENT_STYLIST, ContextAssembler().assemble(AGENT_STYLIST, {}), tools
    )

    capability = bundle.capability_context
    assert "可用能力清单" in capability
    assert "search_wardrobe" in capability
    # Frozen #11: the prompt carries a one-line manifest only — never the
    # JSON Schema (which would drift from the tools= payload).
    assert "input_schema" not in capability
    assert '"properties"' not in capability
    # The full schema rides once, in bundle.tools → tools=.
    definition = next(t for t in bundle.tools if t.name == "search_wardrobe")
    assert definition.input_schema["type"] == "object"
    assert "query" in definition.input_schema["properties"]


def test_dynamic_facts_only_in_runtime_and_user_segments() -> None:
    state = {
        "request": "帮我搭一套看剧的衣服",
        "goal": "看剧穿搭",
        "plan": PlanState(objective="看剧", next_steps=["查天气"], missing_information=["时间"]),
        "task_state": TaskState(goal="看剧穿搭", pending=["搭一套"]),
        "environment_facts": EnvironmentFacts(
            wardrobe_summary={"top": ["白衬衫"]}, weather={"condition": "晴"}
        ),
        "research_evidence": ResearchEvidence(theme_elements=["硬汉舞剧"]),
        "candidates": [{"item_ids": ["top-1"]}],
        "working_draft": Draft(
            outfit=OutfitSnapshot(
                outfit_id="d",
                item_ids=["top-1"],
                items=[
                    ItemSnapshot(item_id="top-1", name="白衬衫", item_type="top", color="white")
                ],
            ),
            layers={},
        ),
        "recalled_memories": ["不喜欢运动鞋"],
        "thread_context": {"last_topic": "音乐剧"},
        "loaded_skills": ["event_outfit_planning"],
    }
    ctx = ContextAssembler().assemble(AGENT_STYLIST, state)
    bundle = _assembler().build(AGENT_STYLIST, ctx, _stylist_tools())

    assert "帮我搭一套看剧的衣服" in bundle.user_message  # layer D
    assert "硬汉舞剧" in bundle.runtime_context  # layer C
    assert "任务计划" in bundle.runtime_context
    assert "top-1" in bundle.runtime_context
    assert "event_outfit_planning" in bundle.capability_context
    for marker in ("帮我搭一套看剧的衣服", "硬汉舞剧", "任务计划", "top-1", "不喜欢运动鞋"):
        assert marker not in bundle.stable_system
        assert marker not in bundle.capability_context


def test_system_text_excludes_dynamic_runtime_layer() -> None:
    # Give the state a plan so the C layer is non-empty — all three segments
    # must render, joined by "\n\n" and nothing trailing.
    state = {"request": "x", "plan": PlanState(objective="看剧")}
    bundle = _assembler().build(
        AGENT_STYLIST, ContextAssembler().assemble(AGENT_STYLIST, state), _stylist_tools()
    )
    assert bundle.runtime_context != ""
    assert bundle.system_text == bundle.stable_system + "\n\n" + bundle.capability_context
    assert bundle.runtime_context not in bundle.system_text
    assert bundle.runtime_context in bundle.model_user_message


def test_fingerprint_deterministic_and_ignores_dynamic_parts() -> None:
    assembler = _assembler()
    ctx_a = ContextAssembler().assemble(AGENT_STYLIST, {"request": "A"})
    ctx_b = ContextAssembler().assemble(
        AGENT_STYLIST,
        {"request": "B", "research_evidence": ResearchEvidence(theme_elements=["x"])},
    )
    fp_a = assembler.build(AGENT_STYLIST, ctx_a, _stylist_tools()).stable_prefix_fingerprint
    fp_b = assembler.build(AGENT_STYLIST, ctx_b, _stylist_tools()).stable_prefix_fingerprint

    # Machine fingerprint covers only the stable prefix (A+B + tools + skills);
    # request/evidence live in C/D and never participate (frozen #24).
    assert fp_a == fp_b
    assert len(fp_a) == 64  # sha256 hex


def test_fingerprint_captures_tool_catalog_differences() -> None:
    assembler = _assembler()
    with_web = assembler.build(
        AGENT_STYLIST, ContextAssembler().assemble(AGENT_STYLIST, {}), _stylist_tools(True)
    )
    without_web = assembler.build(
        AGENT_STYLIST, ContextAssembler().assemble(AGENT_STYLIST, {}), _stylist_tools(False)
    )

    # Same human version number, but Deployment A has search_web and B does
    # not — the machine fingerprint must tell the difference (frozen #24).
    assert with_web.prompt_profile_key == without_web.prompt_profile_key
    assert with_web.stable_prefix_fingerprint != without_web.stable_prefix_fingerprint
    assert "search_web" in with_web.capability_context
    assert "search_web" not in without_web.capability_context


def test_canonical_tools_is_order_independent() -> None:
    from styleforge.agentic.context.prompt_assembler import _canonical_tools

    tools = _stylist_tools()
    # Frozen #24 note: the *canonical tool serialization* inside the fingerprint
    # is sorted by name, so it does not depend on registry iteration order.
    assert _canonical_tools(tools) == _canonical_tools(list(reversed(tools)))


def test_deterministic_tool_ordering_across_calls() -> None:
    tools = _stylist_tools()
    names_first = [t.name for t in tools]
    names_second = [t.name for t in _stylist_tools()]
    assert names_first == names_second


# ── H1a-5/6: visibility + re-assembly before every model call ───────────────


def test_visibility_gates_sources_per_agent() -> None:
    state = {
        "request": "下周去北京看莫里哀音乐剧穿什么",
        "goal": "看剧",
        "plan": PlanState(objective="看剧"),
        "task_state": TaskState(goal="看剧穿搭"),
        "environment_facts": EnvironmentFacts(wardrobe_summary={"top": ["白衬衫"]}),
        "research_evidence": ResearchEvidence(theme_elements=["硬汉舞剧"]),
        "candidates": [{"item_ids": ["top-1"]}],
        "working_draft": object(),
        "recalled_memories": ["不喜欢运动鞋"],
        "thread_context": {"last_topic": "音乐剧"},
    }
    assembler = ContextAssembler()

    coordinator = assembler.assemble(AGENT_COORDINATOR, state)
    assert coordinator.user_request != ""
    assert coordinator.research_evidence is not None
    assert coordinator.candidates != []
    # Coordinator never sees the Stylist's working trajectory (frozen #9/#15).
    assert coordinator.working_draft is None
    assert coordinator.environment_facts is None
    assert coordinator.memories == []
    assert coordinator.thread_context == {"last_topic": "音乐剧"}

    research = assembler.assemble(AGENT_RESEARCH, state)
    assert research.plan is not None
    assert research.thread_context is not None
    # Research never sees the Stylist trajectory.
    assert research.working_draft is None
    assert research.candidates == []
    assert research.environment_facts is None
    assert research.memories == ["不喜欢运动鞋"]

    stylist = assembler.assemble(AGENT_STYLIST, state)
    assert stylist.working_draft is not None
    assert stylist.environment_facts is not None
    assert stylist.candidates != []
    assert stylist.memories == ["不喜欢运动鞋"]

    critic = assembler.assemble(AGENT_CRITIC, state)
    assert critic.user_request != ""
    assert critic.research_evidence is not None
    assert critic.candidates != []
    assert critic.working_draft is not None
    # Critic sees only the artifacts under review, never task bookkeeping.
    assert critic.task_state is None
    assert critic.plan is None
    assert critic.memories == []
    assert critic.thread_context is None


def test_visibility_unknown_agent_raises() -> None:
    with pytest.raises(ValueError):
        ContextVisibilityPolicy().view_for("nobody")


def test_assembler_reassembles_fresh_state_before_each_call() -> None:
    """Frozen #15: BootstrapContext seeds base facts only; every model call
    re-assembles freshly-produced evidence / candidates / feedback."""
    assembler = ContextAssembler()
    state = {"request": "看剧", "goal": "穿搭"}

    first = assembler.assemble(AGENT_STYLIST, state)
    assert first.research_evidence is None
    assert first.candidates == []

    # Research completes between the two calls → next call must see it.
    state["research_evidence"] = ResearchEvidence(
        theme_elements=["硬汉舞剧"], uncertainties=["未找到官方着装要求"]
    )
    state["candidates"] = [{"item_ids": ["top-1"]}]
    second = assembler.assemble(AGENT_STYLIST, state)

    assert second.research_evidence is not None
    assert second.research_evidence.theme_elements == ["硬汉舞剧"]
    assert second.candidates[0]["item_ids"] == ["top-1"]

    # And the rendered prompt reflects it, while the stable fingerprint does
    # not change (evidence/candidates are C-layer, never stable prefix).
    prompt_assembler = _assembler()
    bundle_first = prompt_assembler.build(AGENT_STYLIST, first, _stylist_tools())
    bundle_second = prompt_assembler.build(AGENT_STYLIST, second, _stylist_tools())
    assert "硬汉舞剧" in bundle_second.runtime_context
    assert "硬汉舞剧" not in bundle_first.runtime_context
    assert bundle_first.stable_prefix_fingerprint == bundle_second.stable_prefix_fingerprint


# ── H1a-7: dual protocol (one bundle feeds chat_tools AND chat_json) ────────


def test_same_bundle_feeds_chat_tools_and_chat_json() -> None:
    prompt = _assembler()
    stylist_bundle = prompt.build(
        AGENT_STYLIST,
        ContextAssembler().assemble(AGENT_STYLIST, {"request": "看剧"}),
        _stylist_tools(),
    )
    critic_bundle = prompt.build(
        AGENT_CRITIC, ContextAssembler().assemble(AGENT_CRITIC, {"request": "看剧"}), []
    )

    llm = FakeLlm([{"decision_summary": "ok", "control": "CANDIDATE_READY"}])
    decision_text, blocks, _ = llm.chat_tools(
        system=stylist_bundle.system_text,
        user=stylist_bundle.model_user_message,
        tools=stylist_bundle.tools,
    )
    assert llm.calls[0]["system"] == stylist_bundle.system_text
    assert llm.calls[0]["user"] == stylist_bundle.model_user_message
    assert llm.calls[0]["tools"] == stylist_bundle.tools
    assert "CANDIDATE_READY" in decision_text
    assert blocks == []

    json_llm = FakeLlm([{"approved": True, "issues": [], "feedback": ""}])
    payload, _ = json_llm.chat_json(
        system=critic_bundle.system_text,
        user=critic_bundle.model_user_message,
        json_schema={"type": "object", "properties": {"approved": {"type": "boolean"}}},
    )
    assert json_llm.calls[0]["system"] == critic_bundle.system_text
    assert payload["approved"] is True


def test_all_agent_decision_schemas_require_unified_intent() -> None:
    for model in (
        CoordinatorDecision,
        ResearchDecision,
        StylistDecision,
        ExtensionDecision,
    ):
        schema = model.model_json_schema()
        assert "intent" in schema["properties"]
        assert "intent" in schema["required"]


# ── H1a-16: decision state machines + handoff envelope ──────────────────────


def test_clarification_request_requires_question() -> None:
    with pytest.raises(ValidationError):
        ClarificationRequest()  # question is required
    assert ClarificationRequest(question="具体哪里不满意？").question


def test_coordinator_need_user_requires_clarification() -> None:
    assert (
        check_decision_contract(CoordinatorDecision(decision_summary="s", goal="g", need_user=True))
        != []
    )
    assert (
        check_decision_contract(
            CoordinatorDecision(
                decision_summary="s",
                goal="g",
                need_user=True,
                clarification=ClarificationRequest(question="哪里不满意？"),
            )
        )
        == []
    )


def test_coordinator_three_state_mutual_exclusion() -> None:
    # need_user + need_plan_update are mutually exclusive.
    assert (
        check_decision_contract(
            CoordinatorDecision(
                decision_summary="s",
                goal="g",
                need_user=True,
                need_plan_update=True,
                clarification=ClarificationRequest(question="?"),
            )
        )
        != []
    )
    # need_user with next_agent set is a violation.
    assert (
        check_decision_contract(
            CoordinatorDecision(
                decision_summary="s",
                goal="g",
                need_user=True,
                clarification=ClarificationRequest(question="?"),
                next_agent="STYLIST",
            )
        )
        != []
    )
    # handoff mode requires next_agent.
    assert check_decision_contract(CoordinatorDecision(decision_summary="s", goal="g")) != []
    assert (
        check_decision_contract(
            CoordinatorDecision(decision_summary="s", goal="g", next_agent="STYLIST")
        )
        == []
    )


def test_research_and_stylist_need_user_contract() -> None:
    assert (
        check_decision_contract(ResearchDecision(decision_summary="s", control="NEED_USER")) != []
    )
    assert (
        check_decision_contract(
            ResearchDecision(
                decision_summary="s",
                control="NEED_USER",
                clarification=ClarificationRequest(question="需要哪一天？"),
            )
        )
        == []
    )
    assert (
        check_decision_contract(ResearchDecision(decision_summary="s", control="RESEARCH_COMPLETE"))
        == []
    )
    # clarification without NEED_USER is a protocol mismatch.
    assert (
        check_decision_contract(
            ResearchDecision(
                decision_summary="s",
                control="RESEARCH_COMPLETE",
                clarification=ClarificationRequest(question="?"),
            )
        )
        != []
    )
    assert (
        check_decision_contract(StylistDecision(decision_summary="s", control="CANDIDATE_READY"))
        == []
    )
    assert check_decision_contract(StylistDecision(decision_summary="s", control="NEED_USER")) != []


def test_handoff_result_envelope_and_closed_statuses() -> None:
    completed = AgentHandoffResult(status="COMPLETED", trace_summary={"agent": "stylist"})
    assert completed.status == "COMPLETED"
    assert completed.trace_summary == {"agent": "stylist"}

    needs = AgentHandoffResult(
        status="NEEDS_CLARIFICATION", clarification=ClarificationRequest(question="哪里不满意？")
    )
    assert needs.clarification.question == "哪里不满意？"

    protocol_error = AgentHandoffResult(status="PROTOCOL_ERROR")
    assert protocol_error.clarification is None

    with pytest.raises(ValidationError):
        AgentHandoffResult(status="MAYBE")


# ── H1a-8: ContextGuard ─────────────────────────────────────────────────────


def _small_bundle() -> PromptBundle:
    stable = "S" * 50
    runtime = "【任务计划】\n目标：看剧\n\n【研究证据】" + "长证据内容" * 800
    return PromptBundle(
        stable_system=stable,
        capability_context="",
        runtime_context=runtime,
        user_message="U" * 20,
        tools=[],
        prompt_profile_key="test:v1",
        stable_prefix_fingerprint="fp",
        context_stats=ContextStats(
            total_chars=50 + len(runtime) + 20,
            stable_chars=50,
            dynamic_chars=len(runtime) + 20,
            tools=0,
        ),
    )


def test_guard_ok_under_budget() -> None:
    bundle = _small_bundle()
    result = ContextGuard(char_budget=1_000_000).check(bundle)
    assert result.status == CONTEXT_OK
    assert result.passes
    assert result.bundle is bundle
    assert result.warnings == []


def test_guard_over_budget_truncates_runtime_keeps_stable_and_user() -> None:
    bundle = _small_bundle()
    # Budget that forces the dynamic section to shrink but keeps A+D intact.
    budget = len(bundle.stable_system) + len(bundle.user_message) + 200
    result = ContextGuard(char_budget=budget).check(bundle)

    assert result.status == CONTEXT_OVER_BUDGET
    assert result.passes
    assert result.warnings
    assert result.reclaimed_chars > 0
    truncated = result.bundle
    assert truncated.stable_system == bundle.stable_system  # cache prefix intact
    assert truncated.user_message == bundle.user_message  # current turn intact
    assert len(truncated.runtime_context) < len(bundle.runtime_context)
    # Head of the C-layer is preserved — plan / latest task survive truncation
    # (frozen: keep latest task/plan/draft).
    assert "目标：看剧" in truncated.runtime_context
    # Stats were recomputed to match the truncated bundle.
    assert truncated.context_stats.total_chars == (
        len(truncated.system_text) + len(truncated.model_user_message)
    )
    assert truncated.context_stats.dynamic_chars == len(truncated.model_user_message)


def test_guard_hard_limit_is_explicit_failure_not_faked_completion() -> None:
    bundle = PromptBundle(
        stable_system="s" * 5000,
        capability_context="",
        runtime_context="",
        user_message="u" * 200,
        tools=[],
    )
    result = ContextGuard(char_budget=100, hard_limit=1000).check(bundle)

    assert result.status == CONTEXT_LIMIT
    assert not result.passes
    assert result.bundle is None  # the model call is skipped — never fake done


def test_guard_passes_through_under_hard_limit_when_unable_to_reclaim() -> None:
    bundle = PromptBundle(
        stable_system="s" * 5000,
        capability_context="",
        runtime_context="",
        user_message="u" * 200,
        tools=[],
    )
    result = ContextGuard(char_budget=100, hard_limit=1_000_000).check(bundle)

    # Over soft budget, unable to reclaim (stable+user alone overflow), but
    # under the hard limit → still run the Agent, with a warning.
    assert result.status == CONTEXT_OVER_BUDGET
    assert result.passes
    assert result.bundle is bundle
