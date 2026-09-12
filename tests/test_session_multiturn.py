"""Multi-turn session propagation over the agentic primary chains.

The session contract (recommend -> follow-up modify -> follow-up modify) now
runs entirely through the Multi-Agent Harness: a recommendation produces
candidate outfits persisted for re-anchoring, and a follow-up modification
without an explicit outfit choice expands to "modify all three" (one Harness
run per recent candidate, ``_agentic_targets``). Round-1 recommendations are
harness-driven with a scripted ``FakeLlm`` so the candidate item sets are
deterministic; the no-LLM path (``_deterministic_recommend``) covers the
degrade-to-recommend cases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from styleforge.models.task import TaskExecutionInput
from styleforge.repositories import preference_evidence_repository
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.preference_model_repository import list_preferences
from styleforge.repositories.wardrobe_repository import add_items, deactivate_all_items
from styleforge.services.chat_service import outfit_context_from_payload
from styleforge.workflow.task_workflow import MultiTaskWorkflow, is_follow_up

from tests.helpers import make_item
from tests.llm.fake_llm import FakeLlm


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


def _workflow(database_path: str, llm: Any | None) -> MultiTaskWorkflow:
    return MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=llm,
    )


# ── Harness script builders ────────────────────────────────────────────────

# (region, layer) per seed item — the Environment would resolve placement from
# structure facts anyway; explicit values keep the plans self-documenting.
_PLACEMENT: dict[str, tuple[str, str]] = {
    "top-1": ("upper_body", "base"),
    "bottom-1": ("lower_body", "base"),
    "coat-1": ("upper_body", "outer"),
    "blazer-1": ("upper_body", "outer"),
    "shoes-1": ("feet", "base"),
    "heels-1": ("feet", "base"),
    "loafers-1": ("feet", "base"),
    "dress-1": ("full_body", "base"),
    "dark-pants-2": ("lower_body", "base"),
}

_APPROVE = {"approved": True, "issues": [], "feedback": "方案合理"}


def _add_plan(*item_ids: str) -> dict[str, Any]:
    """One modify_outfit plan building an outfit from the empty base."""
    ops = []
    for item_id in item_ids:
        region, layer = _PLACEMENT[item_id]
        ops.append(
            {
                "action": "add",
                "item_id": item_id,
                "placement": {"region": region, "layer": layer},
            }
        )
    return {
        "intent": {
            "message": "推荐穿搭",
            "goal": "从现有衣橱组合完整穿搭",
            "requirements": ["只使用衣橱中的真实单品"],
        },
        "plan": {"ops": ops, "reasoning": "组合完整搭配"},
    }


def _replace_plan(item_id: str, replacement_item_id: str) -> dict[str, Any]:
    """Swap one item for another, keeping the rest of the outfit."""
    region, layer = _PLACEMENT[replacement_item_id]
    return {
        "plan": {
            "ops": [
                {
                    "action": "replace",
                    "item_id": item_id,
                    "replacement_item_id": replacement_item_id,
                    "placement": {"region": region, "layer": layer},
                }
            ],
            "reasoning": "按用户要求替换单品",
        }
    }


def _rebuild_plan(remove_ids: list[str], add_ids: list[str]) -> dict[str, Any]:
    """Full rebuild: remove the base items, then add the target set."""
    ops = [{"action": "remove", "item_id": item_id} for item_id in remove_ids]
    for item_id in add_ids:
        region, layer = _PLACEMENT[item_id]
        ops.append(
            {
                "action": "add",
                "item_id": item_id,
                "placement": {"region": region, "layer": layer},
            }
        )
    return {"plan": {"ops": ops, "reasoning": "整体调整搭配"}}


def _recommend_block(
    goal: str,
    item_ids: list[str],
    feedback: str = "方案合理",
) -> list[Any]:
    """One recommend round: three fast Stylist + Critic candidate cycles.

    The Main-Graph goal gate demands three candidates (``target_candidates=3``
    in ``_run_agentic_recommend``); all three are scripted to the same item set
    so a later "modify all three" can share one tool call per target.
    """
    block: list[Any] = []
    arguments = _add_plan(*item_ids)
    arguments["intent"] = {
        "message": goal,
        "goal": goal,
        "requirements": ["只使用衣橱中的真实单品"],
    }
    tool = {"name": "modify_outfit", "arguments": arguments}
    for _ in range(3):
        block.extend(
            [
                ({"decision_summary": "组合候选", "control": "CONTINUE"}, [tool]),
                {**_APPROVE, "feedback": feedback},
            ]
        )
    return block


def _modify_block(
    goal: str,
    tool_args: dict[str, Any],
    feedback: str = "已按你的要求调整搭配",
) -> list[Any]:
    """One fast Harness run for one target: Stylist semantic/action + Critic."""
    arguments = {
        **tool_args,
        "intent": {
            "message": goal,
            "goal": goal,
            "requirements": ["保留未明确要求修改的单品", goal],
        },
    }
    return [
        (
            {"decision_summary": "执行调整", "control": "CONTINUE"},
            [{"name": "modify_outfit", "arguments": arguments}],
        ),
        {**_APPROVE, "feedback": feedback},
    ]


def _empty_extraction() -> dict[str, Any]:
    return {"evidence": []}


# ── multi-turn chain: recommend -> modify -> overall adjust ────────────────


def test_session_multiturn_reuses_outfit_context(db_dsn: str) -> None:
    database_path = db_dsn
    initialize_database(database_path)
    _seed_wardrobe(database_path)

    script: list[Any] = []
    # R1: agentic recommendation, three identical candidates on the base set.
    script += _recommend_block(
        "通勤搭配", ["top-1", "bottom-1", "coat-1", "shoes-1"], feedback="符合通勤正式感"
    )
    # R1 memory extraction distills one contextual style claim (observable).
    script.append(
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
        }
    )
    # R2: explicit slot follow-up -> "modify all three", coat -> blazer.
    for _ in range(3):
        script += _modify_block(
            "换一件外套", _replace_plan("coat-1", "blazer-1"), feedback="已换成深蓝西装外套"
        )
    script.append(_empty_extraction())  # R2 extraction
    # R3: slot-less follow-up -> session_follow_up, full formal rebuild.
    for _ in range(3):
        script += _modify_block(
            "整体调整更正式",
            _rebuild_plan(["top-1", "bottom-1", "blazer-1", "shoes-1"], ["dress-1", "heels-1"]),
            feedback="已调整为正式连衣裙搭配",
        )
    script.append(_empty_extraction())  # R3 extraction

    workflow = _workflow(database_path, FakeLlm(script))

    # R1: plain recommendation (agentic).
    payload1 = workflow.execute(
        TaskExecutionInput(user_id="u", request="帮我推荐一套通勤搭配")
    )
    assert payload1["task_type"] == "outfit_recommend"
    assert payload1["status"] == "completed"
    assert payload1["llm_call_count"] == 6  # 3 × (stylist semantic/action + critic)
    with database_session(database_path) as connection:
        memories = list_preferences(connection, "u")
    assert [(m["dimension"], m["attribute"], m["value"]) for m in memories] == [
        ("style", "style", "通勤")
    ]

    # R2: explicit slot modification reuses the round-1 outfit.
    session_context = outfit_context_from_payload(payload1)
    assert session_context["current_item_ids"] == [
        "top-1", "bottom-1", "coat-1", "shoes-1",
    ]
    payload2 = workflow.execute(
        TaskExecutionInput(user_id="u", request="换一件外套"),
        session_context=session_context,
    )
    assert payload2["task_type"] == "outfit_modify"
    assert payload2["status"] == "completed"
    assert payload2["llm_call_count"] == 6  # 3 targets × (stylist semantic/action + critic)
    assert payload2["result"]["target_slot"] == "outerwear"
    assert len(payload2["result"]["alternatives"]) == 3  # modify all three
    alternative = payload2["result"]["alternatives"][0]
    assert alternative["item_ids"] == ["top-1", "bottom-1", "blazer-1", "shoes-1"]
    assert alternative["replaced_item_ids"] == ["coat-1"]
    assert alternative["locked_item_ids"] == ["top-1", "bottom-1", "shoes-1"]

    # R3: slot-less follow-up becomes an overall adjustment.
    next_context = outfit_context_from_payload(payload2)
    assert next_context["current_item_ids"] == [
        "top-1", "bottom-1", "blazer-1", "shoes-1",
    ]
    payload3 = workflow.execute(
        TaskExecutionInput(user_id="u", request="更正式一点"),
        session_context=next_context,
    )
    assert payload3["task_type"] == "outfit_modify"
    assert payload3["route"]["reason"] == "session_follow_up"
    assert payload3["result"]["alternatives"][0]["item_ids"] == ["dress-1", "heels-1"]


def test_follow_up_without_session_stays_recommend(db_dsn: str) -> None:
    database_path = db_dsn
    initialize_database(database_path)
    _seed_wardrobe(database_path)
    workflow = _workflow(database_path, None)
    # Without session context, a slot-less request is routed as a fresh
    # recommendation rather than being forced into a modification.
    payload = workflow.execute(
        TaskExecutionInput(user_id="u", request="更正式一点")
    )
    assert payload["task_type"] == "outfit_recommend"


# ---------------------------------------------------------------------------
# M3: multi-turn dialogue with natural, colloquial user input
# ---------------------------------------------------------------------------


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

    script: list[Any] = []
    script += _recommend_block("客户会面正式通勤", ["top-1", "bottom-1", "coat-1", "shoes-1"])
    script.append(_empty_extraction())  # R1 extraction
    for _ in range(3):
        script += _modify_block(
            "换件西装外套", _replace_plan("coat-1", "blazer-1"), feedback="已换成深蓝西装外套"
        )
    script.append(_empty_extraction())  # R2 extraction
    for _ in range(3):
        script += _modify_block(
            "换条深色裤子", _replace_plan("bottom-1", "dark-pants-2"), feedback="已换成深灰西裤"
        )
    script.append(_empty_extraction())  # R3 extraction
    for _ in range(3):
        script += _modify_block(
            "整体再正式",
            _rebuild_plan(
                ["top-1", "dark-pants-2", "blazer-1", "shoes-1"], ["dress-1", "heels-1"]
            ),
            feedback="已调整为正式连衣裙搭配",
        )
    script.append(_empty_extraction())  # R4 extraction

    workflow = _workflow(database_path, FakeLlm(script))

    # R1: colloquial fresh brief.
    payload1 = workflow.execute(
        TaskExecutionInput(user_id="u", request="下周一有个重要客户要见，帮我配一身，别太花哨")
    )
    assert payload1["task_type"] == "outfit_recommend"
    ctx1 = outfit_context_from_payload(payload1)
    assert ctx1["current_item_ids"] == ["top-1", "bottom-1", "coat-1", "shoes-1"]

    # R2: replace the coat; the blazer must carry into the next round.
    payload2 = workflow.execute(
        TaskExecutionInput(user_id="u", request="嗯，外套换件西装吧，这大衣太随意了"),
        session_context=ctx1,
    )
    assert payload2["task_type"] == "outfit_modify"
    assert payload2["result"]["target_slot"] == "outerwear"
    assert payload2["result"]["alternatives"][0]["item_ids"] == [
        "top-1", "bottom-1", "blazer-1", "shoes-1",
    ]
    ctx2 = outfit_context_from_payload(payload2)
    assert ctx2["current_item_ids"] == ["top-1", "bottom-1", "blazer-1", "shoes-1"]

    # R3: replace the trousers; must carry the R2 coat (no fallback to R1).
    payload3 = workflow.execute(
        TaskExecutionInput(user_id="u", request="裤子能不能换条深色的"),
        session_context=ctx2,
    )
    assert payload3["task_type"] == "outfit_modify"
    assert payload3["result"]["target_slot"] == "bottom"
    alternative3 = payload3["result"]["alternatives"][0]
    assert alternative3["replaced_item_ids"] == ["bottom-1"]
    assert "blazer-1" in alternative3["item_ids"]
    assert "coat-1" not in alternative3["item_ids"]

    # R4: slot-less follow-up becomes an overall adjustment.
    ctx3 = outfit_context_from_payload(payload3)
    payload4 = workflow.execute(
        TaskExecutionInput(user_id="u", request="整体再正式一点点"),
        session_context=ctx3,
    )
    assert payload4["task_type"] == "outfit_modify"
    assert payload4["route"]["reason"] == "session_follow_up"
    assert payload4["result"]["alternatives"][0]["item_ids"] == ["dress-1", "heels-1"]


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


def test_mixed_chain_fresh_scene_does_not_rewrite(db_dsn: str) -> None:
    """Recommend -> swap coat -> fresh wedding brief -> overall color adjust.

    A fresh scenario inside an active session must keep its own recommend route
    (never be swallowed by the follow-up rewrite), and its plan becomes the new
    baseline for the next modification instead of the older swapped chain.
    """
    database_path = db_dsn
    initialize_database(database_path)
    _seed_with_loafers(database_path)

    script: list[Any] = []
    script += _recommend_block("通勤搭配", ["top-1", "bottom-1", "coat-1", "shoes-1"])
    script.append(_empty_extraction())  # R1 extraction
    for _ in range(3):
        script += _modify_block(
            "大衣换成西装", _replace_plan("coat-1", "blazer-1"), feedback="已换成深蓝西装外套"
        )
    script.append(_empty_extraction())  # R2 extraction
    # R3 is a FRESH wedding recommend — a new baseline, no blazer leak.
    script += _recommend_block("婚礼正式", ["top-1", "bottom-1", "coat-1", "shoes-1"], feedback="符合婚礼正式感")
    script.append(_empty_extraction())  # R3 extraction
    for _ in range(3):
        script += _modify_block(
            "整体改成红色系", _replace_plan("shoes-1", "heels-1"), feedback="已换成高跟鞋"
        )
    script.append(_empty_extraction())  # R4 extraction

    workflow = _workflow(database_path, FakeLlm(script))

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
    alternative2 = payload2["result"]["alternatives"][0]
    assert alternative2["replaced_item_ids"] == ["coat-1"]
    assert alternative2["locked_item_ids"] == ["top-1", "bottom-1", "shoes-1"]
    ctx2 = outfit_context_from_payload(payload2)
    assert ctx2["current_item_ids"] == ["top-1", "bottom-1", "blazer-1", "shoes-1"]

    # R3: a fresh scenario must NOT be rewritten into a modification.
    payload3 = workflow.execute(
        TaskExecutionInput(user_id="u", request="对了，下周末婚礼穿什么，帮我看看"),
        session_context=ctx2,
    )
    assert payload3["task_type"] == "outfit_recommend"
    assert not is_follow_up("对了，下周末婚礼穿什么，帮我看看")
    # The fresh plan starts over from a new baseline; the R2 swap (blazer)
    # must not leak into the wedding plan.
    ctx3 = outfit_context_from_payload(payload3)
    assert ctx3["current_item_ids"] == ["top-1", "bottom-1", "coat-1", "shoes-1"]

    # R4: a follow-up color adjustment applies to the fresh wedding outfit.
    payload4 = workflow.execute(
        TaskExecutionInput(user_id="u", request="这套改成红色系"),
        session_context=ctx3,
    )
    assert payload4["task_type"] == "outfit_modify"
    alternative4 = payload4["result"]["alternatives"][0]
    assert alternative4["item_ids"] == ["top-1", "bottom-1", "coat-1", "heels-1"]
    assert "blazer-1" not in alternative4["item_ids"]


def test_negative_feedback_then_footwear_swap(db_dsn: str) -> None:
    """Recommend -> "这套太严肃了" (overall) -> "换成乐福鞋" (footwear slot).

    Negative feedback is a session_follow_up overall adjustment; the next round
    then swaps the footwear slot while locking the dress — proving the modify
    chain carries the *latest* outfit forward round after round.
    """
    database_path = db_dsn
    initialize_database(database_path)
    _seed_with_loafers(database_path)

    script: list[Any] = []
    script += _recommend_block("约会通勤", ["top-1", "bottom-1", "coat-1", "shoes-1"])
    script.append(_empty_extraction())  # R1 extraction
    for _ in range(3):
        script += _modify_block(
            "整体改休闲",
            _rebuild_plan(["top-1", "bottom-1", "coat-1", "shoes-1"], ["dress-1", "heels-1"]),
            feedback="已换成连衣裙与高跟鞋",
        )
    script.append(_empty_extraction())  # R2 extraction
    for _ in range(3):
        script += _modify_block(
            "换乐福鞋", _replace_plan("heels-1", "loafers-1"), feedback="已换成乐福鞋"
        )
    script.append(_empty_extraction())  # R3 extraction

    workflow = _workflow(database_path, FakeLlm(script))

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
    assert payload2["result"]["alternatives"][0]["item_ids"] == ["dress-1", "heels-1"]
    ctx2 = outfit_context_from_payload(payload2)
    assert ctx2["current_item_ids"] == ["dress-1", "heels-1"]

    # R3: swap the footwear slot; the dress stays locked.
    payload3 = workflow.execute(
        TaskExecutionInput(user_id="u", request="换成乐福鞋"),
        session_context=ctx2,
    )
    assert payload3["task_type"] == "outfit_modify"
    alternative3 = payload3["result"]["alternatives"][0]
    assert alternative3["item_ids"] == ["dress-1", "loafers-1"]
    assert alternative3["replaced_item_ids"] == ["heels-1"]
    assert alternative3["locked_item_ids"] == ["dress-1"]


def test_follow_up_ignores_stale_session_items(db_dsn: str) -> None:
    """A follow-up whose session outfit items were removed must not force a
    modify with dead item ids — it degrades to a fresh recommendation."""
    database_path = db_dsn
    initialize_database(database_path)
    _seed_wardrobe(database_path)
    workflow = _workflow(database_path, None)
    payload1 = workflow.execute(
        TaskExecutionInput(user_id="u", request="帮我搭一套上班的")
    )
    ctx1 = outfit_context_from_payload(payload1)
    # The deterministic pipeline picks a base outfit from the seed wardrobe.
    assert "top-1" in ctx1["current_item_ids"]

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
    workflow = _workflow(database_path, None)
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
    script: list[Any] = []
    script += _recommend_block("休闲搭配", ["top-1", "bottom-1", "coat-1", "shoes-1"])
    script.append(_empty_extraction())  # R1 extraction
    for _ in range(3):
        script += _modify_block(
            "去掉蓝色", _replace_plan("shoes-1", "heels-1"), feedback="已换成高跟鞋"
        )
    script.append(_empty_extraction())  # R2 extraction

    workflow = _workflow(database_path, FakeLlm(script))
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
    assert payload2["result"]["alternatives"][0]["item_ids"] == [
        "top-1", "bottom-1", "coat-1", "heels-1",
    ]


def test_empty_session_context_degrades_to_recommend(db_dsn: str) -> None:
    """A session context with no produced outfit must not rewrite the route."""
    database_path = db_dsn
    initialize_database(database_path)
    _seed_wardrobe(database_path)
    workflow = _workflow(database_path, None)
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
    workflow = _workflow(database_path, None)
    payload = workflow.execute(
        TaskExecutionInput(user_id="u", request="帮我搭一套通勤的")
    )
    assert payload["status"] == "completed"
    with database_session(database_path) as connection:
        assert list_preferences(connection, "u") == []
        assert preference_evidence_repository.list_evidence(connection, "u") == []
