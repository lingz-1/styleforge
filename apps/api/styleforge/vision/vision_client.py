"""Multi-modal clothing-image analysis over an OpenAI-compatible endpoint.

The backend speaks the same ``/v1/chat/completions`` protocol that the
reference ``wardrowbe-main`` project uses: the image is embedded as a base64
data URL inside an ``image_url`` content block, and the model is asked to
return a strict JSON object. By default the endpoint is the local
``tools/gemini_proxy.py`` (an OpenAI-compatible proxy in front of Vertex
Gemini), but any compatible endpoint can be configured via
``STYLEFORGE_VISION_BASE_URL``.
"""

from __future__ import annotations

import base64
import io
import json
import time
from typing import Any, Protocol

import httpx
from PIL import Image, ImageOps

from styleforge.core.config import Settings


class VisionUnavailable(Exception):
    """Provider, network, auth, rate-limit, or 5xx failure (after retries)."""


class VisionInvalidJson(Exception):
    """Provider returned text that cannot be parsed as JSON."""


def preprocess_image(image_bytes: bytes) -> str:
    """Normalize an uploaded image to a JPEG base64 data URL for the vision API.

    Mirrors the reference ``_preprocess_image``: EXIF transpose, RGB, capped at
    512x512, JPEG q85. The small payload keeps proxy latency low.
    """
    with Image.open(io.BytesIO(image_bytes)) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        img = ImageOps.exif_transpose(img)
        img.thumbnail((512, 512), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        raw = buffer.getvalue()
    encoded = base64.b64encode(raw).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


class VisionClient(Protocol):
    """The call surface the API and services depend on (fake-able in tests)."""

    def analyze_image(self, image_bytes: bytes, prompt: str) -> dict[str, Any]: ...


class OpenAiVisionClient:
    """OpenAI-compatible vision client backed by httpx."""

    name = "vision.analyze_clothing"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120.0,
        max_retries: int = 2,
        max_tokens: int = 2048,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_tokens = max_tokens

    def analyze_image(self, image_bytes: bytes, prompt: str) -> dict[str, Any]:
        data_url = preprocess_image(image_bytes)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    }
                ],
            },
        ]
        request_body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "max_tokens": self.max_tokens,
            "temperature": 0.2,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        raw = self._request_with_retry(request_body, headers)
        text = self._extract_text(raw)
        try:
            return json.loads(self._extract_json_object(text))
        except (json.JSONDecodeError, TypeError) as error:
            raise VisionInvalidJson(
                f"Vision model returned non-JSON output: {error}"
            ) from error

    @staticmethod
    def _extract_json_object(text: str) -> str:
        """Pull the JSON object out of the model's raw text.

        Gemini commonly wraps the JSON in a markdown code fence
        (`````json ... ````), which ``json.loads`` rejects verbatim. Strip the
        fence and any prose, then fall back to the outermost brace-delimited
        block so a stray sentence never breaks the parse.
        """
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        if cleaned.startswith("{"):
            return cleaned
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end < start:
            return cleaned
        return cleaned[start : end + 1]

    def _request_with_retry(
        self, request_body: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=request_body,
                    timeout=self.timeout,
                )
                if response.status_code >= 500 or response.status_code == 429:
                    raise httpx.HTTPStatusError(
                        f"Vision provider returned {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException as error:
                last_error = error
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 429 or error.response.status_code >= 500:
                    last_error = error
                else:
                    raise VisionUnavailable(
                        f"Vision provider error {error.response.status_code}: "
                        f"{error.response.text[:300]}"
                    ) from error
            except httpx.HTTPError as error:
                last_error = error
            if attempt < self.max_retries:
                time.sleep(min(2**attempt, 8))
        raise VisionUnavailable(
            f"Vision provider unavailable after {self.max_retries + 1} attempts: "
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise VisionUnavailable(
                f"Vision provider returned an unexpected payload: {str(payload)[:300]}"
            ) from error
        if not isinstance(content, str):
            raise VisionUnavailable("Vision provider returned non-text content")
        return content


def vision_client_from_settings(settings: Settings) -> VisionClient | None:
    """Build the vision client, or None when the feature is disabled."""
    if not settings.vision_enabled:
        return None
    return OpenAiVisionClient(
        base_url=settings.vision_base_url,
        model=settings.vision_model,
        api_key=settings.vision_api_key,
        timeout=settings.vision_timeout,
        max_retries=settings.vision_max_retries,
    )
