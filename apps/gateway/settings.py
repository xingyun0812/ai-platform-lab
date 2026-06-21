from __future__ import annotations

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

    # Phase G — 语义缓存 (#34)
    semantic_cache_enabled: bool = Field(
        default=False,
        validation_alias="SEMANTIC_CACHE_ENABLED",
    )
    semantic_cache_mode: str = Field(
        default="semantic",
        validation_alias="SEMANTIC_CACHE_MODE",
        description="exact=SHA256 精确匹配（无 embedding 也可用）；semantic=embedding 余弦相似度",
    )
    semantic_cache_similarity_threshold: float = Field(
        default=0.92,
        validation_alias="SEMANTIC_CACHE_SIMILARITY_THRESHOLD",
    )
    semantic_cache_ttl_seconds: int = Field(
        default=3600,
        validation_alias="SEMANTIC_CACHE_TTL_SECONDS",
    )
    semantic_cache_max_entries_per_tenant: int = Field(
        default=256,
        validation_alias="SEMANTIC_CACHE_MAX_ENTRIES_PER_TENANT",
    )
    semantic_cache_skip_models: str = Field(
        default="",
        validation_alias="SEMANTIC_CACHE_SKIP_MODELS",
        description="逗号分隔模型名，这些模型跳过缓存（如 o1,reasoning-*）",
    )
    semantic_cache_max_temperature: float = Field(
        default=0.3,
        validation_alias="SEMANTIC_CACHE_MAX_TEMPERATURE",
        description="temperature 高于此值的请求跳过缓存（非确定性生成）",
    )

    # Phase F — Prompt 版本化 (#29)
    prompt_registry_enabled: bool = Field(
        default=True,
        validation_alias="PROMPT_REGISTRY_ENABLED",
        description="启用后 RAG/Agent 优先从 registry 取 prompt；关闭则回退 legacy txt",
    )
    prompts_config_path: Path = Field(
        default=REPO_ROOT / "config" / "prompts.yaml",
        validation_alias="PROMPTS_CONFIG_PATH",
        description="Prompt 版本 YAML 默认（git 跟踪）",
    )
    prompt_overrides_path: Path = Field(
        default=REPO_ROOT / "data" / "prompt_overrides.json",
        validation_alias="PROMPT_OVERRIDES_PATH",
        description="Prompt 运行时 overrides（admin API 写入，不进 git）",
    )

    # Phase F — Prompt A/B 实验 (#30)
    prompt_experiment_enabled: bool = Field(
        default=True,
        validation_alias="PROMPT_EXPERIMENT_ENABLED",
        description="启用后 RAG query 时按实验分桶；关闭则始终用 active 版本",
    )
    prompt_experiments_path: Path = Field(
        default=REPO_ROOT / "data" / "prompt_experiments.json",
        validation_alias="PROMPT_EXPERIMENTS_PATH",
        description="A/B 实验存储（JSON，不进 git）",
    )
    prompt_experiment_default_min_samples: int = Field(
        default=100,
        validation_alias="PROMPT_EXPERIMENT_DEFAULT_MIN_SAMPLES",
        description="自动胜出所需最小样本数（每 variant）",
    )
    prompt_experiment_default_margin: float = Field(
        default=0.1,
        validation_alias="PROMPT_EXPERIMENT_DEFAULT_MARGIN",
        description="自动胜出相对改进阈值（10%）",
    )

    # Phase F — 长记忆 (#31)
    memory_store_enabled: bool = Field(
        default=True,
        validation_alias="MEMORY_STORE_ENABLED",
        description="启用后 Agent 自动 summarize + 持久化到 Postgres / 进程内兜底",
    )
    agent_memory_model: str | None = Field(
        default=None,
        validation_alias="AGENT_MEMORY_MODEL",
        description="长记忆摘要使用的模型；未配置则回退到 default_model",
    )
    memory_summarize_every_n_turns: int = Field(
        default=8,
        validation_alias="MEMORY_SUMMARIZE_EVERY_N_TURNS",
        description="每 N 轮自动触发一次记忆摘要",
    )
    memory_default_ttl_seconds: int = Field(
        default=0,
        validation_alias="MEMORY_DEFAULT_TTL_SECONDS",
        description="默认 TTL；0 表示不过期",
    )
    memory_search_top_k: int = Field(
        default=5,
        validation_alias="MEMORY_SEARCH_TOP_K",
        description="检索返回 top_k 条",
    )

    # Phase F — 上下文压缩 (#33)
    context_llm_summary_enabled: bool = Field(
        default=True,
        validation_alias="CONTEXT_LLM_SUMMARY_ENABLED",
        description="启用后 maybe_compact 用 LLM 替换 stub_summarize；关闭则保持 stub 行为",
    )
    context_memory_injection_enabled: bool = Field(
        default=True,
        validation_alias="CONTEXT_MEMORY_INJECTION_ENABLED",
        description="启用后 Agent 启动时检索长记忆注入 system prompt",
    )
    context_memory_injection_top_k: int = Field(
        default=3,
        validation_alias="CONTEXT_MEMORY_INJECTION_TOP_K",
        description="注入记忆条数上限",
    )
    context_memory_injection_min_budget: int = Field(
        default=500,
        validation_alias="CONTEXT_MEMORY_INJECTION_MIN_BUDGET",
        description="剩余 Token 预算低于此值时跳过注入",
    )

    # Phase F — MCP 真实集成 (#32)
    mcp_enabled: bool = Field(
        default=True,
        validation_alias="MCP_ENABLED",
        description="启用后 Agent 动态加载 MCP server 工具；关闭则仅用内置工具",
    )
    mcp_servers_config_path: Path = Field(
        default=REPO_ROOT / "config" / "mcp_servers.yaml",
        validation_alias="MCP_SERVERS_CONFIG_PATH",
        description="MCP server YAML 配置（git 跟踪）",
    )
    mcp_overrides_path: Path = Field(
        default=REPO_ROOT / "data" / "mcp_servers_overrides.json",
        validation_alias="MCP_OVERRIDES_PATH",
        description="MCP server 运行时 overrides（admin API 写入，不进 git）",
    )
    mcp_connect_timeout_seconds: float = Field(
        default=5.0,
        validation_alias="MCP_CONNECT_TIMEOUT_SECONDS",
        description="MCP server 连接超时",
    )
    mcp_tool_call_timeout_seconds: float = Field(
        default=30.0,
        validation_alias="MCP_TOOL_CALL_TIMEOUT_SECONDS",
        description="MCP 工具调用超时",
    )

    # Phase H — 控制流编排引擎 (#37)
    orchestrator_enabled: bool = Field(
        default=True,
        validation_alias="ORCHESTRATOR_ENABLED",
        description="启用后支持 DAG + 条件分支 + 循环工作流",
    )
    orchestrator_workflows_path: Path = Field(
        default=REPO_ROOT / "config" / "orchestrator_workflows.yaml",
        validation_alias="ORCHESTRATOR_WORKFLOWS_PATH",
        description="工作流定义 YAML（git 跟踪）",
    )
    orchestrator_overrides_path: Path = Field(
        default=REPO_ROOT / "data" / "orchestrator_overrides.json",
        validation_alias="ORCHESTRATOR_OVERRIDES_PATH",
        description="工作流运行时 overrides（admin API，不进 git）",
    )
    orchestrator_max_steps: int = Field(
        default=100,
        validation_alias="ORCHESTRATOR_MAX_STEPS",
        description="单次工作流最大节点执行数（防死循环）",
    )
    orchestrator_timeout_seconds: float = Field(
        default=300.0,
        validation_alias="ORCHESTRATOR_TIMEOUT_SECONDS",
        description="单次工作流总超时",
    )
    orchestrator_max_parallel_branches: int = Field(
        default=5,
        validation_alias="ORCHESTRATOR_MAX_PARALLEL_BRANCHES",
        description="parallel 节点最大并发分支数",
    )

    # Phase H — Multi-Agent 协作框架 (#38)
    multi_agent_enabled: bool = Field(
        default=True,
        validation_alias="MULTI_AGENT_ENABLED",
        description="启用后支持主 Agent 委托子 Agent 协作",
    )
    agents_config_path: Path = Field(
        default=REPO_ROOT / "config" / "agents.yaml",
        validation_alias="AGENTS_CONFIG_PATH",
        description="Agent 定义 YAML（git 跟踪）",
    )
    agents_overrides_path: Path = Field(
        default=REPO_ROOT / "data" / "agents_overrides.json",
        validation_alias="AGENTS_OVERRIDES_PATH",
        description="Agent 运行时 overrides（admin API，不进 git）",
    )
    multi_agent_default_timeout: float = Field(
        default=60.0,
        validation_alias="MULTI_AGENT_DEFAULT_TIMEOUT",
        description="单次委托默认超时",
    )
    multi_agent_max_depth: int = Field(
        default=3,
        validation_alias="MULTI_AGENT_MAX_DEPTH",
        description="委托最大深度（防递归爆炸）",
    )

    # Phase H — Agent 生命周期管理 (#39)
    agent_lifecycle_enabled: bool = Field(
        default=True,
        validation_alias="AGENT_LIFECYCLE_ENABLED",
        description="启用 Agent 版本管理 + 灰度发布 + 回滚",
    )
    agent_lifecycle_versions_path: Path = Field(
        default=REPO_ROOT / "config" / "agent_versions.yaml",
        validation_alias="AGENT_LIFECYCLE_VERSIONS_PATH",
        description="Agent 版本 YAML（git 跟踪）",
    )
    agent_lifecycle_overrides_path: Path = Field(
        default=REPO_ROOT / "data" / "agent_versions_overrides.json",
        validation_alias="AGENT_LIFECYCLE_OVERRIDES_PATH",
        description="Agent 版本运行时 overrides（不进 git）",
    )

    # Phase H — HITL 完整工作流 (#40)
    hitl_enabled: bool = Field(
        default=True,
        validation_alias="HITL_ENABLED",
        description="启用 HITL 审批工作流（替换 stub）",
    )
    hitl_store_database_url: str | None = Field(
        default=None,
        validation_alias="HITL_STORE_DATABASE_URL",
        description="HITL 审批队列存储；None=内存",
    )
    hitl_default_timeout_seconds: int = Field(
        default=300,
        validation_alias="HITL_DEFAULT_TIMEOUT_SECONDS",
        description="审批默认超时",
    )
    hitl_webhook_url: str | None = Field(
        default=None,
        validation_alias="HITL_WEBHOOK_URL",
        description="审批通知 webhook URL",
    )
    hitl_webhook_secret: str | None = Field(
        default=None,
        validation_alias="HITL_WEBHOOK_SECRET",
        description="webhook HMAC 签名密钥",
    )
    hitl_expiry_check_interval_seconds: int = Field(
        default=60,
        validation_alias="HITL_EXPIRY_CHECK_INTERVAL_SECONDS",
        description="过期检查间隔",
    )

    # Phase G — Embedding 独立服务 (#35)
    embedding_service_enabled: bool = Field(
        default=True,
        validation_alias="EMBEDDING_SERVICE_ENABLED",
        description="启用独立 Embedding 服务",
    )
    embedding_models_config_path: Path = Field(
        default=REPO_ROOT / "config" / "embedding_models.yaml",
        validation_alias="EMBEDDING_MODELS_CONFIG_PATH",
        description="Embedding 模型 YAML（git 跟踪）",
    )
    embedding_models_overrides_path: Path = Field(
        default=REPO_ROOT / "data" / "embedding_models_overrides.json",
        validation_alias="EMBEDDING_MODELS_OVERRIDES_PATH",
        description="Embedding 模型运行时 overrides（不进 git）",
    )
    embedding_cache_max_size: int = Field(
        default=10000,
        validation_alias="EMBEDDING_CACHE_MAX_SIZE",
        description="Embedding 缓存最大条数",
    )
    embedding_default_model: str = Field(
        default="text-embedding-3-small",
        validation_alias="EMBEDDING_DEFAULT_MODEL",
        description="默认 embedding 模型",
    )

    # Phase I #41 — 沙箱容器隔离
    sandbox_enabled: bool = Field(
        default=False,
        validation_alias="SANDBOX_ENABLED",
        description="启用工具沙箱隔离",
    )
    sandbox_default_runtime: str = Field(
        default="process",
        validation_alias="SANDBOX_DEFAULT_RUNTIME",
        description="默认运行时: process/docker/gvisor",
    )
    sandbox_default_image: str = Field(
        default="python:3.11-slim",
        validation_alias="SANDBOX_DEFAULT_IMAGE",
        description="默认容器镜像",
    )
    sandbox_profiles_config_path: Path = Field(
        default=REPO_ROOT / "config" / "sandbox_profiles.yaml",
        validation_alias="SANDBOX_PROFILES_CONFIG_PATH",
        description="沙箱档案 YAML（git 跟踪）",
    )
    sandbox_profiles_overrides_path: Path = Field(
        default=REPO_ROOT / "data" / "sandbox_profiles_overrides.json",
        validation_alias="SANDBOX_PROFILES_OVERRIDES_PATH",
        description="沙箱档案 overrides（不进 git）",
    )
    sandbox_default_memory_mb: int = Field(
        default=256,
        validation_alias="SANDBOX_DEFAULT_MEMORY_MB",
        description="默认内存限制（MB）",
    )
    sandbox_default_cpu_limit: float = Field(
        default=0.5,
        validation_alias="SANDBOX_DEFAULT_CPU_LIMIT",
        description="默认 CPU 核数限制",
    )
    sandbox_default_timeout_seconds: float = Field(
        default=30.0,
        validation_alias="SANDBOX_DEFAULT_TIMEOUT_SECONDS",
        description="默认超时（秒）",
    )

    # Phase I #42 — 动作分级审计
    audit_actions_enabled: bool = Field(
        default=True,
        validation_alias="AUDIT_ACTIONS_ENABLED",
        description="启用动作分级审计",
    )
    audit_actions_config_path: Path = Field(
        default=REPO_ROOT / "config" / "tool_classifications.yaml",
        validation_alias="AUDIT_ACTIONS_CONFIG_PATH",
        description="工具分类 YAML（git 跟踪）",
    )
    audit_actions_overrides_path: Path = Field(
        default=REPO_ROOT / "data" / "tool_classifications_overrides.json",
        validation_alias="AUDIT_ACTIONS_OVERRIDES_PATH",
        description="工具分类 overrides（不进 git）",
    )
    audit_actions_store_database_url: str | None = Field(
        default=None,
        validation_alias="AUDIT_ACTIONS_STORE_DATABASE_URL",
        description="动作审计存储；None=内存",
    )
    audit_destructive_requires_approval: bool = Field(
        default=True,
        validation_alias="AUDIT_DESTRUCTIVE_REQUIRES_APPROVAL",
        description="destructive 动作是否强制审批",
    )

    # Phase I #43 — PII 脱敏 + 内容安全
    pii_service_enabled: bool = Field(
        default=True,
        validation_alias="PII_SERVICE_ENABLED",
        description="启用 PII 脱敏 + 内容安全",
    )
    pii_patterns_config_path: Path = Field(
        default=REPO_ROOT / "config" / "pii_patterns.yaml",
        validation_alias="PII_PATTERNS_CONFIG_PATH",
        description="PII 模式 YAML（git 跟踪）",
    )
    pii_patterns_overrides_path: Path = Field(
        default=REPO_ROOT / "data" / "pii_patterns_overrides.json",
        validation_alias="PII_PATTERNS_OVERRIDES_PATH",
        description="PII 模式 overrides（不进 git）",
    )
    pii_safety_keywords_path: Path = Field(
        default=REPO_ROOT / "config" / "safety_keywords.yaml",
        validation_alias="PII_SAFETY_KEYWORDS_PATH",
        description="安全关键词 YAML",
    )
    pii_default_policy: str = Field(
        default="default",
        validation_alias="PII_DEFAULT_POLICY",
        description="默认脱敏策略",
    )
    pii_block_on_safety_failure: bool = Field(
        default=False,
        validation_alias="PII_BLOCK_ON_SAFETY_FAILURE",
        description="内容安全检查失败时是否阻断",
    )

    # Phase I #44 — OAuth2 / mTLS
    oauth2_enabled: bool = Field(
        default=False,
        validation_alias="OAUTH2_ENABLED",
        description="启用 OAuth2 鉴权（默认关闭，保持 JWT HS256）",
    )
    oauth2_client_id: str | None = Field(
        default=None,
        validation_alias="OAUTH2_CLIENT_ID",
    )
    oauth2_client_secret: str | None = Field(
        default=None,
        validation_alias="OAUTH2_CLIENT_SECRET",
    )
    oauth2_authorization_endpoint: str = Field(
        default="",
        validation_alias="OAUTH2_AUTHORIZATION_ENDPOINT",
    )
    oauth2_token_endpoint: str = Field(
        default="",
        validation_alias="OAUTH2_TOKEN_ENDPOINT",
    )
    oauth2_userinfo_endpoint: str = Field(
        default="",
        validation_alias="OAUTH2_USERINFO_ENDPOINT",
    )
    oauth2_redirect_uri: str = Field(
        default="http://127.0.0.1:8000/internal/auth/oauth2/callback",
        validation_alias="OAUTH2_REDIRECT_URI",
    )
    oauth2_scopes: str = Field(
        default="openid profile email",
        validation_alias="OAUTH2_SCOPES",
    )
    oauth2_issuer: str | None = Field(
        default=None,
        validation_alias="OAUTH2_ISSUER",
    )
    oauth2_jwt_fallback: bool = Field(
        default=True,
        validation_alias="OAUTH2_JWT_FALLBACK",
        description="OAuth2 失败时回退 JWT",
    )
    mtls_enabled: bool = Field(
        default=False,
        validation_alias="MTLS_ENABLED",
        description="启用 mTLS 客户端证书校验",
    )
    mtls_ca_cert_path: str | None = Field(
        default=None,
        validation_alias="MTLS_CA_CERT_PATH",
    )
    mtls_server_cert_path: str | None = Field(
        default=None,
        validation_alias="MTLS_SERVER_CERT_PATH",
    )
    mtls_server_key_path: str | None = Field(
        default=None,
        validation_alias="MTLS_SERVER_KEY_PATH",
    )
    mtls_client_cert_required: bool = Field(
        default=True,
        validation_alias="MTLS_CLIENT_CERT_REQUIRED",
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
