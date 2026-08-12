from __future__ import annotations

from pathlib import Path

from styleforge.models.task import TaskExecutionInput
from styleforge.repositories import preference_evidence_repository
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.preference_model_repository import list_preferences
from styleforge.repositories.wardrobe_repository import add_items, deactivate_all_items
from styleforge.services.chat_service import outfit_context_from_payload
from styleforge.workflow.task_workflow import MultiTaskWorkflow, is_follow_up

from tests.extension_llm import ScriptedExtensionLlm, approved_review, intent_response
from tests.helpers import make_item


def _seed_wardrobe(database_path: str) -> None:
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


def test_session_multiturn_reuses_outfit_context(db_dsn: str) -> None:
    database_path = db_dsn
    initialize_database(database_path)
    _seed_wardrobe(database_path)

    # Every successful execute runs one memory-extraction call afterwards, so
    # each round needs an extraction response before the next round's agents.
    script = [
        # Round 1 is a deterministic recommendation (no LLM); its extraction
        # returns one contextual style claim so persistence is observable.
        {
            "evidence": [
                {
                    "dimension": "style",
                    "attribute": "style",
                    "value": "通勤",
                    "polarity": "positive",
                    "strength": 0.6,
                    "scope": {"type": "contextual", "occasions": ["通勤"]},
                }
            ]
        },
        intent_response("理解换外套请求"),
        ROUND2_MODIFY,
        approved_review(),
        # Round 2 extraction: nothing durable to keep.
        {"evidence": []},
        intent_response("理解整体调整请求"),
        ROUND3_MODIFY,
        approved_review(),
        # Round 3 extraction: nothing durable to keep.
        {"evidence": []},
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
    # The round-1 request distills a contextual style preference into the model.
    with database_session(database_path) as connection:
        memories = list_preferences(connection, "u")
    assert [(m["dimension"], m["attribute"], m["value"]) for m in memories] == [
        ("style", "style", "通勤")
    ]

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


def test_follow_up_without_session_stays_recommend(db_dsn: str) -> None:
    database_path = db_dsn
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


# ---------------------------------------------------------------------------
# M3: multi-turn dialogue with natural, colloquial user input
# ---------------------------------------------------------------------------

ROUND3_MODIFY_BOTTOM = {
    "task_type": "outfit_modify",
    "status": "completed",
    "summary": "已把裤子换成深色西裤",
    "result": {
        "status": "completed",
        "current_outfit_id": "outfit-1",
        "target_slot": "bottom",
        "replaced_item_ids": ["bottom-1"],
        "locked_item_ids": ["blazer-1", "top-1", "shoes-1"],
        "alternatives": [
            {
                "outfit_id": "outfit-2",
                "item_ids": ["blazer-1", "top-1", "dark-pants-2", "shoes-1"],
                "reasoning": "换成深色西裤，保持通勤正式感",
            }
        ],
        "message": "已为你更换裤子",
    },
    "used_item_ids": ["blazer-1", "top-1", "dark-pants-2", "shoes-1"],
    "evidence_source_ids": [],
}


def _seed_with_dark_pants(database_path: str) -> None:
    """Seed the shared wardrobe plus one extra dark pair of trousers."""
    _seed_wardrobe(database_path)
    with database_session(database_path) as connection:
        upsert_items(connection, [make_item("dark-pants-2", "pants", "深灰西裤", "gray")], "test")
        add_items(connection, "u", ["dark-pants-2"])


def test_three_round_modify_chain_keeps_latest_outfit(db_dsn: str) -> None:
    """Four natural rounds: recommend -> swap coat -> swap trousers -> overall.

    The modification chain must reuse the *previous* round's outfit on every
    turn (never fall back to the original plan), and a slot-less follow-up is
    re-routed to an overall adjustment with ``session_follow_up``.
    """
    database_path = db_dsn
    initialize_database(database_path)
    _seed_with_dark_pants(database_path)

    script = [
        {"evidence": []},  # R1 extraction
        intent_response("理解换外套请求"), ROUND2_MODIFY, approved_review(), {"evidence": []},  # R2
        intent_response("理解换裤子请求"), ROUND3_MODIFY_BOTTOM, approved_review(), {"evidence": []},  # R3
        intent_response("理解整体调整请求"), ROUND3_MODIFY, approved_review(), {"evidence": []},  # R4
    ]
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=ScriptedExtensionLlm(script),
        recommendation_runner=_fake_recommendation_runner,
    )

    # R1: colloquial fresh brief.
    payload1 = workflow.execute(
        TaskExecutionInput(user_id="u", request="下周一有个重要客户要见，帮我配一身，别太花哨")
    )
    assert payload1["task_type"] == "outfit_recommend"
    ctx1 = outfit_context_from_payload(payload1)
    assert ctx1["current_item_ids"] == ["top-1", "bottom-1", "coat-1", "shoes-1"]

    # R2: replace the coat.
    payload2 = workflow.execute(
        TaskExecutionInput(user_id="u", request="嗯，外套换件西装吧，这大衣太随意了"),
        session_context=ctx1,
    )
    assert payload2["task_type"] == "outfit_modify"
    assert payload2["result"]["target_slot"] == "outerwear"
    ctx2 = outfit_context_from_payload(payload2)
    assert ctx2["current_item_ids"] == ["blazer-1", "top-1", "bottom-1", "shoes-1"]

    # R3: replace the trousers; must carry the R2 coat (no fallback to R1).
    payload3 = workflow.execute(
        TaskExecutionInput(user_id="u", request="裤子能不能换条深色的"),
        session_context=ctx2,
    )
    assert payload3["task_type"] == "outfit_modify"
    assert payload3["result"]["target_slot"] == "bottom"
    assert payload3["result"]["replaced_item_ids"] == ["bottom-1"]
    facts3 = payload3["agent_outputs"]["agent1"]["facts"]
    assert "blazer-1" in facts3.get("current_item_ids", [])
    assert "coat-1" not in facts3.get("current_item_ids", [])

    # R4: slot-less follow-up becomes an overall adjustment.
    ctx3 = outfit_context_from_payload(payload3)
    payload4 = workflow.execute(
        TaskExecutionInput(user_id="u", request="整体再正式一点点"),
        session_context=ctx3,
    )
    assert payload4["task_type"] == "outfit_modify"
    assert payload4["route"]["reason"] == "session_follow_up"
    assert payload4["agent_outputs"]["agent1"]["facts"]["adjustment_mode"] == "overall"
    assert payload4["result"]["target_slot"] == ""


