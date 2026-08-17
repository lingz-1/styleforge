"""ShadowRunner: runs the Agentic loop alongside the legacy path (Stage 2).

Covers the pinned contract points:
  - enabled=False (or missing llm) ⇒ run() returns None and never calls the LLM;
  - the runner freezes ``EnvironmentFacts`` from the *pre-legacy* context pack
    and runs the loop against real database items;
  - the workflow mount runs the shadow only for OUTFIT_MODIFY and only attaches
    ``payload["agentic_shadow"]`` when ``expose_agentic_shadow`` is on;
  - the legacy payload is unchanged by the shadow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from styleforge.agentic.shadow import AgenticShadowRunner
from styleforge.models.context import (
    ContextPack,
    EnvironmentContext,
    OutfitContext,
    RequestContext,
    UserContext,
)
from styleforge.models.task import TaskExecutionInput
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session
from styleforge.repositories.wardrobe_repository import add_items
from styleforge.workflow.task_workflow import MultiTaskWorkflow

from tests.extension_llm import ScriptedExtensionLlm, approved_review, intent_response
from tests.helpers import make_item


def _seed(database_path: str) -> None:
    items = [
        make_item("top-1", "top", "白衬衫", "white"),
        make_item("bottom-1", "pants", "黑色西裤", "black"),
        make_item("coat-1", "outwear", "灰色大衣", "gray"),
        make_item("shoes-1", "shoes", "黑色皮鞋", "black"),
        make_item("blazer-1", "outwear", "深蓝西装外套", "navy"),
        make_item("dress-1", "dress", "黑色连衣裙", "black"),
        make_item("heels-1", "shoes", "黑色高跟鞋", "black"),
        make_item("sneakers-1", "shoes", "白色运动鞋", "white"),
    ]
    with database_session(database_path) as connection:
        upsert_items(connection, items, "test")
        add_items(connection, "u", [item.item_id for item in items])


def _context_pack() -> ContextPack:
    return ContextPack(
        request_context=RequestContext(
            original_request="外套换件西装吧",
            task_type="OUTFIT_MODIFY",
            route_reason="test",
            route_confidence=1.0,
        ),
        outfit_context=OutfitContext(
            current_outfit_id="outfit-1",
            current_item_ids=["top-1", "bottom-1", "coat-1", "shoes-1"],
        ),
        user_context=UserContext(user_id="u"),
        environment_context=EnvironmentContext(weather={"summary": "sunny"}),
    )


def _task_input() -> TaskExecutionInput:
    return TaskExecutionInput(
        user_id="u",
        request="外套换件西装吧",
        current_outfit_id="outfit-1",
        current_item_ids=["top-1", "bottom-1", "coat-1", "shoes-1"],
    )


def _shadow_modify() -> dict[str, Any]:
    return {
        "thought": "（原始推理）",
        "goal": "把灰色大衣换成深蓝西装外套",
        "requirements": ["保留其余单品"],
        "action": "modify_outfit",
        "query": "",
        "outfit_id": "",
        "plan": {
            "ops": [
                {
                    "action": "replace",
                    "item_id": "coat-1",
                    "replacement_item_id": "blazer-1",
                    "placement": {"region": "upper_body", "layer": "outer"},
                    "reason": "",
                }
            ],
            "reasoning": "换西装外套保持通勤正式感",
        },
        "question": "",
    }


def _shadow_finish() -> dict[str, Any]:
    return {
        "thought": "（原始推理）",
        "goal": "把灰色大衣换成深蓝西装外套",
        "requirements": ["保留其余单品"],
        "action": "finish",
        "query": "",
        "outfit_id": "",
        "plan": None,
        "question": "",
    }


_REVIEW_APPROVED = {"approved": True, "issues": [], "feedback": ""}

# Composer response for the legacy OUTFIT_MODIFY chain (same shape as the
# existing modification tests).
_LEGACY_MODIFY = {
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


# ── runner unit behaviour ────────────────────────────────────────────


def test_runner_disabled_returns_none_and_never_calls_llm() -> None:
    llm = ScriptedExtensionLlm([])
    runner = AgenticShadowRunner("sqlite:///unused", llm, enabled=False)
    result = runner.run(_task_input(), _context_pack())
    assert result is None
    assert llm.responses == []


def test_runner_missing_llm_returns_none() -> None:
    runner = AgenticShadowRunner("sqlite:///unused", None, enabled=True)
    assert runner.run(_task_input(), _context_pack()) is None


def test_runner_swallows_errors(db_dsn: str) -> None:
    # A malformed agent step (illegal action) raises inside the loop; the runner
    # must never fail the request it rides along with.
    from styleforge.repositories.database import initialize_database

    initialize_database(db_dsn)
    _seed(db_dsn)
    llm = ScriptedExtensionLlm(
        [
            {
                "thought": "x",
                "goal": "g",
                "requirements": [],
                "action": "not_an_action",
                "query": "",
                "outfit_id": "",
                "plan": None,
                "question": "",
            }
        ]
    )
    runner = AgenticShadowRunner(db_dsn, llm, enabled=True)
    assert runner.run(_task_input(), _context_pack()) is None


# ── runner end-to-end against a real database ───────────────────────


def test_runner_runs_loop_on_real_database(db_dsn: str) -> None:
    from styleforge.repositories.database import initialize_database

    initialize_database(db_dsn)
    _seed(db_dsn)
    llm = ScriptedExtensionLlm(
        [
            _shadow_modify(),
            _shadow_finish(),
            _REVIEW_APPROVED,
        ]
    )
    runner = AgenticShadowRunner(db_dsn, llm, enabled=True)
    outcome = runner.run(_task_input(), _context_pack())

    assert outcome is not None
    assert outcome["status"] == "success"
    assert outcome["candidate"]["item_ids"] == ["top-1", "bottom-1", "blazer-1", "shoes-1"]
    assert outcome["review"]["approved"] is True
    assert outcome["llm_call_count"] == 3
    for step in outcome["steps"]:
        assert "thought" not in str(step)


# ── workflow mount ───────────────────────────────────────────────────


def test_workflow_mount_full_e2e_with_expose(db_dsn: str) -> None:
    workflow = MultiTaskWorkflow(
        database_path=db_dsn,
        knowledge_root=Path("knowledge"),
        llm_client=ScriptedExtensionLlm(
            [
                intent_response("理解换外套请求"),
                _LEGACY_MODIFY,
                approved_review(),
                {"evidence": []},  # memory extraction
                _shadow_modify(),
                _shadow_finish(),
                _REVIEW_APPROVED,
            ]
        ),
        agentic_shadow=True,
        expose_agentic_shadow=True,
    )
    _seed(db_dsn)

    payload = workflow.execute(
        TaskExecutionInput(user_id="u", request="嗯，外套换件西装吧，这大衣太随意了"),
        session_context={
            "current_outfit_id": "outfit-1",
            "current_item_ids": ["top-1", "bottom-1", "coat-1", "shoes-1"],
        },
    )

    # Legacy contract is intact.
    assert payload["task_type"] == "outfit_modify"
    assert payload["agent_outputs"]["agent1"]["task_type"] == "outfit_modify"
    assert payload["result"]["target_slot"] == "outerwear"

    # Shadow outcome rides the payload only because expose is on.
    shadow = payload["agentic_shadow"]
    assert shadow["status"] == "success"
    assert shadow["candidate"]["item_ids"] == ["top-1", "bottom-1", "blazer-1", "shoes-1"]
    assert shadow["review"]["approved"] is True
    for step in shadow["steps"]:
        assert "thought" not in str(step)


def test_workflow_mount_expose_off_hides_shadow_and_freezes_pre_legacy(db_dsn: str) -> None:
    captured: dict[str, Any] = {}

    def _stub_run(task_input: TaskExecutionInput, context_pack: ContextPack, session_context=None) -> dict:
        captured["context_pack"] = context_pack
        return {"status": "success", "stubbed": True}

    workflow = MultiTaskWorkflow(
        database_path=db_dsn,
        knowledge_root=Path("knowledge"),
        llm_client=ScriptedExtensionLlm(
            [
                intent_response("理解换外套请求"),
                _LEGACY_MODIFY,
                approved_review(),
                {"evidence": []},
            ]
        ),
        agentic_shadow=True,
        expose_agentic_shadow=False,
    )
    workflow.agentic_shadow_runner.run = _stub_run  # isolate the mount
    _seed(db_dsn)

    payload = workflow.execute(
        TaskExecutionInput(user_id="u", request="嗯，外套换件西装吧，这大衣太随意了"),
        session_context={
            "current_outfit_id": "outfit-1",
            "current_item_ids": ["top-1", "bottom-1", "coat-1", "shoes-1"],
        },
    )

    assert "agentic_shadow" not in payload
    assert payload["task_type"] == "outfit_modify"
    # The shadow saw the pre-legacy input state: the session's current outfit.
    assert captured["context_pack"].outfit_context.current_outfit_id == "outfit-1"
    assert captured["context_pack"].outfit_context.current_item_ids == [
        "top-1",
        "bottom-1",
        "coat-1",
        "shoes-1",
    ]


def test_workflow_mount_shadow_does_not_run_for_recommend(db_dsn: str) -> None:
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

    workflow = MultiTaskWorkflow(
        database_path=db_dsn,
        knowledge_root=Path("knowledge"),
        llm_client=ScriptedExtensionLlm([{"evidence": []}]),  # memory extraction only
        recommendation_runner=_fake_recommendation_runner,
        agentic_shadow=True,
        expose_agentic_shadow=True,
    )
    workflow.agentic_shadow_runner.run = lambda *a, **k: (_ for _ in ()).throw(  # noqa: E731
        AssertionError("shadow must not run for non-OUTFIT_MODIFY")
    )
    _seed(db_dsn)

    payload = workflow.execute(
        TaskExecutionInput(user_id="u", request="下周一有个重要客户要见，帮我配一身，别太花哨")
    )

    assert payload["task_type"] == "outfit_recommend"
    assert "agentic_shadow" not in payload
