from __future__ import annotations

from pathlib import Path

import pytest

from styleforge.llm.client import LlmUnavailable
from styleforge.models.task import CandidateItem, TaskExecutionInput
from styleforge.orchestration.task_router import TaskType
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.task_run_repository import get_task_run
from styleforge.repositories.wardrobe_repository import add_items
from styleforge.workflow.task_workflow import MultiTaskWorkflow

from tests.extension_llm import ScriptedExtensionLlm, approved_review, intent_response
from tests.helpers import make_item


KNOWLEDGE_ROOT = Path("knowledge")


def test_recommendation_weather_is_copied_into_shared_context_pack(
    tmp_path: Path,
) -> None:
    database_path = _seed_database(tmp_path)
    weather = {
        "status": "available",
        "source": "open-meteo",
        "requested_location": "上海",
        "forecast_date": "2026-08-11",
        "temperature_min_c": 25,
        "temperature_max_c": 32,
    }
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=None,
        recommendation_runner=lambda **_: {
            "structured_result": {"status": "completed", "recommendations": []},
            "environment_context": {"weather": weather},
        },
    )

    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="明天在上海参加户外活动，帮我推荐穿搭",
            requested_task_type=TaskType.OUTFIT_RECOMMEND,
        )
    )

    assert payload["context_pack"]["environment_context"]["weather"] == weather
    assert payload["result"]["environment_context"]["weather"] == weather


def test_task_execution_forwards_device_location_context(tmp_path: Path) -> None:
    database_path = _seed_database(tmp_path)
    captured: dict[str, object] = {}
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=None,
        recommendation_runner=lambda **kwargs: captured.update(kwargs) or {
            "structured_result": {"status": "completed", "recommendations": []},
            "environment_context": {},
        },
    )
    location_context = {
        "latitude": 31.234567,
        "longitude": 121.474444,
        "accuracy_m": 85.0,
        "captured_at": "2026-08-10T08:00:00+00:00",
        "source": "device",
        "consent_granted": True,
    }

    workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="今晚在上海的露台约会穿什么",
            requested_task_type=TaskType.OUTFIT_RECOMMEND,
            location_context=location_context,
        )
    )

    # The device location flows verbatim into the recommendation subgraph.
    assert captured.get("location_context") == location_context
    assert captured.get("user_id") == "u"
    assert captured.get("request") == "今晚在上海的露台约会穿什么"


def _seed_database(tmp_path: Path, user_id: str = "u") -> Path:
    database_path = tmp_path / "styleforge.db"
    initialize_database(database_path)
    items = [
        make_item("vest", "top", "White tailored waistcoat vest", "white"),
        make_item("shirt", "top", "White collared shirt", "white"),
        make_item("tee", "top", "Vintage graphic t-shirt", "cream"),
        make_item("knit", "top", "Brown knit sweater", "brown"),
        make_item("trousers", "pants", "Black tailored trousers", "black"),
        make_item("jeans", "pants", "Blue straight jeans", "blue"),
        make_item("skirt", "skirt", "Gray pleated skirt", "gray"),
        make_item("loafers", "shoes", "Black leather loafers", "black"),
        make_item("sneakers", "shoes", "White running sneakers", "white"),
        make_item("boots", "shoes", "Brown ankle boots", "brown"),
        make_item("trench", "outwear", "Beige trench coat", "beige"),
        make_item("leather", "outwear", "Black leather jacket", "black"),
        make_item("bag", "bag", "Brown tote bag", "brown"),
    ]
    with database_session(database_path) as connection:
        upsert_items(connection, items, "test")
        add_items(connection, user_id, [item.item_id for item in items])
    return database_path


def _workflow(
    database_path: Path,
    agent2_response: dict,
) -> tuple[MultiTaskWorkflow, ScriptedExtensionLlm]:
    llm = ScriptedExtensionLlm(
        [intent_response("理解请求并建立衣橱事实范围"), agent2_response, approved_review()]
    )
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=llm,
        recommendation_runner=lambda **_: {
            "structured_result": {"status": "completed", "recommendations": []}
        },
    )
    return workflow, llm


