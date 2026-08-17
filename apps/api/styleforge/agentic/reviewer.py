"""Semantic Reviewer gate (``review_outfit``) for the Agentic loop.

A separate LLM gate that judges what ``check_environment`` must never touch:
intent fidelity (did the result violate the user's explicit asks?), completion
(did it really meet the goal?) and quality. On FAIL the feedback returns to
the Agent as an observation for replanning — never a real commit.
"""

from __future__ import annotations

from typing import Any

from styleforge.models.agentic_contract import (
    InteractionContext,
    OutfitSnapshot,
    ReviewResult,
    UserIntent,
)

_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "feedback": {"type": "string"},
    },
    "required": ["approved", "issues", "feedback"],
    "additionalProperties": False,
}

_REVIEW_SYSTEM = (
    "你是穿搭审校员，判断一次修改后的穿搭是否合格。规则：\n"
    "1. 忠实用户原意：不得违背用户的明确要求（如「上衣别动」「鞋要深色」）。"
    "但如果用户原话授权了自主决定（如「没有的话你看着办」「你建议」「都行」），"
    "Agent 在此授权范围内做的合理替代选择不算擅自放宽。\n"
    "2. 真正完成目标：修改确实朝用户要的方向去了，而不是答非所问。\n"
    "3. 搭配质量合理：没有明显的结构荒谬（如两件上衣叠穿冲突）。\n"
    "你只做语义判断，不判断数据库或物理结构是否合法（那是程序的事）。\n"
    "只输出 JSON：{\"approved\": bool, \"issues\": [\"...\"], \"feedback\": \"给修改 Agent 的重做建议\"}。"
)


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


def review_outfit(
    llm: Any,
    *,
    user_message: str,
    interaction: InteractionContext,
    before: OutfitSnapshot | None,
    after: OutfitSnapshot | None,
    intent: UserIntent,
) -> ReviewResult:
    """One LLM call judging the candidate against the user's original intent."""
    user = "\n".join(
        [
            f"用户原话：{user_message}",
            f"用户意图：{intent.goal or '（未给出）'}",
            f"明确要求：{'；'.join(intent.requirements) or '（无）'}",
            f"交互定位：active_outfit={interaction.active_outfit_id or '无'}，"
            f"selected_item={interaction.selected_item_id or '无'}",
            f"修改前：{_outfit_text(before)}",
            f"修改后：{_outfit_text(after)}",
        ]
    )
    payload, _ = llm.chat_json(
        system=_REVIEW_SYSTEM,
        user=user,
        json_schema=_REVIEW_SCHEMA,
    )
    return ReviewResult.model_validate(payload)
