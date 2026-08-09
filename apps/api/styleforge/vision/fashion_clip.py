"""Local FashionCLIP image and text encoder."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    import numpy as np
    from PIL import Image


SUPPORTED_PRECISIONS = {"float32", "float16", "bfloat16"}


class FashionClipEncoder:
    """Load FashionCLIP locally and emit L2-normalized float32 embeddings."""

    def __init__(
        self,
        model_dir: Path,
        *,
        device: str = "cuda",
        precision: str = "float16",
    ) -> None:
        import torch
        from transformers import AutoProcessor, CLIPModel

        model_dir = model_dir.resolve()
        if not (model_dir / "config.json").is_file():
            raise FileNotFoundError(f"FashionCLIP config is missing: {model_dir / 'config.json'}")
        if not (model_dir / "model.safetensors").is_file():
            raise FileNotFoundError(
                f"FashionCLIP weights are missing: {model_dir / 'model.safetensors'}"
            )
        if precision not in SUPPORTED_PRECISIONS:
            raise ValueError(
                f"Unsupported precision {precision!r}; choose from {sorted(SUPPORTED_PRECISIONS)}"
            )
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false")

        self.model_dir = model_dir
        self.device = torch.device(device)
        self.precision = precision
        self.processor = AutoProcessor.from_pretrained(
            model_dir,
            local_files_only=True,
            use_fast=False,
        )
        self.model = CLIPModel.from_pretrained(
            model_dir,
            local_files_only=True,
            use_safetensors=True,
        ).eval()
        self.model.to(self.device)
        self.dimension = int(self.model.config.projection_dim)

    def _autocast_context(self):
        import torch

        if self.device.type != "cuda" or self.precision == "float32":
            return nullcontext()
        dtype = torch.float16 if self.precision == "float16" else torch.bfloat16
        return torch.autocast(device_type="cuda", dtype=dtype)

    def encode_images(self, images: Sequence["Image.Image"]) -> "np.ndarray":
        """Encode an image batch into normalized float32 NumPy vectors."""
        import torch
        import torch.nn.functional as functional

        if not images:
            raise ValueError("images must not be empty")
        inputs = self.processor(images=list(images), return_tensors="pt")
        pixel_values = inputs["pixel_values"].to(self.device, non_blocking=True)
        with torch.inference_mode(), self._autocast_context():
            features = self.model.get_image_features(pixel_values=pixel_values)
        features = functional.normalize(features.float(), p=2, dim=-1)
        return features.cpu().numpy()

    def encode_texts(self, texts: Sequence[str]) -> "np.ndarray":
        """Encode text queries into the same normalized FashionCLIP space."""
        import torch
        import torch.nn.functional as functional

        if not texts:
            raise ValueError("texts must not be empty")
        inputs = self.processor(
            text=list(texts),
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=77,
        )
        inputs = {key: value.to(self.device, non_blocking=True) for key, value in inputs.items()}
        with torch.inference_mode(), self._autocast_context():
            features = self.model.get_text_features(**inputs)
        features = functional.normalize(features.float(), p=2, dim=-1)
        return features.cpu().numpy()
