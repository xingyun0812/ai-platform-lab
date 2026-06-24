#!/usr/bin/env python3
"""tests/test_agent_reasoning.py — Phase O #88 CoT reasoning mode。"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.reasoning import (  # noqa: E402
    ReasoningModeError,
    apply_cot_to_assistant_message,
    merge_cot_system_prompt,
    parse_thinking_content,
    resolve_reasoning_mode,
)
from packages.agent.session_state import SessionState  # noqa: E402


class TestParseThinking(unittest.TestCase):
    def test_with_thinking_block(self) -> None:
        raw = "<thinking>先查资料</thinking>\n答案是 42"
        thinking, visible = parse_thinking_content(raw)
        self.assertEqual(thinking, "先查资料")
        self.assertEqual(visible, "答案是 42")

    def test_without_thinking(self) -> None:
        thinking, visible = parse_thinking_content("直接回答")
        self.assertIsNone(thinking)
        self.assertEqual(visible, "直接回答")

    def test_empty_content(self) -> None:
        thinking, visible = parse_thinking_content(None)
        self.assertIsNone(thinking)
        self.assertEqual(visible, "")


class TestResolveMode(unittest.TestCase):
    def test_default_react(self) -> None:
        self.assertEqual(resolve_reasoning_mode(None, "react"), "react")

    def test_request_overrides_settings(self) -> None:
        self.assertEqual(resolve_reasoning_mode("cot", "react"), "cot")

    def test_invalid_mode(self) -> None:
        with self.assertRaises(ReasoningModeError):
            resolve_reasoning_mode("chain", "react")


class TestMergePrompt(unittest.TestCase):
    def test_prepends_system(self) -> None:
        out = merge_cot_system_prompt([{"role": "user", "content": "hi"}])
        self.assertEqual(out[0]["role"], "system")
        self.assertIn("CoT", out[0]["content"])

    def test_merges_existing_system(self) -> None:
        out = merge_cot_system_prompt(
            [{"role": "system", "content": "base"}, {"role": "user", "content": "hi"}]
        )
        self.assertIn("base", out[0]["content"])
        self.assertIn("CoT", out[0]["content"])


class TestApplyMessage(unittest.TestCase):
    def test_strips_thinking_from_message(self) -> None:
        msg = {"role": "assistant", "content": "<thinking>t</thinking>ok"}
        new_msg, thinking = apply_cot_to_assistant_message(msg)
        self.assertEqual(thinking, "t")
        self.assertEqual(new_msg["content"], "ok")


class TestRunnerCotIntegration(unittest.TestCase):
    def test_run_agent_records_reasoning_trace(self) -> None:
        async def fake_route(payload, requested_model=None):
            class R:
                status = 200
                body = {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "<thinking>step1</thinking>done",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
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

        async def _run():
            from packages.agent.runner import run_agent

            with patch(
                "packages.agent.runner.forward_with_model_router",
                fake_route,
            ):
                result = await run_agent(
                    tenant_id="admin",
                    session_id="cot-test",
                    new_messages=[{"role": "user", "content": "hi"}],
                    allowed_tools=(),
                    allowed_models=(),
                    model="chat-fast",
                    session_store=MemStore(),
                    reasoning_mode="cot",
                )
            self.assertEqual(result["reasoning_mode"], "cot")
            trace = result.get("reasoning_trace") or []
            self.assertEqual(len(trace), 1)
            self.assertEqual(trace[0].thinking, "step1")
            self.assertEqual(result["final_message"], "done")

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
