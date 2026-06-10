from __future__ import annotations

from packages.agent.tools.base import ToolDefinition
from packages.agent.tools.builtin import (
    handle_calc,
    handle_get_kb_snippet,
    handle_httpbin_delay,
    handle_math_llm_stub,
    handle_search_web_stub,
)

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
        try:
            from packages.agent.mcp_stub import load_mcp_stub_tools

            merged.update(load_mcp_stub_tools())
        except Exception:
            pass
        _REGISTRY = merged
    return _REGISTRY


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
