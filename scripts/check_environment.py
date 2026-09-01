"""Validate the local StyleForge runtime without mutating project data."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = WORKSPACE_ROOT / "apps" / "api"
EXPECTED_PYTHON = Path(r"D:\anaconda\envs\style\python.exe")
REQUIRED_MODULES = (
    "fastapi",
    "uvicorn",
    "psycopg",
    "langgraph",
    "pydantic",
    "mcp",
    "mcp_server_fetch",
    "mcp_server_time",
)


def build_report() -> dict[str, Any]:
    """Return a secret-free environment report."""
    sys.path.insert(0, str(API_ROOT))
    from styleforge.core.config import Settings
    from styleforge.repositories.database import connect

    checks: list[dict[str, Any]] = []
    actual_python = Path(sys.executable).resolve()
    checks.append(
        {
            "name": "python_runtime",
            "ok": actual_python == EXPECTED_PYTHON.resolve(),
            "detail": str(actual_python),
        }
    )
    for module in REQUIRED_MODULES:
        checks.append(
            {
                "name": f"module:{module}",
                "ok": importlib.util.find_spec(module) is not None,
                "detail": "installed" if importlib.util.find_spec(module) else "missing",
            }
        )

    settings = Settings.from_env()
    database_ok = False
    database_detail = "STYLEFORGE_DATABASE_DSN is not configured"
    if settings.database_dsn:
        try:
            connection = connect(settings.database_dsn)
            try:
                connection.execute("SELECT 1").fetchone()
                database_ok = True
                database_detail = "postgresql connection succeeded"
            finally:
                connection.close()
        except Exception as error:  # pragma: no cover - depends on local service
            database_detail = f"postgresql connection failed: {type(error).__name__}"
    checks.append({"name": "database", "ok": database_ok, "detail": database_detail})

    image_root = settings.image_root
    checks.append(
        {
            "name": "image_root",
            "ok": image_root is not None and image_root.is_dir(),
            "required": False,
            "detail": "not configured" if image_root is None else str(image_root),
        }
    )
    required_ok = all(check["ok"] for check in checks if check.get("required", True))
    return {
        "status": "ok" if required_ok else "error",
        "workspace": str(WORKSPACE_ROOT),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()
    report = build_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for check in report["checks"]:
            marker = "OK" if check["ok"] else ("WARN" if not check.get("required", True) else "FAIL")
            print(f"[{marker}] {check['name']}: {check['detail']}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
