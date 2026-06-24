#!/usr/bin/env python3
"""Phase E1 / L #58：Agent 轨迹评测 — 四率指标 + baseline 报告。"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPO_ROOT / "eval" / "agent_baseline.jsonl"
RUNS_DIR = REPO_ROOT / "eval" / "runs" / "agent"

TENANT_TOKENS: dict[str, str] = {
    "admin": "sk-tenant-admin-change-me",
    "demo-a": "sk-tenant-demo-a-change-me",
    "demo-b": "sk-tenant-demo-b-change-me",
}

METRIC_KEYS = (
    "tool_precision_at_1",
    "needless_tool_rate",
    "missing_tool_rate",
    "arg_valid_rate",
)


@dataclass
class TrajectoryMetrics:
    first_tool_correct: bool | None = None
    needless_tool: bool = False
    missing_tool: bool = False
    arg_invalid: bool = False
    tool_names: list[str] = field(default_factory=list)


@dataclass
class AgentCaseResult:
    id: str
    tenant_id: str
    passed: bool
    reason: str
    http_status: int
    error_code: str | None = None
    final_message_preview: str | None = None
    tool_names: list[str] = field(default_factory=list)
    trajectory: TrajectoryMetrics = field(default_factory=TrajectoryMetrics)
    expect_tools: list[str] = field(default_factory=list)
    forbid_tools: list[str] = field(default_factory=list)
    expect_no_tools: bool = False
    expect_first_tool: str | None = None
    direct_answer: bool = False
    require_tools: bool = False


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        cases.append(json.loads(line))
    return cases


def _case_expect_tools(case: dict[str, Any]) -> list[str]:
    if case.get("expect_tools"):
        return [str(t) for t in case["expect_tools"]]
    expected = case.get("expected_tool")
    if expected:
        return [str(expected)]
    return []


def _case_direct_answer(case: dict[str, Any]) -> bool:
    if "direct_answer" in case:
        return bool(case["direct_answer"])
    return bool(case.get("expect_no_tools"))


def _case_require_tools(case: dict[str, Any]) -> bool:
    if "require_tools" in case:
        return bool(case["require_tools"])
    return bool(_case_expect_tools(case))


def _tool_names_from_body(body: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for tc in body.get("tool_calls") or []:
        if isinstance(tc, dict) and isinstance(tc.get("tool_name"), str):
            names.append(tc["tool_name"])
    return names


def _has_bad_args(tool_calls: list[Any]) -> bool:
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        err = tc.get("error") or ""
        if isinstance(err, str) and "AGENT_TOOL_BAD_ARGS" in err:
            return True
        if tc.get("status") == "failed" and isinstance(err, str) and "JSON" in err:
            return True
    return False


def evaluate_agent_case(
    case: dict[str, Any],
    *,
    status: int,
    body: dict[str, Any],
) -> tuple[bool, str, TrajectoryMetrics]:
    """评估单条 Agent 用例，返回 (passed, reason, trajectory_metrics)。"""
    expect = case.get("expect", "success")
    metrics = TrajectoryMetrics()

    if expect == "error":
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        code = err.get("code")
        expect_code = case.get("expect_error_code")
        if status == 200:
            return False, "期望错误响应但返回 200", metrics
        if expect_code and code != expect_code:
            return False, f"期望 error.code={expect_code} 实际 {code!r}", metrics
        return True, f"错误码符合预期 code={code}", metrics

    if status != 200:
        code = (body.get("error") or {}).get("code") if isinstance(body.get("error"), dict) else None
        return False, f"期望 200 但 status={status} code={code}", metrics

    if case.get("expect_reasoning_trace"):
        trace = body.get("reasoning_trace")
        if not isinstance(trace, list) or not trace:
            return False, "缺少 reasoning_trace", metrics

    tool_calls = body.get("tool_calls") if isinstance(body.get("tool_calls"), list) else []
    tool_names = _tool_names_from_body(body)
    metrics.tool_names = tool_names
    metrics.arg_invalid = _has_bad_args(tool_calls)

    expect_tools = _case_expect_tools(case)
    forbid_tools = [str(t) for t in (case.get("forbid_tools") or [])]
    expect_first = case.get("expect_first_tool")
    direct_answer = _case_direct_answer(case)
    require_tools = _case_require_tools(case)

    if direct_answer and tool_names:
        metrics.needless_tool = True

    forbidden_hit = [t for t in forbid_tools if t in tool_names]
    if forbidden_hit:
        metrics.needless_tool = True
        return False, f"禁止工具被调用: {forbidden_hit}", metrics

    if direct_answer and tool_names:
        return False, f"期望 direct_answer 不调用工具但调用了: {tool_names}", metrics

    if require_tools and not tool_names:
        metrics.missing_tool = True
        return False, "require_tools 但未调用任何工具", metrics

    if expect_tools and tool_names:
        first = tool_names[0]
        metrics.first_tool_correct = first in expect_tools
        if expect_first is not None:
            metrics.first_tool_correct = first == expect_first

    missing = [t for t in expect_tools if t not in tool_names]
    if missing:
        metrics.missing_tool = True
        if metrics.first_tool_correct is None and expect_tools and tool_names:
            metrics.first_tool_correct = tool_names[0] in expect_tools
        return False, f"缺少期望工具: {missing}，实际 {tool_names}", metrics

    if expect_tools and tool_names:
        first = tool_names[0]
        if expect_first is not None:
            if first != expect_first:
                return False, f"第一步工具期望 {expect_first!r} 实际 {first!r}", metrics
        elif first not in expect_tools:
            return False, f"第一步工具 {first!r} 不在 expect_tools {expect_tools}", metrics
    elif expect_first is not None:
        first = tool_names[0] if tool_names else None
        metrics.first_tool_correct = first == expect_first
        if first != expect_first:
            return False, f"第一步工具期望 {expect_first!r} 实际 {first!r}", metrics
    else:
        metrics.first_tool_correct = None

    final = body.get("final_message") if isinstance(body.get("final_message"), str) else ""
    for kw in case.get("assert_contains") or []:
        if kw not in final:
            return False, f"final_message 未包含 {kw!r}", metrics
    contains_any = case.get("assert_contains_any") or case.get("contains_any")
    if contains_any and not any(k in final for k in contains_any):
        return False, f"final_message 未包含任一关键词 {contains_any}", metrics

    return True, "轨迹与断言通过", metrics


def aggregate_trajectory_metrics(results: list[AgentCaseResult]) -> dict[str, Any]:
    """汇总四率 + pass_rate（Phase L #58 agent_metrics 数据源）。"""
    precision_cases = [
        r for r in results if r.expect_tools and r.trajectory.first_tool_correct is not None
    ]
    precision_ok = sum(1 for r in precision_cases if r.trajectory.first_tool_correct)

    needless_denom = [r for r in results if r.direct_answer]
    needless_bad = [r for r in needless_denom if r.trajectory.needless_tool]

    missing_denom = [r for r in results if r.require_tools]
    missing_bad = [r for r in missing_denom if r.trajectory.missing_tool]

    tool_call_cases = [r for r in results if r.trajectory.tool_names]
    arg_invalid_cases = [r for r in tool_call_cases if r.trajectory.arg_invalid]

    total = len(results)
    passed = sum(1 for r in results if r.passed)

    def rate(num: int, denom: int) -> float | None:
        return round(num / denom, 4) if denom else None

    metrics = {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": rate(passed, total) or 0.0,
        "tool_precision_at_1": rate(precision_ok, len(precision_cases)),
        "needless_tool_rate": rate(len(needless_bad), len(needless_denom)),
        "missing_tool_rate": rate(len(missing_bad), len(missing_denom)),
        "arg_valid_rate": rate(
            len(tool_call_cases) - len(arg_invalid_cases),
            len(tool_call_cases),
        ),
        "tool_precision_at_1_detail": {
            "correct": precision_ok,
            "total": len(precision_cases),
        },
        "needless_tool_cases": len(needless_bad),
        "missing_tool_cases": len(missing_bad),
        "arg_invalid_cases": len(arg_invalid_cases),
    }
    return metrics


