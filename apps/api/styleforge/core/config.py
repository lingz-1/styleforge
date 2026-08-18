"""Runtime configuration with explicit, local-first defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _find_workspace_root(start: Path) -> Path:
    """Walk up to the first directory containing pyproject.toml (the repo root)."""
    current = start
    for _ in range(8):
        if (current / "pyproject.toml").is_file():
            return current
        current = current.parent
    raise RuntimeError(f"Could not locate workspace root from {start}")


WORKSPACE_ROOT = _find_workspace_root(Path(__file__).resolve().parent)

# Load a project-local .env if present (DEEPSEEK_* etc.). Existing process
# environment variables take precedence (override=False).
load_dotenv(WORKSPACE_ROOT / ".env")


def _optional_path(value: str | None) -> Path | None:
    if value is None or not value.strip():
        return None
    return Path(value).expanduser().resolve()


def _optional_bool(value: str | None, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    metadata_path: Path
    outfit_path: Path
    image_root: Path | None
    artifact_root: Path
    embedding_dir: Path
    index_dir: Path
    dataset_revision: str
    knowledge_root: Path
    # PostgreSQL DSN (STYLEFORGE_DATABASE_DSN); the SQLite file path is retired.
    database_dsn: str = ""
    # Redis cache for session-level outfit context (optional external service).
    redis_enabled: bool = False
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_ttl: int = 86400
    # Chroma RAG persistent directory (knowledge text embeddings).
    chroma_dir: Path = Path("artifacts/chroma")
    llm_enabled: bool = False
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_timeout: float = 60.0
    deepseek_max_retries: int = 2
    weather_enabled: bool = True
    weather_provider: str = "open-meteo"
    weather_default_location: str = ""
    weather_timeout: float = 10.0
    location_max_age_seconds: int = 1800
    location_max_accuracy_m: float = 5000.0
    reverse_geocode_endpoint: str = ""
    # Multi-modal clothing-image recognition via the OpenAI-compatible
    # gemini_proxy (see tools/gemini_proxy.py). The proxy speaks plain
    # /v1/chat/completions, so the base URL can point at any compatible
    # endpoint (Gemini proxy, local LLM, etc.).
    vision_enabled: bool = True
    vision_base_url: str = "http://127.0.0.1:5088/v1"
    vision_api_key: str = ""
    vision_model: str = "gemini-2.5-flash"
    vision_timeout: float = 120.0
    vision_max_retries: int = 2
    # Web search for the agentic loop (search_web / Tavily). Optional: without
    # a key the tool degrades to an "unconfigured" observation and the loop
    # keeps working.
    tavily_api_key: str = ""
    web_search_enabled: bool = True
    web_search_timeout: float = 10.0
    web_search_max_results: int = 5

    @classmethod
    def from_env(cls) -> "Settings":
        artifact_root = Path(
            os.getenv("STYLEFORGE_ARTIFACT_ROOT", WORKSPACE_ROOT / "artifacts")
        ).resolve()
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        try:
            timeout = float(os.getenv("DEEPSEEK_TIMEOUT", "60"))
        except ValueError:
            timeout = 60.0
        try:
            max_retries = int(os.getenv("DEEPSEEK_MAX_RETRIES", "2"))
        except ValueError:
            max_retries = 2
        try:
            weather_timeout = float(os.getenv("STYLEFORGE_WEATHER_TIMEOUT", "10"))
        except ValueError:
            weather_timeout = 10.0
        try:
            location_max_age = int(
                os.getenv("STYLEFORGE_LOCATION_MAX_AGE_SECONDS", "1800")
            )
        except ValueError:
            location_max_age = 1800
        try:
            location_max_accuracy = float(
                os.getenv("STYLEFORGE_LOCATION_MAX_ACCURACY_M", "5000")
            )
        except ValueError:
            location_max_accuracy = 5000.0
        try:
            vision_timeout = float(os.getenv("STYLEFORGE_VISION_TIMEOUT", "120"))
        except ValueError:
            vision_timeout = 120.0
        try:
            vision_max_retries = int(os.getenv("STYLEFORGE_VISION_MAX_RETRIES", "2"))
        except ValueError:
            vision_max_retries = 2
        try:
            web_search_timeout = float(os.getenv("STYLEFORGE_WEB_SEARCH_TIMEOUT", "10"))
        except ValueError:
            web_search_timeout = 10.0
        try:
            web_search_max_results = int(
                os.getenv("STYLEFORGE_WEB_SEARCH_MAX_RESULTS", "5")
            )
        except ValueError:
            web_search_max_results = 5
        return cls(
            metadata_path=Path(
                os.getenv(
                    "GARMENTS2LOOK_METADATA_PATH",
                    WORKSPACE_ROOT / "polyvore_image_v1.0_2512.json",
                )
            ).resolve(),
            outfit_path=Path(
                os.getenv(
                    "GARMENTS2LOOK_OUTFIT_PATH",
                    WORKSPACE_ROOT / "polyvore_outfit_v1.0_2512.json",
                )
            ).resolve(),
            image_root=_optional_path(os.getenv("GARMENTS2LOOK_IMAGE_ROOT")),
            artifact_root=artifact_root,
            embedding_dir=Path(
                os.getenv(
                    "STYLEFORGE_EMBEDDING_DIR",
                    artifact_root / "embeddings" / "fashionclip",
                )
            ).resolve(),
            index_dir=Path(
                os.getenv(
                    "STYLEFORGE_INDEX_DIR",
                    artifact_root / "index" / "fashionclip",
                )
            ).resolve(),
            dataset_revision=os.getenv(
                "GARMENTS2LOOK_REVISION", "5324b92e86beefa27196116c6d3957fcc6242205"
            ),
            knowledge_root=Path(
                os.getenv("STYLEFORGE_KNOWLEDGE_ROOT", WORKSPACE_ROOT / "knowledge")
            ).resolve(),
            database_dsn=os.getenv("STYLEFORGE_DATABASE_DSN", "").strip(),
            redis_enabled=_optional_bool(os.getenv("STYLEFORGE_REDIS_ENABLED")),
            redis_url=os.getenv("STYLEFORGE_REDIS_URL", "redis://127.0.0.1:6379/0").strip(),
            redis_ttl=int(os.getenv("STYLEFORGE_REDIS_TTL", "86400") or 86400),
            chroma_dir=Path(
                os.getenv(
                    "STYLEFORGE_CHROMA_DIR", artifact_root / "chroma"
                )
            ).resolve(),
            llm_enabled=_optional_bool(os.getenv("STYLEFORGE_LLM_ENABLED"))
            or bool(api_key),
            deepseek_api_key=api_key,
            deepseek_base_url=os.getenv(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
            ).strip(),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip(),
            deepseek_timeout=timeout,
            deepseek_max_retries=max_retries,
            weather_enabled=_optional_bool(
                os.getenv("STYLEFORGE_WEATHER_ENABLED"), default=True
            ),
            weather_provider=os.getenv(
                "STYLEFORGE_WEATHER_PROVIDER", "open-meteo"
            ).strip(),
            weather_default_location=os.getenv(
                "STYLEFORGE_DEFAULT_LOCATION", ""
            ).strip(),
            weather_timeout=weather_timeout,
            location_max_age_seconds=location_max_age,
            location_max_accuracy_m=location_max_accuracy,
            reverse_geocode_endpoint=os.getenv(
                "STYLEFORGE_REVERSE_GEOCODE_ENDPOINT", ""
            ).strip(),
            vision_enabled=_optional_bool(
                os.getenv("STYLEFORGE_VISION_ENABLED"), default=True
            ),
            vision_base_url=os.getenv(
                "STYLEFORGE_VISION_BASE_URL", "http://127.0.0.1:5088/v1"
            ).strip(),
            vision_api_key=os.getenv("STYLEFORGE_VISION_API_KEY", "").strip(),
            vision_model=os.getenv("STYLEFORGE_VISION_MODEL", "gemini-2.5-flash").strip(),
            vision_timeout=vision_timeout,
            vision_max_retries=vision_max_retries,
            tavily_api_key=os.getenv("TAVILY_API_KEY", "").strip(),
            web_search_enabled=_optional_bool(
                os.getenv("STYLEFORGE_WEB_SEARCH_ENABLED"), default=True
            ),
            web_search_timeout=web_search_timeout,
            web_search_max_results=web_search_max_results,
        )
