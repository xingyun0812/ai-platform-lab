#!/usr/bin/env python3
"""tests/test_plan_quality_gate.py — Phase Q Q6 规划质量门禁单元测试。

所有测试无外部 API 依赖，使用 unittest。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Import directly from eval/plan_quality_gate.py (no pydantic chain needed)
from eval.plan_quality_gate import (  # noqa: E402
    _AgentPlan,
    _PlanStep,
    _build_mock_plan_for_case,
    _topological_sort,
    check_plan_quality,
    load_baseline,
    run_gate,
    static_check_baseline,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan(
    goal: str = "test goal",
    steps_data: list[dict] | None = None,
) -> _AgentPlan:
    """Build a minimal _AgentPlan from a list of step dicts."""
    if steps_data is None:
        steps_data = [{"id": "s1", "description": "唯一步骤", "tool_hint": None, "depends_on": []}]
    steps = [
        _PlanStep(
            id=s["id"],
            description=s.get("description", ""),
            tool_hint=s.get("tool_hint"),
            depends_on=s.get("depends_on", []),
        )
        for s in steps_data
    ]
    return _AgentPlan(goal=goal, steps=steps)


# ---------------------------------------------------------------------------
# Tests: load_baseline
# ---------------------------------------------------------------------------


class TestLoadBaseline(unittest.TestCase):
    def test_load_baseline_returns_list(self) -> None:
        """baseline 文件加载成功，返回非空列表。"""
        cases = load_baseline()
        self.assertIsInstance(cases, list)
        self.assertGreater(len(cases), 0)

    def test_load_baseline_has_required_fields(self) -> None:
        """每条用例有 id / goal / min_steps / max_steps。"""
        cases = load_baseline()
        required = {"id", "goal", "min_steps", "max_steps"}
        for case in cases:
            for field in required:
                self.assertIn(field, case, f"用例 {case.get('id', '?')} 缺少字段 {field}")

    def test_load_baseline_at_least_5_cases(self) -> None:
        """baseline 至少包含 5 条用例。"""
        cases = load_baseline()
        self.assertGreaterEqual(len(cases), 5)

    def test_load_baseline_invalid_path_raises(self) -> None:
        """不存在的路径应抛出 FileNotFoundError。"""
        with self.assertRaises(FileNotFoundError):
            load_baseline("/nonexistent/path/plan_baseline.jsonl")

    def test_load_baseline_invalid_jsonl_raises(self, tmp_path=None) -> None:
        """包含无效 JSON 行的文件应抛出 ValueError。"""
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("{invalid json}\n")
            fname = f.name
        with self.assertRaises(ValueError):
            load_baseline(fname)


# ---------------------------------------------------------------------------
# Tests: check_plan_quality
# ---------------------------------------------------------------------------


class TestCheckPlanQuality(unittest.TestCase):
    def test_check_plan_quality_steps_ok(self) -> None:
        """步骤数量在范围内 → steps_ok=True。"""
        case = {
            "id": "t1",
            "goal": "g",
            "min_steps": 1,
            "max_steps": 3,
            "required_tool_hints": None,
        }
        plan = _make_plan(
            steps_data=[
                {"id": "s1", "description": "步骤1", "depends_on": []},
                {"id": "s2", "description": "步骤2", "depends_on": ["s1"]},
            ]
        )
        result = check_plan_quality(plan, case)
        self.assertTrue(result["steps_ok"])
        self.assertEqual(result["steps_count"], 2)

    def test_check_plan_quality_steps_too_many(self) -> None:
        """步骤超出 max_steps → steps_ok=False。"""
        case = {
            "id": "t2",
            "goal": "g",
            "min_steps": 1,
            "max_steps": 2,
            "required_tool_hints": None,
        }
        plan = _make_plan(
            steps_data=[
                {"id": "s1", "description": "步骤1", "depends_on": []},
                {"id": "s2", "description": "步骤2", "depends_on": ["s1"]},
                {"id": "s3", "description": "步骤3", "depends_on": ["s2"]},
            ]
        )
        result = check_plan_quality(plan, case)
        self.assertFalse(result["steps_ok"])

    def test_check_plan_quality_steps_too_few(self) -> None:
        """步骤少于 min_steps → steps_ok=False。"""
        case = {
            "id": "t3",
            "goal": "g",
            "min_steps": 3,
            "max_steps": 5,
            "required_tool_hints": None,
        }
        plan = _make_plan(
            steps_data=[
                {"id": "s1", "description": "步骤1", "depends_on": []},
            ]
        )
        result = check_plan_quality(plan, case)
        self.assertFalse(result["steps_ok"])

    def test_check_plan_quality_tool_hint_match(self) -> None:
        """plan 含 required tool_hint → tool_hints_ok=True。"""
        case = {
            "id": "t4",
            "goal": "g",
            "min_steps": 1,
            "max_steps": 3,
            "required_tool_hints": ["calc", "web_search"],
        }
        plan = _make_plan(
            steps_data=[
                {"id": "s1", "description": "计算", "tool_hint": "calc", "depends_on": []},
            ]
        )
        result = check_plan_quality(plan, case)
        self.assertTrue(result["tool_hints_ok"])

    def test_check_plan_quality_tool_hint_none_in_list(self) -> None:
        """required_tool_hints 含 None → 任意 plan 都通过 tool_hints_ok。"""
        case = {
            "id": "t5",
            "goal": "g",
            "min_steps": 1,
            "max_steps": 3,
            "required_tool_hints": ["weather_api", None],
        }
        plan = _make_plan(
            steps_data=[
                {"id": "s1", "description": "查天气", "tool_hint": "other_tool", "depends_on": []},
            ]
        )
        result = check_plan_quality(plan, case)
        self.assertTrue(result["tool_hints_ok"])

    def test_check_plan_quality_tool_hint_mismatch(self) -> None:
        """plan 不含任何 required tool_hint 且无 None → tool_hints_ok=False。"""
        case = {
            "id": "t6",
            "goal": "g",
            "min_steps": 1,
            "max_steps": 3,
            "required_tool_hints": ["sql_query", "file_write"],
        }
        plan = _make_plan(
            steps_data=[
                {"id": "s1", "description": "步骤1", "tool_hint": "calc", "depends_on": []},
            ]
        )
        result = check_plan_quality(plan, case)
        self.assertFalse(result["tool_hints_ok"])

    def test_check_plan_quality_no_cycle(self) -> None:
        """正常 plan（无循环）→ no_cycle=True。"""
        case = {
            "id": "t7",
            "goal": "g",
            "min_steps": 1,
            "max_steps": 5,
            "required_tool_hints": None,
        }
        plan = _make_plan(
            steps_data=[
                {"id": "s1", "description": "a", "depends_on": []},
                {"id": "s2", "description": "b", "depends_on": ["s1"]},
                {"id": "s3", "description": "c", "depends_on": ["s2"]},
            ]
        )
        result = check_plan_quality(plan, case)
        self.assertTrue(result["no_cycle"])

    def test_check_plan_quality_with_cycle(self) -> None:
        """含循环依赖的 plan → no_cycle=False。"""
        case = {
            "id": "t8",
            "goal": "g",
            "min_steps": 1,
            "max_steps": 5,
            "required_tool_hints": None,
        }
        # Build plan with cycle s1->s2->s1
        steps = [
            _PlanStep(id="s1", description="a", depends_on=["s2"]),
            _PlanStep(id="s2", description="b", depends_on=["s1"]),
        ]
        plan = _AgentPlan(goal="g", steps=steps)
        result = check_plan_quality(plan, case)
        self.assertFalse(result["no_cycle"])

    def test_check_plan_quality_overall_pass(self) -> None:
        """全部条件满足 → overall_pass=True。"""
        case = {
            "id": "t9",
            "goal": "查询天气",
            "min_steps": 1,
            "max_steps": 3,
            "required_tool_hints": ["calc", None],
        }
        plan = _make_plan(
            steps_data=[
                {"id": "s1", "description": "查询", "tool_hint": "calc", "depends_on": []},
                {"id": "s2", "description": "汇总", "tool_hint": None, "depends_on": ["s1"]},
            ]
        )
        result = check_plan_quality(plan, case)
        self.assertTrue(result["overall_pass"])

    def test_check_plan_quality_overall_fail_steps(self) -> None:
        """步骤超出 max_steps → overall_pass=False。"""
        case = {
            "id": "t10",
            "goal": "简单任务",
            "min_steps": 1,
            "max_steps": 1,
            "required_tool_hints": None,
        }
        plan = _make_plan(
            steps_data=[
                {"id": "s1", "description": "步骤1", "depends_on": []},
                {"id": "s2", "description": "步骤2", "depends_on": ["s1"]},
            ]
        )
        result = check_plan_quality(plan, case)
        self.assertFalse(result["overall_pass"])


# ---------------------------------------------------------------------------
# Tests: run_gate
# ---------------------------------------------------------------------------


class TestRunGate(unittest.TestCase):
    def test_run_gate_mock_returns_dict(self) -> None:
        """run_gate(mock_generate=True) 返回包含 passed/failed/total 的 dict。"""
        result = run_gate(mock_generate=True)
        self.assertIsInstance(result, dict)
        self.assertIn("passed", result)
        self.assertIn("failed", result)
        self.assertIn("total", result)
        self.assertIn("results", result)

    def test_run_gate_total_equals_baseline_count(self) -> None:
        """run_gate 的 total 等于 baseline 用例数。"""
        cases = load_baseline()
        result = run_gate(mock_generate=True)
        self.assertEqual(result["total"], len(cases))

    def test_run_gate_passed_plus_failed_equals_total(self) -> None:
        """passed + failed == total。"""
        result = run_gate(mock_generate=True)
        self.assertEqual(result["passed"] + result["failed"], result["total"])

    def test_run_gate_all_pass_in_mock(self) -> None:
        """mock 模式下所有用例通过（mock plan 满足条件）。"""
        result = run_gate(mock_generate=True)
        self.assertEqual(result["failed"], 0, f"Some cases failed: {result['results']}")
        self.assertEqual(result["passed"], result["total"])

    def test_run_gate_results_have_required_keys(self) -> None:
        """每条结果含 id / overall_pass / steps_ok / tool_hints_ok / no_cycle。"""
        result = run_gate(mock_generate=True)
        for r in result["results"]:
            for key in ("id", "overall_pass", "steps_ok", "tool_hints_ok", "no_cycle"):
                self.assertIn(key, r, f"Result missing key '{key}': {r}")


# ---------------------------------------------------------------------------
# Tests: static_check_baseline
# ---------------------------------------------------------------------------


class TestStaticCheckBaseline(unittest.TestCase):
    def test_static_check_valid(self) -> None:
        """正式 baseline 文件静态校验通过。"""
        result = static_check_baseline()
        self.assertTrue(result["valid"], f"Errors: {result['errors']}")
        self.assertEqual(result["errors"], [])

    def test_static_check_missing_file(self) -> None:
        """不存在的文件 → valid=False。"""
        result = static_check_baseline("/nonexistent/path.jsonl")
        self.assertFalse(result["valid"])

    def test_static_check_too_few_cases(self) -> None:
        """少于 5 条用例 → valid=False。"""
        import tempfile

        data = [{"id": f"x{i}", "goal": f"g{i}", "min_steps": 1, "max_steps": 2} for i in range(3)]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            for d in data:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
            fname = f.name
        result = static_check_baseline(fname)
        self.assertFalse(result["valid"])


# ---------------------------------------------------------------------------
# Tests: topological sort
# ---------------------------------------------------------------------------


class TestTopologicalSort(unittest.TestCase):
    def test_topo_sort_simple_chain(self) -> None:
        """简单链式依赖 s1→s2→s3 拓扑排序正确。"""
        steps = [
            _PlanStep(id="s1", description="a", depends_on=[]),
            _PlanStep(id="s2", description="b", depends_on=["s1"]),
            _PlanStep(id="s3", description="c", depends_on=["s2"]),
        ]
        ordered = _topological_sort(steps)
        self.assertIsNotNone(ordered)
        assert ordered is not None
        self.assertEqual([s.id for s in ordered], ["s1", "s2", "s3"])

    def test_topo_sort_cycle_returns_none(self) -> None:
        """含环的 plan 返回 None。"""
        steps = [
            _PlanStep(id="s1", description="a", depends_on=["s2"]),
            _PlanStep(id="s2", description="b", depends_on=["s1"]),
        ]
        self.assertIsNone(_topological_sort(steps))


if __name__ == "__main__":
    unittest.main()
