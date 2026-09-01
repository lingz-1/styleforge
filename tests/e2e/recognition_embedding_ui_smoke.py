"""Browser smoke for recognition-batch embedding progress and retry UI."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.parse import unquote

from playwright.sync_api import Route, expect, sync_playwright


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = WORKSPACE_ROOT / "artifacts" / "e2e-runtime"
EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
WEB_ROOT = os.getenv("STYLEFORGE_E2E_WEB_ROOT", "http://127.0.0.1:15174").rstrip("/")


def _batch(status: str, embedding_status: str) -> dict:
    embedding = {
        "status": embedding_status,
        "requested_items": 1,
        "retryable": embedding_status == "failed",
    }
    if embedding_status == "failed":
        embedding.update(
            {
                "error_code": "PERSONAL_EMBEDDING_FAILED",
                "error": "衣物已保存，但向量生成失败，可稍后重试",
                "wardrobe_commit_preserved": True,
            }
        )
    if embedding_status == "completed":
        embedding.update({"embedded_items": 1, "image_embeddings": 1})
    return {
        "batch_id": "recognition:e2e",
        "user_id": "demo-user",
        "total": 1,
        "gender": "women",
        "done": 1,
        "percent": 100,
        "succeeded": 1,
        "failed": 0,
        "status": status,
        "embedding": embedding,
        "eta_seconds": 0,
        "started_at": "2026-08-29T08:00:00+00:00",
        "finished_at": None if status == "embedding" else "2026-08-29T08:00:05+00:00",
        "results": [
            {
                "index": 0,
                "filename": "black-shirt.png",
                "status": "succeeded",
                "reason": "",
                "item_id": "11111111-1111-4111-8111-111111111111",
                "item_type": "top",
                "subtype": "t_shirt",
                "color": "black",
                "name": "黑色纯棉短袖T恤",
                "confidence": 0.92,
                "attributes": {"description": "黑色纯棉短袖T恤"},
            }
        ],
    }


def _recognition_batch(status: str) -> dict:
    succeeded = status == "completed"
    return {
        "batch_id": "recognition:recover",
        "user_id": "demo-user",
        "total": 1,
        "gender": "women",
        "done": 1 if status in {"completed", "partial_failed"} else 0,
        "percent": 100 if status in {"completed", "partial_failed"} else 0,
        "succeeded": int(succeeded),
        "failed": int(status == "partial_failed"),
        "status": status,
        "embedding": {
            "status": "skipped",
            "reason": "disabled",
            "retryable": False,
        },
        "eta_seconds": 0,
        "started_at": "2026-08-29T08:01:00+00:00",
        "finished_at": (
            "2026-08-29T08:01:05+00:00"
            if status in {"completed", "partial_failed"}
            else None
        ),
        "results": [
            {
                "index": 0,
                "filename": "recover-shirt.png",
                "status": "succeeded" if succeeded else (
                    "failed" if status == "partial_failed" else "pending"
                ),
                "reason": "" if succeeded else (
                    "low_confidence" if status == "partial_failed" else ""
                ),
                "item_id": (
                    "33333333-3333-4333-8333-333333333333" if succeeded else ""
                ),
                "item_type": "top",
                "subtype": "t_shirt",
                "color": "blue",
                "name": "蓝色衬衫",
                "confidence": 0.92 if succeeded else 0.2,
                "attributes": {"description": "蓝色衬衫"},
                "attempt_count": 2 if succeeded else 1,
            }
        ],
    }


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    retry_started = False
    list_after_retry = 0
    recognition_retry_started = False
    recognition_list_after_retry = 0
    personal_repaired = False
    console_errors: list[str] = []

    def handle(route: Route) -> None:
        nonlocal retry_started, list_after_retry, personal_repaired
        nonlocal recognition_retry_started, recognition_list_after_retry
        request = route.request
        path = unquote(request.url.split("/api", 1)[-1].split("?", 1)[0])
        if path == "/catalog/taxonomy":
            payload = {"categories": []}
        elif path == "/wardrobes/demo-user":
            payload = {
                "user_id": "demo-user",
                "count": 1,
                "items": [
                    {
                        "item_id": "22222222-2222-4222-8222-222222222222",
                        "name": "待修复白衬衫",
                        "item_type": "top",
                        "color": "white",
                        "gender": "women",
                        "attributes": {},
                        "image_url": "/items/22222222-2222-4222-8222-222222222222/image",
                        "embedding_status": "ready" if personal_repaired else "pending",
                    }
                ],
            }
        elif path == "/wardrobes/demo-user/embeddings/retry":
            personal_repaired = True
            payload = {
                "status": "completed",
                "user_id": "demo-user",
                "eligible_items": 1,
                "embedded_items": 1,
            }
        elif path.endswith("/items/0/image"):
            route.fulfill(
                status=200,
                content_type="image/png",
                body=base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                ),
            )
            return
        elif path.endswith("/recognition:e2e/retry-embedding"):
            retry_started = True
            payload = _batch("embedding", "running")
        elif path.endswith("/recognition:recover/retry"):
            recognition_retry_started = True
            payload = _recognition_batch("retrying")
        elif path == "/wardrobes/demo-user/recognition-batches":
            if retry_started:
                list_after_retry += 1
                embedding_batch = (
                    _batch("embedding", "running")
                    if list_after_retry == 1
                    else _batch("completed", "completed")
                )
            else:
                embedding_batch = _batch("completed", "failed")
            if recognition_retry_started:
                recognition_list_after_retry += 1
                recognition_batch = (
                    _recognition_batch("recognizing")
                    if recognition_list_after_retry == 1
                    else _recognition_batch("completed")
                )
            else:
                recognition_batch = _recognition_batch("partial_failed")
            payload = {
                "user_id": "demo-user",
                "count": 2,
                "batches": [embedding_batch, recognition_batch],
            }
        elif path.startswith("/items/") and path.endswith("/image"):
            payload = {}
        else:
            route.abort()
            return
        route.fulfill(
            status=202 if request.method == "POST" else 200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(EDGE_PATH),
            headless=True,
        )
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.route("**/api/**", handle)
        page.goto(f"{WEB_ROOT}/wardrobe", wait_until="networkidle")

        expect(page.get_by_text("部分图片未完成识别")).to_be_visible()
        expect(page.locator(".task-img img")).to_have_count(2)
        expect(page.locator(".task-img img").nth(1)).to_have_js_property("naturalWidth", 1)
        recognition_retry = page.get_by_role("button", name="重试未完成识别")
        expect(recognition_retry).to_be_visible()
        recognition_retry.click()
        expect(page.get_by_text("正在重试", exact=True)).to_be_visible()
        expect(page.get_by_text("部分图片未完成识别")).not_to_be_visible(timeout=6_000)
        expect(page.get_by_text("已完成", exact=True)).to_have_count(2, timeout=6_000)

        expect(page.get_by_text("衣物已入库，但检索向量生成失败")).to_be_visible()
        retry_button = page.get_by_role("button", name="重试生成向量")
        expect(retry_button).to_be_visible()
        retry_button.click()

        expect(page.get_by_text("生成向量中", exact=True)).to_be_visible()
        expect(page.get_by_text("正在为 1 件衣物生成检索向量")).to_be_visible()
        expect(page.get_by_text("已完成", exact=True)).to_be_visible(timeout=6_000)
        expect(page.get_by_text("衣物已入库，但检索向量生成失败")).not_to_be_visible()

        repair_button = page.get_by_role("button", name="补齐检索向量（1）")
        expect(repair_button).to_be_visible()
        page.get_by_text("上装（1）", exact=True).click()
        expect(page.get_by_text("待生成向量", exact=True)).to_be_visible()
        repair_button.click()
        expect(page.get_by_role("button", name="补齐检索向量（1）")).not_to_be_visible()
        expect(page.get_by_text("待生成向量", exact=True)).not_to_be_visible()
        page.screenshot(
            path=str(OUTPUT_ROOT / "recognition-embedding-retry.png"),
            full_page=True,
        )
        browser.close()

    if console_errors:
        raise AssertionError(f"Browser console errors: {console_errors}")
    print("recognition_embedding_ui_smoke=passed")


if __name__ == "__main__":
    main()
