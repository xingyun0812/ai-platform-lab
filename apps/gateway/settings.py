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
    llm_secret_ref: str = Field(default="", validation_alias="LLM_SECRET_REF")
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
    rag_retrieval_mode: str = Field(default="vector", validation_alias="RAG_RETRIEVAL_MODE")
    rag_bm25_top_k: int = Field(default=20, validation_alias="RAG_BM25_TOP_K")
    rag_hybrid_rrf_k: int = Field(default=60, validation_alias="RAG_HYBRID_RRF_K")
    rag_rerank_enabled: bool = Field(default=False, validation_alias="RAG_RERANK_ENABLED")
    rag_rerank_mode: str = Field(default="stub", validation_alias="RAG_RERANK_MODE")
    rag_rerank_top_n: int = Field(default=10, validation_alias="RAG_RERANK_TOP_N")

    # Agent（第 4 周）
    agent_max_steps: int = Field(default=8, validation_alias="AGENT_MAX_STEPS")
    agent_tool_timeout_seconds: float = Field(
        default=10.0,
        validation_alias="AGENT_TOOL_TIMEOUT_SECONDS",
    )
    agent_tool_max_retries: int = Field(default=1, validation_alias="AGENT_TOOL_MAX_RETRIES")
    agent_model: str | None = Field(default=None, validation_alias="AGENT_MODEL")
    agent_tool_routing_enabled: bool = Field(
        default=True,
        validation_alias="AGENT_TOOL_ROUTING_ENABLED",
    )
    agent_tool_rag_enabled: bool = Field(
        default=False,
        validation_alias="AGENT_TOOL_RAG_ENABLED",
    )
    agent_context_token_budget: int = Field(
        default=8000,
        validation_alias="AGENT_CONTEXT_TOKEN_BUDGET",
    )
    agent_summary_every_n_turns: int = Field(
        default=6,
        validation_alias="AGENT_SUMMARY_EVERY_N_TURNS",
    )
    agent_context_keep_recent_turns: int = Field(
        default=4,
        validation_alias="AGENT_CONTEXT_KEEP_RECENT_TURNS",
    )
    agent_tool_result_max_chars: int = Field(
        default=2000,
        validation_alias="AGENT_TOOL_RESULT_MAX_CHARS",
    )
    agent_quality_min_score: float = Field(
        default=0.3,
        validation_alias="AGENT_QUALITY_MIN_SCORE",
    )
    agent_reflect_max_retries: int = Field(
        default=2,
        validation_alias="AGENT_REFLECT_MAX_RETRIES",
    )

    # 观测（第 5 周）
    otel_enabled: bool = Field(default=False, validation_alias="OTEL_ENABLED")
    otel_console_export: bool = Field(default=True, validation_alias="OTEL_CONSOLE_EXPORT")
    otel_exporter_otlp_endpoint: str = Field(
        default="",
        validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT",
    )
    metrics_enabled: bool = Field(default=True, validation_alias="METRICS_ENABLED")

    # 硬化（第 6 周）
    models_config_path: Path = Field(
        default=REPO_ROOT / "config" / "models.yaml",
        validation_alias="MODELS_CONFIG_PATH",
    )
    default_rate_limit_rps: float = Field(default=20.0, validation_alias="DEFAULT_RATE_LIMIT_RPS")
    default_rate_limit_burst: int = Field(default=40, validation_alias="DEFAULT_RATE_LIMIT_BURST")

    # Phase A — 可内测
    redis_url: str = Field(default="", validation_alias="REDIS_URL")
    use_index_worker: bool = Field(default=False, validation_alias="USE_INDEX_WORKER")
    index_queue_name: str = Field(
        default="ai_platform:index_queue",
        validation_alias="INDEX_QUEUE_NAME",
    )
    audit_enabled: bool = Field(default=True, validation_alias="AUDIT_ENABLED")
    audit_db_path: Path = Field(
        default=REPO_ROOT / "data" / "audit.db",
        validation_alias="AUDIT_DB_PATH",
    )

    # Phase B — 小流量生产（计费）
    database_url: str = Field(default="", validation_alias="DATABASE_URL")

    # Phase B2 — 密钥 / 混合检索 / 可观测栈
    secrets_provider: str = Field(default="env", validation_alias="SECRETS_PROVIDER")
    vault_addr: str = Field(default="", validation_alias="VAULT_ADDR")
    vault_token: str = Field(default="", validation_alias="VAULT_TOKEN")
    vault_mount: str = Field(default="secret", validation_alias="VAULT_MOUNT")

    # Phase D — 身份与运维
    auth_jwt_enabled: bool = Field(default=False, validation_alias="AUTH_JWT_ENABLED")
    auth_jwt_secret: str = Field(default="", validation_alias="AUTH_JWT_SECRET")
    audit_postgres_enabled: bool = Field(default=True, validation_alias="AUDIT_POSTGRES_ENABLED")
    circuit_breaker_threshold: int = Field(default=5, validation_alias="CIRCUIT_BREAKER_THRESHOLD")
    canary_auto_rollback_min_pass_rate: float = Field(
        default=0.85,
        validation_alias="CANARY_AUTO_ROLLBACK_MIN_PASS_RATE",
    )


