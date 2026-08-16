"""Evaluate StyleForge outfit generation on the official Polyvore Outfits (p-outfit) cases.

Runs the real three-agent recommendation chain over a per-mode isolated wardrobe
snapshot (``order`` = order-import / text-only, ``image`` = photo-import /
image-embedding) and scores both the top-1 generated outfit and the golden
outfit with the independent text-only judge (``IndependentJudge``). The report
covers judge quality, hard-assertion violations, gap/judge-failure rates,
critic-vs-judge consistency, provenance and per-case performance.

Experiment isolation: each mode gets its **own** schema on the *test* database
(``STYLEFORGE_TEST_DATABASE_DSN``), its own wardrobe user and its own embedding
dir, so the order (text-only, ``unbound`` images) and image (``available``
images) data shapes never mix. With ``--env`` (default) the env is **persistent**
and reused across runs: fixed schema per mode, cached wardrobe snapshot,
already-built embeddings, and a per-case journal that lets an interrupted run
resume without re-paying LLM cost; ``--prepare-only`` builds just the env. Without
``--env`` the old ephemeral behavior applies (random schema dropped in ``finally``).
Nothing is written to the primary database.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import gc
import hashlib
import json
import os
import re
import shutil
import statistics
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
APPS_API_ROOT = WORKSPACE_ROOT / "apps" / "api"
if str(APPS_API_ROOT) not in sys.path:
    sys.path.insert(0, str(APPS_API_ROOT))

from styleforge.common.console import configure_utf8_console  # noqa: E402
from styleforge.common.files import write_json_atomic  # noqa: E402
from styleforge.core.categories import infer_slot  # noqa: E402
from styleforge.core.config import Settings  # noqa: E402
from styleforge.core.rubric import aggregate_score  # noqa: E402
from styleforge.llm.client import llm_client_from_settings  # noqa: E402
from styleforge.agents.judge import IndependentJudge  # noqa: E402
from styleforge.llm.judge_prompts import JUDGE_PROMPT_VERSION  # noqa: E402
from styleforge.data.p_outfit import (  # noqa: E402
    POutfitItem,
    _FOLLOW_UP_MARKERS,
    normalize_p_outfit_item,
)
from styleforge.pipelines.uuid_mapping import ensure_uuids, reid_catalog_item  # noqa: E402
from styleforge.repositories.catalog_repository import upsert_items  # noqa: E402
from styleforge.repositories.database import connect, database_session, initialize_database  # noqa: E402
from styleforge.repositories.wardrobe_repository import add_items  # noqa: E402
from styleforge.vision.fashion_clip import FashionClipEncoder  # noqa: E402
from styleforge.workflow.graph import _critic_dimension_scores, StyleForgeWorkflow  # noqa: E402

_SCHEMA_VERSION = "styleforge.p-outfit-eval.v1"
_RUNNER_VERSION = "evaluate_p_outfit.v1"
_DEFAULT_PASS_MIN = 60
_HARD_ASSERTIONS = ("all_items_in_wardrobe", "has_required_slots")
_FIVE_DIMENSION_KEYS = (
    "request_relevance",
    "request_specificity",
    "outfit_coordination",
    "wearability",
    "freshness",
)

DEFAULT_CASES = Path(__file__).resolve().parents[1] / "cases" / "p_outfit.json"
DEFAULT_REPORT = WORKSPACE_ROOT / "artifacts" / "evaluation" / "p_outfit.json"


# --- case loading ---------------------------------------------------------------

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_cases(path: Path) -> list[dict[str, Any]]:
    """Validate the p-outfit case file and return the cases (with derived maps).

    Mirrors the P3 review contract: unique reviewed cases, ``golden ⊆ items``,
    request length 25-120, no follow-up markers, no answer leakage.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("p-outfit case file must contain a non-empty 'cases' array")
    if payload.get("case_count") != len(cases):
        raise ValueError("case_count does not match the cases array")

    seen: set[str] = set()
    for case in cases:
        case_id = str(case.get("id", "")).strip()
        if not case_id or case_id in seen:
            raise ValueError(f"empty or duplicate case id: {case_id!r}")
        seen.add(case_id)
        if not case.get("_reviewed"):
            raise ValueError(f"case {case_id} is not _reviewed (P3 freeze required)")
        request = str(case.get("user_request", "")).strip()
        if not (25 <= len(request) <= 120):
            raise ValueError(f"case {case_id} request length {len(request)} outside 25-120")
        for marker in _FOLLOW_UP_MARKERS:
            if marker in request:
                raise ValueError(f"case {case_id} contains follow-up marker {marker!r}")
        items = case.get("items", [])
        golden = [str(g) for g in case.get("golden_item_ids", [])]
        item_ids = {str(item.get("item_id", "")) for item in items}
        if set(golden) != item_ids:
            raise ValueError(f"case {case_id} golden_item_ids != items item_ids")
        for item in items:
            if not str(item.get("url_name", "")).strip():
                raise ValueError(f"case {case_id} item lacks url_name")
        # no-answer-leakage: request must not quote golden title/url_name tokens.
        lowered = request.lower()
        for item in items:
            for field in ("title", "url_name"):
                for token in re.findall(r"[a-zA-Z]{4,}", str(item.get(field, "") or "")):
                    if token.lower() in lowered:
                        raise ValueError(f"case {case_id} leaks golden {field} token {token!r}")
        case["_request"] = request
        case["_item_ids"] = item_ids
        case["_text_by_raw"] = {
            str(item["item_id"]): _item_text_block(item) for item in items
        }
    return cases


