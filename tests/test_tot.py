from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from packages.agent.tot.tree import (
    CandidateThought,
    ThoughtNode,
    ThoughtTree,
    TotConfig,
    TotResult,
)


class TestThoughtNode(unittest.TestCase):
    """ThoughtNode 数据模型测试。"""

    def test_create_node(self):
        node = ThoughtNode(node_id="n1", state="1+1=2", depth=1)
        self.assertEqual(node.node_id, "n1")
        self.assertEqual(node.state, "1+1=2")
        self.assertEqual(node.depth, 1)
        self.assertIsNone(node.value)
        self.assertEqual(node.status, "pending")
        self.assertIsNone(node.parent_id)
        self.assertEqual(node.children, [])
        self.assertEqual(node.visits, 0)

    def test_to_dict(self):
        node = ThoughtNode(node_id="n1", state="test", value=0.8, depth=2)
        d = node.to_dict()
        self.assertEqual(d["node_id"], "n1")
        self.assertEqual(d["value"], 0.8)
        self.assertEqual(d["depth"], 2)


class TestTotConfig(unittest.TestCase):
    """TotConfig 配置测试。"""

    def test_default_config(self):
        cfg = TotConfig()
        self.assertEqual(cfg.search_algorithm, "bfs")
        self.assertEqual(cfg.branching_factor, 3)
        self.assertEqual(cfg.beam_width, 2)
        self.assertEqual(cfg.max_depth, 5)
        self.assertEqual(cfg.max_total_nodes, 50)

    def test_custom_config(self):
        cfg = TotConfig(
            search_algorithm="dfs",
            branching_factor=4,
            beam_width=3,
            max_depth=10,
        )
        self.assertEqual(cfg.search_algorithm, "dfs")
        self.assertEqual(cfg.branching_factor, 4)

    def test_to_dict(self):
        cfg = TotConfig(enabled=True)
        d = cfg.to_dict()
        self.assertTrue(d["enabled"])
        self.assertEqual(d["search_algorithm"], "bfs")


class TestThoughtTree(unittest.TestCase):
    """ThoughtTree 数据模型测试。"""

    def test_create(self):
        tree = ThoughtTree.create(goal="test goal", initial_state="start")
        self.assertEqual(tree.goal, "test goal")
        self.assertEqual(len(tree.nodes), 1)
        self.assertIn(tree.root_id, tree.nodes)
        root = tree.nodes[tree.root_id]
        self.assertEqual(root.state, "start")
        self.assertEqual(root.depth, 0)

    def test_add_node(self):
        tree = ThoughtTree.create(goal="g", initial_state="s")
        child = tree.add_node(parent_id=tree.root_id, state="child state", value=0.9)
        self.assertEqual(len(tree.nodes), 2)
        self.assertEqual(child.parent_id, tree.root_id)
        self.assertEqual(child.depth, 1)
        self.assertEqual(child.value, 0.9)
        # verify parent has child reference
        root = tree.nodes[tree.root_id]
        self.assertIn(child.node_id, root.children)

    def test_add_node_invalid_parent(self):
        tree = ThoughtTree.create(goal="g", initial_state="s")
        with self.assertRaises(ValueError):
            tree.add_node(parent_id="nonexistent", state="x")

    def test_leaf_nodes(self):
        tree = ThoughtTree.create(goal="g", initial_state="s")
        self.assertEqual(len(tree.leaf_nodes()), 1)
        tree.add_node(parent_id=tree.root_id, state="c1")
        self.assertEqual(len(tree.leaf_nodes()), 1)
        tree.add_node(parent_id=tree.root_id, state="c2")
        self.assertEqual(len(tree.leaf_nodes()), 2)

    def test_active_leaves(self):
        tree = ThoughtTree.create(goal="g", initial_state="s")
        c1 = tree.add_node(parent_id=tree.root_id, state="c1", status="maybe")
        tree.add_node(parent_id=tree.root_id, state="c2", status="impossible")
        active = tree.active_leaves()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].node_id, c1.node_id)

    def test_best_path_single(self):
        tree = ThoughtTree.create(goal="g", initial_state="root")
        path = tree.best_path()
        self.assertEqual(len(path), 1)
        self.assertEqual(path[0].state, "root")

    def test_best_path_multi(self):
        tree = ThoughtTree.create(goal="g", initial_state="root")
        tree.add_node(parent_id=tree.root_id, state="child1", value=0.5)
        c2 = tree.add_node(parent_id=tree.root_id, state="child2", value=0.9)
        tree.add_node(parent_id=c2.node_id, state="grandchild", value=0.95)
        path = tree.best_path()
        self.assertEqual(len(path), 3)  # root → child2 → grandchild
        self.assertEqual(path[0].state, "root")
        self.assertEqual(path[1].state, "child2")
        self.assertEqual(path[2].state, "grandchild")

    def test_best_answer(self):
        tree = ThoughtTree.create(goal="g", initial_state="start")
        tree.add_node(parent_id=tree.root_id, state="final answer", value=0.9)
        ans = tree.best_answer()
        self.assertEqual(ans, "final answer")

    def test_total_nodes(self):
        tree = ThoughtTree.create(goal="g", initial_state="root")
        tree.add_node(parent_id=tree.root_id, state="a")
        tree.add_node(parent_id=tree.root_id, state="b")
        self.assertEqual(tree.total_nodes(), 3)

    def test_max_depth(self):
        tree = ThoughtTree.create(goal="g", initial_state="root")
        c1 = tree.add_node(parent_id=tree.root_id, state="a")
        tree.add_node(parent_id=c1.node_id, state="b")
        self.assertEqual(tree.max_depth_reached(), 2)

    def test_empty_tree_max_depth(self):
        tree = ThoughtTree(root_id="r", nodes={}, goal="g", config=TotConfig())
        self.assertEqual(tree.max_depth_reached(), 0)

    def test_to_dict(self):
        tree = ThoughtTree.create(goal="g", initial_state="s")
        d = tree.to_dict()
        self.assertEqual(d["goal"], "g")
        self.assertEqual(d["total_nodes"], 1)
        self.assertEqual(d["root_id"], tree.root_id)