def _item_advice_draft(*, summary: str = "以黑色马甲组合衣橱单品") -> dict:
    return {
        "task_type": "item_advice",
        "status": "completed",
        "summary": summary,
        "result": {
            "status": "completed",
            "knowledge_type": "item",
            "title": "黑色马甲搭配",
            "summary": summary,
            "anchor_item": {"item_id": "candidate-preview", "name": "black vest"},
            "anchor_source": "candidate",
            "compatible_items_by_slot": {
                "top": [{"item_id": "shirt", "name": "White collared shirt"}],
                "bottom": [{"item_id": "trousers", "name": "Black tailored trousers"}],
                "footwear": [{"item_id": "loafers", "name": "Black leather loafers"}],
            },
            "wardrobe_matches": [],
            "wardrobe_matches_by_slot": {},
            "sample_outfits": [
                {
                    "outfit_id": "vest-look-1",
                    "item_ids": ["candidate-preview", "shirt", "trousers", "loafers"],
                    "reasoning": "白衬衫作为内层，黑裤和乐福鞋保持利落。",
                }
            ],
            "evidence": [{"source_id": "item-vest"}],
            "clarification_question": "",
            "limitations": [],
        },
        "used_item_ids": ["shirt", "trousers", "loafers"],
        "evidence_source_ids": ["item-vest"],
    }


def _assert_three_agent_execution(payload: dict, llm: ScriptedExtensionLlm) -> None:
    assert payload["llm_enabled"] is True
    assert payload["llm_call_count"] == 3
    assert len(llm.calls) == 3
    assert [item["node"] for item in payload["trace"]][-3:] == [
        "semantic_retriever_agent",
        "composer_agent",
        "critic_agent",
    ]
    assert all(not step.get("degraded", False) for step in payload["trace"][-3:])


def test_local_modification_locks_non_target_items(tmp_path: Path) -> None:
    database_path = _seed_database(tmp_path)
    agent2 = {
        "task_type": "outfit_modify",
        "status": "completed",
        "summary": "只替换鞋履",
        "result": {
            "status": "completed",
            "current_outfit_id": "",
            "target_slot": "footwear",
            "replaced_item_ids": ["loafers"],
            "locked_item_ids": ["shirt", "trousers", "trench"],
            "alternatives": [
                {
                    "outfit_id": "modified-1",
                    "item_ids": ["shirt", "trousers", "trench", "sneakers"],
                    "score": 88.0,
                    "reasoning": "保留上衣、裤装和外套，只换成运动鞋。",
                }
            ],
            "message": "已锁定其他三件单品，只替换鞋履。",
        },
        "used_item_ids": ["shirt", "trousers", "trench", "sneakers"],
        "evidence_source_ids": [],
    }
    workflow, llm = _workflow(database_path, agent2)
    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="鞋太正式，只换一双，其他保持不变。",
            requested_task_type=TaskType.OUTFIT_MODIFY,
            current_item_ids=["shirt", "trousers", "loafers", "trench"],
            target_slot="footwear",
        )
    )

    assert payload["result"]["locked_item_ids"] == ["shirt", "trousers", "trench"]
    assert payload["result"]["alternatives"][0]["item_ids"] == [
        "shirt",
        "trousers",
        "trench",
        "sneakers",
    ]
    _assert_three_agent_execution(payload, llm)


def test_style_advice_uses_local_knowledge_as_supporting_evidence(tmp_path: Path) -> None:
    database_path = _seed_database(tmp_path)
    agent2 = {
        "task_type": "style_advice",
        "status": "completed",
        "summary": "用衣橱已有单品落实美式复古",
        "result": {
            "status": "completed",
            "knowledge_type": "style",
            "title": "American Vintage 美式复古",
            "summary": "以图案 T 恤、牛仔裤和皮夹克建立复古层次。",
            "principles": [{"title": "层次", "content": "控制年代元素数量。"}],
            "wardrobe_matches": [{"item_id": "tee", "name": "Vintage graphic t-shirt"}],
            "evidence": [{"source_id": "style-american-vintage"}],
            "limitations": [],
        },
        "used_item_ids": ["tee"],
        "evidence_source_ids": ["style-american-vintage"],
    }
    workflow, llm = _workflow(database_path, agent2)
    payload = workflow.execute(
        TaskExecutionInput(user_id="u", request="American Vintage 风格应该怎么穿？")
    )

    assert payload["task_type"] == "style_advice"
    assert payload["result"]["evidence"][0]["source_id"] == "style-american-vintage"
    assert payload["result"]["wardrobe_matches"][0]["item_id"] == "tee"
    _assert_three_agent_execution(payload, llm)


