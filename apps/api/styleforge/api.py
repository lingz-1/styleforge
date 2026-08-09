"""FastAPI application for wardrobe management and multi-agent recommendations."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from styleforge.core.config import Settings
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.dataset_source_repository import (
    get_source_image_root,
    list_dataset_sources,
)
from styleforge.repositories.wardrobe_import_repository import (
    commit_import_rows,
    create_import_preview,
    get_import_batch,
    list_import_rows,
    personal_image_root,
)
from styleforge.repositories.user_preferences_repository import (
    get_evaluation_weights,
    save_evaluation_weights,
)
from styleforge.services.order_import import parse_order_workbook
from styleforge.services.personal_embeddings import embed_personal_items
from styleforge.services.personal_images import bind_personal_image
from styleforge.services.wardrobe_item_service import (
    create_photo_item,
    update_personal_item,
)
from styleforge.workflow.graph import StyleForgeWorkflow


class RecommendationRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    request: str = Field(min_length=1, max_length=2000)
    max_results: int = Field(default=3, ge=1, le=10)


class WardrobeItemRequest(BaseModel):
    item_id: str = Field(min_length=1, max_length=128)


class OrderWorkbookUpload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=30_000_000)
    default_audience: str = Field(default="", max_length=16)


class WardrobeImportSelection(BaseModel):
    row_id: str = Field(min_length=1, max_length=128)
    item_type: str | None = Field(default=None, max_length=64)
    subtype: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, max_length=64)
    size: str | None = Field(default=None, max_length=64)
    audience: str | None = Field(default=None, max_length=16)


class WardrobeImportCommitRequest(BaseModel):
    selections: list[WardrobeImportSelection] = Field(min_length=1, max_length=2000)
    auto_embed: bool = True


class PersonalImageUpload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=30_000_000)


class EvaluationWeightsRequest(BaseModel):
    weights: dict[str, float]


class PhotoItemRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=30_000_000)
    item_type: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=128)
    subtype: str = Field(default="", max_length=64)
    color: str = Field(default="", max_length=64)
    gender: str = Field(default="women", max_length=16)
    size: str = Field(default="", max_length=16)


class UpdateItemRequest(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    item_type: str | None = Field(default=None, max_length=64)
    subtype: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, max_length=64)
    gender: str | None = Field(default=None, max_length=16)
    size: str | None = Field(default=None, max_length=16)


settings = Settings.from_env()
initialize_database(settings.database_path)
app = FastAPI(
    title="StyleForge API",
    version="0.2.0",
    description="Local-first multi-agent personal wardrobe styling API.",
)


@lru_cache(maxsize=1)
def get_workflow() -> StyleForgeWorkflow:
    return StyleForgeWorkflow(
        database_path=settings.database_path,
        embedding_dir=settings.embedding_dir,
        model_dir=settings.artifact_root / "models",
        device="cuda",
    )


def _catalog_row_to_dict(row) -> dict[str, Any]:
    return {
        "item_id": row["item_id"],
        "source": row["source"],
        "gender": row["gender"],
        "item_type": row["item_type"],
        "main_category": row["main_category"],
        "name": row["name"],
        "color": row["color"],
        "description": row["description"],
        "relative_image_path": row["relative_image_path"],
        "image_status": row["image_status"],
        "embedding_status": row["embedding_status"],
        "image_url": f"/items/{row['item_id']}/image",
    }


def _decode_base64(content: str, *, maximum_bytes: int) -> bytes:
    try:
        payload = base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=422, detail="Invalid base64 payload") from error
    if len(payload) > maximum_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file is too large")
    return payload


@app.get("/health")
def health() -> dict[str, Any]:
    embedding_manifest = settings.embedding_dir / "manifest.json"
    index_manifest = settings.index_dir / "manifest.json"
    with database_session(settings.database_path) as connection:
        catalog_count = connection.execute("SELECT COUNT(*) FROM catalog_items").fetchone()[0]
        ready_count = connection.execute(
            "SELECT COUNT(*) FROM catalog_items WHERE embedding_status = 'ready'"
        ).fetchone()[0]
        personal_embedding_count = connection.execute(
            "SELECT COUNT(*) FROM personal_item_embeddings"
        ).fetchone()[0]
        source_counts = {
            row["source"]: row["count"]
            for row in connection.execute(
                "SELECT source, COUNT(*) AS count FROM catalog_items GROUP BY source"
            )
        }
        image_sources = {
            row["source"]: {
                "image_root": row["image_root"],
                "available": Path(row["image_root"]).is_dir(),
            }
            for row in list_dataset_sources(connection)
        }
        if settings.image_root is not None and "polyvore" not in image_sources:
            image_sources["polyvore"] = {
                "image_root": str(settings.image_root),
                "available": settings.image_root.is_dir(),
                "configuration": "environment_fallback",
            }
    return {
        "status": "ok",
        "database": str(settings.database_path),
        "catalog_items": catalog_count,
        "embedding_ready_items": ready_count,
        "personal_embedding_items": personal_embedding_count,
        "catalog_items_by_source": source_counts,
        "image_sources": image_sources,
        "embedding_manifest_available": embedding_manifest.is_file(),
        "index_manifest_available": index_manifest.is_file(),
        "image_root_configured": bool(image_sources),
    }


@app.get("/preferences/{user_id}/evaluation")
def get_user_evaluation_weights(user_id: str) -> dict[str, Any]:
    with database_session(settings.database_path) as connection:
        weights = get_evaluation_weights(connection, user_id)
    return {"user_id": user_id, "weights": weights}


@app.put("/preferences/{user_id}/evaluation")
def put_user_evaluation_weights(
    user_id: str,
    request: EvaluationWeightsRequest,
) -> dict[str, Any]:
    with database_session(settings.database_path) as connection:
        normalized = save_evaluation_weights(connection, user_id, request.weights)
    return {"user_id": user_id, "weights": normalized}


@app.post("/recommendations")
def recommend(request: RecommendationRequest) -> dict[str, Any]:
    try:
        payload = get_workflow().recommend_payload(
            user_id=request.user_id,
            request=request.request,
            max_results=request.max_results,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except BaseException as error:
        raise HTTPException(
            status_code=500,
            detail=f"Recommendation workflow failed: {type(error).__name__}: {error}",
        ) from error
    payload["image_endpoint_template"] = "/items/{item_id}/image"
    return payload


@app.get("/wardrobes/{user_id}")
def get_wardrobe(user_id: str) -> dict[str, Any]:
    with database_session(settings.database_path) as connection:
        rows = connection.execute(
            "SELECT c.* FROM wardrobe_items w "
            "JOIN catalog_items c ON c.item_id = w.item_id "
            "WHERE w.user_id = ? AND w.active = 1 "
            "ORDER BY c.item_type, c.item_id",
            (user_id,),
        ).fetchall()
        items = []
        for row in rows:
            item = _catalog_row_to_dict(row)
            if item["image_status"] == "available":
                try:
                    image_path = _resolve_image_path(
                        connection,
                        source=row["source"],
                        relative_image_path=row["relative_image_path"],
                        item_id=row["item_id"],
                    )
                    if not image_path.is_file():
                        item["image_status"] = "missing"
                        item["image_url"] = ""
                except HTTPException:
                    item["image_status"] = "missing"
                    item["image_url"] = ""
            items.append(item)
    return {"user_id": user_id, "count": len(items), "items": items}


@app.post("/wardrobes/{user_id}/imports", status_code=201)
def preview_wardrobe_import(
    user_id: str,
    request: OrderWorkbookUpload,
) -> dict[str, Any]:
    if not request.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=422, detail="Only .xlsx order exports are supported")
    payload = _decode_base64(request.content_base64, maximum_bytes=20 * 1024 * 1024)
    try:
        workbook = parse_order_workbook(
            payload,
            default_audience=request.default_audience,
        )
        with database_session(settings.database_path) as connection:
            batch_id, created, refreshed = create_import_preview(
                connection,
                user_id=user_id,
                source_filename=request.filename,
                workbook=workbook,
            )
            batch = get_import_batch(connection, user_id=user_id, batch_id=batch_id)
            rows = list_import_rows(
                connection,
                user_id=user_id,
                batch_id=batch_id,
                limit=2000,
            )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "batch": batch,
        "created": created,
        "refreshed": refreshed,
        "count": len(rows),
        "rows": rows,
    }


@app.get("/wardrobes/{user_id}/imports/{batch_id}")
def get_wardrobe_import(user_id: str, batch_id: str) -> dict[str, Any]:
    with database_session(settings.database_path) as connection:
        batch = get_import_batch(connection, user_id=user_id, batch_id=batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="Wardrobe import batch not found")
        rows = list_import_rows(
            connection,
            user_id=user_id,
            batch_id=batch_id,
            limit=2000,
        )
    return {"batch": batch, "count": len(rows), "rows": rows}


@app.post("/wardrobes/{user_id}/imports/{batch_id}/commit")
def commit_wardrobe_import(
    user_id: str,
    batch_id: str,
    request: WardrobeImportCommitRequest,
) -> dict[str, Any]:
    image_root = personal_image_root(settings.artifact_root, user_id)
    image_root.mkdir(parents=True, exist_ok=True)
    try:
        with database_session(settings.database_path) as connection:
            item_ids = commit_import_rows(
                connection,
                user_id=user_id,
                batch_id=batch_id,
                selections=[
                    selection.model_dump(exclude_none=True)
                    for selection in request.selections
                ],
                image_root=image_root,
            )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    embedding: dict[str, Any] | None = None
    if request.auto_embed:
        try:
            embedding = embed_personal_items(
                database_path=settings.database_path,
                item_ids=item_ids,
                model_dir=settings.artifact_root / "models",
                device="cuda",
            )
        except BaseException as error:
            embedding = {
                "status": "failed",
                "error_type": type(error).__name__,
                "error": str(error),
                "wardrobe_commit_preserved": True,
            }
    return {
        "batch_id": batch_id,
        "user_id": user_id,
        "committed_item_count": len(item_ids),
        "item_ids": item_ids,
        "embedding": embedding,
    }


@app.post("/wardrobes/{user_id}/items/{item_id}/image")
def upload_personal_item_image(
    user_id: str,
    item_id: str,
    request: PersonalImageUpload,
) -> dict[str, Any]:
    payload = _decode_base64(request.content_base64, maximum_bytes=20 * 1024 * 1024)
    try:
        return bind_personal_image(
            database_path=settings.database_path,
            artifact_root=settings.artifact_root,
            user_id=user_id,
            item_id=item_id,
            image_bytes=payload,
            model_dir=settings.artifact_root / "models",
            device="cuda",
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=422, detail=f"Invalid image: {error}") from error


@app.post("/wardrobes/{user_id}/items/photo", status_code=201)
def create_wardrobe_photo_item(
    user_id: str,
    request: PhotoItemRequest,
) -> dict[str, Any]:
    payload = _decode_base64(request.content_base64, maximum_bytes=20 * 1024 * 1024)
    try:
        return create_photo_item(
            database_path=settings.database_path,
            artifact_root=settings.artifact_root,
            user_id=user_id,
            image_bytes=payload,
            model_dir=settings.artifact_root / "models",
            device="cuda",
            name=request.name,
            item_type=request.item_type,
            subtype=request.subtype,
            color=request.color,
            gender=request.gender,
            size=request.size,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.put("/wardrobes/{user_id}/items/{item_id}")
def update_wardrobe_item(
    user_id: str,
    item_id: str,
    request: UpdateItemRequest,
) -> dict[str, Any]:
    try:
        return update_personal_item(
            database_path=settings.database_path,
            user_id=user_id,
            item_id=item_id,
            model_dir=settings.artifact_root / "models",
            device="cuda",
            name=request.name,
            item_type=request.item_type,
            subtype=request.subtype,
            color=request.color,
            gender=request.gender,
            size=request.size,
        )
    except ValueError as error:
        status_code = 404 if "not found" in str(error) else 422
        raise HTTPException(status_code=status_code, detail=str(error)) from error


@app.post("/wardrobes/{user_id}/items", status_code=201)
def add_wardrobe_item(user_id: str, request: WardrobeItemRequest) -> dict[str, Any]:
    with database_session(settings.database_path) as connection:
        row = connection.execute(
            "SELECT * FROM catalog_items WHERE item_id = ?",
            (request.item_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Catalog item not found")
        if row["image_status"] != "available":
            raise HTTPException(status_code=409, detail="Catalog item image is unavailable")
        connection.execute(
            "INSERT INTO wardrobe_items(user_id, item_id, active, favorite, notes, added_at) "
            "VALUES (?, ?, 1, 0, '', ?) "
            "ON CONFLICT(user_id, item_id) DO UPDATE SET active = 1",
            (user_id, request.item_id, datetime.now(timezone.utc).isoformat()),
        )
    return {"user_id": user_id, "item": _catalog_row_to_dict(row)}


@app.delete("/wardrobes/{user_id}/items/{item_id}")
def remove_wardrobe_item(user_id: str, item_id: str) -> dict[str, Any]:
    with database_session(settings.database_path) as connection:
        cursor = connection.execute(
            "UPDATE wardrobe_items SET active = 0 WHERE user_id = ? AND item_id = ?",
            (user_id, item_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Wardrobe item not found")
    return {"user_id": user_id, "item_id": item_id, "active": False}


@app.get("/catalog/search")
def search_catalog(
    q: str = Query(default="", max_length=200),
    item_type: str | None = Query(default=None, max_length=64),
    audience: str | None = Query(default=None, max_length=32),
    source: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=24, ge=1, le=100),
) -> dict[str, Any]:
    clauses = ["image_status = 'available'", "source NOT LIKE 'personal-%'"]
    parameters: list[Any] = []
    if q.strip():
        clauses.append("(name LIKE ? OR color LIKE ? OR description LIKE ?)")
        pattern = f"%{q.strip()}%"
        parameters.extend((pattern, pattern, pattern))
    if item_type:
        clauses.append("item_type = ?")
        parameters.append(item_type)
    if audience:
        clauses.append("gender = ?")
        parameters.append(audience)
    if source:
        clauses.append("source = ?")
        parameters.append(source)
    parameters.append(limit)
    sql = (
        "SELECT * FROM catalog_items WHERE "
        + " AND ".join(clauses)
        + " ORDER BY item_id LIMIT ?"
    )
    with database_session(settings.database_path) as connection:
        rows = connection.execute(sql, parameters).fetchall()
    return {"count": len(rows), "items": [_catalog_row_to_dict(row) for row in rows]}


@app.get("/items/{item_id}")
def get_item(item_id: str) -> dict[str, Any]:
    with database_session(settings.database_path) as connection:
        row = connection.execute(
            "SELECT * FROM catalog_items WHERE item_id = ?",
            (item_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Catalog item not found")
    return _catalog_row_to_dict(row)


def _resolve_image_path(
    connection,
    *,
    source: str,
    relative_image_path: str,
    item_id: str | None = None,
) -> Path:
    """Resolve an image file, preferring the dataset root then the personal root.

    Uploaded and bound photos are stored under ``personal_image_root`` while
    the catalog row may still carry a dataset ``source`` (e.g. polyvore) whose
    image root is not configured. Fall back to the item owner's personal image
    root so those images remain reachable. Raises 404 when no root or file
    exists (an absent image, not a server error).
    """
    roots: list[Path] = []
    image_root = get_source_image_root(connection, source)
    if image_root is None and source == "polyvore":
        image_root = settings.image_root
    if image_root is not None:
        roots.append(image_root)
    if item_id is not None:
        user_row = connection.execute(
            "SELECT user_id FROM wardrobe_items "
            "WHERE item_id = ? AND active = 1 LIMIT 1",
            (item_id,),
        ).fetchone()
        if user_row is not None:
            roots.append(personal_image_root(settings.artifact_root, user_row["user_id"]))
    if not roots:
        raise HTTPException(status_code=404, detail="Item image not found")

    relative_path = PurePosixPath(relative_image_path)
    for root in roots:
        resolved_root = root.resolve()
        candidate = resolved_root.joinpath(*relative_path.parts).resolve()
        if not candidate.is_relative_to(resolved_root):
            raise HTTPException(status_code=400, detail="Invalid image path")
        if candidate.is_file():
            return candidate
    # No file exists under any configured root; return the first candidate so
    # the caller reports 404 instead of falling through to a missing image.
    return roots[0].resolve().joinpath(*relative_path.parts).resolve()


def _image_response(connection, row, *, item_id: str):
    image_path = _resolve_image_path(
        connection,
        source=row["source"],
        relative_image_path=row["relative_image_path"],
        item_id=item_id,
    )
    if row["image_status"] != "available" or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Item image not found")
    return FileResponse(image_path, media_type="image/jpeg", filename=image_path.name)


@app.get("/items/{item_id}/image", response_class=FileResponse)
def get_item_image(item_id: str):
    with database_session(settings.database_path) as connection:
        row = connection.execute(
            "SELECT source, relative_image_path, image_status "
            "FROM catalog_items WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Catalog item not found")
        return _image_response(connection, row, item_id=item_id)


@app.get("/items/{item_id}/images")
def list_item_images(item_id: str) -> dict[str, Any]:
    with database_session(settings.database_path) as connection:
        item = connection.execute(
            "SELECT source FROM catalog_items WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        if item is None:
            raise HTTPException(status_code=404, detail="Catalog item not found")
        rows = connection.execute(
            "SELECT position, image_role, image_filename, relative_image_path, "
            "image_status, is_primary FROM catalog_item_images "
            "WHERE item_id = ? ORDER BY position",
            (item_id,),
        ).fetchall()
    return {
        "item_id": item_id,
        "source": item["source"],
        "count": len(rows),
        "images": [
            {
                **dict(row),
                "image_url": f"/items/{item_id}/images/{row['position']}",
            }
            for row in rows
        ],
    }


@app.get("/items/{item_id}/images/{position}", response_class=FileResponse)
def get_item_image_by_position(item_id: str, position: int):
    with database_session(settings.database_path) as connection:
        row = connection.execute(
            "SELECT c.source, i.relative_image_path, i.image_status "
            "FROM catalog_item_images i "
            "JOIN catalog_items c ON c.item_id = i.item_id "
            "WHERE i.item_id = ? AND i.position = ?",
            (item_id, position),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Item image not found")
        return _image_response(connection, row, item_id=item_id)


def main() -> None:
    import uvicorn

    uvicorn.run("styleforge.api:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
