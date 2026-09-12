"""Stage 4c primary chain: the four extension tasks run the Agent loop.

STYLE_ADVICE / ITEM_ADVICE / WARDROBE_COMPATIBILITY / WARDROBE_GAP now execute
through the Multi-Agent Harness (Coordinator → Extension subgraph → closing
node) as the primary chain. The legacy three-agent graph is skipped entirely;
``extension_result`` rides ``agentic_outcome`` so ``payload.result`` keeps the
exact legacy contract shape (the front end never changes).

These tests drive the loop with a scripted FakeLlm; the closing-node drafts are
built from ``analyze_extension_task`` output so they provably satisfy the hard
validators (``validate_task_result`` + ``validate_extension_draft``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from styleforge.llm.client import LlmUnavailable
from styleforge.models.task import CandidateItem, TaskExecutionInput
from styleforge.orchestration.task_router import TaskRouter, TaskType
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.wardrobe_repository import add_items
from styleforge.tools.extension_analysis import analyze_extension_task
from styleforge.workflow.task_workflow import MultiTaskWorkflow

from tests.helpers import make_item
from tests.llm.fake_llm import FakeLlm


KNOWLEDGE_ROOT = Path("knowledge")


def _seed(database_path: str) -> str:
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
        add_items(connection, "u", [item.item_id for item in items])
    return database_path


def _workflow(database_path: str, llm: Any) -> MultiTaskWorkflow:
    return MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=llm,
    )


def _intent(task_input: TaskExecutionInput, goal: str) -> dict[str, Any]:
    return {
        "message": task_input.request,
        "goal": goal,
        "requirements": [],
    }


def _analyzed(database_path: str, task_input: TaskExecutionInput):
    """Deterministic facts the execute side will pre-compute for this input.

    The tests build closing-node drafts from these facts so the scripted LLM
    output provably passes ``validate_extension_draft`` — no guessing at the
    exact candidate scope the analyzer derives from the real wardrobe.
    """
    route = TaskRouter().route(
        task_input.request,
        requested_task_type=task_input.requested_task_type,
    )
    agent1 = analyze_extension_task(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        task_input=task_input,
        route=route,
        context_pack=None,  # _analyze_* discards it; not touched here
    )
    return route, agent1


# ── closing-draft builders (from the deterministic Agent1 facts) ───────────


def _style_result(agent1: Any) -> dict[str, Any]:
    target = agent1.resolved_target or {}
    matches = list((agent1.facts.get("wardrobe_matches") or [])[:4])
    return {
        "status": "completed",
        "title": str(target.get("style") or "风格建议"),
        "summary": "以简约剪裁与中性色为主，突出质感与松弛感。",
        "principles": [
            {
                "title": "简约剪裁",
                "section": "风格建议",
                "content": "以基础款为主，线条干净利落。",
                "description": "减少装饰性元素，靠版型和面料体现质感。",
            }
        ],
        "wardrobe_matches": matches,
        "evidence": [],
        "limitations": [],
    }


def _item_result(agent1: Any) -> dict[str, Any]:
    anchor = dict(agent1.facts.get("anchor_item") or {})
    anchor_id = str(anchor.get("item_id", ""))
    candidate_ids = list(agent1.candidate_item_ids)
    compatible_sel: dict[str, list[dict[str, Any]]] = {}
    for slot, items in (agent1.facts.get("compatible_items_by_slot") or {}).items():
        if items:
            compatible_sel[slot] = [items[0]]
    supporters = [items[0] for items in compatible_sel.values()]
    if not supporters and len(candidate_ids) > 1:
        supporters = [{"item_id": candidate_ids[1]}]
    sample_ids = [anchor_id] + [str(item["item_id"]) for item in supporters]
    return {
        "status": "completed",
        "title": str(anchor.get("name") or "单品搭配"),
        "summary": "以锚点单品为中心，组合衣橱内可搭单品。",
        "anchor_item": anchor,
        "anchor_source": agent1.facts.get("anchor_source") or "wardrobe",
        "compatible_items_by_slot": compatible_sel,
        "wardrobe_matches": list((agent1.facts.get("wardrobe_matches") or [])[:2]),
        "wardrobe_matches_by_slot": {},
        "sample_outfits": [
            {
                "outfit_id": "look-1",
                "item_ids": sample_ids,
                "reasoning": "锚点单品居核心，选择可搭的上下装与鞋履。",
            }
        ],
        "evidence": [],
        "clarification_question": "",
        "limitations": [],
    }


def _compatibility_result(agent1: Any) -> dict[str, Any]:
    facts = agent1.facts
    compatible_sel: dict[str, list[dict[str, Any]]] = {}
    for slot, items in (facts.get("compatible_items_by_slot") or {}).items():
        if items:
            compatible_sel[slot] = [items[0]]
    return {
        "status": "completed",
        "candidate_item": dict(facts.get("candidate_item") or {}),
        "candidate_slot": str(facts.get("candidate_slot") or ""),
        "compatibility_score": 82.0,
        "recommendation": "recommended",
        "recommendation_text": "该新品能与衣橱内多数单品自然组合。",
        "compatible_item_counts": {slot: 1 for slot in compatible_sel},
        "compatible_items_by_slot": compatible_sel,
        "complete_outfit_count": 0,
        "sample_outfits": [],
        "redundancy_score": 18.0,
        "similar_wardrobe_items": list((facts.get("similar_wardrobe_items") or [])[:2]),
        "evidence": [],
        "clarification_question": "",
        "limitations": [],
    }


def _gap_result(agent1: Any) -> dict[str, Any]:
    facts = agent1.facts
    target = agent1.resolved_target or {}
    missing = list(facts.get("missing_elements") or [])
    gaps = [
        {
            "id": str(element.get("id") or f"gap-{index}"),
            "priority": element.get("priority", "medium"),
            "label": str(element.get("label") or element.get("id") or ""),
            "gap_type": "missing",
            "suggestion": str(element.get("suggestion") or ""),
        }
        for index, element in enumerate(missing)
    ]
    return {
        "status": "completed",
        "analysis_mode": target.get("analysis_mode") or "general",
        "target": {"style": target.get("style", "")},
        "wardrobe_item_count": int(facts.get("wardrobe_item_count") or 0),
        "slot_counts": dict(facts.get("slot_counts") or {}),
        "covered_elements": list(facts.get("covered_elements") or []),
        "gaps": gaps,
        "gap_count": len(gaps),
        "summary": "衣橱缺口分析完成。",
        "evidence": [],
        "limitations": [],
    }


# ── end-to-end: each extension task through the Harness ────────────────────


def test_style_advice_primary_end_to_end(db_dsn: str) -> None:
    database_path = _seed(db_dsn)
    task_input = TaskExecutionInput(
        user_id="u",
        request="想要简约耐看的日常穿搭风格建议",
        requested_task_type=TaskType.STYLE_ADVICE,
    )
    route, agent1 = _analyzed(database_path, task_input)
    assert route.task_type is TaskType.STYLE_ADVICE
    llm = FakeLlm(
        [
            {
                "user_intent": {
                    "message": task_input.request,
                    "goal": "获得简约耐看的日常风格建议",
                    "requirements": ["简约", "耐看", "适合日常"],
                },
                "status": "completed",
                "summary": "风格建议",
                "result": _style_result(agent1),
            },
            {"evidence": []},  # memory extraction (outside the harness proxy)
        ]
    )
    workflow = _workflow(database_path, llm)

    payload = workflow.execute(task_input)

    assert payload["task_type"] == "style_advice"
    assert payload["status"] == "completed"
    result = payload["result"]
    assert result["status"] == "completed"
    assert result["title"]
    assert result["principles"]
    assert payload["selected_subgraph"] == "agentic_harness"
    assert payload["agents"] == {"harness": "styleforge_harness"}
    assert payload["llm_call_count"] == 1  # complete facts route straight to closing
    assert payload["agentic_outcome"]["extension_result"]["status"] == "completed"
    assert payload["agentic_outcome"]["user_intent"].requirements == [
        "简约", "耐看", "适合日常",
    ]
    assert payload["semantic_intent"] == {
        "message": task_input.request,
        "goal": "获得简约耐看的日常风格建议",
        "requirements": ["简约", "耐看", "适合日常"],
    }
    assert "结果综合模式" in llm.calls[0]["system"]
    assert "禁止输出 control" in llm.calls[0]["system"]
    assert "【输出 JSON Schema】" in llm.calls[0]["system"]
    assert '"principles"' in llm.calls[0]["system"]
    assert "<STYLEFORGE_RUNTIME_DATA" in llm.calls[0]["user"]
    assert "<STYLEFORGE_USER_REQUEST" in llm.calls[0]["user"]
    # Closing does not need the general ReAct/tool protocol or full wardrobe
    # context. Keep this bound explicit so future prompt growth is visible.
    assert len(llm.calls[0]["system"]) < 8_000


def test_item_advice_primary_end_to_end(db_dsn: str) -> None:
    database_path = _seed(db_dsn)
    task_input = TaskExecutionInput(
        user_id="u",
        request="黑色夹克怎么搭配",
        requested_task_type=TaskType.ITEM_ADVICE,
    )
    route, agent1 = _analyzed(database_path, task_input)
    assert route.task_type is TaskType.ITEM_ADVICE
    assert agent1.facts.get("anchor_item")  # deterministic anchor resolved from wardrobe
    llm = FakeLlm(
        [
            {
                "user_intent": _intent(task_input, "获得黑色夹克的搭配建议"),
                "status": "completed",
                "summary": "单品搭配建议",
                "result": _item_result(agent1),
            },
            {"evidence": []},
        ]
    )
    workflow = _workflow(database_path, llm)

    payload = workflow.execute(task_input)

    assert payload["task_type"] == "item_advice"
    assert payload["status"] == "completed"
    result = payload["result"]
    assert result["status"] == "completed"
    assert result["anchor_source"] == "wardrobe"
    anchor_id = result["anchor_item"]["item_id"]
    assert result["sample_outfits"][0]["item_ids"][0] == anchor_id
    assert result["sample_outfits"][0]["item_ids"][0] in result["sample_outfits"][0]["item_ids"]
    assert payload["llm_call_count"] == 1  # direct closing; no coordinator planning
    assert payload["agentic_outcome"]["extension_result"]["status"] == "completed"


def test_item_advice_closing_uses_grounded_fallback_after_false_infeasible(
    db_dsn: str,
) -> None:
    database_path = _seed(db_dsn)
    task_input = TaskExecutionInput(
        user_id="u",
        request="黑色夹克怎么搭配",
        requested_task_type=TaskType.ITEM_ADVICE,
    )
    route, agent1 = _analyzed(database_path, task_input)
    assert route.task_type is TaskType.ITEM_ADVICE
    assert agent1.facts.get("anchor_item")
    assert any((agent1.facts.get("compatible_items_by_slot") or {}).values())
    false_infeasible = {
        "user_intent": _intent(task_input, "获得黑色夹克的搭配建议"),
        "status": "infeasible",
        "summary": "无法搭配",
        "result": {
            "status": "infeasible",
            "title": "单品搭配",
            "summary": "无法搭配",
        },
    }
    llm = FakeLlm(
        [
            false_infeasible,
            {"evidence": []},
        ]
    )
    payload = _workflow(database_path, llm).execute(task_input)

    assert payload["status"] == "completed"
    assert payload["llm_call_count"] == 1
    assert payload["result"]["generation_mode"] == "deterministic_grounded_fallback"
    assert payload["result"]["sample_outfits"]
    anchor_id = agent1.facts["anchor_item"]["item_id"]
    assert all(
        anchor_id in outfit["item_ids"]
        for outfit in payload["result"]["sample_outfits"]
    )
    assert "黑色夹克怎么搭配" in payload["result"]["summary"]
    assert all(
        "未知颜色或材质不作推断" in outfit["reasoning"]
        for outfit in payload["result"]["sample_outfits"]
    )


def test_item_advice_repairs_missing_llm_intent_before_grounded_result(
    db_dsn: str,
) -> None:
    database_path = _seed(db_dsn)
    task_input = TaskExecutionInput(
        user_id="u",
        request="黑色夹克怎么搭配",
        requested_task_type=TaskType.ITEM_ADVICE,
    )
    _, agent1 = _analyzed(database_path, task_input)
    result = _item_result(agent1)
    llm = FakeLlm(
        [
            {"status": "completed", "summary": "缺少意图", "result": result},
            {
                "user_intent": _intent(task_input, "获得黑色夹克的搭配建议"),
                "status": "completed",
                "summary": "单品搭配建议",
                "result": result,
            },
            {"evidence": []},
        ]
    )

    payload = _workflow(database_path, llm).execute(task_input)

    assert payload["status"] == "completed"
    assert payload["llm_call_count"] == 2
    assert payload["semantic_intent"]["goal"] == "获得黑色夹克的搭配建议"
    assert payload["agentic_outcome"]["extension_validation_failures"] == [
        "missing_user_intent"
    ]


def test_wardrobe_compatibility_primary_end_to_end(db_dsn: str) -> None:
    database_path = _seed(db_dsn)
    task_input = TaskExecutionInput(
        user_id="u",
        request="评估这件棕色皮夹克是否适合我的衣橱",
        requested_task_type=TaskType.WARDROBE_COMPATIBILITY,
        candidate_item=CandidateItem(
            item_id="candidate-preview",
            name="brown leather jacket",
            item_type="outwear",
            color="brown",
            subtype="jacket",
        ),
    )
    route, agent1 = _analyzed(database_path, task_input)
    assert route.task_type is TaskType.WARDROBE_COMPATIBILITY
    draft = _compatibility_result(agent1)
    draft["clarification_question"] = None
    draft["candidate_slot"] = "wrong-slot"
    draft["compatible_items_by_slot"] = {
        "one_piece": [{"item_id": "candidate-preview", "name": "transient"}]
    }
    llm = FakeLlm(
        [
            {
                "user_intent": _intent(task_input, "评估棕色皮夹克与衣橱的兼容性"),
                "status": "completed",
                "summary": "兼容性评估",
                "result": draft,
            },
            {"evidence": []},
        ]
    )
    workflow = _workflow(database_path, llm)

    payload = workflow.execute(task_input)

    assert payload["task_type"] == "wardrobe_compatibility"
    assert payload["status"] == "completed"
    result = payload["result"]
    assert result["status"] == "completed"
    assert result["recommendation"] in ("recommended", "consider", "not_recommended")
    assert 0 <= result["compatibility_score"] <= 100
    assert result["candidate_item"]["item_id"] == "candidate-preview"
    assert result["candidate_slot"] == agent1.facts["candidate_slot"]
    assert all(
        item["item_id"] != "candidate-preview"
        for items in result["compatible_items_by_slot"].values()
        for item in items
    )
    assert payload["llm_call_count"] == 1
    assert payload["agentic_outcome"]["extension_result"]["status"] == "completed"


def test_wardrobe_gap_primary_end_to_end(db_dsn: str) -> None:
    database_path = _seed(db_dsn)
    task_input = TaskExecutionInput(
        user_id="u",
        request="分析我的衣橱缺少哪些风格单品",
        requested_task_type=TaskType.WARDROBE_GAP,
    )
    route, agent1 = _analyzed(database_path, task_input)
    assert route.task_type is TaskType.WARDROBE_GAP
    draft = _gap_result(agent1)
    draft["wardrobe_item_count"] = 999
    draft["slot_counts"] = {"hallucinated": 999}
    llm = FakeLlm(
        [
            {
                "user_intent": _intent(task_input, "分析衣橱缺失的风格单品"),
                "status": "completed",
                "summary": "衣橱缺口分析",
                "result": draft,
            },
            {"evidence": []},
        ]
    )
    workflow = _workflow(database_path, llm)

    payload = workflow.execute(task_input)

    assert payload["task_type"] == "wardrobe_gap"
    assert payload["status"] == "completed"
    result = payload["result"]
    assert result["status"] == "completed"
    assert result["gap_count"] == len(result["gaps"])
    assert result["analysis_mode"] in ("targeted", "general")
    assert result["wardrobe_item_count"] == agent1.facts["wardrobe_item_count"]
    assert result["slot_counts"] == agent1.facts["slot_counts"]
    if result["gap_count"]:
        assert "结构性缺口" in result["summary"]
    else:
        assert "未发现结构性缺口" in result["summary"]
    assert payload["llm_call_count"] == 1
    assert payload["agentic_outcome"]["extension_result"]["status"] == "completed"


def test_formal_dinner_gap_has_deterministic_missing_elements(db_dsn: str) -> None:
    database_path = _seed(db_dsn)
    task_input = TaskExecutionInput(
        user_id="u",
        request="如果下个月参加正式晚宴，这个衣柜还缺什么？",
        requested_task_type=TaskType.WARDROBE_GAP,
    )

    _, agent1 = _analyzed(database_path, task_input)

    assert agent1.resolved_target["analysis_mode"] == "targeted"
    assert agent1.facts["missing_elements"]
    assert any(
        element["id"] == "occasion:formal_main"
        for element in agent1.facts["missing_elements"]
    )


# ── clarification + no-LLM paths ───────────────────────────────────────────


def test_item_advice_unresolved_anchor_ships_clarification(db_dsn: str) -> None:
    # The deterministic facts flag needs_clarification (no resolvable anchor);
    # the closing node still performs the unified LLM semantic pass before its
    # contract-shaped clarification fallback.
    database_path = _seed(db_dsn)
    task_input = TaskExecutionInput(
        user_id="u",
        request="这件单品怎么搭配",
        requested_task_type=TaskType.ITEM_ADVICE,
    )
    route, agent1 = _analyzed(database_path, task_input)
    assert route.task_type is TaskType.ITEM_ADVICE
    assert agent1.needs_clarification is True
    llm = FakeLlm(
        [
            {
                "decision_summary": "识别为单品建议任务",
                "goal": "给出单品搭配建议",
                "intent": _intent(task_input, "给出单品搭配建议"),
                "next_agent": "EXTENSION",
            },
            {
                "decision_summary": "需要澄清",
                "control": "READY",
                "intent": _intent(task_input, "给出单品搭配建议"),
            },
            {
                "user_intent": _intent(task_input, "给出单品搭配建议"),
                "status": "needs_clarification",
                "summary": "需要确认单品",
                "result": {},
            },
            {"evidence": []},
        ]
    )
    workflow = _workflow(database_path, llm)

    payload = workflow.execute(task_input)

    assert payload["status"] == "needs_clarification"
    assert payload["result"]["status"] == "needs_clarification"
    assert payload["result"]["clarification_question"]
    assert payload["agentic_outcome"]["extension_result"]["status"] == "needs_clarification"
    # coordinator + extension(READY) + semantic closing pass.
    assert payload["llm_call_count"] == 3


def test_extension_without_llm_raises(db_dsn: str) -> None:
    # Matches the legacy ``run_extension``: no model → LlmUnavailable (503),
    # never a deterministic fallback. The failed run is still persisted so a
    # no-key request leaves an honest task_runs record (legacy contract).
    database_path = _seed(db_dsn)
    workflow = _workflow(database_path, None)

    with pytest.raises(LlmUnavailable, match="OUTFIT extension tasks require an LLM client"):
        workflow.execute(
            TaskExecutionInput(
                user_id="u",
                request="我的衣橱适合什么风格",
                requested_task_type=TaskType.STYLE_ADVICE,
            )
        )

    with database_session(database_path) as connection:
        stored = connection.execute(
            "SELECT status, error_message FROM task_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert stored["status"] == "failed"
    assert "OUTFIT extension tasks require an LLM client" in stored["error_message"]