def build_agent_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    return {k: summary.get(k) for k in METRIC_KEYS}


def _metrics_from_report(report: dict[str, Any]) -> dict[str, Any]:
    if isinstance(report.get("agent_metrics"), dict):
        return report["agent_metrics"]
    return report.get("summary", {})


def validate_agent_baseline(path: Path) -> tuple[bool, list[str]]:
    """校验 agent_baseline.jsonl 结构，无需运行服务。"""
    errors: list[str] = []
    if not path.is_file():
        return False, [f"文件不存在: {path}"]
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"行 {i}: JSON 解析失败 — {e}")
            continue
        if not isinstance(case, dict):
            errors.append(f"行 {i}: 每行须为 JSON 对象")
            continue
        if "id" not in case:
            errors.append(f"行 {i}: 缺少 id")
        if "tenant_id" not in case:
            errors.append(f"行 {i}: 缺少 tenant_id")
        if "session_id" not in case:
            errors.append(f"行 {i}: 缺少 session_id")
        if "messages" not in case:
            errors.append(f"行 {i}: 缺少 messages")
        elif not isinstance(case["messages"], list) or not case["messages"]:
            errors.append(f"行 {i}: messages 须为非空数组")
        expect = case.get("expect", "success")
        if expect not in ("success", "error"):
            errors.append(f"行 {i}: expect 须为 success 或 error，实际 {expect!r}")
        if expect == "error" and not case.get("expect_error_code"):
            errors.append(f"行 {i}: expect=error 时须提供 expect_error_code")
        tenant = case.get("tenant_id")
        if tenant and tenant not in TENANT_TOKENS:
            errors.append(f"行 {i}: 未知 tenant_id {tenant!r}，须在 {list(TENANT_TOKENS)}")
        for flag in ("direct_answer", "require_tools"):
            if flag in case and not isinstance(case[flag], bool):
                errors.append(f"行 {i}: {flag} 须为 bool")
        for list_key in ("expect_tools", "forbid_tools"):
            if list_key in case and not isinstance(case[list_key], list):
                errors.append(f"行 {i}: {list_key} 须为数组")
    return len(errors) == 0, errors


