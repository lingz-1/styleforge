# OpenAI-compatible proxy -> Gemini (Vertex AI via google-genai SDK).
#
# wardrowbe calls a plain OpenAI /chat/completions endpoint. The AQ API key only
# works on Vertex AI (Agent Platform), whose native OpenAI-compatible endpoint
# requires an OAuth token - not something wardrowbe can provide. So this small
# FastAPI app translates OpenAI-format requests into google-genai Vertex calls.
#
# Usage:
#   "D:/anaconda/envs/cc-python/python.exe" gemini_proxy.py
#   -> listens on 0.0.0.0:5088
# Then point wardrowbe's AI_BASE_URL at http://host.docker.internal:5088/v1

import base64
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
from PIL import Image

from google import genai

load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
PROJECT_ID = os.environ.get("MY_PROJECT_ID", "").strip()
LOCATION = os.environ.get("GCP_LOCATION", "us-central1").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()

if not GEMINI_API_KEY or not PROJECT_ID:
    sys.exit("Error: GEMINI_API_KEY and MY_PROJECT_ID must be set in .env")

client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION,
    api_key=GEMINI_API_KEY,
)

print(f"Gemini proxy ready -> {GEMINI_MODEL} @ vertex:{PROJECT_ID} ({LOCATION})")


def _image_from_url(image_url: str) -> Image.Image:
    """image_url may be a base64 data URI or an http(s) URL."""
    if image_url.startswith("data:"):
        # data:image/jpeg;base64,XXXX
        header, _, b64 = image_url.partition(",")
        raw = base64.b64decode(b64)
        return Image.open(io.BytesIO(raw)).convert("RGB")
    if image_url.startswith("http://") or image_url.startswith("https://"):
        import urllib.request
        req = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        return Image.open(io.BytesIO(raw)).convert("RGB")
    raise ValueError("image_url must be a base64 data URI or an http(s) URL")


def _convert_openai_messages(messages: list[dict]) -> tuple[list[str], list[Image.Image]]:
    """Split OpenAI chat messages into text list + PIL images."""
    text_parts: list[str] = []
    images: list[Image.Image] = []

    for msg in messages:
        content = msg.get("content")
        role = msg.get("role", "user")

        if isinstance(content, str):
            text_parts.append(content)
            continue

        if isinstance(content, list):
            for part in content:
                if isinstance(part, str):
                    text_parts.append(part)
                    continue
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype == "text":
                    text_parts.append(part.get("text", ""))
                elif ptype == "image_url":
                    iu = part.get("image_url")
                    url = iu if isinstance(iu, str) else (iu or {}).get("url", "")
                    if url:
                        images.append(_image_from_url(url))

    return text_parts, images


def _chat_completions(body: dict) -> dict:
    messages = body.get("messages", [])
    max_tokens = body.get("max_tokens", 1024)
    temperature = body.get("temperature", 0.2)

    text_parts, images = _convert_openai_messages(messages)

    contents = text_parts + images
    if not contents:
        raise ValueError("Empty request: no text or images provided")

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config={
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        },
    )

    text = response.text or ""

    # Estimate token counts (rough: chars/4) since genai doesn't expose usage here.
    prompt_chars = sum(len(str(t)) for t in text_parts) + 0
    usage = {
        "prompt_tokens": max(1, prompt_chars // 4),
        "completion_tokens": max(1, len(text) // 4),
        "total_tokens": max(1, (prompt_chars + len(text)) // 4),
    }

    return {
        "id": "chatcmpl-geminiproxy",
        "object": "chat.completion",
        "created": 0,
        "model": GEMINI_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }


def _list_models() -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": GEMINI_MODEL,
                "object": "model",
                "owned_by": "google",
            }
        ],
    }


# --- HTTP server (stdlib only, no Flask/FastAPI needed) ---
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import urllib.parse


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict, raw: bytes | None = None) -> None:
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self._send(404, {"error": {"message": f"not found: {self.path}"}})
            return
        try:
            body = self._read_body()
            result = _chat_completions(body)
            self._send(200, result)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send(500, {"error": {"message": str(e)}})

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/v1/models":
            self._send(200, _list_models())
        else:
            self._send(404, {"error": {"message": f"not found: {self.path}"}})

    def log_message(self, fmt, *args):
        print(f"[proxy] {self.client_address[0]} {fmt % args}", flush=True)


if __name__ == "__main__":
    port = int(os.environ.get("PROXY_PORT", "5088"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Gemini OpenAI-proxy listening on http://0.0.0.0:{port}/v1")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
