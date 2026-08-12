"""Prompt template for LLM-based long-term memory extraction."""

from __future__ import annotations

MEMORY_PROMPT_VERSION = "memory-extract-v1.0"

CATEGORY_GUIDE = (
    "- category: 品类/单品偏好（如 衬衫、连衣裙、外套）\n"
    "- color: 颜色偏好（如 黑色、米白；否定用「避免黑色」）\n"
    "- style: 风格偏好（如 复古、极简、街头）\n"
    "- formality: 正式度偏好（如 正式、休闲、商务）\n"
    "- occasion: 常去场合（如 通勤、约会、运动）\n"
    "- habit: 穿衣习惯（如 每天穿衬衫、总是叠穿）\n"
    "- general: 其他长期偏好\n"
)

SYSTEM_PROMPT = (
    "你是 StyleForge 的记忆提炼器。从用户的一段造型请求中提炼**长期偏好**：\n"
    "只保留用户明确表达或反复出现、跨对话仍值得记住的内容；\n"
    "**忽略一次性场景**（如「明天面试穿什么」中只与本次相关的「面试」不算长期场合偏好，"
    "但「我通勤都穿衬衫」里的「通勤」「衬衫」算）。\n"
    "category 只能是以下之一：\n"
    f"{CATEGORY_GUIDE}"
    "content 用简洁中文（不超过 64 字）。否定偏好把 content 写成「避免」+ 词，"
    "并在 meta 里加 {\"polarity\": \"negative\"}。meta 是附加键值（如 occasion、style_tag）。\n"
    "输出 JSON：{\"memories\": [{\"category\": ..., \"content\": ..., \"meta\": {...}}]}。"
    "没有长期偏好时返回 {\"memories\": []}。"
)

FEW_SHOT_EXAMPLES = """\
示例输入：「黑色衬衫 + 通勤正式」
示例输出：{"memories": [
  {"category": "color", "content": "黑色", "meta": {"polarity": "positive"}},
  {"category": "category", "content": "衬衫", "meta": {}},
  {"category": "occasion", "content": "通勤", "meta": {}},
  {"category": "formality", "content": "正式", "meta": {}}
]}

示例输入：「不要黑色」
示例输出：{"memories": [
  {"category": "color", "content": "避免黑色", "meta": {"polarity": "negative"}}
]}

示例输入：「明天面试穿什么？」
示例输出：{"memories": []}
"""


def build_memory_extraction_prompt(request: str) -> tuple[str, str]:
    """Return ``(system, user)`` for one memory-extraction LLM call."""
    user = (
        f"{FEW_SHOT_EXAMPLES}\n"
        "---\n"
        f"现在提炼下面这段请求的长期偏好：\n\n{request.strip()}"
    )
    return SYSTEM_PROMPT, user
