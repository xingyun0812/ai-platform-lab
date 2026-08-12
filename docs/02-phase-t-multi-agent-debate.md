# Phase T — Multi-Agent Debate 推理模式

> **状态**：✅ **已交付**（Phase T · 2026-08-05）
> **前置**：Phase H（Multi-Agent 基础设施）· Phase O（Delegation v2）· Phase S（ToT）
> **门禁**：`python eval/debate_quality_gate.py run && gate`

---

## 1. 动机

ToT 的局限在于所有思维节点由同一个 LLM 生成和评估。对于需要权衡不同观点的事实性问题，多 Agent 辩论更有优势：

1. **多视角** — 不同 prompt/模型产生不同推理路径
2. **交叉验证** — Critic 评审可发现单个 Agent 的盲点
3. **收敛机制** — Judge 综合全部信息给出裁定

## 2. 架构

```
run_debate(question, config)
  │
  ├─ Round 1: 并行提案
  │   └─ parallel_delegate(proposer_1, proposer_2, proposer_3)
  │
  ├─ Round 2: 交叉评议
  │   └─ parallel_delegate(critic_1, critic_2, critic_3)
  │
  ├─ (可选 Round 3): 反驳/修订
  │
  └─ Final: 裁定
      └─ delegate_to_agent(judge) → 最终答案 + 置信度
```

## 3. API

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

## 4. 配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `num_proposers` | 3 | 提案 Agent 数 |
| `num_rounds` | 2 | 辩论轮数 |
| `temperature` | 0.7 | Proposer 温度 |
| `critic_temperature` | 0.3 | Critic 温度 |
| `judge_temperature` | 0.1 | Judge 温度 |

## 5. 核心文件

| 路径 | 职责 |
|------|------|
| `packages/agent/debate/__init__.py` | `run_debate()` 编排器 |
| `packages/agent/debate/models.py` | 数据模型 |
| `config/agents.yaml` | 辩论角色定义（proposer×3, critic×3, judge） |
| `config/agent_debate.yaml` | 配置 |
| `apps/gateway/agent/routes.py` | `POST /v1/agent/debate` |
| `tests/test_debate.py` | 15 个单元测试 |
| `docs/adr/0005-multi-agent-debate.md` | 架构决策 |