def test_item_advice_anchors_wardrobe_item_and_builds_outfit(tmp_path: Path) -> None:
    database_path = _seed_database(tmp_path)
    agent2 = {
        "task_type": "item_advice",
        "status": "completed",
        "summary": "以黑色马甲为锚点组合衣橱单品",
        "result": {
            "status": "completed",
            "knowledge_type": "item",
            "title": "黑色马甲搭配",
            "summary": "用白衬衫建立内层，以黑裤和乐福鞋保持利落。",
            "anchor_item": {"item_id": "candidate-preview", "name": "black vest"},
            "anchor_source": "candidate",
            "compatible_items_by_slot": {
                "top": [{"item_id": "shirt", "name": "White collared shirt"}],
                "bottom": [{"item_id": "trousers", "name": "Black tailored trousers"}],
                "footwear": [{"item_id": "loafers", "name": "Black leather loafers"}],
            },
            "wardrobe_matches": [],
            "wardrobe_matches_by_slot": {},
            "sample_outfits": [
                {
                    "outfit_id": "vest-look-1",
                    "item_ids": ["candidate-preview", "shirt", "trousers", "loafers"],
                    "reasoning": "锚点马甲与利落下装、鞋履形成完整搭配。",
                }
            ],
            "evidence": [{"source_id": "item-vest"}],
            "clarification_question": "",
            "limitations": [],
        },
        "used_item_ids": ["shirt", "trousers", "loafers"],
        "evidence_source_ids": ["item-vest"],
    }
    workflow, llm = _workflow(database_path, agent2)
    payload = workflow.execute(TaskExecutionInput(user_id="u", request="黑色马甲怎么搭？"))

    assert payload["task_type"] == "item_advice"
    assert payload["result"]["anchor_item"]["item_id"] == "candidate-preview"
    assert "candidate-preview" in payload["result"]["sample_outfits"][0]["item_ids"]
    assert "shirt" in payload["agent_outputs"]["agent1"]["candidate_item_ids"]
    _assert_three_agent_execution(payload, llm)


def test_compatibility_keeps_candidate_transient(tmp_path: Path) -> None:
    database_path = _seed_database(tmp_path)
    agent2 = {
        "task_type": "wardrobe_compatibility",
        "status": "completed",
        "summary": "候选牛仔靴能与衣橱基础单品组成完整搭配",
        "result": {
            "status": "completed",
            "candidate_item": {"item_id": "candidate-preview", "name": "Brown cowboy boots"},
            "candidate_slot": "footwear",
            "compatibility_score": 86.0,
            "recommendation": "recommended",
            "recommendation_text": "衣橱已有上衣和下装能支持多套搭配。",
            "compatible_item_counts": {"top": 1, "bottom": 1},
            "compatible_items_by_slot": {
                "top": [{"item_id": "shirt", "name": "White collared shirt"}],
                "bottom": [{"item_id": "jeans", "name": "Blue straight jeans"}],
            },
            "complete_outfit_count": 1,
            "sample_outfits": [
                {
                    "outfit_id": "candidate-look-1",
                    "wardrobe_item_ids": ["shirt", "jeans"],
                    "reasoning": "白衬衫和牛仔裤能与候选靴履形成完整日常造型。",
                }
            ],
            "redundancy_score": 30.0,
            "similar_wardrobe_items": [{"item_id": "boots", "name": "Brown ankle boots"}],
            "evidence": [{"source_id": "item-cowboy-boots"}],
            "clarification_question": "",
            "limitations": [],
        },
        "used_item_ids": ["shirt", "jeans", "boots"],
        "evidence_source_ids": ["item-cowboy-boots"],
    }
    workflow, llm = _workflow(database_path, agent2)
    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="这双牛仔靴值得买吗？",
            candidate_item=CandidateItem(
                name="Brown cowboy boots",
                item_type="shoes",
                subtype="boots",
                color="brown",
            ),
        )
    )

    assert payload["result"]["complete_outfit_count"] == 1
    with database_session(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM catalog_items WHERE item_id = 'candidate-preview'"
        ).fetchone()[0] == 0
    _assert_three_agent_execution(payload, llm)