def _item_text_block(item: dict[str, Any]) -> str:
    return POutfitItem(
        item_id=str(item["item_id"]),
        semantic_category=str(item["semantic_category"]),
        item_type=str(item["item_type"]),
        title=str(item.get("title", "")),
        description=str(item.get("description", "")),
        url_name=str(item.get("url_name", "")),
    ).to_text_block()


def _scoped_dsn(dsn: str, schema: str) -> str:
    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}options=-csearch_path%3D{schema}"


def _create_schema(dsn: str, schema: str) -> None:
    admin = connect(dsn)
    try:
        admin.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        admin.commit()
    finally:
        admin.close()


def _load_journal(path: Path) -> dict[str, dict[str, Any]]:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_journal(path: Path, journal: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(journal, ensure_ascii=False), encoding="utf-8")


class _Journal:
    """Crash-safe per-case persistence.

    Holds ``{case_id: result}`` for one mode and rewrites the journal file after
    every completed case, so an interrupted run only loses the case currently in
    flight; a resumed run (same env dir) reuses every recorded case and pays no
    LLM cost for it. Writing the whole dict per case is fine at ~100 cases (a
    few hundred KB); the lock keeps concurrent workers from clobbering it.
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.data = _load_journal(path) if path is not None else {}

    @property
    def done_ids(self) -> set[str]:
        return set(self.data)

    def has(self, case_id: str) -> bool:
        return case_id in self.data

    def get(self, case_id: str) -> dict[str, Any]:
        return self.data[case_id]

    def record(self, case_id: str, result: dict[str, Any]) -> None:
        if self.path is None:
            return
        with self._lock:
            self.data[case_id] = result
            _save_journal(self.path, self.data)


def _drop_schema(dsn: str, schema: str) -> None:
    admin = connect(dsn)
    try:
        admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.commit()
    finally:
        admin.close()


# --- wardrobe snapshot ------------------------------------------------------------

def build_wardrobe_snapshot(
    *,
    mode: str,
    db_dsn: str,
    cases: list[dict[str, Any]],
    user_id: str,
    source_revision: str,
) -> dict[str, Any]:
    """Insert every golden item as a UUID-rebased catalog row for one user.

    Returns ``uuid_by_raw`` / ``raw_by_uuid`` (raw p-outfit id <-> UUID) plus
    the global per-raw text blocks and item types so the runner can resolve a
    generated outfit (which may draw items from *other* cases' wardrobes) back
    to judge input.
    """
    raw_to_uuid: dict[str, str] = {}
    all_items: dict[str, dict[str, Any]] = {}
    for case in cases:
        for item in case["items"]:
            raw = str(item["item_id"])
            all_items.setdefault(raw, item)

    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        ensure_uuids(connection, "polyvore", raw_to_uuid, list(all_items))
        catalog_items = []
        for raw, item in all_items.items():
            po = POutfitItem(
                item_id=raw,
                semantic_category=str(item["semantic_category"]),
                item_type=str(item["item_type"]),
                title=str(item.get("title", "")),
                description=str(item.get("description", "")),
                url_name=str(item.get("url_name", "")),
            )
            catalog_items.append(reid_catalog_item(normalize_p_outfit_item(po, mode=mode), raw_to_uuid))
        upsert_items(connection, catalog_items, source_revision)
        add_items(connection, user_id, [item.item_id for item in catalog_items])

    return {
        "uuid_by_raw": dict(raw_to_uuid),
        "raw_by_uuid": {value: key for key, value in raw_to_uuid.items()},
        "text_by_raw": {raw: _item_text_block(item) for raw, item in all_items.items()},
        "item_type_by_raw": {raw: str(item["item_type"]) for raw, item in all_items.items()},
        "item_count": len(all_items),
    }


def build_embeddings(
    *,
    mode: str,
    embedding_dir: Path,
    encoder: FashionClipEncoder,
    snapshot: dict[str, Any],
    image_root: Path | None,
) -> None:
    """Write the wardrobe embedding artifact for CatalogVectorStore.

    ``order``: L2-normalized FashionCLIP text embeddings of "title description
    url_name". ``image``: L2-normalized image embeddings of the official photos.
    """
    import numpy as np

    embedding_dir.mkdir(parents=True, exist_ok=True)
    ordered_uuids = list(snapshot["uuid_by_raw"].values())
    if mode == "order":
        texts = [snapshot["text_by_raw"][snapshot["raw_by_uuid"][uid]] for uid in ordered_uuids]
        matrix = encoder.encode_texts(texts)
    elif mode == "image":
        if image_root is None:
            raise ValueError("--image-root is required for image mode")
        from PIL import Image

        images = [
            Image.open(
                image_root / "images" / "nondisjoint" / "train" / f"{snapshot['raw_by_uuid'][uid]}.jpg"
            ).convert("RGB")
            for uid in ordered_uuids
        ]
        matrix = encoder.encode_images(images)
    else:
        raise ValueError(f"unknown mode: {mode!r}")

    if len(ordered_uuids) != matrix.shape[0]:
        raise RuntimeError("embedding rows do not match item ids")
    (embedding_dir / "item_ids.json").write_text(
        json.dumps(ordered_uuids, ensure_ascii=False), encoding="utf-8"
    )
    np.save(embedding_dir / "embeddings.npy", np.asarray(matrix, dtype=np.float32))
    (embedding_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "fashionclip-embeddings.v1",
                "status": "completed",
                "total_items": len(ordered_uuids),
                "dimension": int(matrix.shape[1]),
                "mode": mode,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def make_workflow(
    *,
    settings: Settings,
    db_dsn: str,
    embedding_dir: Path,
    llm_client: Any,
    model_dir: Path,
    device: str,
) -> StyleForgeWorkflow:
    """Deterministic Context Router (weather off) over the scoped schema."""
    eval_settings = dataclasses.replace(
        settings,
        database_dsn=db_dsn,
        embedding_dir=embedding_dir,
        llm_enabled=True,
        weather_enabled=False,
    )
    return StyleForgeWorkflow(
        database_path=db_dsn,
        embedding_dir=embedding_dir,
        model_dir=model_dir,
        device=device,
        llm_client=llm_client,
        settings=eval_settings,
        weather_tool=None,
    )


# --- per-case execution --------------------------------------------------------------

def _run_cases_parallel(
    *,
    cases: list[dict[str, Any]],
    settings: Settings,
    db_dsn: str,
    embedding_dir: Path,
    llm_client: Any,
    model_dir: Path,
    device: str,
    user_id: str,
    snapshot: dict[str, Any],
    max_workers: int,
    progress: Callable[[dict[str, Any]], None],
    journal: _Journal | None = None,
) -> list[dict[str, Any]]:
    """Run cases concurrently, one workflow per worker.

    Cases within a mode are independent and the DeepSeek client is thread-safe,
    so wall-clock drops by ~``max_workers``. Each worker owns one workflow
    (pre-built sequentially to avoid racing ``initialize_database`` DDL on the
    shared schema) and runs it serially; the read-only snapshot and the stateless
    judge are shared. One workflow per worker instead of per case matters: every
    ``StyleForgeWorkflow`` lazily builds its own FashionCLIP encoder (~1.2 GB on
    GPU), so one workflow per case would leave 100 encoders resident and exhaust
    the card (observed CUDA OOM at 30+ GiB reserved). ``progress`` is called as
    each case finishes; the returned list keeps case order. When ``journal`` is
    given, already-recorded case ids are reused (resume) and every fresh result
    is recorded before ``progress`` returns.
    """
    workers = min(max_workers, len(cases)) if cases else 0
    if workers < 1:
        return []
    workflows = [make_workflow(settings=settings, db_dsn=db_dsn, embedding_dir=embedding_dir,
                               llm_client=llm_client, model_dir=model_dir, device=device)
                 for _ in range(workers)]
    judges = [IndependentJudge() for _ in range(workers)]

    def run_chunk(worker_idx: int) -> list[tuple[str, dict[str, Any]]]:
        workflow, judge = workflows[worker_idx], judges[worker_idx]
        chunk = []
        for case in cases[worker_idx::workers]:
            case_id = case["id"]
            if journal is not None and journal.has(case_id):
                chunk.append((case_id, journal.get(case_id)))
                continue
            result = run_case(workflow=workflow, judge=judge, llm=llm_client,
                              case=case, user_id=user_id, snapshot=snapshot)
            if journal is not None:
                journal.record(case_id, result)
            chunk.append((case_id, result))
            progress(result)
        return chunk

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        chunks = list(pool.map(run_chunk, range(workers)))
    results = {case_id: result for chunk in chunks for case_id, result in chunk}
    return [results[case["id"]] for case in cases]

def _generated_outfit(payload: dict[str, Any]) -> list[str]:
    recommendations = (payload.get("result") or {}).get("recommendations") or []
    if not recommendations:
        return []
    rec = recommendations[0]
    slots = rec.get("slot_items") or {}
    return list(slots.values()) if slots else list(rec.get("item_ids") or [])


def _has_required_slots(item_types: list[str]) -> bool:
    slots = {infer_slot(t) for t in item_types}
    return {"top", "bottom", "footwear"} <= slots or {"one_piece", "footwear"} <= slots


def _scored_outfit_ids(critic_output: dict[str, Any] | None) -> list[str]:
    if not critic_output:
        return []
    ids: list[str] = []
    assessment = critic_output.get("outfit_assessment", {})
    if assessment.get("outfit_id"):
        ids.append(assessment["outfit_id"])
    ids.extend(
        alt.get("outfit_id")
        for alt in critic_output.get("alternatives", [])
        if alt.get("outfit_id")
    )
    return ids


def _critic_score_for(
    critic_output: dict[str, Any] | None,
    outfit_id: str,
) -> float:
    dims = _critic_dimension_scores(critic_output, outfit_id)
    return aggregate_score(dims) if dims else 0.0


def _match_generated_to_proposal(
    payload: dict[str, Any],
    generated_uuids: list[str],
    default: str,
) -> str:
    """Return the critic-scored outfit id whose item set equals the presented top-1."""
    if not generated_uuids:
        return default
    target = set(generated_uuids)
    for proposal in payload.get("proposals") or []:
        if set(proposal.get("item_ids") or []) == target:
            return proposal.get("outfit_id") or default
    return default


def run_case(
    *,
    workflow: StyleForgeWorkflow,
    judge: IndependentJudge,
    llm: Any,
    case: dict[str, Any],
    user_id: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """One case: generate, score generated + golden with the judge, record everything."""
    started = time.perf_counter()
    payload = workflow.recommend_payload(user_id=user_id, request=case["_request"], max_results=3)
    elapsed = round(time.perf_counter() - started, 3)

    decision = payload.get("decision") or ""
    llm_call_count = payload.get("llm_call_count", 0)
    llm_attempts = payload.get("llm_attempts", 0)
    fallback_count = payload.get("fallback_count", 0)
    degraded_reason = payload.get("degraded_reason")
    raw_by_uuid = snapshot["raw_by_uuid"]
    text_by_raw = snapshot["text_by_raw"]
    item_type_by_raw = snapshot["item_type_by_raw"]

    generated_uuids = _generated_outfit(payload)
    generated_raw = [raw_by_uuid[uid] for uid in generated_uuids if raw_by_uuid.get(uid)]
    generated_texts = [text_by_raw[raw] for raw in generated_raw if raw in text_by_raw]
    gen_types = [item_type_by_raw[raw] for raw in generated_raw if raw in item_type_by_raw]
    golden_texts = list(case["_text_by_raw"].values())

    all_in_wardrobe = bool(generated_uuids) and all(uid in raw_by_uuid for uid in generated_uuids)
    has_required = _has_required_slots(gen_types)

    judge_gen = judge.score_outfit(
        user_request=case["_request"], item_texts=generated_texts, llm=llm, outfit_id="generated"
    )
    judge_golden = judge.score_outfit(
        user_request=case["_request"], item_texts=golden_texts, llm=llm, outfit_id="golden"
    )

    critic_output = payload.get("critic")
    preferred_id = (critic_output or {}).get("outfit_assessment", {}).get("outfit_id", "")
    critic_outfit_id = _match_generated_to_proposal(payload, generated_uuids, preferred_id)
    critic_score = _critic_score_for(critic_output, critic_outfit_id)
    critic_all = [
        _critic_score_for(critic_output, outfit_id) for outfit_id in _scored_outfit_ids(critic_output)
    ]
    if critic_score == 0.0 and critic_all:
        critic_score = critic_all[0]  # fall back to the critic's preferred outfit

    gen_scores = judge_gen[0]
    golden_scores = judge_golden[0]
    judge_overall = aggregate_score(gen_scores.to_dict()) if gen_scores else None
    judge_overall_golden = aggregate_score(golden_scores.to_dict()) if golden_scores else None
    judge_failed = gen_scores is None or golden_scores is None

    violations: list[str] = []
    if not all_in_wardrobe:
        violations.append("all_items_in_wardrobe")
    if not has_required:
        violations.append("has_required_slots")
    if not generated_uuids:
        violations.append("no_recommendation")
    gap = decision in {"wardrobe_gap", "infeasible"} or not generated_uuids
    passed = (
        (judge_overall is not None and judge_overall >= _DEFAULT_PASS_MIN) and not violations
    )

    return {
        "id": case["id"],
        "source_set_id": case["source_set_id"],
        "source_title": case["source_title"],
        "user_request": case["_request"],
        "decision": decision,
        "llm_call_count": llm_call_count,
        "llm_attempts": llm_attempts,
        "fallback_count": fallback_count,
        "degraded_reason": degraded_reason,
        "latency_seconds": elapsed,
        "gap": gap,
        "judge_failed": judge_failed,
        "judge_overall": judge_overall,
        "judge_overall_golden": judge_overall_golden,
        "judge_dimensions": gen_scores.to_dict() if gen_scores else None,
        "judge_dimensions_golden": golden_scores.to_dict() if golden_scores else None,
        "critic_outfit_id": critic_outfit_id,
        "critic_score": critic_score,
        "critic_scores": critic_all,
        "generated_item_texts": generated_texts,
        "generated_raw": generated_raw,
        "golden_item_texts": golden_texts,
        "violations": violations,
        "hard_assertions": list(_HARD_ASSERTIONS),
        "passed": passed,
    }


# --- aggregation ---------------------------------------------------------------------

def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 3) if values else None


def _stddev(values: list[float]) -> float | None:
    return round(statistics.stdev(values), 3) if len(values) >= 2 else None


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation over paired non-null lists (pure python)."""
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    den = (sum((x - mean_x) ** 2 for x in xs) ** 0.5) * (sum((y - mean_y) ** 2 for y in ys) ** 0.5)
    if den == 0:
        return None
    return round(num / den, 4)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * (pct / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return round(ordered[lower], 3)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower), 3)