def _seed_with_loafers(database_path: str) -> None:
    """Seed the shared wardrobe plus one pair of loafers for footwear swaps."""
    _seed_wardrobe(database_path)
    with database_session(database_path) as connection:
        upsert_items(
            connection,
            [make_item("loafers-1", "shoes", "黑色乐福鞋", "black")],
            "test",
        )
        add_items(connection, "u", ["loafers-1"])


ROUND_FOOTWEAR_MODIFY = {
    "task_type": "outfit_modify",
    "status": "completed",
    "summary": "已把高跟鞋换成乐福鞋",
    "result": {
        "status": "completed",
        "current_outfit_id": "outfit-2",
        "target_slot": "footwear",
        "replaced_item_ids": ["heels-1"],
        "locked_item_ids": ["dress-1"],
        "alternatives": [
            {
                "outfit_id": "outfit-3",
                "item_ids": ["dress-1", "loafers-1"],
                "reasoning": "换成乐福鞋，约会更轻松自在",
            }
        ],
        "message": "已为你更换鞋子",
    },
    "used_item_ids": ["dress-1", "loafers-1"],
    "evidence_source_ids": [],
}


def test_mixed_chain_fresh_scene_does_not_rewrite(db_dsn: str) -> None:
    """Recommend -> swap coat -> fresh wedding brief -> overall color adjust.

    A fresh scenario inside an active session must keep its own recommend route
    (never be swallowed by the follow-up rewrite), and its plan becomes the new
    baseline for the next modification instead of the older swapped chain.
    """
    database_path = db_dsn
    initialize_database(database_path)
    _seed_with_loafers(database_path)

    script = [
        {"evidence": []},  # R1 extraction
        intent_response("理解换外套请求"), ROUND2_MODIFY, approved_review(), {"evidence": []},  # R2
        {"evidence": []},  # R3 extraction
        intent_response("理解整体改色请求"), ROUND3_MODIFY, approved_review(), {"evidence": []},  # R4
    ]
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=ScriptedExtensionLlm(script),
        recommendation_runner=_fake_recommendation_runner,
    )

    # R1: plain recommend.
    payload1 = workflow.execute(
        TaskExecutionInput(user_id="u", request="帮我推荐一套通勤搭配")
    )
    ctx1 = outfit_context_from_payload(payload1)
    assert ctx1["current_item_ids"] == ["top-1", "bottom-1", "coat-1", "shoes-1"]

    # R2: the router's new ``换成`` pattern picks up a collar-style swap.
    payload2 = workflow.execute(
        TaskExecutionInput(user_id="u", request="把这件大衣换成西装"),
        session_context=ctx1,
    )
    assert payload2["task_type"] == "outfit_modify"
    assert payload2["result"]["target_slot"] == "outerwear"
    assert payload2["result"]["replaced_item_ids"] == ["coat-1"]
    assert payload2["result"]["locked_item_ids"] == ["top-1", "bottom-1", "shoes-1"]
    ctx2 = outfit_context_from_payload(payload2)
    assert ctx2["current_item_ids"] == ["blazer-1", "top-1", "bottom-1", "shoes-1"]

    # R3: a fresh scenario must NOT be rewritten into a modification.
    payload3 = workflow.execute(
        TaskExecutionInput(user_id="u", request="对了，下周末婚礼穿什么，帮我看看"),
        session_context=ctx2,
    )
    assert payload3["task_type"] == "outfit_recommend"
    assert not is_follow_up("对了，下周末婚礼穿什么，帮我看看")
    # The fresh plan starts over from the deterministic runner; the R2 swap
    # (blazer) must not leak into the wedding plan.
    ctx3 = outfit_context_from_payload(payload3)
    assert ctx3["current_item_ids"] == ["top-1", "bottom-1", "coat-1", "shoes-1"]

    # R4: a follow-up color adjustment applies to the fresh wedding outfit.
    payload4 = workflow.execute(
        TaskExecutionInput(user_id="u", request="这套改成红色系"),
        session_context=ctx3,
    )
    assert payload4["task_type"] == "outfit_modify"
    assert payload4["result"]["target_slot"] == ""
    assert payload4["agent_outputs"]["agent1"]["facts"]["adjustment_mode"] == "overall"


