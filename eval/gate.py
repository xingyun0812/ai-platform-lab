#!/usr/bin/env python3
"""eval/gate.py — CI 评测门禁 (Phase J #47)

读取 main 分支基线 JSON，与当前 EvalReport 对比，超过阈值则 exit(1)。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE_PATH = REPO_ROOT / "eval" / "baselines" / "main_baseline.json"
_EVAL_DIR = REPO_ROOT / "eval"


def _ensure_pipeline():
    """Lazy-load eval.pipeline to avoid circular import issues."""
    if "eval.pipeline" not in sys.modules:
        spec = importlib.util.spec_from_file_location("eval.pipeline", _EVAL_DIR / "pipeline.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["eval.pipeline"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["eval.pipeline"]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class GateResult:
    """CI 门禁结果。"""

    passed: bool
    reason: str
    delta: float  # current_pass_rate - baseline_pass_rate (percentage points)
    threshold: float  # default 5.0
    current_pass_rate: float
    baseline_pass_rate: float
    by_category: dict | None = None


# ---------------------------------------------------------------------------
# Gate logic
# ---------------------------------------------------------------------------


def check_gate(
    report,
    baseline_path: Path | None = None,
    threshold_pct: float = 5.0,
) -> GateResult:
    """Compare report to baseline and return GateResult.

    Gate PASSES if: delta > -threshold_pct
    Gate FAILS if:  delta <= -threshold_pct  (regression > threshold)
    """
    pipeline_mod = _ensure_pipeline()
    EvalPipeline = pipeline_mod.EvalPipeline

    path = baseline_path or DEFAULT_BASELINE_PATH

    if not path.is_file():
        return GateResult(
            passed=False,
            reason=f"Baseline file not found: {path}",
            delta=0.0,
            threshold=threshold_pct,
            current_pass_rate=report.pass_rate,
            baseline_pass_rate=0.0,
        )

    try:
        _baseline_data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return GateResult(
            passed=False,
            reason=f"Failed to parse baseline: {exc}",
            delta=0.0,
            threshold=threshold_pct,
            current_pass_rate=report.pass_rate,
            baseline_pass_rate=0.0,
        )

    # Use pipeline comparison
    pipeline = EvalPipeline()
    comparison = pipeline.compare_to_baseline(report, path, threshold_pct=threshold_pct)

    if comparison.gate_passed:
        reason = (
            f"Gate PASSED: delta={comparison.delta_pct:+.2f}% "
            f"(threshold=-{threshold_pct:.1f}%)"
        )
    else:
        reason = (
            f"Gate FAILED: delta={comparison.delta_pct:+.2f}% "
            f"exceeds regression threshold of -{threshold_pct:.1f}%"
        )

    return GateResult(
        passed=comparison.gate_passed,
        reason=reason,
        delta=comparison.delta_pct,
        threshold=threshold_pct,
        current_pass_rate=comparison.current_pass_rate,
        baseline_pass_rate=comparison.baseline_pass_rate,
        by_category=comparison.by_category,
    )


def run_gate_and_exit(
    baseline_path: Path | None = None,
    threshold_pct: float = 5.0,
    categories: list | None = None,
    sample_limit: int | None = None,
) -> None:
    """完整运行：评测 + 对比 baseline + 输出结果，失败则 exit(1)。"""
    import os

    pipeline_mod = _ensure_pipeline()
    EvalPipeline = pipeline_mod.EvalPipeline

    gateway_url = os.environ.get("EVAL_GATEWAY_URL", "http://127.0.0.1:8000")
    api_key = os.environ.get("EVAL_API_KEY") or os.environ.get("LLM_API_KEY")

    pipeline = EvalPipeline(gateway_url=gateway_url, api_key=api_key)
    report = pipeline.run_all(categories=categories, sample_limit=sample_limit)

    gate = check_gate(report, baseline_path=baseline_path, threshold_pct=threshold_pct)

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

    if not gate.passed:
        sys.exit(1)
