"""Gate tests for the p-outfit independent-LLM evaluation.

Design doc: ``docs/P_OUTFIT_EVAL_TEST_COVERAGE.md``. Groups:

- A  case-file contract (no DB, no LLM)
- B  p-outfit semantic mapping / slots (pure functions)
- C  independent judge (pure functions + FakeLlm)
- D  metric pure functions (runner aggregation / Pearson)
- E  workflow execution paths (db fixture + FakeLlm, locked payload contract)
- F  runner integration (db fixture + FakeLlm + scripted encoder)

Everything is offline: FakeLlm, a scripted encoder, and throwaway schemas on the
test database. No real LLM calls, no GPU, no E:\\ dataset reads.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tests.helpers import make_item
from tests.llm.fake_llm import FakeLlm

from styleforge.agents.judge import IndependentJudge
from styleforge.core.categories import ALLOWED_ITEM_TYPES, infer_slot
from styleforge.core.config import Settings
from styleforge.core.rubric import aggregate_score
from styleforge.core.schemas import ImageStatus
from styleforge.data.p_outfit import (
    SEMANTIC_TO_ITEM_TYPE,
    POutfitItem,
    _FOLLOW_UP_MARKERS,
    normalize_p_outfit_item,
    outfit_completeness,
    refine_item_type,
    semantic_to_item_type,
)
from styleforge.llm.judge_prompts import build_judge_prompt
from styleforge.repositories.database import connect, database_session, initialize_database
from styleforge.workflow.graph import _critic_score, StyleForgeWorkflow

from evals.runners.evaluate_p_outfit import (
    _DEFAULT_PASS_MIN,
    _HARD_ASSERTIONS,
    _create_schema,
    _drop_schema,
    _generated_outfit,
    _has_required_slots,
    _scoped_dsn,
    aggregate_metrics,
    build_wardrobe_snapshot,
    evaluate_p_outfit,
    load_cases,
    pearson,
)

_CASES_PATH = Path(__file__).resolve().parents[1] / "evals" / "cases" / "p_outfit.json"

# A valid 1x1 PNG for the image-mode dummy photos (PIL must decode them).
def _png_1px() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), (120, 120, 120)).save(buffer, format="PNG")
    return buffer.getvalue()


_PNG_1PX = _png_1px()


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

def _case_payload() -> dict:
    return json.loads(_CASES_PATH.read_text(encoding="utf-8"))


def _five_dims(*values: int) -> dict:
    keys = ("request_relevance", "request_specificity", "outfit_coordination", "wearability", "freshness")
    return dict(zip(keys, values, strict=True))


# --- A / C helpers ---------------------------------------------------------

def _settings(tmp_path: Path, db_dsn: str, *, llm_enabled: bool = False) -> Settings:
    return Settings(
        metadata_path=tmp_path / "meta.json",
        outfit_path=tmp_path / "outfit.json",
        image_root=None,
        knowledge_root=Path("knowledge"),
        database_dsn=db_dsn,
        artifact_root=tmp_path / "artifacts",
        embedding_dir=tmp_path / "artifacts" / "embeddings" / "fashionclip",
        index_dir=tmp_path / "artifacts" / "index" / "fashionclip",
        dataset_revision="test",
        llm_enabled=llm_enabled,
        weather_enabled=False,
        deepseek_model="test-model",
    )


def _a1_payload() -> dict:
    return {
        "request_signature": {
            "theme": "日常通勤",
            "unique_mood": ["干净"],
            "practical_context": ["通勤"],
            "generic_tendencies_to_avoid": ["避免高跟鞋"],
        },
        "retrieval_plans": [
            {"type": "core", "query": "daily commute outfit", "score_weight": 0.40},
            {"type": "distinctive", "query": "clean minimal piece", "score_weight": 0.30},
            {"type": "supporting", "query": "comfortable shoes", "score_weight": 0.10},
        ],
        "candidate_requirements": {
            "tops": 5, "bottoms": 5, "dresses": 0, "outerwear": 0, "shoes": 5, "accessories": 0,
        },
    }


def _a2_payload(itemsets: list[tuple[str, list[str]]]) -> dict:
    outfits = []
    for outfit_id, item_ids in itemsets:
        outfits.append(
            {
                "outfit_id": outfit_id,
                "composition_strategy": {
                    "visual_anchor": "结构感",
                    "supporting_direction": "深色下装",
                    "practical_balance": "舒适",
                },
                "item_ids": item_ids,
                "style_tag": "日常",
                "reasoning": "干净利落",
                "request_specific_elements": [],
            }
        )
    return {"outfits": outfits}


def _a3_payload(dims: tuple[int, ...], decision: str = "accept", **overrides) -> dict:
    payload = {
        "outfit_assessment": {
            "outfit_id": "o1",
            "dimension_scores": _five_dims(*dims),
            "reasoning": "整体协调",
            "improvements": "",
        },
        "explanation_assessment": {"grounded": True, "unsupported_claims": []},
        "alternatives": [{"outfit_id": "o2", "strength": "更实穿"}],
        "decision": decision,
        "failure_source": "",
        "feedback": "",
        "missing_items": [],
        "best_effort_outfit_id": "",
    }
    payload.update(overrides)
    return payload


def _judge_payload(dims: tuple[int, ...]) -> dict:
    return {
        "outfit_id": "judge",
        "dimension_scores": _five_dims(*dims),
        "reasoning": "符合需求",
    }


# --- E helpers (raw-id catalog like test_workflow_llm) ----------------------

EVAL_USER = "eval-order"
EVAL_REQUEST = "请推荐一套日常穿搭，以上衣和长裤为主体，搭配合适鞋履。"

EVAL_WARDROBE = [
    make_item("top-1", "top", name="White shirt", color="White"),
    make_item("top-2", "top", name="Black blouse", color="Black"),
    make_item("bottom-1", "pants", name="Navy trousers", color="Navy"),
    make_item("bottom-2", "pants", name="Gray pants", color="Gray"),
    make_item("shoe-1", "shoes", name="Black loafers", color="Black"),
    make_item("shoe-2", "shoes", name="Brown oxfords", color="Brown"),
]
_EVAL_TYPE_BY_ID = {item.item_id: item.item_type for item in EVAL_WARDROBE}
_EVAL_ITEMSETS = [
    ("o1", ["top-1", "bottom-1", "shoe-1"]),
    ("o2", ["top-2", "bottom-2", "shoe-2"]),
    ("o3", ["top-1", "bottom-2", "shoe-1"]),
]


def _catalog_row(item) -> tuple:
    return (
        item.item_id, item.source, item.gender, item.item_type, item.main_category,
        item.name, item.color, item.description, json.dumps(list(item.features)),
        "{}", item.image_filename, item.relative_image_path, item.image_status.value,
        item.embedding_status.value, item.raw_json_hash, "test",
        "2026-01-01T00:00:00+00:00", item.dataset_item_id or "",
    )


def _seed_wardrobe(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        for item in EVAL_WARDROBE:
            connection.execute(
                "INSERT INTO catalog_items VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (item_id) DO NOTHING",
                _catalog_row(item),
            )
            connection.execute(
                "INSERT INTO wardrobe_items (user_id, item_id, active, favorite, notes, added_at) "
                "VALUES (%s, %s, 1, 0, '', '2026-01-01T00:00:00+00:00') "
                "ON CONFLICT (user_id, item_id) DO NOTHING",
                (EVAL_USER, item.item_id),
            )


def _make_eval_workflow(tmp_path: Path, db_dsn: str, llm: FakeLlm | None) -> StyleForgeWorkflow:
    settings = _settings(tmp_path, db_dsn, llm_enabled=llm is not None)
    return StyleForgeWorkflow(
        database_path=settings.database_dsn,
        embedding_dir=settings.embedding_dir,
        model_dir=settings.artifact_root / "models",
        device="cpu",
        llm_client=llm,
        settings=settings,
        weather_tool=None,
    )


def _payload_types(payload: dict) -> list[str]:
    gen = _generated_outfit(payload)
    return [_EVAL_TYPE_BY_ID[item_id] for item_id in gen if item_id in _EVAL_TYPE_BY_ID]


# --- F helpers (deterministic UUID snapshot + scripted encoder) --------------

def _stable_uuid(raw: str) -> str:
    return str(uuid.UUID(bytes=hashlib.sha256(raw.encode("utf-8")).digest()[:16]))


def _fake_ensure_uuids(connection, source: str, raw_to_uuid: dict[str, str], raw_ids: list[str]) -> None:
    del connection, source
    for raw in raw_ids:
        raw_to_uuid.setdefault(raw, _stable_uuid(raw))


class _ScriptedEncoder:
    """L2-normalized constant vectors; no model load, no GPU."""

    dimension = 512

    def encode_texts(self, texts) -> np.ndarray:
        return self._ones(len(texts))

    def encode_images(self, images) -> np.ndarray:
        return self._ones(len(images))

    @staticmethod
    def _ones(n: int) -> np.ndarray:
        matrix = np.ones((n, 512), dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return (matrix / norms).astype(np.float32)


def _outfit_uuid_set(case_items: list[dict]) -> list[str]:
    """One complete outfit's UUID item ids for a case (dress or separates)."""
    by_slot: dict[str, str] = {}
    for item in case_items:
        item_id = str(item["item_id"])
        by_slot.setdefault(infer_slot(str(item["item_type"])), item_id)
    if "one_piece" in by_slot and "footwear" in by_slot:
        ids = [by_slot["one_piece"], by_slot["footwear"]]
        extra = by_slot.get("accessory")
        if extra:
            ids.append(extra)
    else:
        ids = [by_slot["top"], by_slot["bottom"], by_slot["footwear"]]
    return [_stable_uuid(raw) for raw in ids]


