# Multi-Agent Debate 模板

> 多个 Agent 围绕同一问题独立推理、互相评议、收敛答案。复用 `parallel_delegate()` 和 `BlackboardStore`。

## 架构

```
run_debate(question, config)
  │
  ├─ Round 1: 并行提案
  │   └─ parallel_delegate(proposer_1, proposer_2, proposer_3)
  │       → 黑板写入 3 条提案
  │
  ├─ Round 2: 交叉评议
  │   └─ parallel_delegate(critic_1, critic_2, critic_3)
  │       → 每个 critic 评审其他人的提案
  │       → 黑板写入 3 条评议
  │
  ├─ (可选 Round 3): 反驳/修订
  │   └─ proposers 看到 critic 反馈后修订
  │
  └─ Final: 裁定
      └─ delegate_to_agent(judge, 全部提案+评议)
          → 最终答案 + 置信度
```

## 角色定义（`config/agents.yaml`）

```yaml
- agent_id: debate_proposer_1
  role: specialist
  system_prompt: "你是辩论提案者。从正面角度推理..."
  allowed_tools: []

- agent_id: debate_critic_1
  role: reviewer  # 自动获取黑板上下文
  system_prompt: "你是评论者。严格评审逻辑完整性..."
  allowed_tools: []

- agent_id: debate_judge
  role: reviewer
  system_prompt: "你是裁判。综合所有信息给出最终答案..."
  allowed_tools: []
```

## 代码骨架

```python
from packages.agent.debate import run_debate, DebateConfig

result = await run_debate(
    question="TCP 三次握手的作用是什么？",
    config=DebateConfig(
        num_proposers=3,    # 3 个提案者
        num_rounds=2,       # 提案 + 评议（无反驳）
    ),
)
print(result.verdict)            # 最终答案
print(result.verdict_confidence)  # 置信度 0-1
print(len(result.proposals))     # 提案数
print(len(result.critiques))     # 评议数
```

## curl 调用

```bash
curl -s http://127.0.0.1:8000/v1/agent/debate \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{
    "tenant_id": "admin",
    "session_id": "debate-demo",
    "goal": "TCP 三次握手的作用是什么？",
    "debate_config": {
      "num_proposers": 3,
      "num_rounds": 2
    }
  }'
```

## 关键配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `num_proposers` | 3 | 提案 Agent 数 |
| `num_rounds` | 2 | 辩论轮数（1=仅提案, 2=提案+评议, 3=含反驳）|
| `temperature` | 0.7 | Proposer 生成温度 |
| `critic_temperature` | 0.3 | Critic 评议温度 |
| `judge_temperature` | 0.1 | Judge 裁定温度 |

## 核心文件

| 路径 | 职责 |
|------|------|
| `packages/agent/debate/__init__.py` | `run_debate()` 编排器 |
| `packages/agent/debate/models.py` | DebateConfig, DebateProposal, DebateCritique |
| `packages/agent/multi_agent/delegation.py` | `parallel_delegate()` 并行委托 |
| `packages/agent/multi_agent/blackboard.py` | 黑板存储和共享上下文 |
| `config/agents.yaml` | 辩论角色定义 |

## 与其它模式的关系

- Debate 的并行委托基础设施（`parallel_delegate`）复用自 Phase H/O 的 Multi-Agent 框架
- 类比：ToT 是**单模型多路径**探索，Debate 是**多模型多视角**碰撞
- Research 可以使用 Debate 作为信息综合阶段的增强（多视角评估)
