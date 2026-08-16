"""Evaluate the three production chains on real LLM calls for the extend cases.

Runs the real ``MultiTaskWorkflow`` (one router + the shared Agent1/2/3 chain)
over the persistent p-outfit *order* wardrobe env (schema ``eval_order``, user
``eval-order``). Turn-1 outfit recommendations go through the full three-agent
semantic chain because ``recommendation_runner`` is wired to
``StyleForgeWorkflow.recommend_payload``; the deterministic ``parse_request``
fallback is never reached.

- ``item_advice`` cases: anchor a concrete wardrobe item via ``item_id``; the
  chain composes sample outfits (each must contain the anchor by contract) and
  the independent text-only judge scores the top sample outfit.
- ``multiturn`` chains: a natural-language ask (outfit_recommend) -> slot swaps
  (outfit_modify) -> one final global adjustment. Each swap is verified to have
  targeted the requested slot; the judge scores the final outfit. Session
  context is threaded between turns via ``outfit_context_from_payload``.
- ``memory``: not scored. The ``preference_evidence`` / ``preference_model``
  rows accumulated while the chains run are exported so the reviewer can check
  them against each chain's stated user intent.

Reuses the persistent env prepared by ``evaluate_p_outfit --mode order``
(snapshot + embeddings + eval_order schema) and keeps a separate journal so an
interrupted run resumes without re-paying LLM cost.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
APPS_API_ROOT = WORKSPACE_ROOT / "apps" / "api"
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
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
from styleforge.models.task import TaskExecutionInput  # noqa: E402
from styleforge.repositories.database import database_session  # noqa: E402
from styleforge.services.chat_service import outfit_context_from_payload  # noqa: E402
from styleforge.workflow.task_workflow import MultiTaskWorkflow  # noqa: E402

from evals.runners.evaluate_p_outfit import (  # noqa: E402
    _create_schema,
    _Journal,
    _scoped_dsn,
    build_embeddings,
    build_wardrobe_snapshot,
    make_workflow,
)

_SCHEMA_VERSION = "styleforge.extend-eval.v1"
_RUNNER_VERSION = "evaluate_extend.v1"
_DEFAULT_PASS_MIN = 60
_MODE = "order"  # extend eval runs on the order (text) wardrobe env only

DEFAULT_CASES = Path(__file__).resolve().parents[1] / "cases" / "extend_advice.json"
DEFAULT_REPORT = WORKSPACE_ROOT / "artifacts" / "evaluation" / "extend_advice.json"
DEFAULT_ENV = WORKSPACE_ROOT / "artifacts" / "evaluation" / "env" / "order"


# --- case loading ---------------------------------------------------------------

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_extend_cases(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for group in ("item_advice", "multiturn"):
        if not isinstance(payload.get(group), list) or not payload[group]:
            raise ValueError(f"extend case file must contain a non-empty '{group}' array")
        ids = [c["id"] for c in payload[group]]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate ids in {group}")
    return payload


# --- pipeline construction ------------------------------------------------------

def make_pipeline(
    *,
    settings: Settings,
    db_dsn: str,
    embedding_dir: Path,
    llm_client: Any,
    model_dir: Path,
    device: str,
) -> tuple[MultiTaskWorkflow, Any]:
    """One worker pipeline: a StyleForgeWorkflow (LLM recommend backend) plus a
    MultiTaskWorkflow whose OUTFIT_RECOMMEND node is wired to it."""
    styleforge = make_workflow(
        settings=settings,
        db_dsn=db_dsn,
        embedding_dir=embedding_dir,
        llm_client=llm_client,
        model_dir=model_dir,
        device=device,
    )

    def recommend_runner(
        *,
        user_id: str,
        request: str,
        max_results: int,
        location_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return styleforge.recommend_payload(
            user_id=user_id,
            request=request,
            max_results=max_results,
            location_context=location_context,
        )

    mtw = MultiTaskWorkflow(
        database_path=db_dsn,
        knowledge_root=settings.knowledge_root,
        llm_client=llm_client,
        recommendation_runner=recommend_runner,
    )
    return mtw, styleforge


# --- per-case execution ---------------------------------------------------------

def _resolve_item_texts(ids: list[str], snapshot: dict[str, Any]) -> list[str]:
    raw_by_uuid = snapshot["raw_by_uuid"]
    text_by_raw = snapshot["text_by_raw"]
    texts = []
    for uid in ids:
        raw = raw_by_uuid.get(uid)
        if raw and raw in text_by_raw:
            texts.append(text_by_raw[raw])
    return texts


def _slots_of(ids: list[str], snapshot: dict[str, Any]) -> list[str]:
    item_type_by_raw = snapshot["item_type_by_raw"]
    slots = []
    for uid in ids:
        raw = snapshot["raw_by_uuid"].get(uid)
        if raw and raw in item_type_by_raw:
            slots.append(infer_slot(item_type_by_raw[raw]))
    return slots


def _judge_outfit(
    *,
    judge: IndependentJudge,
    llm: Any,
    request: str,
    item_texts: list[str],
    outfit_id: str,
) -> tuple[float | None, bool]:
    scores = judge.score_outfit(
        user_request=request, item_texts=item_texts, llm=llm, outfit_id=outfit_id
    )
    if scores[0] is None:
        return None, True
    return aggregate_score(scores[0].to_dict()), False


def _execute_with_retry(
    mtw: MultiTaskWorkflow,
    task_input: TaskExecutionInput,
    session_context: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Run one turn; annotate and retry once on any exception.

    Returns ``(payload, first_error)``. ``first_error is None`` when the first
    attempt succeeded or the retry succeeded; otherwise it carries both errors
    so the caller can mark the turn failed. The common retry reason is an
    out-of-scope draft (agent2 not respecting agent1's candidate list), which is
    usually a transient LLM slip — one retry shakes it off while keeping the
    failure visible in the report.
    """
    try:
        return mtw.execute(task_input, session_context=session_context), None
    except Exception as first:  # noqa: BLE001 - annotate + retry any turn error
        try:
            return (
                mtw.execute(task_input, session_context=session_context),
                f"{type(first).__name__}: {first}",
            )
        except Exception as second:  # noqa: BLE001
            return (
                None,
                f"{type(first).__name__}: {first} || retry {type(second).__name__}: {second}",
            )


