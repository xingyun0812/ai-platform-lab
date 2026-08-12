from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from packages.agent.computer_use.models import (
    ActionResult,
    ComputerUseConfig,
    ComputerUseResult,
    ScreenState,
)


class TestComputerUseConfig(unittest.TestCase):
    """ComputerUseConfig 数据模型测试。"""

    def test_default_config(self):
        cfg = ComputerUseConfig()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.max_steps, 10)

    def test_to_dict(self):
        cfg = ComputerUseConfig(max_steps=5)
        d = cfg.to_dict()
        self.assertEqual(d["max_steps"], 5)


class TestActionResult(unittest.TestCase):
    """ActionResult 数据模型测试。"""

    def test_create(self):
        a = ActionResult(action_type="click", x=100, y=200, description="click button")
        self.assertEqual(a.action_type, "click")
        self.assertEqual(a.x, 100)
        self.assertEqual(a.y, 200)

    def test_to_dict(self):
        a = ActionResult(action_type="done", text="finished", llm_reasoning="done")
        d = a.to_dict()
        self.assertEqual(d["action_type"], "done")
        self.assertEqual(d["text"], "finished")
        self.assertIn("llm_reasoning", d)


class TestScreenState(unittest.TestCase):
    """ScreenState 数据模型测试。"""

    def test_create(self):
        s = ScreenState(screenshot_base64="abcd", width=1024, height=768)
        self.assertEqual(s.width, 1024)
        self.assertEqual(s.screenshot_base64, "abcd")


class TestComputerUseResult(unittest.TestCase):
    """ComputerUseResult 数据模型测试。"""

    def test_create(self):
        r = ComputerUseResult(task="test", final_answer="done", success=True, execution_time_ms=100.0)
        self.assertEqual(r.task, "test")
        self.assertTrue(r.success)

    def test_to_dict(self):
        r = ComputerUseResult(task="t", final_answer="a", success=True, execution_time_ms=50.0)
        d = r.to_dict()
        self.assertEqual(d["final_answer"], "a")
        self.assertTrue(d["success"])


class TestComputerUseExecutor(unittest.IsolatedAsyncioTestCase):
    """ComputerUseExecutor 单元测试（mock 截图和输入）。"""

    async def test_screenshot_returns_bytes(self):
        from packages.agent.computer_use.executor import ComputerUseExecutor

        exe = ComputerUseExecutor()
        result = await exe.screenshot()
        self.assertIsInstance(result, bytes)
        self.assertGreater(len(result), 0)

    async def test_execute_click(self):
        from packages.agent.computer_use.executor import ComputerUseExecutor

        exe = ComputerUseExecutor()
        action = ActionResult(action_type="click", x=100, y=200)
        result = await exe.execute(action)
        self.assertEqual(result.action_type, "click")
        # mock 模式下不应有错误
        self.assertIsNone(result.error)

    async def test_execute_type(self):
        from packages.agent.computer_use.executor import ComputerUseExecutor

        exe = ComputerUseExecutor()
        action = ActionResult(action_type="type", text="hello")
        result = await exe.execute(action)
        self.assertEqual(result.action_type, "type")

    async def test_execute_done(self):
        from packages.agent.computer_use.executor import ComputerUseExecutor

        exe = ComputerUseExecutor()
        action = ActionResult(action_type="done", text="finished")
        result = await exe.execute(action)
        self.assertEqual(result.action_type, "done")


class TestComputerUsePlanner(unittest.IsolatedAsyncioTestCase):
    """ComputerUsePlanner 单元测试（mock LLM）。"""

    async def test_plan_with_mock_llm(self):
        from packages.agent.computer_use.planner import ComputerUsePlanner

        mock_route = MagicMock()
        mock_route.status = 200
        mock_route.body = {
            "choices": [{"message": {"content": (
                '{"action": "click", "x": 500, "y": 300, "reasoning": "点击按钮"}'
            )}}]
        }

        with patch(
            "packages.platform.forward_with_model_router",
            AsyncMock(return_value=mock_route),
        ):
            planner = ComputerUsePlanner(model="test")
            screen = ScreenState(screenshot_base64="fake_base64", width=1024, height=768)
            action = await planner.plan(screen, "click the button", [])
            self.assertEqual(action.action_type, "click")
            self.assertEqual(action.x, 500)
            self.assertEqual(action.y, 300)

    async def test_plan_fallback_on_llm_error(self):
        from packages.agent.computer_use.planner import ComputerUsePlanner

        with patch(
            "packages.platform.forward_with_model_router",
            AsyncMock(return_value=MagicMock(status=500, body=None)),
        ):
            planner = ComputerUsePlanner()
            screen = ScreenState(screenshot_base64="fake", width=1024, height=768)
            action = await planner.plan(screen, "test", [])
            self.assertEqual(action.action_type, "screenshot")


class TestRunComputerUse(unittest.IsolatedAsyncioTestCase):
    """run_computer_use 集成测试（mock LLM）。"""

    async def test_completes_with_done(self):
        from packages.agent.computer_use import run_computer_use

        mock_done_response = (
            '{"action": "done", "text": "任务完成", "reasoning": "完成"}'
        )

        async def mock_llm(_payload):
            route = MagicMock()
            route.status = 200
            route.body = {"choices": [{"message": {"content": mock_done_response}}]}
            return route

        with patch(
            "packages.platform.forward_with_model_router",
            mock_llm,
        ):
            result = await run_computer_use(
                task="test task",
                config=ComputerUseConfig(max_steps=5),
            )
            self.assertTrue(result.success)
            self.assertIsNotNone(result.final_answer)
            self.assertGreater(len(result.steps), 0)

    async def test_error_handling(self):
        from packages.agent.computer_use import run_computer_use

        with patch(
            "packages.platform.forward_with_model_router",
            AsyncMock(side_effect=RuntimeError("API error")),
        ):
            result = await run_computer_use(
                task="test",
                config=ComputerUseConfig(max_steps=3),
            )
            self.assertFalse(result.success)
            self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
