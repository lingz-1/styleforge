from __future__ import annotations

from pathlib import Path

from styleforge.models.task import TaskExecutionInput
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.wardrobe_repository import add_items
from styleforge.services.chat_service import outfit_context_from_payload
from styleforge.workflow.task_workflow import MultiTaskWorkflow

from tests.extension_llm import ScriptedExtensionLlm, approved_review, intent_response
from tests.helpers import make_item


def _seed_wardrobe(database_path: Path) -> None:
    items = [
        make_item("top-1", "top", "白衬衫", "white"),
        make_item("bottom-1", "pants", "黑色西裤", "black"),
        make_item("coat-1", "outwear", "灰色大衣", "gray"),
        make_item("shoes-1", "shoes", "黑色皮鞋", "black"),
        make_item("blazer-1", "outwear", "深蓝西装外套", "navy"),
        make_item("dress-1", "dress", "黑色连衣裙", "black"),
        make_item("heels-1", "shoes", "黑色高跟鞋", "black"),
    ]
    with database_session(database_path) as connection:
        upsert_items(connection, items, "test")
        add_items(connection, "u", [item.item_id for item in items])


def _fake_recommendation_runner(**kwargs) -> dict:
    return {
        "structured_result": {
            "status": "completed",
            "advice": ["这是一套通勤搭配"],
            "recommendations": [
                {
                    "outfit_id": "outfit-1",
                    "item_ids": ["top-1", "bottom-1", "coat-1", "shoes-1"],
                }
            ],
        },
        "result": {
            "status": "completed",
            "recommendations": [
                {
                    "outfit_id": "outfit-1",
                    "item_ids": ["top-1", "bottom-1", "coat-1", "shoes-1"],
                }
            ],
        },
    }


ROUND2_MODIFY = {
    "task_type": "outfit_modify",
    "status": "completed",
    "summary": "已把外套换成西装外套",
    "result": {
        "status": "completed",
        "current_outfit_id": "outfit-1",
        "target_slot": "outerwear",
        "replaced_item_ids": ["coat-1"],
        "locked_item_ids": ["top-1", "bottom-1", "shoes-1"],
        "alternatives": [
            {
                "outfit_id": "outfit-2",
                "item_ids": ["blazer-1", "top-1", "bottom-1", "shoes-1"],
                "reasoning": "换成深蓝色西装外套，保持通勤正式感",
            }
        ],
        "message": "已为你更换外套",
    },
    "used_item_ids": ["blazer-1", "top-1", "bottom-1", "shoes-1"],
    "evidence_source_ids": [],
}

ROUND3_MODIFY = {
    "task_type": "outfit_modify",
    "status": "completed",
    "summary": "整体调整为更正式的搭配",
    "result": {
        "status": "completed",
        "current_outfit_id": "outfit-1",
        "target_slot": "",
        "replaced_item_ids": [],
        "locked_item_ids": [],
        "alternatives": [
            {
                "outfit_id": "outfit-3",
                "item_ids": ["dress-1", "heels-1"],
                "reasoning": "换上连衣裙与高跟鞋，整体更正式",
            }
        ],
        "message": "已调整为更正式的搭配",
    },
    "used_item_ids": ["dress-1", "heels-1"],
    "evidence_source_ids": [],
}


def test_session_multiturn_reuses_outfit_context(tmp_path: Path) -> None:
    database_path = tmp_path / "multiturn.db"
    initialize_database(database_path)
    _seed_wardrobe(database_path)

    script = [
        intent_response("理解换外套请求"),
        ROUND2_MODIFY,
        approved_review(),
        intent_response("理解整体调整请求"),
        ROUND3_MODIFY,
        approved_review(),
    ]
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=ScriptedExtensionLlm(script),
        recommendation_runner=_fake_recommendation_runner,
    )

    # Round 1: plain recommendation (deterministic runner, no LLM).
    payload1 = workflow.execute(
        TaskExecutionInput(user_id="u", request="帮我推荐一套通勤搭配")
    )
    assert payload1["task_type"] == "outfit_recommend"
    assert payload1["status"] == "completed"

    # Round 2: explicit slot modification, reuse the round-1 outfit.
    session_context = outfit_context_from_payload(payload1)
    assert session_context["current_item_ids"] == [
        "top-1",
        "bottom-1",
        "coat-1",
        "shoes-1",
    ]
    payload2 = workflow.execute(
        TaskExecutionInput(user_id="u", request="换一件外套"),
        session_context=session_context,
    )
    assert payload2["task_type"] == "outfit_modify"
    assert payload2["result"]["target_slot"] == "outerwear"
    assert payload2["result"]["locked_item_ids"] == ["top-1", "bottom-1", "shoes-1"]
    assert payload2["result"]["alternatives"][0]["item_ids"] == [
        "blazer-1",
        "top-1",
        "bottom-1",
        "shoes-1",
    ]

    # Round 3: slot-less follow-up becomes an overall adjustment.
    next_context = outfit_context_from_payload(payload2)
    assert next_context["current_item_ids"] == [
        "blazer-1",
        "top-1",
        "bottom-1",
        "shoes-1",
    ]
    payload3 = workflow.execute(
        TaskExecutionInput(user_id="u", request="更正式一点"),
        session_context=next_context,
    )
    assert payload3["task_type"] == "outfit_modify"
    assert payload3["route"]["reason"] == "session_follow_up"
    agent1_facts = payload3["agent_outputs"]["agent1"]["facts"]
    assert agent1_facts["adjustment_mode"] == "overall"
    assert agent1_facts["locked_item_ids"] == []
    assert agent1_facts["replaced_item_ids"] == []
    assert payload3["result"]["target_slot"] == ""
    assert payload3["result"]["locked_item_ids"] == []
    assert payload3["result"]["alternatives"][0]["item_ids"] == ["dress-1", "heels-1"]


def test_follow_up_without_session_stays_recommend(tmp_path: Path) -> None:
    database_path = tmp_path / "multiturn.db"
    initialize_database(database_path)
    _seed_wardrobe(database_path)
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=None,
        recommendation_runner=_fake_recommendation_runner,
    )
    # Without session context, a slot-less request is routed as a fresh
    # recommendation rather than being forced into a modification.
    payload = workflow.execute(
        TaskExecutionInput(user_id="u", request="更正式一点")
    )
    assert payload["task_type"] == "outfit_recommend"
