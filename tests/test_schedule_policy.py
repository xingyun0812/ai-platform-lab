#!/usr/bin/env python3
"""tests/test_schedule_policy.py — 调度策略模型与双轨声明加载（PRD #243，ADR-0009 #244）。

只测外部行为：给 YAML 片段 / 工具注册元数据 / 工具名 → 得到正确合并策略。
纯单测，零外部依赖（不依赖 LLM/DB），不 mock 具体实现细节。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.scheduling.schedule_policy import (  # noqa: E402
    SchedulePolicyError,
    SchedulePolicyStore,
    load_scheduling_config,
    merge_tool_policy,
)


class _FakeRegistry:
    def __init__(self, meta_map: dict[str, dict]) -> None:
        self._meta = meta_map

    def get(self, name: str):
        return _FakeTool(self._meta.get(name, {}))


class _FakeTool:
    def __init__(self, meta: dict) -> None:
        self._meta = meta

    def scheduling_metadata(self) -> dict:
        return self._meta


class MergeToolPolicyTest(unittest.TestCase):
    """双轨合并：YAML 优先，注册元数据兜底。"""

    def test_yaml_wins_over_metadata(self):
        result = merge_tool_policy(
            {"mutex_group": "db", "priority": 10, "resource_pool": "core"},
            {"mutex_group": "other", "priority": 99, "resource_pool": "shared"},
        )
        self.assertEqual(result.mutex_group, "db")  # YAML 赢
        self.assertEqual(result.priority, 10)
        self.assertEqual(result.resource_pool, "core")
        self.assertTrue(result.is_constrained)

    def test_metadata_fills_when_yaml_missing_field(self):
        # YAML 只声明了 output_schema，priority 从注册元数据兜底
        result = merge_tool_policy(
            {"output_schema": '{"x": "int"}'}, {"priority": 5}
        )
        self.assertEqual(result.output_schema, '{"x": "int"}')
        self.assertEqual(result.priority, 5)

    def test_undeclared_is_no_constraint_backward_compat(self):
        result = merge_tool_policy(None, None)
        self.assertEqual(result.mutex_group, None)
        self.assertEqual(result.priority, None)
        self.assertEqual(result.output_schema, None)
        self.assertEqual(result.resource_pool, "shared")  # 未声明落默认值
        self.assertFalse(result.is_constrained)

    def test_resource_pool_defaults_to_shared(self):
        result = merge_tool_policy({"mutex_group": "g"}, None)
        self.assertEqual(result.resource_pool, "shared")

    def test_invalid_priority_raises(self):
        with self.assertRaises(SchedulePolicyError):
            merge_tool_policy({"priority": "not-an-int"}, None)

    def test_invalid_resource_pool_raises(self):
        with self.assertRaises(SchedulePolicyError):
            merge_tool_policy({"resource_pool": "invalid"}, None)


class LoadSchedulingConfigTest(unittest.TestCase):
    """从实际 YAML 加载调度字段。"""

    def test_loads_from_repo_config(self):
        cfg = load_scheduling_config()
        # 该文件里至少覆盖互斥组 / priority / resource_pool / output_schema 各一处
        self.assertTrue(cfg)  # 应能从真实 config/tool_classifications.yaml 读到
        self.assertIn("sql_query", cfg)
        self.assertIn("delete_file", cfg)
        self.assertIn("send_email", cfg)

    def test_missing_file_returns_empty(self):
        self.assertEqual(load_scheduling_config("nonexistent_tool_sched.yaml"), {})

    def test_declared_tools_parse_fields(self):
        cfg = load_scheduling_config()
        self.assertEqual(cfg["sql_query"]["resource_pool"], "shared")
        self.assertTrue("output_schema" in cfg["sql_query"])
        self.assertEqual(cfg["send_email"]["mutex_group"], "write")
        self.assertEqual(cfg["delete_file"]["resource_pool"], "core")


class SchedulePolicyStoreTest(unittest.TestCase):
    """仓库：聚合 YAML + 注册元数据，按工具名解析。"""

    def setUp(self):
        self.yaml_cfg = {
            "db_tool": {"mutex_group": "db", "priority": 10},
            "write_tool": {"mutex_group": "write", "resource_pool": "core"},
        }

    def test_resolve_from_yaml_only(self):
        store = SchedulePolicyStore(config=dict(self.yaml_cfg))
        self.assertEqual(store.resolve("db_tool").mutex_group, "db")
        self.assertEqual(store.resolve("db_tool").priority, 10)
        # 未声明的工具返回默认无约束
        self.assertFalse(store.resolve("unknown_tool").is_constrained)

    def test_resolve_with_registry_metadata_fallback(self):
        # db_tool 在 YAML 没声明 output_schema，从注册元数据兜底
        reg = _FakeRegistry({"db_tool": {"output_schema": '{"rows": "array"}'}})
        store = SchedulePolicyStore(config=dict(self.yaml_cfg), registry=reg)
        self.assertEqual(store.resolve("db_tool").output_schema, '{"rows": "array"}')
        self.assertEqual(store.resolve("db_tool").mutex_group, "db")

    def test_yaml_wins_even_with_registry_metadata(self):
        # YAML 声明 priority=10，注册元数据 priority=99 → YAML 胜
        reg = _FakeRegistry({"db_tool": {"priority": 99}})
        store = SchedulePolicyStore(config=dict(self.yaml_cfg), registry=reg)
        self.assertEqual(store.resolve("db_tool").priority, 10)

    def test_mutex_groups_grouping(self):
        store = SchedulePolicyStore(config=dict(self.yaml_cfg))
        groups = store.mutex_groups()
        self.assertEqual(groups.get("db"), ["db_tool"])
        self.assertEqual(groups.get("write"), ["write_tool"])

    def test_mutex_groups_via_real_config(self):
        cfg = load_scheduling_config()
        store = SchedulePolicyStore(config=cfg)
        groups = store.mutex_groups()
        self.assertIn("write", groups)
        self.assertIn("destructive", groups)

    def test_resolve_many(self):
        store = SchedulePolicyStore(config=dict(self.yaml_cfg))
        out = store.resolve_many(["db_tool", "unknown"])
        self.assertIn("db_tool", out)
        self.assertIn("unknown", out)


if __name__ == "__main__":
    unittest.main()