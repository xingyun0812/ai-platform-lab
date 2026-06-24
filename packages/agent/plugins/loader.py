"""从 config/plugins/*.yaml 加载 Plugin Manifest 工具。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from packages.agent.plugins.handlers import BUILTIN_PLUGIN_HANDLERS, make_http_handler
from packages.agent.tools.base import ToolDefinition

logger = logging.getLogger("ai_platform.agent.plugins")

_LOADED_PLUGINS: dict[str, ToolDefinition] | None = None


class PluginLoadError(ValueError):
    """Plugin manifest 解析或注册失败。"""


def reset_plugins_for_tests() -> None:
    global _LOADED_PLUGINS
    _LOADED_PLUGINS = None


def _resolve_handler(raw: Any, *, source: str) -> Any:
    if isinstance(raw, str):
        handler = BUILTIN_PLUGIN_HANDLERS.get(raw.strip())
        if handler is None:
            raise PluginLoadError(f"{source}: unknown builtin handler {raw!r}")
        return handler

    if not isinstance(raw, dict):
        raise PluginLoadError(f"{source}: handler must be string or mapping")

    handler_type = str(raw.get("type") or "builtin").strip().lower()
    if handler_type == "builtin":
        name = str(raw.get("name") or "").strip()
        if not name:
            raise PluginLoadError(f"{source}: builtin handler missing name")
        handler = BUILTIN_PLUGIN_HANDLERS.get(name)
        if handler is None:
            raise PluginLoadError(f"{source}: unknown builtin handler {name!r}")
        return handler

    if handler_type == "http":
        url = str(raw.get("url") or "").strip()
        if not url:
            raise PluginLoadError(f"{source}: http handler missing url")
        method = str(raw.get("method") or "POST")
        timeout = float(raw.get("timeout_seconds") or 10.0)
        return make_http_handler(url=url, method=method, timeout_seconds=timeout)

    raise PluginLoadError(f"{source}: unsupported handler type {handler_type!r}")


def parse_plugin_manifest(data: dict[str, Any], *, source: str) -> ToolDefinition:
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PluginLoadError(f"{source}: missing or invalid name")
    name = name.strip()

    if data.get("enabled") is False:
        raise PluginLoadError(f"{source}: plugin disabled")

    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise PluginLoadError(f"{source}: missing description")

    schema = data.get("parameters_schema")
    if not isinstance(schema, dict):
        raise PluginLoadError(f"{source}: parameters_schema must be object")

    handler = _resolve_handler(data.get("handler"), source=source)
    return ToolDefinition(
        name=name,
        description=description.strip(),
        parameters_schema=schema,
        handler=handler,
    )


def load_plugins_from_directory(
    plugins_dir: Path,
    *,
    reserved_names: frozenset[str] | None = None,
    strict: bool = False,
) -> dict[str, ToolDefinition]:
    """扫描目录下 ``*.yaml``，返回 name → ToolDefinition。"""
    reserved = reserved_names or frozenset()
    if not plugins_dir.is_dir():
        logger.info("plugins dir missing path=%s", plugins_dir)
        return {}

    loaded: dict[str, ToolDefinition] = {}
    errors: list[str] = []

    for path in sorted(plugins_dir.glob("*.yaml")):
        source = str(path)
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as e:
            msg = f"{source}: yaml parse error: {e}"
            errors.append(msg)
            logger.warning(msg)
            continue

        if not isinstance(raw, dict):
            errors.append(f"{source}: root must be mapping")
            continue

        try:
            tool = parse_plugin_manifest(raw, source=source)
        except PluginLoadError as e:
            if "disabled" in str(e):
                logger.debug("skip disabled plugin %s", source)
                continue
            errors.append(str(e))
            logger.warning(str(e))
            continue

        if tool.name in reserved or tool.name in loaded:
            msg = f"{source}: duplicate plugin name {tool.name!r}"
            errors.append(msg)
            logger.warning(msg)
            continue

        loaded[tool.name] = tool

    if strict and errors:
        raise PluginLoadError("; ".join(errors))

    logger.info("plugins loaded count=%d dir=%s", len(loaded), plugins_dir)
    return loaded


def get_loaded_plugins(
    plugins_dir: Path | None = None,
    *,
    reserved_names: frozenset[str] | None = None,
) -> dict[str, ToolDefinition]:
    global _LOADED_PLUGINS
    if _LOADED_PLUGINS is not None:
        return dict(_LOADED_PLUGINS)

    if plugins_dir is None:
        from apps.gateway.settings import get_settings

        settings = get_settings()
        if not settings.agent_plugins_enabled:
            _LOADED_PLUGINS = {}
            return {}
        plugins_dir = settings.agent_plugins_config_dir

    _LOADED_PLUGINS = load_plugins_from_directory(
        plugins_dir,
        reserved_names=reserved_names,
    )
    return dict(_LOADED_PLUGINS)
