#!/usr/bin/env python3
"""tests/test_aggregator.py — 结果聚合校验与缺失补全（PRD #243，ADR-0009 #244，#248）。

只测外部行为：给定 parallel / 多工具产物 + 各工具 output_schema → 得到正确
完整性校验、缺失补全重试、失败标记与部分成功保留。

纯单测：只依赖 packages.agent.scheduling.aggregator 纯函数 + 可选补全回调，
零外部依赖（不依赖 LLM/DB）。表驱动，镜像 tests/test_mutex.py 风格。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.scheduling.aggregator import (  # noqa: E402
    AggregatedProduct,
    OutputSchema,
    aggregate_tool_outputs,
    parse_output_schema,
    validate_completeness,
)


class OutputSchemaTest(unittest.TestCase):
    """output_schema 解析与完整性判定。"""

    def test_parses_compact_json(self):
        schema = OutputSchema('{"result": "number"}')
        self.assertTrue(schema.declared)
        self.assertEqual(schema.required_fields, ("result",))

    def test_parses_mapping_input(self):
        schema = OutputSchema({"rows": "array", "count": "integer"})
        self.assertEqual(set(schema.required_fields), {"rows", "count"})

    def test_none_undeclared(self):
        self.assertFalse(OutputSchema(None).declared)
        self.assertEqual(OutputSchema(None).missing({"x": 1}), ())

    def test_invalid_json_undeclared(self):
        self.assertFalse(OutputSchema("not-json").declared)
        self.assertFalse(OutputSchema("").declared)

    def test_missing_detects_absent_and_none(self):
        schema = OutputSchema('{"result": "number", "note": "string"}')
        # 缺 result（键不存在），note 值为 None → 都算缺失
        self.assertEqual(set(schema.missing({"note": None})), {"result", "note"})

    def test_complete_when_all_fields_present(self):
        schema = OutputSchema('{"result": "number"}')
        self.assertEqual(schema.missing({"result": 42}), ())

    def test_non_mapping_output_all_missing(self):
        schema = OutputSchema('{"result": "number"}')
        self.assertEqual(set(schema.missing("not-a-map")), {"result"})

    def test_parse_output_schema_factory(self):
        self.assertTrue(parse_output_schema('{"a": "b"}').declared)


class ValidateCompletenessTest(unittest.TestCase):
    """单产物完整性校验。"""

    async def run_case(self, **kw: Any) -> AggregatedProduct:
        return await validate_completeness(**kw)

    def test_declared_schema_complete(self):
        import asyncio

        p = asyncio.run(self.run_case(tool_name="calc", output={"result": 42}, raw_schema='{"result": "number"}'))
        self.assertEqual(p.status, "complete")
        self.assertEqual(p.missing_fields, ())

    def test_declared_schema_incomplete(self):
        import asyncio

        p = asyncio.run(self.run_case(tool_name="calc", output={}, raw_schema='{"result": "number"}'))
        self.assertEqual(p.status, "incomplete")
        self.assertEqual(p.missing_fields, ("result",))

    def test_undeclared_schema_backward_compat_complete(self):
        import asyncio

        # 未声明 schema → 不校验，原样 complete
        p = asyncio.run(self.run_case(tool_name="calc", output={"anything": 1}, raw_schema=None))
        self.assertEqual(p.status, "complete")
        self.assertEqual(p.attempts, 0)


class AggregateToolOutputsTest(unittest.TestCase):
    """parallel / 多工具聚合：缺失→补全→仍失败标记→成功分支保留。"""

    async def _agg(
        self,
        results: list[dict],
        *,
        schemas: dict,
        cb: Any = None,
        attempts: int = 1,
    ):
        return await aggregate_tool_outputs(
            results, schemas=schemas, completions_cb=cb, completion_attempts=attempts
        )

    def run_agg(self, *args: Any, **kw: Any):
        import asyncio

        return asyncio.run(self._agg(*args, **kw))

    def test_backward_compat_no_schema_passthrough(self):
        # 无 output_schema 工具：不校验、不补全，产物原样 complete 返回
        res = self.run_agg(
            [{"tool_name": "calc", "result": {"value": 7}}, {"tool_name": "search", "result": {"hits": []}}],
            schemas={},
            cb=None,
        )
        self.assertFalse(res.has_failures)
        self.assertEqual(res.attempts, 0)
        self.assertEqual(res.full_outputs(), {"calc": {"value": 7}, "search": {"hits": []}})
        for p in res.products:
            self.assertEqual(p.status, "complete")

    def test_missing_detected_and_completed_via_callback(self):
        # schema 缺失 → 识别为 incomplete → 补全回调返回完整产物 → complete
        def _cb(tool: str, output: Any):
            self.assertEqual(tool, "calc")
            return {"result": 99}  # 补全成功

        res = self.run_agg(
            [{"tool_name": "calc", "result": {}}],
            schemas={"calc": '{"result": "number"}'},
            cb=_cb,
            attempts=1,
        )
        self.assertFalse(res.has_failures)
        self.assertEqual(res.attempts, 1)
        self.assertEqual(res.products[0].status, "complete")
        self.assertEqual(res.full_outputs(), {"calc": {"result": 99}})

    def test_failed_after_completion_marked_not_blocking(self):
        # 补全仍失败 → 标记到 failed + 聚合错误；成功分支完整返回
        def _cb(tool: str, output: Any):
            return None  # 补全失败

        res = self.run_agg(
            [
                {"tool_name": "calc", "result": {}},  # 缺失 result
                {"tool_name": "search", "result": {"hits": "ok"}},  # 无 schema，透传
            ],
            schemas={"calc": '{"result": "number"}'},
            cb=_cb,
            attempts=1,
        )
        self.assertTrue(res.has_failures)
        self.assertEqual(res.attempts, 1)
        # 成功分支完整返回（不整体丢弃）
        self.assertEqual(res.full_outputs(), {"search": {"hits": "ok"}})
        failed_names = {p.tool_name for p in res.failed}
        self.assertEqual(failed_names, {"calc"})
        self.assertTrue(any("calc" in e for e in res.errors))

    def test_failed_product_absent_output(self):
        # 产物无 output/result 且声明了 schema → failed，进入补全
        def _cb(tool: str, output: Any):
            return {"result": 5}

        res = self.run_agg(
            [{"tool_name": "calc"}],
            schemas={"calc": '{"result": "number"}'},
            cb=_cb,
        )
        self.assertFalse(res.has_failures)
        self.assertEqual(res.products[0].status, "complete")
        self.assertEqual(res.full_outputs(), {"calc": {"result": 5}})

    def test_configurable_attempts(self):
        # completion_attempts 可配置：多次补全
        calls: list[int] = []

        def _cb(tool: str, output: Any):
            calls.append(1)
            # 第一次返回缺 result 的产物（仍 incomplete），第二次才补全 → 需 2 次
            return {"result": len(calls) if len(calls) > 1 else None}

        res = self.run_agg(
            [{"tool_name": "calc", "result": {}}],
            schemas={"calc": '{"result": "number"}'},
            cb=_cb,
            attempts=2,
        )
        self.assertEqual(len(calls), 2)  # 尝试 2 次后补全
        self.assertEqual(res.attempts, 2)
        self.assertEqual(res.products[0].status, "complete")
        self.assertEqual(res.products[0].attempts, 2)
        self.assertEqual(res.full_outputs(), {"calc": {"result": 2}})

    def test_zero_attempts_means_no_completion(self):
        # attempts=0 → 不触发补全，直接标记失败
        called: list[bool] = []

        def _cb(tool: str, output: Any):
            called.append(True)
            return {"result": 1}

        res = self.run_agg(
            [{"tool_name": "calc", "result": {}}],
            schemas={"calc": '{"result": "number"}'},
            cb=_cb,
            attempts=0,
        )
        self.assertEqual(called, [])  # 未触发
        self.assertTrue(res.has_failures)
        self.assertEqual(res.failed[0].status, "incomplete")

    def test_async_completion_callback_supported(self):
        async def _cb(tool: str, output: Any):
            return {"result": 7}

        res = self.run_agg(
            [{"tool_name": "calc", "result": {}}],
            schemas={"calc": '{"result": "number"}'},
            cb=_cb,
        )
        self.assertEqual(res.full_outputs(), {"calc": {"result": 7}})

    def test_schemas_callable_resolver(self):
        # schemas 可传回调（生产传 SchedulePolicyStore.resolve）
        def _resolve(tool: str):
            return '{"result": "number"}' if tool == "calc" else None

        def _cb(tool: str, output: Any):
            return {"result": 3}

        res = self.run_agg(
            [{"tool_name": "calc", "result": {}}],
            schemas=_resolve,
            cb=_cb,
        )
        self.assertEqual(res.full_outputs(), {"calc": {"result": 3}})

    def test_completion_callback_error_treated_as_failure(self):
        def _cb(tool: str, output: Any):
            raise RuntimeError("boom")

        res = self.run_agg(
            [{"tool_name": "calc", "result": {}}],
            schemas={"calc": '{"result": "number"}'},
            cb=_cb,
        )
        self.assertTrue(res.has_failures)
        self.assertTrue(any("boom" in e for e in res.errors))

    def test_mixed_partial_success_preserved(self):
        # 一个成功、一个补全成功、一个补全失败 → 成功/补全产物都保留，仅失败者标记
        def _cb(tool: str, output: Any):
            return {"result": 1} if tool == "calc" else None

        res = self.run_agg(
            [
                {"tool_name": "ok_tool", "result": {"data": [1]}},
                {"tool_name": "calc", "result": {}},
                {"tool_name": "bad_tool", "result": {}},
            ],
            schemas={
                "ok_tool": '{"data": "array"}',
                "calc": '{"result": "number"}',
                "bad_tool": '{"result": "number"}',
            },
            cb=_cb,
        )
        # 成功 + 补全成功 都完整返回，bad_tool 失败不阻塞
        self.assertEqual(
            set(res.full_outputs()), {"ok_tool", "calc"}
        )
        self.assertEqual(set(p.tool_name for p in res.failed), {"bad_tool"})


if __name__ == "__main__":
    unittest.main()