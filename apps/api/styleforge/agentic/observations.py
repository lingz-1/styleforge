"""Tool observation formatters shared by the Agentic loop and the Harness.

Extracted verbatim from ``agent.py`` so the legacy loop and the new
CapabilityRegistry handlers normalise the same facts to the same bounded
observation strings. Facts stay facts — an unavailable search / skill /
weather result is formatted as a fact the Agent works around, never a crash.
"""

from __future__ import annotations

from styleforge.models.agentic_contract import OutfitSnapshot, SkillResult, WebSearchResult
from styleforge.tools.weather.schemas import WeatherFacts

MAX_OBSERVATION_CHARS = 2000


def outfit_text(outfit: OutfitSnapshot | None) -> str:
    if outfit is None:
        return "（无）"
    items = [
        f"{item.item_id}({item.item_type or '?'}/{item.color or '?'})"
        for item in outfit.items
    ]
    if not items:
        items = list(outfit.item_ids)
    return "；".join(items) or "（空）"


def web_search_observation(result: WebSearchResult) -> str:
    """Format a ``search_web`` result into one bounded observation line.

    An unconfigured / failed / empty search is a fact the Agent works around
    (switch to the wardrobe, ask the user, or decide on its own) — never an
    error the loop must crash on.
    """
    if not result.available or result.error:
        reason = result.error or "未配置"
        return f"联网搜索未可用：{reason}。可改用衣橱搜索、ask_user 或自主决定。"
    if not result.results:
        return "联网搜索结果为空。可改用衣橱搜索、ask_user 或自主决定。"
    lines = [f"{hit.title}：{hit.content}（{hit.url}）" for hit in result.results]
    text = "联网搜索结果（仅供知识参考）：\n" + "\n".join(lines)
    if result.answer:
        text = f"联网搜索摘要：{result.answer}\n" + text
    return text[:MAX_OBSERVATION_CHARS]


def skill_observation(result: SkillResult) -> str:
    """Format a ``load_skill`` result into one bounded observation line.

    A missing / unconfigured skill is a fact — the Agent falls back to its
    own judgement (cognitive-boundary + counterfactual test), never a crash.
    """
    if not result.available or result.error:
        reason = result.error or "未配置"
        return f"技能不可用：{reason}。请按认知边界原则自行判断是否需要搜索。"
    return (
        f"已加载技能「{result.name}」，请按其流程指导行动：\n{result.content}"
    )[: MAX_OBSERVATION_CHARS * 2]


def weather_observation(facts: WeatherFacts) -> str:
    """Format a ``get_weather`` result into one bounded observation line."""
    if facts.status != "available":
        reason = facts.error_message or "未配置"
        return f"天气查询未可用：{reason}。可按衣橱现有单品自主搭配。"
    location = (
        facts.resolved_location.display_name
        if facts.resolved_location
        else (facts.requested_location or "当地")
    )
    day_lines: list[str] = []
    for day in facts.days:
        temps = [
            str(value)
            for value in (day.temperature_min_c, day.temperature_max_c)
            if value is not None
        ]
        parts: list[str] = []
        if temps:
            parts.append("~".join(temps) + "°C")
        if day.condition:
            parts.append(day.condition)
        if day.precipitation_probability_percent is not None:
            parts.append(f"降水{day.precipitation_probability_percent}%")
        day_lines.append(f"{day.date}：{'；'.join(parts) or '（无数据）'}")
    text = f"天气（{location}）：\n" + "\n".join(day_lines)
    return text[: MAX_OBSERVATION_CHARS * 2]
