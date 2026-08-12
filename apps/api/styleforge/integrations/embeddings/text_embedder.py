"""Local text embedder for knowledge-chunk vectors (FashionCLIP transition).

Wraps the shared :class:`FashionClipEncoder.encode_texts` so RAG indexing and
querying use the exact same 512-dimensional L2-normalized space as the catalog
visual index. Long paragraphs are sub-chunked at indexing time (see
``knowledge.chunking``) to stay inside FashionCLIP's 77-token window.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    import numpy as np

from styleforge.vision.fashion_clip import FashionClipEncoder


class TextEmbedder:
    """Thin text-encoding adapter over the shared FashionCLIP encoder."""

    def __init__(
        self,
        model_dir: Path,
        *,
        device: str = "cuda",
        precision: str = "float16",
    ) -> None:
        self._encoder = FashionClipEncoder(
            model_dir,
            device=device,
            precision=precision,
        )

    @property
    def dimension(self) -> int:
        return self._encoder.dimension

    def embed_texts(self, texts: Sequence[str]) -> "np.ndarray":
        """Encode texts into normalized float32 vectors (rows = texts)."""
        return self._encoder.encode_texts(list(texts))
