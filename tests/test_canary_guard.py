#!/usr/bin/env python3
"""tests/test_canary_guard.py — Phase L #57 金丝雀自动回滚。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import packages.rag.canary_guard as cg  # noqa: E402


class TestLatestEvalPassRate(unittest.TestCase):
    def test_reads_summary_pass_rate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            report = {"summary": {"pass_rate": 0.72}}
            (runs / "run_a.json").write_text(json.dumps(report), encoding="utf-8")
            with patch.object(cg, "EVAL_RUNS_DIR", runs):
                rate = cg.latest_eval_pass_rate()
            self.assertAlmostEqual(rate, 0.72)

    def test_reads_top_level_pass_rate(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"pass_rate": 0.91}, f)
            path = Path(f.name)
        try:
            rate = cg.latest_eval_pass_rate(eval_path=path)
            self.assertAlmostEqual(rate, 0.91)
        finally:
            path.unlink(missing_ok=True)


class TestCheckCanaryGuard(unittest.TestCase):
    def test_noop_when_no_eval(self) -> None:
        with patch.object(cg, "latest_eval_pass_rate", return_value=None):
            result = cg.check_canary_guard(kb_id="lab-demo", min_pass_rate=0.85)
        self.assertEqual(result.action, "noop")
        self.assertEqual(result.detail.get("reason"), "no_eval_report")

    def test_noop_when_pass_rate_ok(self) -> None:
        with patch.object(cg, "latest_eval_pass_rate", return_value=0.9):
            result = cg.check_canary_guard(kb_id="lab-demo", min_pass_rate=0.85)
        self.assertEqual(result.action, "noop")

    def test_rollback_when_low_pass_rate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            guard_path = Path(tmp) / "canary_guard.json"
            with patch.object(cg, "GUARD_PATH", guard_path):
                with patch.object(cg, "latest_eval_pass_rate", return_value=0.7):
                    with patch.object(cg, "_record_rollback_metric"):
                        with patch.object(cg, "_notify_webhook"):
                            result = cg.check_canary_guard(kb_id="lab-demo", min_pass_rate=0.85)
            self.assertEqual(result.action, "rollback")
            data = json.loads(guard_path.read_text(encoding="utf-8"))
            self.assertEqual(data["kb_routing_overrides"]["lab-demo"]["canary_percent"], 0)

    def test_dry_run_no_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            guard_path = Path(tmp) / "canary_guard.json"
            with patch.object(cg, "GUARD_PATH", guard_path):
                with patch.object(cg, "latest_eval_pass_rate", return_value=0.5):
                    result = cg.check_canary_guard(
                        kb_id="lab-demo", min_pass_rate=0.85, dry_run=True
                    )
            self.assertEqual(result.action, "rollback")
            self.assertFalse(guard_path.exists())

    def test_get_override_after_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            guard_path = Path(tmp) / "canary_guard.json"
            guard_path.write_text(
                json.dumps({"kb_routing_overrides": {"lab-demo": {"canary_percent": 0}}}),
                encoding="utf-8",
            )
            with patch.object(cg, "GUARD_PATH", guard_path):
                override = cg.get_kb_routing_override("lab-demo")
            self.assertEqual(override["canary_percent"], 0)

    def test_apply_auto_rollback_compat(self) -> None:
        with patch.object(cg, "check_canary_guard") as mock_check:
            mock_check.return_value = cg.CanaryCheckResult(
                kb_id="lab-demo",
                pass_rate=0.6,
                min_pass_rate=0.85,
                action="rollback",
                detail={},
            )
            out = cg.apply_auto_rollback(kb_id="lab-demo")
        self.assertEqual(out["action"], "canary_percent=0")

    def test_cli_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            guard_path = Path(tmp) / "canary_guard.json"
            guard_path.write_text("{}", encoding="utf-8")
            with patch.object(cg, "GUARD_PATH", guard_path):
                code = cg.main(["status"])
            self.assertEqual(code, 0)


class TestMetricsHook(unittest.TestCase):
    def test_record_rollback_metric(self) -> None:
        from packages.observability.metrics import get_metrics_store

        store = get_metrics_store()
        before = dict(store._canary_auto_rollback_total)
        cg._record_rollback_metric("lab-demo")
        after = dict(store._canary_auto_rollback_total)
        self.assertGreaterEqual(after.get("lab-demo", 0), before.get("lab-demo", 0) + 1)
        text = store.prometheus_text()
        self.assertIn("canary_auto_rollback_total", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
