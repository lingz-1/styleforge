"""End-to-end workflow tests driven by a scripted fake LLM (no network, no GPU)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from tests.helpers import make_item
from tests.llm.fake_llm import FakeLlm

from styleforge.core.config import Settings
from styleforge.core.schemas import CatalogItem
from styleforge.repositories.database import database_session, initialize_database
from styleforge.workflow.graph import StyleForgeWorkflow
from styleforge.tools.weather import ResolvedLocation, WeatherFacts


class _RecordingWeatherTool:
    """Fake tool that records inputs and returns a fixed available fact."""

    def __init__(self) -> None:
        self.inputs = []

    def __call__(self, tool_input):
        self.inputs.append(tool_input)
        name = tool_input.location or tool_input.display_name or "设备定位"
        return WeatherFacts(
            status="available",
            requested_location=name,
            requested_date=tool_input.date,
            resolved_location=ResolvedLocation(
                name=name,
                country="中国",
                admin1="上海市",
                latitude=tool_input.latitude or 31.23,
                longitude=tool_input.longitude or 121.47,
                timezone="Asia/Shanghai",
                source="device" if tool_input.latitude is not None else "resolved",
            ),
            forecast_date="2026-08-11",
            temperature_min_c=25,
            temperature_max_c=32,
            feels_like_c=31,
            precipitation_probability_percent=70,
            humidity_percent=82,
            wind_speed_kmh=18,
            weather_code=61,
            condition="小雨",
        )

USER = "u"
REQUEST = "明天参加互联网公司的面试，希望正式但不要太老气，不穿红色。"

WARDROBE_ITEMS = [
    make_item("top-1", "top", name="White formal shirt", color="White"),
    make_item("top-2", "top", name="Black blouse", color="Black"),
    make_item("top-3", "top", name="Navy knit top", color="Navy"),
    make_item("bottom-1", "pants", name="Navy trousers", color="Navy"),
    make_item("bottom-2", "pants", name="Gray pants", color="Gray"),
    make_item("shoe-1", "shoes", name="Black loafers", color="Black"),
    make_item("shoe-2", "shoes", name="Brown oxfords", color="Brown"),
]


def _catalog_row(item: CatalogItem) -> tuple:
    return (
        item.item_id,
        item.source,
        item.gender,
        item.item_type,
        item.main_category,
        item.name,
        item.color,
        item.description,
        json.dumps(list(item.features)),
        item.image_filename,
        item.relative_image_path,
        item.image_status.value,
        item.embedding_status.value,
        item.raw_json_hash,
        "test",
        "2026-01-01T00:00:00+00:00",
    )


def _seed_database(db_path: Path) -> None:
    initialize_database(db_path)
    with database_session(db_path) as connection:
        for item in WARDROBE_ITEMS:
            connection.execute(
                "INSERT OR REPLACE INTO catalog_items VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                _catalog_row(item),
            )
            connection.execute(
                "INSERT OR REPLACE INTO wardrobe_items "
                "(user_id, item_id, active, favorite, notes, added_at) "
                "VALUES (?, ?, 1, 0, '', ?)",
                (USER, item.item_id, "2026-01-01T00:00:00+00:00"),
            )


def _settings(tmp_path: Path, *, llm_enabled: bool = False) -> Settings:
    return Settings(
        metadata_path=tmp_path / "meta.json",
        outfit_path=tmp_path / "outfit.json",
        image_root=None,
        database_path=tmp_path / "db.sqlite",
        artifact_root=tmp_path / "artifacts",
        embedding_dir=tmp_path / "artifacts" / "embeddings" / "fashionclip",
        index_dir=tmp_path / "artifacts" / "index" / "fashionclip",
        dataset_revision="test",
        llm_enabled=llm_enabled,
    )


def _make_workflow(
    tmp_path: Path,
    llm: FakeLlm | None,
    *,
    weather_tool=None,
) -> StyleForgeWorkflow:
    settings = _settings(tmp_path, llm_enabled=llm is not None)
    workflow = StyleForgeWorkflow(
        database_path=settings.database_path,
        embedding_dir=settings.embedding_dir,
        model_dir=settings.artifact_root / "models",
        device="cpu",
        llm_client=llm,
        settings=settings,
        weather_tool=weather_tool,
    )
    return workflow


def _agent1_payload() -> dict:
    return {
        "request_signature": {
            "theme": "互联网公司面试",
            "unique_mood": ["专业", "年轻"],
            "practical_context": ["办公室"],
            "generic_tendencies_to_avoid": ["仅由基础款组成"],
        },
        "retrieval_plans": [
            {"type": "core", "query": "modern professional interview outfit", "score_weight": 0.40},
            {"type": "distinctive", "query": "young structured business piece", "score_weight": 0.30},
            {"type": "supporting", "query": "comfortable semi-formal office wear", "score_weight": 0.10},
        ],
        "candidate_requirements": {"tops": 5, "bottoms": 5, "dresses": 0, "outerwear": 0, "shoes": 5, "accessories": 0},
    }


def _agent2_payload(outfit_ids: tuple[str, ...] = ("outfit_001", "outfit_002", "outfit_003")) -> dict:
    def _outfit(outfit_id: str, items: list[str]) -> dict:
        return {
            "outfit_id": outfit_id,
            "composition_strategy": {
                "visual_anchor": "结构感外套",
                "supporting_direction": "深色下装",
                "practical_balance": "舒适",
            },
            "item_ids": items,
            "style_tag": "面试正装",
            "reasoning": "正式但年轻",
            "request_specific_elements": [],
        }

    return {
        "outfits": [
            _outfit("outfit_001", ["top-1", "bottom-1", "shoe-1"]),
            _outfit("outfit_002", ["top-2", "bottom-2", "shoe-2"]),
            _outfit("outfit_003", ["top-3", "bottom-1", "shoe-1"]),
        ]
    }


def _agent3_payload(decision: str = "accept", **overrides) -> dict:
    payload = {
        "outfit_assessment": {
            "outfit_id": "outfit_001",
            "dimension_scores": {
                "request_relevance": 9,
                "request_specificity": 8,
                "outfit_coordination": 9,
                "wearability": 8,
                "freshness": 7,
            },
            "reasoning": "正式且年轻",
            "improvements": "",
        },
        "explanation_assessment": {"grounded": True, "unsupported_claims": []},
        "alternatives": [{"outfit_id": "outfit_002", "strength": "更实穿"}],
        "decision": decision,
        "failure_source": "",
        "feedback": "",
        "missing_items": [],
        "best_effort_outfit_id": "",
    }
    payload.update(overrides)
    return payload


def test_workflow_accept_with_three_llm_calls(tmp_path: Path) -> None:
    _seed_database(tmp_path / "db.sqlite")
    fake = FakeLlm([_agent1_payload(), _agent2_payload(), _agent3_payload()])
    workflow = _make_workflow(tmp_path, fake)

    output = workflow.recommend(user_id=USER, request=REQUEST)

    assert output.mode == "llm_semantic_multi_agent"
    assert output.llm_call_count == 3
    assert output.llm_attempts == 3
    assert output.decision == "accept"
    assert output.llm_enabled is True
    assert output.request_signature["theme"] == "互联网公司面试"
    assert len(output.result.recommendations) == 3
    assert output.fallback_count == 0
    # Every recommendation carries a score: LLM score for the critic's pick,
    # deterministic score_outfit for the alternatives (never 0).
    scores = [recommendation.score for recommendation in output.result.recommendations]
    assert all(score > 0 for score in scores)


def test_workflow_weather_context_is_shared_with_three_agents(tmp_path: Path) -> None:
    class FakeWeatherTool:
        def __init__(self) -> None:
            self.inputs = []

        def __call__(self, tool_input):
            self.inputs.append(tool_input)
            return WeatherFacts(
                status="available",
                requested_location=tool_input.location,
                requested_date=tool_input.date,
                resolved_location=ResolvedLocation(
                    name="上海",
                    country="中国",
                    admin1="上海",
                    latitude=31.23,
                    longitude=121.47,
                    timezone="Asia/Shanghai",
                ),
                forecast_date="2026-08-11",
                temperature_min_c=25,
                temperature_max_c=32,
                feels_like_c=31,
                precipitation_probability_percent=70,
                humidity_percent=82,
                wind_speed_kmh=18,
                weather_code=61,
                condition="小雨",
            )

    _seed_database(tmp_path / "db.sqlite")
    first_agent1 = _agent1_payload()
    first_agent1["context_requirements"] = {
        "weather": {
            "needed": True,
            "location": "上海",
            "date": "明天",
            "reason": "明天的户外面试通勤受天气影响",
        }
    }
    contextual_agent1 = _agent1_payload()
    contextual_agent1["request_signature"]["practical_context"] = [
        "小雨",
        "25-32°C",
        "湿度82%",
    ]
    contextual_agent1["context_requirements"] = first_agent1[
        "context_requirements"
    ]
    fake_llm = FakeLlm(
        [first_agent1, contextual_agent1, _agent2_payload(), _agent3_payload()]
    )
    fake_weather = FakeWeatherTool()
    workflow = _make_workflow(
        tmp_path,
        fake_llm,
        weather_tool=fake_weather,
    )

    output = workflow.recommend(
        user_id=USER,
        request="明天在上海参加户外面试，穿什么？",
    )

    assert output.llm_call_count == 4
    assert len(fake_weather.inputs) == 1
    assert output.environment_context["weather"]["status"] == "available"
    assert output.tool_calls[0]["status"] == "available"
    assert [item["node"] for item in output.trace].count("semantic_retriever") == 2
    assert "temperature_min_c" in fake_llm.calls[1]["user"]
    assert "temperature_min_c" in fake_llm.calls[2]["user"]
    assert "temperature_min_c" in fake_llm.calls[3]["user"]


def test_workflow_persists_semantic_detail(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    _seed_database(db_path)
    fake = FakeLlm([_agent1_payload(), _agent2_payload(), _agent3_payload()])
    workflow = _make_workflow(tmp_path, fake)

    output = workflow.recommend(user_id=USER, request=REQUEST)

    with database_session(db_path) as connection:
        row = connection.execute(
            "SELECT semantic_detail_json FROM styling_runs WHERE run_id = ?",
            (output.result.run_id,),
        ).fetchone()
    assert row is not None
    detail = json.loads(row["semantic_detail_json"])
    assert detail["request_signature"]["theme"] == "互联网公司面试"
    assert detail["decision"] == "accept"
    assert detail["llm_call_count"] == 3


def test_workflow_recompose_then_accept(tmp_path: Path) -> None:
    _seed_database(tmp_path / "db.sqlite")
    fake = FakeLlm(
        [
            _agent1_payload(),
            _agent2_payload(),
            _agent3_payload(
                "recompose",
                failure_source="composer",
                feedback="候选中已有特色单品，请重新组合",
            ),
            _agent2_payload(),
            _agent3_payload(),
        ]
    )
    workflow = _make_workflow(tmp_path, fake)

    output = workflow.recommend(user_id=USER, request=REQUEST)

    assert output.decision == "accept"
    assert output.llm_call_count == 5
    assert output.fallback_count == 1
    assert len(output.result.recommendations) == 3


def test_workflow_retrieve_more_then_accept(tmp_path: Path) -> None:
    _seed_database(tmp_path / "db.sqlite")
    fake = FakeLlm(
        [
            _agent1_payload(),
            _agent2_payload(),
            _agent3_payload("retrieve_more", failure_source="candidate_pool", feedback="候选池缺少特色单品"),
            _agent1_payload(),
            _agent2_payload(),
            _agent3_payload(),
        ]
    )
    workflow = _make_workflow(tmp_path, fake)

    output = workflow.recommend(user_id=USER, request=REQUEST)

    assert output.decision == "accept"
    assert output.llm_call_count == 6
    assert output.fallback_count == 1
    assert len(output.result.recommendations) == 3


def test_workflow_wardrobe_gap_returns_best_effort(tmp_path: Path) -> None:
    _seed_database(tmp_path / "db.sqlite")
    fake = FakeLlm(
        [
            _agent1_payload(),
            _agent2_payload(),
            _agent3_payload(
                "wardrobe_gap",
                failure_source="wardrobe",
                feedback="衣橱缺少合适鞋履",
                missing_items=[{"category": "shoes", "desired_features": ["深色", "半正式"]}],
                best_effort_outfit_id="outfit_002",
            ),
        ]
    )
    workflow = _make_workflow(tmp_path, fake)

    output = workflow.recommend(user_id=USER, request=REQUEST)

    assert output.decision == "wardrobe_gap"
    assert output.llm_call_count == 3
    assert output.fallback_count == 0
    assert output.critic["missing_items"][0]["category"] == "shoes"
    # best-effort outfit is still persisted as recommendations.
    assert len(output.result.recommendations) == 3


def test_workflow_repeated_recompose_degrades_to_best_effort(tmp_path: Path) -> None:
    _seed_database(tmp_path / "db.sqlite")
    fake = FakeLlm(
        [
            _agent1_payload(),
            _agent2_payload(),
            _agent3_payload("recompose", failure_source="composer", feedback="再试一次"),
            _agent2_payload(),
            _agent3_payload("recompose", failure_source="composer", feedback="还是不行"),
        ]
    )
    workflow = _make_workflow(tmp_path, fake)

    output = workflow.recommend(user_id=USER, request=REQUEST)

    # Guard clamps to a single recompose: fallback_count stays <= 1 and the
    # total LLM calls never exceed the frozen 5-call recompose budget.
    assert output.fallback_count == 1
    assert output.llm_call_count <= 5
    assert output.decision == "recompose"
    assert output.best_effort is not None


def test_workflow_without_llm_uses_deterministic_chain(tmp_path: Path) -> None:
    _seed_database(tmp_path / "db.sqlite")
    workflow = _make_workflow(tmp_path, None)

    output = workflow.recommend(user_id=USER, request=REQUEST)

    assert output.mode == "deterministic_multi_agent_with_fashionclip"
    assert output.llm_enabled is False
    assert output.decision == ""
    assert output.result.status in {"completed", "infeasible"}


def _weather_agent1_payload(*, location: str = "", weather_location: str = "", date_expr: str = "明天") -> dict:
    payload = _agent1_payload()
    payload["context_requirements"] = {
        "temporal": {
            "needed": bool(date_expr),
            "expression": date_expr,
            "reason": "需要确定日期的天气",
        },
        "location": {
            "needed": True,
            "query": location,
            "allow_profile_default": True,
            "reason": "天气查询需要地点",
        },
        "weather": {
            "needed": True,
            "location": weather_location,
            "date": date_expr,
            "granularity": "daily",
            "reason": "出行受天气影响",
        },
    }
    payload["context_criticality"] = "helpful"
    payload["default_policy_allowed"] = True
    payload["implicit_context_signals"] = ["未显式提天气但隐含出行依赖"]
    return payload


def _contextual_agent1(original: dict) -> dict:
    payload = _agent1_payload()
    payload["context_requirements"] = original["context_requirements"]
    payload["request_signature"]["practical_context"] = ["小雨", "25-32°C"]
    return payload


def test_workflow_device_location_uses_coordinates_and_keeps_privacy(tmp_path: Path) -> None:
    _seed_database(tmp_path / "db.sqlite")
    first_agent1 = _weather_agent1_payload()
    fake_llm = FakeLlm(
        [first_agent1, _contextual_agent1(first_agent1), _agent2_payload(), _agent3_payload()]
    )
    fake_weather = _RecordingWeatherTool()
    workflow = _make_workflow(tmp_path, fake_llm, weather_tool=fake_weather)
    # captured_at is relative so the resolver's real clock does not mark it stale.
    location_context = {
        "latitude": 31.234567,
        "longitude": 121.474444,
        "accuracy_m": 85.0,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "consent_granted": True,
    }

    output = workflow.recommend(
        user_id=USER,
        request="明天穿什么？",
        location_context=location_context,
    )

    assert output.llm_call_count == 4
    assert len(fake_weather.inputs) == 1
    tool_input = fake_weather.inputs[0]
    assert tool_input.latitude == round(31.234567, 2)
    assert tool_input.longitude == round(121.474444, 2)
    assert tool_input.location == ""
    assert output.resolved_location_context["source"] == "device"
    assert output.resolved_location_context["accuracy_bucket"] == "high"
    # Privacy: no raw coordinates in the public payload.
    assert "latitude" not in output.resolved_location_context
    assert "longitude" not in output.resolved_location_context
    assert "latitude" not in output.tool_calls[0]["arguments"]
    assert "longitude" not in output.tool_calls[0]["arguments"]


def test_workflow_beijing_travel_applies_default_3_day_window(tmp_path: Path) -> None:
    _seed_database(tmp_path / "db.sqlite")
    first_agent1 = _weather_agent1_payload(
        location="北京", weather_location="北京", date_expr=""
    )
    fake_llm = FakeLlm(
        [first_agent1, _contextual_agent1(first_agent1), _agent2_payload(), _agent3_payload()]
    )
    fake_weather = _RecordingWeatherTool()
    workflow = _make_workflow(tmp_path, fake_llm, weather_tool=fake_weather)

    output = workflow.recommend(user_id=USER, request="去北京旅游该怎么穿？")

    assert output.llm_call_count == 4
    assert len(fake_weather.inputs) == 1
    tool_input = fake_weather.inputs[0]
    assert tool_input.location == "北京"
    assert tool_input.date == ""
    today = date.today()
    assert tool_input.start_date == today.isoformat()
    assert tool_input.end_date == (today + timedelta(days=2)).isoformat()
    time_context = output.resolved_time_context
    assert time_context["status"] == "resolved"
    assert time_context["default_applied"] is True
    assert time_context["precision"] == "near_3_days"


def test_workflow_style_knowledge_query_skips_weather_tool(tmp_path: Path) -> None:
    _seed_database(tmp_path / "db.sqlite")
    first_agent1 = _agent1_payload()
    first_agent1["context_requirements"] = {
        "weather": {"needed": False},
        "temporal": {"needed": False},
        "location": {"needed": False},
    }
    first_agent1["context_criticality"] = "not_needed"
    first_agent1["default_policy_allowed"] = False
    fake_llm = FakeLlm([first_agent1, _agent2_payload(), _agent3_payload()])
    fake_weather = _RecordingWeatherTool()
    workflow = _make_workflow(tmp_path, fake_llm, weather_tool=fake_weather)

    output = workflow.recommend(user_id=USER, request="黑色马甲怎么搭？")

    assert output.llm_call_count == 3
    assert fake_weather.inputs == []
    assert output.tool_calls == []
    assert output.environment_context == {}
