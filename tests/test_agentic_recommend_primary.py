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
from styleforge.agentic.graph.main import (
    _desired_features_for_request,
    _request_needs_outerwear,
)
from styleforge.orchestration.task_router import TaskType
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.task_run_repository import get_task_run
from styleforge.repositories.wardrobe_repository import add_items
from styleforge.tools.weather.schemas import ResolvedLocation, WeatherDay, WeatherFacts
from styleforge.tools.web_search import TavilySearchProvider
from styleforge.workflow.task_workflow import MultiTaskWorkflow

from tests.helpers import make_item
from tests.llm.fake_llm import FakeLlm


def test_recovery_intent_features_cover_quality_benchmark_constraints() -> None:
    commute = _desired_features_for_request("见客户，鞋要适合久站")
    rain = _desired_features_for_request("明天有雨而且降温，要防水防滑")
    sport = _desired_features_for_request("今晚打篮球，给我一套能直接上场的穿搭")

    assert {"formal", "comfortable"} <= commute
    assert {"waterproof", "non_slip", "warm"} <= rain
    assert {"sport", "breathable", "cushioned", "non_slip"} <= sport
    assert _request_needs_outerwear("明天有雨而且降温", rain) is True
    assert _request_needs_outerwear("普通室内通勤", commute) is False


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


def _add_wedding_items(database_path: str) -> None:
    items = [
        make_item(
            "dress-1",
            "dress",
            "Lace wedding guest dress",
            "navy",
            features=("wedding", "elegant"),
        ),
        make_item(
            "wedding-shoes",
            "shoes",
            "Formal wedding sandals",
            "gold",
            features=("wedding", "formal"),
        ),
        make_item(
            "accessory-1",
            "accessory",
            "Wedding hair accessory",
            "gold",
            features=("wedding",),
        ),
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

_CRITIC_SCORES: list[dict[str, int]] = [
    {
        "request_relevance": 9,
        "request_specificity": 9,
        "outfit_coordination": 8,
        "wearability": 8,
        "freshness": 7,
    },
    {
        "request_relevance": 6,
        "request_specificity": 6,
        "outfit_coordination": 7,
        "wearability": 9,
        "freshness": 9,
    },
    {
        "request_relevance": 8,
        "request_specificity": 8,
        "outfit_coordination": 9,
        "wearability": 7,
        "freshness": 8,
    },
]


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
    for tool_args, dimension_scores in zip(
        _CANDIDATE_TOOLS, _CRITIC_SCORES, strict=True
    ):
        script.extend(
            [
                (
                    {"decision_summary": "组合候选", "control": "CONTINUE"},
                    [{"name": "modify_outfit", "arguments": tool_args}],
                ),
                {"decision_summary": "完成", "control": "CANDIDATE_READY"},
                {**_APPROVE, "dimension_scores": dimension_scores},
            ]
        )
    script.append({"evidence": []})  # memory extraction (chat_json, outside proxy)
    return script


def test_simple_recommend_uses_authoritative_route_and_skips_coordinator(
    db_dsn: str,
) -> None:
    initialize_database(db_dsn)
    _seed(db_dsn)
    llm = FakeLlm(
        [
            (
                {"decision_summary": "组合一套日常搭配", "control": "CONTINUE"},
                [{"name": "modify_outfit", "arguments": _CANDIDATE_TOOLS[0]}],
            ),
            {"decision_summary": "完成", "control": "CANDIDATE_READY"},
            {**_APPROVE, "dimension_scores": _CRITIC_SCORES[0]},
            {"evidence": []},
        ]
    )
    workflow = _workflow(
        db_dsn,
        llm,
        web_provider=_web_provider(),
        weather_provider=_FakeWeatherProvider(),
    )

    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="从衣柜里推荐一套日常穿搭",
            requested_task_type=TaskType.OUTFIT_RECOMMEND,
            max_results=1,
        )
    )

    assert payload["status"] == "completed"
    assert payload["llm_call_count"] == 3
    assert len(payload["result"]["recommendations"]) == 1
    assert payload["agentic_outcome"]["task_type"] == "outfit_recommend"


def _seed_sparse_accessories(database_path: str) -> list[str]:
    items = [
        make_item("sparse-bag", "bag", "Black tote", "black"),
        make_item("sparse-ring", "rings", "Silver ring", "silver"),
    ]
    with database_session(database_path) as connection:
        upsert_items(connection, items, "test")
        add_items(connection, "u", [item.item_id for item in items])
    return [item.item_id for item in items]


