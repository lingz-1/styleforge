"""Extension subgraph: analyze an extension task, then synthesize (frozen).

Owns the Extension working trajectory privately. It loops ``extension_agent ↔
tool_step`` until the Agent signals READY (or NEED_USER); a closing node then
turns the deterministic ``extension_facts`` (pre-computed by the execute side,
never a tool — the Runtime deps live in task_workflow) plus the Agent's tool
observations into one task result, validates it against the hard contracts
(``validate_task_result`` + ``validate_extension_draft``) with bounded repair,
and ships it to the parent via ``extension_result``.

No outfit chain: Extension never produces candidates, never touches the
Environment Gate / Critic / StageCandidate / Goal Gate (the Main Graph routes
its COMPLETED envelope straight to end_node). The closing node is a *fixed node
inside this subgraph* — no tools, no more analysis, same discipline as the
Evidence Synthesizer (frozen #22).
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from styleforge.agentic.agentic_contract import (
    AgentHandoffResult,
    ExtensionDecision,
)
from styleforge.agentic.runtime.agent_runtime import AgentRuntime, ContextLimitError
from styleforge.models.agent_tasks import Agent1TaskOutput, Agent2TaskOutput
from styleforge.models.task_results import RESULT_MODELS, validate_task_result
from styleforge.orchestration.task_router import TaskType
from styleforge.tools.extension_validation import (
    sanitize_extension_references,
    validate_extension_draft,
)

# Legacy three-agent chain capped each extension task at ~3 LLM calls; 8 steps
# leaves headroom for the coordinator turn plus tool grounding.
MAX_EXTENSION_STEPS = 8

# Bounded repair budget for the closing node (mirrors the legacy
# ``_critic_with_repair`` semantics): a draft that fails the hard validators is
# fed back to the model at most this many times before a deterministic
# grounded fallback or clarification ends the run without fabricated IDs.
MAX_CLOSING_RETRIES = 2

_CLOSING_MODE_INSTRUCTION = """【结果综合模式】
你现在是扩展任务的最终结果综合节点，不再执行 Extension 决策循环。
禁止输出 control、READY、NEED_USER 或工具调用。必须严格返回本次提供的 JSON Schema：
顶层包含 status、summary、result，result 必须填写对应任务合同的全部必填字段。
所有衣橱单品、候选新品、槽位统计和缺口只能来自【确定性分析事实】；不得编造 ID。"""


class ExtensionState(TypedDict, total=False):
    """Subgraph state. Shared channels flow to/from the Main Graph; the private
    trajectory (tool_observations / trace / counters / flags) stays inside and
    is dropped on RETURN."""

    # context sources (input from the Main Graph, read-only inside the subgraph)
    request: str
    goal: str
    task_state: Any
    plan: Any
    thread_context: dict | None
    recalled_memories: list[Any]
    loaded_skills: list[str]
    interaction: Any
    environment_facts: Any
    grounding_context: dict | None
    extension_facts: Any  # Agent1TaskOutput.model_dump(mode="json") — execute-side
    # private trajectory — never returned to the parent (frozen #9)
    tool_observations: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    trajectory_step_count: int
    trajectory_protocol_errors: int
    pending_tools: list[dict[str, Any]]
    trajectory_done: bool  # finished this run — private, never the shared envelope
    trajectory_closing: bool  # READY → run the closing node
    # products shared with the parent (frozen #20)
    handoff_result: AgentHandoffResult | None
    extension_result: Any  # {task_type, status, summary, result} — task contract
    extension_validation_failures: list[str]


def can_direct_close_extension(state: dict[str, Any]) -> bool:
    """Return whether verified extension facts can bypass redundant planning."""
    facts = state.get("extension_facts") or {}
    task_type = facts.get("task_type")
    if facts.get("needs_clarification"):
        return False
    fact_body = facts.get("facts") or {}
    resolved = facts.get("resolved_target") or {}
    if task_type in (TaskType.STYLE_ADVICE, TaskType.STYLE_ADVICE.value):
        return bool(
            resolved.get("style")
            and "knowledge_entries" in fact_body
            and "wardrobe_matches" in fact_body
        )
    if task_type in (
        TaskType.WARDROBE_COMPATIBILITY,
        TaskType.WARDROBE_COMPATIBILITY.value,
    ):
        compatible = fact_body.get("compatible_items_by_slot") or {}
        return bool(
            fact_body.get("candidate_item")
            and fact_body.get("candidate_slot")
            and any(compatible.values())
        )
    if task_type in (TaskType.WARDROBE_GAP, TaskType.WARDROBE_GAP.value):
        return "wardrobe_item_count" in fact_body and "slot_counts" in fact_body
    if task_type not in (TaskType.ITEM_ADVICE, TaskType.ITEM_ADVICE.value):
        return False
    anchor = fact_body.get("anchor_item") or {}
    compatible = fact_body.get("compatible_items_by_slot") or {}
    requested_slots = fact_body.get("requested_support_slots") or []
    return bool(
        anchor
        and any(compatible.values())
        and all(compatible.get(slot) for slot in requested_slots)
    )


def build_extension_subgraph(runtime: AgentRuntime):
    """Compile the Extension subgraph over one AgentRuntime."""

    closing_node = make_extension_closing(runtime)

    def _envelope(status: str, trace: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        return {
            "handoff_result": AgentHandoffResult(
                status=status,
                trace_summary=_trace_summary(trace),
                **extra,
            ),
            "trajectory_done": True,
        }

    def extension_agent(state: ExtensionState) -> dict[str, Any]:
        if state.get("trajectory_step_count", 0) >= MAX_EXTENSION_STEPS:
            # Step budget exhausted without a READY signal. Extension's product
            # is the deterministic facts + gathered observations; a forced
            # closing pass is a legitimate wrap-up (the closing node falls back
            # to a deterministic clarification draft rather than inventing a
            # completed result).
            return {"trajectory_closing": True, "trace": state.get("trace", [])}
        # Frozen #21 hard cap: re-entry after a protocol error is bounded.
        if state.get("trajectory_protocol_errors", 0) > 5:
            return _envelope("PROTOCOL_ERROR", state.get("trace", []))
        try:
            result = runtime.call(
                "extension",
                dict(state),
                decision_model=ExtensionDecision,
            )
        except ContextLimitError:
            return _envelope("PROTOCOL_ERROR", state.get("trace", []))

        if result.protocol_error is not None:
            return {
                "trajectory_protocol_errors": state.get("trajectory_protocol_errors", 0) + 1,
                "tool_observations": state.get("tool_observations", [])
                + [{
                    "tool": "__protocol__",
                    "observation": result.protocol_error,
                    "error_code": result.error_code,
                    "retryable": result.retryable,
                }],
            }

        trace = state.get("trace", []) + [result.trace]
        # A valid turn resets the protocol-error budget (consecutive-only bound).
        if result.tool_uses:
            # CONTINUE with one or more tool calls — all executed in order,
            # every observation kept for the closing node.
            return {
                "pending_tools": [
                    {"name": item.name, "arguments": item.arguments}
                    for item in result.tool_uses
                ],
                "trace": trace,
                "trajectory_protocol_errors": 0,
            }

        decision: ExtensionDecision = result.decision
        if decision.control == "NEED_USER":
            return {
                "handoff_result": AgentHandoffResult(
                    status="NEEDS_CLARIFICATION",
                    clarification=decision.clarification,
                    trace_summary=_trace_summary(trace),
                ),
                "trace": trace,
                "trajectory_done": True,
                "trajectory_protocol_errors": 0,
            }
        # READY — stop analyzing; the closing node synthesizes the result.
        return {
            "trace": trace,
            "trajectory_closing": True,
            "trajectory_protocol_errors": 0,
        }

    def tool_step(state: ExtensionState) -> dict[str, Any]:
        remaining = list(state.get("pending_tools") or [])
        pending = remaining.pop(0)
        tool_result = runtime.execute_tool(
            pending["name"],
            pending["arguments"],
            state=dict(state),
        )
        updates: dict[str, Any] = {
            "tool_observations": state.get("tool_observations", [])
            + [{
                "tool": pending["name"],
                "observation": tool_result.observation,
                "error_code": tool_result.error_code,
                "retryable": tool_result.retryable,
            }],
            "trajectory_step_count": state.get("trajectory_step_count", 0) + 1,
            "pending_tools": remaining,
        }
        updates.update(tool_result.state_updates or {})
        return updates

    def _route(state: ExtensionState) -> str:
        if state.get("trajectory_done"):
            return END
        if state.get("trajectory_closing"):
            return "closing"
        if state.get("pending_tools"):
            return "tool_step"
        return "extension_agent"  # protocol-error re-entry with an appended observation

    def _start_route(state: ExtensionState) -> str:
        """Skip redundant analysis when deterministic item facts are complete."""
        return (
            "closing"
            if can_direct_close_extension(dict(state))
            else "extension_agent"
        )

    builder = StateGraph(ExtensionState)
    builder.add_node("extension_agent", extension_agent)
    builder.add_node("tool_step", tool_step)
    builder.add_node("closing", closing_node)
    builder.add_conditional_edges(
        START,
        _start_route,
        {"extension_agent": "extension_agent", "closing": "closing"},
    )
    builder.add_conditional_edges(
        "extension_agent",
        _route,
        {
            "tool_step": "tool_step",
            "extension_agent": "extension_agent",
            "closing": "closing",
            END: END,
        },
    )
    builder.add_edge("tool_step", "extension_agent")
    builder.add_edge("closing", END)
    return builder.compile()


def make_extension_closing(runtime: AgentRuntime):
    """The Extension subgraph's closing node over one AgentRuntime.

    A fixed ``chat_json`` node (no tools). It rebuilds the Agent1 facts from
    ``extension_facts``, asks the model for the task result aligned with the
    RESULT_MODELS JSON Schema, then hard-validates (contract + hard boundary)
    with bounded repair. ``needs_clarification`` deterministically wins when the
    deterministic facts already flagged it; the bounded-repair fallback is a
    deterministic clarification draft — never a fabricated completion.
    """

    def closing_node(state: dict[str, Any]) -> dict[str, Any]:
        facts = state.get("extension_facts") or {}
        try:
            agent1 = Agent1TaskOutput(**facts)
        except ValidationError as error:
            # Malformed deterministic facts cannot produce any result — the
            # boundary was broken by the caller, not the model.
            failed = _protocol_envelope(state)
            failed["extension_validation_failures"] = [
                f"agent1_contract: {str(error)[:500]}"
            ]
            return failed
        trace = state.get("trace", [])

        if facts.get("needs_clarification"):
            shipped = _try_finalize(agent1, trace, _clarification_draft(agent1, facts))
            return shipped if not isinstance(shipped, str) else _protocol_envelope(state)

        task_type = agent1.task_type
        schema = _closing_schema(task_type)
        bundle = runtime.assemble_bundle("extension", dict(state), tools=[])
        guard = runtime.guard.check(bundle)
        if guard.bundle is None:
            raise ContextLimitError(
                f"context guard: {guard.status}: {'；'.join(guard.warnings)}"
            )
        system = guard.bundle.system_text + "\n\n" + _CLOSING_MODE_INSTRUCTION
        base_user = (
            guard.bundle.model_user_message
            + "\n\n【本轮目标 JSON Schema】\n"
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        )
        user = base_user
        validation_failures: list[str] = []
        attempt_count = (
            1
            if task_type is TaskType.ITEM_ADVICE
            else MAX_CLOSING_RETRIES + 1
        )
        for attempt in range(attempt_count):
            payload, _ = runtime.llm.chat_json(
                system=system,
                user=user,
                json_schema=schema,
            )
            status = payload.get("status") or "needs_clarification"
            draft = {
                "status": status,
                "summary": payload.get("summary") or "",
                "result": payload.get("result") or {},
            }
            shipped = _try_finalize(agent1, trace, draft)
            if not isinstance(shipped, str):
                if validation_failures:
                    shipped["extension_validation_failures"] = validation_failures
                return shipped
            validation_failures.append(shipped)
            user = base_user + "\n\n上一轮产出未通过硬校验，请修正：\n" + shipped
        # The facts already contain verified wardrobe candidates. For feasible
        # item advice, compose a grounded result from those facts instead of
        # asking the user to clarify information the system already resolved.
        if task_type is TaskType.ITEM_ADVICE:
            fallback = _grounded_item_advice_fallback(agent1)
            if fallback is not None:
                shipped = _try_finalize(agent1, trace, fallback)
                if not isinstance(shipped, str):
                    shipped["extension_validation_failures"] = validation_failures
                    return shipped
        # Other tasks, or an item-advice fact set without enough candidates,
        # retain the honest clarification fallback after bounded repair.
        shipped = _try_finalize(agent1, trace, _clarification_draft(agent1, facts))
        if isinstance(shipped, str):
            failed = _protocol_envelope(state)
            failed["extension_validation_failures"] = validation_failures + [shipped]
            return failed
        shipped["extension_validation_failures"] = validation_failures
        return shipped

    return closing_node


def _grounded_item_advice_fallback(
    agent1: Agent1TaskOutput,
) -> dict[str, Any] | None:
    """Build item advice only from deterministic Agent 1 wardrobe facts."""
    facts = agent1.facts
    anchor = dict(facts.get("anchor_item") or {})
    anchor_id = str(anchor.get("item_id") or "")
    compatible = {
        str(slot): list(items)
        for slot, items in (facts.get("compatible_items_by_slot") or {}).items()
        if items
    }
    requested_slots = [
        str(slot) for slot in (facts.get("requested_support_slots") or [])
    ]
    selected_slots = requested_slots or sorted(compatible)
    if (
        not anchor_id
        or not selected_slots
        or any(not compatible.get(slot) for slot in selected_slots)
    ):
        return None

    outfit_count = min(3, max(len(compatible[slot]) for slot in selected_slots))
    outfits: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for index in range(outfit_count):
        item_ids = [anchor_id]
        for slot in selected_slots:
            candidates = compatible[slot]
            candidate = candidates[index % len(candidates)]
            item_ids.append(str(candidate["item_id"]))
        item_ids = list(dict.fromkeys(item_ids))
        signature = tuple(item_ids)
        if len(item_ids) < 2 or signature in seen:
            continue
        seen.add(signature)
        outfits.append(
            {
                "outfit_id": f"grounded-{index + 1}",
                "item_ids": item_ids,
                "reasoning": "保留指定单品，并按用户点名槽位选用衣橱内已验证的高匹配单品。",
            }
        )
    if not outfits:
        return None

    result = {
        "status": "completed",
        "title": str(anchor.get("name") or "单品搭配建议"),
        "summary": "已基于当前衣橱中通过匹配与边界校验的真实单品生成搭配。",
        "anchor_item": anchor,
        "anchor_source": str(facts.get("anchor_source") or "wardrobe"),
        "compatible_items_by_slot": {
            slot: compatible[slot] for slot in selected_slots
        },
        "wardrobe_matches": list(facts.get("wardrobe_matches") or []),
        "wardrobe_matches_by_slot": {},
        "sample_outfits": outfits,
        "evidence": [],
        "clarification_question": "",
        "limitations": ["模型结构化输出未通过硬校验，已使用确定性候选事实降级生成。"],
        "generation_mode": "deterministic_grounded_fallback",
    }
    return {"status": "completed", "summary": result["summary"], "result": result}


def _try_finalize(
    agent1: Agent1TaskOutput,
    trace: list[dict[str, Any]],
    draft: dict[str, Any],
) -> dict[str, Any] | str:
    """Ship a validated ``extension_result``, or return the failure feedback.

    Pipeline: task contract validation → rebuild Agent2 → scope-sanitize the
    references → re-validate → hard-boundary checks. Any failure returns a
    Chinese feedback string the closing loop feeds back to the model.
    """
    task_type = agent1.task_type
    try:
        result = validate_task_result(task_type, draft["result"])
        agent2 = Agent2TaskOutput(
            task_type=task_type,
            status=draft["status"],
            summary=draft["summary"],
            result=result,
            used_item_ids=sorted(
                set(agent1.candidate_item_ids)
                | set(agent1.facts.get("current_item_ids", []))
            ),
            evidence_source_ids=[str(item.source_id) for item in agent1.evidence],
        )
        sanitized = sanitize_extension_references(agent1, agent2)
        validate_task_result(task_type, sanitized.result)  # re-validate after sanitize
        scope = set(agent1.candidate_item_ids) | set(agent1.facts.get("current_item_ids", []))
        validate_extension_draft(agent1=agent1, agent2=sanitized, wardrobe_ids=scope)
    except (ValueError, ValidationError) as error:
        return str(error)[:500]
    return {
        "extension_result": {
            "task_type": task_type.value,
            "status": sanitized.status,
            "summary": sanitized.summary,
            "result": sanitized.result,
        },
        "handoff_result": AgentHandoffResult(
            status="COMPLETED",
            trace_summary=_trace_summary(trace),
        ),
        "trajectory_done": True,
    }


def _protocol_envelope(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "handoff_result": AgentHandoffResult(
            status="PROTOCOL_ERROR",
            trace_summary=_trace_summary(state.get("trace", [])),
        ),
        "trajectory_done": True,
    }


def _clarification_draft(agent1: Agent1TaskOutput, facts: dict[str, Any]) -> dict[str, Any]:
    """Deterministic needs_clarification draft satisfying every RESULT_MODELS
    contract — used when the deterministic facts already flagged a clarification,
    or when bounded repair ran out. Never invents a completion."""
    task_type = agent1.task_type
    question = (
        facts.get("clarification_question")
        or agent1.clarification_question
        or "需要补充信息后才能继续。"
    )
    if task_type is TaskType.STYLE_ADVICE:
        result: dict[str, Any] = {
            "status": "needs_clarification",
            "title": agent1.intent_summary or (facts.get("resolved_target") or {}).get("style") or "风格建议",
            "summary": question,
            "clarification_question": question,
        }
    elif task_type is TaskType.ITEM_ADVICE:
        result = {
            "status": "needs_clarification",
            "title": agent1.intent_summary or "单品搭配建议",
            "summary": question,
            "clarification_question": question,
        }
    elif task_type is TaskType.WARDROBE_COMPATIBILITY:
        fact_body = facts.get("facts") or {}
        result = {
            "status": "needs_clarification",
            "candidate_item": dict(fact_body.get("candidate_item") or {}),
            "candidate_slot": str(fact_body.get("candidate_slot") or ""),
            "recommendation": "unknown",
            "recommendation_text": question,
            "clarification_question": question,
        }
    elif task_type is TaskType.WARDROBE_GAP:
        target = facts.get("resolved_target") or {}
        fact_body = facts.get("facts") or {}
        result = {
            "status": "needs_clarification",
            "analysis_mode": target.get("analysis_mode") or "general",
            "wardrobe_item_count": int(fact_body.get("wardrobe_item_count") or 0),
            "gap_count": 0,
            "gaps": [],
            "summary": question,
            "clarification_question": question,
        }
    else:
        result = {"status": "needs_clarification"}
    return {"status": "needs_clarification", "summary": question, "result": result}


def _closing_schema(task_type: TaskType) -> dict[str, Any]:
    """The chat_json schema: a status/summary envelope around the task contract
    (``$defs`` lifted so the nested model_json_schema resolves its $refs)."""
    result_schema = RESULT_MODELS[task_type].model_json_schema()
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "status": {"enum": ["completed", "infeasible", "needs_clarification"]},
            "summary": {"type": "string"},
            "result": result_schema,
        },
        "required": ["status", "summary", "result"],
    }
    defs = result_schema.get("$defs")
    if defs:
        schema["$defs"] = defs
    return schema


def _trace_summary(trace: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "agent": "extension",
        "turns": len(trace),
        "decisions": [t.get("decision_summary", "") for t in trace[-3:]],
    }