def _fake_case_items(cases: list[dict]) -> dict[str, dict]:
    """case id -> {raw: item} for every item the fake script references."""
    return {case["id"]: case["_text_by_raw"] for case in cases}


# --------------------------------------------------------------------------
# A. Case-file contract (no DB / no LLM)
# --------------------------------------------------------------------------

def test_case_file_valid() -> None:
    payload = _case_payload()
    cases = payload["cases"]
    assert payload["case_count"] == len(cases) == 100
    required = {
        "id", "source_set_id", "source_title", "user_request",
        "golden_item_ids", "items", "hard_assertions", "pass_criteria",
    }
    seen_sets: set[str] = set()
    for case in cases:
        assert required <= set(case), f"case {case['id']} missing fields"
        assert case["source_set_id"] not in seen_sets, f"duplicate set {case['source_set_id']}"
        seen_sets.add(case["source_set_id"])
        golden = {str(item_id) for item_id in case["golden_item_ids"]}
        item_ids = {str(item["item_id"]) for item in case["items"]}
        assert golden == item_ids, f"case {case['id']} golden_item_ids != items"
        assert 25 <= len(case["user_request"]) <= 120, f"case {case['id']} request length"
        for item in case["items"]:
            assert str(item.get("url_name", "")).strip(), f"case {case['id']} item lacks url_name"
        assert set(_HARD_ASSERTIONS) <= set(case["hard_assertions"])
    # runner's gate accepts the file as-is
    assert len(load_cases(_CASES_PATH)) == 100


