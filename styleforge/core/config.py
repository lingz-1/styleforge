"""Runtime configuration with explicit, local-first defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]

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
    database_path: Path
    artifact_root: Path
    embedding_dir: Path
    index_dir: Path
    dataset_revision: str
    llm_enabled: bool = False
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_timeout: float = 60.0
    deepseek_max_retries: int = 2

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
            database_path=Path(
                os.getenv("STYLEFORGE_DATABASE_PATH", WORKSPACE_ROOT / "data" / "styleforge.db")
            ).resolve(),
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
            llm_enabled=_optional_bool(os.getenv("STYLEFORGE_LLM_ENABLED"))
            or bool(api_key),
            deepseek_api_key=api_key,
            deepseek_base_url=os.getenv(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
            ).strip(),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip(),
            deepseek_timeout=timeout,
            deepseek_max_retries=max_retries,
        )
