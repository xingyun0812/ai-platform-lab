"""In-memory PlatformPort — 单测 / eval 注入，不依赖 apps.gateway。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packages.platform.paths import REPO_ROOT
from packages.platform.types import PlatformSettings


@dataclass
class InMemoryPlatformSettings:
    default_model: str = "test-model"
    agent_model: str = "test-agent-model"
    plan_execution_mode: str = "parallel"
    plan_require_approval: bool = False
    plan_max_replan_attempts: int = 2
    rag_data_root: Path = field(default_factory=lambda: Path("/tmp/rag-test"))
    tenants_config_path: Path = field(default_factory=lambda: REPO_ROOT / "config" / "tenants.yaml")
    default_rate_limit_rps: float = 10.0
    default_rate_limit_burst: int = 20
    embedding_model: str = "text-embedding-3-small"
    database_url: str = ""
    redis_url: str = ""
    index_queue_name: str = "index:tasks"
    agent_context_token_budget: int = 32000
    agent_context_keep_recent_turns: int = 8
    agent_tool_result_max_chars: int = 4000
    agent_reasoning_mode: str = "react"
    agent_tool_call_strategy: str = "parallel"
    agent_tool_routing_enabled: bool = False
    agent_tool_rag_enabled: bool = False
    agent_reflect_max_retries: int = 1
    agent_max_steps: int = 8
    agent_plugins_enabled: bool = True
    agent_plugins_config_dir: Path = field(
        default_factory=lambda: REPO_ROOT / "config" / "plugins"
    )
    embedding_service_enabled: bool = True
    rag_multimodal_embedding_model: str = "stub-multimodal"
    mcp_enabled: bool = False
    context_memory_injection_enabled: bool = False
    plan_structured_output_enabled: bool = False
    circuit_breaker_threshold: int = 3

    def __getattr__(self, name: str) -> Any:
        """未知 settings 字段返回安全默认值，避免单测因缺字段失败。"""
        if name.endswith("_enabled"):
            return False
        if name.endswith("_path") or name.endswith("_root") or name.endswith("_url"):
            return ""
        if (
            name.endswith("_seconds")
            or name.endswith("_ms")
            or name.endswith("_budget")
            or name.endswith("_steps")
            or name.endswith("_attempts")
            or name.endswith("_turns")
        ):
            return 0
        return None


@dataclass
class InMemoryPlatformPort:
    settings: InMemoryPlatformSettings = field(default_factory=InMemoryPlatformSettings)
    forward_result: Any = None
    allowed: bool = True
    resolved_model: str = "test-model"
    source_paths: dict[str, Path] = field(default_factory=dict)
    retrieve_versions: dict[str, int] = field(default_factory=dict)

    def get_settings(self) -> PlatformSettings:
        return self.settings

    async def forward_with_model_router(
        self,
        payload: dict[str, Any],
        *,
        requested_model: str | None = None,
        tenant_default: str | None = None,
    ) -> Any:
        if self.forward_result is not None:
            return self.forward_result
        return {
            "status": 200,
            "body": {"choices": [{"message": {"content": "mock"}}]},
            "model_used": requested_model or self.settings.default_model,
        }

    def is_model_allowed(
        self,
        requested: str | None,
        *,
        tenant_default: str | None,
        allowed_models: tuple[str, ...] = (),
    ) -> tuple[bool, str]:
        resolved = requested or tenant_default or self.resolved_model
        if not allowed_models:
            return True, resolved
        if not self.allowed:
            return False, resolved
        if resolved in allowed_models:
            return True, resolved
        return False, resolved

    def resolve_source_path(self, source_uri: str) -> Path:
        if source_uri in self.source_paths:
            return self.source_paths[source_uri]
        root = self.settings.rag_data_root
        rel = source_uri.strip().lstrip("/")
        return (root / rel).resolve()

    def resolve_retrieve_version(self, kb_id: str, version: int | None) -> int:
        if version is not None:
            return version
        return self.retrieve_versions.get(kb_id, 1)
