from __future__ import annotations

import json
from typing import Any

from apps.gateway.settings import REPO_ROOT

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


def latest_eval_pass_rate() -> float | None:
    if not EVAL_RUNS_DIR.is_dir():
        return None
    files = sorted(EVAL_RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:5]:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            rate = report.get("summary", {}).get("pass_rate")
            if isinstance(rate, (int, float)):
                return float(rate)
        except Exception:
            continue
    return None


def apply_auto_rollback(*, kb_id: str, min_pass_rate: float = 0.85) -> dict[str, Any] | None:
    """若最近 eval pass_rate 低于阈值，将 kb 金丝雀比例压为 0。"""
    rate = latest_eval_pass_rate()
    if rate is None or rate >= min_pass_rate:
        return None
    guard = _read_guard()
    overrides = guard.setdefault("kb_routing_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}
        guard["kb_routing_overrides"] = overrides
    overrides[kb_id] = {
        "canary_percent": 0,
        "reason": f"auto_rollback pass_rate={rate} < {min_pass_rate}",
    }
    guard["last_rollback_at"] = __import__("datetime").datetime.now(
        __import__("datetime").UTC
    ).isoformat()
    _write_guard(guard)
    return {"kb_id": kb_id, "pass_rate": rate, "action": "canary_percent=0"}


def get_kb_routing_override(kb_id: str) -> dict[str, Any] | None:
    guard = _read_guard()
    overrides = guard.get("kb_routing_overrides")
    if not isinstance(overrides, dict):
        return None
    cfg = overrides.get(kb_id)
    return cfg if isinstance(cfg, dict) else None