def save_report(report: dict[str, Any]) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{report['run_id']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def run_agent_baseline(
    *,
    base_url: str,
    baseline_path: Path,
    run_id: str | None,
    timeout: float,
    bearer_token: str | None,
) -> dict[str, Any]:
    cases = load_cases(baseline_path)
    run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results: list[AgentCaseResult] = []

    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        for case in cases:
            case_id = str(case.get("id", uuid.uuid4()))
            tenant_id = str(case["tenant_id"])
            token = bearer_token or TENANT_TOKENS.get(tenant_id, "")
            headers = {
                "Content-Type": "application/json",
                "X-Tenant-Id": tenant_id,
                "Authorization": f"Bearer {token}",
            }
            payload: dict[str, Any] = {
                "tenant_id": tenant_id,
                "session_id": case["session_id"],
                "messages": case["messages"],
            }
            if case.get("kb_id"):
                payload["kb_id"] = case["kb_id"]
            if case.get("reasoning_mode"):
                payload["reasoning_mode"] = case["reasoning_mode"]

            try:
                r = await client.post("/v1/agent/run", json=payload, headers=headers)
                body = r.json() if r.content else {}
            except Exception as e:
                results.append(
                    AgentCaseResult(
                        id=case_id,
                        tenant_id=tenant_id,
                        passed=False,
                        reason=f"请求异常: {e}",
                        http_status=0,
                    )
                )
                continue

            if not isinstance(body, dict):
                body = {}
            passed, reason, traj = evaluate_agent_case(case, status=r.status_code, body=body)
            err_code = (body.get("error") or {}).get("code") if isinstance(body.get("error"), dict) else None
            final = body.get("final_message") if isinstance(body.get("final_message"), str) else None
            expect_tools = _case_expect_tools(case)
            results.append(
                AgentCaseResult(
                    id=case_id,
                    tenant_id=tenant_id,
                    passed=passed,
                    reason=reason,
                    http_status=r.status_code,
                    error_code=err_code,
                    final_message_preview=(final[:120] if final else None),
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
    agent_metrics = build_agent_metrics(summary)
    report = {
        "run_id": run_id,
        "kind": "agent",
        "created_at": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "baseline": str(baseline_path),
        "summary": summary,
        "agent_metrics": agent_metrics,
        "results": [
            {
                **asdict(r),
                "trajectory": asdict(r.trajectory),
            }
            for r in results
        ],
    }
    return report


def compare_reports(path_a: Path, path_b: Path) -> dict[str, Any]:
    a = json.loads(path_a.read_text(encoding="utf-8"))
    b = json.loads(path_b.read_text(encoding="utf-8"))
    ma, mb = _metrics_from_report(a), _metrics_from_report(b)
    rate_a = a.get("summary", {}).get("pass_rate", 0)
    rate_b = b.get("summary", {}).get("pass_rate", 0)
    by_id_a = {r["id"]: r for r in a.get("results", [])}
    by_id_b = {r["id"]: r for r in b.get("results", [])}
    flipped: list[dict[str, str]] = []
    for cid in sorted(set(by_id_a) & set(by_id_b)):
        pa, pb = by_id_a[cid]["passed"], by_id_b[cid]["passed"]
        if pa != pb:
            flipped.append(
                {
                    "id": cid,
                    "before": "pass" if pa else "fail",
                    "after": "pass" if pb else "fail",
                }
            )

    def metric_delta(key: str) -> dict[str, Any]:
        va = ma.get(key)
        vb = mb.get(key)
        if va is None or vb is None:
            return {"a": va, "b": vb, "delta": None}
        return {"a": va, "b": vb, "delta": round(vb - va, 4) if isinstance(va, (int, float)) else None}

    agent_metrics_delta = {k: metric_delta(k) for k in METRIC_KEYS}
    return {
        "kind": "agent",
        "run_a": a.get("run_id"),
        "run_b": b.get("run_id"),
        "pass_rate_a": rate_a,
        "pass_rate_b": rate_b,
        "pass_rate_delta": round(rate_b - rate_a, 4),
        "flipped_cases": flipped,
        "agent_metrics": agent_metrics_delta,
        "trajectory_metrics": agent_metrics_delta,
    }


async def _async_main(args: argparse.Namespace) -> int:
    if args.command == "compare":
        diff = compare_reports(Path(args.report_a), Path(args.report_b))
        print(json.dumps(diff, ensure_ascii=False, indent=2))
        return 0

    if args.command == "validate-baseline":
        ok, errors = validate_agent_baseline(Path(args.baseline))
        if ok:
            print(json.dumps({"valid": True, "baseline": str(args.baseline)}, ensure_ascii=False))
            return 0
        print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1

    report = await run_agent_baseline(
        base_url=args.base_url,
        baseline_path=Path(args.baseline),
        run_id=args.run_id,
        timeout=args.timeout,
        bearer_token=args.bearer_token,
    )
    out = save_report(report)
    summary = report["summary"]
    print(json.dumps({"summary": summary, "agent_metrics": report["agent_metrics"]}, ensure_ascii=False, indent=2))
    print(f"报告已写入: {out}")
    if summary["failed"] > 0:
        return 1
    min_rate = getattr(args, "min_pass_rate", None)
    if min_rate is not None and summary["pass_rate"] < min_rate:
        print(
            json.dumps(
                {
                    "gate": "failed",
                    "pass_rate": summary["pass_rate"],
                    "min_pass_rate": min_rate,
                },
                ensure_ascii=False,
            )
        )
        return 1
    if min_rate is not None:
        print(
            json.dumps(
                {
                    "gate": "passed",
                    "pass_rate": summary["pass_rate"],
                    "min_pass_rate": min_rate,
                },
                ensure_ascii=False,
            )
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent 轨迹 baseline 评测")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="执行 agent_baseline.jsonl")
    run_p.add_argument("--base-url", default="http://127.0.0.1:8000")
    run_p.add_argument("--bearer-token", default=None, help="覆盖所有租户 token（默认按 tenant_id 选）")
    run_p.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    run_p.add_argument("--run-id", default=None)
    run_p.add_argument("--timeout", type=float, default=180.0)
    run_p.add_argument(
        "--min-pass-rate",
        type=float,
        default=None,
        help="通过率门禁：低于该值 exit 1（如 0.8）",
    )

    val_p = sub.add_parser("validate-baseline", help="校验 agent_baseline.jsonl（无需服务）")
    val_p.add_argument("--baseline", default=str(DEFAULT_BASELINE))

    cmp_p = sub.add_parser("compare", help="对比两次 Agent 评测报告")
    cmp_p.add_argument("report_a")
    cmp_p.add_argument("report_b")

    args = parser.parse_args()
    if args.command == "validate-baseline":
        ok, errors = validate_agent_baseline(Path(args.baseline))
        if ok:
            print(json.dumps({"valid": True, "baseline": str(args.baseline)}, ensure_ascii=False))
            raise SystemExit(0)
        print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    if args.command == "compare":
        diff = compare_reports(Path(args.report_a), Path(args.report_b))
        print(json.dumps(diff, ensure_ascii=False, indent=2))
        raise SystemExit(0)

    import asyncio

    raise SystemExit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
