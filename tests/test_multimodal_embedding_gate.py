"""Phase P P4 — multimodal_embedding_gate 元测试。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_required_paths_exist() -> None:
    from eval.multimodal_embedding_gate import verify_required_paths

    missing = verify_required_paths()
    assert missing == [], missing


def test_checks_cover_p1_through_p4() -> None:
    from eval.multimodal_embedding_gate import CHECKS

    issues = {c.issue for c in CHECKS}
    assert {"P1", "P2", "P3", "P4"}.issubset(issues)


def test_list_command_json() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO / "eval" / "multimodal_embedding_gate.py"), "list"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    assert isinstance(data, list)
    assert len(data) >= 6