class TestCandidateThought(unittest.TestCase):
    """CandidateThought 数据模型测试。"""

    def test_default(self):
        c = CandidateThought(text="hello")
        self.assertEqual(c.text, "hello")
        self.assertIsNone(c.value)
        self.assertEqual(c.status, "pending")

    def test_full(self):
        c = CandidateThought(text="world", value=0.8, status="sure")
        self.assertEqual(c.text, "world")
        self.assertEqual(c.value, 0.8)
        self.assertEqual(c.status, "sure")


class TestTotResult(unittest.TestCase):
    """TotResult 数据模型测试。"""

    def test_to_dict(self):
        tree = ThoughtTree.create(goal="g", initial_state="s")
        result = TotResult(
            tree=tree,
            best_answer="answer",
            best_value=0.9,
            total_nodes=1,
            search_depth=0,
            execution_time_ms=100.0,
        )
        d = result.to_dict()
        self.assertEqual(d["best_answer"], "answer")
        self.assertEqual(d["best_value"], 0.9)
        self.assertEqual(d["total_nodes"], 1)
        self.assertIn("tree", d)


class TestThoughtGenerator(unittest.IsolatedAsyncioTestCase):
    """ThoughtGenerator 单元测试（mock LLM）。"""

    async def test_generate_returns_candidates(self):
        mock_route = MagicMock()
        mock_route.status = 200
        mock_route.body = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '['
                            '{"thought": "先计算个位"},'
                            '{"thought": "先计算十位"},'
                            '{"thought": "用分配律"}'
                            ']'
                        )
                    }
                }
            ]
        }

        with patch(
            "packages.agent.tot.generator.forward_with_model_router",
            AsyncMock(return_value=mock_route),
        ):
            from packages.agent.tot.generator import ThoughtGenerator

            gen = ThoughtGenerator(model="test-model")
            candidates = await gen.generate(
                state="1+1=?",
                goal="计算 1+1",
                n_candidates=3,
            )
            self.assertEqual(len(candidates), 3)
            self.assertEqual(candidates[0].text, "先计算个位")
            self.assertEqual(candidates[1].text, "先计算十位")

    async def test_generate_empty_on_llm_error(self):
        with patch(
            "packages.agent.tot.generator.forward_with_model_router",
            AsyncMock(side_effect=RuntimeError("API down")),
        ):
            from packages.agent.tot.generator import ThoughtGenerator

            gen = ThoughtGenerator(model="test-model")
            candidates = await gen.generate(
                state="test", goal="test", n_candidates=2
            )
            self.assertEqual(candidates, [])

    async def test_generate_empty_on_upstream_error(self):
        mock_route = MagicMock()
        mock_route.status = 500
        mock_route.body = None

        with patch(
            "packages.agent.tot.generator.forward_with_model_router",
            AsyncMock(return_value=mock_route),
        ):
            from packages.agent.tot.generator import ThoughtGenerator

            gen = ThoughtGenerator(model="test-model")
            candidates = await gen.generate(
                state="test", goal="test", n_candidates=2
            )
            self.assertEqual(candidates, [])


