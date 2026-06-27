#!/usr/bin/env python3
"""Phase R R4 — Agent Harness 联合 CI 门禁（离线，无 Gateway / LLM Key）。

用法：
  python eval/harness_capability_gate.py list
  python eval/harness_capability_gate.py check
  python eval/harness_capability_gate.py run
  python eval/harness_capability_gate.py run --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO_ROOT / "eval" / "harness_baseline.jsonl"
PYTHON = sys.executable

REQUIRED_PHASES = frozenset({"R1", "R2", "R3"})


@dataclass(frozen=True)
class HarnessGateCheck:
    name: str
    issue: str
    command: tuple[str, ...]
    description: str = ""


@dataclass
class HarnessGateResult:
    name: str
    issue: str
    passed: bool
    duration_ms: float
    returncode: int
    detail: str


CHECKS: tuple[HarnessGateCheck, ...] = (
    HarnessGateCheck(
        "self_evolve_unit",
        "R1",
        (PYTHON, "-m", "pytest", "tests/test_self_evolve.py", "-q"),
        "自进化 Agent 单测",
    ),
    HarnessGateCheck(
        "self_evolve_smoke",
        "R1",
        (PYTHON, str(REPO_ROOT / "eval" / "self_evolve_smoke.py")),
        "经验库复用 smoke",
    ),
    HarnessGateCheck(
        "long_horizon_unit",
        "R2",
        (PYTHON, "-m", "pytest", "tests/test_long_horizon.py", "-q"),
        "长程任务单测",
    ),
    HarnessGateCheck(
        "long_horizon_persistence_unit",
        "R2",
        (PYTHON, "-m", "pytest", "tests/test_long_horizon_persistence.py", "-q"),
        "长程任务持久化单测",
    ),
    HarnessGateCheck(
        "long_horizon_smoke",
        "R2",
        (PYTHON, str(REPO_ROOT / "eval" / "long_horizon_smoke.py")),
        "跨 session checkpoint/resume smoke",
    ),
    HarnessGateCheck(
        "capability_profile_unit",
        "R3",
        (PYTHON, str(REPO_ROOT / "tests" / "test_capability_profile.py")),
        "模型能力画像 + Router 反哺",
    ),
    HarnessGateCheck(
        "capability_benchmark_mock",
        "R3",
        (
            PYTHON,
            str(REPO_ROOT / "eval" / "harness_capability_benchmark.py"),
            "run",
            "--model",
            "chat-fast",
            "--mock",
        ),
        "4 维 benchmark mock",
    ),
)

REQUIRED_PATHS: tuple[tuple[str, Path], ...] = (
    ("experience_store", REPO_ROOT / "packages" / "agent" / "experience_store.py"),
    ("self_evolve", REPO_ROOT / "packages" / "agent" / "self_evolve.py"),
    ("long_horizon", REPO_ROOT / "packages" / "agent" / "long_horizon.py"),
    ("capability_profile", REPO_ROOT / "packages" / "agent" / "capability_profile.py"),
    ("harness_benchmark", REPO_ROOT / "eval" / "harness_capability_benchmark.py"),
    ("harness_baseline", BASELINE_PATH),
    ("phase_r_doc", REPO_ROOT / "docs" / "phase-r-agent-harness.md"),
    ("capability_report_sample", REPO_ROOT / "docs" / "phase-r-capability-report-sample.md"),
)


def verify_required_paths() -> list[str]:
    return [label for label, path in REQUIRED_PATHS if not path.is_file()]


def load_baseline_cases() -> list[dict[str, Any]]:
    if not BASELINE_PATH.is_file():
        raise FileNotFoundError(f"baseline not found: {BASELINE_PATH}")
    cases: list[dict[str, Any]] = []
    for line_no, line in enumerate(BASELINE_PATH.read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSONL line {line_no}: {e}") from e
        if not isinstance(row, dict):
            raise ValueError(f"line {line_no}: expected object")
        cases.append(row)
    return cases


def validate_baseline(cases: list[dict[str, Any]] | None = None) -> list[str]:
    """静态校验 harness_baseline.jsonl；返回错误列表（空=通过）。"""
    errors: list[str] = []
    rows = cases if cases is not None else load_baseline_cases()
    if len(rows) < 5:
        errors.append(f"baseline 至少需要 5 条，当前 {len(rows)}")

    seen_ids: set[str] = set()
    phases: set[str] = set()
    check_names = {c.name for c in CHECKS}

    for row in rows:
        case_id = row.get("id")
        phase = row.get("phase")
        if not isinstance(case_id, str) or not case_id.strip():
            errors.append("每条 baseline 须含非空 id")
            continue
        if case_id in seen_ids:
            errors.append(f"重复 id: {case_id}")
        seen_ids.add(case_id)

        if phase not in REQUIRED_PHASES:
            errors.append(f"{case_id}: phase 须为 R1/R2/R3")
        else:
            phases.add(str(phase))

        required_checks = row.get("required_checks")
        if not isinstance(required_checks, list) or not required_checks:
            errors.append(f"{case_id}: required_checks 须为非空数组")
        else:
            for check in required_checks:
                if check not in check_names:
                    errors.append(f"{case_id}: 未知 required_checks {check!r}")

        min_overall = row.get("min_overall_score")
        if min_overall is not None:
            try:
                score = float(min_overall)
                if not 0 <= score <= 1:
                    errors.append(f"{case_id}: min_overall_score 须在 0~1")
            except (TypeError, ValueError):
                errors.append(f"{case_id}: min_overall_score 须为数字")

    if phases != REQUIRED_PHASES:
        missing = sorted(REQUIRED_PHASES - phases)
        errors.append(f"baseline 须覆盖 Phase: {', '.join(missing)}")

    return errors


def run_check(check: HarnessGateCheck) -> HarnessGateResult:
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
    return HarnessGateResult(
        name=check.name,
        issue=check.issue,
        passed=proc.returncode == 0,
        duration_ms=duration_ms,
        returncode=proc.returncode,
        detail=detail,
    )


def run_gate(*, checks: tuple[HarnessGateCheck, ...] = CHECKS) -> tuple[bool, list[HarnessGateResult]]:
    missing = verify_required_paths()
    if missing:
        result = HarnessGateResult(
            name="required_paths",
            issue="R4",
            passed=False,
            duration_ms=0.0,
            returncode=1,
            detail=f"missing: {', '.join(missing)}",
        )
        return False, [result]

    baseline_errors = validate_baseline()
    if baseline_errors:
        result = HarnessGateResult(
            name="baseline_check",
            issue="R4",
            passed=False,
            duration_ms=0.0,
            returncode=1,
            detail="; ".join(baseline_errors[:5]),
        )
        return False, [result]

    results = [run_check(c) for c in checks]
    passed = all(r.passed for r in results)
    return passed, results


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase R Agent Harness gate (#137)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出检查项")

    check_p = sub.add_parser("check", help="静态校验 baseline 与必需文件")
    check_p.add_argument("--json", action="store_true")

    run_p = sub.add_parser("run", help="运行全部离线检查")
    run_p.add_argument("--json", action="store_true")

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

    if args.command == "check":
        missing = verify_required_paths()
        baseline_errors = validate_baseline()
        passed = not missing and not baseline_errors
        summary = {
            "passed": passed,
            "missing_paths": missing,
            "baseline_errors": baseline_errors,
            "baseline_cases": len(load_baseline_cases()) if BASELINE_PATH.is_file() else 0,
        }
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            if missing:
                print(f"[FAIL] missing paths: {', '.join(missing)}")
            if baseline_errors:
                for err in baseline_errors:
                    print(f"[FAIL] baseline: {err}")
            if passed:
                print(f"[OK] harness baseline valid ({summary['baseline_cases']} cases)")
        raise SystemExit(0 if passed else 1)

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
            f"\nHarness capability gate: {'PASSED' if passed else 'FAILED'} "
            f"({len(results) - len(summary['failed'])}/{len(results)})"
        )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
