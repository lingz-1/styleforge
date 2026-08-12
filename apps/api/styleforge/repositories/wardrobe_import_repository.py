"""Persistence for auditable shopping-order wardrobe imports."""

from __future__ import annotations

import hashlib
import json
from styleforge.repositories.database import Connection, Row
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from styleforge.core.categories import TYPE_TO_SLOT, infer_slot
from styleforge.core.schemas import CatalogItem, EmbeddingStatus, ImageStatus
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.dataset_source_repository import register_dataset_source
from styleforge.services.order_import import PARSER_REVISION, ParsedOrderWorkbook


AUDIENCES = {"", "women", "men", "girls", "boys", "baby", "life"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def personal_source_for_user(user_id: str) -> str:
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
    return f"personal-{digest}"


def personal_image_root(artifact_root: Path, user_id: str) -> Path:
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
    return (artifact_root / "personal_images" / digest).resolve()


def _insert_preview_rows(
    connection: Connection,
    *,
    batch_id: str,
    workbook: ParsedOrderWorkbook,
) -> None:
    connection.executemany(
        """
        INSERT INTO wardrobe_import_rows(
            row_id, batch_id, source_row_number, row_hash,
            external_order_id_hash, order_submitted_at, order_status, shop_name,
            refund_status, after_sale_status, logistics_status,
            order_eligibility, order_eligibility_reason, external_product_id,
            product_name, canonical_url, variant_text, quantity, listed_amount,
            paid_amount, currency, predicted_item_type, predicted_subtype,
            predicted_color, predicted_size, predicted_audience, confidence,
            decision, decision_reason
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        """,
        (
            (
                str(uuid.uuid4()),
                batch_id,
                row.source_row_number,
                row.row_hash,
                row.external_order_id_hash,
                row.order_submitted_at,
                row.order_status,
                row.shop_name,
                row.refund_status,
                row.after_sale_status,
                row.logistics_status,
                row.order_eligibility,
                row.order_eligibility_reason,
                row.external_product_id,
                row.product_name,
                row.canonical_url,
                row.variant_text,
                row.quantity,
                row.listed_amount,
                row.paid_amount,
                row.currency,
                row.predicted_item_type,
                row.predicted_subtype,
                row.predicted_color,
                row.predicted_size,
                row.predicted_audience,
                row.confidence,
                row.decision,
                row.decision_reason,
            )
            for row in workbook.rows
        ),
    )


def create_import_preview(
    connection: Connection,
    *,
    user_id: str,
    source_filename: str,
    workbook: ParsedOrderWorkbook,
) -> tuple[str, bool, bool]:
    """Persist privacy-safe parsed rows and make same-file uploads idempotent."""
    existing = connection.execute(
        "SELECT batch_id, status, parser_revision FROM wardrobe_import_batches "
        "WHERE user_id = %s AND platform = %s AND file_sha256 = %s",
        (user_id, workbook.platform, workbook.file_sha256),
    ).fetchone()
    if existing is not None:
        batch_id = str(existing["batch_id"])
        if existing["status"] == "previewed" and existing["parser_revision"] != PARSER_REVISION:
            statistics = {**workbook.statistics(), "sheet_name": workbook.sheet_name}
            connection.execute(
                "DELETE FROM wardrobe_import_rows WHERE batch_id = %s",
                (batch_id,),
            )
            connection.execute(
                """
                UPDATE wardrobe_import_batches
                SET source_filename = %s, parser_revision = %s, statistics_json = %s,
                    error_message = NULL
                WHERE batch_id = %s
                """,
                (
                    Path(source_filename).name,
                    PARSER_REVISION,
                    json.dumps(statistics, ensure_ascii=False, sort_keys=True),
                    batch_id,
                ),
            )
            _insert_preview_rows(connection, batch_id=batch_id, workbook=workbook)
            return batch_id, False, True
        return batch_id, False, False

    batch_id = str(uuid.uuid4())
    statistics = {**workbook.statistics(), "sheet_name": workbook.sheet_name}
    connection.execute(
        """
        INSERT INTO wardrobe_import_batches(
            batch_id, user_id, platform, source_filename, file_sha256,
            parser_revision, status, statistics_json, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, 'previewed', %s, %s)
        """,
        (
            batch_id,
            user_id,
            workbook.platform,
            Path(source_filename).name,
            workbook.file_sha256,
            PARSER_REVISION,
            json.dumps(statistics, ensure_ascii=False, sort_keys=True),
            _now(),
        ),
    )
    _insert_preview_rows(connection, batch_id=batch_id, workbook=workbook)
    return batch_id, True, False


def get_import_batch(
    connection: Connection,
    *,
    user_id: str,
    batch_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM wardrobe_import_batches WHERE batch_id = %s AND user_id = %s",
        (batch_id, user_id),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["statistics"] = json.loads(result.pop("statistics_json"))
    return result


def list_import_rows(
    connection: Connection,
    *,
    user_id: str,
    batch_id: str,
    offset: int = 0,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    if offset < 0 or limit < 1:
        raise ValueError("Invalid import row pagination")
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT r.* FROM wardrobe_import_rows AS r
            JOIN wardrobe_import_batches AS b ON b.batch_id = r.batch_id
            WHERE r.batch_id = %s AND b.user_id = %s
            ORDER BY r.source_row_number
            LIMIT %s OFFSET %s
            """,
            (batch_id, user_id, limit, offset),
        )
    ]


def _selected_attributes(row: Row, override: dict[str, Any]) -> dict[str, str]:
    item_type = str(override.get("item_type") or row["predicted_item_type"]).strip().lower()
    if item_type not in TYPE_TO_SLOT:
        raise ValueError(f"Unsupported item type for row {row['row_id']}: {item_type!r}")
    audience = str(override.get("audience") or row["predicted_audience"]).strip().lower()
    if audience not in AUDIENCES or not audience:
        raise ValueError(f"Audience must be confirmed for row {row['row_id']}")
    return {
        "item_type": item_type,
        "subtype": str(override.get("subtype") or row["predicted_subtype"]).strip(),
        "color": str(override.get("color") or row["predicted_color"]).strip(),
        "size": str(override.get("size") or row["predicted_size"]).strip(),
        "audience": audience,
    }


def commit_import_rows(
    connection: Connection,
    *,
    user_id: str,
    batch_id: str,
    selections: Sequence[dict[str, Any]],
    image_root: Path,
) -> list[str]:
    """Create confirmed personal catalog records from explicit row selections."""
    batch = connection.execute(
        "SELECT * FROM wardrobe_import_batches WHERE batch_id = %s AND user_id = %s",
        (batch_id, user_id),
    ).fetchone()
    if batch is None:
        raise ValueError("Wardrobe import batch not found")
    if not selections:
        raise ValueError("At least one import row must be selected")

    row_ids = tuple(dict.fromkeys(str(item["row_id"]) for item in selections))
    override_by_row = {str(item["row_id"]): dict(item) for item in selections}
    placeholders = ",".join("%s" for _ in row_ids)
    rows = connection.execute(
        f"SELECT * FROM wardrobe_import_rows "  # noqa: S608
        f"WHERE batch_id = %s AND row_id IN ({placeholders})",
        (batch_id, *row_ids),
    ).fetchall()
    if len(rows) != len(row_ids):
        raise ValueError("One or more selected rows do not belong to this import batch")

    ineligible_rows = [
        row for row in rows if row["order_eligibility"] != "eligible"
    ]
    if ineligible_rows:
        details = ", ".join(
            f"row {row['source_row_number']} ({row['order_status'] or 'unknown'})"
            for row in ineligible_rows[:5]
        )
        raise ValueError(
            "Only received orders without refund, return, or after-sale signals "
            f"can be committed: {details}"
        )

    source = personal_source_for_user(user_id)
    register_dataset_source(
        connection,
        source=source,
        image_root=image_root,
        source_revision="personal-wardrobe-v2",
    )
    created_at = _now()
    catalog_items = []
    item_ids = []
    for row in rows:
        attributes = _selected_attributes(row, override_by_row[row["row_id"]])
        existing_item_id = row["catalog_item_id"]
        item_id = str(existing_item_id or f"personal:{row['row_id']}")
        existing_catalog = connection.execute(
            "SELECT image_filename, relative_image_path, image_status, embedding_status "
            "FROM catalog_items WHERE item_id = %s",
            (item_id,),
        ).fetchone()
        features = tuple(
            value
            for value in (
                attributes["subtype"],
                f"size:{attributes['size']}" if attributes["size"] else "",
                row["variant_text"],
            )
            if value
        )
        selected_hash = hashlib.sha256(
            json.dumps(
                {
                    "row_hash": row["row_hash"],
                    "order_status": row["order_status"],
                    "order_eligibility": row["order_eligibility"],
                    **attributes,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        catalog_items.append(
            CatalogItem(
                item_id=item_id,
                source=source,
                gender=attributes["audience"],
                item_type=attributes["item_type"],
                main_category=infer_slot(attributes["item_type"]),
                name=row["product_name"],
                color=attributes["color"],
                description=row["variant_text"],
                features=features,
                image_filename=(
                    existing_catalog["image_filename"] if existing_catalog else ""
                ),
                relative_image_path=(
                    existing_catalog["relative_image_path"] if existing_catalog else ""
                ),
                image_status=(
                    ImageStatus(existing_catalog["image_status"])
                    if existing_catalog
                    else ImageStatus.UNBOUND
                ),
                embedding_status=(
                    EmbeddingStatus(existing_catalog["embedding_status"])
                    if existing_catalog
                    else EmbeddingStatus.PENDING
                ),
                raw_json_hash=selected_hash,
            )
        )
        item_ids.append(item_id)

    upsert_items(connection, catalog_items, "personal-wardrobe-v2")
    for row, item_id in zip(rows, item_ids, strict=True):
        connection.execute(
            """
            INSERT INTO wardrobe_items(user_id, item_id, active, favorite, notes, added_at)
            VALUES (%s, %s, 1, 0, '', %s)
            ON CONFLICT(user_id, item_id) DO UPDATE SET active = 1
            """,
            (user_id, item_id, created_at),
        )
        connection.execute(
            """
            INSERT INTO personal_wardrobe_items(
                item_id, user_id, import_row_id, external_order_id_hash,
                order_submitted_at, order_status, shop_name, external_product_id,
                variant_text, quantity_owned, listed_amount, paid_amount, currency,
                canonical_url, ownership_status, review_status, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                'owned', 'confirmed', %s, %s
            )
            ON CONFLICT(item_id) DO UPDATE SET
                quantity_owned = excluded.quantity_owned,
                order_status = excluded.order_status,
                shop_name = excluded.shop_name,
                ownership_status = 'owned',
                review_status = 'confirmed',
                updated_at = excluded.updated_at
            """,
            (
                item_id,
                user_id,
                row["row_id"],
                row["external_order_id_hash"],
                row["order_submitted_at"],
                row["order_status"],
                row["shop_name"],
                row["external_product_id"],
                row["variant_text"],
                row["quantity"],
                row["listed_amount"],
                row["paid_amount"],
                row["currency"],
                row["canonical_url"],
                created_at,
                created_at,
            ),
        )
        connection.execute(
            "UPDATE wardrobe_import_rows SET decision = 'committed', catalog_item_id = %s "
            "WHERE row_id = %s",
            (item_id, row["row_id"]),
        )

    connection.execute(
        f"UPDATE wardrobe_import_rows SET decision = 'rejected' "  # noqa: S608
        f"WHERE batch_id = %s AND decision = 'candidate' "
        f"AND row_id NOT IN ({placeholders})",
        (batch_id, *row_ids),
    )
    connection.execute(
        "UPDATE wardrobe_import_batches SET status = 'committed', committed_at = %s, "
        "error_message = NULL WHERE batch_id = %s",
        (created_at, batch_id),
    )
    return item_ids