def test_targeted_medieval_gap_and_task_run_persistence(tmp_path: Path) -> None:
    database_path = _seed_database(tmp_path)
    agent2 = {
        "task_type": "wardrobe_gap",
        "status": "completed",
        "summary": "衣橱已覆盖马甲和靴履，缺少长线条裙装",
        "result": {
            "status": "completed",
            "analysis_mode": "targeted",
            "target": {"style": "Medieval-Inspired 中世纪灵感风格"},
            "wardrobe_item_count": 13,
            "slot_counts": {"top": 4, "bottom": 3, "footwear": 3, "outerwear": 2, "bag": 1},
            "covered_elements": [
                {"id": "medieval-layering-vest", "label": "有结构感的叠穿马甲", "matching_item_ids": ["vest"]},
                {"id": "medieval-grounded-boots", "label": "皮革感靴履", "matching_item_ids": ["boots"]},
            ],
            "gaps": [
                {
                    "id": "medieval-long-silhouette",
                    "label": "长线条裙装",
                    "priority": "high",
                    "suggestion": "补充一件长裙或长连衣裙。",
                }
            ],
            "gap_count": 1,
            "redundancies": [],
            "summary": "要形成中世纪灵感轮廓，当前最明确的缺口是长线条裙装。",
            "evidence": [{"source_id": "style-medieval-inspired"}],
            "limitations": [],
        },
        "used_item_ids": ["vest", "boots"],
        "evidence_source_ids": ["style-medieval-inspired"],
    }
    workflow, llm = _workflow(database_path, agent2)
    payload = workflow.execute(
        TaskExecutionInput(user_id="u", request="要搭配中世纪风格的话，我的衣柜还缺什么？")
    )

    assert payload["task_type"] == "wardrobe_gap"
    assert payload["result"]["analysis_mode"] == "targeted"
    assert payload["result"]["gaps"][0]["label"] == "长线条裙装"
    with database_session(database_path) as connection:
        stored = get_task_run(connection, user_id="u", run_id=payload["run_id"])
    assert stored is not None
    assert stored["status"] == "completed"
    assert "missing_elements 才是缺口的唯一事实范围" in llm.calls[2]["system"]
    _assert_three_agent_execution(payload, llm)


def test_gap_draft_must_match_agent1_missing_elements(tmp_path: Path) -> None:
    database_path = _seed_database(tmp_path)
    invalid_agent2 = {
        "task_type": "wardrobe_gap",
        "status": "completed",
        "summary": "当前衣橱没有缺口",
        "result": {
            "status": "completed",
            "analysis_mode": "targeted",
            "target": {"style": "Medieval-Inspired 中世纪灵感风格"},
            "wardrobe_item_count": 13,
            "slot_counts": {
                "top": 4,
                "bottom": 3,
                "footwear": 3,
                "outerwear": 2,
                "bag": 1,
            },
            "covered_elements": [
                {"id": "medieval-layering-vest", "matching_item_ids": ["vest"]},
                {"id": "medieval-grounded-boots", "matching_item_ids": ["boots"]},
                {"id": "medieval-textured-accessory", "matching_item_ids": ["bag"]},
            ],
            "gaps": [],
            "gap_count": 0,
            "redundancies": [],
            "summary": "当前衣橱没有缺口。",
            "evidence": [{"source_id": "style-medieval-inspired"}],
            "limitations": [],
        },
        "used_item_ids": ["vest", "boots", "bag"],
        "evidence_source_ids": ["style-medieval-inspired"],
    }
    llm = ScriptedExtensionLlm(
        [intent_response("分析中世纪风格衣橱缺口"), invalid_agent2]
    )
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=llm,
    )

    with pytest.raises(ValueError, match="missing_elements 不一致"):
        workflow.execute(
            TaskExecutionInput(
                user_id="u",
                request="要搭配中世纪风格，我的衣柜还缺什么？",
            )
        )

    assert len(llm.calls) == 2


