"""Semantic Agent: a bounded ReAct loop over the minimal tool set.

The Agent owns semantics — it reads the user's message and decides what to do.
The program owns execution (Environment tools) and the finish gate (forced
``check_environment`` + ``review_outfit``). The Agent decides when it thinks it
is done; the program decides whether it is allowed to be done.

This is the Stage 2 *minimal* loop: no weather / RAG / Skill / Memory tools.
Weather and the memory profile arrive as snapshots inside EnvironmentFacts and
are never actively retrieved.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from styleforge.llm.client import LlmSchemaViolation
from styleforge.models.agentic_contract import (
    EnvironmentFacts,
    ModifyPlan,
    OutfitSnapshot,
    ReviewResult,
    UserIntent,
)
from styleforge.agentic.environment import Draft, Environment
from styleforge.agentic.reviewer import review_outfit

SYSTEM_PROMPT = """\
你是穿搭修改 Agent。你的职责是理解用户想怎么穿，然后通过工具修改一套穿搭。

环境已经告诉你：
- 当前正在编辑的活动穿搭（active outfit）
- 衣橱摘要（按单品类型统计，含颜色样例）
- 天气快照（若有）
- 用户记忆画像（若有）

你可以使用的工具：
- inspect_outfit {outfit_id}：查看某套穿搭的详情
- search_wardrobe {query}：在用户衣橱中搜索单品。结果有数量上限；空结果不代表衣柜里没有，可以换一个表达再搜
- modify_outfit {plan}：修改当前穿搭。add/remove/replace 三种操作，可以一次提交多个。程序会做物理校验，非法修改会被拒绝并返回原因
- check_environment：检查当前穿搭的物理合法性（可选；最终提交时程序也会强制检查）
- ask_user {question}：需要用户澄清时使用（例如用户要求明确，但衣柜里确实没有满足条件的单品）
- finish：你认为修改完成时使用

