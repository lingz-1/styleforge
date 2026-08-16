"""Serve the standalone p-outfit eval showcase.

Routes:
    /              -> index.html
    /data.json     -> merged case+report data (built by build_data.py)
    /img/{id}.jpg  -> official Polyvore-Outfits item photo (proxied from disk)

Run:  D:\\anaconda\\envs\\style\\python.exe evals/frontend/server.py [--port 8000]
The image root is read from env EVAL_IMAGE_DIR and defaults to the local
official benchmark layout.
"""

from __future__ import annotations

import os
import pathlib

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse, Response

FRONT = pathlib.Path(__file__).resolve().parent
IMG_DIR = pathlib.Path(os.environ.get(
    "EVAL_IMAGE_DIR",
    r"E:\01-style-dataset\p-outfit\images\nondisjoint\train",
))

app = FastAPI(title="p-outfit eval showcase", docs_url=None, redoc_url=None)


@app.get("/", include_in_schema=False)
def index() -> Response:
    return FileResponse(FRONT / "index.html")


@app.get("/data.json", include_in_schema=False)
def data() -> Response:
    path = FRONT / "data.json"
    if not path.is_file():
        return PlainTextResponse("data.json not found — run build_data.py first", status_code=404)
    return FileResponse(path)


@app.get("/img/{item_id}.jpg", include_in_schema=False)
def item_photo(item_id: str) -> Response:
    path = IMG_DIR / f"{item_id}.jpg"
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Serve the p-outfit eval showcase")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    print(f"Serving eval showcase at http://{args.host}:{args.port}  (images: {IMG_DIR})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
