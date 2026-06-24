"""tests/test_live_gate.py — live_gate 无 Key 时 skip 不失败。"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class TestLiveGate(unittest.TestCase):
    def test_run_without_llm_key_skips(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "LLM_API_KEY"}
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO / "eval" / "live_gate.py"),
                "run",
                "--no-dotenv",
            ],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("blocked", proc.stdout.lower() + proc.stderr.lower())


if __name__ == "__main__":
    unittest.main()
