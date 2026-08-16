"""Prompt contract for the independent offline judge (pure functions).

The judge is deliberately separated from the runtime critic (Agent 3): it only
scores an outfit against a user request using the shared five-dimension rubric,
and its prompt never carries the critic's reasoning-check / decision vocabulary
(``request_signature``, ``阶段二``, ``决策枚举``, ``recompose``, ...). The unit
test asserts that separation so the two evals cannot silently merge.
"""

from __future__ import annotations

from styleforge.core.rubric import rubric_text

JUDGE_PROMPT_VERSION = "2026.08.14-poutfit-judge-v1"

# A standalone scorer. Unlike _AGENT3_SYSTEM (llm/prompts.py) there is no
# two-phase review, no explanation grounding pass, and no decision enum -- the
# judge's only output is the five dimension scores plus a short justification.
_JUDGE_SYSTEM = (
    "你是一位客观独立的穿搭质量评审员，负责为一套穿搭方案打分。\n"
    "系统将提供：1) 用户的穿搭需求；2) 该穿搭包含的单品文本信息（每行一件，格式：品类 | 名称 | 描述）。\n"
    "打分规则：\n"
    "1. 严格只依据上述需求文本与单品文本本身打分，不要臆测文本之外的颜色、材质、细节或图片信息。\n"
    "2. 信息不足时，基于需求与单品文本中明确表达的内容如实打分，并在 reasoning 中说明信息缺口，不要编造。\n"
    "3. 每个维度给出 1-10 的整数分。\n"
    "输出必须是合法 JSON（仅输出 JSON，不要任何解释文字）。\n"
)


def build_judge_prompt(
    *,
    user_request: str,
    item_texts: list[str],
    weights: dict[str, float] | None = None,
) -> tuple[str, str]:
    """Build the judge's ``(system, user)`` prompt pair.

    The prompt is a strict subset of information: the user request, the item
    text blocks, and the shared rubric. Nothing about the runtime context,
    request signature, or decision routing leaks in.
    """
    system = _JUDGE_SYSTEM + "\n" + rubric_text(weights)
    lines = [
        "用户穿搭需求：",
        user_request.strip(),
        "",
        "待评价穿搭的单品（一行一件）：",
        *[f"- {text.strip()}" for text in item_texts if text.strip()],
        "",
        "请按以下 JSON 结构输出（dimension_scores 每维度 1-10 整数）：",
        (
            '{"outfit_id": "…", "dimension_scores": '
            '{"request_relevance": 1, "request_specificity": 1, '
            '"outfit_coordination": 1, "wearability": 1, "freshness": 1}, '
            '"reasoning": "简短理由"}'
        ),
    ]
    return system, "\n".join(lines)
