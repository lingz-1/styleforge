"""PromptAssembler: configure the model's context for ONE call, cache-friendly.

The system prompt is not a static string — it is configured at runtime per
Agent + state, but "runtime configured" ≠ "all dynamic". Layers A→D increase in
dynamism so the front stays byte-identical:

    A. stable_system        shared/harness_core.md + shared/tool_protocol.md
                             + {agent}.md  (loaded once, cached per agent)
    B. capability_context   one-line manifest of the tools actually deployable
                             (B layer never repeats JSON Schema — the full
                             schema rides only in ``bundle.tools`` → ``tools=``)
    C. runtime_context      evidence / plan / drafts / candidates / facts /
                             memories / thread, gated by the visibility view
    D. user_message         the current request + goal

Versioning is deliberately split (frozen #13/#24):
    prompt_profile_key       human-readable: agent + harness_core + agent
                             instruction + tool_catalog + skill_manifest
                             versions ("stylist:v1:v1:v1:v1"). Trace key only;
                             request/memory/draft/evidence NEVER participate.
    stable_prefix_fingerprint machine sha256 over (stable_system + capability
                             manifest + canonical tool definitions + skill
                             manifest) — the only way to tell whether two
                             calls really share the same stable prefix bytes.
Both are for the harness's own observability; neither is passed to the
provider to control its prompt cache (that is an LLM-adapter capability).

This assembler is protocol-agnostic: the same ``build()`` feeds
``chat_tools`` (Coordinator / Research / Stylist) and ``chat_json``
(Critic / Evidence Synthesizer) — those agents never hand-craft prompts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from styleforge.agentic.context.grounding import thread_grounding_to_prompt
from styleforge.agentic.context.thread_preferences import thread_preferences_to_prompt
from styleforge.agentic.context.wardrobe_index import format_wardrobe_index

from pydantic import BaseModel, ConfigDict, Field

from styleforge.agentic.agentic_contract import ResearchEvidence, TaskState
from styleforge.agentic.context.assembler import PromptContext
from styleforge.agentic.observations import MAX_OBSERVATION_CHARS, outfit_text
from styleforge.llm.client import ToolDefinition

DEFAULT_AGENT_INSTRUCTION_VERSION = "v1"


class ContextStats(BaseModel):
    total_chars: int = 0
    stable_chars: int = 0
    dynamic_chars: int = 0
    tools: int = 0


class PromptBundle(BaseModel):
    """The explicitly segmented model context for one call (frozen contract)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    stable_system: str = ""
    capability_context: str = ""
    runtime_context: str = ""
    user_message: str = ""
    tools: list[ToolDefinition] = Field(default_factory=list)
    prompt_profile_key: str | None = None
    stable_prefix_fingerprint: str | None = None
    context_stats: ContextStats = Field(default_factory=ContextStats)

    @property
    def system_text(self) -> str:
        """The full system prompt: stable prefix + capability + runtime."""
        return "\n\n".join(
            part for part in (self.stable_system, self.capability_context, self.runtime_context) if part
        )


