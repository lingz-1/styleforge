import json

import numpy as np

from styleforge.services.catalog_vector_store import CatalogVectorStore, normalize_relevance


def test_vector_store_respects_allowed_item_ids(tmp_path) -> None:
    embeddings = np.asarray(
        [
            [1.0, 0.0],
            [0.0, 1.0],
            [2**-0.5, 2**-0.5],
        ],
        dtype=np.float32,
    )
    np.save(tmp_path / "embeddings.npy", embeddings)
    (tmp_path / "item_ids.json").write_text(
        json.dumps(["a", "b", "c"]),
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"status": "completed", "embedding_version": "test"}),
        encoding="utf-8",
    )
    store = CatalogVectorStore(tmp_path)

    scores = store.score_items(np.asarray([1.0, 0.0]), ["b", "c", "missing"])

    assert set(scores) == {"b", "c"}
    assert scores["c"] > scores["b"]


def test_normalize_relevance_has_stable_flat_distribution() -> None:
    assert normalize_relevance({"a": 0.2, "b": 0.2}) == {"a": 50.0, "b": 50.0}
