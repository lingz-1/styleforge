"""Start an isolated StyleForge API instance for browser smoke tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = WORKSPACE_ROOT / "apps" / "api"
E2E_ARTIFACT_ROOT = Path(
    os.getenv(
        "STYLEFORGE_E2E_ARTIFACT_ROOT",
        WORKSPACE_ROOT / "artifacts" / "e2e-runtime",
    )
).resolve()

# Set all overrides before importing the application settings module.
os.environ["STYLEFORGE_DATABASE_DSN"] = os.getenv(
    "STYLEFORGE_E2E_DATABASE_DSN",
    "postgresql://styleforge@127.0.0.1:5432/styleforge_test",
)
os.environ["STYLEFORGE_ARTIFACT_ROOT"] = str(E2E_ARTIFACT_ROOT)
os.environ["STYLEFORGE_REDIS_ENABLED"] = "false"
os.environ["STYLEFORGE_LLM_ENABLED"] = "false"
os.environ["STYLEFORGE_WEATHER_ENABLED"] = "false"
os.environ["STYLEFORGE_VISION_ENABLED"] = "false"
os.environ["STYLEFORGE_WEB_SEARCH_ENABLED"] = "false"
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["STYLEFORGE_VISION_API_KEY"] = ""
os.environ["TAVILY_API_KEY"] = ""

sys.path.insert(0, str(API_ROOT))

import uvicorn  # noqa: E402


if __name__ == "__main__":
    E2E_ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        "styleforge.api:app",
        app_dir=str(API_ROOT),
        host="127.0.0.1",
        port=int(os.getenv("STYLEFORGE_E2E_API_PORT", "18000")),
        log_level="warning",
    )
