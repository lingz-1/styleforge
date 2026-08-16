"""Build the self-contained p-outfit eval case set (development-time one-shot).

Reads the official Polyvore Outfits files under ``E:\\01-style-dataset\\p-outfit``
and writes ``evals/cases/p_outfit.json``: 100 complete outfits whose items are
embedded inline (title / description / url_name) so the eval runner and CI never
need access to E:\\. ``user_request`` is machine-drafted (see
``data.p_outfit.draft_user_request``) and must be human-reviewed/refined before
the case set is frozen.

The case-file contract (also asserted by ``tests/test_p_outfit_eval.py``):
- every case has an id, a unique ``source_set_id``, a non-empty ``user_request``
  that is a fresh brief (no follow-up markers), ``golden_item_ids`` subset of
  ``items``, and complete outfits (top+bottom+footwear or one_piece+footwear).
- ``user_request`` construction rule: reverse-drafted from the English outfit
  title + item descriptions into a Chinese scene+category+mood brief, then
  human-refined so it names only categories the golden outfit actually carries.

Run with the style env. Usage:
  D:\\anaconda\\envs\\style\\python.exe -m styleforge.pipelines.build_p_outfit_cases
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from styleforge.common.console import configure_utf8_console
from styleforge.common.files import write_json_atomic
from styleforge.data.p_outfit import (
    load_item_metadata,
    load_outfit_titles,
    select_eval_cases,
)

DEFAULT_EVAL_ROOT = Path(r"E:\01-style-dataset\p-outfit")
DEFAULT_OUTPUT = Path(__file__).resolve().parents[4] / "evals" / "cases" / "p_outfit.json"

_CASE_FILE_NOTES = [
    "Self-contained p-outfit eval cases (Polyvore Outfits official train split).",
    "user_request rule: fresh-brief Chinese brief reverse-drafted from the English "
    "title + item text, then human-refined to name only categories the golden "
    "outfit carries. No follow-up markers (更/再/别/不/一点/太/有点/调整/改变).",
    "golden_item_ids are the raw p-outfit item ids; the eval runner UUID-maps them "
    "when building the isolated wardrobe snapshot.",
    "hard_assertions: all_items_in_wardrobe (every golden item is in the snapshot), "
    "has_required_slots (top+bottom+footwear or one_piece+footwear).",
    "pass_criteria: min_judge_overall=60 gates the generated outfit's judge score.",
]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--train",
        type=Path,
        default=DEFAULT_EVAL_ROOT / "nondisjoint" / "train.json",
        help="Official nondisjoint/train.json (list of {set_id, items}).",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=DEFAULT_EVAL_ROOT / "polyvore_item_metadata.json",
        help="Official item metadata JSON.",
    )
    parser.add_argument(
        "--titles",
        type=Path,
        default=DEFAULT_EVAL_ROOT / "polyvore_outfit_titles.json",
        help="Official outfit-level title JSON.",
    )
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260814)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--preview",
        type=int,
        default=0,
        help="Print N drafted cases instead of writing the file.",
    )
    return parser


def _validate_payload(payload: dict[str, Any]) -> None:
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("built payload must contain a non-empty 'cases' list")
    seen_set_ids: set[str] = set()
    for case in cases:
        set_id = str(case.get("source_set_id", "")).strip()
        if not set_id or set_id in seen_set_ids:
            raise ValueError(f"empty or duplicate source_set_id: {set_id!r}")
        seen_set_ids.add(set_id)
        request = str(case.get("user_request", "")).strip()
        if not request:
            raise ValueError(f"case {set_id} has an empty user_request")
        golden = case.get("golden_item_ids", [])
        item_ids = {item.get("item_id") for item in case.get("items", [])}
        if not golden or not set(golden) <= item_ids:
            raise ValueError(f"case {set_id} golden_item_ids must be a non-empty subset of items")


def main() -> None:
    configure_utf8_console()
    args = _build_parser().parse_args()
    if not args.train.is_file() or not args.metadata.is_file() or not args.titles.is_file():
        print(
            "Official p-outfit files were not found. This pipeline reads E:\\ once; "
            "CI and the eval runner consume the self-contained case file instead.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    metadata = load_item_metadata(args.metadata)
    titles = load_outfit_titles(args.titles)
    cases = select_eval_cases(
        args.train,
        metadata,
        titles,
        count=args.count,
        seed=args.seed,
    )
    payload = {
        "schema_version": "styleforge.p-outfit-cases.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(DEFAULT_EVAL_ROOT),
        "notes": _CASE_FILE_NOTES,
        "case_count": len(cases),
        "cases": cases,
    }
    _validate_payload(payload)

    if args.preview:
        for case in cases[: args.preview]:
            print(
                json.dumps(
                    {
                        "id": case["id"],
                        "source_title": case["source_title"],
                        "user_request": case["user_request"],
                        "item_types": sorted(
                            {item["item_type"] for item in case["items"]}
                        ),
                    },
                    ensure_ascii=False,
                )
            )
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(args.output, payload)
    # Re-run draft_user_request on every case to prove reproducibility: the
    # drafted request must equal the stored one (fresh sample => fresh draft).
    replayed = select_eval_cases(
        args.train,
        metadata,
        titles,
        count=args.count,
        seed=args.seed,
    )
    mismatched = [
        (a["id"], b["user_request"])
        for a, b in zip(cases, replayed, strict=True)
        if a["user_request"] != b["user_request"]
    ]
    print(
        json.dumps(
            {
                "wrote": str(args.output),
                "case_count": len(cases),
                "mismatched_drafts": len(mismatched),
                "sample": [
                    {
                        "id": case["id"],
                        "source_title": case["source_title"],
                        "user_request": case["user_request"],
                    }
                    for case in cases[:3]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
