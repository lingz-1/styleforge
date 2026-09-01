"""Browser regression for result-count inference and timeout recovery."""

from __future__ import annotations

import json
from pathlib import Path
import time

from playwright.sync_api import Route, sync_playwright


submitted: dict[str, object] = {}
task_finished = False
EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def json_response(route: Route, payload: dict[str, object], status: int = 200) -> None:
    route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False),
    )


def handle_api(route: Route) -> None:
    global task_finished
    request = route.request
    path = request.url.split("/api", 1)[-1]
    if path.startswith("/users/demo-user/chat-sessions") and request.method == "GET":
        json_response(route, {"user_id": "demo-user", "count": 0, "sessions": []})
        return
    if path.startswith("/users/demo-user/chat-sessions") and request.method == "POST":
        json_response(route, {"session_id": "session-e2e", "title": "测试会话"}, 201)
        return
    if path.startswith("/chat-sessions/session-e2e"):
        messages: list[dict[str, object]] = [
            {"message_id": "u1", "role": "user", "content": "推荐一套演唱会穿搭"}
        ]
        if task_finished:
            messages.append(
                {
                    "message_id": "a1",
                    "role": "assistant",
                    "content": "后台任务已完成并恢复显示",
                    "result": {
                        "run_id": "run-e2e",
                        "task_type": "outfit_recommend",
                        "status": "completed",
                        "llm_call_count": 9,
                        "result": {
                            "status": "completed",
                            "message": "后台任务已完成并恢复显示",
                            "alternatives": [],
                        },
                        "trace": [],
                        "diagnostics": {},
                    },
                }
            )
        json_response(
            route,
            {
                "session_id": "session-e2e",
                "user_id": "demo-user",
                "title": "测试会话",
                "messages": messages,
            },
        )
        return
    if path.startswith("/tasks/execute"):
        submitted.update(request.post_data_json)
        # Longer than VITE_TASK_TIMEOUT_MS used by this E2E server.
        time.sleep(0.8)
        task_finished = True
        try:
            json_response(route, {"status": "completed"})
        except Exception:
            pass
        return
    if path.startswith("/weather/now"):
        json_response(route, {"status": "unavailable"})
        return
    json_response(route, {})


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        headless=True,
        executable_path=str(EDGE_PATH),
    )
    page = browser.new_page()
    page.route("**/api/**", handle_api)
    page.goto("http://127.0.0.1:15174/recommend")
    page.wait_for_load_state("networkidle")
    prompt = page.locator("textarea")
    prompt.fill("推荐一套演唱会穿搭")
    page.get_by_role("button", name="让三位 Agent 处理").click()
    page.get_by_text("连接已超时，但任务仍在后台执行，正在恢复结果").wait_for(
        timeout=5000
    )
    page.get_by_text("后台任务已完成并恢复显示", exact=True).first.wait_for(timeout=7000)
    assert submitted["max_results"] == 1, submitted
    assert page.locator(".chat-bubble.assistant.failed").count() == 0
    browser.close()

print("recommend timeout recovery e2e: ok")
