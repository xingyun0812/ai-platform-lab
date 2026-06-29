"""Platform port types — packages 可读配置与 gateway 适配端口。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PlatformSettings(Protocol):
    """packages 层可读的配置切片。

    Gateway 专属（不在此 Protocol）：CORS、JWT、HTTP 监听端口、静态文件路径等。
    见 ``apps.gateway.settings.Settings`` 完整字段。
    """

    default_model: str
    agent_model: str
    plan_execution_mode: str
    plan_require_approval: bool
    plan_max_replan_attempts: int
    rag_data_root: Path
    tenants_config_path: Path
    default_rate_limit_rps: float
    default_rate_limit_burst: int
    models_config_path: Path
    circuit_breaker_threshold: int
    llm_api_key: str
    llm_base_url: str
    upstream_max_retries: int
    upstream_timeout_seconds: float
    database_url: str
    redis_url: str
    qdrant_url: str
    embedding_model: str
    chunk_size: int
    chunk_overlap: int
    rag_config_path: Path
    use_index_worker: bool
    index_queue_name: str


@runtime_checkable
class PlatformPort(Protocol):
    def get_settings(self) -> PlatformSettings: ...

    async def forward_with_model_router(
        self,
        payload: dict[str, Any],
        *,
        requested_model: str | None = None,
        tenant_default: str | None = None,
    ) -> Any: ...

    def is_model_allowed(
        self,
        requested: str | None,
        *,
        tenant_default: str | None,
        allowed_models: tuple[str, ...] = (),
    ) -> tuple[bool, str]: ...

    def resolve_source_path(self, source_uri: str) -> Path: ...

    def resolve_retrieve_version(self, kb_id: str, version: int | None) -> int: ...
