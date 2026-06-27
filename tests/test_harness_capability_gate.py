"""Phase R R4 — harness_capability_gate 元测试。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_required_paths_exist() -> None:
    from eval.harness_capability_gate import verify_required_paths

    missing = verify_required_paths()
    assert missing == [], missing


def test_baseline_valid() -> None:
    from eval.harness_capability_gate import load_baseline_cases, validate_baseline

    cases = load_baseline_cases()
    assert len(cases) >= 5
    assert validate_baseline(cases) == []


def test_checks_cover_r1_through_r3() -> None:
    from eval.harness_capability_gate import CHECKS

    issues = {c.issue for c in CHECKS}
    assert {"R1", "R2", "R3"}.issubset(issues)


def test_list_command_json() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO / "eval" / "harness_capability_gate.py"), "list"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    assert isinstance(data, list)
    assert len(data) >= 7


def test_check_command_passes() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO / "eval" / "harness_capability_gate.py"), "check"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
