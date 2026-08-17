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

from pydantic import BaseModel, Field, ValidationError, model_validator

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

# Physical-position aliases a real model may emit instead of the enum values.
# The program translates natural-language positions to the ontology facts
# deterministically; anything not listed still fails schema validation and the
# model retries. This is a fixed translation table, not a per-case patch.
_REGION_ALIASES = {
    "upper": "upper_body",
    "torso": "upper_body",
    "chest": "upper_body",
    "lower": "lower_body",
    "legs": "lower_body",
    "leg": "lower_body",
    "waist": "lower_body",
    "whole_body": "full_body",
    "whole": "full_body",
    "foot": "feet",
    "feet": "feet",
    "head": "accessory",
    "accessory": "accessory",
    # A model often writes the *item type* where the ontology wants the body
    # region ("shoes" instead of "feet"). These are deterministic translations
    # of type words to the region they occupy.
    "top": "upper_body",
    "shirt": "upper_body",
    "blouse": "upper_body",
    "sweater": "upper_body",
    "knitwear": "upper_body",
    "outwear": "upper_body",
    "jacket": "upper_body",
    "coat": "upper_body",
    "blazer": "upper_body",
    "pants": "lower_body",
    "trousers": "lower_body",
    "shorts": "lower_body",
    "skirt": "lower_body",
    "jeans": "lower_body",
    "dress": "full_body",
    "jumpsuit": "full_body",
    "suit": "full_body",
    "shoes": "feet",
    "boots": "feet",
    "sneakers": "feet",
    "heels": "feet",
    "bag": "accessory",
    "hat": "accessory",
    "necklace": "accessory",
}
_LAYER_ALIASES = {
    "innermost": "base",
    "inner": "base",
    "bottom": "base",
    "mid_layer": "mid",
    "mid-layer": "mid",
    "middle": "mid",
    "outerwear": "outer",
    "top": "outer",
    "over": "outer",
}