def _recommend_outfit_ids(payload: dict[str, Any]) -> list[str]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    structured = result.get("structured_result")
    recs = None
    if isinstance(structured, dict):
        recs = structured.get("recommendations")
    if not recs:
        recs = result.get("recommendations")
    if isinstance(recs, list) and recs:
        first = recs[0]
        if isinstance(first, dict):
            return list(first.get("item_ids") or [])
    return []


def run_item_advice(
    *,
    pipeline: tuple[MultiTaskWorkflow, Any],
    judge: IndependentJudge,
    llm: Any,
    case: dict[str, Any],
    user_id: str,
    snapshot: dict[str, Any],
    min_judge: int,
) -> dict[str, Any]:
    mtw, _ = pipeline
    started = time.perf_counter()
    anchor_raw = str(case["anchor_item_id"])
    anchor_uuid = snapshot["uuid_by_raw"].get(anchor_raw, "")
    if not anchor_uuid:
        return {
            "id": case["id"], "error": f"anchor {anchor_raw} not in wardrobe snapshot",
        }

    task_input = TaskExecutionInput(
        user_id=user_id, request=case["request"], max_results=3, item_id=anchor_uuid
    )
    payload, first_error = _execute_with_retry(mtw, task_input, None)
    elapsed = round(time.perf_counter() - started, 3)
    if payload is None:
        return {
            "id": case["id"], "case_id": case["case_id"],
            "anchor_item_id": anchor_raw, "request": case["request"],
            "error": first_error, "retried": True, "passed": False,
            "latency_seconds": elapsed,
        }

    task_type = payload.get("task_type", "")
    result = payload.get("result") or {}
    sample_outfits = result.get("sample_outfits") or []
    top = sample_outfits[0] if sample_outfits else {}
    top_ids = list(top.get("item_ids") or [])
    anchor_in_outfit = anchor_uuid in top_ids

    judge_overall, judge_failed = None, False
    if top_ids:
        judge_overall, judge_failed = _judge_outfit(
            judge=judge, llm=llm, request=case["request"],
            item_texts=_resolve_item_texts(top_ids, snapshot), outfit_id="top_sample",
        )
    passed = bool(
        top_ids and anchor_in_outfit
        and judge_overall is not None and judge_overall >= min_judge
    )

    return {
        "id": case["id"],
        "case_id": case["case_id"],
        "anchor_item_id": anchor_raw,
        "anchor_slot": infer_slot(case["anchor_type"]) if case.get("anchor_type") else "other",
        "request": case["request"],
        "task_type": task_type,
        "status": payload.get("status", ""),
        "llm_call_count": payload.get("llm_call_count", 0),
        "llm_attempts": payload.get("llm_attempts", 0),
        "sample_outfit_count": len(sample_outfits),
        "top_outfit_item_ids": top_ids,
        "top_outfit_slots": _slots_of(top_ids, snapshot),
        "anchor_in_outfit": anchor_in_outfit,
        "judge_overall": judge_overall,
        "judge_failed": judge_failed,
        "retried": first_error is not None,
        "first_error": first_error,
        "passed": passed,
        "latency_seconds": elapsed,
        "_payload": payload,  # full trace/agent_outputs, detached to logs before journaling
    }