def aggregate_metrics(per_case: list[dict[str, Any]]) -> dict[str, Any]:
    """Mode-level metrics over per-case records (pure function, unit-testable)."""
    scored = [c for c in per_case if c["judge_overall"] is not None]
    gaps = [c for c in per_case if c["gap"]]
    passed = [c for c in per_case if c["passed"]]

    def dim_mean(key: str) -> float | None:
        values = [
            c["judge_dimensions"][key]
            for c in scored
            if c["judge_dimensions"] and key in c["judge_dimensions"]
        ]
        return _mean(values)

    gen_overall = [float(c["judge_overall"]) for c in scored]
    golden_overall = [
        float(c["judge_overall_golden"]) for c in scored if c["judge_overall_golden"] is not None
    ]
    paired_delta = [
        float(c["judge_overall"]) - float(c["judge_overall_golden"])
        for c in scored
        if c["judge_overall_golden"] is not None
    ]
    critic_judge_pairs = [
        (float(c["judge_overall"]), float(c["critic_score"]))
        for c in per_case
        if c["judge_overall"] is not None and c["critic_score"] > 0
    ]
    latency = [float(c["latency_seconds"]) for c in per_case]
    llm_calls = [float(c["llm_call_count"]) for c in per_case]

    return {
        "n_cases": len(per_case),
        "mean_judge_overall": _mean(gen_overall),
        "std_judge_overall": _stddev(gen_overall),
        "mean_judge_overall_golden": _mean(golden_overall),
        "delta_golden_vs_generated": _mean([-d for d in paired_delta]),
        "mean_dimension_scores": {key: dim_mean(key) for key in _FIVE_DIMENSION_KEYS},
        "pass_rate": round(len(passed) / len(per_case), 4) if per_case else 0.0,
        "hard_violation_rate": (
            round(len([c for c in per_case if c["violations"]]) / len(per_case), 4)
            if per_case
            else 0.0
        ),
        "gap_rate": round(len(gaps) / len(per_case), 4) if per_case else 0.0,
        "judge_failure_rate": (
            round(len([c for c in per_case if c["judge_failed"]]) / len(per_case), 4)
            if per_case
            else 0.0
        ),
        "critic_judge_consistency": {
            "pearson_r": pearson(
                [pair[0] for pair in critic_judge_pairs],
                [pair[1] for pair in critic_judge_pairs],
            ),
            "mean_abs_delta": (
                round(statistics.fmean([abs(a - b) for a, b in critic_judge_pairs]), 3)
                if critic_judge_pairs
                else None
            ),
            "n": len(critic_judge_pairs),
        },
        "performance": {
            "p50_latency_s": _percentile(latency, 50),
            "p95_latency_s": _percentile(latency, 95),
            "mean_llm_calls": _mean(llm_calls),
        },
    }


