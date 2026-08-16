"""Build frontend data for the p-outfit eval showcase.

Merges the run report (``artifacts/evaluation/p_outfit.json``) with the frozen
case file (``evals/cases/p_outfit.json``) into a single ``data.json`` the
standalone frontend renders. Also tags each item with whether its photo exists
under the official benchmark image root, so the page can show a placeholder
instead of a broken image.
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPORT = ROOT / "artifacts" / "evaluation" / "p_outfit.json"
CASES = ROOT / "evals" / "cases" / "p_outfit.json"
CASES_DIR = ROOT / "evals" / "cases"
OUT = pathlib.Path(__file__).resolve().parent / "data.json"

# Input-only evaluation sets (no generated outfit / no photos): they carry the
# user input plus an expected label used for routing / extraction classification.
_FOLLOW_UP_EXPECTED_ZH = {"follow_up": "多轮调整", "fresh": "新推荐"}
_TASK_TYPE_ZH = {
    "outfit_recommend": "推荐搭配", "outfit_modify": "修改搭配", "style_advice": "风格建议",
    "item_advice": "单品搭配", "wardrobe_compatibility": "衣橱兼容", "wardrobe_gap": "衣橱缺口",
}
_DIMENSION_ZH = {"garment": "品类", "color": "颜色", "occasion": "场合", "style": "风格", "material": "材质"}
_POLARITY_ZH = {"positive": "偏好", "negative": "排斥"}

# Official Polyvore-Outfits image layout: images/nondisjoint/train/{item_id}.jpg
IMG_DIR = pathlib.Path(r"E:\01-style-dataset\p-outfit\images\nondisjoint\train")


def _parse_text_block(block: str) -> dict:
    """'品类 | 名称 | 描述' -> {category, name, description}."""
    parts = [part.strip() for part in block.split("|")] if block else []
    return {
        "category": parts[0] if parts else "",
        "name": parts[1] if len(parts) > 1 else "",
        "description": parts[2] if len(parts) > 2 else "",
    }


def build() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    case_file = json.loads(CASES.read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in case_file["cases"]}

    img_cache: dict[str, bool] = {}

    def has_img(raw: str) -> bool:
        if raw not in img_cache:
            img_cache[raw] = (IMG_DIR / f"{raw}.jpg").is_file()
        return img_cache[raw]

    per_mode = {
        mode: {r["id"]: r for r in report["modes"][mode]["per_case"]}
        for mode in ("order", "image")
    }

    out_cases = []
    for cid in sorted(by_id):
        case = by_id[cid]
        golden = []
        for item in case["items"]:
            raw = str(item["item_id"])
            name = item.get("title") or item.get("url_name") or ""
            golden.append(
                {
                    "raw": raw,
                    "category": item.get("semantic_category", ""),
                    "name": name,
                    "text": f"{item.get('item_type', '')} | {name}",
                    "has_img": has_img(raw),
                }
            )
        entry = {
            "id": cid,
            "source_set_id": case["source_set_id"],
            "source_title": case.get("source_title", ""),
            "user_request": case["user_request"],
            "golden": golden,
            "golden_item_ids": case["golden_item_ids"],
            "modes": {},
        }
        for mode in ("order", "image"):
            rec = per_mode[mode].get(cid)
            if rec is None:
                continue
            generated = []
            raws = rec.get("generated_raw") or []
            texts = rec.get("generated_item_texts") or []
            for raw, text in zip(raws, texts):
                generated.append({"raw": raw, ** _parse_text_block(text), "has_img": has_img(raw)})
            entry["modes"][mode] = {
                "judge_overall": rec.get("judge_overall"),
                "judge_overall_golden": rec.get("judge_overall_golden"),
                "passed": rec.get("passed"),
                "decision": rec.get("decision"),
                "critic_score": rec.get("critic_score"),
                "gap": rec.get("gap"),
                "violations": rec.get("violations") or [],
                "judge_dimensions": rec.get("judge_dimensions"),
                "generated": generated,
                "llm_call_count": rec.get("llm_call_count"),
                "latency_seconds": rec.get("latency_seconds"),
            }
        out_cases.append(entry)

    input_sets = build_input_sets()

    payload = {
        "meta": {
            "generated_at": report.get("generated_at"),
            "judge_prompt_version": report.get("judge_prompt_version"),
            "schema_version": report.get("schema_version"),
            "case_count": len(out_cases),
            "modes_metrics": {mode: report["modes"][mode]["metrics"] for mode in ("order", "image")},
            "comparison": report.get("comparison"),
        },
        "cases": out_cases,
        "input_sets": input_sets,
    }

    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"built {len(out_cases)} cases -> {OUT}")
    for mode in ("order", "image"):
        passed = sum(1 for c in out_cases if c["modes"].get(mode, {}).get("passed"))
        missing = sum(1 for c in out_cases for it in c["modes"].get(mode, {}).get("generated", []) if not it["has_img"])
        print(f"  {mode}: {passed}/{len(out_cases)} passed, {missing} generated items missing photo")


def _memory_evidence_summary(evidence: list) -> str:
    """Collapse expected_evidence into readable text: '品类=牛仔(偏好), 场合=正式(排斥)'."""
    parts = []
    for ev in evidence or []:
        dim = _DIMENSION_ZH.get(ev.get("dimension", ""), ev.get("dimension", ""))
        attr = ev.get("attribute", "")
        value = ev.get("value", "")
        pol = _POLARITY_ZH.get(ev.get("polarity", ""), ev.get("polarity", ""))
        parts.append(f"{dim}/{attr}={value}({pol})")
    return "；".join(parts) if parts else ""


def build_input_sets() -> list[dict]:
    """Read the input-only case files (multiturn / routing / memory extraction).

    These sets carry user input plus an expected label, but no generated
    outfit and no photos — they are classification / extraction evaluation
    sets. The frontend renders them as spec cards, distinct from the
    p-outfit generated-outfit cards.
    """
    sets = []

    fu_path = CASES_DIR / "follow_up.json"
    if fu_path.is_file():
        fu = json.loads(fu_path.read_text(encoding="utf-8"))
        sets.append({
            "type": "multiturn",
            "title": "多轮对话路由用例",
            "source": "follow_up.json",
            "count": len(fu),
            "items": [
                {
                    "id": row.get("id", ""),
                    "request": row.get("request", ""),
                    "expected": _FOLLOW_UP_EXPECTED_ZH.get(row.get("expected", ""), row.get("expected", "")),
                    "expected_raw": row.get("expected", ""),
                    "rule": row.get("rule", ""),
                    "known_gap": bool(row.get("known_gap")),
                }
                for row in fu
            ],
        })

    tr_path = CASES_DIR / "task_routing.json"
    if tr_path.is_file():
        tr = json.loads(tr_path.read_text(encoding="utf-8"))
        sets.append({
            "type": "routing",
            "title": "任务路由分类用例",
            "source": "task_routing.json",
            "count": len(tr),
            "items": [
                {
                    "id": row.get("id", ""),
                    "request": row.get("request", ""),
                    "expected": _TASK_TYPE_ZH.get(row.get("expected_task_type", ""), row.get("expected_task_type", "")),
                }
                for row in tr
            ],
        })

    mem_path = CASES_DIR / "memory_extraction.json"
    if mem_path.is_file():
        mem = json.loads(mem_path.read_text(encoding="utf-8"))
        sets.append({
            "type": "memory",
            "title": "记忆提炼评估用例",
            "source": "memory_extraction.json",
            "count": len(mem),
            "items": [
                {
                    "id": row.get("id", ""),
                    "request": row.get("request", ""),
                    "rule": row.get("rule", ""),
                    "expected": _memory_evidence_summary(row.get("expected_evidence")),
                }
                for row in mem
            ],
        })

    return sets


if __name__ == "__main__":
    build()
