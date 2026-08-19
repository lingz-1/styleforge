"""Stage 4 primary chain: OUTFIT_MODIFY runs the Agent loop as the real chain.

When ``modify_mode`` is ``agentic`` the legacy graph (and its shadow mount) is
skipped entirely: the loop outcome is wrapped into the OutfitModifyResult
contract, committed to task_runs, and — when it produces a completed outfit —
persisted to candidate_outfits so a later turn can re-anchor on it by
outfit_id (multi-turn grounding).

These tests opt in with ``modify_mode="agentic"``; the conftest autouse fixture
keeps the legacy chain the default for every other test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from styleforge.models.task import TaskExecutionInput
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.wardrobe_repository import add_items
from styleforge.workflow.task_workflow import MultiTaskWorkflow, _resolve_modify_mode

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


def _workflow(database_path: str, llm: Any, *, modify_mode: str = "agentic") -> MultiTaskWorkflow:
    return MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=llm,
        modify_mode=modify_mode,
    )


def _modify_task() -> TaskExecutionInput:
    return TaskExecutionInput(
        user_id="u",
        request="这双鞋不喜欢，帮我换双舒适的运动鞋",
        current_outfit_id="outfit-1",
        current_item_ids=["top-1", "bottom-1", "coat-1", "shoes-1"],
    )


def _success_outcome() -> dict[str, Any]:
    return {
        "status": "success",
        "intent": {
            "message": "换双舒适的运动鞋",
            "goal": "把皮鞋换成舒适的运动鞋",
            "requirements": ["要舒适"],
        },
        "candidate": {
            "outfit_id": "outfit-1",
            "item_ids": ["top-1", "bottom-1", "coat-1", "sneakers-1"],
        },
        "review": {"approved": True, "issues": [], "feedback": "已换成白色运动鞋"},
        "ask_user": None,
        "steps": [
            {
                "step": 1,
                "action": "modify_outfit",
                "args": {
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
                "observation": "已应用修改",
            }
        ],
        "llm_call_count": 3,
    }


def _ask_user_outcome() -> dict[str, Any]:
    return {
        "status": "ask_user",
        "intent": {"message": "换皮鞋", "goal": "换双皮鞋", "requirements": []},
        "candidate": {
            "outfit_id": "outfit-1",
            "item_ids": ["top-1", "bottom-1", "coat-1", "shoes-1"],
        },
        "review": None,
        "ask_user": {"question": "衣橱里没有黑色皮鞋，换棕色短靴可以吗？"},
        "steps": [],
        "llm_call_count": 1,
    }


# ── modify_mode resolution ───────────────────────────────────────────


def test_resolve_modify_mode_argument_wins_over_env(monkeypatch) -> None:
    monkeypatch.setenv("STYLEFORGE_MODIFY_MODE", "legacy")
    assert _resolve_modify_mode("agentic") == "agentic"
    assert _resolve_modify_mode("shadow") == "shadow"
    assert _resolve_modify_mode(None) == "legacy"


def test_resolve_modify_mode_defaults_to_agentic(monkeypatch) -> None:
    monkeypatch.delenv("STYLEFORGE_MODIFY_MODE", raising=False)
    assert _resolve_modify_mode(None) == "agentic"
    assert _resolve_modify_mode("unknown") == "agentic"


# ── outcome -> result wrapping (pure, no DB) ─────────────────────────


def test_outcome_to_result_success_wraps_candidate(db_dsn: str) -> None:
    workflow = _workflow(db_dsn, None)
    result = workflow._agentic_outcome_to_result(
        _modify_task(), _success_outcome(), {"shoes-1": "shoes", "sneakers-1": "shoes"}
    )

    assert result["status"] == "completed"
    assert result["target_slot"] == "footwear"
    assert result["replaced_item_ids"] == ["shoes-1"]
    assert result["locked_item_ids"] == ["top-1", "bottom-1", "coat-1"]
    assert result["current_outfit_id"].startswith("outfit-1-mod-")
    assert result["alternatives"][0]["item_ids"] == [
        "top-1", "bottom-1", "coat-1", "sneakers-1",
    ]
    assert result["alternatives"][0]["outfit_id"] == result["current_outfit_id"]


def test_outcome_to_result_ask_user_maps_to_clarification(db_dsn: str) -> None:
    workflow = _workflow(db_dsn, None)
    result = workflow._agentic_outcome_to_result(_modify_task(), _ask_user_outcome(), {})

    assert result["status"] == "needs_clarification"
    assert result["message"] == "衣橱里没有黑色皮鞋，换棕色短靴可以吗？"
    assert result["alternatives"] == []


def test_outcome_to_result_timeout_maps_to_infeasible(db_dsn: str) -> None:
    workflow = _workflow(db_dsn, None)
    outcome = {
        "status": "timeout",
        "candidate": {"outfit_id": "outfit-1", "item_ids": []},
        "steps": [{"step": 1, "action": "search_wardrobe", "args": {}, "observation": "x"}],
        "llm_call_count": 8,
    }
    result = workflow._agentic_outcome_to_result(_modify_task(), outcome, {})

    assert result["status"] == "infeasible"
    assert result["alternatives"] == []


def test_outcome_single_item_becomes_clarification(db_dsn: str) -> None:
    # OutfitReference.item_ids requires at least two; a one-item outfit cannot
    # satisfy the contract, so the wrapper surfaces it as a clarification
    # instead of failing the whole run.
    workflow = _workflow(db_dsn, None)
    outcome = dict(_success_outcome())
    outcome["candidate"] = {
        "outfit_id": "outfit-1",
        "item_ids": ["sneakers-1"],
    }
    result = workflow._agentic_outcome_to_result(_modify_task(), outcome, {})

    assert result["status"] == "needs_clarification"
    assert result["alternatives"] == []
    assert result["current_outfit_id"] == "outfit-1"


# ── execute() primary end-to-end against a real database ─────────────

# The one tool call the scripted Stylist makes: replace the leather shoes with
# the sneakers, keeping the rest of the outfit (Harness modify contract).
_REPLACE_SHOES = {
    "name": "modify_outfit",
    "arguments": {
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
            # coordinator → STYLIST handoff
            {"decision_summary": "换运动鞋", "goal": "把皮鞋换成舒适的运动鞋", "next_agent": "STYLIST"},
            # stylist: one modify_outfit call, then the candidate is ready
            ({"decision_summary": "替换皮鞋", "control": "CONTINUE"}, [_REPLACE_SHOES]),
            {"decision_summary": "完成", "control": "CANDIDATE_READY"},
            # Main-Graph critic (chat_json)
            {"approved": True, "issues": [], "feedback": "已换成白色运动鞋"},
            # memory extraction (chat_json, outside the harness proxy)
            {"evidence": []},
        ]
    )
    workflow = _workflow(db_dsn, llm, modify_mode="agentic")

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
    # coordinator + stylist(continue) + stylist(ready) + critic = 4.
    assert payload["llm_call_count"] == 4
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
            {"decision_summary": "换双皮鞋", "goal": "换双皮鞋", "next_agent": "STYLIST"},
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
    workflow = _workflow(db_dsn, llm, modify_mode="agentic")

    payload = workflow.execute(_modify_task())

    assert payload["status"] == "needs_clarification"
    assert payload["result"]["message"] == "衣橱里没有黑色皮鞋，换棕色短靴可以吗？"
    assert payload["result"]["alternatives"] == []
    assert payload["agentic_outcome"]["status"] == "needs_clarification"
    assert payload["agentic_outcome"]["clarification_question"] == (
        "衣橱里没有黑色皮鞋，换棕色短靴可以吗？"
    )
    assert payload["llm_call_count"] == 2  # coordinator + stylist
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
            {"decision_summary": "换运动鞋", "goal": "把皮鞋换成舒适的运动鞋", "next_agent": "STYLIST"},
            ({"decision_summary": "替换皮鞋", "control": "CONTINUE"}, [_REPLACE_SHOES]),
            {"decision_summary": "完成", "control": "CANDIDATE_READY"},
            {"approved": True, "issues": [], "feedback": "已换成白色运动鞋"},
            {"evidence": []},
        ]
    )
    workflow = _workflow(db_dsn, llm, modify_mode="agentic")

    payload = workflow.execute(_modify_task())

    assert payload["status"] == "completed"
    assert payload["llm_call_count"] == 4
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
            {"decision_summary": "换双皮鞋", "goal": "换双皮鞋", "next_agent": "STYLIST"},
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
    workflow = _workflow(db_dsn, llm, modify_mode="agentic")

    payload = workflow.execute(_modify_task())

    assert payload["status"] == "needs_clarification"
    assert payload["result"]["message"] == "衣橱里没有黑色皮鞋，换棕色短靴可以吗？"
    assert payload["result"]["alternatives"] == []
    assert payload["agentic_outcome"]["status"] == "needs_clarification"
    assert payload["llm_call_count"] == 3  # coordinator + stylist search + stylist ask
    with database_session(db_dsn) as connection:
        rows = connection.execute("SELECT COUNT(*) AS n FROM candidate_outfits").fetchone()
    assert rows["n"] == 0