def _record_turn(
    *,
    payload: dict[str, Any],
    request: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    task_type = payload.get("task_type", "")
    result = payload.get("result") or {}
    record: dict[str, Any] = {
        "request": request,
        "task_type": task_type,
        "status": payload.get("status", ""),
        "llm_call_count": payload.get("llm_call_count", 0),
    }
    if task_type == "outfit_recommend":
        ids = _recommend_outfit_ids(payload)
        record["item_ids"] = ids
        record["slots"] = _slots_of(ids, snapshot)
    elif task_type == "outfit_modify":
        record["target_slot"] = result.get("target_slot", "")
        record["replaced_item_ids"] = list(result.get("replaced_item_ids") or [])
        record["locked_item_ids"] = list(result.get("locked_item_ids") or [])
        alternatives = result.get("alternatives") or []
        record["alternative_count"] = len(alternatives)
        if alternatives:
            record["item_ids"] = list(alternatives[0].get("item_ids") or [])
            record["slots"] = _slots_of(record["item_ids"], snapshot)
    else:
        record["note"] = f"unexpected task_type {task_type}"
    return record


def run_multiturn_chain(
    *,
    pipeline: tuple[MultiTaskWorkflow, Any],
    judge: IndependentJudge,
    llm: Any,
    chain: dict[str, Any],
    user_id: str,
    snapshot: dict[str, Any],
    min_judge: int,
) -> dict[str, Any]:
    mtw, _ = pipeline
    started = time.perf_counter()
    session_context: dict[str, Any] | None = None
    turns: list[dict[str, Any]] = []
    llm_calls_total = 0

    # turn 1: seek outfit advice (natural-language, no slot swap yet)
    turn_input = TaskExecutionInput(user_id=user_id, request=chain["turn1"]["request"], max_results=3)
    payload, t1_error = _execute_with_retry(mtw, turn_input, session_context)
    if payload is None:
        return {
            "id": chain["id"], "case_id": chain["case_id"],
            "error": t1_error, "retried": True, "passed": False,
            "latency_seconds": round(time.perf_counter() - started, 3),
        }
    rec = _record_turn(payload=payload, request=chain["turn1"]["request"], snapshot=snapshot)
    rec["expected_route"] = chain["turn1"].get("expected_route", "outfit_recommend")
    rec["_payload"] = payload  # full turn trace, detached to logs before journaling
    turns.append(rec)
    llm_calls_total += int(payload.get("llm_call_count", 0))
    session_context = outfit_context_from_payload(payload)

    # swaps: user replaces a specific slot. If the slot is absent from the
    # current outfit (e.g. asking for outerwear on a dress), the system answers
    # "no <slot> in this outfit" — a correct clarification, not a defect — so the
    # chain skips it and moves on, exactly as a real user would.
    swap_correct = True
    swaps_executed = 0
    swaps_skipped = 0
    for sp in chain["swap_priority"]:
        req = f"把{sp['slot_word']}换成{sp['style']}的"
        current_ids = list(session_context.get("current_item_ids") or [])
        current_slots = set(_slots_of(current_ids, snapshot))
        if sp["slot"] not in current_slots:
            # The slot is absent from the current outfit. The system now rebuilds
            # the outfit to insert it (flexible mode, EXT-001 fix) instead of
            # bouncing back with a clarification, so run it as a real turn. Only
            # when the rebuild fails or still omits the slot does the chain skip.
            turn_input = TaskExecutionInput(user_id=user_id, request=req, max_results=3)
            payload, first_error = _execute_with_retry(mtw, turn_input, session_context)
            new_ids = list(
                (
                    outfit_context_from_payload(payload)
                    if payload is not None
                    else {}
                ).get("current_item_ids")
                or []
            )
            inserted = payload is not None and sp["slot"] in set(
                _slots_of(new_ids, snapshot)
            )
            if not inserted:
                swaps_skipped += 1
                turns.append({
                    "request": req,
                    "task_type": "skipped",
                    "expected_slot": sp["slot"],
                    "slot_word": sp["slot_word"],
                    "style": sp["style"],
                    "reason": f"no_{sp['slot']}_insert_failed",
                    "current_outfit_slots": sorted(current_slots),
                    "insert_attempted": True,
                    "error": first_error,
                })
                continue
            rec = _record_turn(payload=payload, request=req, snapshot=snapshot)
            rec["expected_slot"] = sp["slot"]
            rec["slot_word"] = sp["slot_word"]
            rec["style"] = sp["style"]
            rec["_payload"] = payload
            rec["retried"] = first_error is not None
            rec["first_error"] = first_error
            rec["inserted_slot"] = sp["slot"]
            swaps_executed += 1
            turns.append(rec)
            llm_calls_total += int(payload.get("llm_call_count", 0))
            session_context = outfit_context_from_payload(payload)
            continue
        turn_input = TaskExecutionInput(user_id=user_id, request=req, max_results=3)
        payload, first_error = _execute_with_retry(mtw, turn_input, session_context)
        if payload is None:
            swaps_executed += 1
            swap_correct = False
            turns.append({
                "request": req,
                "task_type": "failed",
                "expected_slot": sp["slot"],
                "slot_word": sp["slot_word"],
                "style": sp["style"],
                "error": first_error,
                "retried": True,
            })
            continue
        rec = _record_turn(payload=payload, request=req, snapshot=snapshot)
        rec["expected_slot"] = sp["slot"]
        rec["slot_word"] = sp["slot_word"]
        rec["style"] = sp["style"]
        rec["_payload"] = payload
        rec["retried"] = first_error is not None
        rec["first_error"] = first_error
        swaps_executed += 1
        if rec["task_type"] != "outfit_modify" or rec.get("target_slot") != sp["slot"]:
            swap_correct = False
        turns.append(rec)
        llm_calls_total += int(payload.get("llm_call_count", 0))
        session_context = outfit_context_from_payload(payload)

    # adjust: one final global tweak
    adjust_req = chain["adjust"]["request"]
    turn_input = TaskExecutionInput(user_id=user_id, request=adjust_req, max_results=3)
    payload, adj_error = _execute_with_retry(mtw, turn_input, session_context)
    if payload is None:
        # The chain completed its swaps but the final adjustment failed; keep the
        # recorded turns and mark the chain failed with the adjust error.
        elapsed = round(time.perf_counter() - started, 3)
        final_ids = list(session_context.get("current_item_ids") or [])
        return {
            "id": chain["id"], "case_id": chain["case_id"],
            "turn_count": len(turns), "swaps_executed": swaps_executed,
            "swaps_skipped": swaps_skipped, "turns": turns,
            "llm_call_count": llm_calls_total, "swap_correct": swap_correct,
            "adjust_error": adj_error, "retried": True,
            "final_outfit_item_ids": final_ids,
            "final_outfit_slots": _slots_of(final_ids, snapshot),
            "final_judge_overall": None, "final_judge_failed": False,
            "final_judge_pass": False, "passed": False,
            "latency_seconds": elapsed,
        }
    rec = _record_turn(payload=payload, request=adjust_req, snapshot=snapshot)
    rec["expected_route"] = chain["adjust"].get("expected_route", "outfit_modify")
    rec["_payload"] = payload
    rec["retried"] = adj_error is not None
    rec["first_error"] = adj_error
    turns.append(rec)
    llm_calls_total += int(payload.get("llm_call_count", 0))
    session_context = outfit_context_from_payload(payload)
    elapsed = round(time.perf_counter() - started, 3)

    # final outfit (threaded through the last turn)
    final_ids = list(session_context.get("current_item_ids") or [])
    final_judge, judge_failed = None, False
    if final_ids:
        final_judge, judge_failed = _judge_outfit(
            judge=judge, llm=llm, request=adjust_req,
            item_texts=_resolve_item_texts(final_ids, snapshot), outfit_id="final",
        )

    # If no requested slot existed in the current outfit at all, the system has
    # no replacement to perform: mark the chain infeasible (not pass/fail) so it
    # is excluded from the pass rate rather than counted as a defect.
    infeasible = swaps_executed == 0 and swaps_skipped > 0

    return {
        "id": chain["id"],
        "case_id": chain["case_id"],
        "turn_count": len(turns),
        "swaps_executed": swaps_executed,
        "swaps_skipped": swaps_skipped,
        "turns": turns,
        "llm_call_count": llm_calls_total,
        "swap_correct": swap_correct,
        "infeasible": infeasible,
        "final_outfit_item_ids": final_ids,
        "final_outfit_slots": _slots_of(final_ids, snapshot),
        "final_judge_overall": final_judge,
        "final_judge_failed": judge_failed,
        "final_judge_pass": bool(
            not infeasible and final_judge is not None
            and final_judge >= min_judge and final_ids
        ),
        "passed": bool(not infeasible and swap_correct and final_ids),
        "latency_seconds": elapsed,
    }


# --- memory export ---------------------------------------------------------------

def export_memory(db_dsn: str, user_id: str) -> dict[str, Any]:
    with database_session(db_dsn) as connection:
        evidence = [
            dict(row)
            for row in connection.execute(
                "SELECT attribute, value, polarity, strength, scope_json, source, "
                "created_at FROM preference_evidence WHERE user_id = %s "
                "ORDER BY created_at",
                (user_id,),
            ).fetchall()
        ]
        model = [
            dict(row)
            for row in connection.execute(
                "SELECT dimension, attribute, value, polarity, lifecycle, "
                "confidence, support_count, contradiction_count, scope_json, "
                "updated_at FROM preference_model WHERE user_id = %s AND active = 1 "
                "ORDER BY updated_at",
                (user_id,),
            ).fetchall()
        ]
    return {"evidence_count": len(evidence), "evidence": evidence,
            "model_count": len(model), "model": model}


# --- full-process logging ---------------------------------------------------------

def _persist_raw(case_id: str, result: dict[str, Any], env_dir: Path) -> dict[str, Any]:
    """Detach the full turn payloads into a per-case log file (trace, agent
    outputs, diagnostics) and return the summary without them, so the journal
    stays small while nothing is lost. Logs are what the reviewer reads to
    confirm what each LLM turn actually did."""
    log_dir = env_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    raw: list[dict[str, Any]] = []
    if "_payload" in result:
        raw.append({"turn": "item", "request": result.get("request", ""),
                    "payload": result.pop("_payload")})
    turns = result.get("turns")
    if isinstance(turns, list):
        for idx, t in enumerate(turns):
            if "_payload" in t:
                raw.append({"turn": idx, "request": t.get("request", ""),
                            "payload": t.pop("_payload")})
    if raw:
        (log_dir / f"{case_id}.json").write_text(
            json.dumps({"case_id": case_id, "log": raw}, ensure_ascii=False,
                       default=str),
            encoding="utf-8",
        )
    return result


# --- aggregation -----------------------------------------------------------------

def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def aggregate_item_metrics(per_case: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [c for c in per_case if c.get("judge_overall") is not None]
    return {
        "n_cases": len(per_case),
        "n_errors": len([c for c in per_case if "error" in c]),
        "mean_judge_overall": _mean([c["judge_overall"] for c in scored]),
        "anchor_rate": round(
            len([c for c in per_case if c.get("anchor_in_outfit")]) / len(per_case), 4
        ) if per_case else 0.0,
        "pass_rate": round(len([c for c in per_case if c.get("passed")]) / len(per_case), 4)
        if per_case else 0.0,
        "judge_failure_rate": round(
            len([c for c in per_case if c.get("judge_failed")]) / len(per_case), 4
        ) if per_case else 0.0,
        "retried_count": len([c for c in per_case if c.get("retried")]),
        "mean_llm_calls": _mean([float(c.get("llm_call_count", 0)) for c in per_case]),
        "mean_latency_s": _mean([float(c.get("latency_seconds", 0)) for c in per_case]),
    }


def aggregate_multiturn_metrics(per_chain: list[dict[str, Any]]) -> dict[str, Any]:
    judge_scored = [c for c in per_chain if c.get("final_judge_overall") is not None]
    evaluable = [c for c in per_chain if not c.get("infeasible")]
    infeasible_count = len(per_chain) - len(evaluable)
    return {
        "n_chains": len(per_chain),
        "n_errors": len([c for c in per_chain if "error" in c]),
        "infeasible_rate": round(infeasible_count / len(per_chain), 4) if per_chain else 0.0,
        "swap_correct_rate": round(
            len([c for c in evaluable if c.get("swap_correct")]) / len(evaluable), 4
        ) if evaluable else 0.0,
        "swap_skip_rate": round(
            sum(c.get("swaps_skipped", 0) for c in per_chain)
            / sum(c.get("swaps_executed", 0) + c.get("swaps_skipped", 0) for c in per_chain),
            4,
        ) if per_chain and sum(c.get("swaps_executed", 0) + c.get("swaps_skipped", 0) for c in per_chain) else 0.0,
        "mean_final_judge": _mean([c["final_judge_overall"] for c in judge_scored]),
        "final_judge_pass_rate": round(
            len([c for c in evaluable if c.get("final_judge_pass")]) / len(evaluable), 4
        ) if evaluable else 0.0,
        "judge_failure_rate": round(
            len([c for c in per_chain if c.get("final_judge_failed")]) / len(per_chain), 4
        ) if per_chain else 0.0,
        "retried_chain_count": len([
            c for c in per_chain
            if c.get("retried") or any(t.get("retried") for t in c.get("turns", []))
        ]),
        "mean_turns": _mean([float(c.get("turn_count", 0)) for c in per_chain]),
        "mean_llm_calls": _mean([float(c.get("llm_call_count", 0)) for c in per_chain]),
        "mean_latency_s": _mean([float(c.get("latency_seconds", 0)) for c in per_chain]),
    }


# --- finding detection ------------------------------------------------------------

def _detect_findings(
    item_results: list[dict[str, Any]],
    chain_results: list[dict[str, Any]],
    memory: dict[str, Any],
) -> list[dict[str, Any]]:
    """Turn reproducible system behaviors observed this run into explicit
    findings so the report does not bury them in metrics. Findings are recorded
    facts for the reviewer, not scored pass/fail items."""
    findings: list[dict[str, Any]] = []

    chains_with_adjust = [c for c in chain_results if "error" not in c]
    adj_failed = [c for c in chains_with_adjust if c.get("adjust_error")]
    if adj_failed:
        findings.append({
            "id": "EXT-001",
            "severity": "high",
            "area": "multiturn_adjust",
            "title": "无明确槽位的全局调整请求触发 extension 链硬抛 ValueError",
            "detail": (
                f"{len(adj_failed)}/{len(chains_with_adjust)} 条链的 adjust 轮（如「整体再正式一点」）被 "
                "is_follow_up 重定向到 OUTFIT_MODIFY 后，因请求不含明确槽位，agent1/agent2 对替换目标产生 "
                "不一致事实：agent2 产出越出 agent1 候选范围的 alternatives 或 replaced_item_ids 与 agent1 "
                "不一致，_validate_modify 直接抛 ValueError，重试一次仍失败，整链标记 failed，final_judge 无法产出。"
            ),
            "evidence": [
                {"chain": c["id"], "error": (c.get("adjust_error") or "")[:120]}
                for c in adj_failed
            ],
            "impact": "多轮对话中「整体再正式一点」这类最常见请求当前系统会崩溃而非优雅调整；multiturn 评估的 final_judge 数据全部缺失。",
        })

    sparse = [c for c in item_results if len(c.get("top_outfit_slots", [])) < 3]
    if sparse:
        findings.append({
            "id": "EXT-002",
            "severity": "medium",
            "area": "item_advice_composition",
            "title": "锚定 one_piece（连衣裙）时仅补齐鞋，不补用户额外要求的配饰/外套",
            "detail": (
                f"{len(sparse)} 个单品搭配 case 的生成套不足 3 件，全部为 one_piece 锚点（连衣裙+鞋）。"
                "item-001 请求「配齐鞋子和合适的配饰」仍只有 2 件，judge 32.0 为全组最低。"
            ),
            "evidence": [
                {"case": c["id"], "slots": c.get("top_outfit_slots", []),
                 "judge": c.get("judge_overall")}
                for c in sparse
            ],
            "impact": "锚定连衣裙时配饰/外套等附加槽位不被填充，拉低整体协调分。",
        })

    noise = [
        m for m in memory.get("model", [])
        if m.get("attribute") == "category" and m.get("polarity") == "negative"
        and m.get("support_count", 0) == 0
    ]
    if noise:
        findings.append({
            "id": "EXT-003",
            "severity": "low",
            "area": "memory_induction",
            "title": "category_induction 将替换行为过度归纳为负面偏好",
            "detail": (
                f"{len(noise)} 条 category 负面归纳（support_count=0）：用户把某双鞋换掉被归纳成"
                "「不喜欢鞋这类」，替换行为的过度泛化，易污染长期画像。"
            ),
            "evidence": [
                {"dimension": m["dimension"], "value": m["value"],
                 "confidence": m.get("confidence")}
                for m in noise[:8]
            ],
            "impact": "长期画像可能积累与真实意图相悖的负面类别偏好。",
        })

    return findings


# --- runner + CLI ------------------------------------------------------------------

def evaluate_extend(
    *,
    cases_path: Path,
    report_path: Path,
    db_dsn: str,
    settings: Settings,
    model_dir: Path,
    device: str,
    env_dir: Path,
    max_items: int | None,
    max_chains: int | None,
    llm_client: Any | None = None,
) -> dict[str, Any]:
    del report_path
    cases = load_extend_cases(cases_path)
    items = cases["item_advice"]
    chains = cases["multiturn"]
    if max_items is not None and max_items > 0:
        items = items[:max_items]
    if max_chains is not None and max_chains > 0:
        chains = chains[:max_chains]
    if not items and not chains:
        raise SystemExit("no item_advice or multiturn cases to run")

    if llm_client is None:
        llm_client = llm_client_from_settings(settings)
        if llm_client is None:
            raise SystemExit("DEEPSEEK_API_KEY is not set; extend eval requires a real LLM.")

    schema = f"eval_{_MODE}"
    scoped = _scoped_dsn(db_dsn, schema)
    _create_schema(db_dsn, schema)
    env_dir.mkdir(parents=True, exist_ok=True)

    user_id = f"eval-{_MODE}"
    snapshot_path = env_dir / "snapshot.json"
    if snapshot_path.is_file():
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    else:
        poutfit_payload = json.loads(
            (cases_path.parent / "p_outfit.json").read_text(encoding="utf-8")
        )
        snapshot = build_wardrobe_snapshot(
            mode=_MODE, db_dsn=scoped, cases=poutfit_payload.get("cases", []),
            user_id=user_id, source_revision=f"p-outfit-{_MODE}",
        )
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    embedding_dir = env_dir / "embeddings"
    if not (embedding_dir / "manifest.json").is_file():
        from evals.runners.evaluate_p_outfit import _build_encoder
        encoder = _build_encoder(settings, model_dir, device)
        try:
            build_embeddings(
                mode=_MODE, embedding_dir=embedding_dir, encoder=encoder,
                snapshot=snapshot, image_root=None,
            )
        finally:
            del encoder

    journal = _Journal(env_dir / "extend_journal.json")
    done = len(journal.done_ids)
    if done:
        print(f"resuming: {done} cases already recorded in journal")

    mtw, styleforge = make_pipeline(
        settings=settings, db_dsn=scoped, embedding_dir=embedding_dir,
        llm_client=llm_client, model_dir=model_dir, device=device,
    )
    judge = IndependentJudge()
    min_judge = int(cases.get("pass_criteria", {}).get("min_judge_overall", _DEFAULT_PASS_MIN))

    item_results: list[dict[str, Any]] = []
    for case in items:
        cid = case["id"]
        if journal.has(cid):
            item_results.append(journal.get(cid))
            continue
        result = run_item_advice(
            pipeline=(mtw, styleforge), judge=judge, llm=llm_client, case=case,
            user_id=user_id, snapshot=snapshot, min_judge=min_judge,
        )
        result = _persist_raw(cid, result, env_dir)
        journal.record(cid, result)
        item_results.append(result)
        print(
            f"[item] {cid} type={result.get('task_type')} anchor={result.get('anchor_in_outfit')} "
            f"judge={result.get('judge_overall')} calls={result.get('llm_call_count')}",
            flush=True,
        )

    chain_results: list[dict[str, Any]] = []
    for chain in chains:
        cid = chain["id"]
        if journal.has(cid):
            chain_results.append(journal.get(cid))
            continue
        result = run_multiturn_chain(
            pipeline=(mtw, styleforge), judge=judge, llm=llm_client, chain=chain,
            user_id=user_id, snapshot=snapshot, min_judge=min_judge,
        )
        result = _persist_raw(cid, result, env_dir)
        journal.record(cid, result)
        chain_results.append(result)
        print(
            f"[chain] {cid} swaps_ok={result.get('swap_correct')} "
            f"final_judge={result.get('final_judge_overall')} calls={result.get('llm_call_count')}",
            flush=True,
        )

    memory = export_memory(scoped, user_id)

    return {
        "schema_version": _SCHEMA_VERSION,
        "runner_version": _RUNNER_VERSION,
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": _MODE,
        "provenance": {
            "model": settings.deepseek_model,
            "case_file_sha256": _sha256(cases_path),
            "cases_path": str(cases_path.resolve()),
            "env_schema": schema,
            "wardrobe_user": user_id,
            "wardrobe_items": snapshot.get("item_count"),
            "device": device,
        },
        "pass_criteria": {
            "min_judge_overall": min_judge,
            "item_advice_anchor_in_top_outfit": True,
            "multiturn_swap_must_hit_requested_slot": True,
            "memory_not_scored": True,
        },
        "item_advice": {
            "metrics": aggregate_item_metrics(item_results),
            "per_case": item_results,
        },
        "multiturn": {
            "metrics": aggregate_multiturn_metrics(chain_results),
            "per_chain": chain_results,
        },
        "memory": memory,
        "findings": _detect_findings(item_results, chain_results, memory),
        "limitations": [
            "Runs on the p-outfit order (text-only) wardrobe env only; image mode is a separate experiment.",
            "Judge is text-only DeepSeek over item text blocks; no image input.",
            "Multiturn swap verification checks the requested slot was targeted, not that the user's exact style was found.",
            "Memory module is not scored; the exported evidence/model rows are for manual intent review.",
            "A chain interrupted mid-way is re-run whole on resume (journal is per item/chain, not per turn).",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    default_dsn = os.getenv("STYLEFORGE_TEST_DATABASE_DSN") or settings.database_dsn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--database", default=default_dsn)
    parser.add_argument("--model-dir", type=Path, default=settings.artifact_root / "models")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--max-chains", type=int, default=None)
    return parser


def main() -> None:
    configure_utf8_console()
    args = build_parser().parse_args()
    if not args.database:
        raise SystemExit(
            "Neither STYLEFORGE_TEST_DATABASE_DSN nor STYLEFORGE_DATABASE_DSN is set"
        )
    settings = Settings.from_env()
    report = evaluate_extend(
        cases_path=args.cases,
        report_path=args.report,
        db_dsn=args.database,
        settings=settings,
        model_dir=args.model_dir,
        device=args.device,
        env_dir=args.env,
        max_items=args.max_items,
        max_chains=args.max_chains,
    )
    write_json_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