def test_extension_without_llm_fails_and_is_persisted(tmp_path: Path) -> None:
    database_path = _seed_database(tmp_path)
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=None,
    )

    with pytest.raises(LlmUnavailable, match="Agent 1 requires"):
        workflow.execute(TaskExecutionInput(user_id="u", request="黑色马甲怎么搭？"))

    with database_session(database_path) as connection:
        stored = connection.execute(
            "SELECT status, error_message FROM task_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert stored["status"] == "failed"
    assert "Agent 1 requires" in stored["error_message"]


def test_needs_clarification_is_persisted_as_a_terminal_status(tmp_path: Path) -> None:
    database_path = _seed_database(tmp_path)
    agent2 = {
        "task_type": "item_advice",
        "status": "needs_clarification",
        "summary": "需要确认具体单品",
        "result": {
            "status": "needs_clarification",
            "knowledge_type": "item",
            "title": "请补充单品信息",
            "summary": "当前描述无法解析出明确单品。",
            "anchor_item": None,
            "anchor_source": "unresolved",
            "compatible_items_by_slot": {},
            "wardrobe_matches": [],
            "wardrobe_matches_by_slot": {},
            "sample_outfits": [],
            "evidence": [],
            "clarification_question": "请补充单品的品类和颜色。",
            "limitations": ["缺少明确单品"],
        },
        "used_item_ids": [],
        "evidence_source_ids": [],
    }
    workflow, llm = _workflow(database_path, agent2)

    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="这个怎么搭？",
            requested_task_type=TaskType.ITEM_ADVICE,
        )
    )

    assert payload["status"] == "needs_clarification"
    with database_session(database_path) as connection:
        stored = get_task_run(connection, user_id="u", run_id=payload["run_id"])
    assert stored is not None
    assert stored["status"] == "needs_clarification"
    _assert_three_agent_execution(payload, llm)


def test_agent2_repairs_incomplete_item_advice_before_critic(tmp_path: Path) -> None:
    database_path = _seed_database(tmp_path)
    invalid_draft = {
        "task_type": "item_advice",
        "status": "needs_clarification",
        "summary": "缺少场合信息",
        "result": {
            "status": "needs_clarification",
            "knowledge_type": "item",
            "title": "黑色马甲搭配",
            "summary": "请补充场合。",
            "anchor_item": {"item_id": "candidate-preview", "name": "black vest"},
            "anchor_source": "candidate",
            "compatible_items_by_slot": {
                "bottom": [{"item_id": "trousers", "name": "Black tailored trousers"}]
            },
            "wardrobe_matches": [],
            "wardrobe_matches_by_slot": {},
            "sample_outfits": [],
            "evidence": [{"source_id": "item-vest"}],
            "clarification_question": "请补充穿着场合。",
            "limitations": [],
        },
        "used_item_ids": ["trousers"],
        "evidence_source_ids": ["item-vest"],
    }
    llm = ScriptedExtensionLlm(
        [
            intent_response("黑色马甲通用搭配"),
            invalid_draft,
            _item_advice_draft(),
            approved_review(),
        ]
    )
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=llm,
    )

    payload = workflow.execute(TaskExecutionInput(user_id="u", request="黑色马甲怎么搭？"))

    assert payload["status"] == "completed"
    assert payload["result"]["sample_outfits"]
    assert payload["llm_call_count"] == 4
    assert payload["diagnostics"]["agent2"]["call_count"] == 2
    assert "needs_clarification is forbidden" in llm.calls[2]["user"]


def test_agent3_rejection_recomposes_once_and_reviews_again(tmp_path: Path) -> None:
    database_path = _seed_database(tmp_path)
    first_draft = _item_advice_draft(summary="给出一套基础组合")
    revised_draft = _item_advice_draft(summary="补充锚点、分层逻辑和完整通用搭配")
    llm = ScriptedExtensionLlm(
        [
            intent_response("黑色马甲通用搭配"),
            first_draft,
            {
                "approved": False,
                "grounded": True,
                "summary": "说明不够具体",
                "issues": ["需要解释马甲、内搭、下装和鞋履之间的组合逻辑"],
            },
            revised_draft,
            approved_review("重做方案已完整回答怎么搭"),
        ]
    )
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=llm,
    )

    payload = workflow.execute(TaskExecutionInput(user_id="u", request="黑色马甲怎么搭？"))

    assert payload["status"] == "completed"
    assert payload["result"]["summary"] == "补充锚点、分层逻辑和完整通用搭配"
    assert payload["llm_call_count"] == 5
    assert [item["node"] for item in payload["trace"]][-3:] == [
        "critic_agent",
        "composer_agent",
        "critic_agent",
    ]
    assert payload["trace"][-3]["status"] == "rejected"
    assert payload["trace"][-1]["status"] == "completed"