def test_negative_feedback_then_footwear_swap(db_dsn: str) -> None:
    """Recommend -> "这套太严肃了" (overall) -> "换成乐福鞋" (footwear slot).

    Negative feedback is a session_follow_up overall adjustment; the next round
    then swaps the footwear slot while locking the dress — proving the modify
    chain carries the *latest* outfit forward round after round.
    """
    database_path = db_dsn
    initialize_database(database_path)
    _seed_with_loafers(database_path)

    script = [
        {"evidence": []},  # R1 extraction
        intent_response("理解整体严肃度调整"), ROUND3_MODIFY, approved_review(), {"evidence": []},  # R2
        intent_response("理解换乐福鞋"), ROUND_FOOTWEAR_MODIFY, approved_review(), {"evidence": []},  # R3
    ]
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=ScriptedExtensionLlm(script),
        recommendation_runner=_fake_recommendation_runner,
    )

    # R1: recommend for a date.
    payload1 = workflow.execute(
        TaskExecutionInput(user_id="u", request="晚上约会穿什么好")
    )
    ctx1 = outfit_context_from_payload(payload1)
    assert ctx1["current_item_ids"] == ["top-1", "bottom-1", "coat-1", "shoes-1"]

    # R2: negative feedback routes to an overall session follow-up.
    payload2 = workflow.execute(
        TaskExecutionInput(user_id="u", request="这套太严肃了"),
        session_context=ctx1,
    )
    assert payload2["task_type"] == "outfit_modify"
    assert payload2["route"]["reason"] == "session_follow_up"
    assert payload2["result"]["target_slot"] == ""
    assert payload2["agent_outputs"]["agent1"]["facts"]["adjustment_mode"] == "overall"
    ctx2 = outfit_context_from_payload(payload2)
    assert ctx2["current_item_ids"] == ["dress-1", "heels-1"]

    # R3: swap the footwear slot; the dress stays locked.
    payload3 = workflow.execute(
        TaskExecutionInput(user_id="u", request="换成乐福鞋"),
        session_context=ctx2,
    )
    assert payload3["task_type"] == "outfit_modify"
    assert payload3["result"]["target_slot"] == "footwear"
    assert payload3["result"]["replaced_item_ids"] == ["heels-1"]
    assert payload3["result"]["locked_item_ids"] == ["dress-1"]
    assert payload3["result"]["alternatives"][0]["item_ids"] == ["dress-1", "loafers-1"]