def test_case_requests_are_fresh_briefs() -> None:
    for case in _case_payload()["cases"]:
        request = case["user_request"]
        for marker in _FOLLOW_UP_MARKERS:
            assert marker not in request, f"case {case['id']} has follow-up marker {marker!r}"


def test_case_requests_reviewed() -> None:
    for case in _case_payload()["cases"]:
        assert case.get("_reviewed") is True, f"case {case['id']} is not _reviewed (P3 freeze)"


def test_case_pass_criteria_preregistered() -> None:
    for case in _case_payload()["cases"]:
        assert case["pass_criteria"]["min_judge_overall"] == _DEFAULT_PASS_MIN == 60
        assert set(_HARD_ASSERTIONS) <= set(case["hard_assertions"])
    # the pass line is frozen in the runner's report too (pre-registration)
    assert _DEFAULT_PASS_MIN == 60
    assert _HARD_ASSERTIONS == ("all_items_in_wardrobe", "has_required_slots")


def test_case_no_answer_leakage() -> None:
    for case in _case_payload()["cases"]:
        lowered = case["user_request"].lower()
        for item in case["items"]:
            for field in ("title", "url_name"):
                for token in re.findall(r"[a-zA-Z]{4,}", str(item.get(field, "") or "")):
                    assert token.lower() not in lowered, (
                        f"case {case['id']} leaks golden {field} token {token!r}"
                    )


