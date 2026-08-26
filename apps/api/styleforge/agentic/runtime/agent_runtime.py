"""AgentRuntime: one Agent model call through the whole Harness chain.

BEFORE every model call (frozen #15):
    ContextVisibilityPolicy → ContextAssembler → PromptAssembler → ContextGuard → LLM

AFTER it:
    parse the Agent's raw decision block → validate the frozen decision contract
    (``check_decision_contract``) → validate the control/tool combination → hand
    the single tool call (if any) to ToolRuntime.

Protocol errors (parse retry exhausted, contract violations, control/tool
mismatch, context limit) are reported back as ``AgentCallResult.protocol_error``;
the graph does the re-entry accounting (``protocol_error_count``, frozen #21).
The runtime never fabricates a control signal and never calls a tool the agent's
control is not allowed to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Type

from pydantic import BaseModel, ValidationError

from styleforge.agentic.agentic_contract import (
    CoordinatorDecision,
    ExtensionDecision,
    ResearchDecision,
    StylistDecision,
    check_decision_contract,
)
from styleforge.agentic.context.assembler import ContextAssembler
from styleforge.agentic.context.grounding import required_searchable_kinds
from styleforge.agentic.context.guard import ContextGuard
from styleforge.agentic.context.prompt_assembler import PromptAssembler, PromptBundle
from styleforge.agentic.context.visibility import ContextVisibilityPolicy
from styleforge.agentic.hooks.manager import HookManager
from styleforge.agentic.runtime.capability_registry import CapabilityRegistry
from styleforge.agentic.runtime.tool_runtime import ToolRuntime
from styleforge.llm.client import LlmUnavailable, ToolDefinition, ToolUseBlock


class ContextLimitError(RuntimeError):
    """The ContextGuard refused the model call (CONTEXT_LIMIT) — never faked."""


class _CountingLlm:
    """Transparent LLM wrapper that counts every model call.

    Critic / Evidence Synthesizer call ``runtime.llm.chat_json`` directly and
    tool-calling agents go through ``runtime.llm.chat_tools``; both are routed
    through this one seam so the harness can report a single ``llm_call_count``
    (the H2c front-end contract). Any other attribute transparently falls
    through to the wrapped client, so fakes keep recording ``calls`` verbatim.
    """

    def __init__(self, inner: Any, bump: Callable[[], None]) -> None:
        self._inner = inner
        self._bump = bump

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def chat_tools(self, *args: Any, **kwargs: Any) -> Any:
        self._bump()
        return self._inner.chat_tools(*args, **kwargs)

    def chat_json(self, *args: Any, **kwargs: Any) -> Any:
        self._bump()
        return self._inner.chat_json(*args, **kwargs)


@dataclass(frozen=True)
class AgentCallResult:
    """One validated agent turn: a decision, its tool calls, or an error.

    ``tool_uses`` carries the full batch a provider may emit in a single
    response (OpenAI-compatible parallel tool calls). ``tool_use`` stays as the
    first element for trace compatibility.
    """

    decision: BaseModel | None = None
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    tool_use: ToolUseBlock | None = None
    protocol_error: str | None = None
    trace: dict[str, Any] = field(default_factory=dict)
    bundle: PromptBundle | None = None


# control → (min, max) native tool calls required (frozen decision contracts).
# ``max=None`` = unbounded: a provider may emit several parallel tool calls in
# one response, and Research/Stylist CONTINUE executes them all in order. The
# Coordinator has no ``control`` field — its three modes are handled by
# ``_expected_tool_count`` (need_user → 0, need_plan_update → 1, handoff → 0).
_TOOL_COUNT_RULES: dict[str, dict[str, tuple[int, int | None]]] = {
    "stylist": {"CONTINUE": (1, None), "CANDIDATE_READY": (0, 0), "NEED_USER": (0, 0)},
    "research": {"CONTINUE": (1, None), "RESEARCH_COMPLETE": (0, 0), "NEED_USER": (0, 0)},
    "extension": {"CONTINUE": (1, None), "READY": (0, 0), "NEED_USER": (0, 0)},
}


def _expected_tool_count(agent: str, decision: BaseModel) -> tuple[int, int | None] | None:
    """The frozen control/tool contract per agent decision.

    ``None`` = no contract for this agent (no tool-count check). Coordinator
    decisions are mode-based, not control-based (frozen #16/#14).
    """
    if agent == "coordinator":
        return (1, 1) if getattr(decision, "need_plan_update", False) else (0, 0)
    return _TOOL_COUNT_RULES.get(agent, {}).get(getattr(decision, "control", None))


class AgentRuntime:
    def __init__(
        self,
        *,
        llm: Any,
        registry: CapabilityRegistry,
        instructions_root: Any,
        runtime_capabilities: frozenset[str] = frozenset(),
        hooks: HookManager | None = None,
        visibility: ContextVisibilityPolicy | None = None,
        guard: ContextGuard | None = None,
        agent_instruction_versions: dict[str, str] | None = None,
        memory_retriever: Any | None = None,
    ) -> None:
        # Every chat_tools / chat_json call (incl. Critic + Evidence Synthesizer,
        # which reach ``self.llm`` directly) bumps this counter.
        self.model_calls = 0
        self.llm = _CountingLlm(llm, self._bump_model_calls)
        self.registry = registry
        self.runtime_capabilities = runtime_capabilities
        self.hooks = hooks
        self.visibility = visibility or ContextVisibilityPolicy()
        self.assembler = ContextAssembler(self.visibility, memory_retriever)
        self.prompt_assembler = PromptAssembler(
            instructions_root=instructions_root,
            agent_instruction_versions=agent_instruction_versions,
        )
        self.guard = guard or ContextGuard()
        self.tool_runtime = ToolRuntime(registry, hooks)

    def _bump_model_calls(self) -> None:
        self.model_calls += 1

    # -- the one model call -------------------------------------------------

    def call(
        self,
        agent: str,
        state: dict[str, Any],
        *,
        decision_model: Type[BaseModel],
    ) -> AgentCallResult:
        bundle = self.assemble_bundle(agent, state)
        guard = self.guard.check(bundle)
        if guard.bundle is None:
            raise ContextLimitError(f"context guard: {guard.status}: {'；'.join(guard.warnings)}")
        bundle = guard.bundle

        try:
            decision, tool_blocks, parse_error = self._request(agent, bundle, decision_model, state)
            if parse_error is not None:
                # One in-call parse retry (frozen #21: parse retry ≤ 1) with explicit
                # structured-output feedback — providers frequently slip into a
                # natural-language reply on the turn after a tool call. The hint
                # names the agent's exact decision shape so the retry is a template,
                # not an abstraction.
                decision, tool_blocks, parse_error = self._request(
                    agent,
                    bundle,
                    decision_model,
                    state,
                    retry_hint=_retry_hint_for(agent, parse_error),
                )
        except LlmUnavailable as error:
            # A transient provider failure (network blip, overload) is not a
            # logic error in the Agent: report it as a recoverable protocol
            # error so the graph re-enters (bounded by the frozen #21 protocol
            # budget) instead of crashing the whole run.
            return AgentCallResult(protocol_error=f"provider unavailable: {error}")
        if parse_error is not None:
            return AgentCallResult(protocol_error=_parse_error_observation(agent, parse_error))

        violations = check_decision_contract(decision)
        if violations:
            return AgentCallResult(
                protocol_error="；".join(violations),
                decision=decision,
            )

        expected = _expected_tool_count(agent, decision)
        if expected is not None:
            min_calls, max_calls = expected
            count = len(tool_blocks)
            if count < min_calls or (max_calls is not None and count > max_calls):
                if min_calls == max_calls:
                    label = f"{min_calls}"  # keep the historical "requires N tool call(s)" wording
                elif max_calls is None:
                    label = f"at least {min_calls}"
                else:
                    label = f"{min_calls}..{max_calls}"
                return AgentCallResult(
                    protocol_error=_tool_count_observation(
                        agent,
                        getattr(decision, "control", None),
                        f"{label} tool call(s), got {count}",
                    ),
                    decision=decision,
                )
        # H3a-5 (frozen gap 3): SEARCH_FIRST is a lifecycle contract, not a
        # prompt hint. Under a search_first grounding decision the research
        # agent must have *attempted* (good-faith checked) every missing kind a
        # deployed capability can resolve before it may RESEARCH_COMPLETE or
        # NEED_USER. Attempted ≠ resolved: a checked-but-empty search passes
        # (the synthesizer records the gap in uncertainties) — no dead loop.
        if agent == "research":
            grounding = state.get("grounding_context") or {}
            if grounding.get("decision") == "search_first":
                control = getattr(decision, "control", None)
                if control in ("RESEARCH_COMPLETE", "NEED_USER"):
                    required = required_searchable_kinds(
                        grounding.get("missing"), self.runtime_capabilities
                    )
                    attempted = set(state.get("grounding_attempted_kinds") or [])
                    if not required <= attempted:
                        return AgentCallResult(
                            protocol_error=(
                                "Grounding requires verification first. Missing kinds "
                                f"resolvable by your tools: {sorted(required)}; you have only "
                                f"attempted {sorted(attempted)}. Use search_web/get_weather to "
                                "check these before completing or asking the user."
                            ),
                            decision=decision,
                        )
        # Frozen #14: need_plan_update must be the update_plan tool, never a
        # wardrobe mutation.
        if (
            agent == "coordinator"
            and getattr(decision, "need_plan_update", False)
            and tool_blocks
            and any(item.name != "update_plan" for item in tool_blocks)
        ):
            return AgentCallResult(
                protocol_error=(
                    "need_plan_update requires exactly the update_plan tool, "
                    f"got {[item.name for item in tool_blocks]}"
                ),
                decision=decision,
            )

        tool_use = tool_blocks[0] if len(tool_blocks) == 1 else (tool_blocks[0] if tool_blocks else None)
        trace = {
            "agent": agent,
            "decision_summary": getattr(decision, "decision_summary", ""),
            "control": getattr(decision, "control", None),
            "prompt_profile_key": bundle.prompt_profile_key,
            "stable_prefix_fingerprint": bundle.stable_prefix_fingerprint,
        }
        return AgentCallResult(
            decision=decision,
            tool_uses=list(tool_blocks),
            tool_use=tool_use,
            trace=trace,
            bundle=bundle,
        )

    def assemble_bundle(
        self,
        agent: str,
        state: dict[str, Any],
        *,
        tools: list[ToolDefinition] | None = None,
    ) -> PromptBundle:
        """Assemble the prompt for one agent (frozen #15) — no guard applied."""
        context = self.assembler.assemble(agent, state)
        if tools is None:
            tools = self.registry.runtime_available(agent, self.runtime_capabilities)
        return self.prompt_assembler.build(agent, context, tools)

    def execute_tool(self, name: str, arguments: dict[str, Any], *, state: dict[str, Any]):
        """Execute one tool through the ToolRuntime gate chain."""
        return self.tool_runtime.execute(name, arguments, state=state)

    # -- internals ----------------------------------------------------------

    def _request(
        self,
        agent: str,
        bundle: PromptBundle,
        decision_model: Type[BaseModel],
        state: dict[str, Any],
        retry_hint: str | None = None,
    ) -> tuple[BaseModel | None, list[ToolUseBlock], str | None]:
        user_message = bundle.user_message
        if retry_hint:
            user_message = f"{user_message}\n\n{retry_hint}"
        decision_text, tool_blocks, _ = self.llm.chat_tools(
            system=bundle.system_text,
            user=user_message,
            tools=bundle.tools,
        )
        decision, error = _parse_decision(decision_text, decision_model)
        if error is not None and decision is None and tool_blocks:
            # A tool-calling provider may return an empty assistant text when the
            # model decides to call a tool (the decision JSON is omitted), or a
            # prose prefix around the tools when its narration mode slips in —
            # both are the same signal: the call itself means "keep working".
            # The decision is synthesised (CONTINUE for Research/Stylist,
            # need_plan_update for the Coordinator) and the tool-count and
            # update_plan-only checks below still guard the call. A terminal
            # decision (RESEARCH_COMPLETE / CANDIDATE_READY) never carries tools,
            # so prose WITHOUT tools stays a protocol error — a completion is
            # never fabricated from narration.
            decision, tool_blocks, error = _infer_decision(
                agent, decision_model, tool_blocks, state
            )
        return decision, tool_blocks, error


def _retry_hint_for(agent: str, parse_error: str) -> str:
    """A parse-retry hint naming the exact decision shape for this agent.

    The generic "output a JSON object" hint is too abstract for a provider that
    just slipped into a natural-language reply; giving it the concrete template
    converts most of those into a valid decision block.
    """
    templates = {
        "coordinator": '{"decision_summary": "...", "goal": "...", "next_agent": "STYLIST" | "RESEARCH" | "EXTENSION"}',
        "research": '{"decision_summary": "...", "control": "CONTINUE" | "RESEARCH_COMPLETE"}',
        "stylist": '{"decision_summary": "...", "control": "CONTINUE" | "CANDIDATE_READY"}',
        "extension": '{"decision_summary": "...", "control": "CONTINUE" | "READY"}',
    }
    example = templates.get(agent, '{"decision_summary": "..."}')
    return (
        f"你上一条输出无法解析为决策 JSON（{parse_error}）。"
        "你输出的是散文而不是决策块。"
        f"请只输出如下形式的 JSON 对象，不要任何其他文字：{example}"
    )


def _tool_count_observation(agent: str, control: str | None, expectation: str) -> str:
    """Re-entry observation for a control/tool-count mismatch.

    The raw English contract error (``stylist control CONTINUE requires at least
    1 tool call(s), got 0``) tells the model *what* failed but not *what to do* —
    a real provider repeatedly emits ``CONTINUE`` with a decision_summary that
    *describes* the plan instead of calling a tool, and the same observation gets
    replayed verbatim. This message forces the two-way choice (frozen #21:
    never fabricate a decision, never silently drop the turn):
    """
    if agent == "stylist":
        return (
            f"你的控制信号 {control} 需要{expectation}，但你这次没有发出工具调用。"
            "你只有两个选择：\n"
            "1) CONTINUE → 必须伴随恰好一个工具调用（modify_outfit / search_wardrobe / "
            "search_web / get_weather 等），直接动手；\n"
            "2) CANDIDATE_READY → 当前搭配已满足目标，直接提交（0 工具调用）。\n"
            "不要只描述你打算做什么——要么调用工具，要么提交当前方案。"
        )
    if agent == "research":
        return (
            f"你的控制信号 {control} 需要{expectation}，但你这次没有发出工具调用。"
            "CONTINUE 必须伴随恰好一个工具调用；若外部事实已查够，请直接输出 "
            'control: "RESEARCH_COMPLETE"（0 工具调用）收尾。'
        )
    if agent == "extension":
        return (
            f"你的控制信号 {control} 需要{expectation}，但你这次没有发出工具调用。"
            "CONTINUE 必须伴随恰好一个工具调用（search_wardrobe / search_knowledge / "
            "search_web / inspect_outfit）；若确定性事实已足够，请直接输出 "
            'control: "READY"（0 工具调用）进入结果综合。'
        )
    return (
        f"{agent} control {control} requires {expectation}"
    )


def _parse_error_observation(agent: str, parse_error: str) -> str:
    """Re-entry observation for a parse/prose failure.

    The in-call retry already showed the exact schema template; this message is
    the last friendly instruction before the graph burns its protocol-error
    budget. It states the failure in plain Chinese (the raw pydantic error is
    English and mostly noise) and offers a graceful exit — a valid terminal
    decision — so a model stuck in narration mode can finish the turn instead of
    cycling prose forever.
    """
    exit_decision = {
        "coordinator": '{"decision_summary": "...", "goal": "...", "next_agent": "STYLIST"}',
        "research": '{"decision_summary": "...", "control": "RESEARCH_COMPLETE"}',
        "stylist": '{"decision_summary": "...", "control": "CANDIDATE_READY"}',
        "extension": '{"decision_summary": "...", "control": "READY"}',
    }.get(agent, '{"decision_summary": "..."}')
    return (
        f"你上一条输出无法解析为决策 JSON（{parse_error[:200]}）。"
        "你输出的是散文/叙述而不是决策块，散文会被程序丢弃。"
        "如果你需要查询或改动，就发起对应的工具调用（search_web / search_wardrobe / "
        "modify_outfit / get_weather / load_skill 等），正文只放决策 JSON；"
        "不要用自然语言描述你想做什么。"
        "请只输出一个 JSON 对象，不要任何其他文字。"
        "若你无法继续，直接输出这个合法决策来结束本回合："
        f"{exit_decision}"
    )


def _parse_decision(text: str, decision_model: Type[BaseModel]) -> tuple[BaseModel | None, str | None]:
    cleaned = _strip_code_fence(text).strip()
    if not cleaned:
        return None, "empty decision block"
    try:
        decision = decision_model.model_validate_json(cleaned)
        return decision, None
    except (ValidationError, ValueError) as error:
        # A tool-calling provider may sandwich the decision JSON between natural
        # language ("I need to check… ```json {...} ```"). Extract the first
        # balanced JSON object before giving up (frozen #21 parse-retry budget).
        extracted = _extract_json_object(cleaned)
        if extracted is None:
            return None, str(error)[:500]
    try:
        decision = decision_model.model_validate_json(extracted)
    except (ValidationError, ValueError) as error:
        return None, str(error)[:500]
    return decision, None


def _extract_json_object(text: str) -> str | None:
    """Return the first balanced JSON object in ``text``, or None.

    Scans from the first ``{`` and tracks nesting while skipping quoted strings,
    so a leading prose prefix (or trailing explanation) around the JSON block is
    tolerated.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _infer_decision(
    agent: str,
    decision_model: Type[BaseModel],
    tool_blocks: list[ToolUseBlock],
    state: dict[str, Any] | None = None,
) -> tuple[BaseModel | None, list[ToolUseBlock], str | None]:
    """Synthesise the decision a tool call implies when the assistant text is empty.

    Only reached when there was NO text at all AND at least one tool call — a
    legitimate OpenAI-compatible behaviour. The synthesised decision is minimal
    and the normal contract checks (tool count, update_plan-only) still apply.

    Returns the *effective* tool blocks too: the Coordinator spin guard swallows
    a repeated ``update_plan`` (it was never executed, so dropping it is safe) —
    otherwise the handoff it synthesises would fail the 0-tool-count check.
    """
    if agent == "coordinator" and decision_model is CoordinatorDecision:
        # Coordinator tools are update_plan-only (frozen #14); the
        # need_plan_update → update_plan guard below still validates the name.
        # A tool-calling provider can keep emitting update_plan on every turn
        # (it never sees the previous assistant tool call, each request is a
        # fresh model call). If the plan is ALREADY in state, the first
        # update_plan already ran — the plan is the product, the next turn must
        # hand off (frozen #14: "next round hands off"). Where to goes follows
        # the evidence: gathered → STYLIST; still missing → RESEARCH.
        if state is not None and state.get("plan") is not None:
            next_agent = (
                "STYLIST" if state.get("research_evidence") is not None else "RESEARCH"
            )
            return (
                CoordinatorDecision(decision_summary="", goal="", next_agent=next_agent),
                [],
                None,
            )
        return (
            CoordinatorDecision(decision_summary="", goal="", need_plan_update=True),
            tool_blocks,
            None,
        )
    if agent == "research" and decision_model is ResearchDecision:
        return ResearchDecision(decision_summary="", control="CONTINUE"), tool_blocks, None
    if agent == "stylist" and decision_model is StylistDecision:
        return StylistDecision(decision_summary="", control="CONTINUE"), tool_blocks, None
    if agent == "extension" and decision_model is ExtensionDecision:
        return ExtensionDecision(decision_summary="", control="CONTINUE"), tool_blocks, None
    return None, tool_blocks, "empty decision block with tool calls"


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text