class PromptAssembler:
    def __init__(
        self,
        *,
        instructions_root: Path | str,
        harness_core_version: str = "v1",
        tool_catalog_version: str = "v1",
        skill_manifest_version: str = "v1",
        agent_instruction_versions: dict[str, str] | None = None,
    ) -> None:
        self.instructions_root = Path(instructions_root)
        self.harness_core_version = harness_core_version
        self.tool_catalog_version = tool_catalog_version
        self.skill_manifest_version = skill_manifest_version
        self._agent_versions = dict(agent_instruction_versions or {})
        self._stable_cache: dict[str, str] = {}

    # -- public API --------------------------------------------------------

    def build(
        self,
        agent: str,
        context: PromptContext,
        tools: list[ToolDefinition],
    ) -> PromptBundle:
        stable_system = self._stable_system(agent)
        capability_context = self._capability_context(tools)
        skill_manifest = self._skill_manifest(context.loaded_skills)
        # B layer = tool capability manifest + skill manifest (metadata only).
        if skill_manifest:
            capability_context = (
                f"{capability_context}\n\n{skill_manifest}" if capability_context else skill_manifest
            )
        runtime_context = self._runtime_context(context)
        user_message = self._user_message(context)

        stable_chars = len(stable_system) + len(capability_context)
        dynamic_chars = len(runtime_context) + len(user_message)
        return PromptBundle(
            stable_system=stable_system,
            capability_context=capability_context,
            runtime_context=runtime_context,
            user_message=user_message,
            tools=list(tools),
            prompt_profile_key=self._profile_key(agent),
            stable_prefix_fingerprint=self._fingerprint(
                stable_system, capability_context, skill_manifest, tools
            ),
            context_stats=ContextStats(
                total_chars=stable_chars + dynamic_chars,
                stable_chars=stable_chars,
                dynamic_chars=dynamic_chars,
                tools=len(tools),
            ),
        )

    # -- layer A: stable system prefix --------------------------------------

    def _stable_system(self, agent: str) -> str:
        cached = self._stable_cache.get(agent)
        if cached is not None:
            return cached
        parts = [
            self._read("shared/harness_core.md"),
            self._read("shared/tool_protocol.md"),
            self._read(f"{agent}.md"),
        ]
        stable = "\n\n".join(parts).strip() + "\n"
        self._stable_cache[agent] = stable
        return stable

    def _read(self, relative: str) -> str:
        path = self.instructions_root / relative
        try:
            return path.read_text(encoding="utf-8")
        except OSError as error:
            raise FileNotFoundError(f"instruction file missing: {path} ({error})") from error

    # -- layer B: capability + skill manifests ------------------------------

    def _capability_context(self, tools: list[ToolDefinition]) -> str:
        """One-line capability manifest — never the JSON Schema (frozen #11).

        The full schema travels only in ``bundle.tools`` → ``tools=``, so the
        model sees exactly one copy of each tool's contract.
        """
        if not tools:
            return ""
        lines = ["【可用能力清单】"]
        lines.extend(f"- {tool.name}：{tool.description}" for tool in tools)
        return "\n".join(lines)

    def _skill_manifest(self, loaded_skills: list[str]) -> str:
        """Skill manifest: metadata-only catalog (bodies load via load_skill)."""
        if not loaded_skills:
            return ""
        return "【可用技能】" + "；".join(loaded_skills)

    # -- layer C: runtime context (gated by the visibility view) -------------

    def _runtime_context(self, context: PromptContext) -> str:
        view = context.view
        sections: list[str] = []
        if view is None:
            return ""
        if view.enabled("plan") and context.plan is not None:
            sections.append(_format_plan(context.plan))
        if view.enabled("task_state") and context.task_state is not None:
            sections.append(_format_task_state(context.task_state))
        if view.enabled("research_evidence") and context.research_evidence is not None:
            sections.append(_format_evidence(context.research_evidence))
        if view.enabled("raw_evidence") and context.raw_evidence:
            sections.append(_format_raw_evidence(context.raw_evidence))
        if view.enabled("candidates") and context.candidates:
            sections.append(_format_candidates(context.candidates))
        if view.enabled("drafts"):
            draft_section = _format_drafts(
                context.working_draft, context.base_draft, len(context.candidates)
            )
            if draft_section:
                sections.append(draft_section)
        if view.enabled("environment_facts") and context.environment_facts is not None:
            sections.append(_format_facts(context.environment_facts))
        # C-layer tail order (H3a): thread → memories → grounding, so the
        # grounding decision is the last thing the model reads before the turn.
        if view.enabled("thread_context") and context.thread_context:
            sections.append(_format_thread(context.thread_context))
        if view.enabled("memories") and context.memories:
            sections.append(_format_memories(context.memories))
        if view.enabled("grounding") and context.grounding:
            sections.append(_format_grounding(context.grounding))
        return "\n\n".join(sections)

    # -- layer D: current turn ----------------------------------------------

    def _user_message(self, context: PromptContext) -> str:
        lines = [f"用户消息：{context.user_request}"]
        if context.goal:
            lines.append(f"已确认目标：{context.goal}")
        if context.gate_feedback:
            lines.append("")
            lines.append(f"【上一轮审校反馈】{context.gate_feedback}")
        if context.tool_observations:
            lines.append("")
            lines.append("【最近工具观察】")
            for index, item in enumerate(context.tool_observations[-3:], 1):
                tool = item.get("tool") or "?"
                text = str(item.get("observation") or "")[: MAX_OBSERVATION_CHARS * 2]
                lines.append(f"[{index}] {tool}：{text}")
        return "\n".join(lines)

    # -- versioning / fingerprint -------------------------------------------

    def _profile_key(self, agent: str) -> str:
        instruction_version = self._agent_versions.get(agent, DEFAULT_AGENT_INSTRUCTION_VERSION)
        return (
            f"{agent}:{self.harness_core_version}:{instruction_version}:"
            f"{self.tool_catalog_version}:{self.skill_manifest_version}"
        )

    def _fingerprint(
        self,
        stable_system: str,
        capability_context: str,
        skill_manifest: str,
        tools: list[ToolDefinition],
    ) -> str:
        canonical_tools = _canonical_tools(tools)
        payload = "\x1f".join(
            [stable_system, capability_context, skill_manifest, canonical_tools]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_tools(tools: list[ToolDefinition]) -> str:
    """Deterministic canonical form of the tool catalog for the fingerprint.

    Sorted by name so the hash does not depend on registry iteration order.
    """
    blocks: list[str] = []
    for tool in sorted(tools, key=lambda item: item.name):
        blocks.append(
            json.dumps(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return "\n".join(blocks)


# ── C-layer section formatters ──────────────────────────────────────────────

def _format_plan(plan: Any) -> str:
    lines = ["【任务计划】"]
    if plan.objective:
        lines.append(f"目标：{plan.objective}")
    if plan.completed_steps:
        lines.append("已完成：" + "、".join(plan.completed_steps))
    if plan.next_steps:
        lines.append("下一步：" + "、".join(plan.next_steps))
    if plan.remaining_steps:
        lines.append("剩余：" + "、".join(plan.remaining_steps))
    if plan.missing_information:
        lines.append("待查：" + "、".join(plan.missing_information))
    return "\n".join(lines)


def _format_task_state(state: TaskState) -> str:
    lines = ["【任务状态】"]
    if state.goal:
        lines.append(f"目标：{state.goal}")
    if state.completed:
        lines.append("已完成：" + "、".join(state.completed))
    if state.pending:
        lines.append("待办：" + "、".join(state.pending))
    if state.next_agent:
        lines.append(f"下一处理 agent：{state.next_agent}")
    return "\n".join(lines)


def _format_raw_evidence(raw_evidence: list[dict[str, Any]]) -> str:
    """The Research subgraph's private buffer, rendered for the Synthesizer only."""
    lines = ["【研究原始证据】"]
    for index, item in enumerate(raw_evidence, 1):
        source = item.get("source") or {}
        kind = source.get("kind") or ""
        content = str(item.get("content") or "")[: MAX_OBSERVATION_CHARS * 2]
        lines.append(f"[{index}] {kind}：{content}")
    return "\n".join(lines)


def _format_evidence(evidence: ResearchEvidence) -> str:
    lines = ["【研究证据】"]
    if evidence.event:
        name = evidence.event.name or ""
        description = evidence.event.description or ""
        lines.append(f"活动：{name}" + (f"（{description}）" if description else ""))
    if evidence.venue:
        lines.append(f"地点：{evidence.venue.name} {evidence.venue.location}".strip())
    if evidence.timing:
        lines.append(f"时间：{evidence.timing.date} {evidence.timing.time}".strip())
    if evidence.weather:
        lines.append(
            f"天气：{evidence.weather.location} {evidence.weather.temperature_c} {evidence.weather.condition}".strip()
        )
    for label, values in (
        ("着装语境", evidence.dress_context),
        ("实际需求", evidence.practical_requirements),
        ("限制", evidence.restrictions),
        ("主题元素", evidence.theme_elements),
    ):
        if values:
            lines.append(f"{label}：" + "；".join(values))
    if evidence.uncertainties:
        lines.append("未确认：" + "；".join(evidence.uncertainties))
    return "\n".join(lines)


def _format_candidates(candidates: list[dict[str, Any]]) -> str:
    lines = ["【已产出的候选搭配】"]
    for index, candidate in enumerate(candidates, 1):
        item_ids = candidate.get("item_ids") or []
        lines.append(f"候选{index}：" + ("、".join(item_ids) if item_ids else "（空）"))
    lines.append("下一套请与已有候选方向明显不同。")
    return "\n".join(lines)


def _format_drafts(working_draft: Any, base_draft: Any, candidate_count: int = 0) -> str:
    draft = working_draft if working_draft is not None else base_draft
    if draft is None:
        return ""
    outfit = getattr(draft, "outfit", None)
    if outfit is None or not outfit.item_ids:
        # Recommend-mode reset: the previous candidate was saved, this draft was
        # cleared. The model repeatedly mistakes the empty draft for the last
        # candidate and fires replace/remove ops that atomically fail — the
        # reset must be unmistakable so it composes fresh instead.
        if candidate_count:
            return (
                f"当前搭配：（空）——上一套已保存为候选{candidate_count}，这是全新的一套。"
                "必须**从零组合**：一次性 add 全部所需单品（上装/下装/鞋履，按需外套/配饰），"
                "不要对空搭配做 replace 或 remove。"
            )
        return "当前搭配：（空）——从零组合一套完整搭配。"
    return f"当前搭配：{outfit_text(outfit)}"


def _format_facts(facts: Any) -> str:
    lines = ["【环境事实】"]
    if facts.wardrobe_summary:
        # A count-level capability index (~300-500 chars), never the 262 KB item
        # dump. Concrete ids come from the search_wardrobe tool (see stylist.md).
        lines.append(format_wardrobe_index(facts.wardrobe_summary))
    if facts.weather:
        lines.append(f"天气快照：{json.dumps(facts.weather, ensure_ascii=False)}")
    if facts.selected_item is not None:
        lines.append(
            f"用户点击定位的单品：{facts.selected_item.item_id}"
            f"({facts.selected_item.item_type}/{facts.selected_item.color})"
        )
    if facts.active_outfit is not None:
        lines.append(f"正在编辑的搭配：{outfit_text(facts.active_outfit)}")
    # memory_profile is deliberately NOT dumped here: the full preference list
    # (~12 K chars) polluted the prompt. Layered Top-K recall replaces it via the
    # memories channel (PreferenceRetriever, H3a-4).
    return "\n".join(lines)


def _format_memories(memories: list[Any]) -> str:
    """Render recalled preferences, grouped by read-chain layer when tagged.

    H3a-4 PreferenceRetriever entries carry a ``layer``/``layer_label`` and render
    under 【偏好上下文】 in read-chain order (短期偏好 → 场景偏好 → 长期偏好 →
    避免), keeping the layer boundaries visible. Untagged legacy entries keep the
    flat 【相关记忆】 shape.
    """
    if memories and all(
        isinstance(item, dict) and item.get("layer") for item in memories
    ):
        sections: list[str] = []
        current_label: str | None = None
        for item in memories:
            label = str(item.get("layer_label") or item.get("layer") or "偏好")
            if label != current_label:
                sections.append(f"\n【{label}】")
                current_label = label
            sections.append(f"- {_memory_text(item)}")
        return "【偏好上下文】" + "\n".join(sections)
    lines = ["【相关记忆】"]
    for memory in memories:
        text = memory if isinstance(memory, str) else json.dumps(memory, ensure_ascii=False)
        lines.append(f"- {text}")
    return "\n".join(lines)


def _memory_text(item: dict[str, Any]) -> str:
    """One preference row: ``- [dimension][polarity][置信度] value``."""
    dimension = item.get("dimension") or item.get("category") or "general"
    value = item.get("value") or item.get("content") or item.get("attribute") or ""
    confidence = float(item.get("confidence") or 0.0)
    polarity = item.get("polarity") or ""
    polarity_tag = f"[{polarity}]" if polarity else ""
    prefix = "避免: " if polarity == "negative" else ""
    return f"[{dimension}]{polarity_tag}[置信度 {confidence:.2f}] {prefix}{value}"


def _format_grounding(grounding: dict[str, Any]) -> str:
    """【环境定位】— the deterministic grounding facts for this turn (H3a).

    Renders the date / city / activity the model should reason from, plus the
    search-before-ask decision so research/stylist know what remains to verify.
    """
    lines = ["【环境定位】"]
    if grounding.get("current_date"):
        lines.append(f"当前日期：{grounding['current_date']}")
    if grounding.get("current_city"):
        lines.append(
            f"当前城市：{grounding['current_city']}"
            f"（来源：{grounding.get('location_source', 'none')}）"
        )
    if grounding.get("destination_city"):
        lines.append(f"观演城市：{grounding['destination_city']}")
    if grounding.get("explicit_date"):
        lines.append(f"日期：{grounding['explicit_date']}")
    elif grounding.get("approximate_time"):
        lines.append(f"时间：{grounding['approximate_time']}")
    elif grounding.get("date_expression"):
        lines.append(f"时间：{grounding['date_expression']}")
    if grounding.get("activity"):
        lines.append(f"活动：{grounding['activity']}")
    decision = grounding.get("decision", "ready")
    if decision == "search_first":
        missing = "、".join(grounding.get("missing") or [])
        lines.append(f"决策：先查证再作答（待查证：{missing or '（无）'}）")
    elif decision == "need_user":
        missing = "、".join(grounding.get("missing") or [])
        lines.append(f"决策：需向用户确认（缺失：{missing}）")
    else:
        lines.append("决策：上下文已足够")
    return "\n".join(lines)


def _format_thread(thread: dict[str, Any]) -> str:
    """Render the session-scoped thread layer.

    Thread preferences and grounding get dedicated human-readable sections
    (H3a-2); everything else (outfit anchor, session signals) rides the compact
    JSON line.
    """
    sections: list[str] = []
    if thread.get("thread_preferences"):
        sections.append(thread_preferences_to_prompt(thread["thread_preferences"]))
    if thread.get("thread_grounding"):
        sections.append(thread_grounding_to_prompt(thread["thread_grounding"]))
    other = {
        key: value
        for key, value in thread.items()
        if key not in ("thread_preferences", "thread_grounding")
    }
    if other:
        sections.append(f"【对话上下文】{json.dumps(other, ensure_ascii=False)}")
    return "\n\n".join(sections) or "【对话上下文】（无）"
