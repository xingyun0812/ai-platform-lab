#!/usr/bin/env python3
"""tests/test_runner_boundary.py — #172 PR-6a ReActLoop / runner facade 契约测试。"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from apps.gateway.settings import Settings  # noqa: E402
from packages.agent.context_budget import ContextBudgetMeta  # noqa: E402
from packages.agent.react_loop import (  # noqa: E402
    AgentRunError,
    ReActLoopResult,
    execute_tool,
    run_react_loop,
)
from packages.agent.registry import ToolDefinition, ToolRegistry  # noqa: E402
from packages.agent.runner import run_agent  # noqa: E402
from packages.agent.session import SessionStore  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _assistant(content: str = "done") -> dict:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ]
    }


def _tool_call_response(tool_name: str = "echo") -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "tc1",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": '{"text":"hi"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


class TestReActLoopBoundary(unittest.TestCase):
    def _settings(self) -> Settings:
        s = Settings()
        s.agent_max_steps = 5
        s.context_memory_injection_enabled = False
        return s

    def test_run_react_loop_text_only(self) -> None:
        settings = self._settings()
        route = MagicMock()
        route.error = None
        route.status = 200
        route.body = _assistant("hello")
        route.model_used = "gpt-test"
        route.models_tried = ("gpt-test",)

        async def _run_loop():
            with patch(
                "packages.agent.runner.forward_with_model_router",
                new=AsyncMock(return_value=route),
            ):
                return await run_react_loop(
                    messages=[{"role": "user", "content": "hi"}],
                    session_messages=[{"role": "user", "content": "hi"}],
                    registry=ToolRegistry(),
                    tools_spec=[],
                    resolved_model="gpt-test",
                    model=None,
                    tenant_id="t1",
                    session_id="s1",
                    allowed_tools=(),
                    settings=settings,
                    shadow_mode=False,
                    active_reasoning_mode="default",
                    active_tool_call_strategy="sequential",
                    budget_meta=ContextBudgetMeta(
                        budget=8000,
                        estimated_tokens=10,
                        truncated_messages=0,
                        truncated_tool_results=0,
                        summary_applied=False,
                        keep_recent_turns=4,
                    ),
                    pinned_prefix=0,
                    reflect_remaining=1,
                )

        result = _run(_run_loop())
        self.assertIsInstance(result, ReActLoopResult)
        self.assertEqual(result.final_message, "hello")
        self.assertEqual(result.steps, 1)

    def test_run_react_loop_tool_then_text(self) -> None:
        settings = self._settings()
        responses = [_tool_call_response("echo"), _assistant("ok")]

        async def fake_route(payload, requested_model=None):
            body = responses.pop(0)
            r = MagicMock()
            r.error = None
            r.status = 200
            r.body = body
            r.model_used = "gpt-test"
            r.models_tried = ("gpt-test",)
            return r

        async def echo_handler(args: dict) -> str:
            return args.get("text", "")

        reg = ToolRegistry(
            tools={
                "echo": ToolDefinition(
                    name="echo",
                    description="echo",
                    parameters_schema={"type": "object"},
                    handler=echo_handler,
                )
            }
        )

        async def _run_loop():
            with patch(
                "packages.agent.runner.forward_with_model_router",
                new=fake_route,
            ):
                return await run_react_loop(
                    messages=[{"role": "user", "content": "go"}],
                    session_messages=[{"role": "user", "content": "go"}],
                    registry=reg,
                    tools_spec=reg.openai_tools_spec_subset(("echo",), ("echo",)),
                    resolved_model="gpt-test",
                    model=None,
                    tenant_id="t1",
                    session_id="s1",
                    allowed_tools=("echo",),
                    settings=settings,
                    shadow_mode=False,
                    active_reasoning_mode="default",
                    active_tool_call_strategy="sequential",
                    budget_meta=ContextBudgetMeta(
                        budget=8000,
                        estimated_tokens=10,
                        truncated_messages=0,
                        truncated_tool_results=0,
                        summary_applied=False,
                        keep_recent_turns=4,
                    ),
                    pinned_prefix=0,
                    reflect_remaining=1,
                )

        result = _run(_run_loop())
        self.assertEqual(result.final_message, "ok")
        self.assertEqual(result.steps, 2)
        self.assertEqual(len(result.trace), 1)
        self.assertEqual(result.trace[0].tool_name, "echo")

    def test_execute_tool_forbidden(self) -> None:
        reg = ToolRegistry()
        with self.assertRaises(AgentRunError) as ctx:
            _run(
                execute_tool(
                    reg,
                    tool_name="missing",
                    arguments_json="{}",
                    allowed_tools=(),
                    tool_timeout=1.0,
                    tool_max_retries=0,
                    tenant_id="t1",
                    session_id="s1",
                )
            )
        self.assertEqual(ctx.exception.code, "AGENT_TOOL_FORBIDDEN")


class TestRunnerFacade(unittest.TestCase):
    @patch("packages.agent.runner.forward_with_model_router", new_callable=AsyncMock)
    @patch("packages.agent.runner.get_settings")
    def test_run_agent_delegates_to_react_loop(self, mock_settings, mock_route) -> None:
        settings = Settings()
        settings.context_memory_injection_enabled = False
        settings.context_llm_summary_enabled = False
        settings.memory_store_enabled = False
        mock_settings.return_value = settings

        route = MagicMock()
        route.error = None
        route.status = 200
        route.body = _assistant("facade ok")
        route.model_used = settings.agent_model or settings.default_model
        route.models_tried = (route.model_used,)
        mock_route.return_value = route

        store = SessionStore()

        async def _run_agent():
            return await run_agent(
                tenant_id="admin",
                session_id="sess1",
                new_messages=[{"role": "user", "content": "hi"}],
                allowed_tools=(),
                allowed_models=(settings.default_model,),
                model=None,
                session_store=store,
            )

        payload = _run(_run_agent())
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["final_message"], "facade ok")
        self.assertIn("_platform", payload)
        mock_route.assert_awaited()


if __name__ == "__main__":
    unittest.main()
