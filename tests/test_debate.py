from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from packages.agent.debate.models import (
    DebateConfig,
    DebateCritique,
    DebateProposal,
    DebateResult,
)


class TestDebateConfig(unittest.TestCase):
    """DebateConfig 数据模型测试。"""

    def test_default_config(self):
        cfg = DebateConfig()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.num_proposers, 3)
        self.assertEqual(cfg.num_rounds, 2)
        self.assertEqual(cfg.temperature, 0.7)
        self.assertEqual(cfg.critic_temperature, 0.3)

    def test_custom_config(self):
        cfg = DebateConfig(num_proposers=5, num_rounds=3, timeout_seconds=300.0)
        self.assertEqual(cfg.num_proposers, 5)
        self.assertEqual(cfg.num_rounds, 3)
        self.assertEqual(cfg.timeout_seconds, 300.0)

    def test_to_dict(self):
        cfg = DebateConfig()
        d = cfg.to_dict()
        self.assertEqual(d["num_proposers"], 3)
        self.assertIn("enabled", d)


class TestDebateProposal(unittest.TestCase):
    """DebateProposal 数据模型测试。"""

    def test_create(self):
        p = DebateProposal(agent_id="a1", proposal="answer is 42", round_number=1)
        self.assertEqual(p.agent_id, "a1")
        self.assertEqual(p.proposal, "answer is 42")
        self.assertEqual(p.round_number, 1)
        self.assertIsNone(p.confidence)
        self.assertIsNone(p.error)

    def test_to_dict(self):
        p = DebateProposal(agent_id="a1", proposal="test", round_number=1, execution_time_ms=100.0)
        d = p.to_dict()
        self.assertEqual(d["agent_id"], "a1")
        self.assertEqual(d["execution_time_ms"], 100.0)


class TestDebateCritique(unittest.TestCase):
    """DebateCritique 数据模型测试。"""

    def test_create(self):
        c = DebateCritique(
            critic_agent_id="critic_1",
            target_agent_id="proposer_1",
            critique="needs more evidence",
            round_number=2,
        )
        self.assertEqual(c.critic_agent_id, "critic_1")
        self.assertEqual(c.target_agent_id, "proposer_1")
        self.assertEqual(c.round_number, 2)

    def test_to_dict(self):
        c = DebateCritique(
            critic_agent_id="c1", target_agent_id="p1",
            critique="review", round_number=2, agreement=0.7,
        )
        d = c.to_dict()
        self.assertEqual(d["agreement"], 0.7)


class TestDebateResult(unittest.TestCase):
    """DebateResult 数据模型测试。"""

    def test_create(self):
        r = DebateResult(
            question="test q",
            verdict="answer",
            verdict_confidence=0.9,
            num_rounds_completed=2,
            execution_time_ms=500.0,
        )
        self.assertEqual(r.question, "test q")
        self.assertEqual(r.verdict, "answer")
        self.assertEqual(r.verdict_confidence, 0.9)

    def test_to_dict(self):
        r = DebateResult(
            question="q", verdict="a", verdict_confidence=0.8,
            proposals=[DebateProposal(agent_id="p1", proposal="prop", round_number=1)],
            critiques=[], num_rounds_completed=1, execution_time_ms=100.0,
        )
        d = r.to_dict()
        self.assertEqual(d["verdict"], "a")
        self.assertEqual(len(d["proposals"]), 1)


class TestExtractConfidence(unittest.TestCase):
    """_extract_confidence 工具函数测试。"""

    def test_extract_confidence(self):
        from packages.agent.debate import _extract_confidence
        self.assertEqual(_extract_confidence("置信度：0.85\n理由：合理"), 0.85)
        self.assertEqual(_extract_confidence("置信度: 0.9"), 0.9)
        self.assertEqual(_extract_confidence("no confidence here"), 0.0)
        self.assertEqual(_extract_confidence("置信度：1"), 1.0)
        self.assertEqual(_extract_confidence("置信度：0"), 0.0)