def test_sparse_wardrobe_recommend_is_infeasible_without_agent_calls(
    db_dsn: str,
) -> None:
    initialize_database(db_dsn)
    _seed_sparse_accessories(db_dsn)
    workflow = _workflow(db_dsn, FakeLlm([]))

    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="只用衣柜推荐一套完整正式通勤搭配",
            requested_task_type=TaskType.OUTFIT_RECOMMEND,
            max_results=1,
        )
    )

    assert payload["status"] == "infeasible"
    assert payload["result"]["recommendations"] == []
    assert payload["llm_call_count"] == 0


def test_sparse_wardrobe_modify_is_infeasible_without_agent_calls(
    db_dsn: str,
) -> None:
    initialize_database(db_dsn)
    item_ids = _seed_sparse_accessories(db_dsn)
    workflow = _workflow(db_dsn, FakeLlm([]))

    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="把当前包和戒指改成完整正式通勤搭配，只用已有衣物",
            requested_task_type=TaskType.OUTFIT_MODIFY,
            current_item_ids=item_ids,
            max_results=1,
        )
    )

    assert payload["status"] == "infeasible"
    assert payload["result"]["alternatives"] == []
    assert payload["llm_call_count"] == 0


def test_coordinator_cannot_misroute_recommend_to_extension(db_dsn: str) -> None:
    initialize_database(db_dsn)
    _seed(db_dsn)
    llm = FakeLlm(
        [
            {
                "decision_summary": "错误地准备交给扩展任务",
                "goal": "生成婚礼穿搭",
                "next_agent": "EXTENSION",
            },
            (
                {"decision_summary": "组合婚礼穿搭", "control": "CONTINUE"},
                [{"name": "modify_outfit", "arguments": _CANDIDATE_TOOLS[0]}],
            ),
            {"decision_summary": "提交", "control": "CANDIDATE_READY"},
            {**_APPROVE, "dimension_scores": _CRITIC_SCORES[0]},
            {"evidence": []},
        ]
    )
    workflow = _workflow(
        db_dsn,
        llm,
        web_provider=_web_provider(),
        weather_provider=_FakeWeatherProvider(),
    )

    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="今天去北京参加婚礼，推荐一套穿搭",
            requested_task_type=TaskType.OUTFIT_RECOMMEND,
            max_results=1,
        )
    )

    assert payload["status"] == "completed"
    assert payload["llm_call_count"] == 4
    assert payload["agentic_outcome"]["handoff_result"].trace_summary["agent"] == "stylist"
    assert not payload["agentic_outcome"].get("extension_validation_failures")


def test_empty_stylist_protocol_failure_recovers_grounded_candidate(
    db_dsn: str,
) -> None:
    initialize_database(db_dsn)
    _seed(db_dsn)
    _add_wedding_items(db_dsn)
    llm = FakeLlm(
        [
            (
                {"decision_summary": "先加入上装", "control": "CONTINUE"},
                [
                    {
                        "name": "modify_outfit",
                        "arguments": _add_ops(("top-1", "upper_body")),
                    }
                ],
            ),
            *([{"control": "CONTINUE"}] * 12),
            {**_APPROVE, "dimension_scores": _CRITIC_SCORES[0]},
            {"evidence": []},
        ]
    )
    workflow = _workflow(db_dsn, llm)

    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="从衣柜里推荐一套婚礼宾客穿搭，要有连衣裙、鞋履和配饰",
            requested_task_type=TaskType.OUTFIT_RECOMMEND,
            max_results=1,
        )
    )

    assert payload["status"] == "completed"
    assert payload["llm_call_count"] == 14
    recommendation = payload["result"]["recommendations"][0]
    assert recommendation["acceptance_status"] == "DEGRADED_ACCEPTED"
    assert {"one_piece", "footwear", "accessory"} <= set(
        recommendation["slot_items"]
    )


