#!/usr/bin/env python3
"""Phase O #95 — Agent JD2 能力矩阵 CI 门禁（离线，无 Gateway / LLM Key）。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PYTHON = sys.executable


@dataclass(frozen=True)
class Jd2GateCheck:
    name: str
    issue: str
    command: tuple[str, ...]
    description: str = ""


@dataclass
class Jd2GateResult:
    name: str
    issue: str
    passed: bool
    duration_ms: float
    returncode: int
    detail: str


CHECKS: tuple[Jd2GateCheck, ...] = (
    Jd2GateCheck(
        "planner_unit",
        "O1",
        (PYTHON, "-m", "unittest", "tests.test_agent_planner", "-q"),
        "Task Planner 单测",
    ),
    Jd2GateCheck(
        "planner_smoke",
        "O1",
        (PYTHON, str(REPO_ROOT / "eval" / "agent_planner_smoke.py")),
        "Planner mock smoke",
    ),
    Jd2GateCheck(
        "auto_plan_vertical",
        "O1+O9",
        (PYTHON, str(REPO_ROOT / "eval" / "auto_plan_vertical.py"), "--mock"),
        "O1 auto_plan + 数据分析 vertical 闭环",
    ),
    Jd2GateCheck(
        "reasoning_unit",
        "O2",
        (PYTHON, "-m", "unittest", "tests.test_agent_reasoning", "-q"),
        "CoT reasoning 单测",
    ),
    Jd2GateCheck(
        "cot_smoke",
        "O2",
        (PYTHON, str(REPO_ROOT / "eval" / "agent_cot_smoke.py")),
        "CoT mock smoke",
    ),
    Jd2GateCheck(
        "blackboard_unit",
        "O4",
        (PYTHON, "-m", "unittest", "tests.test_multi_agent_blackboard", "-q"),
        "Multi-Agent v2 黑板",
    ),
    Jd2GateCheck(
        "plugins_unit",
        "O5",
        (PYTHON, "-m", "unittest", "tests.test_agent_plugins", "-q"),
        "Plugin manifest",
    ),
    Jd2GateCheck(
        "web_search_unit",
        "O6",
        (PYTHON, "-m", "unittest", "tests.test_tools_web_search", "-q"),
        "web_search 工具",
    ),
    Jd2GateCheck(
        "sql_query_unit",
        "O7",
        (PYTHON, "-m", "unittest", "tests.test_tools_sql_query", "-q"),
        "sql_query 工具",
    ),
    Jd2GateCheck(
        "data_analysis_vertical",
        "O9",
        (PYTHON, str(REPO_ROOT / "eval" / "data_analysis_vertical.py"), "--mock"),
        "数据分析 vertical mock",
    ),
    Jd2GateCheck(
        "agent_perf_unit",
        "O10",
        (PYTHON, "-m", "unittest", "tests.test_agent_perf", "-q"),
        "并行工具 + metrics",
    ),
    Jd2GateCheck(
        "agent_trajectory_gate",
        "O11",
        (PYTHON, str(REPO_ROOT / "eval" / "agent_gate.py"), "run-offline", "--threshold", "5"),
        "Agent 轨迹回归 gate",
    ),
)

REQUIRED_PATHS: tuple[tuple[str, Path], ...] = (
    ("planner", REPO_ROOT / "packages" / "agent" / "planner.py"),
    ("reasoning", REPO_ROOT / "packages" / "agent" / "reasoning.py"),
    ("blackboard", REPO_ROOT / "packages" / "agent" / "multi_agent" / "blackboard.py"),
    ("plugins_loader", REPO_ROOT / "packages" / "agent" / "plugins" / "loader.py"),
    ("web_search", REPO_ROOT / "packages" / "agent" / "tools" / "web_search.py"),
    ("sql_query", REPO_ROOT / "packages" / "agent" / "tools" / "sql_query.py"),
    ("data_analysis_workflow", REPO_ROOT / "config" / "workflows" / "data_analysis.yaml"),
    ("perf_metrics", REPO_ROOT / "packages" / "agent" / "perf_metrics.py"),
    ("phase_o_doc", REPO_ROOT / "docs" / "phase-o-agent-jd2-alignment.md"),
)


def verify_required_paths() -> list[str]:
    missing = [label for label, path in REQUIRED_PATHS if not path.is_file()]
    return missing


def run_check(check: Jd2GateCheck) -> Jd2GateResult:
    t0 = time.perf_counter()
    proc = subprocess.run(
        list(check.command),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    duration_ms = round((time.perf_counter() - t0) * 1000, 2)
    detail = (proc.stdout or proc.stderr or "").strip()
    if len(detail) > 400:
        detail = detail[:400] + "..."
    return Jd2GateResult(
        name=check.name,
        issue=check.issue,
        passed=proc.returncode == 0,
        duration_ms=duration_ms,
        returncode=proc.returncode,
        detail=detail,
    )


def run_gate(*, checks: tuple[Jd2GateCheck, ...] = CHECKS) -> tuple[bool, list[Jd2GateResult]]:
    missing = verify_required_paths()
    if missing:
        result = Jd2GateResult(
            name="required_paths",
            issue="O11",
            passed=False,
            duration_ms=0.0,
            returncode=1,
            detail=f"missing: {', '.join(missing)}",
        )
        return False, [result]

    results = [run_check(c) for c in checks]
    passed = all(r.passed for r in results)
    return passed, results


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase O Agent JD2 gate (#95)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出检查项")

    run_p = sub.add_parser("run", help="运行全部离线检查")
    run_p.add_argument("--json", action="store_true", help="JSON 输出")

    args = parser.parse_args()

    if args.command == "list":
        payload = [
            {
                "name": c.name,
                "issue": c.issue,
                "description": c.description,
                "command": list(c.command),
            }
            for c in CHECKS
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(0)

    passed, results = run_gate()
    summary = {
        "passed": passed,
        "total": len(results),
        "failed": [r.name for r in results if not r.passed],
        "results": [asdict(r) for r in results],
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        for r in results:
            mark = "OK" if r.passed else "FAIL"
            print(f"[{mark}] {r.issue} {r.name} ({r.duration_ms}ms)")
            if not r.passed and r.detail:
                print(f"       {r.detail}")
        print(
            f"\nAgent JD2 gate: {'PASSED' if passed else 'FAILED'} "
            f"({len(results) - len(summary['failed'])}/{len(results)})"
        )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