class TestRunDebate(unittest.IsolatedAsyncioTestCase):
    """run_debate 编排器测试（mock delegation）。"""

    async def test_run_debate_round1_only(self):
        """num_rounds=1: 只有提案，没有评议和裁定。"""
        with patch(
            "packages.agent.multi_agent.delegation.parallel_delegate",
            AsyncMock(return_value=[
                MagicMock(agent_id="debate_proposer_1", output="answer 1",
                          execution_time_ms=100.0, error=None, status="completed"),
                MagicMock(agent_id="debate_proposer_2", output="answer 2",
                          execution_time_ms=200.0, error=None, status="completed"),
            ]),
        ), patch(
            "packages.agent.multi_agent.delegation.delegate_to_agent",
            AsyncMock(return_value=MagicMock(
                output="最终答案：42\n置信度：0.9", execution_time_ms=50.0, error=None)),
        ):
            from packages.agent.debate import run_debate
            result = await run_debate(
                question="test",
                config=DebateConfig(num_proposers=2, num_rounds=1),
            )
            self.assertEqual(len(result.proposals), 2)
            self.assertEqual(len(result.critiques), 0)
            self.assertEqual(result.num_rounds_completed, 1)
            self.assertGreater(result.execution_time_ms, 0)
            self.assertIsNone(result.error)

    async def test_run_debate_full(self):
        """num_rounds=2: 提案 + 评议 + 裁定。"""
        mock_proposal = MagicMock(
            agent_id="debate_proposer_1", output="proposal text",
            execution_time_ms=100.0, error=None, status="completed")
        mock_judge = MagicMock(
            output="最终答案：正确\n置信度：0.95", execution_time_ms=30.0, error=None)

        with patch(
            "packages.agent.multi_agent.delegation.parallel_delegate",
            AsyncMock(return_value=[mock_proposal, mock_proposal]),
        ), patch(
            "packages.agent.multi_agent.delegation.delegate_to_agent",
            AsyncMock(return_value=mock_judge),
        ):
            from packages.agent.debate import run_debate
            result = await run_debate(
                question="test question",
                config=DebateConfig(num_proposers=2, num_rounds=2),
            )
            self.assertEqual(len(result.proposals), 2)
            self.assertGreaterEqual(len(result.critiques), 0)
            self.assertIsNotNone(result.verdict)

    async def test_run_debate_error_handling(self):
        """delegation 抛出异常时优雅降级。"""
        with patch(
            "packages.agent.multi_agent.delegation.parallel_delegate",
            AsyncMock(side_effect=RuntimeError("delegation failed")),
        ):
            from packages.agent.debate import run_debate
            result = await run_debate(question="test")
            self.assertIsNotNone(result.error)
            self.assertIn("delegation failed", result.error)

    async def test_run_debate_some_failures(self):
        """部分 proposer 失败不影响其他人。"""
        with patch(
            "packages.agent.multi_agent.delegation.parallel_delegate",
            AsyncMock(return_value=[
                MagicMock(agent_id="debate_proposer_1", output="good answer",
                          execution_time_ms=100.0, error=None, status="completed"),
                MagicMock(agent_id="debate_proposer_2", output="",
                          execution_time_ms=50.0, error="AGENT_TIMEOUT", status="timeout"),
            ]),
        ), patch(
            "packages.agent.multi_agent.delegation.delegate_to_agent",
            AsyncMock(return_value=MagicMock(
                output="最终答案：ok\n置信度：0.8", execution_time_ms=30.0, error=None)),
        ):
            from packages.agent.debate import run_debate
            result = await run_debate(
                question="test",
                config=DebateConfig(num_proposers=2, num_rounds=1),
            )
            self.assertEqual(len(result.proposals), 2)
            # 一个成功一个失败
            self.assertIsNone(result.proposals[0].error)
            self.assertIsNotNone(result.proposals[1].error)
            self.assertIsNone(result.error)

    async def test_run_debate_with_context(self):
        """传入 context 参数。"""
        with patch(
            "packages.agent.multi_agent.delegation.parallel_delegate",
            AsyncMock(return_value=[
                MagicMock(agent_id="debate_proposer_1", output="answer",
                          execution_time_ms=50.0, error=None, status="completed"),
            ]),
        ), patch(
            "packages.agent.multi_agent.delegation.delegate_to_agent",
            AsyncMock(return_value=MagicMock(
                output="最终答案：yes\n置信度：0.7", execution_time_ms=20.0, error=None)),
        ):
            from packages.agent.debate import run_debate
            result = await run_debate(
                question="test",
                context="背景：这是数学题",
                config=DebateConfig(num_proposers=1, num_rounds=1),
            )
            self.assertIsNotNone(result)
            self.assertIsNone(result.error)


if __name__ == "__main__":
    unittest.main()