class TestThoughtEvaluator(unittest.IsolatedAsyncioTestCase):
    """ThoughtEvaluator 单元测试（mock LLM）。"""

    def _make_mock_route(self, status_code: int, body: dict | None):
        mock_route = MagicMock()
        mock_route.status = status_code
        mock_route.body = body
        return mock_route

    async def test_evaluate_candidates(self):
        mock_route = self._make_mock_route(200, {
            "choices": [
                {
                    "message": {
                        "content": '{"value": 0.85, "status": "sure", "reason": "正确方法"}'
                    }
                }
            ]
        })

        with patch(
            "packages.agent.tot.evaluator.forward_with_model_router",
            AsyncMock(return_value=mock_route),
        ):
            from packages.agent.tot.evaluator import ThoughtEvaluator

            evaluator = ThoughtEvaluator(model="test-model")
            candidates = [
                CandidateThought(text="用乘法分配律"),
                CandidateThought(text="直接计算"),
            ]
            results = await evaluator.evaluate(candidates, goal="计算 23×45")
            self.assertEqual(len(results), 2)
            self.assertIsNotNone(results[0].value)
            self.assertEqual(results[0].status, "sure")

    async def test_evaluate_fallback_on_error(self):
        with patch(
            "packages.agent.tot.evaluator.forward_with_model_router",
            AsyncMock(side_effect=RuntimeError("API down")),
        ):
            from packages.agent.tot.evaluator import ThoughtEvaluator

            evaluator = ThoughtEvaluator(model="test-model")
            candidates = [CandidateThought(text="test thought")]
            results = await evaluator.evaluate(candidates, goal="test")
            # 降级：保留原始候选，默认评分
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].value, 0.5)
            self.assertEqual(results[0].status, "maybe")


class TestTotSearcher(unittest.IsolatedAsyncioTestCase):
    """TotSearcher 单元测试（mock Generator + Evaluator）。"""

    async def test_bfs_search(self):
        from packages.agent.tot.evaluator import ThoughtEvaluator
        from packages.agent.tot.generator import ThoughtGenerator
        from packages.agent.tot.searcher import TotSearcher

        gen = ThoughtGenerator(model="test-model")
        ev = ThoughtEvaluator(model="test-model")
        searcher = TotSearcher(generator=gen, evaluator=ev)

        tree = ThoughtTree.create(
            goal="数学题",
            initial_state="题目：25×4",
            config=TotConfig(
                search_algorithm="bfs",
                branching_factor=2,
                beam_width=2,
                max_depth=2,
            ),
        )

        # mock LLM 调用
        mock_candidates = (
            '[{"thought":"25×4=100"}, {"thought":"拆成20×4+5×4"}]'
        )
        mock_eval = '{"value": 0.9, "status": "sure", "reason": "合理"}'

        async def mock_forward(payload):
            route = MagicMock()
            route.status = 200
            route.body = {"choices": [{"message": {"content": mock_candidates}}]}
            return route

        async def mock_forward_eval(payload):
            route = MagicMock()
            route.status = 200
            route.body = {"choices": [{"message": {"content": mock_eval}}]}
            return route

        with patch(
            "packages.agent.tot.generator.forward_with_model_router",
            mock_forward,
        ), patch(
            "packages.agent.tot.evaluator.forward_with_model_router",
            mock_forward_eval,
        ):
            result_tree = await searcher.search(tree)
            self.assertGreater(result_tree.total_nodes(), 1)
            self.assertGreaterEqual(result_tree.max_depth_reached(), 1)

    async def test_dfs_search(self):
        from packages.agent.tot.searcher import TotSearcher

        gen_mock = MagicMock()
        gen_mock.generate = AsyncMock(return_value=[
            CandidateThought(text="step 1", value=0.8),
            CandidateThought(text="step 2", value=0.6),
        ])
        ev_mock = MagicMock()
        ev_mock.evaluate = AsyncMock(return_value=[
            CandidateThought(text="step 1", value=0.8, status="sure"),
            CandidateThought(text="step 2", value=0.6, status="maybe"),
        ])

        searcher = TotSearcher(generator=gen_mock, evaluator=ev_mock)
        tree = ThoughtTree.create(
            goal="test",
            initial_state="start",
            config=TotConfig(search_algorithm="dfs", branching_factor=2, max_depth=2),
        )
        result = await searcher.search(tree)
        self.assertGreater(result.total_nodes(), 1)