def _load_yaml_defaults(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


@lru_cache
def get_settings() -> Settings:
    rag_defaults = _load_yaml_defaults(REPO_ROOT / "config" / "rag.yaml")
    agent_defaults = _load_yaml_defaults(REPO_ROOT / "config" / "agent.yaml")
    routing_defaults = _load_yaml_defaults(REPO_ROOT / "config" / "agent_tool_routing.yaml")
    obs_defaults = _load_yaml_defaults(REPO_ROOT / "config" / "observability.yaml")
    overrides: dict[str, Any] = {}
    if isinstance(rag_defaults.get("chunk_size"), int):
        overrides["chunk_size"] = rag_defaults["chunk_size"]
    if isinstance(rag_defaults.get("chunk_overlap"), int):
        overrides["chunk_overlap"] = rag_defaults["chunk_overlap"]
    if isinstance(rag_defaults.get("min_score"), (int, float)):
        overrides["rag_min_score"] = float(rag_defaults["min_score"])
    if isinstance(rag_defaults.get("prompt_path"), str):
        overrides["rag_prompt_path"] = REPO_ROOT / rag_defaults["prompt_path"]
    if isinstance(agent_defaults.get("max_steps"), int):
        overrides["agent_max_steps"] = agent_defaults["max_steps"]
    if isinstance(agent_defaults.get("tool_timeout_seconds"), (int, float)):
        overrides["agent_tool_timeout_seconds"] = float(agent_defaults["tool_timeout_seconds"])
    if isinstance(agent_defaults.get("tool_max_retries"), int):
        overrides["agent_tool_max_retries"] = agent_defaults["tool_max_retries"]
    if isinstance(agent_defaults.get("agent_model"), str):
        overrides["agent_model"] = agent_defaults["agent_model"]
    if isinstance(routing_defaults.get("enabled"), bool):
        overrides["agent_tool_routing_enabled"] = routing_defaults["enabled"]
    if isinstance(agent_defaults.get("context_token_budget"), int):
        overrides["agent_context_token_budget"] = agent_defaults["context_token_budget"]
    if isinstance(agent_defaults.get("summary_every_n_turns"), int):
        overrides["agent_summary_every_n_turns"] = agent_defaults["summary_every_n_turns"]
    if isinstance(agent_defaults.get("context_keep_recent_turns"), int):
        overrides["agent_context_keep_recent_turns"] = agent_defaults["context_keep_recent_turns"]
    if isinstance(agent_defaults.get("tool_result_max_chars"), int):
        overrides["agent_tool_result_max_chars"] = agent_defaults["tool_result_max_chars"]
    if isinstance(agent_defaults.get("quality_min_score"), (int, float)):
        overrides["agent_quality_min_score"] = float(agent_defaults["quality_min_score"])
    if isinstance(agent_defaults.get("reflect_max_retries"), int):
        overrides["agent_reflect_max_retries"] = agent_defaults["reflect_max_retries"]
    if isinstance(obs_defaults.get("otel_enabled"), bool):
        overrides["otel_enabled"] = obs_defaults["otel_enabled"]
    if isinstance(obs_defaults.get("otel_console_export"), bool):
        overrides["otel_console_export"] = obs_defaults["otel_console_export"]
    if isinstance(obs_defaults.get("metrics_enabled"), bool):
        overrides["metrics_enabled"] = obs_defaults["metrics_enabled"]
    if isinstance(obs_defaults.get("otel_exporter_otlp_endpoint"), str):
        overrides["otel_exporter_otlp_endpoint"] = obs_defaults["otel_exporter_otlp_endpoint"]
    if isinstance(rag_defaults.get("retrieval_mode"), str):
        overrides["rag_retrieval_mode"] = rag_defaults["retrieval_mode"]
    if isinstance(rag_defaults.get("bm25_top_k"), int):
        overrides["rag_bm25_top_k"] = rag_defaults["bm25_top_k"]
    if isinstance(rag_defaults.get("hybrid_rrf_k"), int):
        overrides["rag_hybrid_rrf_k"] = rag_defaults["hybrid_rrf_k"]
    if isinstance(rag_defaults.get("rerank_enabled"), bool):
        overrides["rag_rerank_enabled"] = rag_defaults["rerank_enabled"]
    if isinstance(rag_defaults.get("rerank_mode"), str):
        overrides["rag_rerank_mode"] = rag_defaults["rerank_mode"]
    if isinstance(rag_defaults.get("rerank_top_n"), int):
        overrides["rag_rerank_top_n"] = rag_defaults["rerank_top_n"]

    settings = Settings(**overrides)
    if settings.llm_secret_ref:
        try:
            from packages.secrets.provider import resolve_secret

            key = resolve_secret(settings.llm_secret_ref, fallback=settings.llm_api_key)
            settings = settings.model_copy(update={"llm_api_key": key})
        except Exception:
            pass
    return settings
