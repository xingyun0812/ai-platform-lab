#!/usr/bin/env python3
"""Phase P P4 — 多模态 Embedding CI 门禁（离线，无 Gateway / LLM Key）。"""

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
class MultimodalGateCheck:
    name: str
    issue: str
    command: tuple[str, ...]
    description: str = ""


@dataclass
class MultimodalGateResult:
    name: str
    issue: str
    passed: bool
    duration_ms: float
    returncode: int
    detail: str


CHECKS: tuple[MultimodalGateCheck, ...] = (
    MultimodalGateCheck(
        "multimodal_unit",
        "P1",
        (PYTHON, "-m", "unittest", "tests.test_multimodal_embedding", "-q"),
        "多模态 inputs 单测",
    ),
    MultimodalGateCheck(
        "multimodal_smoke",
        "P1",
        (PYTHON, str(REPO_ROOT / "eval" / "multimodal_embedding_smoke.py")),
        "Embedding 服务 stub smoke",
    ),
    MultimodalGateCheck(
        "rag_multimodal_unit",
        "P2",
        (PYTHON, "-m", "unittest", "tests.test_rag_multimodal_index", "-q"),
        "RAG 图文索引单测",
    ),
    MultimodalGateCheck(
        "rag_multimodal_smoke",
        "P2",
        (PYTHON, str(REPO_ROOT / "eval" / "rag_multimodal_smoke.py")),
        "RAG image embed smoke",
    ),
    MultimodalGateCheck(
        "sdk_multimodal_smoke",
        "P3",
        (PYTHON, str(REPO_ROOT / "eval" / "sdk_multimodal_smoke.py")),
        "Python SDK multimodal smoke",
    ),
    MultimodalGateCheck(
        "embedding_regression",
        "P4",
        (PYTHON, str(REPO_ROOT / "tests" / "test_embedding.py")),
        "Embedding 服务回归",
    ),
)

REQUIRED_PATHS: tuple[tuple[str, Path], ...] = (
    ("multimodal", REPO_ROOT / "packages" / "embedding" / "multimodal.py"),
    ("rag_multimodal_index", REPO_ROOT / "packages" / "rag" / "multimodal_index.py"),
    ("embedding_models_yaml", REPO_ROOT / "config" / "embedding_models.yaml"),
    ("samples_chart", REPO_ROOT / "samples" / "chart.png"),
    ("console_embedding_page", REPO_ROOT / "console-v2" / "src" / "pages" / "Embedding.tsx"),
    ("console_embedding_api", REPO_ROOT / "console-v2" / "src" / "api" / "embedding.ts"),
    ("sdk_embedding", REPO_ROOT / "sdk" / "python" / "ai_platform_lab" / "resources" / "embedding.py"),
    ("phase_p_doc", REPO_ROOT / "docs" / "phase-p-multimodal-embedding.md"),
)


def verify_required_paths() -> list[str]:
    missing = [label for label, path in REQUIRED_PATHS if not path.is_file()]
    if not missing:
        yaml_text = (REPO_ROOT / "config" / "embedding_models.yaml").read_text(encoding="utf-8")
        if "stub-multimodal" not in yaml_text:
            missing.append("stub-multimodal in embedding_models.yaml")
    return missing


def run_check(check: MultimodalGateCheck) -> MultimodalGateResult:
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
    return MultimodalGateResult(
        name=check.name,
        issue=check.issue,
        passed=proc.returncode == 0,
        duration_ms=duration_ms,
        returncode=proc.returncode,
        detail=detail,
    )


def run_gate(
    *,
    checks: tuple[MultimodalGateCheck, ...] = CHECKS,
) -> tuple[bool, list[MultimodalGateResult]]:
    missing = verify_required_paths()
    if missing:
        result = MultimodalGateResult(
            name="required_paths",
            issue="P4",
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
    parser = argparse.ArgumentParser(description="Phase P multimodal embedding gate")
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
            f"\nMultimodal embedding gate: {'PASSED' if passed else 'FAILED'} "
            f"({len(results) - len(summary['failed'])}/{len(results)})"
        )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
