#!/usr/bin/env python3
"""eval/plan_quality_gate.py — Phase Q Q6 规划质量门禁。

用法：
  python eval/plan_quality_gate.py run        # 跑全部用例（mock LLM）
  python eval/plan_quality_gate.py check      # 只做静态校验（不调 LLM）
  python eval/plan_quality_gate.py summary    # 打印 baseline 摘要
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE_PATH = REPO_ROOT / "eval" / "plan_baseline.jsonl"

# ---------------------------------------------------------------------------
# Lazy module loading helpers (avoid triggering packages.agent chain)
# ---------------------------------------------------------------------------


def _load_planner_module():
    """Lazy-load packages.agent.planner to avoid circular import issues."""
    key = "packages.agent.planner"
    if key not in sys.modules:
        # Ensure parent package stubs exist in sys.modules
        _ensure_package_stub("packages")
        _ensure_package_stub("packages.contracts")
        _ensure_package_stub("packages.agent")
        spec = importlib.util.spec_from_file_location(
            key, REPO_ROOT / "packages" / "agent" / "planner.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            # If full load fails, remove and re-raise
            sys.modules.pop(key, None)
            raise
    return sys.modules[key]


def _ensure_package_stub(package_name: str) -> None:
    """Register a minimal stub for a package if not already loaded."""
    if package_name not in sys.modules:
        import types

        mod = types.ModuleType(package_name)
        sys.modules[package_name] = mod


def _load_contracts_module():
    """Lazy-load packages.contracts.agent_schemas."""
    key = "packages.contracts.agent_schemas"
    if key not in sys.modules:
        _ensure_package_stub("packages")
        _ensure_package_stub("packages.contracts")
        spec = importlib.util.spec_from_file_location(
            key, REPO_ROOT / "packages" / "contracts" / "agent_schemas.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


# ---------------------------------------------------------------------------
# Inline data model (avoids importing pydantic chain in tests)
# ---------------------------------------------------------------------------


class _PlanStep:
    """Minimal PlanStep stand-in for mock usage in gate runner."""

    def __init__(
        self,
        id: str,
        description: str,
        tool_hint: str | None = None,
        agent_hint: str | None = None,
        depends_on: list[str] | None = None,
    ) -> None:
        self.id = id
        self.description = description
        self.tool_hint = tool_hint
        self.agent_hint = agent_hint
        self.depends_on = depends_on or []


class _AgentPlan:
    """Minimal AgentPlan stand-in for mock usage in gate runner."""

    def __init__(self, goal: str, steps: list[_PlanStep]) -> None:
        self.goal = goal
        self.steps = steps


# ---------------------------------------------------------------------------
# Topological sort (self-contained, no pydantic dependency)
# ---------------------------------------------------------------------------


def _topological_sort(steps: list[Any]) -> list[Any] | None:
    """Kahn topological sort. Returns None if cycle detected."""
    from collections import deque

    by_id = {s.id: s for s in steps}
    indegree = {s.id: 0 for s in steps}
    graph: dict[str, list[str]] = {s.id: [] for s in steps}
    for step in steps:
        for dep in step.depends_on:
            if dep in graph:
                graph[dep].append(step.id)
                indegree[step.id] += 1
            # Unknown deps are ignored here (validate_plan handles them)

    queue = deque([sid for sid, deg in indegree.items() if deg == 0])
    ordered: list[Any] = []
    while queue:
        sid = queue.popleft()
        ordered.append(by_id[sid])
        for nxt in graph[sid]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if len(ordered) != len(steps):
        return None
    return ordered


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def load_baseline(path: str | None = None) -> list[dict]:
    """加载 plan_baseline.jsonl，返回用例列表。

    Args:
        path: JSONL 文件路径，缺省使用 eval/plan_baseline.jsonl。

    Returns:
        用例字典列表，每个元素对应一条 JSONL 行。
    """
    p = Path(path) if path else DEFAULT_BASELINE_PATH
    if not p.is_file():
        raise FileNotFoundError(f"Baseline file not found: {p}")

    cases: list[dict] = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"plan_baseline.jsonl line {i}: invalid JSON — {exc}") from exc
        if not isinstance(obj, dict):
            raise ValueError(f"plan_baseline.jsonl line {i}: expected object, got {type(obj)}")
        cases.append(obj)
    return cases


def check_plan_quality(plan: Any, case: dict) -> dict:
    """对单个 baseline 用例验证 Plan 质量。

    Args:
        plan: AgentPlan 实例（或任意含 .goal / .steps 属性的对象）。
        case: baseline 用例字典，含 min_steps / max_steps / required_tool_hints。

    Returns:
        dict 包含：
          - steps_ok: 步骤数量在 [min_steps, max_steps] 范围内
          - tool_hints_ok: 若 case 有 required_tool_hints，至少一个 step 的
                           tool_hint 在列表中（None 表示任意）
          - no_cycle: 无循环依赖
          - overall_pass: 以上全部 True
    """
    steps = list(plan.steps)
    n = len(steps)

    # Step count check
    min_s = int(case.get("min_steps", 1))
    max_s = int(case.get("max_steps", 100))
    steps_ok = min_s <= n <= max_s

    # Tool hint check
    required_hints = case.get("required_tool_hints")
    if required_hints is None:
        # No constraint → always pass
        tool_hints_ok = True
    else:
        plan_hints = {s.tool_hint for s in steps if getattr(s, "tool_hint", None)}
        if None in required_hints:
            # None in list means "any tool_hint counts"
            tool_hints_ok = True
        else:
            # At least one required hint must appear in plan
            tool_hints_ok = any(h in plan_hints for h in required_hints if h is not None)

    # Cycle check
    no_cycle = _topological_sort(steps) is not None

    overall_pass = steps_ok and tool_hints_ok and no_cycle

    return {
        "id": case.get("id", ""),
        "goal": case.get("goal", ""),
        "steps_count": n,
        "steps_ok": steps_ok,
        "tool_hints_ok": tool_hints_ok,
        "no_cycle": no_cycle,
        "overall_pass": overall_pass,
    }


def _build_mock_plan_for_case(case: dict) -> _AgentPlan:
    """构建满足用例约束的 mock Plan（用于 mock_generate=True 模式）。"""
    goal = case.get("goal", "mock goal")
    min_s = int(case.get("min_steps", 1))
    # Use min_steps as step count
    n = max(min_s, 1)

    required_hints = case.get("required_tool_hints")
    steps: list[_PlanStep] = []
    for i in range(1, n + 1):
        sid = f"s{i}"
        deps: list[str] = [f"s{i - 1}"] if i > 1 else []

        # Assign tool_hint: use first non-None required hint on step 1
        tool_hint: str | None = None
        if required_hints and i == 1:
            non_none = [h for h in required_hints if h is not None]
            if non_none:
                tool_hint = non_none[0]

        steps.append(
            _PlanStep(
                id=sid,
                description=f"步骤 {i}: {goal[:20]}",
                tool_hint=tool_hint,
                depends_on=deps,
            )
        )

    return _AgentPlan(goal=goal, steps=steps)


def run_gate(mock_generate: bool = True) -> dict:
    """运行规划质量门禁。

    Args:
        mock_generate: True 时使用预设 mock Plan（不调真实 LLM）；
                       False 时尝试调用真实 LLM（需要外部 API）。

    Returns:
        dict 包含：passed / failed / total / results。
    """
    cases = load_baseline()
    results: list[dict] = []
    passed = 0
    failed = 0

    for case in cases:
        if mock_generate:
            plan = _build_mock_plan_for_case(case)
        else:
            # Real LLM path (requires API key; not used in tests)
            try:
                plan = _real_generate_plan(case)
            except Exception as exc:
                results.append(
                    {
                        "id": case.get("id", ""),
                        "overall_pass": False,
                        "error": str(exc),
                    }
                )
                failed += 1
                continue

        quality = check_plan_quality(plan, case)
        results.append(quality)
        if quality["overall_pass"]:
            passed += 1
        else:
            failed += 1

    return {
        "passed": passed,
        "failed": failed,
        "total": len(cases),
        "results": results,
    }


def _real_generate_plan(case: dict) -> Any:
    """调用真实 planner（仅在 mock_generate=False 时使用）。"""
    import asyncio

    planner = _load_planner_module()

    async def _inner():
        plan, _ = await planner.generate_plan(
            goal=case["goal"],
            allowed_models=(),
            allowed_tools=("calc", "get_kb_snippet", "web_search", "sql_query", "file_write"),
        )
        return plan

    return asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Static check (no LLM)
# ---------------------------------------------------------------------------


def static_check_baseline(path: str | None = None) -> dict:
    """静态校验 baseline 文件格式，不调用 LLM。

    Returns:
        dict 含 valid / errors / case_count。
    """
    errors: list[str] = []

    try:
        cases = load_baseline(path)
    except (FileNotFoundError, ValueError) as exc:
        return {"valid": False, "errors": [str(exc)], "case_count": 0}

    if len(cases) < 5:
        errors.append(f"baseline 用例数 {len(cases)} < 5，不足以覆盖基本场景")

    required_fields = {"id", "goal", "min_steps", "max_steps"}
    seen_ids: set[str] = set()
    for i, case in enumerate(cases, start=1):
        missing = required_fields - set(case.keys())
        if missing:
            errors.append(f"case {i}: 缺少字段 {missing}")
        cid = case.get("id", "")
        if cid in seen_ids:
            errors.append(f"case {i}: 重复 id '{cid}'")
        seen_ids.add(cid)
        if case.get("min_steps", 0) < 1:
            errors.append(f"case {i} ({cid}): min_steps 必须 >= 1")
        if case.get("max_steps", 0) < case.get("min_steps", 0):
            errors.append(
                f"case {i} ({cid}): max_steps ({case.get('max_steps')}) "
                f"< min_steps ({case.get('min_steps')})"
            )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "case_count": len(cases),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI 入口，支持 run / check / summary 三个子命令。"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Phase Q Q6 — Plan Quality Gate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="跑全部用例（mock LLM，不调外部 API）")
    sub.add_parser("check", help="静态校验 baseline 格式（无 LLM 依赖）")
    sub.add_parser("summary", help="打印 baseline 摘要")

    args = parser.parse_args()

    if args.command == "run":
        result = run_gate(mock_generate=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["failed"] == 0 else 1)

    if args.command == "check":
        result = static_check_baseline()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["valid"] else 1)

    if args.command == "summary":
        cases = load_baseline()
        print(f"Plan baseline: {len(cases)} cases")
        print(f"{'ID':<10} {'min':>4} {'max':>4}  {'goal'}")
        print("-" * 60)
        for c in cases:
            print(f"{c['id']:<10} {c['min_steps']:>4} {c['max_steps']:>4}  {c['goal'][:40]}")


if __name__ == "__main__":
    main()
