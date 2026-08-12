# ToT (Tree of Thoughts) Agent 模板

> 在 LLM 推理时维护一棵「思维树」，每个节点是一个推理中间状态，通过 BFS/DFS/MCTS 搜索找到最优路径。

## 架构

```
run_tot(goal, config)
  │
  ├─ Generator: state → [candidate1, candidate2, ...]
  ├─ Evaluator: candidate → {value, status}  (sure/maybe/impossible)
  └─ Searcher:
       ├─ BFS: beam search（每层保留 top-K）
       ├─ DFS: 回溯（value >= threshold 继续深入）
       └─ MCTS: UCB + 选择→扩展→模拟→回溯
  │
  └─ best_path() → 最优推理链
```

## 支持三种算法

| 算法 | 配置 | 特点 |
|------|------|------|
| BFS | `search_algorithm: "bfs"` | 宽度优先，beam search |
| DFS | `search_algorithm: "dfs"` | 深度优先，阈值剪枝 |
| MCTS | `search_algorithm: "mcts"` | UCB 公式平衡探索/利用 |

## 代码骨架

```python
from packages.agent.tot import run_tot, TotConfig

result = await run_tot(
    goal="计算 23 × 45",
    initial_state="题目：23 × 45",
    config=TotConfig(
        search_algorithm="bfs",
        branching_factor=3,  # 每个节点生成 3 个候选
        beam_width=2,        # BFS 每层保留 2 个
        max_depth=5,
    ),
)
print(result.best_answer)   # 1035
print(result.best_path())    # 最优路径节点列表
print(result.total_nodes)    # 搜索树总节点数
```

## curl 调用

```bash
curl -s http://127.0.0.1:8000/v1/agent/tot \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{
    "tenant_id": "admin",
    "session_id": "tot-demo",
    "goal": "计算 23 × 45",
    "tot_config": {
      "search_algorithm": "bfs",
      "branching_factor": 3,
      "beam_width": 2,
      "max_depth": 5
    }
  }'
```

### Plan 增强模式（ToT → Planner）

ToT 产出可作为 Planner 的 context 注入：

```bash
curl -s http://127.0.0.1:8000/v1/agent/run \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{
    "tenant_id": "admin",
    "session_id": "plan-tot-demo",
    "auto_plan": true,
    "goal": "分析销售数据并生成报告",
    "tot_config": {
      "enabled": true,
      "search_algorithm": "bfs",
      "branching_factor": 2,
      "beam_width": 2,
      "max_depth": 3
    }
  }'
```

## 关键配置（`config/agent_tot.yaml`）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `search_algorithm` | bfs | bfs / dfs / mcts |
| `branching_factor` | 3 | 每节点候选数 |
| `beam_width` | 2 | BFS beam 宽度 |
| `max_depth` | 5 | 最大深度 |
| `max_total_nodes` | 50 | 总节点上限（安全阀）|
| `mcts_exploration_weight` | 1.4 | UCB 探索常数 C |
| `mcts_simulations` | 5 | MCTS 模拟次数 |

## 核心文件

| 路径 | 职责 |
|------|------|
| `packages/agent/tot/__init__.py` | `run_tot()` 编排入口 |
| `packages/agent/tot/tree.py` | `ThoughtNode`, `ThoughtTree` 数据模型 |
| `packages/agent/tot/generator.py` | LLM 候选思维生成 |
| `packages/agent/tot/evaluator.py` | 思维评分分类 |
| `packages/agent/tot/searcher.py` | BFS / DFS / MCTS 搜索 |

## 与其它模式的关系

- ToT 的 Generator 和 Evaluator 底层使用 LLM（同 ReAct 的 `forward_with_model_router`）
- ToT 可单独使用，也可作为 Planner 的前置推理增强
- 类比：ToT 是**单模型多路径**搜索，Debate 是**多模型观点碰撞**
