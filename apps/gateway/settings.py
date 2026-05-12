from functools import lru_cache
from pathlib import Path

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
