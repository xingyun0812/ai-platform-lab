"""MCP Server 注册表 — 管理 MCP server 配置、鉴权、健康状态。

存储：
    config/mcp_servers.yaml — git 跟踪的默认配置
    data/mcp_servers_overrides.json — admin API 运行时修改（不进 git）

鉴权：
    api_key — 在 HTTP header 注入（仅 http transport）
    env — stdio transport 的环境变量（可注入 token）

健康检查：
    启动时探测；运行时记录 last_error + healthy 标记
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("ai_platform.mcp.registry")


@dataclass
class MCPServerConfig:
    server_id: str
    transport: str  # "stdio" | "http"
    enabled: bool = True
    # stdio
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # http
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    # 鉴权
    api_key: str = ""
    # 元数据
    description: str = ""
    created_at: float = field(default_factory=time.time)
    created_by: str = "system"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # 不暴露 api_key
        if d.get("api_key"):
            d["api_key"] = "***"
        return d

    def effective_headers(self) -> dict[str, str]:
        """返回实际请求 header（含 api_key）。"""
        h = dict(self.headers)
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def effective_env(self) -> dict[str, str]:
        """返回实际环境变量。"""
        return dict(self.env)


@dataclass
class MCPServerStatus:
    healthy: bool = False
    last_check: float = 0.0
    last_error: str = ""
    tools_count: int = 0


class MCPServerRegistry:
    """MCP server 注册表。

    线程安全。启动时从 YAML + JSON overrides 加载。
    """

    def __init__(
        self,
        *,
        yaml_path: Path | None = None,
        overrides_path: Path | None = None,
    ) -> None:
        self._yaml_path = yaml_path
        self._overrides_path = overrides_path
        self._lock = threading.RLock()
        self._servers: dict[str, MCPServerConfig] = {}
        self._statuses: dict[str, MCPServerStatus] = {}
        self._loaded = False

    def load(self) -> None:
        with self._lock:
            self._servers.clear()
            self._statuses.clear()
            if self._yaml_path and self._yaml_path.is_file():
                try:
                    data = yaml.safe_load(self._yaml_path.read_text(encoding="utf-8"))
                    self._merge_yaml(data)
                    logger.info(
                        "mcp registry loaded yaml=%s servers=%d",
                        self._yaml_path,
                        len(self._servers),
                    )
                except Exception as e:
                    logger.warning("mcp yaml load failed: %s", e)
            if self._overrides_path and self._overrides_path.is_file():
                try:
                    data = json.loads(self._overrides_path.read_text(encoding="utf-8"))
                    self._merge_overrides(data)
                    logger.info(
                        "mcp registry loaded overrides=%s servers=%d",
                        self._overrides_path,
                        len(self._servers),
                    )
                except Exception as e:
                    logger.warning("mcp overrides load failed: %s", e)
            self._loaded = True

    def _merge_yaml(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        servers = data.get("servers")
        if not isinstance(servers, list):
            return
        for item in servers:
            cfg = self._parse_config(item)
            if cfg is not None:
                self._servers[cfg.server_id] = cfg
                self._statuses[cfg.server_id] = MCPServerStatus()

    def _merge_overrides(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        servers = data.get("servers")
        if not isinstance(servers, list):
            return
        for item in servers:
            cfg = self._parse_config(item)
            if cfg is not None:
                self._servers[cfg.server_id] = cfg
                self._statuses.setdefault(cfg.server_id, MCPServerStatus())

    def _parse_config(self, item: dict[str, Any]) -> MCPServerConfig | None:
        try:
            server_id = str(item["server_id"])
            transport = str(item.get("transport", "stdio"))
            return MCPServerConfig(
                server_id=server_id,
                transport=transport,
                enabled=bool(item.get("enabled", True)),
                command=list(item.get("command", [])),
                env=dict(item.get("env", {})),
                url=str(item.get("url", "")),
                headers=dict(item.get("headers", {})),
                api_key=str(item.get("api_key", "")),
                description=str(item.get("description", "")),
                created_at=float(item.get("created_at", time.time())),
                created_by=str(item.get("created_by", "system")),
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("mcp config parse failed: %s item=%r", e, item)
            return None

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def list_server_ids(self) -> list[str]:
        self._ensure_loaded()
        with self._lock:
            return sorted(self._servers.keys())

    def list_servers(self) -> list[MCPServerConfig]:
        self._ensure_loaded()
        with self._lock:
            return [self._servers[sid] for sid in sorted(self._servers.keys())]

    def get_server(self, server_id: str) -> MCPServerConfig | None:
        self._ensure_loaded()
        with self._lock:
            return self._servers.get(server_id)

    def get_status(self, server_id: str) -> MCPServerStatus | None:
        self._ensure_loaded()
        with self._lock:
            return self._statuses.get(server_id)

    def add_server(self, config: MCPServerConfig) -> MCPServerConfig:
        self._ensure_loaded()
        with self._lock:
            self._servers[config.server_id] = config
            self._statuses[config.server_id] = MCPServerStatus()
            self._persist()
            return config

    def update_server(
        self,
        server_id: str,
        *,
        enabled: bool | None = None,
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        api_key: str | None = None,
        description: str | None = None,
    ) -> MCPServerConfig | None:
        self._ensure_loaded()
        with self._lock:
            cfg = self._servers.get(server_id)
            if cfg is None:
                return None
            if enabled is not None:
                cfg.enabled = enabled
            if command is not None:
                cfg.command = list(command)
            if env is not None:
                cfg.env = dict(env)
            if url is not None:
                cfg.url = url
            if headers is not None:
                cfg.headers = dict(headers)
            if api_key is not None:
                cfg.api_key = api_key
            if description is not None:
                cfg.description = description
            self._persist()
            return cfg

    def remove_server(self, server_id: str) -> bool:
        self._ensure_loaded()
        with self._lock:
            if server_id not in self._servers:
                return False
            del self._servers[server_id]
            self._statuses.pop(server_id, None)
            self._persist()
            return True

    def mark_healthy(self, server_id: str, tools_count: int = 0) -> None:
        with self._lock:
            status = self._statuses.setdefault(server_id, MCPServerStatus())
            status.healthy = True
            status.last_check = time.time()
            status.last_error = ""
            status.tools_count = tools_count

    def mark_unhealthy(self, server_id: str, error: str) -> None:
        with self._lock:
            status = self._statuses.setdefault(server_id, MCPServerStatus())
            status.healthy = False
            status.last_check = time.time()
            status.last_error = error

    def _persist(self) -> None:
        if not self._overrides_path:
            return
        try:
            self._overrides_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "servers": [
                    {
                        **cfg.to_dict(),
                        "api_key": cfg.api_key,  # 持久化时保留真实值
                    }
                    for cfg in self._servers.values()
                ]
            }
            self._overrides_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error("mcp persist failed: %s", e)

    def stats(self) -> dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            total = len(self._servers)
            enabled = sum(1 for s in self._servers.values() if s.enabled)
            healthy = sum(1 for s in self._statuses.values() if s.healthy)
            return {
                "total_servers": total,
                "enabled_servers": enabled,
                "healthy_servers": healthy,
            }


# --------------------------------------------------------------------- #
# 全局单例
# --------------------------------------------------------------------- #

_global_registry: MCPServerRegistry | None = None
_global_lock = threading.Lock()


def init_mcp_registry(
    *,
    yaml_path: Path | None = None,
    overrides_path: Path | None = None,
) -> MCPServerRegistry | None:
    global _global_registry
    with _global_lock:
        if not yaml_path:
            return _global_registry
        _global_registry = MCPServerRegistry(
            yaml_path=yaml_path,
            overrides_path=overrides_path,
        )
        _global_registry.load()
        return _global_registry


def get_mcp_registry() -> MCPServerRegistry | None:
    return _global_registry


def reset_mcp_registry_for_tests() -> None:
    global _global_registry
    with _global_lock:
        _global_registry = None
