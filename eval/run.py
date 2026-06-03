#!/usr/bin/env python3
"""第 5 周：读取 baseline.jsonl，调用 RAG 问答 API，输出 pass/fail 报告。"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
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
    run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
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
        "created_at": datetime.now(UTC).isoformat(),
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

    report = await run_baseline(
        base_url=args.base_url,
        tenant_id=args.tenant_id,
        bearer_token=args.bearer_token,
        baseline_path=Path(args.baseline),
        run_id=args.run_id,
        timeout=args.timeout,
    )
    out = save_report(report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"报告已写入: {out}")
    return 0 if report["summary"]["failed"] == 0 else 1


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

    cmp_p = sub.add_parser("compare", help="对比两次评测报告")
    cmp_p.add_argument("report_a")
    cmp_p.add_argument("report_b")

    args = parser.parse_args()
    if args.command == "run":
        import asyncio

        raise SystemExit(asyncio.run(_async_main(args)))
    if args.command == "compare":
        diff = compare_reports(Path(args.report_a), Path(args.report_b))
        print(json.dumps(diff, ensure_ascii=False, indent=2))
        raise SystemExit(0)


if __name__ == "__main__":
    main()
