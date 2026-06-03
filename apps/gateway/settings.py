from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ai-platform-lab"
    app_version: str = Field(default="0.1.0", validation_alias="APP_VERSION")

    llm_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias="LLM_BASE_URL",
        description="OpenAI 兼容 API 根，勿带末尾路径 /chat/completions",
    )
    llm_api_key: str = Field(default="", validation_alias="LLM_API_KEY")
    default_model: str = Field(default="gpt-4o-mini", validation_alias="DEFAULT_MODEL")

    tenants_config_path: Path = Field(
        default=REPO_ROOT / "config" / "tenants.yaml",
        validation_alias="TENANTS_CONFIG_PATH",
    )

    upstream_timeout_seconds: float = Field(default=60.0, validation_alias="UPSTREAM_TIMEOUT_SECONDS")
    upstream_max_retries: int = Field(default=2, validation_alias="UPSTREAM_MAX_RETRIES")

    # RAG（第 2 周）
    qdrant_url: str = Field(default="http://127.0.0.1:6333", validation_alias="QDRANT_URL")
    qdrant_collection: str = Field(
        default="ai_platform_lab",
        validation_alias="QDRANT_COLLECTION",
    )
    embedding_model: str = Field(
        default="text-embedding-3-small",
        validation_alias="EMBEDDING_MODEL",
    )
    embedding_dimensions: int = Field(default=1536, validation_alias="EMBEDDING_DIMENSIONS")
    rag_data_root: Path = Field(
        default=REPO_ROOT / "data" / "rag",
        validation_alias="RAG_DATA_ROOT",
    )
    rag_config_path: Path = Field(
        default=REPO_ROOT / "config" / "rag.yaml",
        validation_alias="RAG_CONFIG_PATH",
    )
    chunk_size: int = Field(default=512, validation_alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=64, validation_alias="CHUNK_OVERLAP")
    embedding_batch_size: int = Field(default=32, validation_alias="EMBEDDING_BATCH_SIZE")

    # RAG 问答（第 3 周）
    rag_min_score: float = Field(default=0.35, validation_alias="RAG_MIN_SCORE")
    rag_prompt_path: Path = Field(
        default=REPO_ROOT / "config" / "rag_prompt.txt",
        validation_alias="RAG_PROMPT_PATH",
    )
    rag_query_model: str | None = Field(default=None, validation_alias="RAG_QUERY_MODEL")


def _load_rag_yaml_defaults(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


@lru_cache
def get_settings() -> Settings:
    rag_defaults = _load_rag_yaml_defaults(REPO_ROOT / "config" / "rag.yaml")
    overrides: dict[str, Any] = {}
    if isinstance(rag_defaults.get("chunk_size"), int):
        overrides["chunk_size"] = rag_defaults["chunk_size"]
    if isinstance(rag_defaults.get("chunk_overlap"), int):
        overrides["chunk_overlap"] = rag_defaults["chunk_overlap"]
    if isinstance(rag_defaults.get("min_score"), (int, float)):
        overrides["rag_min_score"] = float(rag_defaults["min_score"])
    if isinstance(rag_defaults.get("prompt_path"), str):
        overrides["rag_prompt_path"] = REPO_ROOT / rag_defaults["prompt_path"]
    return Settings(**overrides)
