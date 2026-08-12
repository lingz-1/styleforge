"""User-facing serialization that resolves stable item IDs to readable details."""

from __future__ import annotations

from typing import Any

from styleforge.core.schemas import RecommendationResult
from styleforge.repositories.database import database_session


def present_result(database_path: str, result: RecommendationResult) -> dict[str, Any]:
    payload = result.to_dict()
    item_ids = {
        item_id
        for recommendation in payload["recommendations"]
        for item_id in recommendation["item_ids"]
    }
    if not item_ids:
        return payload
    placeholders = ",".join("%s" for _ in item_ids)
    with database_session(database_path) as connection:
        summaries = {
            row["item_id"]: {
                "item_id": row["item_id"],
                "name": row["name"],
                "type": row["item_type"],
                "color": row["color"],
                "relative_image_path": row["relative_image_path"],
                "image_status": row["image_status"],
            }
            for row in connection.execute(
                f"""
                SELECT item_id, name, item_type, color, relative_image_path, image_status
                FROM catalog_items WHERE item_id IN ({placeholders})
                """,  # noqa: S608
                tuple(item_ids),
            )
        }
    slot_labels = {
        "top": "上装",
        "bottom": "下装",
        "one_piece": "连体装",
        "footwear": "鞋",
        "outerwear": "外套",
        "bag": "包",
        "accessory": "配饰",
    }
    for index, recommendation in enumerate(payload["recommendations"], start=1):
        recommendation["items"] = {
            slot: summaries.get(item_id, {"item_id": item_id, "missing": True})
            for slot, item_id in recommendation["slot_items"].items()
        }
        item_phrases = [
            f"{slot_labels.get(slot, slot)}选 {item.get('name', item['item_id'])}"
            + (f"（{item['color']}）" if item.get("color") else "")
            for slot, item in recommendation["items"].items()
        ]
        recommendation["summary"] = (
            f"方案 {index}：" + "，".join(item_phrases) + "。" + "；".join(recommendation["reasons"]) + "。"
        )
    payload["advice"] = [
        recommendation["summary"] for recommendation in payload["recommendations"]
    ]
    return payload
