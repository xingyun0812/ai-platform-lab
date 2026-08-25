#!/usr/bin/env python3
"""tests/test_orchestrator_aggregation.py — orchestrator 聚合补全钩子集成（#248 AC2）。

直接测内部 ``_run_aggregation_hook``：tool_call/parallel 节点执行后，缺产物触发
重跑工具补全（可配置次数，默认 1），补全仍失败标记 trace 不阻塞成功分支，未声明
schema 不补全。零外部依赖（mock 掉补全回调的工具 handler）。
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.orchestrator.engine import (  # noqa: E402
    _run_aggregation_hook,
)
from packages.agent.tools.base import ToolDefinition  # noqa: E402


def _fake_tool(handler) -> ToolDefinition:
    """构造带 mock handler 的工具，供补全回调重跑。"""
    return ToolDefinition(
        name="calc",
        description="test calc",
        parameters_schema={"type": "object", "properties": {}},
        handler=handler,
    )


def _run(coro, *args, **kw):
    return asyncio.run(coro(*args, **kw))


class AggregationHookCompletionTest(unittest.TestCase):
    """tool_call 缺产物 → 触发重跑工具补全的三种形态。"""

    def _patch_registry(self, tool: ToolDefinition):
        """把补全回调用的 ToolRegistry 换成只含给定工具的 fake，返回 handler mock。"""
        return patch(
            "packages.agent.registry.ToolRegistry",
            lambda *a, **k: _OneToolRegistry(tool),
        )

    def test_incomplete_triggers_completion_and_succeeds(self):
        # calc 有 schema{"result":"number"}；产物 result=None → incomplete，
        # 补全回调重跑 calc handler 返回 "3" → complete、status ok。
        calls: list[dict] = []

        async def handler(args: dict):
            calls.append(args)
            return "3"

        tool = _fake_tool(handler)
        with self._patch_registry(tool):
            agg = _run(
                _run_aggregation_hook,
                "tool_call",
                {"tool_name": "calc", "arguments": {"expression": "1+2"}},
                {"result": None, "tool": "calc"},
            )
        self.assertEqual(agg["aggregation"]["status"], "ok")
        self.assertEqual(agg["aggregation"]["attempts"], 1)  # 补全重跑 1 次
        self.assertEqual(len(calls), 1)  # 工具被重跑一次
        # 补全回调用原 arguments 重跑
        self.assertEqual(calls[0].get("expression"), "1+2")

    def test_completion_still_fails_marks_failed_no_block(self):
        # 补全回调 handler 仍返回 None → 仍 incomplete → 标记 failed、status partial，
        # 但成功产品不整体丢弃。
        def handler(args: dict):
            # 同步 handler，重跑仍无产
            return None

        tool = _fake_tool(handler)
        with self._patch_registry(tool):
            agg = _run(
                _run_aggregation_hook,
                "tool_call",
                {"tool_name": "calc", "arguments": {"expression": "1+2"}},
                {"result": None, "tool": "calc"},
            )
        summary = agg["aggregation"]
        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["attempts"], 1)
        self.assertEqual(len(summary["failed"]), 1)
        self.assertEqual(summary["failed"][0]["tool_name"], "calc")
        self.assertIn("result", summary["failed"][0]["missing_fields"])
        self.assertTrue(any("calc" in e for e in summary["errors"]))

    def test_no_output_schema_no_completion(self):
        # web_search 未声明 output_schema → 不校验、不补全、原样透传。
        fired = {"calls": 0}

        def handler(args: dict):
            fired["calls"] += 1
            return "hits"

        tool = ToolDefinition(
            name="web_search",
            description="test search",
            parameters_schema={"type": "object", "properties": {}},
            handler=handler,
        )
        with self._patch_registry(tool):
            agg = _run(
                _run_aggregation_hook,
                "tool_call",
                {"tool_name": "web_search", "arguments": {"query": "x"}},
                {"result": None, "tool": "web_search"},
            )
        summary = agg["aggregation"]
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary.get("attempts"), 0)  # 未触发补全
        self.assertEqual(fired["calls"], 0)
        self.assertNotIn("failed", summary)  # 无失败产物不落 failed 字段

    def test_parallel_branch_missing_triggers_completion(self):
        # parallel 分支（tool_name=calc，有 schema）缺产物 → 补全回调以空参重跑。
        calls: list[dict] = []

        async def handler(args: dict):
            calls.append(args)
            return "7"

        tool = _fake_tool(handler)
        output = {
            "results": [
                # 分支产物缺 result → incomplete → 触发补全（无 branch_id 时用 tool_name 聚合）
                {"tool_name": "calc", "output": {"result": None}},
            ],
            "gather": "all",
        }
        with self._patch_registry(tool):
            agg = _run(
                _run_aggregation_hook,
                "parallel",
                {"branches": []},
                output,
            )
        summary = agg["aggregation"]
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(len(calls), 1)
        # 平行分支无原参：重跑用空参（补全回调不依赖原参）
        self.assertEqual(calls[0], {})


class _OneToolRegistry:
    """只装一个工具的 fake ToolRegistry，供补全回调 get()。"""

    def __init__(self, tool: ToolDefinition) -> None:
        self._tool = tool

    def get(self, name: str):
        return self._tool if name == self._tool.name else None


if __name__ == "__main__":
    unittest.main()
