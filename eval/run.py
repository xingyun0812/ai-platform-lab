#!/usr/bin/env python3
"""第 5 周：读取 baseline.jsonl，调用 RAG 问答 API，输出 pass/fail 报告。
Phase J 扩展：run-eval 子命令运行完整评测 Pipeline，gate 子命令运行 CI 门禁。
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPO_ROOT / "eval" / "baseline.jsonl"
RUNS_DIR = REPO_ROOT / "eval" / "runs"


@dataclass
class CaseResult:
    id: str
    expect: str
    passed: bool
    reason: str
    http_status: int
    error_code: str | None = None
    answer_preview: str | None = None


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        cases.append(json.loads(line))
    return cases


def evaluate_case(case: dict[str, Any], *, status: int, body: dict[str, Any]) -> tuple[bool, str]:
    expect = case.get("expect", "hit")
    if expect == "hit":
        if status != 200:
            code = (body.get("error") or {}).get("code")
            return False, f"期望命中但 status={status} code={code}"
        answer = body.get("answer") or ""
        contains_any = case.get("contains_any")
        if contains_any:
            if not any(k in answer for k in contains_any):
                return False, f"answer 未包含关键词 {contains_any}"
        return True, "命中"

    if expect == "refuse":
        if status == 200:
            return False, "期望拒答但返回 200"
        err = body.get("error") or {}
        code = err.get("code")
        allowed = case.get("refuse_codes")
        if allowed and code not in allowed:
            return False, f"拒答码 {code} 不在期望列表 {allowed}"
        if status not in (422, 404, 403):
            return False, f"非预期拒答 HTTP status={status}"
        return True, f"拒答 code={code}"

    return False, f"未知 expect={expect}"


async def run_baseline(
    *,
    base_url: str,
    tenant_id: str,
    bearer_token: str,
    baseline_path: Path,
    run_id: str | None,
    timeout: float,
) -> dict[str, Any]:
    cases = load_cases(baseline_path)
    run_id = run_id or datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    headers = {
        "Content-Type": "application/json",
        "X-Tenant-Id": tenant_id,
        "Authorization": f"Bearer {bearer_token}",
    }
    results: list[CaseResult] = []

    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        for case in cases:
            case_id = str(case.get("id", uuid.uuid4()))
            payload: dict[str, Any] = {
                "tenant_id": tenant_id,
                "kb_id": case["kb_id"],
                "query": case["query"],
                "top_k": case.get("top_k", 5),
            }
            if case.get("version") is not None:
                payload["version"] = case["version"]
            if case.get("min_score") is not None:
                payload["min_score"] = case["min_score"]

            try:
                r = await client.post("/v1/rag/query", json=payload, headers=headers)
                body = r.json() if r.content else {}
            except Exception as e:
                results.append(
                    CaseResult(
                        id=case_id,
                        expect=str(case.get("expect", "")),
                        passed=False,
                        reason=f"请求异常: {e}",
                        http_status=0,
                    )
                )
                continue

            if not isinstance(body, dict):
                body = {}
            passed, reason = evaluate_case(case, status=r.status_code, body=body)
            err_code = (body.get("error") or {}).get("code") if isinstance(body.get("error"), dict) else None
            answer = body.get("answer") if isinstance(body.get("answer"), str) else None
            results.append(
                CaseResult(
                    id=case_id,
                    expect=str(case.get("expect", "")),
                    passed=passed,
                    reason=reason,
                    http_status=r.status_code,
                    error_code=err_code,
                    answer_preview=(answer[:120] if answer else None),
                )
            )

    passed_n = sum(1 for r in results if r.passed)
    total = len(results)
    report = {
        "run_id": run_id,
        "created_at": datetime.utcnow().isoformat(),
        "base_url": base_url,
        "tenant_id": tenant_id,
        "baseline": str(baseline_path),
        "summary": {
            "total": total,
            "passed": passed_n,
            "failed": total - passed_n,
            "pass_rate": round(passed_n / total, 4) if total else 0.0,
        },
        "results": [asdict(r) for r in results],
    }
    return report


def save_report(report: dict[str, Any]) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{report['run_id']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def validate_baseline(path: Path) -> tuple[bool, list[str]]:
    """校验 baseline.jsonl 结构，无需运行服务。"""
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
        if "kb_id" not in case:
            errors.append(f"行 {i}: 缺少 kb_id")
        if "query" not in case:
            errors.append(f"行 {i}: 缺少 query")
        expect = case.get("expect", "hit")
        if expect not in ("hit", "refuse"):
            errors.append(f"行 {i}: expect 须为 hit 或 refuse，实际 {expect!r}")
    return len(errors) == 0, errors


def compare_reports(path_a: Path, path_b: Path) -> dict[str, Any]:
    a = json.loads(path_a.read_text(encoding="utf-8"))
    b = json.loads(path_b.read_text(encoding="utf-8"))
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
    return {
        "run_a": a.get("run_id"),
        "run_b": b.get("run_id"),
        "pass_rate_a": rate_a,
        "pass_rate_b": rate_b,
        "pass_rate_delta": round(rate_b - rate_a, 4),
        "flipped_cases": flipped,
    }


async def _async_main(args: argparse.Namespace) -> int:
    if args.command == "compare":
        diff = compare_reports(Path(args.report_a), Path(args.report_b))
        print(json.dumps(diff, ensure_ascii=False, indent=2))
        return 0

    if args.command == "validate-baseline":
        ok, errors = validate_baseline(Path(args.baseline))
        if ok:
            print(json.dumps({"valid": True, "baseline": str(args.baseline)}, ensure_ascii=False))
            return 0
        print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1

    report = await run_baseline(
        base_url=args.base_url,
        tenant_id=args.tenant_id,
        bearer_token=args.bearer_token,
        baseline_path=Path(args.baseline),
        run_id=args.run_id,
        timeout=args.timeout,
    )
    out = save_report(report)
    summary = report["summary"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
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


def _cmd_run_eval(args: argparse.Namespace) -> int:
    """run-eval subcommand: 运行完整 Pipeline 并输出报告。"""
    import importlib.util

    EVAL_DIR = REPO_ROOT / "eval"

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    import sys as _sys
    _load("eval.pipeline", EVAL_DIR / "pipeline.py")
    _load("eval.report", EVAL_DIR / "report.py")

    from eval.pipeline import EvalPipeline  # noqa: F401
    from eval.report import save_report as save_eval_report  # noqa: F401

    gateway_url = getattr(args, "gateway_url", None) or os.environ.get(
        "EVAL_GATEWAY_URL", "http://127.0.0.1:8000"
    )
    api_key = getattr(args, "api_key", None) or os.environ.get("EVAL_API_KEY")
    categories_arg = getattr(args, "categories", None)
    cats = categories_arg.split(",") if categories_arg else None
    sample_limit = getattr(args, "sample_limit", None)

    pipeline = EvalPipeline(gateway_url=gateway_url, api_key=api_key)
    report = pipeline.run_all(categories=cats, sample_limit=sample_limit)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_path = RUNS_DIR / f"eval_{ts}"
    md_path, json_path = save_eval_report(report, out_path)

    summary = {
        "total_cases": report.total_cases,
        "passed": report.passed,
        "failed": report.failed,
        "skipped": report.skipped,
        "pass_rate": report.pass_rate,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"报告已写入: {json_path}")
    print(f"Markdown 报告: {md_path}")

    return 1 if report.failed > 0 else 0


def _cmd_gate(args: argparse.Namespace) -> int:
    """gate subcommand: 与 main baseline 对比，超过阈值则 exit 1。"""
    import importlib.util
    import sys as _sys

    EVAL_DIR = REPO_ROOT / "eval"

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        _sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    _load("eval.pipeline", EVAL_DIR / "pipeline.py")
    _load("eval.gate", EVAL_DIR / "gate.py")

    from eval.pipeline import EvalPipeline  # noqa: F401
    from eval.gate import check_gate  # noqa: F401

    gateway_url = getattr(args, "gateway_url", None) or os.environ.get(
        "EVAL_GATEWAY_URL", "http://127.0.0.1:8000"
    )
    api_key = getattr(args, "api_key", None) or os.environ.get("EVAL_API_KEY")
    threshold = float(getattr(args, "threshold", 5.0))
    baseline_path = Path(
        getattr(args, "baseline_path", None)
        or REPO_ROOT / "eval" / "baselines" / "main_baseline.json"
    )
    categories_arg = getattr(args, "categories", None)
    cats = categories_arg.split(",") if categories_arg else None
    sample_limit = getattr(args, "sample_limit", None)

    pipeline = EvalPipeline(gateway_url=gateway_url, api_key=api_key)
    report = pipeline.run_all(categories=cats, sample_limit=sample_limit)

    gate = check_gate(report, baseline_path=baseline_path, threshold_pct=threshold)

    output = {
        "gate_passed": gate.passed,
        "reason": gate.reason,
        "delta_pct": gate.delta,
        "threshold_pct": gate.threshold,
        "current_pass_rate": gate.current_pass_rate,
        "baseline_pass_rate": gate.baseline_pass_rate,
        "report_summary": {
            "total_cases": report.total_cases,
            "passed": report.passed,
            "failed": report.failed,
            "skipped": report.skipped,
            "pass_rate": report.pass_rate,
        },
        "by_category": gate.by_category,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))

    return 0 if gate.passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG baseline 评测")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="执行 baseline.jsonl")
    run_p.add_argument("--base-url", default="http://127.0.0.1:8000")
    run_p.add_argument("--tenant-id", default="admin")
    run_p.add_argument("--bearer-token", default="sk-tenant-admin-change-me")
    run_p.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    run_p.add_argument("--run-id", default=None)
    run_p.add_argument("--timeout", type=float, default=120.0)
    run_p.add_argument(
        "--min-pass-rate",
        type=float,
        default=None,
        help="通过率门禁：低于该值 exit 1（如 0.85）",
    )

    val_p = sub.add_parser("validate-baseline", help="校验 baseline.jsonl 格式（无需服务）")
    val_p.add_argument("--baseline", default=str(DEFAULT_BASELINE))

    cmp_p = sub.add_parser("compare", help="对比两次评测报告")
    cmp_p.add_argument("report_a")
    cmp_p.add_argument("report_b")

    # Phase J: run-eval subcommand
    reval_p = sub.add_parser("run-eval", help="运行完整 Eval Pipeline（RAG/Agent/Safety）")
    reval_p.add_argument("--gateway-url", default=None, help="Gateway URL（默认 http://127.0.0.1:8000）")
    reval_p.add_argument("--api-key", default=None, help="API key（无则跳过 live 用例）")
    reval_p.add_argument("--categories", default=None, help="逗号分隔类别（默认全部）")
    reval_p.add_argument("--sample-limit", type=int, default=None, help="每类别最多用例数")

    # Phase J: gate subcommand
    gate_p = sub.add_parser("gate", help="运行 CI 门禁对比（超过阈值则 exit 1）")
    gate_p.add_argument("--threshold", type=float, default=5.0, help="回退阈值百分点（默认 5）")
    gate_p.add_argument("--baseline-path", default=None, help="main baseline JSON 路径")
    gate_p.add_argument("--gateway-url", default=None)
    gate_p.add_argument("--api-key", default=None)
    gate_p.add_argument("--categories", default=None)
    gate_p.add_argument("--sample-limit", type=int, default=None)

    args = parser.parse_args()
    if args.command == "run":
        import asyncio

        raise SystemExit(asyncio.run(_async_main(args)))
    if args.command == "validate-baseline":
        ok, errors = validate_baseline(Path(args.baseline))
        if ok:
            print(json.dumps({"valid": True, "baseline": str(args.baseline)}, ensure_ascii=False))
            raise SystemExit(0)
        print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    if args.command == "compare":
        diff = compare_reports(Path(args.report_a), Path(args.report_b))
        print(json.dumps(diff, ensure_ascii=False, indent=2))
        raise SystemExit(0)

    if args.command == "run-eval":
        raise SystemExit(_cmd_run_eval(args))

    if args.command == "gate":
        raise SystemExit(_cmd_gate(args))


if __name__ == "__main__":
    main()
