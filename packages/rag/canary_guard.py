from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apps.gateway.settings import REPO_ROOT

logger = logging.getLogger("ai_platform.rag.canary_guard")

GUARD_PATH = REPO_ROOT / "data" / "canary_guard.json"
EVAL_RUNS_DIR = REPO_ROOT / "eval" / "runs"


def _read_guard() -> dict[str, Any]:
    if not GUARD_PATH.is_file():
        return {}
    data = json.loads(GUARD_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _write_guard(data: dict[str, Any]) -> None:
    GUARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    GUARD_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _pass_rate_from_report(report: dict[str, Any]) -> float | None:
    summary = report.get("summary")
    if isinstance(summary, dict):
        rate = summary.get("pass_rate")
        if isinstance(rate, (int, float)):
            return float(rate)
    rate = report.get("pass_rate")
    if isinstance(rate, (int, float)):
        return float(rate)
    return None


def latest_eval_pass_rate(*, eval_path: Path | None = None) -> float | None:
    """读最近 eval 报告 pass_rate（兼容 eval/run.py 与 pipeline JSON）。"""
    if eval_path is not None:
        if not eval_path.is_file():
            return None
        try:
            report = json.loads(eval_path.read_text(encoding="utf-8"))
            return _pass_rate_from_report(report if isinstance(report, dict) else {})
        except Exception:
            return None

    if not EVAL_RUNS_DIR.is_dir():
        return None
    files = sorted(EVAL_RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:10]:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                continue
            rate = _pass_rate_from_report(report)
            if rate is not None:
                return rate
        except Exception:
            continue
    return None


def _notify_webhook(payload: dict[str, Any]) -> None:
    url = (os.environ.get("CANARY_GUARD_WEBHOOK_URL") or "").strip()
    if not url:
        return
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    secret = (os.environ.get("CANARY_GUARD_WEBHOOK_SECRET") or "").strip()
    if secret:
        headers["X-Canary-Guard-Secret"] = secret
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except urllib.error.URLError as exc:
        logger.warning("canary guard webhook failed: %s", exc)


def _record_rollback_metric(kb_id: str) -> None:
    try:
        from packages.observability.metrics import get_metrics_store

        get_metrics_store().record_canary_auto_rollback(kb_id=kb_id)
    except Exception:
        pass


@dataclass
class CanaryCheckResult:
    kb_id: str
    pass_rate: float | None
    min_pass_rate: float
    action: str  # noop | rollback
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kb_id": self.kb_id,
            "pass_rate": self.pass_rate,
            "min_pass_rate": self.min_pass_rate,
            "action": self.action,
            "detail": self.detail,
        }


def check_canary_guard(
    *,
    kb_id: str,
    min_pass_rate: float = 0.85,
    eval_path: Path | None = None,
    dry_run: bool = False,
) -> CanaryCheckResult:
    """检查 eval pass_rate；低于阈值则写 canary_percent=0 到 guard 文件。"""
    rate = latest_eval_pass_rate(eval_path=eval_path)
    if rate is None:
        return CanaryCheckResult(
            kb_id=kb_id,
            pass_rate=None,
            min_pass_rate=min_pass_rate,
            action="noop",
            detail={"reason": "no_eval_report"},
        )
    if rate >= min_pass_rate:
        return CanaryCheckResult(
            kb_id=kb_id,
            pass_rate=rate,
            min_pass_rate=min_pass_rate,
            action="noop",
            detail={"reason": "pass_rate_above_threshold"},
        )

    detail = {
        "reason": f"pass_rate={rate} < {min_pass_rate}",
        "canary_percent": 0,
    }
    if not dry_run:
        guard = _read_guard()
        overrides = guard.setdefault("kb_routing_overrides", {})
        if not isinstance(overrides, dict):
            overrides = {}
            guard["kb_routing_overrides"] = overrides
        overrides[kb_id] = {
            "canary_percent": 0,
            "reason": detail["reason"],
            "rolled_back_at": datetime.now(UTC).isoformat(),
        }
        guard["last_rollback_at"] = datetime.now(UTC).isoformat()
        guard["last_rollback_kb_id"] = kb_id
        _write_guard(guard)
        _record_rollback_metric(kb_id)
        _notify_webhook({"event": "canary_auto_rollback", "kb_id": kb_id, **detail, "pass_rate": rate})

    return CanaryCheckResult(
        kb_id=kb_id,
        pass_rate=rate,
        min_pass_rate=min_pass_rate,
        action="rollback",
        detail=detail,
    )


def apply_auto_rollback(*, kb_id: str, min_pass_rate: float = 0.85) -> dict[str, Any] | None:
    """兼容旧 API：执行 check 并在 rollback 时返回摘要。"""
    result = check_canary_guard(kb_id=kb_id, min_pass_rate=min_pass_rate)
    if result.action != "rollback":
        return None
    return {
        "kb_id": kb_id,
        "pass_rate": result.pass_rate,
        "action": "canary_percent=0",
    }


def get_kb_routing_override(kb_id: str) -> dict[str, Any] | None:
    guard = _read_guard()
    overrides = guard.get("kb_routing_overrides")
    if not isinstance(overrides, dict):
        return None
    cfg = overrides.get(kb_id)
    return cfg if isinstance(cfg, dict) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="kb 金丝雀 eval 阈值自动回滚")
    sub = parser.add_subparsers(dest="command", required=True)

    check_p = sub.add_parser("check", help="读 eval pass_rate，低于阈值则 canary_percent=0")
    check_p.add_argument("--kb-id", required=True)
    check_p.add_argument("--min-pass-rate", type=float, default=0.85)
    check_p.add_argument("--eval-path", type=Path, default=None, help="指定 eval 报告 JSON")
    check_p.add_argument("--dry-run", action="store_true")

    sub.add_parser("status", help="打印 canary_guard.json 内容")

    args = parser.parse_args(argv)
    if args.command == "status":
        print(json.dumps(_read_guard(), ensure_ascii=False, indent=2))
        return 0

    result = check_canary_guard(
        kb_id=args.kb_id,
        min_pass_rate=args.min_pass_rate,
        eval_path=args.eval_path,
        dry_run=args.dry_run,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.action == "noop" else 2


if __name__ == "__main__":
    raise SystemExit(main())