# --------------------------------------------------------------------------
# B. p-outfit mapping and slots (pure functions)
# --------------------------------------------------------------------------

def test_semantic_to_item_type_slot_safe() -> None:
    for category in SEMANTIC_TO_ITEM_TYPE:
        mapped = semantic_to_item_type(category)
        assert mapped in ALLOWED_ITEM_TYPES, f"{category} -> {mapped} not allowed"
        assert infer_slot(mapped) != "other", f"{category} -> {mapped} is slot-less"


def test_refine_item_type_fidelity() -> None:
    assert refine_item_type("bottoms", "black lace skirt") == "skirt"
    assert refine_item_type("all-body", "silk jumpsuit") == "jumpsuit"
    assert refine_item_type("all-body", "ivory tuxedo") == "suit"
    # refinement never changes the coarse slot
    for category, text in (("bottoms", "lace skirt"), ("all-body", "silk jumpsuit"), ("all-body", "tuxedo")):
        base = semantic_to_item_type(category)
        assert infer_slot(refine_item_type(category, text)) == infer_slot(base)


def test_outfit_completeness() -> None:
    def item(item_type: str) -> POutfitItem:
        return POutfitItem("id", "tops", item_type, "x", "", "x")

    assert outfit_completeness([item("top"), item("pants"), item("shoes")])
    assert outfit_completeness([item("dress"), item("shoes")])
    assert not outfit_completeness([item("top"), item("pants")])
    assert not outfit_completeness([item("top"), item("bag"), item("shoes")])


def test_normalize_p_outfit_item_modes() -> None:
    po = POutfitItem("123", "all-body", "dress", "Silk Dress", "red", "silk-dress")
    order_item = normalize_p_outfit_item(po, mode="order")
    assert order_item.image_status is ImageStatus.UNBOUND
    assert order_item.image_filename == "" and order_item.relative_image_path == ""
    image_item = normalize_p_outfit_item(po, mode="image")
    assert image_item.image_status is ImageStatus.AVAILABLE
    assert image_item.image_filename == "123.jpg"
    assert image_item.relative_image_path == "nondisjoint/train/123.jpg"


def test_item_text_block_format() -> None:
    block = POutfitItem("1", "tops", "top", "White Shirt", "cotton", "white-shirt").to_text_block()
    assert block == "上衣 | White Shirt | cotton"
    short = POutfitItem("2", "shoes", "shoes", "Loafers", "", "loafers").to_text_block()
    assert short == "鞋履 | Loafers"


# --------------------------------------------------------------------------
# C. Independent judge (pure functions + FakeLlm)
# --------------------------------------------------------------------------

def test_build_judge_prompt_independence() -> None:
    system, user = build_judge_prompt(
        user_request="日常通勤穿搭",
        item_texts=["上衣 | 白衬衫 | 棉"],
    )
    combined = system + user
    forbidden = ("recompose", "retrieve_more", "wardrobe_gap", "request_signature",
                 "阶段二", "解释核对", "critic", "decision")
    for token in forbidden:
        assert token not in combined.lower(), f"judge prompt leaks critic vocabulary {token!r}"


def test_judge_score_success() -> None:
    fake = FakeLlm([_judge_payload((9, 8, 9, 8, 7))])
    scores, diag, _ = IndependentJudge().score_outfit(
        user_request="日常通勤穿搭", item_texts=["上衣 | 白衬衫"], llm=fake
    )
    assert scores is not None and diag["degraded"] is False
    assert scores.to_dict() == _five_dims(9, 8, 9, 8, 7)
    assert fake.call_count == 1


def test_judge_score_failure_no_mask() -> None:
    fake = FakeLlm([RuntimeError("llm down")])
    scores, diag, _ = IndependentJudge().score_outfit(
        user_request="日常通勤穿搭", item_texts=["上衣 | 白衬衫"], llm=fake
    )
    assert scores is None
    assert diag["degraded"] is True
    assert "llm down" in diag["reason"]