def test_severe_critic_intent_mismatch_rebuilds_from_grounded_features(
    db_dsn: str,
) -> None:
    initialize_database(db_dsn)
    _seed(db_dsn)
    _add_wedding_items(db_dsn)
    severe_reject = {
        "approved": False,
        "issues": ["候选与婚礼请求不相关"],
        "feedback": "重新选择婚礼单品",
        "dimension_scores": {
            "request_relevance": 2,
            "request_specificity": 2,
            "outfit_coordination": 5,
            "wearability": 6,
            "freshness": 5,
        },
    }
    llm = FakeLlm(
        [
            (
                {"decision_summary": "组合了错误场景", "control": "CONTINUE"},
                [{"name": "modify_outfit", "arguments": _CANDIDATE_TOOLS[0]}],
            ),
            {"decision_summary": "提交", "control": "CANDIDATE_READY"},
            severe_reject,
            {**_APPROVE, "dimension_scores": _CRITIC_SCORES[0]},
            {"evidence": []},
        ]
    )
    workflow = _workflow(db_dsn, llm)

    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="推荐一套婚礼宾客穿搭，要有连衣裙、鞋履和配饰",
            requested_task_type=TaskType.OUTFIT_RECOMMEND,
            max_results=1,
        )
    )

    assert payload["status"] == "completed"
    assert payload["llm_call_count"] == 4
    recommendation = payload["result"]["recommendations"][0]
    assert {"dress-1", "wedding-shoes", "accessory-1"} <= set(
        recommendation["item_ids"]
    )
    assert recommendation["acceptance_status"] == "DEGRADED_ACCEPTED"


def test_explicit_feature_gate_recovers_comfortable_client_outfit(
    db_dsn: str,
) -> None:
    initialize_database(db_dsn)
    _seed(db_dsn)
    feature_items = [
        make_item(
            "formal-top",
            "top",
            "简约通勤衬衫",
            "white",
            features=("formal", "minimal"),
        ),
        make_item(
            "formal-bottom",
            "pants",
            "正式直筒西裤",
            "black",
            features=("formal", "minimal"),
        ),
        make_item(
            "standing-shoes",
            "shoes",
            "久站舒适乐福鞋",
            "black",
            features=("comfortable", "formal"),
        ),
    ]
    with database_session(db_dsn) as connection:
        upsert_items(connection, feature_items, "test")
        add_items(connection, "u", [item.item_id for item in feature_items])

    llm = FakeLlm(
        [
            (
                {"decision_summary": "先给普通方案", "control": "CONTINUE"},
                [{"name": "modify_outfit", "arguments": _CANDIDATE_TOOLS[0]}],
            ),
            {"decision_summary": "提交", "control": "CANDIDATE_READY"},
            {**_APPROVE, "dimension_scores": _CRITIC_SCORES[0]},
            {"evidence": []},
        ]
    )
    workflow = _workflow(
        db_dsn,
        llm,
        web_provider=_web_provider(),
        weather_provider=_FakeWeatherProvider(),
    )

    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="明天见客户，鞋要适合久站，搭一套专业通勤穿搭",
            requested_task_type=TaskType.OUTFIT_RECOMMEND,
            max_results=1,
        )
    )

    recommendation = payload["result"]["recommendations"][0]
    assert payload["status"] == "completed"
    assert "standing-shoes" in recommendation["item_ids"]
    assert "features=comfortable,formal" not in llm.calls[2]["system"]
    assert "features=comfortable,formal" in llm.calls[2]["user"]


def test_indoor_basketball_with_time_skips_unneeded_research(db_dsn: str) -> None:
    initialize_database(db_dsn)
    sport_items = [
        make_item("sport-top", "top", "透气篮球上衣", "black", features=("sport",)),
        make_item("sport-bottom", "shorts", "篮球短裤", "black", features=("sport",)),
        make_item(
            "sport-shoes",
            "shoes",
            "缓震篮球鞋",
            "black",
            features=("sport", "cushioned"),
        ),
    ]
    with database_session(db_dsn) as connection:
        upsert_items(connection, sport_items, "test")
        add_items(connection, "u", [item.item_id for item in sport_items])

    sport_plan = _add_ops(
        ("sport-top", "upper_body"),
        ("sport-bottom", "lower_body"),
        ("sport-shoes", "feet"),
    )
    llm = FakeLlm(
        [
            (
                {"decision_summary": "组合篮球穿搭", "control": "CONTINUE"},
                [{"name": "modify_outfit", "arguments": sport_plan}],
            ),
            {"decision_summary": "提交", "control": "CANDIDATE_READY"},
            {**_APPROVE, "dimension_scores": _CRITIC_SCORES[0]},
            {"evidence": []},
        ]
    )
    workflow = _workflow(
        db_dsn,
        llm,
        web_provider=_web_provider(),
        weather_provider=_FakeWeatherProvider(),
    )

    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="今晚打篮球，给我一套能直接上场的穿搭",
            requested_task_type=TaskType.OUTFIT_RECOMMEND,
            max_results=1,
        )
    )

    assert payload["status"] == "completed"
    assert payload["result"]["environment_context"] == {}
    assert payload["agentic_outcome"].get("research_evidence") is None
    assert payload["llm_call_count"] == 3


