"""Multi-outfit modification: an OUTFIT_MODIFY without an explicit outfit choice
applies to every recent recommendation candidate ("modify all three").

When ``current_outfit_id`` is empty the session context may carry the whole
recommendation batch (``current_candidates``); ``_agentic_targets`` expands it
into one target per candidate, each runs its own ``StyleForgeHarness.invoke``
over that snapshot as ``base_draft``, and the batch of outcomes is wrapped into
a single OutfitModifyResult with one alternative per success. An explicit
``current_outfit_id`` keeps Stage 4's single-outfit path.

Tests here opt in with ``modify_mode="agentic"`` and either exercise the pure
wrappers or drive the harness with a scripted ``FakeLlm``; the conftest autouse
fixture keeps the legacy chain the default for every other test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from styleforge.models.task import TaskExecutionInput
from styleforge.orchestration.task_router import TaskType
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.wardrobe_repository import add_items
from styleforge.services.chat_service import outfit_context_from_payload
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


def _workflow(database_path: str, llm: Any | None) -> MultiTaskWorkflow:
    return MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=llm,
        modify_mode="agentic",
    )


def _no_outfit_task() -> TaskExecutionInput:
    # No explicit outfit choice: the whole recommendation batch is the target.
    return TaskExecutionInput(
        user_id="u",
        request="太正式了,休闲一点",
        current_outfit_id="",
        current_item_ids=[],
    )


def _targets(*candidates: tuple[str, list[str]]) -> list[dict[str, Any]]:
    return [
        {"outfit_id": outfit_id, "item_ids": list(item_ids)}
        for outfit_id, item_ids in candidates
    ]


def _success_outcome(
    outfit_id: str,
    item_ids: list[str],
    *,
    feedback: str = "已按要求修改",
    llm_calls: int = 2,
) -> dict[str, Any]:
    return {
        "status": "success",
        "intent": {"message": "太正式了", "goal": "改休闲", "requirements": []},
        "candidate": {"outfit_id": outfit_id, "item_ids": item_ids},
        "review": {"approved": True, "issues": [], "feedback": feedback},
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
                        ]
                    }
                },
                "observation": "已应用修改",
            }
        ],
        "llm_call_count": llm_calls,
    }


def _timeout_outcome(outfit_id: str) -> dict[str, Any]:
    return {
        "status": "timeout",
        "intent": {"message": "太正式了", "goal": "改休闲", "requirements": []},
        "candidate": {"outfit_id": outfit_id, "item_ids": []},
        "review": None,
        "ask_user": None,
        "steps": [{"step": 1, "action": "search_wardrobe", "args": {}, "observation": "x"}],
        "llm_call_count": 8,
    }


def _ask_user_outcome(outfit_id: str) -> dict[str, Any]:
    return {
        "status": "ask_user",
        "intent": {"message": "太正式了", "goal": "改休闲", "requirements": []},
        "candidate": {"outfit_id": outfit_id, "item_ids": []},
        "review": None,
        "ask_user": {"question": "衣橱里没有合适的，换一种风格可以吗？"},
        "steps": [],
        "llm_call_count": 1,
    }


# ── _agentic_targets (pure) ─────────────────────────────────────────


def test_targets_explicit_outfit_wins(db_dsn: str) -> None:
    workflow = _workflow(db_dsn, None)
    task = TaskExecutionInput(
        user_id="u",
        request="太正式了,休闲一点",
        current_outfit_id="outfit-1",
        current_item_ids=["top-1", "bottom-1", "coat-1", "shoes-1"],
    )
    targets = workflow._agentic_targets(task, {"current_candidates": _targets(("a", []), ("b", []))})

    assert targets == [
        {
            "outfit_id": "outfit-1",
            "item_ids": ["top-1", "bottom-1", "coat-1", "shoes-1"],
        }
    ]


def test_targets_session_candidates_expand(db_dsn: str) -> None:
    workflow = _workflow(db_dsn, None)
    session = {"current_candidates": _targets(("a", ["x", "y"]), ("b", ["z", "w"]), ("c", ["m", "n"]))}

    targets = workflow._agentic_targets(_no_outfit_task(), session)

    assert len(targets) == 3
    assert targets[0]["outfit_id"] == "a"
    assert targets[2]["outfit_id"] == "c"


def test_targets_single_candidate_degrades(db_dsn: str) -> None:
    workflow = _workflow(db_dsn, None)
    targets = workflow._agentic_targets(
        _no_outfit_task(), {"current_candidates": _targets(("a", ["x", "y"]))}
    )

    assert len(targets) == 1
    assert targets[0]["outfit_id"] == "a"


def test_targets_no_session_degrades_to_empty(db_dsn: str) -> None:
    workflow = _workflow(db_dsn, None)
    targets = workflow._agentic_targets(_no_outfit_task(), None)

    assert len(targets) == 1
    assert targets[0]["outfit_id"] == ""
    assert targets[0]["item_ids"] == []


# ── _agentic_outcomes_to_result (pure) ──────────────────────────────


def _targets_for_outcomes() -> list[dict[str, Any]]:
    return _targets(("a", ["x", "y", "z"]), ("b", ["p", "q", "r"]))


def test_outcomes_all_success_completed(db_dsn: str) -> None:
    workflow = _workflow(db_dsn, None)
    result = workflow._agentic_outcomes_to_result(
        _no_outfit_task(),
        _targets_for_outcomes(),
        [_success_outcome("a", ["x", "y", "z"]), _success_outcome("b", ["p", "q", "r"])],
        {"shoes-1": "shoes", "sneakers-1": "shoes"},
    )

    assert result["status"] == "completed"
    assert len(result["alternatives"]) == 2
    assert result["alternatives"][0]["outfit_id"].startswith("a-mod-")
    assert result["alternatives"][1]["outfit_id"].startswith("b-mod-")
    assert result["alternatives"][0]["outfit_id"] != result["alternatives"][1]["outfit_id"]
    assert result["current_outfit_id"] == result["alternatives"][0]["outfit_id"]
    assert result["message"] == "已按你的要求修改 2 套"
    # Top-level replaced/locked merge across every alternative.
    assert result["replaced_item_ids"] == ["shoes-1", "shoes-1"]
    assert result["locked_item_ids"] == ["x", "y", "z", "p", "q", "r"]


def test_outcomes_mixed_completed_with_partial(db_dsn: str) -> None:
    workflow = _workflow(db_dsn, None)
    result = workflow._agentic_outcomes_to_result(
        _no_outfit_task(),
        _targets_for_outcomes(),
        [_success_outcome("a", ["x", "y", "z"]), _timeout_outcome("b")],
        {},
    )

    assert result["status"] == "completed"
    assert len(result["alternatives"]) == 1
    assert result["message"] == "已按你的要求修改 1 套"


def test_outcomes_all_timeout_infeasible(db_dsn: str) -> None:
    workflow = _workflow(db_dsn, None)
    result = workflow._agentic_outcomes_to_result(
        _no_outfit_task(),
        _targets_for_outcomes(),
        [_timeout_outcome("a"), _timeout_outcome("b")],
        {},
    )

    assert result["status"] == "infeasible"
    assert result["alternatives"] == []


def test_outcomes_all_ask_user_clarification(db_dsn: str) -> None:
    workflow = _workflow(db_dsn, None)
    result = workflow._agentic_outcomes_to_result(
        _no_outfit_task(),
        _targets_for_outcomes(),
        [_ask_user_outcome("a"), _timeout_outcome("b")],
        {},
    )

    assert result["status"] == "needs_clarification"
    assert result["message"] == "衣橱里没有合适的，换一种风格可以吗？"
    assert result["alternatives"] == []


def test_outcomes_undersized_candidate_skipped(db_dsn: str) -> None:
    workflow = _workflow(db_dsn, None)
    undersized = _success_outcome("a", ["only-one"])
    result = workflow._agentic_outcomes_to_result(
        _no_outfit_task(),
        _targets_for_outcomes(),
        [undersized, _success_outcome("b", ["p", "q", "r"])],
        {},
    )

    assert result["status"] == "completed"
    assert len(result["alternatives"]) == 1
    assert result["alternatives"][0]["outfit_id"].startswith("b-mod-")
    assert result["message"] == "已修改 1 套，另有 1 套候选单品过少已跳过"


def test_outcomes_single_delegates_to_single_wrapper(db_dsn: str) -> None:
    # One outcome (no explicit choice but a single candidate) keeps the exact
    # Stage 4 single-outfit behaviour, including its own outfit_id.
    workflow = _workflow(db_dsn, None)
    single = _success_outcome("a", ["x", "y", "z", "w"])
    result = workflow._agentic_outcomes_to_result(
        _no_outfit_task(),
        _targets(("a", ["x", "y", "z", "w"])),
        [single],
        {"shoes-1": "shoes", "sneakers-1": "shoes"},
    )

    assert result["status"] == "completed"
    assert len(result["alternatives"]) == 1
    assert result["current_outfit_id"] == result["alternatives"][0]["outfit_id"]


# ── outfit_context_from_payload keeps the full batch (pure) ──────────


def test_recommend_context_keeps_all_candidates() -> None:
    payload = {
        "task_type": TaskType.OUTFIT_RECOMMEND.value,
        "result": {
            "recommendations": [
                {"outfit_id": "outfit_001", "item_ids": ["a", "b"]},
                {"outfit_id": "outfit_002", "item_ids": ["c", "d"]},
                {"outfit_id": "outfit_003", "item_ids": ["e", "f"]},
            ]
        },
    }

    context = outfit_context_from_payload(payload)

    assert context["current_outfit_id"] == "outfit_001"
    assert context["current_item_ids"] == ["a", "b"]
    assert context["current_candidates"] == [
        {"outfit_id": "outfit_001", "item_ids": ["a", "b"]},
        {"outfit_id": "outfit_002", "item_ids": ["c", "d"]},
        {"outfit_id": "outfit_003", "item_ids": ["e", "f"]},
    ]


def test_modify_context_keeps_alternatives() -> None:
    payload = {
        "task_type": TaskType.OUTFIT_MODIFY.value,
        "request": "太正式了,休闲一点",
        "result": {
            "current_outfit_id": "outfit_001-mod-abc123",
            "alternatives": [
                {"outfit_id": "outfit_001-mod-abc123", "item_ids": ["a", "b"]},
                {"outfit_id": "outfit_001-mod-def456", "item_ids": ["c", "d"]},
            ],
        },
    }

    context = outfit_context_from_payload(payload)

    assert context["current_outfit_id"] == "outfit_001-mod-abc123"
    assert context["current_candidates"] == [
        {"outfit_id": "outfit_001-mod-abc123", "item_ids": ["a", "b"]},
        {"outfit_id": "outfit_001-mod-def456", "item_ids": ["c", "d"]},
    ]


# ── execute() multi-target end-to-end against a real database ────────

# Per-target modify tool call. Each target grounds on its OWN base snapshot:
# outfit-a swaps the leather shoes for sneakers, outfit-b does the reverse.
_MODIFY_TO_SNEAKERS = {
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
            "reasoning": "换运动鞋更休闲",
        }
    },
}
_MODIFY_TO_SHOES = {
    "name": "modify_outfit",
    "arguments": {
        "plan": {
            "ops": [
                {
                    "action": "replace",
                    "item_id": "sneakers-1",
                    "replacement_item_id": "shoes-1",
                    "placement": {"region": "feet", "layer": "base"},
                }
            ],
            "reasoning": "换皮鞋更正式",
        }
    },
}


def test_execute_multi_targets_runs_one_harness_per_candidate(db_dsn: str) -> None:
    initialize_database(db_dsn)
    _seed(db_dsn)
    llm = FakeLlm(
        [
            # target outfit-a: shoes-1 → sneakers-1
            {"decision_summary": "换鞋", "goal": "改休闲", "next_agent": "STYLIST"},
            ({"decision_summary": "换运动鞋", "control": "CONTINUE"}, [_MODIFY_TO_SNEAKERS]),
            {"decision_summary": "完成", "control": "CANDIDATE_READY"},
            {"approved": True, "issues": [], "feedback": "已按要求修改"},  # critic a
            # target outfit-b: sneakers-1 → shoes-1
            {"decision_summary": "换鞋", "goal": "改休闲", "next_agent": "STYLIST"},
            ({"decision_summary": "换皮鞋", "control": "CONTINUE"}, [_MODIFY_TO_SHOES]),
            {"decision_summary": "完成", "control": "CANDIDATE_READY"},
            {"approved": True, "issues": [], "feedback": "已按要求修改"},  # critic b
            {"evidence": []},  # memory extraction
        ]
    )
    workflow = _workflow(db_dsn, llm)
    session_context = {
        "current_outfit_id": "outfit-a",
        "current_item_ids": ["top-1", "bottom-1", "coat-1", "shoes-1"],
        "current_candidates": _targets(
            ("outfit-a", ["top-1", "bottom-1", "coat-1", "shoes-1"]),
            ("outfit-b", ["top-1", "bottom-1", "coat-1", "sneakers-1"]),
        ),
    }

    payload = workflow.execute(_no_outfit_task(), session_context=session_context)

    assert payload["status"] == "completed"
    result = payload["result"]
    assert result["status"] == "completed"
    assert len(result["alternatives"]) == 2
    assert result["alternatives"][0]["outfit_id"].startswith("outfit-a-mod-")
    assert result["alternatives"][1]["outfit_id"].startswith("outfit-b-mod-")
    # Each harness ran against its own base snapshot — the item sets diverge.
    assert result["alternatives"][0]["item_ids"] == [
        "top-1", "bottom-1", "coat-1", "sneakers-1",
    ]
    assert result["alternatives"][1]["item_ids"] == [
        "top-1", "bottom-1", "coat-1", "shoes-1",
    ]
    # Two harnesses × (coordinator + stylist×2 + critic) = 8.
    assert payload["llm_call_count"] == 8
    # Multi-target batch rides agentic_outcome as a list — one outcome per target.
    assert isinstance(payload["agentic_outcome"], list)
    assert [o["status"] for o in payload["agentic_outcome"]] == ["done", "done"]
    # Both completed alternatives are committed for multi-turn re-anchoring.
    with database_session(db_dsn) as connection:
        rows = connection.execute(
            "SELECT outfit_id FROM candidate_outfits ORDER BY rank"
        ).fetchall()
    assert [r["outfit_id"] for r in rows] == [
        result["alternatives"][0]["outfit_id"],
        result["alternatives"][1]["outfit_id"],
    ]


def test_execute_explicit_outfit_keeps_single_target(db_dsn: str) -> None:
    # An explicit current_outfit_id must not be expanded by session candidates:
    # exactly one harness runs, exactly one alternative survives.
    initialize_database(db_dsn)
    _seed(db_dsn)
    llm = FakeLlm(
        [
            {"decision_summary": "换鞋", "goal": "改休闲", "next_agent": "STYLIST"},
            ({"decision_summary": "换运动鞋", "control": "CONTINUE"}, [_MODIFY_TO_SNEAKERS]),
            {"decision_summary": "完成", "control": "CANDIDATE_READY"},
            {"approved": True, "issues": [], "feedback": "已按要求修改"},
            {"evidence": []},
        ]
    )
    workflow = _workflow(db_dsn, llm)
    task = TaskExecutionInput(
        user_id="u",
        request="太正式了,休闲一点",
        current_outfit_id="outfit-1",
        current_item_ids=["top-1", "bottom-1", "coat-1", "shoes-1"],
    )
    session_context = {
        "current_candidates": _targets(("a", ["x", "y"]), ("b", ["z", "w"]))
    }

    payload = workflow.execute(task, session_context=session_context)

    assert payload["status"] == "completed"
    assert len(payload["result"]["alternatives"]) == 1
    assert payload["result"]["alternatives"][0]["outfit_id"].startswith("outfit-1-mod-")
    assert payload["llm_call_count"] == 4
