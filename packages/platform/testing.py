"""In-memory PlatformPort — 单测 / eval 注入，不依赖 apps.gateway。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packages.platform.types import PlatformSettings


@dataclass
class InMemoryPlatformSettings:
    default_model: str = "test-model"
    agent_model: str = "test-agent-model"
    plan_execution_mode: str = "parallel"
    rag_data_root: Path = field(default_factory=lambda: Path("/tmp/rag-test"))


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
