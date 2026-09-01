"""Personal-vector lifecycle, invalidation, and runtime reuse tests."""

from __future__ import annotations

import io
from typing import Any

import numpy as np
from PIL import Image

from styleforge.repositories.database import database_session
from styleforge.repositories.personal_embedding_repository import PersonalEmbeddingStore
from styleforge.services.personal_images import bind_personal_image
from styleforge.services.wardrobe_item_service import create_photo_item


def _photo_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (32, 32), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeEncoder:
    init_calls = 0
    dimension = 3

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        type(self).init_calls += 1

    def encode_images(
        self,
        images: list[Image.Image],
        batch_size: int = 64,
    ) -> np.ndarray:
        return np.asarray([[1.0, 0.0, 0.0] for _image in images], dtype=np.float32)

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        return np.asarray([[0.0, 1.0, 0.0] for _text in texts], dtype=np.float32)


def _create_pending(db_dsn: str, artifact_root, user_id: str, color) -> str:
    created = create_photo_item(
        database_path=db_dsn,
        artifact_root=artifact_root,
        user_id=user_id,
        image_bytes=_photo_bytes(color),
        model_dir=artifact_root / "models",
        name="测试上衣",
        item_type="top",
        color="black",
        skip_embedding=True,
    )
    return created["item_id"]


def test_personal_encoder_is_reused_and_fingerprints_are_persisted(
    tmp_path, db_dsn, monkeypatch
) -> None:
    import styleforge.services.personal_embeddings as embeddings
    import styleforge.vision.fashion_clip as fashion_clip

    embeddings.clear_personal_embedding_runtime_cache()
    _FakeEncoder.init_calls = 0
    monkeypatch.setattr(fashion_clip, "FashionClipEncoder", _FakeEncoder)
    first = _create_pending(db_dsn, tmp_path / "artifacts", "u1", (10, 20, 30))
    second = _create_pending(db_dsn, tmp_path / "artifacts", "u1", (40, 50, 60))

    first_result = embeddings.embed_personal_items(
        database_path=db_dsn,
        item_ids=[first],
        model_dir=tmp_path / "models",
        device="cpu",
        precision="float32",
    )
    second_result = embeddings.embed_personal_items(
        database_path=db_dsn,
        item_ids=[second],
        model_dir=tmp_path / "models",
        device="cpu",
        precision="float32",
    )

    with database_session(db_dsn) as connection:
        rows = connection.execute(
            "SELECT item_id, input_fingerprint FROM personal_item_embeddings "
            "WHERE item_id = ANY(%s) ORDER BY item_id",
            ([first, second],),
        ).fetchall()

    assert _FakeEncoder.init_calls == 1
    assert first_result["embedded_items"] == 1
    assert second_result["embedded_items"] == 1
    assert len(rows) == 2
    assert all(len(row["input_fingerprint"]) == 64 for row in rows)
    assert rows[0]["input_fingerprint"] != rows[1]["input_fingerprint"]
    embeddings.clear_personal_embedding_runtime_cache()


def test_replacing_image_invalidates_old_vector_and_returns_safe_failure(
    tmp_path, db_dsn, monkeypatch
) -> None:
    import styleforge.services.personal_embeddings as embeddings
    import styleforge.services.personal_images as personal_images
    import styleforge.vision.fashion_clip as fashion_clip

    embeddings.clear_personal_embedding_runtime_cache()
    _FakeEncoder.init_calls = 0
    monkeypatch.setattr(fashion_clip, "FashionClipEncoder", _FakeEncoder)
    item_id = _create_pending(db_dsn, tmp_path / "artifacts", "u1", (10, 20, 30))
    embeddings.embed_personal_items(
        database_path=db_dsn,
        item_ids=[item_id],
        model_dir=tmp_path / "models",
        device="cpu",
        precision="float32",
    )

    def _fail(**_kwargs: Any) -> None:
        raise RuntimeError("secret GPU installation path")

    monkeypatch.setattr(personal_images, "embed_personal_items", _fail)
    result = bind_personal_image(
        database_path=db_dsn,
        artifact_root=tmp_path / "artifacts",
        user_id="u1",
        item_id=item_id,
        image_bytes=_photo_bytes((200, 100, 50)),
        model_dir=tmp_path / "models",
        device="cpu",
    )

    with database_session(db_dsn) as connection:
        item = connection.execute(
            "SELECT embedding_status FROM catalog_items WHERE item_id = %s",
            (item_id,),
        ).fetchone()
        vector_count = connection.execute(
            "SELECT COUNT(*) FROM personal_item_embeddings WHERE item_id = %s",
            (item_id,),
        ).fetchone()[0]

    assert result["embedding"]["status"] == "failed"
    assert result["embedding"]["retryable"] is True
    assert "secret GPU installation path" not in str(result)
    assert item["embedding_status"] == "failed"
    assert vector_count == 0
    embeddings.clear_personal_embedding_runtime_cache()


def test_personal_store_never_scores_a_non_ready_stale_vector(
    tmp_path, db_dsn, monkeypatch
) -> None:
    import styleforge.services.personal_embeddings as embeddings
    import styleforge.vision.fashion_clip as fashion_clip

    embeddings.clear_personal_embedding_runtime_cache()
    monkeypatch.setattr(fashion_clip, "FashionClipEncoder", _FakeEncoder)
    item_id = _create_pending(db_dsn, tmp_path / "artifacts", "u1", (10, 20, 30))
    embeddings.embed_personal_items(
        database_path=db_dsn,
        item_ids=[item_id],
        model_dir=tmp_path / "models",
        device="cpu",
        precision="float32",
    )
    with database_session(db_dsn) as connection:
        ready_scores = PersonalEmbeddingStore(connection).score_items(
            np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
            [item_id],
        )
        connection.execute(
            "UPDATE catalog_items SET embedding_status = 'pending' WHERE item_id = %s",
            (item_id,),
        )
        stale_scores = PersonalEmbeddingStore(connection).score_items(
            np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
            [item_id],
        )

    assert ready_scores[item_id] == 1.0
    assert stale_scores == {}
    embeddings.clear_personal_embedding_runtime_cache()
