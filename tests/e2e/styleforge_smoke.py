"""Browser-level StyleForge smoke test using the real API and PostgreSQL."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, expect, sync_playwright


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = Path(
    os.getenv(
        "STYLEFORGE_E2E_OUTPUT_ROOT",
        WORKSPACE_ROOT / "artifacts" / "e2e-runtime",
    )
).resolve()
EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
API_ROOT = os.getenv("STYLEFORGE_E2E_API_ROOT", "http://127.0.0.1:18000").rstrip("/")
WEB_ROOT = os.getenv("STYLEFORGE_E2E_WEB_ROOT", "http://127.0.0.1:15173").rstrip("/")

# Valid 4x4 RGB PNG used only as a deterministic upload fixture.
PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAE0lEQVR4nGP88OED"
    "AwwwwVl4OQCNpALYc6NlUQAAAABJRU5ErkJggg=="
)

WARDROBE_FIXTURES = (
    ("top", "白色衬衫", "white"),
    ("pants", "蓝色长裤", "blue"),
    ("shoes", "黑色皮鞋", "black"),
    ("outwear", "灰色西装外套", "gray"),
    ("bag", "棕色通勤包", "brown"),
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def record_console_error(errors: list[str], message) -> None:
    if message.type != "error":
        return
    location = message.location or {}
    errors.append(f"{message.text} @ {location.get('url', '')}")


def seed_wardrobe(context: BrowserContext, user_id: str) -> list[str]:
    item_ids: list[str] = []
    for item_type, name, color in WARDROBE_FIXTURES:
        response = context.request.post(
            f"{API_ROOT}/wardrobes/{user_id}/items/photo",
            data={
                "filename": f"{item_type}.png",
                "content_base64": PNG_BASE64,
                "item_type": item_type,
                "name": name,
                "color": color,
                "gender": "women",
                "attributes": {"description": f"E2E fixture: {name}"},
            },
            timeout=120_000,
        )
        require(
            response.status == 201,
            f"Seed {item_type} failed: HTTP {response.status} {response.text()}",
        )
        item_ids.append(response.json()["item_id"])
    return item_ids


def assert_health(context: BrowserContext) -> dict:
    response = context.request.get(f"{API_ROOT}/health")
    require(response.status == 200, f"Health check failed: HTTP {response.status}")
    payload = response.json()
    require(
        payload.get("database") == {"backend": "postgresql", "status": "connected"},
        f"Unexpected database health: {payload.get('database')}",
    )
    require(
        payload.get("outfit_recommend_result_schema")
        == "styleforge.outfit-recommend-result.v1",
        f"Wrong API contract version: {payload.get('outfit_recommend_result_schema')}",
    )
    retrieval = payload.get("wardrobe_retrieval") or {}
    require(
        retrieval.get("strategy") == "fashionclip_hybrid_with_keyword_fallback",
        f"Unexpected retrieval strategy: {retrieval}",
    )
    require("semantic_artifacts_available" in retrieval, "Missing retrieval health details")
    return payload


def assert_wardrobe_page(page: Page, user_id: str) -> None:
    page.goto(f"{WEB_ROOT}/wardrobe", wait_until="networkidle")
    user_input = page.locator('input[placeholder="用户 ID"]')
    expect(user_input).to_have_value(user_id)
    page.get_by_role("button", name="全部展开").click()
    for _, name, _ in WARDROBE_FIXTURES:
        expect(page.get_by_text(name, exact=True)).to_be_visible()
    page.screenshot(path=str(OUTPUT_ROOT / "wardrobe.png"), full_page=True)


def execute_recommendation(page: Page, context: BrowserContext, user_id: str) -> dict:
    page.goto(f"{WEB_ROOT}/recommend", wait_until="networkidle")
    expect(page.get_by_role("heading", name="把需求交给三位造型 Agent")).to_be_visible()
    expect(page.locator('input[aria-label="用户 ID"]')).to_have_value(user_id)

    prompt = "请用白色衬衫、蓝色长裤和黑色皮鞋搭一套日常通勤穿搭"
    textarea = page.locator('textarea[placeholder^="例如"]')
    textarea.fill(prompt)
    with page.expect_response(
        lambda response: response.url.endswith("/api/tasks/execute")
        and response.request.method == "POST",
        timeout=120_000,
    ) as response_info:
        page.get_by_role("button", name="让三位 Agent 处理").click()
    response = response_info.value
    require(response.status == 200, f"Task execution failed: HTTP {response.status}")
    payload = response.json()
    require(payload.get("status") == "completed", f"Task was not completed: {payload}")
    require(payload.get("task_type") == "outfit_recommend", f"Wrong task type: {payload}")
    result = payload.get("result") or {}
    require(
        result.get("schema_version") == "styleforge.outfit-recommend-result.v1",
        f"Wrong recommendation contract version: {result}",
    )
    structured = result.get("structured_result") or {}
    recommendations = structured.get("recommendations") or []
    require(structured.get("run_id") == payload.get("run_id"), "Run id is not canonical")
    require(result.get("recommendations") == recommendations, "Compatibility alias diverged")
    require(recommendations, f"No recommendations returned: {payload}")

    expect(page.get_by_role("heading", name="衣橱搭配方案")).to_be_visible()
    expect(page.locator(".outfit-card").first).to_be_visible()
    diagnostics = (payload.get("diagnostics") or {}).get("wardrobe_retrieval") or {}
    if diagnostics.get("calls", 0) > 0:
        expect(page.get_by_text("衣橱检索", exact=True)).to_be_visible()
        if diagnostics.get("semantic_used"):
            expect(page.get_by_text("语义检索正常", exact=True)).to_be_visible()
        else:
            expect(page.get_by_text("本次使用关键词检索", exact=True)).to_be_visible()

    stored = context.request.get(
        f"{API_ROOT}/tasks/{user_id}/{payload['run_id']}", timeout=30_000
    )
    require(stored.status == 200, f"Persisted task missing: HTTP {stored.status}")
    require(
        stored.json().get("diagnostics") == payload.get("diagnostics"),
        "Persisted diagnostics differ from the live response",
    )
    page.screenshot(path=str(OUTPUT_ROOT / "recommend-result.png"), full_page=True)
    return payload


def assert_diagnostics_rendering(context: BrowserContext, payload: dict) -> None:
    diagnostic_payload = json.loads(json.dumps(payload))
    result = diagnostic_payload.get("result") or {}
    recommendations = result.get("recommendations") or []
    result["structured_result"] = {"recommendations": recommendations}
    result.pop("recommendations", None)
    diagnostic_payload["result"] = result
    diagnostic_payload["diagnostics"] = {
        "wardrobe_retrieval": {
            "calls": 2,
            "modes": ["hybrid"],
            "total_duration_ms": 18.5,
            "degraded_calls": 0,
            "semantic_used": True,
        }
    }

    page = context.new_page()
    page.set_default_timeout(120_000)
    page.route(
        "**/api/tasks/execute",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(diagnostic_payload, ensure_ascii=False),
        ),
    )
    try:
        page.goto(f"{WEB_ROOT}/recommend", wait_until="networkidle")
        page.locator('textarea[placeholder^="例如"]').fill("验证检索诊断展示")
        page.get_by_role("button", name="让三位 Agent 处理").click()
        expect(page.get_by_text("衣橱检索", exact=True)).to_be_visible()
        expect(page.get_by_text("2 次 · 语义+关键词 · 18.5 ms", exact=True)).to_be_visible()
        expect(page.get_by_text("语义检索正常", exact=True)).to_be_visible()
        expect(page.locator(".outfit-card").first).to_be_visible()
        page.screenshot(path=str(OUTPUT_ROOT / "retrieval-diagnostics.png"), full_page=True)
    finally:
        page.close()


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    require(EDGE_PATH.is_file(), f"Edge executable not found: {EDGE_PATH}")
    user_id = os.getenv("STYLEFORGE_E2E_USER") or f"e2e-{uuid.uuid4().hex[:12]}"
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_responses: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=str(EDGE_PATH),
            args=["--disable-gpu"],
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.set_default_timeout(120_000)
        page.on("console", lambda message: record_console_error(console_errors, message))
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "response",
            lambda response: failed_responses.append(
                f"HTTP {response.status} {response.url}"
            )
            if response.status >= 400
            else None,
        )
        page.add_init_script(
            f"localStorage.setItem('sf_user_id', {json.dumps(user_id)});"
        )
        try:
            health = assert_health(context)
            item_ids = seed_wardrobe(context, user_id)
            wardrobe = context.request.get(f"{API_ROOT}/wardrobes/{user_id}").json()
            require(wardrobe.get("count") == len(item_ids), f"Wrong wardrobe count: {wardrobe}")
            assert_wardrobe_page(page, user_id)
            payload = execute_recommendation(page, context, user_id)
            assert_diagnostics_rendering(context, payload)
            require(not page_errors, f"Uncaught browser errors: {page_errors}")
            require(
                not console_errors,
                f"Browser console errors: {console_errors}; responses: {failed_responses}",
            )
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "user_id": user_id,
                        "seeded_items": len(item_ids),
                        "run_id": payload["run_id"],
                        "recommendations": len(
                            (payload["result"].get("structured_result") or {}).get(
                                "recommendations"
                            )
                            or payload["result"].get("recommendations")
                            or []
                        ),
                        "retrieval": (payload.get("diagnostics") or {}).get(
                            "wardrobe_retrieval", {}
                        ),
                        "database": health["database"],
                        "screenshots": [
                            str(OUTPUT_ROOT / "wardrobe.png"),
                            str(OUTPUT_ROOT / "recommend-result.png"),
                            str(OUTPUT_ROOT / "retrieval-diagnostics.png"),
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
