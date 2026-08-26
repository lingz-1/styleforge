"""OUTFIT_RECOMMEND through the agentic primary chain (Stage 4b / H2c).

One ``StyleForgeHarness.invoke`` produces all three candidates from the empty
base draft: Coordinator → Research (load_skill / search_web / get_weather run
ONCE) → Evidence Synthesizer → Coordinator → Stylist ×3 sharing the evidence →
Main-Graph gates → StageCandidate. The single outcome rides ``agentic_outcome``
so the front end can show "联网搜索查到 xx → 考虑主题 → 搭配 xx", and the
``get_weather`` fact the Research Agent actually saw rides
``environment_context``. A request without an LLM degrades to the deterministic
recommendation pipeline (``llm_enabled=false``), keeping no-key behaviour
unchanged.

The harness is driven with a scripted ``FakeLlm``; web / weather / skill tools
use fakes. Memory extraction (``extract_language_evidence``) runs outside the
harness proxy and is not counted in ``llm_call_count``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.request import Request


from styleforge.models.task import TaskExecutionInput
from styleforge.orchestration.task_router import TaskType
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.wardrobe_repository import add_items
from styleforge.tools.weather.schemas import ResolvedLocation, WeatherDay, WeatherFacts
from styleforge.tools.web_search import TavilySearchProvider
from styleforge.workflow.task_workflow import MultiTaskWorkflow

from tests.helpers import make_item
from tests.llm.fake_llm import FakeLlm


def _seed(database_path: str) -> None:
    items = [
        make_item("top-1", "top", "白衬衫", "white"),
        make_item("top-2", "top", "黑色毛衣", "black"),
        make_item("bottom-1", "pants", "黑色西裤", "black"),
        make_item("bottom-2", "jeans", "蓝色牛仔裤", "blue"),
        make_item("skirt-1", "skirt", "黑色半身裙", "black"),
        make_item("shoes-1", "shoes", "黑色皮鞋", "black"),
        make_item("shoes-2", "shoes", "白色运动鞋", "white"),
        make_item("coat-1", "outwear", "灰色大衣", "gray"),
    ]
    with database_session(database_path) as connection:
        upsert_items(connection, items, "test")
        add_items(connection, "u", [item.item_id for item in items])


def _workflow(
    database_path: str,
    llm: Any | None,
    *,
    web_provider: Any = None,
    weather_provider: Any = None,
    skills_root: Path | None = None,
) -> MultiTaskWorkflow:
    return MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=llm,
        web_search_provider=web_provider,
        weather_provider=weather_provider,
        skills_root=skills_root,
    )


# ── Harness script helpers ────────────────────────────────────────────

_GOAL = "北京莫里哀音乐剧正式穿搭"

# Research: one load_skill + one search_web + one get_weather, then done. The
# tools run once, shared by all three candidates (frozen H2c requirement).
_RESEARCH_TOOLS = [
    {"name": "load_skill", "arguments": {"skill_name": "event_outfit_planning"}},
    {"name": "search_web", "arguments": {"query": "莫里哀音乐剧 北京 演出"}},
    {"name": "get_weather", "arguments": {"location": "北京"}},
]

# The Evidence Synthesizer's structured output (chat_json, ResearchEvidence).
_EVIDENCE: dict[str, Any] = {
    "event": {"name": "莫里哀音乐剧", "description": "北京演出"},
    "weather": {"location": "北京", "temperature_c": "26", "condition": "晴"},
    "dress_context": ["音乐剧建议正式着装，避免运动鞋"],
    "practical_requirements": ["正式一些"],
    "theme_elements": ["庄重正式"],
    "sources": [
        {
            "kind": "web",
            "title": "剧场观演着装",
            "url": "https://example.com/theatre",
            "snippet": "音乐剧建议正式着装，避免运动鞋",
        }
    ],
    "uncertainties": ["未找到官方着装要求"],
}


def _add_ops(*items: tuple[str, str]) -> dict[str, Any]:
    """One modify_outfit plan adding each (item_id, region) at the base layer."""
    return {
        "plan": {
            "ops": [
                {
                    "action": "add",
                    "item_id": item_id,
                    "placement": {"region": region, "layer": "base"},
                }
                for item_id, region in items
            ],
            "reasoning": "组合完整搭配",
        }
    }


_APPROVE = {"approved": True, "issues": [], "feedback": "方案符合音乐剧正式场合要求"}


class _FakeWeatherProvider:
    """WeatherProvider stand-in: every named location resolves with one day."""

    def resolve_location(self, query: str):
        return ResolvedLocation(
            name=query, country="CN", latitude=39.9, longitude=116.4
        )

    def forecast_range(
        self,
        location: ResolvedLocation,
        start_date: Any,
        end_date: Any,
        *,
        granularity: str = "daily",
        period: str | None = None,
    ) -> WeatherFacts:
        day = WeatherDay(
            date=str(start_date),
            temperature_min_c=18,
            temperature_max_c=26,
            condition="晴",
        )
        return WeatherFacts(
            status="available",
            requested_location=location.display_name,
            resolved_location=location,
            start_date=str(start_date),
            end_date=str(end_date),
            days=[day],
        )


def _web_provider() -> TavilySearchProvider:
    def _transport(request: Request, timeout: float) -> dict[str, Any]:
        return {
            "results": [
                {
                    "title": "莫里哀音乐剧 北京 演出",
                    "content": "莫里哀音乐剧下半年在北京演出，主题庄重正式",
                    "url": "https://example.com/moliere",
                },
                {
                    "title": "剧场观演着装",
                    "content": "音乐剧建议正式着装，避免运动鞋",
                    "url": "https://example.com/theatre",
                },
            ]
        }

    return TavilySearchProvider(api_key="tvly-test", transport=_transport)


def _skills_root(tmp_path: Path) -> Path:
    skill_dir = tmp_path / "tasks" / "event_outfit_planning"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# Event Outfit Planning\n1. 识别活动实体\n2. search_web 查事实\n3. 必要时 get_weather\n",
        encoding="utf-8",
    )
    return tmp_path


def _recommend_task() -> TaskExecutionInput:
    return TaskExecutionInput(
        user_id="u",
        request="下周去北京看莫里哀音乐剧穿什么",
        current_outfit_id="",
        current_item_ids=[],
    )


# Three distinct candidate outfits, each built by one modify_outfit plan.
_CANDIDATE_TOOLS: list[dict[str, Any]] = [
    _add_ops(("top-1", "upper_body"), ("bottom-1", "lower_body"), ("shoes-1", "feet")),
    _add_ops(("top-2", "upper_body"), ("bottom-2", "lower_body"), ("shoes-2", "feet")),
    _add_ops(("top-1", "upper_body"), ("skirt-1", "lower_body"), ("shoes-1", "feet")),
]


def _build_script() -> list[Any]:
    # One full harness run: coordinator → research (skill/web/weather once) →
    # evidence synthesizer → coordinator → stylist ×3 (candidate → critic each).
    script: list[Any] = [
        {"decision_summary": "先研究场合", "goal": _GOAL, "next_agent": "RESEARCH"},
        (
            {"decision_summary": "加载技能", "control": "CONTINUE"},
            [_RESEARCH_TOOLS[0]],
        ),
        (
            {"decision_summary": "查演出信息", "control": "CONTINUE"},
            [_RESEARCH_TOOLS[1]],
        ),
        (
            {"decision_summary": "查天气", "control": "CONTINUE"},
            [_RESEARCH_TOOLS[2]],
        ),
        {"decision_summary": "信息充足", "control": "RESEARCH_COMPLETE"},
        _EVIDENCE,  # Evidence Synthesizer (chat_json)
        {"decision_summary": "开始搭配", "goal": _GOAL, "next_agent": "STYLIST"},
    ]
    for tool_args in _CANDIDATE_TOOLS:
        script.extend(
            [
                (
                    {"decision_summary": "组合候选", "control": "CONTINUE"},
                    [{"name": "modify_outfit", "arguments": tool_args}],
                ),
                {"decision_summary": "完成", "control": "CANDIDATE_READY"},
                dict(_APPROVE),  # critic (chat_json)
            ]
        )
    script.append({"evidence": []})  # memory extraction (chat_json, outside proxy)
    return script


def test_recommend_agentic_primary_returns_three_diverse_outfits(
    db_dsn: str, tmp_path: Path
) -> None:
    initialize_database(db_dsn)
    _seed(db_dsn)
    llm = FakeLlm(_build_script())
    workflow = _workflow(
        db_dsn,
        llm,
        web_provider=_web_provider(),
        weather_provider=_FakeWeatherProvider(),
        skills_root=_skills_root(tmp_path),
    )

    payload = workflow.execute(_recommend_task())

    assert payload["task_type"] == TaskType.OUTFIT_RECOMMEND.value
    assert payload["selected_subgraph"] == "agentic_harness"
    assert payload["status"] == "completed"
    # 2 coordinator + 4 research + 1 synthesizer + 6 stylist + 3 critic = 16.
    assert payload["llm_call_count"] == 16
    result = payload["result"]
    assert result["status"] == "completed"
    recommendations = result["structured_result"]["recommendations"]
    assert len(recommendations) == 3
    for recommendation in recommendations:
        assert recommendation["outfit_id"].startswith("rec-")
        assert len(recommendation["item_ids"]) >= 2
        assert recommendation["reasons"]
        assert recommendation["slot_items"]
    # The three candidates are genuinely different item sets.
    item_sets = {frozenset(r["item_ids"]) for r in recommendations}
    assert len(item_sets) == 3

    # A single outcome rides agentic_outcome: the research evidence the Stylist
    # grounded on plus the three staged candidates for the front end.
    outcome = payload["agentic_outcome"]
    assert isinstance(outcome, dict)
    assert outcome["status"] == "done"
    assert len(outcome["candidates"]) == 3
    evidence = outcome["research_evidence"]
    assert evidence is not None
    assert any("音乐剧" in item for item in evidence.dress_context)
    assert evidence.theme_elements == ["庄重正式"]

    # The weather the Research Agent actually saw surfaces in environment_context.
    assert result["environment_context"]["weather"]["status"] == "available"
    assert result["environment_context"]["weather"]["days"][0]["condition"] == "晴"

    # Every completed recommendation is persisted for multi-turn re-anchoring.
    with database_session(db_dsn) as connection:
        rows = connection.execute(
            "SELECT outfit_id FROM candidate_outfits ORDER BY rank"
        ).fetchall()
    assert [r["outfit_id"] for r in rows] == [
        r["outfit_id"] for r in recommendations
    ]


def test_recommend_without_llm_uses_deterministic_pipeline(db_dsn: str) -> None:
    initialize_database(db_dsn)
    _seed(db_dsn)
    workflow = _workflow(db_dsn, None)  # no LLM key

    payload = workflow.execute(_recommend_task())

    # The agentic branch is gated on llm_client; the deterministic pipeline
    # (parse_request → recommend_for_user → present_result) takes over.
    assert payload["selected_subgraph"] == "outfit_recommend_subgraph"
    assert payload["status"] in {"completed", "infeasible"}
    assert payload["llm_enabled"] is False
    assert payload["llm_call_count"] == 0
    assert "recommendations" in payload["result"]
    assert "agentic_outcome" not in payload