# --- runner + CLI ------------------------------------------------------------------------

def _build_encoder(settings: Settings, model_dir: Path, device: str) -> FashionClipEncoder:
    del settings
    # float16 autocast on CUDA, float32 on CPU.
    precision = "float16" if device.startswith("cuda") else "float32"
    return FashionClipEncoder(model_dir, device=device, precision=precision)


def evaluate_p_outfit(
    *,
    cases_path: Path,
    mode: str,
    report_path: Path,
    db_dsn: str,
    settings: Settings,
    model_dir: Path,
    device: str,
    image_root: Path | None,
    seed: int,
    max_cases: int | None,
    llm_client: Any | None = None,
    encoder_factory: Callable[..., FashionClipEncoder] | None = None,
    parallel: int = 1,
    env_dir: Path | None = None,
    prepare_only: bool = False,
) -> dict[str, Any]:
    del report_path, seed  # report writing and fixed seed are main() concerns
    if mode not in {"order", "image", "both"}:
        raise ValueError("mode must be 'order', 'image' or 'both'")
    cases = load_cases(cases_path)
    if max_cases is not None and max_cases > 0:
        cases = cases[:max_cases]
    if llm_client is None:
        llm_client = llm_client_from_settings(settings)
        if llm_client is None:
            raise SystemExit("DEEPSEEK_API_KEY is not set; p-outfit eval requires a real LLM.")

    modes_to_run = ("order", "image") if mode == "both" else (mode,)
    per_mode: dict[str, list[dict[str, Any]]] = {}
    for current_mode in modes_to_run:
        # With an env dir the schema/user/wardrobe/embeddings are persistent and
        # reusable across runs (prepared once, then resumed), so the schema name
        # is fixed rather than a throwaway uuid. Without one, keep the old
        # ephemeral behavior (random schema, dropped in ``finally``).
        schema = (
            f"eval_{current_mode}"
            if env_dir is not None
            else f"eval_{current_mode}_{uuid.uuid4().hex[:8]}"
        )
        scoped = _scoped_dsn(db_dsn, schema)
        _create_schema(db_dsn, schema)
        mode_env = env_dir / current_mode if env_dir is not None else None
        if mode_env is not None:
            mode_env.mkdir(parents=True, exist_ok=True)
        snapshot_path = mode_env / "snapshot.json" if mode_env is not None else None
        embedding_dir = (
            mode_env / "embeddings"
            if mode_env is not None
            else Path(tempfile.mkdtemp(prefix=f"poutfit-{current_mode}-"))
        )
        journal = _Journal(mode_env / "journal.json") if mode_env is not None else None
        try:
            user_id = f"eval-{current_mode}"
            if snapshot_path is not None and snapshot_path.is_file():
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            else:
                snapshot = build_wardrobe_snapshot(
                    mode=current_mode,
                    db_dsn=scoped,
                    cases=cases,
                    user_id=user_id,
                    source_revision=f"p-outfit-{current_mode}",
                )
                if snapshot_path is not None:
                    snapshot_path.write_text(
                        json.dumps(snapshot, ensure_ascii=False), encoding="utf-8"
                    )
            if (embedding_dir / "manifest.json").is_file():
                print(f"[{current_mode}] embeddings already built; reusing {embedding_dir}")
            else:
                encoder = (encoder_factory or _build_encoder)(settings, model_dir, device)
                try:
                    build_embeddings(
                        mode=current_mode,
                        embedding_dir=embedding_dir,
                        encoder=encoder,
                        snapshot=snapshot,
                        image_root=image_root,
                    )
                finally:
                    del encoder
                    gc.collect()
            if prepare_only:
                print(
                    f"[{current_mode}] env prepared (schema={schema}, "
                    f"snapshot={snapshot['item_count']} items, embeddings ready)"
                )
                continue
            done = len(journal.done_ids) if journal is not None else 0
            if done:
                print(
                    f"[{current_mode}] resuming: {done}/{len(cases)} cases already recorded "
                    f"in journal"
                )

            def _progress(result: dict[str, Any]) -> None:
                print(
                    f"[{current_mode}] {result['id']} decision={result['decision']} "
                    f"judge={result['judge_overall']} calls={result['llm_call_count']}",
                    flush=True,
                )

            if parallel and parallel > 1:
                mode_results = _run_cases_parallel(
                    cases=cases,
                    settings=settings,
                    db_dsn=scoped,
                    embedding_dir=embedding_dir,
                    llm_client=llm_client,
                    model_dir=model_dir,
                    device=device,
                    user_id=user_id,
                    snapshot=snapshot,
                    max_workers=parallel,
                    progress=_progress,
                    journal=journal,
                )
            else:
                workflow = make_workflow(
                    settings=settings,
                    db_dsn=scoped,
                    embedding_dir=embedding_dir,
                    llm_client=llm_client,
                    model_dir=model_dir,
                    device=device,
                )
                judge = IndependentJudge()
                mode_results = []
                for case in cases:
                    case_id = case["id"]
                    if journal is not None and journal.has(case_id):
                        mode_results.append(journal.get(case_id))
                        continue
                    result = run_case(
                        workflow=workflow,
                        judge=judge,
                        llm=llm_client,
                        case=case,
                        user_id=user_id,
                        snapshot=snapshot,
                    )
                    if journal is not None:
                        journal.record(case_id, result)
                    mode_results.append(result)
                    _progress(result)
            per_mode[current_mode] = mode_results
        finally:
            if mode_env is None:
                shutil.rmtree(embedding_dir, ignore_errors=True)
                _drop_schema(db_dsn, schema)

    modes_report: dict[str, Any] = {}
    comparison: dict[str, Any] = {}
    for current_mode, results in per_mode.items():
        metrics = aggregate_metrics(results)
        consistency = metrics["critic_judge_consistency"]
        if consistency.get("pearson_r") is not None and consistency["pearson_r"] >= 0.9:
            consistency["independence_warning"] = (
                "judge-critic correlation >= 0.9; check judge prompt independence"
            )
        modes_report[current_mode] = {"metrics": metrics, "per_case": results}

    if "order" in per_mode and "image" in per_mode:
        order = modes_report["order"]["metrics"]
        image = modes_report["image"]["metrics"]
        comparison = {
            "delta_image_minus_order": {
                "mean_judge_overall": round(
                    (image["mean_judge_overall"] or 0) - (order["mean_judge_overall"] or 0), 3
                ),
                "pass_rate": round(image["pass_rate"] - order["pass_rate"], 4),
                "hard_violation_rate": round(
                    image["hard_violation_rate"] - order["hard_violation_rate"], 4
                ),
                "gap_rate": round(image["gap_rate"] - order["gap_rate"], 4),
            },
            "note": "descriptive delta only; paired significance is reported separately in the analysis stage",
        }

    return {
        "schema_version": _SCHEMA_VERSION,
        "runner_version": _RUNNER_VERSION,
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": {
            "model": settings.deepseek_model,
            "case_file_sha256": _sha256(cases_path),
            "cases_path": str(cases_path.resolve()),
            "device": device,
            "image_root": str(image_root) if image_root else None,
        },
        "pass_criteria": {
            "min_judge_overall": _DEFAULT_PASS_MIN,
            "hard_assertions": list(_HARD_ASSERTIONS),
            "preregistered_at": "2026-08-14 (P3 freeze; see evals/cases/p_outfit.json notes)",
        },
        "case_count": len(cases),
        "modes": modes_report,
        "comparison": comparison,
        "limitations": [
            "No official color field; order mode carries text only (weak-data nature).",
            "Judge is text-only DeepSeek over item text blocks; no image input.",
            "semantic_category->item_type mapping is coarse (11 classes).",
            "Generated-outfit judge text uses the wardrobe-wide text map (may draw items from other cases).",
            "critic-vs-judge compares the critic's preferred outfit when the presented top-1 has no exact proposal match.",
            "Ablation (Full/-Critic/-Retrieval) and Public Benchmark metrics are separate experimental runs.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    default_dsn = os.getenv("STYLEFORGE_TEST_DATABASE_DSN") or settings.database_dsn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["order", "image", "both"], default="both")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--database", default=default_dsn)
    parser.add_argument("--image-root", type=Path, default=None)
    parser.add_argument("--model-dir", type=Path, default=settings.artifact_root / "models")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--seed", type=int, default=20260814)
    parser.add_argument(
        "--parallel",
        type=int,
        default=4,
        help="concurrent cases per mode (cases are independent; DeepSeek is I/O-bound)",
    )
    parser.add_argument(
        "--env",
        type=Path,
        default=Path("artifacts/evaluation/env"),
        help=(
            "persistent env dir: fixed schema, wardrobe snapshot, embeddings and "
            "per-case journal per mode live here and are reused across runs "
            "(prepare once, resume on crash)"
        ),
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="build the wardrobe + embeddings env for each mode and exit without "
        "running any cases (no LLM calls)",
    )
    return parser


def main() -> None:
    configure_utf8_console()
    args = build_parser().parse_args()
    if not args.database:
        raise SystemExit(
            "Neither STYLEFORGE_TEST_DATABASE_DSN nor STYLEFORGE_DATABASE_DSN is set"
        )
    settings = Settings.from_env()
    report = evaluate_p_outfit(
        cases_path=args.cases,
        mode=args.mode,
        report_path=args.report,
        db_dsn=args.database,
        settings=settings,
        model_dir=args.model_dir,
        device=args.device,
        image_root=args.image_root,
        seed=args.seed,
        max_cases=args.max_cases,
        parallel=args.parallel,
        env_dir=args.env,
        prepare_only=args.prepare_only,
    )
    if args.prepare_only:
        return
    write_json_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
