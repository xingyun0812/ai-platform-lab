#!/usr/bin/env python3
"""eval/report.py — 评测报告格式化与保存 (Phase J #47)"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_EVAL_DIR = REPO_ROOT / "eval"


def _ensure_pipeline():
    """Lazy-load eval.pipeline."""
    if "eval.pipeline" not in sys.modules:
        spec = importlib.util.spec_from_file_location("eval.pipeline", _EVAL_DIR / "pipeline.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["eval.pipeline"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["eval.pipeline"]


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def format_report_markdown(report) -> str:
    """生成人类可读的 Markdown 表格报告。"""
    pipeline_mod = _ensure_pipeline()
    CategoryResult = pipeline_mod.CategoryResult

    lines: list[str] = []

    ts = datetime.utcfromtimestamp(report.timestamp).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines.append("# Eval Report\n")
    lines.append(f"- **Timestamp**: {ts}")
    lines.append(f"- **Commit SHA**: `{report.commit_sha or 'local'}`")
    lines.append("")

    lines.append("## Summary\n")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total Cases | {report.total_cases} |")
    lines.append(f"| Passed | {report.passed} |")
    lines.append(f"| Failed | {report.failed} |")
    lines.append(f"| Skipped | {report.skipped} |")
    lines.append(f"| Pass Rate | {report.pass_rate:.2%} |")
    lines.append("")

    if report.by_category:
        lines.append("## By Category\n")
        lines.append("| Category | Total | Passed | Failed | Skipped | Pass Rate |")
        lines.append("|----------|-------|--------|--------|---------|-----------|")
        for cat, result in report.by_category.items():
            if isinstance(result, CategoryResult):
                lines.append(
                    f"| {cat} | {result.total} | {result.passed} | {result.failed} | "
                    f"{result.skipped} | {result.pass_rate:.2%} |"
                )
            elif isinstance(result, dict):
                lines.append(
                    f"| {cat} | {result.get('total', 0)} | {result.get('passed', 0)} | "
                    f"{result.get('failed', 0)} | {result.get('skipped', 0)} | "
                    f"{result.get('pass_rate', 0.0):.2%} |"
                )
        lines.append("")

    return "\n".join(lines) + "\n"


def format_report_json(report) -> str:
    """生成 JSON 格式的评测报告（用于 CI artifact）。"""
    pipeline_mod = _ensure_pipeline()
    CategoryResult = pipeline_mod.CategoryResult

    data = report.to_dict()
    # Convert CategoryResult objects in by_category
    by_cat: dict[str, dict] = {}
    for k, v in data.get("by_category", {}).items():
        if isinstance(v, CategoryResult):
            entry = {
                "category": v.category,
                "total": v.total,
                "passed": v.passed,
                "failed": v.failed,
                "skipped": v.skipped,
                "pass_rate": v.pass_rate,
            }
        elif isinstance(v, dict):
            entry = dict(v)
        else:
            entry = {}
        # Strip detailed cases for JSON output to save space
        entry.pop("cases", None)
        by_cat[k] = entry
    data["by_category"] = by_cat
    return json.dumps(data, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


def save_report(report, path: Path) -> tuple:
    """写出 .md 和 .json 两个文件，返回 (md_path, json_path)。"""
    path.parent.mkdir(parents=True, exist_ok=True)

    md_path = path.with_suffix(".md")
    json_path = path.with_suffix(".json")

    md_path.write_text(format_report_markdown(report), encoding="utf-8")
    json_path.write_text(format_report_json(report), encoding="utf-8")

    return md_path, json_path
