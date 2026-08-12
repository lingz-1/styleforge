"""Preview or commit a shopping-order workbook into a personal wardrobe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from styleforge.common.files import write_json_atomic
from styleforge.core.config import Settings
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.wardrobe_import_repository import (
    commit_import_rows,
    create_import_preview,
    get_import_batch,
    list_import_rows,
    personal_image_root,
)
from styleforge.services.order_import import parse_order_workbook
from styleforge.services.personal_embeddings import embed_personal_items


def import_wardrobe_orders(
    *,
    excel_path: Path,
    database_path: str,
    artifact_root: Path,
    user_id: str,
    default_audience: str,
    commit_candidates: bool,
    auto_embed: bool,
    model_dir: Path,
    device: str,
) -> dict[str, Any]:
    if not user_id.strip():
        raise ValueError("user_id cannot be empty")
    if not excel_path.is_file():
        raise FileNotFoundError(f"Order workbook not found: {excel_path}")
    workbook = parse_order_workbook(excel_path, default_audience=default_audience)
    initialize_database(database_path)
    with database_session(database_path) as connection:
        batch_id, created, refreshed = create_import_preview(
            connection,
            user_id=user_id,
            source_filename=excel_path.name,
            workbook=workbook,
        )
        batch = get_import_batch(connection, user_id=user_id, batch_id=batch_id)
        rows = list_import_rows(
            connection,
            user_id=user_id,
            batch_id=batch_id,
            limit=100_000,
        )

    committed_item_ids: list[str] = []
    embedding_report: dict[str, Any] | None = None
    if commit_candidates:
        selections = [
            {"row_id": row["row_id"]}
            for row in rows
            if row["decision"] in {"candidate", "committed"}
        ]
        image_root = personal_image_root(artifact_root, user_id)
        image_root.mkdir(parents=True, exist_ok=True)
        with database_session(database_path) as connection:
            committed_item_ids = commit_import_rows(
                connection,
                user_id=user_id,
                batch_id=batch_id,
                selections=selections,
                image_root=image_root,
            )
        if auto_embed:
            try:
                embedding_report = embed_personal_items(
                    database_path=database_path,
                    item_ids=committed_item_ids,
                    model_dir=model_dir,
                    device=device,
                )
            except BaseException as error:
                embedding_report = {
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "items_remain_available_for_text_rule_fallback": True,
                }

    candidate_rows = [row for row in rows if row["decision"] == "candidate"]
    return {
        "schema_version": "styleforge.wardrobe-order-import.v1",
        "status": "committed" if commit_candidates else "previewed",
        "batch_id": batch_id,
        "created": created,
        "refreshed": refreshed,
        "user_id": user_id,
        "source_filename": excel_path.name,
        "file_sha256": workbook.file_sha256,
        "platform": workbook.platform,
        "statistics": batch["statistics"] if batch else workbook.statistics(),
        "committed_item_count": len(committed_item_ids),
        "embedding": embedding_report,
        "candidate_preview": [
            {
                "row_id": row["row_id"],
                "source_row_number": row["source_row_number"],
                "order_status": row["order_status"],
                "order_eligibility": row["order_eligibility"],
                "order_eligibility_reason": row["order_eligibility_reason"],
                "shop_name": row["shop_name"],
                "product_name": row["product_name"],
                "variant_text": row["variant_text"],
                "predicted_item_type": row["predicted_item_type"],
                "predicted_subtype": row["predicted_subtype"],
                "predicted_audience": row["predicted_audience"],
                "confidence": row["confidence"],
            }
            for row in candidate_rows[:20]
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--excel", type=Path, required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument(
        "--default-audience",
        choices=("", "women", "men", "girls", "boys", "baby", "life"),
        default="",
    )
    parser.add_argument("--database", type=str, default=settings.database_dsn)
    parser.add_argument("--artifact-root", type=Path, default=settings.artifact_root)
    parser.add_argument("--model-dir", type=Path, default=settings.artifact_root / "models")
    parser.add_argument("--device", default="cuda")
    parser.add_argument(
        "--commit-candidates",
        action="store_true",
        help="Explicitly confirm every predicted clothing candidate as currently owned.",
    )
    parser.add_argument("--no-auto-embed", action="store_false", dest="auto_embed")
    parser.set_defaults(auto_embed=True)
    parser.add_argument(
        "--report",
        type=Path,
        default=settings.artifact_root / "imports" / "wardrobe_order_import.json",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = import_wardrobe_orders(
        excel_path=args.excel,
        database_path=args.database,
        artifact_root=args.artifact_root,
        user_id=args.user_id,
        default_audience=args.default_audience,
        commit_candidates=args.commit_candidates,
        auto_embed=args.auto_embed,
        model_dir=args.model_dir,
        device=args.device,
    )
    write_json_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
