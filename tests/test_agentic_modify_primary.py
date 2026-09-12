"""Stage 4 primary chain: OUTFIT_MODIFY runs the Agent loop as the real chain.

The Harness is the only modify chain (the legacy graph was retired in Stage 2):
the loop outcome is wrapped into the OutfitModifyResult contract, committed to
task_runs, and — when it produces a completed outfit — persisted to
candidate_outfits so a later turn can re-anchor on it by outfit_id
(multi-turn grounding).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from styleforge.models.task import TaskExecutionInput
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.wardrobe_repository import add_items
from styleforge.workflow.task_workflow import MultiTaskWorkflow

from tests.helpers import make_item
from tests.llm.fake_llm import FakeLlm


def _seed(database_path: str) -> None:
    items = [
        make_item("top-1", "top", "白衬衫", "white"),
        make_item("bottom-1", "pants", "黑色西裤", "black"),
        make_item("coat-1", "outwear", "灰色大衣", "gray"),
        make_item("shoes-1", "shoes", "黑色皮鞋", "black"),
        make_item("sneakers-1", "shoes", "白色运动鞋", "white"),
    ]
    with database_session(database_path) as connection:
        upsert_items(connection, items, "test")
        add_items(connection, "u", [item.item_id for item in items])


def _workflow(database_path: str, llm: Any) -> MultiTaskWorkflow:
    return MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=llm,
    )


def _modify_task() -> TaskExecutionInput:
    return TaskExecutionInput(
        user_id="u",
        request="这双鞋不喜欢，帮我换双舒适的运动鞋",
        current_outfit_id="outfit-1",
        current_item_ids=["top-1", "bottom-1", "coat-1", "shoes-1"],
    )


# ── execute() primary end-to-end against a real database ─────────────

# The one tool call the scripted Stylist makes: replace the leather shoes with
# the sneakers, keeping the rest of the outfit (Harness modify contract).
_REPLACE_SHOES = {
    "name": "modify_outfit",
    "arguments": {
        "intent": {
            "message": "这双鞋不喜欢，帮我换双舒适的运动鞋",
            "goal": "把当前皮鞋换成更舒适的运动鞋",
            "requirements": ["保留其他单品", "替换鞋履", "新鞋应舒适且为运动鞋"],
        },
        "plan": {
            "ops": [
                {
                    "action": "replace",
                    "item_id": "shoes-1",
                    "replacement_item_id": "sneakers-1",
                    "placement": {"region": "feet", "layer": "base"},
                }
            ],
            "reasoning": "换运动鞋更舒适",
        }
    },
}


def test_execute_agentic_primary_end_to_end(db_dsn: str) -> None:
    initialize_database(db_dsn)
    _seed(db_dsn)
    llm = FakeLlm(
        [
            # Stylist understands the request and acts in the same model turn.
            ({"decision_summary": "替换皮鞋", "control": "CONTINUE"}, [_REPLACE_SHOES]),
            # Main-Graph critic (chat_json)
            {"approved": True, "issues": [], "feedback": "已换成白色运动鞋"},
            # memory extraction (chat_json, outside the harness proxy)
            {"evidence": []},
        ]
    )
    workflow = _workflow(db_dsn, llm)

    payload = workflow.execute(_modify_task())

    assert payload["task_type"] == "outfit_modify"
    assert payload["status"] == "completed"
    result = payload["result"]
    assert result["status"] == "completed"
    assert result["alternatives"][0]["item_ids"] == [
        "top-1", "bottom-1", "coat-1", "sneakers-1",
    ]
    assert result["replaced_item_ids"] == ["shoes-1"]
    assert result["locked_item_ids"] == ["top-1", "bottom-1", "coat-1"]
    assert result["target_slot"] == "footwear"
    assert result["current_outfit_id"].startswith("outfit-1-mod-")
    # One Stylist semantic/action turn + one Critic turn.
    assert payload["llm_call_count"] == 2
    intent = payload["agentic_outcome"]["user_intent"]
    assert intent.message == _modify_task().request
    assert intent.requirements == [
        "保留其他单品", "替换鞋履", "新鞋应舒适且为运动鞋",
    ]
    assert "【本轮预取候选】" in llm.calls[0]["user"]
    assert "[sneakers-1]" in llm.calls[0]["user"]
    assert "【候选生成快速路径】" in llm.calls[0]["user"]
    modify_schema = next(
        tool.input_schema for tool in llm.calls[0]["tools"]
        if tool.name == "modify_outfit"
    )
    assert set(modify_schema["required"]) == {"intent", "plan"}
    assert payload["semantic_intent"] == intent.model_dump(mode="json")
    # Single outcome rides agentic_outcome as a dict; the StageCandidate entry
    # carries the final item set.
    assert payload["agentic_outcome"]["status"] == "done"
    assert payload["agentic_outcome"]["candidates"][0]["item_ids"] == [
        "top-1", "bottom-1", "coat-1", "sneakers-1",
    ]
    assert payload["selected_subgraph"] == "agentic_harness"
    assert payload["agents"] == {"harness": "styleforge_harness"}

    # The completed candidate is committed so a later turn can re-anchor on it.
    with database_session(db_dsn) as connection:
        rows = connection.execute(
            "SELECT outfit_id, item_ids_json FROM candidate_outfits"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["outfit_id"] == result["alternatives"][0]["outfit_id"]


def test_execute_agentic_primary_ask_user_not_committed(db_dsn: str) -> None:
    # The Stylist suspends for clarification; the Main Graph's ClarificationNode
    # surfaces the question and nothing is committed.
    initialize_database(db_dsn)
    _seed(db_dsn)
    llm = FakeLlm(
        [
            {
                "decision_summary": "缺合适的鞋",
                "control": "NEED_USER",
                "clarification": {
                    "question": "衣橱里没有黑色皮鞋，换棕色短靴可以吗？",
                    "reason": "无合适皮鞋",
                },
            },
            {"evidence": []},  # memory extraction still runs on a suspended run
        ]
    )
    workflow = _workflow(db_dsn, llm)

    payload = workflow.execute(_modify_task())

    assert payload["status"] == "needs_clarification"
    assert payload["result"]["message"] == "衣橱里没有黑色皮鞋，换棕色短靴可以吗？"
    assert payload["result"]["alternatives"] == []
    assert payload["agentic_outcome"]["status"] == "needs_clarification"
    assert payload["agentic_outcome"]["clarification_question"] == (
        "衣橱里没有黑色皮鞋，换棕色短靴可以吗？"
    )
    assert payload["llm_call_count"] == 1
    with database_session(db_dsn) as connection:
        rows = connection.execute("SELECT COUNT(*) AS n FROM candidate_outfits").fetchone()
    assert rows["n"] == 0


def test_execute_agentic_primary_without_web_capability_still_completes(db_dsn: str) -> None:
    # No TAVILY_API_KEY configured (the default): the search_web tool is not
    # even in the Stylist's catalog (Layer 2 capability filtering), so the chain
    # goes straight to the wardrobe and still completes and commits.
    initialize_database(db_dsn)
    _seed(db_dsn)
    llm = FakeLlm(
        [
            ({"decision_summary": "替换皮鞋", "control": "CONTINUE"}, [_REPLACE_SHOES]),
            {"approved": True, "issues": [], "feedback": "已换成白色运动鞋"},
            {"evidence": []},
        ]
    )
    workflow = _workflow(db_dsn, llm)

    payload = workflow.execute(_modify_task())

    assert payload["status"] == "completed"
    assert payload["llm_call_count"] == 2
    assert payload["agentic_outcome"]["status"] == "done"
    assert payload["result"]["alternatives"][0]["item_ids"] == [
        "top-1", "bottom-1", "coat-1", "sneakers-1",
    ]
    with database_session(db_dsn) as connection:
        rows = connection.execute(
            "SELECT outfit_id FROM candidate_outfits"
        ).fetchall()
    assert len(rows) == 1


def test_execute_agentic_primary_tool_then_ask_user_not_committed(db_dsn: str) -> None:
    # The Stylist grounds on a wardrobe search first, then decides it needs
    # clarification — the tool→NEED_USER transition inside one subgraph run is
    # unaffected and nothing is committed.
    initialize_database(db_dsn)
    _seed(db_dsn)
    llm = FakeLlm(
        [
            (
                {"decision_summary": "找鞋", "control": "CONTINUE"},
                [{"name": "search_wardrobe", "arguments": {"query": "皮鞋"}}],
            ),
            {
                "decision_summary": "缺合适的鞋",
                "control": "NEED_USER",
                "clarification": {
                    "question": "衣橱里没有黑色皮鞋，换棕色短靴可以吗？",
                    "reason": "无合适皮鞋",
                },
            },
            {"evidence": []},
        ]
    )
    workflow = _workflow(db_dsn, llm)

    payload = workflow.execute(_modify_task())

    assert payload["status"] == "needs_clarification"
    assert payload["result"]["message"] == "衣橱里没有黑色皮鞋，换棕色短靴可以吗？"
    assert payload["result"]["alternatives"] == []
    assert payload["agentic_outcome"]["status"] == "needs_clarification"
    assert payload["llm_call_count"] == 2  # stylist search + stylist ask
    with database_session(db_dsn) as connection:
        rows = connection.execute("SELECT COUNT(*) AS n FROM candidate_outfits").fetchone()
    assert rows["n"] == 0
