"""Read-only browser E2E for the default wardrobe and MCP health panel."""

from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = Path(
    os.getenv(
        "STYLEFORGE_E2E_OUTPUT_ROOT",
        WORKSPACE_ROOT / "artifacts" / "e2e-runtime" / "mcp",
    )
).resolve()
WEB_ROOT = os.getenv("STYLEFORGE_E2E_WEB_ROOT", "http://127.0.0.1:15173").rstrip("/")
EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    require(EDGE_PATH.is_file(), f"Edge executable not found: {EDGE_PATH}")
    console_errors: list[str] = []
    page_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=str(EDGE_PATH),
            args=["--disable-gpu"],
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.set_default_timeout(120_000)
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.add_init_script("localStorage.setItem('sf_user_id', 'demo-user');")
        try:
            with page.expect_response(
                lambda response: response.url.endswith("/api/wardrobes/demo-user"),
                timeout=120_000,
            ) as wardrobe_response:
                page.goto(f"{WEB_ROOT}/wardrobe", wait_until="networkidle")
            wardrobe = wardrobe_response.value
            require(wardrobe.status == 200, f"Wardrobe HTTP {wardrobe.status}")
            wardrobe_payload = wardrobe.json()
            require(
                wardrobe_payload.get("count") == 2080,
                f"Expected demo wardrobe 2080, got {wardrobe_payload.get('count')}",
            )
            expect(page.get_by_text("我的衣柜", exact=True)).to_be_visible()
            expect(page.locator('input[placeholder="用户 ID"]')).to_have_value("demo-user")
            expect(page.get_by_text("衣柜为空", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="全部展开")).to_be_visible()
            page.screenshot(path=str(OUTPUT_ROOT / "wardrobe-demo-user.png"), full_page=True)

            with page.expect_response(
                lambda response: response.url.endswith("/api/health"),
                timeout=60_000,
            ) as health_response:
                page.goto(f"{WEB_ROOT}/health", wait_until="networkidle")
            health = health_response.value
            require(health.status == 200, f"Health HTTP {health.status}")
            health_payload = health.json()
            mcp = health_payload.get("mcp") or {}
            servers = (mcp.get("client") or {}).get("servers") or []
            names = {server.get("name") for server in servers}
            require(
                {"official-fetch", "official-time"}.issubset(names),
                f"Missing official MCP servers: {names}",
            )
            require((mcp.get("server") or {}).get("enabled") is True, "MCP server disabled")
            require((mcp.get("client") or {}).get("successful_calls", 0) > 0, "No MCP calls")
            expect(page.get_by_role("heading", name="系统脉搏")).to_be_visible()
            expect(page.get_by_role("heading", name="MCP 接入状态")).to_be_visible()
            expect(page.get_by_text("styleforge_mcp", exact=True)).to_be_visible()
            expect(page.get_by_text("official-fetch", exact=True)).to_be_visible()
            expect(page.get_by_text("official-time", exact=True)).to_be_visible()
            page.screenshot(path=str(OUTPUT_ROOT / "mcp-health.png"), full_page=True)

            require(not page_errors, f"Uncaught browser errors: {page_errors}")
            require(not console_errors, f"Browser console errors: {console_errors}")
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "wardrobe_count": wardrobe_payload["count"],
                        "mcp_calls": (mcp.get("client") or {}).get("calls", 0),
                        "mcp_successful_calls": (
                            (mcp.get("client") or {}).get("successful_calls", 0)
                        ),
                        "mcp_servers": sorted(names),
                        "styleforge_tools": len((mcp.get("server") or {}).get("tools") or []),
                        "screenshots": [
                            str(OUTPUT_ROOT / "wardrobe-demo-user.png"),
                            str(OUTPUT_ROOT / "mcp-health.png"),
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
