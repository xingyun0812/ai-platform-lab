#!/usr/bin/env python3
"""Phase O #88 — CoT reasoning smoke（mock LLM，无 Gateway）。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from eval.platform_wire import ensure_platform_wired

    ensure_platform_wired()

    from packages.agent.reasoning import parse_thinking_content, resolve_reasoning_mode

    thinking, visible = parse_thinking_content("<thinking>plan</thinking>结果")
    assert thinking == "plan" and visible == "结果"
    assert resolve_reasoning_mode("cot", "react") == "cot"

    async def _run() -> None:
        from unittest.mock import patch

        from packages.agent.runner import run_agent
        from packages.agent.session_state import SessionState

        async def fake_route(payload, requested_model=None):
            class R:
                status = 200
                body = {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "<thinking>mock</thinking>OK",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                }
                error = None
                model_used = "m"
                models_tried = ("m",)
                fallback_used = False
                provider_id = None

            return R()

        class MemStore:
            def get_session_state(self, tenant_id, session_id):
                return SessionState(messages=[], summary=None, turn_count=0)

            def save_session_state(self, tenant_id, session_id, state):
                return None

        with patch("packages.agent.runner.forward_with_model_router", fake_route):
            result = await run_agent(
                tenant_id="admin",
                session_id="cot-smoke",
                new_messages=[{"role": "user", "content": "test"}],
                allowed_tools=(),
                allowed_models=(),
                model="chat-fast",
                reasoning_mode="cot",
                session_store=MemStore(),
            )
        assert result["final_message"] == "OK"
        assert result["reasoning_trace"][0].thinking == "mock"

    asyncio.run(_run())
    print("OK agent_cot_smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