class TestRunTot(unittest.IsolatedAsyncioTestCase):
    """run_tot 入口集成测试（全部 mock）。"""

    async def test_run_tot_success(self):
        mock_candidates = '[{"thought":"answer is 100"}]'

        async def mock_llm(_payload):
            route = MagicMock()
            route.status = 200
            route.body = {"choices": [{"message": {"content": mock_candidates}}]}
            return route

        with patch(
            "packages.agent.tot.generator.forward_with_model_router",
            mock_llm,
        ), patch(
            "packages.agent.tot.evaluator.forward_with_model_router",
            mock_llm,
        ):
            from packages.agent.tot import run_tot

            result = await run_tot(
                goal="25×4=?",
                initial_state="计算 25×4",
                config=TotConfig(
                    search_algorithm="bfs",
                    branching_factor=1,
                    beam_width=1,
                    max_depth=1,
                ),
            )
            self.assertIsNotNone(result.best_answer)
            self.assertGreater(result.total_nodes, 0)
            self.assertGreater(result.execution_time_ms, 0)
            self.assertIsNone(result.error)

    async def test_run_tot_error_handling(self):
        with patch(
            "packages.agent.tot.generator.forward_with_model_router",
            AsyncMock(side_effect=RuntimeError("API error")),
        ):
            from packages.agent.tot import run_tot

            result = await run_tot(
                goal="test",
                config=TotConfig(branching_factor=1, max_depth=1),
            )
            # LLM 失败时：生成返回空列表 → 搜索无候选 → 返回根节点
            # best_answer 应为初始 state（goal）
            self.assertEqual(result.total_nodes, 1)  # 只有根节点
            self.assertIsNotNone(result.best_answer)
            self.assertIsNone(result.error)  # 不会抛出异常，优雅降级

    async def test_run_tot_with_defaults(self):
        from packages.agent.tot import run_tot

        with patch(
            "packages.agent.tot.generator.forward_with_model_router",
            AsyncMock(return_value=MagicMock(status=500, body=None)),
        ):
            # 无 config、无 initial_state 也能运行
            result = await run_tot(goal="test")
            self.assertIsNotNone(result)
            self.assertIsInstance(result.tree.total_nodes(), int)


class TestMctsTotSearcher(unittest.IsolatedAsyncioTestCase):
    """MCTS 搜索器单元测试（mock Generator + Evaluator）。"""

    async def test_mcts_search(self):
        from packages.agent.tot.searcher import TotSearcher

        gen_mock = MagicMock()
        gen_mock.generate = AsyncMock(return_value=[
            CandidateThought(text="branch 1"),
            CandidateThought(text="branch 2"),
        ])
        ev_mock = MagicMock()
        ev_mock.evaluate = AsyncMock(return_value=[
            CandidateThought(text="branch 1", value=0.8, status="sure"),
            CandidateThought(text="branch 2", value=0.6, status="maybe"),
        ])

        searcher = TotSearcher(generator=gen_mock, evaluator=ev_mock)
        tree = ThoughtTree.create(
            goal="test",
            initial_state="start",
            config=TotConfig(
                search_algorithm="mcts",
                branching_factor=2,
                max_depth=2,
                mcts_simulations=3,
                mcts_exploration_weight=1.4,
            ),
        )
        result = await searcher.search(tree)
        self.assertGreater(result.total_nodes(), 1)
        # 验证 visits 被更新
        root = result.get_node(result.root_id)
        self.assertIsNotNone(root)
        self.assertGreater(root.visits, 0)  # type: ignore[union-attr]

    async def test_mcts_backpropagate_updates_values(self):
        from packages.agent.tot.searcher import TotSearcher

        gen_mock = MagicMock()
        gen_mock.generate = AsyncMock(return_value=[
            CandidateThought(text="child"),
        ])
        ev_mock = MagicMock()
        ev_mock.evaluate = AsyncMock(return_value=[
            CandidateThought(text="child", value=0.9, status="sure"),
        ])

        searcher = TotSearcher(generator=gen_mock, evaluator=ev_mock)
        tree = ThoughtTree.create(
            goal="test",
            initial_state="root",
            config=TotConfig(
                search_algorithm="mcts",
                branching_factor=1,
                max_depth=1,
                mcts_simulations=2,
            ),
        )
        result = await searcher.search(tree)
        root = result.get_node(result.root_id)
        self.assertIsNotNone(root)
        self.assertGreater(root.visits, 0)  # type: ignore[union-attr]

    async def test_mcts_search_via_run_tot(self):
        """通过 run_tot 入口使用 MCTS。"""
        mock_candidates = '[{"thought":"step 1"}]'

        async def mock_llm(_p):
            route = MagicMock()
            route.status = 200
            route.body = {"choices": [{"message": {"content": mock_candidates}}]}
            return route

        with patch(
            "packages.agent.tot.generator.forward_with_model_router",
            mock_llm,
        ), patch(
            "packages.agent.tot.evaluator.forward_with_model_router",
            mock_llm,
        ):
            from packages.agent.tot import run_tot

            result = await run_tot(
                goal="test",
                initial_state="start",
                config=TotConfig(
                    search_algorithm="mcts",
                    branching_factor=1,
                    max_depth=2,
                    mcts_simulations=2,
                ),
            )
            self.assertIsNotNone(result.best_answer)
            self.assertGreaterEqual(result.total_nodes, 1)


if __name__ == "__main__":
    unittest.main()
