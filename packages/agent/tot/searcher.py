from __future__ import annotations

import logging
import time

from packages.agent.tot.evaluator import ThoughtEvaluator
from packages.agent.tot.generator import ThoughtGenerator
from packages.agent.tot.tree import (
    CandidateThought,
    ThoughtTree,
    TotConfig,
)

logger = logging.getLogger("ai_platform.agent.tot.searcher")


class TotSearcher:
    """ToT 搜索器。

    将 Generator + Evaluator 组合成 BFS 或 DFS 搜索算法，
    在思维树上搜索最优推理路径。
    """

    def __init__(
        self,
        generator: ThoughtGenerator,
        evaluator: ThoughtEvaluator,
    ):
        self._generator = generator
        self._evaluator = evaluator

    async def search(
        self,
        tree: ThoughtTree,
        config: TotConfig | None = None,
    ) -> ThoughtTree:
        """在思维树上执行搜索，返回更新后的树。"""
        cfg = config or tree.config
        algorithm = cfg.search_algorithm

        if algorithm == "bfs":
            return await self._search_bfs(tree, cfg)
        elif algorithm == "dfs":
            return await self._search_dfs(tree, cfg)
        elif algorithm == "mcts":
            return await self._search_mcts(tree, cfg)
        else:
            logger.warning("searcher: unknown algorithm %s, falling back to bfs", algorithm)
            return await self._search_bfs(tree, cfg)

    async def _search_bfs(self, tree: ThoughtTree, cfg: TotConfig) -> ThoughtTree:
        """BFS + beam search。

        每层：
          1. 对每个活跃叶节点生成 branching_factor 个候选
          2. 评估并排序所有候选
          3. 保留 beam_width 个最优候选
          4. 深度 +1
        """
        depth = 0
        start_time = time.time()

        while depth < cfg.max_depth:
            if time.time() - start_time > cfg.timeout_seconds:
                logger.warning("searcher: timeout at depth %d", depth)
                break

            if tree.total_nodes() >= cfg.max_total_nodes:
                logger.warning(
                    "searcher: max_total_nodes %d reached at depth %d",
                    cfg.max_total_nodes,
                    depth,
                )
                break

            active = tree.active_leaves()
            if not active:
                logger.info("searcher: no active leaves at depth %d, stopping", depth)
                break

            # 对每个活跃叶节点生成候选
            all_candidates: list[tuple[str, CandidateThought]] = []
            for leaf in active:
                candidates = await self._generator.generate(
                    state=leaf.state,
                    goal=tree.goal,
                    n_candidates=cfg.branching_factor,
                    config=cfg,
                )
                for c in candidates:
                    all_candidates.append((leaf.node_id, c))

            if not all_candidates:
                logger.info("searcher: no candidates generated at depth %d", depth)
                break

            # 评估所有候选
            candidate_texts = [c for _, c in all_candidates]
            evaluated = await self._evaluator.evaluate(candidate_texts, tree.goal, cfg)

            # 配对 parent_id + 评估结果
            paired: list[tuple[str, CandidateThought]] = []
            for (parent_id, _), ev in zip(all_candidates, evaluated):
                paired.append((parent_id, ev))

            # 过滤掉 impossible 的候选
            viable = [(pid, c) for pid, c in paired if c.status != "impossible"]
            if not viable:
                logger.info("searcher: all candidates impossible at depth %d", depth)
                break

            # 按 value 降序排列
            viable.sort(key=lambda x: x[1].value or 0.0, reverse=True)

            # 保留 beam_width 个
            selected = viable[: cfg.beam_width]

            # 加入树
            for parent_id, candidate in selected:
                tree.add_node(
                    parent_id=parent_id,
                    state=candidate.text,
                    value=candidate.value,
                    status=candidate.status,
                    metadata=candidate.metadata,
                )

            # 未选中的叶节点标记为剪枝
            selected_parents = {pid for pid, _ in selected}
            for leaf in active:
                if leaf.node_id not in selected_parents and leaf.status == "pending":
                    leaf.status = "pruned"

            depth += 1

        return tree

    async def _search_dfs(self, tree: ThoughtTree, cfg: TotConfig) -> ThoughtTree:
        """DFS + 回溯 + 评分阈值。

        从根开始，每步：
          1. 对当前节点生成 branching_factor 个候选
          2. 评估候选
          3. 选最优的继续探索（value >= threshold）
          4. 低于阈值的回溯
        """
        start_time = time.time()

        async def _dfs_explore(node_id: str, depth: int) -> None:
            if time.time() - start_time > cfg.timeout_seconds:
                return
            if tree.total_nodes() >= cfg.max_total_nodes:
                return
            if depth >= cfg.max_depth:
                return

            node = tree.get_node(node_id)
            if node is None:
                return

            candidates = await self._generator.generate(
                state=node.state,
                goal=tree.goal,
                n_candidates=cfg.branching_factor,
                config=cfg,
            )
            if not candidates:
                return

            evaluated = await self._evaluator.evaluate(candidates, tree.goal, cfg)
            # 过滤 impossible
            viable = [c for c in evaluated if c.status != "impossible"]
            if not viable:
                return

            # 按 value 排序
            viable.sort(key=lambda x: x.value or 0.0, reverse=True)

            for candidate in viable:
                threshold = cfg.value_threshold
                if threshold is not None and (candidate.value or 0.0) < threshold:
                    continue

                child = tree.add_node(
                    parent_id=node_id,
                    state=candidate.text,
                    value=candidate.value,
                    status=candidate.status,
                    metadata=candidate.metadata,
                )
                await _dfs_explore(child.node_id, depth + 1)

        await _dfs_explore(tree.root_id, 0)
        return tree

    async def _search_mcts(self, tree: ThoughtTree, cfg: TotConfig) -> ThoughtTree:
        """MCTS (Monte Carlo Tree Search)。

        四步循环：
          1. Select — UCB 选择最优子节点直到叶节点
          2. Expand — 生成候选子节点
          3. Simulate — 评估候选
          4. Backpropagate — 沿路径更新 value 和 visits
        """
        start_time = time.time()
        total_simulations = cfg.mcts_simulations * cfg.max_depth
        root = tree.get_node(tree.root_id)
        if root is None:
            return tree

        for sim in range(total_simulations):
            if time.time() - start_time > cfg.timeout_seconds:
                logger.warning("mcts: timeout at simulation %d", sim)
                break
            if tree.total_nodes() >= cfg.max_total_nodes:
                break

            # 1. Select: UCB 选择
            path: list[str] = []
            node_id = tree.root_id
            while True:
                path.append(node_id)
                node = tree.get_node(node_id)
                if node is None:
                    break
                if not node.children:
                    break  # 叶节点
                # UCB 选择最佳子节点
                import math

                best_child_id: str | None = None
                best_ucb = -float("inf")
                n_parent = node.visits or 1  # 避免除零
                for cid in node.children:
                    child = tree.get_node(cid)
                    if child is None:
                        continue
                    if child.status == "impossible":
                        continue
                    n_child = max(child.visits, 1)
                    exploit = child.value or 0.0
                    explore = cfg.mcts_exploration_weight * math.sqrt(
                        math.log(n_parent) / n_child
                    )
                    ucb = exploit + explore
                    if ucb > best_ucb:
                        best_ucb = ucb
                        best_child_id = cid

                if best_child_id is None:
                    break
                node_id = best_child_id

            leaf = tree.get_node(node_id)
            if leaf is None:
                continue

            # 2. Expand: 生成候选
            candidates = await self._generator.generate(
                state=leaf.state,
                goal=tree.goal,
                n_candidates=cfg.branching_factor,
                config=cfg,
            )
            if not candidates:
                # 无法扩展，标记为 impossible 避免重复选择
                leaf.status = "impossible"
                continue

            # 3. Simulate: 评估候选
            evaluated = await self._evaluator.evaluate(candidates, tree.goal, cfg)

            # 添加到树
            child_ids: list[str] = []
            for candidate in evaluated:
                if candidate.status == "impossible":
                    continue
                child = tree.add_node(
                    parent_id=node_id,
                    state=candidate.text,
                    value=candidate.value,
                    status=candidate.status,
                    metadata=candidate.metadata,
                )
                child_ids.append(child.node_id)

            if not child_ids:
                leaf.status = "impossible"
                continue

            # 4. Backpropagate: 回溯更新
            # 使用子节点平均 value 作为奖励
            child_values = [
                (tree.get_node(cid).value or 0.0) for cid in child_ids
                if tree.get_node(cid) is not None
            ]
            reward = sum(child_values) / len(child_values) if child_values else 0.0

            for pid in reversed(path):
                pnode = tree.get_node(pid)
                if pnode is None:
                    continue
                pnode.visits += 1
                old_val = pnode.value or 0.0
                pnode.value = old_val + (reward - old_val) / pnode.visits

        return tree

