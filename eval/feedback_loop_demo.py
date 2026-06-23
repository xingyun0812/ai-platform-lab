#!/usr/bin/env python3
"""Phase L #61 — 反馈飞轮 E2E demo（mock 离线 / live Gateway）。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT_ID = "rag_query"
DEFAULT_TENANT = "admin"


def _admin_headers() -> dict[str, str]:
    return {
        "X-Tenant-Id": os.environ.get("DEMO_TENANT_ID", DEFAULT_TENANT),
        "Authorization": os.environ.get(
            "DEMO_ADMIN_TOKEN",
            "Bearer sk-tenant-admin-change-me",
        ),
        "Content-Type": "application/json",
    }


async def run_mock_demo(
    *,
    tenant_id: str = DEFAULT_TENANT,
    prompt_id: str = DEFAULT_PROMPT_ID,
    auto_experiment: bool = False,
) -> dict[str, Any]:
    """内存 FeedbackStore + 临时 bad_cases.jsonl，跑通 run_full_cycle。"""
    from packages.feedback import init_feedback_store, reset_for_tests as reset_fb
    from packages.feedback.store import Feedback, FeedbackType
    from packages.feedback_loop.pipeline import FeedbackLoop, init_feedback_loop, reset_for_tests

    import tempfile

    reset_fb()
    reset_for_tests()
    store = init_feedback_store()

    with tempfile.TemporaryDirectory() as tmp:
        bad_path = Path(tmp) / "bad_cases.jsonl"
        loop = FeedbackLoop(bad_cases_path=bad_path, auto_experiment=auto_experiment)
        init_feedback_loop(bad_cases_path=bad_path, auto_experiment=auto_experiment)

        for i in range(3):
            await store.create(
                Feedback(
                    feedback_id=f"fb-mock-{i}",
                    tenant_id=tenant_id,
                    session_id="sess-demo",
                    message_id=f"msg-{i}",
                    feedback_type=FeedbackType.THUMBS_DOWN.value,
                    comment=f"mock bad answer #{i}",
                    created_at=time.time(),
                )
            )

        result = await loop.run_full_cycle(tenant_id, prompt_id)
        result["bad_cases_file_lines"] = 0
        if bad_path.is_file():
            result["bad_cases_file_lines"] = len(
                [ln for ln in bad_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            )
        if result.get("suggestion_id"):
            sug = loop.get_suggestion(result["suggestion_id"])
            result["suggestion_status"] = sug.status if sug else None
        return result


async def run_live_demo(
    *,
    base_url: str,
    tenant_id: str = DEFAULT_TENANT,
    prompt_id: str = DEFAULT_PROMPT_ID,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """经 Gateway：点踩 → cycle →（可选）experiment。"""
    import httpx

    headers = _admin_headers()
    report: dict[str, Any] = {
        "mode": "live",
        "base_url": base_url,
        "tenant_id": tenant_id,
        "prompt_id": prompt_id,
        "steps": [],
        "passed": False,
    }

    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        try:
            hz = await client.get("/healthz")
            report["steps"].append({"healthz": hz.status_code})
            if hz.status_code != 200:
                report["error"] = "gateway not healthy"
                return report
        except Exception as exc:
            report["error"] = str(exc)
            report["steps"].append({"healthz": "failed"})
            return report

        # thumbs down ×2
        for i in range(2):
            body = {
                "session_id": "e2e-session",
                "message_id": f"e2e-msg-{int(time.time())}-{i}",
                "feedback_type": "thumbs_down",
                "comment": f"Phase L #61 live bad case {i}",
            }
            r = await client.post("/internal/feedback/", headers=headers, json=body)
            report["steps"].append({"feedback_post": r.status_code, "message_id": body["message_id"]})

        # full cycle
        r = await client.post(
            f"/internal/feedback-loop/cycle/{tenant_id}",
            headers=headers,
            json={"prompt_id": prompt_id},
        )
        cycle_body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        report["steps"].append({"cycle": r.status_code, "body": cycle_body})
        report["cycle_result"] = cycle_body

        sug_id = cycle_body.get("suggestion_id") if isinstance(cycle_body, dict) else None
        if sug_id and r.status_code == 200:
            loop = None
            try:
                from packages.feedback_loop.pipeline import get_feedback_loop

                loop = get_feedback_loop()
            except Exception:
                loop = None
            if loop:
                loop.apply_suggestion(sug_id)
            exp_r = await client.post(
                f"/internal/feedback-loop/experiment/{sug_id}",
                headers=headers,
            )
            exp_body = exp_r.json() if exp_r.headers.get("content-type", "").startswith("application/json") else {}
            report["steps"].append({"experiment": exp_r.status_code, "body": exp_body})

        report["passed"] = (
            r.status_code == 200
            and isinstance(cycle_body, dict)
            and cycle_body.get("bad_cases_collected", 0) >= 1
            and cycle_body.get("suggestion_id")
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Feedback loop E2E demo (#61)")
    parser.add_argument("--mock", action="store_true", help="离线 mock（默认）")
    parser.add_argument("--live", action="store_true", help="连 Gateway live")
    parser.add_argument("--base-url", default=os.environ.get("GATEWAY_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT)
    parser.add_argument("--prompt-id", default=DEFAULT_PROMPT_ID)
    parser.add_argument("--auto-experiment", action="store_true")
    args = parser.parse_args()

    if args.live:
        report = asyncio.run(
            run_live_demo(
                base_url=args.base_url,
                tenant_id=args.tenant_id,
                prompt_id=args.prompt_id,
            )
        )
    else:
        report = asyncio.run(
            run_mock_demo(
                tenant_id=args.tenant_id,
                prompt_id=args.prompt_id,
                auto_experiment=args.auto_experiment,
            )
        )
        report["mode"] = "mock"
        report["passed"] = (
            report.get("bad_cases_collected", 0) >= 1
            and report.get("suggestion_id")
            and report.get("error") is None
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("passed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
