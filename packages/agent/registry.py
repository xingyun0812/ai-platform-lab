from __future__ import annotations

import logging

from packages.agent.tools.base import ToolDefinition
from packages.agent.tools.builtin import (
    handle_calc,
    handle_get_kb_snippet,
    handle_httpbin_delay,
    handle_math_llm_stub,
    handle_search_web_stub,
)
from packages.agent.tools.web_search import handle_web_search

logger = logging.getLogger("ai_platform.agent.registry")

_REGISTRY: dict[str, ToolDefinition] | None = None


def build_default_registry() -> dict[str, ToolDefinition]:
    return {
        "calc": ToolDefinition(
            name="calc",
            description="确定性算术计算，仅支持 + - * / 与括号",
            parameters_schema={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "算术表达式，如 (1+2)*3",
                    }
                },
                "required": ["expression"],
            },
            handler=handle_calc,
        ),
        "get_kb_snippet": ToolDefinition(
            name="get_kb_snippet",
            description="从知识库检索相关片段（向量检索），返回 top 片段摘要",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索问题"},
                    "kb_id": {"type": "string", "description": "知识库 ID"},
                    "version": {
                        "type": "integer",
                        "description": "可选，省略则用最新版本",
                    },
                    "top_k": {"type": "integer", "description": "返回条数，默认 3"},
                },
                "required": ["query", "kb_id"],
            },
            handler=handle_get_kb_snippet,
        ),
        "httpbin_delay": ToolDefinition(
            name="httpbin_delay",
            description="调用 httpbin 延迟接口，用于测试工具超时（秒）",
            parameters_schema={
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "number",
                        "description": "延迟秒数 1-30",
                    }
                },
                "required": ["seconds"],
            },
            handler=handle_httpbin_delay,
        ),
        "search_web_stub": ToolDefinition(
            name="search_web_stub",
            description="在互联网搜索公开网页（When NOT：查企业内部知识库请用 get_kb_snippet）",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["query"],
            },
            handler=handle_search_web_stub,
        ),
        "web_search": ToolDefinition(
            name="web_search",
            description=(
                "搜索公开互联网信息，返回 top-k 标题/摘要/链接（When NOT：查企业内部知识库请用 get_kb_snippet）"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "top_k": {
                        "type": "integer",
                        "description": "返回条数，默认 3，最大 10",
                    },
                },
                "required": ["query"],
            },
            handler=handle_web_search,
        ),
        "math_llm_stub": ToolDefinition(
            name="math_llm_stub",
            description="用 LLM 粗略估算数学问题（When NOT：精确计算请用 calc）",
            parameters_schema={
                "type": "object",
                "properties": {
                    "problem": {"type": "string", "description": "数学问题描述"},
                },
                "required": ["problem"],
            },
            handler=handle_math_llm_stub,
        ),
    }


def get_tool_registry() -> dict[str, ToolDefinition]:
    global _REGISTRY
    if _REGISTRY is None:
        merged = build_default_registry()
        # Phase O #90 — YAML Plugin Manifest
        try:
            from apps.gateway.settings import get_settings
            from packages.agent.plugins.loader import get_loaded_plugins

            settings = get_settings()
            if settings.agent_plugins_enabled:
                plugin_tools = get_loaded_plugins(
                    reserved_names=frozenset(merged.keys()),
                )
                merged.update(plugin_tools)
                logger.info("plugin tools loaded count=%d", len(plugin_tools))
        except Exception as e:
            logger.warning("plugin tools load failed: %s", e)
        # Phase F #32：动态加载 MCP server 工具
        try:
            from apps.gateway.settings import get_settings

            settings = get_settings()
            if settings.mcp_enabled:
                import asyncio

                from packages.mcp import get_mcp_registry, load_mcp_tools

                # 确保 registry 已初始化
                if get_mcp_registry() is None:
                    from packages.mcp import init_mcp_registry

                    init_mcp_registry(
                        yaml_path=settings.mcp_servers_config_path,
                        overrides_path=settings.mcp_overrides_path,
                    )
                # 加载 MCP 工具（同步包装 async）
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                if loop.is_running():
                    # 在已有事件循环中（如 FastAPI）：调度但不阻塞
                    logger.info("mcp tools loading deferred to async context")
                else:
                    mcp_tools = loop.run_until_complete(
                        load_mcp_tools(
                            timeout_seconds=settings.mcp_connect_timeout_seconds
                        )
                    )
                    merged.update(mcp_tools)
                    logger.info("mcp tools loaded count=%d", len(mcp_tools))
            else:
                # 关闭时回退到 stub
                from packages.agent.mcp_stub import load_mcp_stub_tools

                merged.update(load_mcp_stub_tools())
        except Exception:
            # 兜底：用 stub
            try:
                from packages.agent.mcp_stub import load_mcp_stub_tools

                merged.update(load_mcp_stub_tools())
            except Exception:
                pass
        _REGISTRY = merged
    return _REGISTRY


async def refresh_mcp_tools() -> int:
    """重新加载 MCP 工具（运行时刷新）。

    返回新加载的工具数。
    用于 admin 通过 API 添加 MCP server 后刷新 registry。
    """
    from apps.gateway.settings import get_settings
    from packages.mcp import get_mcp_registry, load_mcp_tools

    settings = get_settings()
    if not settings.mcp_enabled:
        return 0
    if get_mcp_registry() is None:
        from packages.mcp import init_mcp_registry

        init_mcp_registry(
            yaml_path=settings.mcp_servers_config_path,
            overrides_path=settings.mcp_overrides_path,
        )
    mcp_tools = await load_mcp_tools(
        timeout_seconds=settings.mcp_connect_timeout_seconds
    )
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build_default_registry()
    # 移除旧的 mcp_ 工具
    old_keys = [k for k in _REGISTRY if k.startswith("mcp_")]
    for k in old_keys:
        _REGISTRY.pop(k, None)
    _REGISTRY.update(mcp_tools)
    logger.info("mcp tools refreshed count=%d", len(mcp_tools))
    return len(mcp_tools)


def reset_tool_registry_for_tests() -> None:
    global _REGISTRY
    _REGISTRY = None
    from packages.agent.plugins.loader import reset_plugins_for_tests

    reset_plugins_for_tests()


class ToolRegistry:
    def __init__(self, tools: dict[str, ToolDefinition] | None = None) -> None:
        self._tools = tools or get_tool_registry()

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_for_tenant(self, allowed_tools: tuple[str, ...]) -> list[ToolDefinition]:
        if not allowed_tools:
            return list(self._tools.values())
        return [self._tools[n] for n in allowed_tools if n in self._tools]

    def openai_tools_spec(self, allowed_tools: tuple[str, ...]) -> list[dict]:
        return [t.openai_tool_spec() for t in self.list_for_tenant(allowed_tools)]

    def openai_tools_spec_subset(
        self,
        tool_names: tuple[str, ...],
        allowed_tools: tuple[str, ...],
    ) -> list[dict]:
        specs: list[dict] = []
        for name in tool_names:
            if not self.is_allowed(name, allowed_tools):
                continue
            tool = self.get(name)
            if tool:
                specs.append(tool.openai_tool_spec())
        return specs

    def is_allowed(self, name: str, allowed_tools: tuple[str, ...]) -> bool:
        if not allowed_tools:
            return name in self._tools
        return name in allowed_tools and name in self._tools