def test_follow_up_ignores_stale_session_items(db_dsn: str) -> None:
    """A follow-up whose session outfit items were removed must not force a
    modify with dead item ids — it degrades to a fresh recommendation."""
    database_path = db_dsn
    initialize_database(database_path)
    _seed_wardrobe(database_path)
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=None,
        recommendation_runner=_fake_recommendation_runner,
    )
    payload1 = workflow.execute(
        TaskExecutionInput(user_id="u", request="帮我搭一套上班的")
    )
    ctx1 = outfit_context_from_payload(payload1)
    assert ctx1["current_item_ids"] == ["top-1", "bottom-1", "coat-1", "shoes-1"]

    # 单品从活跃衣橱全部失效（模拟已删单品）。
    with database_session(database_path) as connection:
        deactivate_all_items(connection, "u")

    payload2 = workflow.execute(
        TaskExecutionInput(user_id="u", request="更正式一点"),
        session_context=ctx1,
    )
    assert payload2["task_type"] == "outfit_recommend"


def test_new_intent_with_session_is_not_forced_to_modify(db_dsn: str) -> None:
    """A fresh brief carrying scenario words must keep its own route even when
    a session outfit exists (no false-positive follow-up rewrite)."""
    database_path = db_dsn
    initialize_database(database_path)
    _seed_wardrobe(database_path)
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=None,
        recommendation_runner=_fake_recommendation_runner,
    )
    payload1 = workflow.execute(
        TaskExecutionInput(user_id="u", request="帮我搭一套上班的")
    )
    ctx1 = outfit_context_from_payload(payload1)

    payload2 = workflow.execute(
        TaskExecutionInput(user_id="u", request="对了，下周婚礼穿什么，帮我重新看看"),
        session_context=ctx1,
    )
    assert payload2["task_type"] == "outfit_recommend"


def test_ambiguous_short_follow_up_is_auditable(db_dsn: str) -> None:
    """A very short adjustment like ``别要蓝色`` is a known false-positive
    rewrite to modify — assert the reason is auditable (session_follow_up)."""
    database_path = db_dsn
    initialize_database(database_path)
    _seed_wardrobe(database_path)
    script = [
        {"evidence": []},  # R1 extraction
        # 无槽位调整 → agent1 走 overall；agent2 必须返回整体调整形式，否则硬校验报错。
        intent_response("理解不要蓝色的调整"), ROUND3_MODIFY, approved_review(), {"evidence": []},  # R2
    ]
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=ScriptedExtensionLlm(script),
        recommendation_runner=_fake_recommendation_runner,
    )
    payload1 = workflow.execute(
        TaskExecutionInput(user_id="u", request="配一套休闲点的")
    )
    ctx1 = outfit_context_from_payload(payload1)

    payload2 = workflow.execute(
        TaskExecutionInput(user_id="u", request="别要蓝色"),
        session_context=ctx1,
    )
    assert payload2["task_type"] == "outfit_modify"
    assert payload2["route"]["reason"] == "session_follow_up"
    assert payload2["result"]["target_slot"] == ""


def test_empty_session_context_degrades_to_recommend(db_dsn: str) -> None:
    """A session context with no produced outfit must not rewrite the route."""
    database_path = db_dsn
    initialize_database(database_path)
    _seed_wardrobe(database_path)
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=None,
        recommendation_runner=_fake_recommendation_runner,
    )
    payload = workflow.execute(
        TaskExecutionInput(user_id="u", request="更正式一点"),
        session_context={"current_outfit_id": "", "current_item_ids": []},
    )
    assert payload["task_type"] == "outfit_recommend"


def test_no_llm_skips_memory_extraction_gracefully(db_dsn: str) -> None:
    """Without an LLM client the task still completes and nothing is distilled
    into the memory model (no crash, no fabricated evidence)."""
    database_path = db_dsn
    initialize_database(database_path)
    _seed_wardrobe(database_path)
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=None,
        recommendation_runner=_fake_recommendation_runner,
    )
    payload = workflow.execute(
        TaskExecutionInput(user_id="u", request="帮我搭一套通勤的")
    )
    assert payload["status"] == "completed"
    with database_session(database_path) as connection:
        assert list_preferences(connection, "u") == []
        assert preference_evidence_repository.list_evidence(connection, "u") == []
