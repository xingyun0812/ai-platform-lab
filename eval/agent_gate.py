#!/usr/bin/env python3
"""Phase L #60 — Agent 轨迹评测 CI 门禁（与 eval/gate.py RAG 门禁对称）。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIOS = REPO_ROOT / "eval" / "baselines" / "agent_scenarios.jsonl"
DEFAULT_TRAJECTORY_BASELINE = REPO_ROOT / "eval" / "baselines" / "agent_trajectory_gate.json"
DEFAULT_FIXTURES = REPO_ROOT / "eval" / "baselines" / "agent_gate_fixtures.jsonl"
DEFAULT_AGENT_BASELINE = REPO_ROOT / "eval" / "agent_baseline.jsonl"
MAIN_BASELINE = REPO_ROOT / "eval" / "baselines" / "main_baseline.json"

METRIC_KEYS = (
    "tool_precision_at_1",
    "needless_tool_rate",
    "missing_tool_rate",
    "arg_valid_rate",
)


@dataclass
class AgentGateResult:
    passed: bool
    reason: str
    pass_rate_delta_pp: float
    threshold_pct: float
    current_pass_rate: float
    baseline_pass_rate: float
    metric_deltas_pp: dict[str, float | None]


def _rate_delta_pp(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline is None:
        return None
    return round((current - baseline) * 100, 2)


def check_agent_gate(
    current: dict[str, Any],
    baseline: dict[str, Any],
    *,
    threshold_pct: float = 5.0,
) -> AgentGateResult:
    """pass_rate 回退超过 threshold_pct（百分点）则失败。"""
    cur_pr = float(current.get("pass_rate", 0.0))
    base_pr = float(baseline.get("pass_rate", cur_pr))
    delta_pp = round((cur_pr - base_pr) * 100, 2)
    passed = delta_pp > -threshold_pct

    cur_metrics = current.get("agent_metrics") or {}
    base_metrics = baseline.get("agent_metrics") or {}
    metric_deltas = {
        k: _rate_delta_pp(cur_metrics.get(k), base_metrics.get(k)) for k in METRIC_KEYS
    }

    if passed:
        reason = f"Agent gate PASSED: pass_rate delta={delta_pp:+.2f}pp (threshold=-{threshold_pct:.1f}pp)"
    else:
        reason = (
            f"Agent gate FAILED: pass_rate delta={delta_pp:+.2f}pp "
            f"exceeds regression threshold -{threshold_pct:.1f}pp"
        )

    return AgentGateResult(
        passed=passed,
        reason=reason,
        pass_rate_delta_pp=delta_pp,
        threshold_pct=threshold_pct,
        current_pass_rate=cur_pr,
        baseline_pass_rate=base_pr,
        metric_deltas_pp=metric_deltas,
    )


def validate_agent_scenarios(path: Path = DEFAULT_SCENARIOS) -> tuple[bool, list[str]]:
    """校验 agent_scenarios.jsonl 数量与三率字段覆盖。"""
    errors: list[str] = []
    if not path.is_file():
        return False, [f"文件不存在: {path}"]

    rows: list[dict[str, Any]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"行 {i}: JSON 无效 — {e}")
            continue
        if not isinstance(row, dict):
            errors.append(f"行 {i}: 须为对象")
            continue
        rows.append(row)

    if len(rows) < 30:
        errors.append(f"用例数 {len(rows)} < 30")

    require_n = sum(1 for r in rows if r.get("require_tools"))
    direct_n = sum(1 for r in rows if r.get("direct_answer"))
    if require_n < 5:
        errors.append(f"require_tools 用例过少: {require_n}")
    if direct_n < 5:
        errors.append(f"direct_answer 用例过少: {direct_n}")

    return len(errors) == 0, errors


def load_report_summary(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "summary" in data:
        summary = data["summary"]
        metrics = data.get("agent_metrics") or {
            k: summary.get(k) for k in METRIC_KEYS if k in summary
        }
        return {"pass_rate": summary.get("pass_rate", 0.0), "agent_metrics": metrics}
    return {
        "pass_rate": data.get("pass_rate", 0.0),
        "agent_metrics": data.get("agent_metrics", {}),
    }


def run_offline_gate(
    *,
    baseline_path: Path = DEFAULT_TRAJECTORY_BASELINE,
    fixtures_path: Path = DEFAULT_FIXTURES,
    cases_path: Path = DEFAULT_AGENT_BASELINE,
    threshold_pct: float = 5.0,
) -> AgentGateResult:
    """用 mock fixture 跑 agent_baseline 轨迹评测，与 gate baseline 对比。"""
    from eval.agent_run import aggregate_trajectory_metrics, evaluate_agent_case

    fixtures: dict[str, dict[str, Any]] = {}
    for line in fixtures_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        fixtures[str(item["case_id"])] = item

    from eval.agent_run import AgentCaseResult, TrajectoryMetrics, _case_direct_answer, _case_expect_tools, _case_require_tools

    results: list[AgentCaseResult] = []
    for line in cases_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        case_id = str(case["id"])
        fx = fixtures.get(case_id)
        if not fx:
            raise ValueError(f"fixture 缺失: {case_id}")
        status = int(fx.get("status", 200))
        body = fx.get("body") if isinstance(fx.get("body"), dict) else {}
        passed, reason, traj = evaluate_agent_case(case, status=status, body=body)
        expect_tools = _case_expect_tools(case)
        results.append(
            AgentCaseResult(
                id=case_id,
                tenant_id=str(case.get("tenant_id", "admin")),
                passed=passed,
                reason=reason,
                http_status=status,
                tool_names=traj.tool_names,
                trajectory=traj,
                expect_tools=expect_tools,
                forbid_tools=[str(t) for t in (case.get("forbid_tools") or [])],
                expect_no_tools=bool(case.get("expect_no_tools")),
                expect_first_tool=case.get("expect_first_tool"),
                direct_answer=_case_direct_answer(case),
                require_tools=_case_require_tools(case),
            )
        )

    summary = aggregate_trajectory_metrics(results)
    current = {
        "pass_rate": summary["pass_rate"],
        "agent_metrics": {k: summary.get(k) for k in METRIC_KEYS},
    }
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    return check_agent_gate(current, baseline, threshold_pct=threshold_pct)


def sync_main_baseline_agent_section(
    trajectory_baseline_path: Path = DEFAULT_TRAJECTORY_BASELINE,
    main_path: Path = MAIN_BASELINE,
) -> None:
    """将 agent_trajectory_gate 同步进 main_baseline.json 的 agent_scenarios 段。"""
    traj = json.loads(trajectory_baseline_path.read_text(encoding="utf-8"))
    main = json.loads(main_path.read_text(encoding="utf-8"))
    cats = main.setdefault("categories", {})
    agent = cats.setdefault("agent_scenarios", {})
    agent["pass_rate"] = traj.get("pass_rate", agent.get("pass_rate", 0.7))
    agent["agent_metrics"] = traj.get("agent_metrics", {})
    agent["total_cases"] = 51
    agent["note"] = "Phase L #60 — pipeline pass_rate + trajectory gate baseline"
    main_path.write_text(json.dumps(main, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent eval CI gate (#60)")
    sub = parser.add_subparsers(dest="command", required=True)

    val = sub.add_parser("validate", help="校验 agent_scenarios.jsonl")
    val.add_argument("--scenarios", default=str(DEFAULT_SCENARIOS))

    chk = sub.add_parser("check", help="对比两份 agent_run 报告")
    chk.add_argument("report_a")
    chk.add_argument("report_b", nargs="?", default=None, help="省略则 report_b 为 baseline JSON")
    chk.add_argument("--threshold", type=float, default=5.0)
    chk.add_argument("--baseline", default=str(DEFAULT_TRAJECTORY_BASELINE))

    off = sub.add_parser("run-offline", help="fixture 离线 gate（CI 无 Key）")
    off.add_argument("--threshold", type=float, default=5.0)

    args = parser.parse_args()

    if args.command == "validate":
        ok, errors = validate_agent_scenarios(Path(args.scenarios))
        print(json.dumps({"valid": ok, "errors": errors}, ensure_ascii=False, indent=2))
        raise SystemExit(0 if ok else 1)

    if args.command == "check":
        current = load_report_summary(Path(args.report_a))
        if args.report_b:
            baseline = load_report_summary(Path(args.report_b))
        else:
            baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        gate = check_agent_gate(current, baseline, threshold_pct=args.threshold)
        print(json.dumps(asdict(gate), ensure_ascii=False, indent=2))
        raise SystemExit(0 if gate.passed else 1)

    gate = run_offline_gate(threshold_pct=args.threshold)
    print(json.dumps(asdict(gate), ensure_ascii=False, indent=2))
    raise SystemExit(0 if gate.passed else 1)


if __name__ == "__main__":
    main()
