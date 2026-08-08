from tests.helpers import make_item
from tests.llm.fake_llm import FakeLlm

from styleforge.agents.composer import ComposerAgent
from styleforge.agents.semantic_retriever import SemanticRetrieverAgent, deterministic_signature
from styleforge.core.schemas import TaskSpec
from styleforge.llm.client import LlmUnavailable

USER_QUERY = "明天参加互联网公司的面试，希望正式但不要太老气，不穿红色。"
WARDROBE_SUMMARY = {
    "tops": {"count": 10, "sample_types": ["shirt"], "sample_colors": ["White"]},
    "bottoms": {"count": 8, "sample_types": ["pants"], "sample_colors": ["Navy"]},
    "footwear": {"count": 6, "sample_types": ["shoes"], "sample_colors": ["Black"]},
}

AGENT1_PAYLOAD = {
    "request_signature": {
        "theme": "互联网公司面试",
        "unique_mood": ["专业", "年轻"],
        "practical_context": ["办公室", "半正式"],
        "generic_tendencies_to_avoid": ["仅由基础款组成"],
    },
    "retrieval_plans": [
        {"type": "core", "query": "modern professional interview outfit", "score_weight": 0.40},
        {"type": "distinctive", "query": "young structured business piece", "score_weight": 0.30},
        {"type": "supporting", "query": "comfortable semi-formal office wear", "score_weight": 0.10},
    ],
    "candidate_requirements": {"tops": 10, "bottoms": 10, "dresses": 0, "outerwear": 8, "shoes": 8, "accessories": 8},
}


def test_agent1_maps_llm_output() -> None:
    task = TaskSpec(user_id="u", occasion="business")
    agent = SemanticRetrieverAgent()
    fake = FakeLlm([AGENT1_PAYLOAD])

    output, info, diagnostics = agent.run(
        user_query=USER_QUERY,
        task=task,
        wardrobe_summary=WARDROBE_SUMMARY,
        recent_memories=[],
        llm=fake,
    )

    assert info["degraded"] is False
    assert diagnostics is not None
    assert output.request_signature.theme == "互联网公司面试"
    assert {plan.type for plan in output.retrieval_plans} == {"core", "distinctive", "supporting"}
    assert sum(output.candidate_requirements.to_dict().values()) == 44


def test_agent1_falls_back_on_llm_failure() -> None:
    task = TaskSpec(user_id="u", occasion="business")
    agent = SemanticRetrieverAgent()
    fake = FakeLlm([LlmUnavailable("boom")])

    output, info, diagnostics = agent.run(
        user_query=USER_QUERY,
        task=task,
        wardrobe_summary=WARDROBE_SUMMARY,
        recent_memories=[],
        llm=fake,
    )

    assert info["degraded"] is True
    assert diagnostics is None
    assert output.request_signature.theme == USER_QUERY
    assert len(output.retrieval_plans) == 3


def test_deterministic_signature_valid() -> None:
    task = TaskSpec(user_id="u", occasion="formal", required_slots=("top", "bottom", "footwear"))
    output = deterministic_signature(task, USER_QUERY)

    assert len(output.retrieval_plans) == 3
    assert output.candidate_requirements.to_dict()["tops"] >= 1
    assert sum(output.candidate_requirements.to_dict().values()) == 50


# --- Composer (Agent 2) ---------------------------------------------------

def _agent2_payload(outfit_ids: list[str]) -> dict:
    def _outfit(outfit_id: str) -> dict:
        return {
            "outfit_id": outfit_id,
            "composition_strategy": {
                "visual_anchor": "结构感外套",
                "supporting_direction": "深色下装",
                "practical_balance": "舒适",
            },
            "item_ids": [f"top-{outfit_id}", f"bottom-{outfit_id}", f"shoe-{outfit_id}"],
            "style_tag": "剧场复古",
            "reasoning": "炭灰大衣配合深酒红内搭",
            "request_specific_elements": [],
        }

    return {"outfits": [_outfit(oid) for oid in outfit_ids]}