def test_unknown_latin_travel_target_clarifies_without_agent_calls(db_dsn: str) -> None:
    initialize_database(db_dsn)
    _seed(db_dsn)
    llm = FakeLlm([])
    workflow = _workflow(db_dsn, llm)

    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="下半年去 piacon 怎么穿搭？",
            requested_task_type=TaskType.OUTFIT_RECOMMEND,
            max_results=1,
        )
    )

    assert payload["status"] == "needs_clarification"
    assert payload["llm_call_count"] == 0
    assert llm.call_count == 0
    assert "piacon" in payload["result"]["clarification_question"]


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
    assert result["schema_version"] == "styleforge.outfit-recommend-result.v1"
    assert result["run_id"] == payload["run_id"]
    assert result["structured_result"]["run_id"] == payload["run_id"]
    recommendations = result["structured_result"]["recommendations"]
    assert result["recommendations"] == recommendations
    assert len(recommendations) == 3
    for recommendation in recommendations:
        assert recommendation["outfit_id"].startswith("rec-")
        assert len(recommendation["item_ids"]) >= 2
        assert recommendation["reasons"]
        assert recommendation["slot_items"]
        assert recommendation["hard_valid"] is True
        assert recommendation["score"] > 0
        assert recommendation["score_source"] == "critic"
        assert recommendation["llm_score"] == recommendation["score"]
        assert set(recommendation["dimension_scores"]) == {
            "request_relevance",
            "request_specificity",
            "outfit_coordination",
            "wearability",
            "freshness",
        }
    assert [item["score"] for item in recommendations] == sorted(
        [item["score"] for item in recommendations], reverse=True
    )
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
        stored = get_task_run(
            connection,
            user_id=payload["user_id"],
            run_id=payload["run_id"],
        )
    assert [r["outfit_id"] for r in rows] == [
        r["outfit_id"] for r in recommendations
    ]
    assert stored is not None
    assert stored["result"]["schema_version"] == result["schema_version"]
    assert stored["result"]["structured_result"] == result["structured_result"]


def test_harness_score_respects_user_evaluation_weights() -> None:
    candidate = {
        "status": "STAGED",
        "review": {
            "dimension_scores": {
                "request_relevance": 10,
                "request_specificity": 5,
                "outfit_coordination": 5,
                "wearability": 1,
                "freshness": 5,
            }
        },
    }

    relevance_first = MultiTaskWorkflow._score_harness_candidate(
        candidate,
        {
            "request_relevance": 0.8,
            "request_specificity": 0.05,
            "outfit_coordination": 0.05,
            "wearability": 0.05,
            "freshness": 0.05,
        },
    )
    wearability_first = MultiTaskWorkflow._score_harness_candidate(
        candidate,
        {
            "request_relevance": 0.05,
            "request_specificity": 0.05,
            "outfit_coordination": 0.05,
            "wearability": 0.8,
            "freshness": 0.05,
        },
    )

    assert relevance_first["score"] > wearability_first["score"]
    assert relevance_first["score_source"] == "critic"


def test_harness_score_uses_explicit_neutral_fallback_for_v1_review() -> None:
    score = MultiTaskWorkflow._score_harness_candidate(
        {"status": "STAGED", "review": {"approved": True}},
        None,
    )

    assert score["score"] == 50.0
    assert score["llm_score"] is None
    assert score["score_source"] == "neutral_fallback"


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
    result = payload["result"]
    assert result["schema_version"] == "styleforge.outfit-recommend-result.v1"
    assert result["run_id"] == payload["run_id"]
    assert result["structured_result"]["run_id"] == payload["run_id"]
    assert result["structured_result"]["status"] == payload["status"]
    assert result["structured_result"]["recommendations"] == result["recommendations"]
    assert "agentic_outcome" not in payload