SYSTEM_PROMPT = """\
你是穿搭修改 Agent。你的职责是理解用户想怎么穿，然后通过工具修改一套穿搭。

环境已经告诉你：
- 当前正在编辑的活动穿搭（active outfit）
- 衣橱摘要（按单品类型统计，含颜色样例）和衣橱单品 id 清单
- 天气快照（若有）
- 用户记忆画像（若有）

你可以使用的工具：
- inspect_outfit {outfit_id}：查看某套穿搭的详情。不填或填 active 表示查看当前正在编辑的搭配
- search_wardrobe {query}：在用户衣橱中搜索单品。结果有数量上限；空结果不代表衣柜里没有，可以换一个表达再搜。
  注意：衣橱单品名称/描述是英文（如 sneakers、denim jacket、jeans），中文关键词通常搜不到。
  搜索时应同时尝试中英文表达（先中文直觉词，若未找到就换英文词或中英混合再搜）
- modify_outfit {plan}：修改当前穿搭。add/remove/replace 三种操作，可以一次提交多个。
  每步 placement 里 region 是身体部位（upper_body/lower_body/feet/full_body/accessory），
  layer 是层（base/mid/outer）；拿不准的字段可以留空，程序会按单品结构自动补全。
  程序会做物理校验，非法修改会被拒绝并返回原因
- check_environment：检查当前穿搭的物理合法性（可选；最终提交时程序也会强制检查）
- ask_user {question}：需要用户澄清时使用（例如用户要求明确，但衣柜里确实没有满足条件的单品）
- finish：修改已满足用户目标时使用。提交后程序会自动做物理与风格审核；审核未通过会返回
  原因，你再针对性调整后重新 finish 即可（不是一次性判死，不必反复试探、过度修改）

操作纪律：
1. 绝不编造单品 id。modify_outfit 里出现的 id 必须来自环境提供的衣橱 id 清单或
   search_wardrobe 的结果；不确定时先 search 再修改。
2. 一次决定，做完即止。选定一个合理的替代后直接完成，不要在同一件单品上反复更换
   不同候选（如 运动鞋↔皮鞋 来回换）。
3. 没有明确偏好时自主选择：优先选与已有单品颜色/风格协调的，不要为无把握的选择
   反复试探。用户说「你看着办」「没有的话你看着办」等于授权你自主决定，这种情况
   不需要 ask_user。
4. 每次 modify 前先想清楚这一轮要达成什么；观察上一轮失败的原因后调整，而不是
   原样重试。
5. 满足即止：只要修改已满足用户目标（例如要求加一件上衣，你已经加了一件），就
   直接 finish 提交，不要继续添加或替换无关单品。
6. 搜索语言：衣橱数据是英文（sneakers/denim/jeans/casual…），中文关键词基本搜不到。
   优先用英文词搜索；拿不准时先用中文直觉词，看到「未找到」提示后再换英文（或中英文
   混合）重试，不要连续换多个中文词空转。
7. 模糊评价先澄清：用户只说「不好看」「不合适」「怪」之类而没有指明哪里不满意或想
   怎么改时，先 inspect 当前穿搭；若仍无法推断具体修改方向，用 ask_user 问清楚
   （例如“具体哪里不满意？想换成什么风格？”），不要反复搜索或凭感觉瞎改。
8. 不要反复查看同一套而不行动：inspect_outfit 看过一次就记住内容；连续 2 次以上只
   查看或搜索而没有任何 modify_outfit / finish，属于空转，应立即决定行动（修改、
   finish 或 ask_user）。

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
    """One decision of the Agent: think, then act.

    ``query / outfit_id / question`` accept None: a real JSON-mode model fills
    the fields it is not using with ``null`` rather than omitting them. The
    dispatcher normalises None to "" at the tool boundary.
    """

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
    query: str | None = None
    outfit_id: str | None = None
    plan: ModifyPlan | None = None
    question: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalise_placements(cls, data: Any) -> Any:
        """Map natural-language region/layer words to the ontology enums.

        A JSON-mode model is not constrained to the enum values; it may say
        ``legs`` where the ontology says ``lower_body``. The program translates
        fixed aliases deterministically before pydantic validates, so a plan
        that means something physical is never rejected for phrasing. Unknown
        words are left untouched and still fail validation.
        """
        if not isinstance(data, dict):
            return data
        ops = (data.get("plan") or {}).get("ops") if isinstance(data.get("plan"), dict) else None
        if not ops:
            return data
        for op in ops:
            if not isinstance(op, dict):
                continue
            placement = op.get("placement")
            if not isinstance(placement, dict):
                continue
            region = placement.get("region")
            if isinstance(region, str):
                if region == "":
                    placement.pop("region", None)
                elif region in _REGION_ALIASES:
                    placement["region"] = _REGION_ALIASES[region]
            layer = placement.get("layer")
            if isinstance(layer, str):
                if layer == "":
                    placement.pop("layer", None)
                elif layer in _LAYER_ALIASES:
                    placement["layer"] = _LAYER_ALIASES[layer]
                elif layer in _REGION_ALIASES:
                    # A real model writes layer="shoes" (an item type / body
                    # word) where the layer belongs. The layer is a decision:
                    # when absent, the environment derives it from the
                    # garment's effective layer. Drop it and let the structure
                    # fill it.
                    placement.pop("layer", None)
        return data


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
        return {"query": step.query or ""}
    if step.action == "inspect_outfit":
        return {"outfit_id": step.outfit_id or ""}
    if step.action == "modify_outfit":
        return {"plan": step.plan.model_dump(mode="json") if step.plan else {}}
    if step.action == "ask_user":
        return {"question": step.question or ""}
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
        # Stall guard: the same tool with the same args N times in a row with no
        # state change is a loop a real model can fall into (C4 re-inspected
        # "active" 8×). The program flags it so the model changes strategy.
        recent_keys: list[str] = []
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
            key = json.dumps(
                {"a": step.action, "g": _args_for(step)},
                sort_keys=True,
                ensure_ascii=False,
            )
            recent_keys.append(key)
            if len(recent_keys) >= 3 and all(k == key for k in recent_keys[-3:]):
                observation += (
                    "。提示：你已连续 3 步执行相同操作且状态未改变，"
                    "请换一种策略（无 active 时用 ask_user 询问用户，"
                    "或目标已达成时直接 finish 提交）。"
                )
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
            outfit_id = (step.outfit_id or "").strip()
            if outfit_id in ("", "active"):
                # "inspect the outfit I am editing" — the draft, not a stored id.
                return f"当前正在编辑的搭配：{_outfit_text(draft.outfit)}", draft
            snapshot = self.environment.inspect_outfit(outfit_id)
            if snapshot is None:
                return f"未找到搭配 {outfit_id}", draft
            return f"搭配 {snapshot.outfit_id}：{_outfit_text(snapshot)}", draft
        if step.action == "search_wardrobe":
            result = self.environment.search_wardrobe(step.query or "")
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
            return (
                f"未找到匹配单品（共检索 {result.matched} 件）。"
                "衣橱单品名称/描述为英文，建议改英文关键词再试（如 sneakers、jeans）。",
                draft,
            )
        if step.action == "modify_outfit":
            if step.plan is None or not step.plan.ops:
                return "modify_outfit 需要提供 plan（至少一个操作）", draft
            next_draft, issues = self.environment.modify_outfit(draft, step.plan)
            if issues:
                return "修改未通过物理校验：" + "；".join(issues), draft
            draft = next_draft
            return (
                f"已应用修改，新搭配：{_outfit_text(draft.outfit)}。"
                "若此修改已满足用户目标，请直接 finish 提交，不要在已改好的单品上继续更换。",
                draft,
            )
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
        # The Agent must only reference ids that exist. Give it the concrete
        # list — guessing (outwear-2, shoes-2) instead of searching was the
        # failure a real model produced; the id list removes the need to guess.
        item_ids = sorted(item.item_id for item in self.environment.wardrobe_items)
        if len(item_ids) > 60:
            lines.append(
                f"衣橱单品 id（前 60 个，共 {len(item_ids)} 个）："
                + "、".join(item_ids[:60])
            )
        else:
            lines.append(f"衣橱单品 id：{'、'.join(item_ids) or '（空）'}")
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
        if not draft.outfit.item_ids:
            lines.append(
                "注意：当前没有正在编辑的搭配（活动穿搭为空）。"
                "若要修改已有的某套搭配，必须用 ask_user 向用户确认是哪一套；"
                "不要反复查看空搭配。"
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