每次只输出一个 JSON 对象：
{
  "thought": "你的推理（不会展示给用户）",
  "goal": "最终目标（自然语言，一句话）",
  "requirements": ["用户的明确要求，逐条列出"],
  "action": "inspect_outfit | search_wardrobe | modify_outfit | check_environment | ask_user | finish",
  "query": "search_wardrobe 时填",
  "outfit_id": "inspect_outfit 时填",
  "plan": {"ops": [{"action": "add|remove|replace", "item_id": "...", "replacement_item_id": "...", "placement": {"region": "...", "layer": "..."}}], "reasoning": "..."},
  "question": "ask_user 时填"
}
不要输出 JSON 以外的任何内容。"""


class AgentStep(BaseModel):
    """One decision of the Agent: think, then act."""

    thought: str = ""
    goal: str = ""
    requirements: list[str] = Field(default_factory=list)
    action: Literal[
        "inspect_outfit",
        "search_wardrobe",
        "modify_outfit",
        "check_environment",
        "ask_user",
        "finish",
    ]
    query: str = ""
    outfit_id: str = ""
    plan: ModifyPlan | None = None
    question: str = ""


class LoopConfig(BaseModel):
    max_steps: int = 8
    ask_user_cap: int = 2
    search_limit: int = 12


def _outfit_text(outfit: OutfitSnapshot | None) -> str:
    if outfit is None:
        return "（无）"
    items = [
        f"{item.item_id}({item.item_type or '?'}/{item.color or '?'})"
        for item in outfit.items
    ]
    if not items:
        items = list(outfit.item_ids)
    return "；".join(items) or "（空）"


def _args_for(step: AgentStep) -> dict[str, Any]:
    if step.action == "search_wardrobe":
        return {"query": step.query}
    if step.action == "inspect_outfit":
        return {"outfit_id": step.outfit_id}
    if step.action == "modify_outfit":
        return {"plan": step.plan.model_dump(mode="json") if step.plan else {}}
    if step.action == "ask_user":
        return {"question": step.question}
    return {}


class AgentLoop:
    """Bounded ReAct loop. The Agent decides; the program gates completion."""

    def __init__(
        self,
        llm: Any,
        environment: Environment,
        user_message: str,
        *,
        config: LoopConfig | None = None,
    ) -> None:
        self.llm = llm
        self.environment = environment
        self.user_message = user_message
        self.config = config or LoopConfig()
        if self.config.search_limit != environment.search_limit:
            environment.search_limit = self.config.search_limit

    def run(self) -> dict[str, Any]:
        facts = self.environment.facts
        original = facts.active_outfit or OutfitSnapshot(
            outfit_id="draft", item_ids=[], items=[]
        )
        draft = Draft(outfit=original, layers={})
        steps: list[dict[str, Any]] = []
        intent: UserIntent | None = None
        llm_calls = 0
        for step_index in range(self.config.max_steps):
            system, user = self._build_prompt(facts, draft, steps, intent)
            payload, _ = self.llm.chat_json(
                system=system,
                user=user,
                json_schema=AgentStep.model_json_schema(),
            )
            llm_calls += 1
            try:
                step = AgentStep.model_validate(payload)
            except ValidationError as error:
                raise LlmSchemaViolation(
                    f"agentic agent step schema violation: {error}"
                ) from error
            if intent is None and (step.goal or step.requirements):
                # Intent is the Agent's *reading of the user message* — capture
                # it from the first substantive decision and keep it. Re-deriving
                # it every step would let a late generic restatement overwrite
                # the specific goal the Reviewer must check against.
                intent = UserIntent(
                    message=self.user_message,
                    goal=step.goal,
                    requirements=step.requirements,
                )
            if step.action == "ask_user":
                return self._outcome(
                    "ask_user", draft, intent, steps, step.question, llm_calls
                )
            if step.action == "finish":
                finished, llm_calls = self._forced_finish(
                    original, draft, intent, steps, llm_calls
                )
                if finished is not None:
                    return finished
                continue  # not allowed to finish yet: replan on the observation
            observation, draft = self._dispatch(step, draft)
            steps.append(
                {
                    "step": step_index + 1,
                    "action": step.action,
                    "args": _args_for(step),
                    "observation": observation,
                }
            )
        return self._outcome("timeout", draft, intent, steps, None, llm_calls)

    # --- gate -----------------------------------------------------------

    def _forced_finish(
        self,
        original: OutfitSnapshot,
        draft: Draft,
        intent: UserIntent | None,
        steps: list[dict[str, Any]],
        llm_calls: int,
    ) -> tuple[dict[str, Any] | None, int]:
        """The Runner forces both gates even if the Agent forgot to check.

        Gate 1: check_environment. Gate 2: review_outfit. Only PASS + PASS is
        a success; anything else is an observation the Agent replans on.
        """
        env_result = self.environment.check_environment(draft)
        if not env_result.valid:
            steps.append(
                {
                    "step": len(steps) + 1,
                    "action": "check_environment",
                    "args": {},
                    "observation": "物理校验未通过：" + "；".join(env_result.issues),
                }
            )
            return None, llm_calls
        review = self._call_reviewer(original, draft, intent)
        llm_calls += 1
        if review.approved:
            return (
                self._outcome("success", draft, intent, steps, None, llm_calls, review),
                llm_calls,
            )
        steps.append(
            {
                "step": len(steps) + 1,
                "action": "review_outfit",
                "args": {},
                "observation": review.feedback or "；".join(review.issues) or "审校未通过",
            }
        )
        return None, llm_calls

    def _call_reviewer(
        self,
        original: OutfitSnapshot,
        draft: Draft,
        intent: UserIntent | None,
    ) -> ReviewResult:
        return review_outfit(
            self.llm,
            user_message=self.user_message,
            interaction=self.environment.facts.interaction,
            before=original,
            after=draft.outfit,
            intent=intent or UserIntent(message=self.user_message),
        )

    # --- dispatch ---------------------------------------------------------

    def _dispatch(self, step: AgentStep, draft: Draft) -> tuple[str, Draft]:
        if step.action == "inspect_outfit":
            snapshot = self.environment.inspect_outfit(step.outfit_id)
            if snapshot is None:
                return f"未找到搭配 {step.outfit_id}", draft
            return f"搭配 {snapshot.outfit_id}：{_outfit_text(snapshot)}", draft
        if step.action == "search_wardrobe":
            result = self.environment.search_wardrobe(step.query)
            items = [
                f"{item.item_id}({item.item_type}/{item.color})"
                for item in result.results
            ]
            if items:
                return (
                    f"找到 {result.matched} 件（展示前 {len(items)} 件）："
                    + "、".join(items),
                    draft,
                )
            return f"未找到匹配单品（共检索 {result.matched} 件）。可以换关键词或表达再搜。", draft
        if step.action == "modify_outfit":
            if step.plan is None or not step.plan.ops:
                return "modify_outfit 需要提供 plan（至少一个操作）", draft
            next_draft, issues = self.environment.modify_outfit(draft, step.plan)
            if issues:
                return "修改未通过物理校验：" + "；".join(issues), draft
            draft = next_draft
            return f"已应用修改，新搭配：{_outfit_text(draft.outfit)}", draft
        if step.action == "check_environment":
            result = self.environment.check_environment(draft)
            if result.valid:
                return "物理校验通过", draft
            return "物理校验未通过：" + "；".join(result.issues), draft
        return f"未知动作：{step.action}", draft

    # --- prompt ----------------------------------------------------------

    def _build_prompt(
        self,
        facts: EnvironmentFacts,
        draft: Draft,
        steps: list[dict[str, Any]],
        intent: UserIntent | None,
    ) -> tuple[str, str]:
        lines = [
            f"用户消息：{self.user_message}",
            f"正在编辑的活动穿搭：{_outfit_text(draft.outfit)}",
            f"衣橱摘要：{json.dumps(facts.wardrobe_summary, ensure_ascii=False)}",
        ]
        if facts.weather:
            lines.append(f"天气快照：{json.dumps(facts.weather, ensure_ascii=False)}")
        if facts.memory_profile:
            lines.append(
                f"记忆画像：{json.dumps(facts.memory_profile, ensure_ascii=False)}"
            )
        if facts.selected_item is not None:
            lines.append(
                f"用户点击定位的单品：{facts.selected_item.item_id}"
                f"({facts.selected_item.item_type}/{facts.selected_item.color})"
            )
        if intent is not None:
            lines.append(
                f"已确认目标：{intent.goal or '—'}；要求：{'；'.join(intent.requirements) or '—'}"
            )
        for step in steps:
            lines.append(f"第 {step['step']} 步（{step['action']}）：{step['observation']}")
        return SYSTEM_PROMPT, "\n".join(lines)

    # --- outcome ---------------------------------------------------------

    def _outcome(
        self,
        status: str,
        draft: Draft,
        intent: UserIntent | None,
        steps: list[dict[str, Any]],
        question: str | None,
        llm_calls: int,
        review: ReviewResult | None = None,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "intent": intent.model_dump(mode="json") if intent else {},
            "candidate": {
                "outfit_id": draft.outfit.outfit_id,
                "item_ids": draft.outfit.item_ids,
            },
            "review": review.model_dump(mode="json") if review else None,
            "ask_user": {"question": question} if question else None,
            "steps": steps,
            "llm_call_count": llm_calls,
        }
