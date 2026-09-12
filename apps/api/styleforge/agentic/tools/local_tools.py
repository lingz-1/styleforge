"""The 8 local tools, registered into the CapabilityRegistry (frozen #12).

Each tool is a thin adapter: pydantic input model → Environment method (or
KnowledgeRetriever) → normalized ``ToolCallResult`` observation. The exact
same facts the legacy ``agent.py`` loop formatted now flow through the
Harness gate chain.

Stateful tools:
    modify_outfit  reads ``state["working_draft"]`` via ToolContext and returns
                   the new draft in ``state_updates``; the graph node persists
                   it. ``update_plan`` writes ``state["plan"]`` the same way.
                   Neither touches real session state and neither keeps state
                   in the registry.

Tool registrations are *stable*: agents / requires / precondition are fixed at
harness construction. Runtime deployment is Layer 2 (``runtime_available``),
so the ``tools=`` payload changes only with real capability availability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from styleforge.agentic.observations import (
    MAX_OBSERVATION_CHARS,
    outfit_text,
    skill_observation,
    weather_observation,
    web_search_observation,
)
from styleforge.agentic.runtime.capability_registry import (
    CapabilityRegistry,
    ToolCapability,
)
from styleforge.agentic.runtime.tool_runtime import (
    STATUS_PRECONDITION_FAILED,
    ToolCallResult,
    ToolContext,
)
from styleforge.models.agentic_contract import ModifyPlan, PlanState, UserIntent

# Agent names the local tools are catalogued under.
AGENT_COORDINATOR = "coordinator"
AGENT_RESEARCH = "research"
AGENT_STYLIST = "stylist"
AGENT_EXTENSION = "extension"
AGENT_CRITIC = "critic"
AGENT_RESEARCH_SYNTHESIZER = "research_synthesizer"

# Capability keys the Harness reports as deployed (Layer 2).
CAP_WEB_SEARCH = "web_search"
CAP_WEATHER = "weather"
CAP_KNOWLEDGE = "knowledge"
CAP_SKILLS = "skills"


# ── input models ──────────────────────────────────────────────────────────


class InspectOutfitInput(BaseModel):
    outfit_id: str = Field(
        default="active",
        max_length=128,
        description="搭配 id；active 表示当前正在编辑的搭配",
    )


class SearchWardrobeInput(BaseModel):
    query: str = Field(min_length=1, max_length=200, description="关键词，中英文均可尝试")
    limit: int | None = Field(default=None, description="返回数量上限")


class SearchWebInput(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=200,
        description="外部事实查询（活动/演出/展会/天气等）",
    )


class GetWeatherInput(BaseModel):
    location: str = Field(min_length=1, max_length=120, description="地点名称")
    date_expression: str = Field(
        default="",
        max_length=32,
        description="ISO 日期；空则默认近 3 天",
    )


class SearchKnowledgeInput(BaseModel):
    query: str = Field(min_length=1, max_length=200, description="知识库检索词")
    kind: Literal["style", "item"] = Field(default="style", description="知识类别")


class LoadSkillInput(BaseModel):
    skill_name: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_./-]+$",
        description="任务类型技能名，如 event_outfit_planning",
    )


class ModifyOutfitInput(BaseModel):
    # The provider-facing schema requires semantic intent. The Python default
    # keeps legacy/non-fast direct callers compatible; the fast workflow also
    # enforces presence at execution time below.
    model_config = ConfigDict(json_schema_extra={"required": ["intent", "plan"]})

    intent: UserIntent | None = Field(
        default=None,
        description="对本轮用户需求的语义理解；普通修改任务必须与修改计划一并给出",
    )
    plan: ModifyPlan = Field(description="修改计划（add/remove/replace）")


class UpdatePlanInput(BaseModel):
    plan: PlanState = Field(description="新的全局任务计划")


# ── precondition (Layer 3) ────────────────────────────────────────────────


def _working_draft_present(state: dict[str, Any]) -> str | None:
    if state.get("working_draft") is None:
        return "当前没有正在编辑的搭配（working_draft 为空），无法执行 modify_outfit"
    return None


# ── handlers ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Wrappers:
    """Environment bound into closures; kept as one small object."""

    inspect: Any
    search_wardrobe: Any
    search_web: Any
    get_weather: Any
    load_skill: Any
    modify_outfit: Any
    knowledge: Any | None
    update_plan: Any


def _make_handlers(env: Any, knowledge_retriever: Any | None) -> _Wrappers:
    def inspect(inp: InspectOutfitInput, ctx: ToolContext) -> ToolCallResult:
        outfit_id = (inp.outfit_id or "").strip()
        if outfit_id in ("", "active"):
            draft = ctx.get("working_draft")
            if draft is None:
                return ToolCallResult(observation="当前没有正在编辑的搭配")
            return ToolCallResult(observation=f"当前正在编辑的搭配：{outfit_text(draft.outfit)}")
        snapshot = env.inspect_outfit(outfit_id)
        if snapshot is None:
            return ToolCallResult(observation=f"未找到搭配 {outfit_id}")
        return ToolCallResult(observation=f"搭配 {snapshot.outfit_id}：{outfit_text(snapshot)}")

    def search_wardrobe(inp: SearchWardrobeInput, ctx: ToolContext) -> ToolCallResult:
        result = env.search_wardrobe(inp.query or "", inp.limit)
        items = [f"{item.item_id}({item.item_type}/{item.color})" for item in result.results]
        mode_label = {
            "hybrid": "语义+关键词",
            "semantic": "语义",
            "keyword": "关键词",
        }.get(result.retrieval_mode, "关键词")
        if items:
            return ToolCallResult(
                observation=(
                    f"通过{mode_label}检索找到 {result.matched} 件"
                    f"（展示前 {len(items)} 件）：" + "、".join(items)
                )
            )
        if result.diagnostics.get("pool_count") == 0:
            retry_hint = "当前衣橱没有该品类的可检索单品。"
        elif result.semantic_available:
            retry_hint = "建议换更具体的品类、颜色或风格描述重试。"
        else:
            retry_hint = "语义索引当前不可用，建议换具体关键词或英文同义词重试。"
        return ToolCallResult(
            observation=(
                f"通过{mode_label}检索未找到匹配单品（共检索 {result.matched} 件）。{retry_hint}"
            )
        )

    def search_web(inp: SearchWebInput, ctx: ToolContext) -> ToolCallResult:
        return ToolCallResult(observation=web_search_observation(env.search_web(inp.query or "")))

    def get_weather(inp: GetWeatherInput, ctx: ToolContext) -> ToolCallResult:
        return ToolCallResult(
            observation=weather_observation(
                env.get_weather(inp.location or "", inp.date_expression or "")
            )
        )

    def search_knowledge(inp: SearchKnowledgeInput, ctx: ToolContext) -> ToolCallResult:
        if knowledge_retriever is None:
            return ToolCallResult(observation="知识库未配置。可改用 search_web 或自行判断。")
        evidence, _ = knowledge_retriever.search(inp.query or "", kind=inp.kind, limit=4)
        if not evidence:
            return ToolCallResult(
                observation="知识库未找到相关条目。可改用 search_web 或自行判断。"
            )
        lines = [f"{e.section}：{e.content}" for e in evidence]
        text = "知识库检索结果（仅供知识参考）：\n" + "\n".join(lines)
        return ToolCallResult(observation=text[: MAX_OBSERVATION_CHARS * 2])

    def load_skill(inp: LoadSkillInput, ctx: ToolContext) -> ToolCallResult:
        return ToolCallResult(observation=skill_observation(env.load_skill(inp.skill_name or "")))

    def modify_outfit(inp: ModifyOutfitInput, ctx: ToolContext) -> ToolCallResult:
        draft = ctx.get("working_draft")
        if draft is None:
            return ToolCallResult(
                observation="当前没有正在编辑的搭配（working_draft 为空），无法执行 modify_outfit",
                status=STATUS_PRECONDITION_FAILED,
            )
        fast_modify = bool(ctx.get("auto_submit_after_modify"))
        if fast_modify and inp.intent is None:
            return ToolCallResult(
                observation=(
                    "普通修改任务必须在 modify_outfit.intent 中同时给出对用户消息的"
                    "语义理解（message/goal/requirements），然后再执行修改。"
                )
            )
        if fast_modify and not inp.plan.ops:
            return ToolCallResult(observation="修改计划不能为空，至少需要一个 add/remove/replace 操作。")
        next_draft, issues = env.modify_outfit(draft, inp.plan)
        if issues:
            return ToolCallResult(observation="修改未通过物理校验：" + "；".join(issues))
        state_updates: dict[str, Any] = {"working_draft": next_draft}
        if inp.intent is not None:
            # The raw message is authoritative application input. The model
            # owns goal/requirements but cannot rewrite what the user said.
            state_updates["user_intent"] = inp.intent.model_copy(
                update={"message": str(ctx.get("request") or inp.intent.message)}
            )
        return ToolCallResult(
            observation=(
                f"已应用修改，新搭配：{outfit_text(next_draft.outfit)}。"
                "若此修改已满足用户目标，请直接提交，不要在已改好的单品上继续更换。"
            ),
            state_updates=state_updates,
        )

    def update_plan(inp: UpdatePlanInput, ctx: ToolContext) -> ToolCallResult:
        plan = inp.plan
        lines: list[str] = []
        if plan.objective:
            lines.append(f"目标：{plan.objective}")
        if plan.missing_information:
            lines.append("待查：" + "、".join(plan.missing_information))
        if plan.next_steps:
            lines.append("下一步：" + "、".join(plan.next_steps))
        if plan.remaining_steps:
            lines.append("剩余：" + "、".join(plan.remaining_steps))
        if plan.completed_steps:
            lines.append("已完成：" + "、".join(plan.completed_steps))
        observation = "计划已更新：\n" + "\n".join(lines) if lines else "计划已清空"
        return ToolCallResult(observation=observation, state_updates={"plan": plan})

    return _Wrappers(
        inspect=inspect,
        search_wardrobe=search_wardrobe,
        search_web=search_web,
        get_weather=get_weather,
        load_skill=load_skill,
        modify_outfit=modify_outfit,
        knowledge=search_knowledge,
        update_plan=update_plan,
    )


# ── registration ──────────────────────────────────────────────────────────


def register_local_tools(
    registry: CapabilityRegistry,
    environment: Any,
    *,
    knowledge_retriever: Any | None = None,
) -> None:
    """Register the 8 local tools in deterministic order (frozen plan list).

    ``agents`` and ``requires`` are fixed here; runtime deployment is Layer 2.
    ``environment`` and ``knowledge_retriever`` are Runtime Dependencies
    captured in handler closures — never in the serializable Execution State.
    """
    h = _make_handlers(environment, knowledge_retriever)
    tools: list[ToolCapability] = [
        ToolCapability(
            name="inspect_outfit",
            description="查看某套搭配的详情；outfit_id 不填或填 active 表示当前正在编辑的搭配",
            input_model=InspectOutfitInput,
            handler=h.inspect,
            agents=frozenset({AGENT_STYLIST, AGENT_EXTENSION}),
        ),
        ToolCapability(
            name="search_wardrobe",
            description="在用户衣橱中搜索单品（中英文关键词均可，结果有数量上限）",
            input_model=SearchWardrobeInput,
            handler=h.search_wardrobe,
            agents=frozenset({AGENT_STYLIST, AGENT_EXTENSION}),
        ),
        ToolCapability(
            name="search_web",
            description="联网查询实时外部事实（活动/演出/展会/天气等），服务于穿搭决策",
            input_model=SearchWebInput,
            handler=h.search_web,
            requires=frozenset({CAP_WEB_SEARCH}),
            agents=frozenset({AGENT_RESEARCH, AGENT_STYLIST, AGENT_EXTENSION}),
        ),
        ToolCapability(
            name="get_weather",
            description="按地点查当地天气，date_expression 可空（默认近 3 天）",
            input_model=GetWeatherInput,
            handler=h.get_weather,
            requires=frozenset({CAP_WEATHER}),
            agents=frozenset({AGENT_RESEARCH, AGENT_STYLIST}),
        ),
        ToolCapability(
            name="search_knowledge",
            description="检索本地知识库中的风格/单品知识条目",
            input_model=SearchKnowledgeInput,
            handler=h.knowledge,
            requires=frozenset({CAP_KNOWLEDGE}),
            agents=frozenset({AGENT_RESEARCH, AGENT_STYLIST, AGENT_EXTENSION}),
        ),
        ToolCapability(
            name="load_skill",
            description="加载某类任务的流程知识（如 event_outfit_planning）",
            input_model=LoadSkillInput,
            handler=h.load_skill,
            requires=frozenset({CAP_SKILLS}),
            agents=frozenset({AGENT_RESEARCH, AGENT_STYLIST}),
        ),
        ToolCapability(
            name="modify_outfit",
            description=(
                "修改当前正在编辑的搭配（add/remove/replace，可一次多操作）；"
                "普通修改任务需同时提交 intent，表达对用户目标和约束的语义理解"
            ),
            input_model=ModifyOutfitInput,
            handler=h.modify_outfit,
            agents=frozenset({AGENT_STYLIST}),
            precondition=_working_draft_present,
        ),
        ToolCapability(
            name="update_plan",
            description="更新全局任务计划（objective / 待查 / 下一步 / 剩余步骤）",
            input_model=UpdatePlanInput,
            handler=h.update_plan,
            agents=frozenset({AGENT_COORDINATOR}),
        ),
    ]
    for tool in tools:
        registry.register_tool(tool)
