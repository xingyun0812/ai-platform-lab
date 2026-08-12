"""packages/agent/tot — Tree of Thoughts (ToT) 推理模式。

ToT 在 LLM 推理时维护一棵「思维树」，每个节点是一个推理中间状态，
通过 BFS/DFS 搜索找到最优路径。

用法：
    result = await run_tot(
        goal="计算 23 × 45",
        initial_state="题目：23 × 45",
        config=TotConfig(search_algorithm="bfs", branching_factor=3, beam_width=2),
    )
    print(result.best_answer)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from packages.agent.tot.evaluator import ThoughtEvaluator
from packages.agent.tot.generator import ThoughtGenerator
from packages.agent.tot.searcher import TotSearcher
from packages.agent.tot.tree import ThoughtTree, TotConfig, TotResult

logger = logging.getLogger("ai_platform.agent.tot")

__all__ = [
    "run_tot",
    "ThoughtTree",
    "TotConfig",
    "TotResult",
    "ThoughtGenerator",
    "ThoughtEvaluator",
    "TotSearcher",
]


async def run_tot(
    goal: str,
    initial_state: str | None = None,
    config: TotConfig | None = None,
    model: str | None = None,
) -> TotResult:
    """运行 ToT 搜索的主入口。

    Args:
        goal: 推理问题的目标描述。
        initial_state: 初始推理状态。默认为 goal 本身。
        config: 搜索策略配置。缺省使用 TotConfig 默认值。
        model: 用于生成和评估的模型名。缺省使用 settings 中的默认模型。

    Returns:
        TotResult 包含思维树、最优答案、搜索统计。
    """
    cfg = config or TotConfig()
    resolved_state = initial_state or goal
    tree = ThoughtTree.create(goal=goal, initial_state=resolved_state, config=cfg)

    generator = ThoughtGenerator(model=model, temperature=cfg.temperature)
    evaluator = ThoughtEvaluator(model=model)
    searcher = TotSearcher(generator=generator, evaluator=evaluator)

    start = time.time()
    trace: list[dict[str, Any]] = []

    try:
        trace.append({"event": "search_start", "algorithm": cfg.search_algorithm, "config": cfg.to_dict()})
        tree = await searcher.search(tree, cfg)
        best_ans = tree.best_answer()
        best_val = max(
            (n.value or 0.0) for n in tree.leaf_nodes()
        ) if tree.leaf_nodes() else 0.0
        elapsed = (time.time() - start) * 1000
        trace.append({
            "event": "search_complete",
            "total_nodes": tree.total_nodes(),
            "max_depth": tree.max_depth_reached(),
            "elapsed_ms": elapsed,
        })
        logger.info(
            "tot search completed: goal=%.40s nodes=%d depth=%d elapsed=%.0fms",
            goal,
            tree.total_nodes(),
            tree.max_depth_reached(),
            elapsed,
        )
        return TotResult(
            tree=tree,
            best_answer=best_ans,
            best_value=best_val,
            total_nodes=tree.total_nodes(),
            search_depth=tree.max_depth_reached(),
            execution_time_ms=elapsed,
            trace=trace,
        )
    except Exception as exc:
        elapsed = (time.time() - start) * 1000
        logger.error("tot search failed: %s", exc)
        trace.append({"event": "search_error", "error": str(exc)})
        return TotResult(
            tree=tree,
            best_answer=tree.best_answer(),
            best_value=0.0,
            total_nodes=tree.total_nodes(),
            search_depth=tree.max_depth_reached(),
            execution_time_ms=elapsed,
            trace=trace,
            error=str(exc),
        )