def _pool_ids(outfit_ids: list[str]) -> set[str]:
    ids: set[str] = set()
    for outfit_id in outfit_ids:
        ids.update([f"top-{outfit_id}", f"bottom-{outfit_id}", f"shoe-{outfit_id}"])
    return ids


def test_composer_maps_llm_outfits() -> None:
    pool_ids = _pool_ids(["o1", "o2", "o3"])
    agent = ComposerAgent()
    fake = FakeLlm([_agent2_payload(["o1", "o2", "o3"])])

    proposals, info, diagnostics = agent.run(
        user_query=USER_QUERY,
        request_signature={"theme": "面试"},
        pool_manifest=[],
        recent_structure_signatures=[],
        llm=fake,
        pool_ids=pool_ids,
    )

    assert info["degraded"] is False
    assert diagnostics is not None
    assert len(proposals) == 3
    assert set(proposals[0].item_ids) <= pool_ids


def test_composer_drops_out_of_pool_outfit() -> None:
    pool_ids = _pool_ids(["o1", "o2"])
    agent = ComposerAgent()
    fake = FakeLlm([_agent2_payload(["o1", "o2", "ghost"])])

    proposals, info, _ = agent.run(
        user_query=USER_QUERY,
        request_signature={"theme": "面试"},
        pool_manifest=[],
        recent_structure_signatures=[],
        llm=fake,
        pool_ids=pool_ids,
    )

    assert info["sanitized_dropped"] == 1
    assert all(set(proposal.item_ids) <= pool_ids for proposal in proposals)


def test_composer_falls_back_when_all_out_of_pool() -> None:
    task = TaskSpec(user_id="u", required_slots=("top", "bottom", "footwear"), max_results=3)
    pool_items = [
        make_item("top-1", "top", name="Top 1", color="Black"),
        make_item("top-2", "top", name="Top 2", color="White"),
        make_item("bottom-1", "pants", name="Pant 1", color="Navy"),
        make_item("bottom-2", "pants", name="Pant 2", color="Gray"),
        make_item("shoe-1", "shoes", name="Shoe 1", color="Black"),
        make_item("shoe-2", "shoes", name="Shoe 2", color="Brown"),
    ]
    pool_ids = {item.item_id for item in pool_items}
    pool_scores = {item.item_id: 1.0 for item in pool_items}
    agent = ComposerAgent()
    fake = FakeLlm([_agent2_payload(["ghost1", "ghost2", "ghost3"])])

    proposals, info, diagnostics = agent.run(
        user_query=USER_QUERY,
        request_signature={"theme": "面试"},
        pool_manifest=[],
        recent_structure_signatures=[],
        llm=fake,
        pool_ids=pool_ids,
        task=task,
        pool_items=pool_items,
        pool_scores=pool_scores,
    )

    assert info["degraded"] is True
    assert diagnostics is None
    assert proposals
    assert all(set(proposal.item_ids) <= pool_ids for proposal in proposals)


def test_composer_llm_failure_falls_back() -> None:
    task = TaskSpec(user_id="u", required_slots=("top", "bottom", "footwear"), max_results=3)
    pool_items = [
        make_item("top-1", "top", name="Top 1", color="Black"),
        make_item("bottom-1", "pants", name="Pant 1", color="Navy"),
        make_item("shoe-1", "shoes", name="Shoe 1", color="Black"),
    ]
    pool_ids = {item.item_id for item in pool_items}
    pool_scores = {item.item_id: 1.0 for item in pool_items}
    agent = ComposerAgent()
    fake = FakeLlm([LlmUnavailable("boom")])

    proposals, info, _ = agent.run(
        user_query=USER_QUERY,
        request_signature={"theme": "面试"},
        pool_manifest=[],
        recent_structure_signatures=[],
        llm=fake,
        pool_ids=pool_ids,
        task=task,
        pool_items=pool_items,
        pool_scores=pool_scores,
    )

    assert info["degraded"] is True
    assert proposals