def test_judge_no_llm_degraded() -> None:
    scores, diag, _ = IndependentJudge().score_outfit(
        user_request="日常通勤穿搭", item_texts=["上衣 | 白衬衫"], llm=None
    )
    assert scores is None
    assert diag["degraded"] is True
    assert "no llm" in diag["reason"]


def test_aggregate_score_matches_critic() -> None:
    dims = _five_dims(9, 8, 9, 8, 7)
    critic = _critic_score({"outfit_assessment": {"outfit_id": "x", "dimension_scores": dims}}, "x")
    assert aggregate_score(dims) == critic


def test_aggregate_score_empty() -> None:
    assert aggregate_score(None) == 0.0
    assert aggregate_score({}) == 0.0


# --------------------------------------------------------------------------
# D. Metric pure functions
# --------------------------------------------------------------------------

def test_aggregate_metrics_numeric() -> None:
    per_case = [
        {
            "judge_overall": 80.0, "judge_overall_golden": 90.0, "judge_failed": False,
            "judge_dimensions": _five_dims(8, 8, 8, 8, 8), "gap": False, "passed": True,
            "violations": [], "critic_score": 75.0, "latency_seconds": 1.0, "llm_call_count": 5,
        },
        {
            "judge_overall": 55.0, "judge_overall_golden": 88.0, "judge_failed": False,
            "judge_dimensions": _five_dims(5, 5, 6, 5, 5), "gap": False, "passed": False,
            "violations": ["has_required_slots"], "critic_score": 55.0, "latency_seconds": 2.0,
            "llm_call_count": 3,
        },
        {
            "judge_overall": None, "judge_overall_golden": None, "judge_failed": True,
            "judge_dimensions": None, "gap": True, "passed": False,
            "violations": ["no_recommendation"], "critic_score": 0.0, "latency_seconds": 3.0,
            "llm_call_count": 1,
        },
    ]
    metrics = aggregate_metrics(per_case)
    assert metrics["n_cases"] == 3
    assert metrics["mean_judge_overall"] == 67.5
    assert metrics["mean_judge_overall_golden"] == 89.0
    # rates are rounded to 4 decimals by the runner
    assert metrics["pass_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert metrics["judge_failure_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert metrics["hard_violation_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert metrics["gap_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert metrics["mean_dimension_scores"]["request_relevance"] == 6.5
    assert metrics["critic_judge_consistency"]["pearson_r"] == 1.0
    assert metrics["critic_judge_consistency"]["mean_abs_delta"] == 2.5
    assert metrics["critic_judge_consistency"]["n"] == 2
    assert metrics["performance"]["p50_latency_s"] == 2.0


def test_pearson_known_value() -> None:
    assert pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0, abs=1e-9)
    assert pearson([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0, abs=1e-9)
    assert pearson([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0, abs=1e-9)
    assert pearson([1], [2]) is None
    assert pearson([1, 2, 3], [1, 2]) is None


# --------------------------------------------------------------------------
# E. Workflow execution paths (db fixture + FakeLlm)
# --------------------------------------------------------------------------

def test_pipeline_accept_path(tmp_path: Path, db_dsn: str) -> None:
    _seed_wardrobe(db_dsn)
    fake = FakeLlm([_a1_payload(), _a2_payload(_EVAL_ITEMSETS), _a3_payload((9, 8, 9, 8, 7))])
    workflow = _make_eval_workflow(tmp_path, db_dsn, fake)

    payload = workflow.recommend_payload(user_id=EVAL_USER, request=EVAL_REQUEST)

    assert payload["decision"] == "accept"
    assert payload["llm_call_count"] == 3
    assert payload["llm_attempts"] == 3
    assert payload["fallback_count"] == 0
    assert len(payload["result"]["recommendations"]) == 3
    gen = _generated_outfit(payload)
    assert gen and all(item_id in _EVAL_TYPE_BY_ID for item_id in gen)
    assert _has_required_slots(_payload_types(payload))
    assert payload["critic"]["outfit_assessment"]["outfit_id"] == "o1"


def test_pipeline_recompose_path(tmp_path: Path, db_dsn: str) -> None:
    _seed_wardrobe(db_dsn)
    fake = FakeLlm(
        [
            _a1_payload(),
            _a2_payload(_EVAL_ITEMSETS),
            _a3_payload((8, 7, 8, 7, 6), decision="recompose",
                        failure_source="composer", feedback="候选中已有特色单品，请重新组合"),
            _a2_payload(_EVAL_ITEMSETS),
            _a3_payload((9, 8, 9, 8, 7)),
        ]
    )
    workflow = _make_eval_workflow(tmp_path, db_dsn, fake)

    payload = workflow.recommend_payload(user_id=EVAL_USER, request=EVAL_REQUEST)

    assert payload["decision"] == "accept"
    assert payload["llm_call_count"] == 5
    assert payload["fallback_count"] == 1
    assert len(payload["result"]["recommendations"]) == 3
    # composer_feedback reached the second composer call
    assert "重新组合" in fake.calls[3]["user"]


def test_pipeline_wardrobe_gap_best_effort(tmp_path: Path, db_dsn: str) -> None:
    _seed_wardrobe(db_dsn)
    fake = FakeLlm(
        [
            _a1_payload(),
            _a2_payload(_EVAL_ITEMSETS),
            _a3_payload(
                (7, 6, 7, 6, 5), decision="wardrobe_gap", failure_source="wardrobe",
                feedback="衣橱缺少合适鞋履",
                missing_items=[{"category": "shoes", "desired_features": ["半正式"]}],
                best_effort_outfit_id="o2",
            ),
        ]
    )
    workflow = _make_eval_workflow(tmp_path, db_dsn, fake)

    payload = workflow.recommend_payload(user_id=EVAL_USER, request=EVAL_REQUEST)

    assert payload["decision"] == "wardrobe_gap"
    assert payload["llm_call_count"] == 3
    assert payload["critic"]["missing_items"][0]["category"] == "shoes"
    assert len(payload["result"]["recommendations"]) == 3  # best-effort still persisted
    assert _generated_outfit(payload)  # and it is presentable


def test_pipeline_repeated_recompose_clamps(tmp_path: Path, db_dsn: str) -> None:
    _seed_wardrobe(db_dsn)
    fake = FakeLlm(
        [
            _a1_payload(),
            _a2_payload(_EVAL_ITEMSETS),
            _a3_payload((7, 6, 7, 6, 5), decision="recompose", failure_source="composer", feedback="再试一次"),
            _a2_payload(_EVAL_ITEMSETS),
            _a3_payload((7, 6, 7, 6, 5), decision="recompose", failure_source="composer", feedback="还是不行"),
        ]
    )
    workflow = _make_eval_workflow(tmp_path, db_dsn, fake)

    payload = workflow.recommend_payload(user_id=EVAL_USER, request=EVAL_REQUEST)

    # guard clamps to a single recompose: llm calls never exceed the frozen budget
    assert payload["llm_call_count"] <= 5
    assert payload["fallback_count"] == 1
    assert payload["decision"] == "recompose"
    assert payload["best_effort"] is not None


def test_pipeline_handoff_preservation(tmp_path: Path, db_dsn: str) -> None:
    _seed_wardrobe(db_dsn)
    a1 = _a1_payload()
    a1["request_signature"]["generic_tendencies_to_avoid"] = ["避免高跟鞋", "避免全黑"]
    fake = FakeLlm([a1, _a2_payload(_EVAL_ITEMSETS), _a3_payload((9, 8, 9, 8, 7))])
    workflow = _make_eval_workflow(tmp_path, db_dsn, fake)

    payload = workflow.recommend_payload(user_id=EVAL_USER, request=EVAL_REQUEST)

    constraints = payload["request_signature"]["generic_tendencies_to_avoid"]
    assert len(constraints) == 2
    assert "避免高跟鞋" in constraints and "避免全黑" in constraints


def test_trace_golden_path_nodes(tmp_path: Path, db_dsn: str) -> None:
    _seed_wardrobe(db_dsn)
    fake = FakeLlm([_a1_payload(), _a2_payload(_EVAL_ITEMSETS), _a3_payload((9, 8, 9, 8, 7))])
    workflow = _make_eval_workflow(tmp_path, db_dsn, fake)

    payload = workflow.recommend_payload(user_id=EVAL_USER, request=EVAL_REQUEST)

    nodes = [entry["node"] for entry in payload["trace"]]
    golden = ("planner_agent", "semantic_retriever", "candidate_pool", "composer",
              "basic_validation", "critic_agent", "persist_result")
    for node in golden:
        assert node in nodes, f"golden-path node {node} missing from trace"
    order = [nodes.index(node) for node in golden]
    assert order == sorted(order), "golden-path nodes executed out of order"
    assert "best_effort" not in nodes  # accept path never degrades


# --------------------------------------------------------------------------
# F. Runner integration (db fixture + FakeLlm + scripted encoder)
# --------------------------------------------------------------------------

def test_build_wardrobe_snapshot(_test_dsn: str) -> None:
    cases = load_cases(_CASES_PATH)[:2]
    schemas = [f"eval_gate_o_{uuid.uuid4().hex[:6]}", f"eval_gate_i_{uuid.uuid4().hex[:6]}"]
    for schema in schemas:
        _create_schema(_test_dsn, schema)
    try:
        order_snap = build_wardrobe_snapshot(
            mode="order", db_dsn=_scoped_dsn(_test_dsn, schemas[0]), cases=cases,
            user_id="eval-order", source_revision="gate",
        )
        image_snap = build_wardrobe_snapshot(
            mode="image", db_dsn=_scoped_dsn(_test_dsn, schemas[1]), cases=cases,
            user_id="eval-image", source_revision="gate",
        )
        assert order_snap["item_count"] == image_snap["item_count"] > 0
        assert set(order_snap["uuid_by_raw"]) == set(image_snap["uuid_by_raw"])
        # user isolation + per-mode image status live in separate schemas
        with connect(_scoped_dsn(_test_dsn, schemas[0])) as connection:
            order_status = {row["item_id"]: row["image_status"] for row in connection.execute(
                "SELECT item_id, image_status FROM catalog_items")}
            order_user_count = connection.execute(
                "SELECT COUNT(*) FROM wardrobe_items WHERE user_id='eval-order'").fetchone()[0]
        assert all(status == "unbound" for status in order_status.values())
        assert order_user_count == order_snap["item_count"]
        with connect(_scoped_dsn(_test_dsn, schemas[1])) as connection:
            image_status = {row["item_id"]: row["image_status"] for row in connection.execute(
                "SELECT item_id, image_status FROM catalog_items")}
            image_user_count = connection.execute(
                "SELECT COUNT(*) FROM wardrobe_items WHERE user_id='eval-image'").fetchone()[0]
        assert all(status == "available" for status in image_status.values())
        assert image_user_count == image_snap["item_count"]
        # item ids are UUID-ized; raw ids survive in dataset_item_id
        for raw, uid in order_snap["uuid_by_raw"].items():
            assert uid in order_status
        with connect(_scoped_dsn(_test_dsn, schemas[0])) as connection:
            raw_row = connection.execute(
                "SELECT dataset_item_id FROM catalog_items WHERE item_id = %s",
                (list(order_snap["uuid_by_raw"].values())[0],)).fetchone()
        assert raw_row["dataset_item_id"] in order_snap["raw_by_uuid"].values()
    finally:
        for schema in schemas:
            _drop_schema(_test_dsn, schema)


def _fake_eval_report(_test_dsn: str, tmp_path: Path, monkeypatch) -> dict:
    """Run mode=both, 2 cases through evaluate_p_outfit with fake LLM/encoder."""
    monkeypatch.setattr("evals.runners.evaluate_p_outfit.ensure_uuids", _fake_ensure_uuids)
    cases = load_cases(_CASES_PATH)[:2]
    dims_by_index = ((9, 8, 9, 8, 7), (7, 6, 7, 6, 5))
    # Real call order is mode-outer, case-inner: [a1, a2, a3, judge_gen, judge_golden].
    script = []
    for _mode in ("order", "image"):
        for index, case in enumerate(cases):
            itemsets = [
                ("o1", _outfit_uuid_set(case["items"])),
                ("o2", _outfit_uuid_set(case["items"])),
                ("o3", _outfit_uuid_set(case["items"])),
            ]
            dims = dims_by_index[index]
            script += [_a1_payload(), _a2_payload(itemsets), _a3_payload(dims),
                       _judge_payload(dims), _judge_payload((8, 7, 8, 7, 6))]

    image_root = tmp_path / "poutfit-images"
    for case in cases:
        for item in case["items"]:
            img = image_root / "images" / "nondisjoint" / "train" / f"{item['item_id']}.jpg"
            img.parent.mkdir(parents=True, exist_ok=True)
            img.write_bytes(_PNG_1PX)  # valid PNG so PIL can decode it

    settings = _settings(tmp_path, _test_dsn)
    report = evaluate_p_outfit(
        cases_path=_CASES_PATH,
        mode="both",
        report_path=tmp_path / "report.json",
        db_dsn=_test_dsn,
        settings=settings,
        model_dir=tmp_path / "models",
        device="cpu",
        image_root=image_root,
        seed=20260814,
        max_cases=2,
        llm_client=FakeLlm(script),
        encoder_factory=lambda s, m, d: _ScriptedEncoder(),
    )
    return report


def test_runner_end_to_end_fake(_test_dsn: str, tmp_path: Path, monkeypatch) -> None:
    def _eval_schemas() -> set[str]:
        with connect(_test_dsn) as connection:
            rows = connection.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'eval_%'").fetchall()
        return {row["nspname"] for row in rows}

    preexisting = _eval_schemas()
    report = _fake_eval_report(_test_dsn, tmp_path, monkeypatch)

    assert report["schema_version"] == "styleforge.p-outfit-eval.v1"
    assert set(report["modes"]) == {"order", "image"}
    assert "delta_image_minus_order" in report["comparison"]
    assert report["case_count"] == 2
    for mode in ("order", "image"):
        per_case = report["modes"][mode]["per_case"]
        assert len(per_case) == 2
        for record in per_case:
            for key in ("id", "decision", "judge_overall", "judge_overall_golden",
                        "critic_score", "violations", "passed", "latency_seconds",
                        "llm_call_count"):
                assert key in record, f"{mode} per-case missing {key}"
            assert record["judge_overall"] is not None  # judge never failed
        metrics = report["modes"][mode]["metrics"]
        assert metrics["mean_judge_overall"] is not None
        assert metrics["critic_judge_consistency"]["pearson_r"] == pytest.approx(1.0)
    # this run's temp schemas are dropped; nothing new persists.
    # (A concurrent real eval may legitimately hold one; only the delta is ours.)
    assert _eval_schemas() <= preexisting


def test_report_provenance_complete(_test_dsn: str, tmp_path: Path, monkeypatch) -> None:
    report = _fake_eval_report(_test_dsn, tmp_path, monkeypatch)
    provenance = report["provenance"]
    for key in ("model", "case_file_sha256", "cases_path", "device"):
        assert provenance[key], f"provenance missing {key}"
    assert provenance["model"] == "test-model"
    assert report["judge_prompt_version"].startswith("2026.08.14")
    assert report["runner_version"]
    assert report["generated_at"]
    # critic-vs-judge consistency is present (soft independence signal)
    consistency = report["modes"]["order"]["metrics"]["critic_judge_consistency"]
    assert set(consistency) >= {"pearson_r", "mean_abs_delta", "n"}


def test_report_critic_judge_consistency_present(_test_dsn: str, tmp_path: Path, monkeypatch) -> None:
    report = _fake_eval_report(_test_dsn, tmp_path, monkeypatch)
    for mode in ("order", "image"):
        consistency = report["modes"][mode]["metrics"]["critic_judge_consistency"]
        # scripted scores are perfectly correlated -> independence warning fires
        assert consistency["pearson_r"] == pytest.approx(1.0)
        assert "independence_warning" in consistency


def test_report_performance_metrics(_test_dsn: str, tmp_path: Path, monkeypatch) -> None:
    report = _fake_eval_report(_test_dsn, tmp_path, monkeypatch)
    for mode in ("order", "image"):
        performance = report["modes"][mode]["metrics"]["performance"]
        assert performance["p50_latency_s"] >= 0
        assert performance["p95_latency_s"] >= performance["p50_latency_s"]
        assert performance["mean_llm_calls"] is not None
        for record in report["modes"][mode]["per_case"]:
            assert record["latency_seconds"] >= 0
            assert record["llm_call_count"] >= 0
