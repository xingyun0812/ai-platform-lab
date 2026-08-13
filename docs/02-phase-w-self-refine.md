# Phase W — Self-Refine（自我修正推理）

> **状态**：✅ **已交付**（Phase W · 2026-08-12）
> **Issue**：[#204](https://github.com/xingyun0812/ai-platform-lab/issues/204)
> **ADR**：[0008-self-refine.md](./adr/0008-self-refine.md)
> **门禁**：`python eval/self_refine_quality_gate.py run && gate`

## 概述

Self-Refine（Madaan et al., 2023）是一种单 Agent 自我迭代推理模式：

```
初始输出 → 自我反馈 → 自我修正 → 收敛检查 →（未收敛）→ 继续反馈
                                            →（收敛）→ 返回最终输出
```

与项目中已有的高级推理模式形成互补：

| 模式 | 特点 | Phase |
|------|------|-------|
| CoT | 链式思考 | Phase O |
| ToT | 树搜索 + BFS/DFS | Phase S |
| Debate | 多角色辩论 | Phase T |
| Deep Research | 搜索-阅读-综合 | Phase U |
| Computer Use | GUI 操作 | Phase V |
| **Self-Refine** | **单 Agent 自我迭代修正** | **Phase W** |

## 核心设计

### 全流程架构

以下从两个视角说明。

---

#### 视角一：用户实际使用视角

你调用 API 让平台写一段代码。**没有 Self-Refine**：LLM 一次输出，好坏看运气。**有 Self-Refine**：发一次请求，LLM 自检自修，返回经过多轮自我修正的版本。

**二进制搜索示例：**

```
Step 1 — 生成：LLM 先写一版
  def binary_search(arr, target):
      left, right = 0, len(arr) - 1
      while left <= right: ...
  → 功能正确，但缺少空数组检查

Step 2 — 反馈：同一 LLM 检查自己的输出
  反馈内容："缺少空数组和边界条件检查"

Step 3 — 修正：LLM 根据反馈修改
  def binary_search(arr, target):
      if not arr: return -1
      left, right = 0, len(arr) - 1
      ...

Step 4 — 收敛检查：LLM 判断是否还有新问题
  "CONVERGED" → 停止迭代，返回最终版本

返回：修正后的函数 + 完整迭代轨迹（trace）
```

**实际 curl 调用：**

```bash
curl -X POST http://localhost:8000/v1/agent/self-refine \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "tenant_id": "admin",
    "session_id": "sr-1",
    "goal": "写一个二分查找函数",
    "model": "gpt-4o",
    "self_refine_config": {
      "max_iterations": 5,
      "convergence_strategy": "hybrid"
    }
  }'
```

**响应示例（截取核心字段）：**

```json
{
  "final_message": "def binary_search(arr, target):\n    if not arr: return -1\n    ...",
  "self_refine_result": {
    "iterations_completed": 2,
    "converged": true,
    "convergence_reason": "no_improvement_needed",
    "total_llm_calls": 7,
    "execution_time_ms": 4520.5,
    "trace": [
      {
        "iteration": 1,
        "feedback": "缺少边界条件检查...",
        "feedback_dimension": "correctness",
        "output_after_refine": "def binary_search(arr, target):\n    if not arr: return -1\n    ..."
      },
      {
        "iteration": 2,
        "feedback": "NO_IMPROVEMENT_NEEDED",
        "feedback_dimension": "clarity"
      }
    ]
  }
}
```

---

#### 视角二：AI 技术视角

##### 调用链

```
apps/gateway/agent/routes.py:500-574
  POST /v1/agent/self-refine
  │  租户校验 → 限流 → 配额 → API Key → 解析 SelfRefineConfig
  │  Pydantic → dataclass 字段映射（第 534-544 行）
  │  component_span("self_refine")  ← OTel 追踪
  │
  └─→ packages/agent/self_refine/orchestrator.py
       run_self_refine(prompt, config, model):
         │
         ├─ generate(...)            → _call_llm(system, user)
         │   system: "You are a helpful assistant..."
         │
         ├─ feedback(...)            → _call_llm(system, user)
         │   system: "You are a quality reviewer..."
         │   ↑ 失败 → retry 1 → 空反馈
         │
         ├─ refine(...)              → _call_llm(system, user)
         │   system: "You are an output refiner..."
         │   ↑ 失败 → retry 1 → 保留上一轮
         │
         └─ convergence_check(...)   → 0~1 LLM 调用
              ├─ similarity: 余弦相似度 ≥ threshold
              ├─ llm_judged: _call_llm (CONVERGED?)
              └─ hybrid: similarity 优先 (免费) → 不达标则 LLM judge
```

##### 四类 System Prompt

| 步骤 | LLM 身份 | System Prompt 摘要 |
|------|---------|-------------------|
| `generate` | 助手 | "You are a helpful assistant. Produce the best possible output." |
| `feedback` | Review 质量员 | "You are a quality reviewer. Analyze... If optimal, respond with exactly: NO_IMPROVEMENT_NEEDED" |
| `refine` | 修正者 | "You are an output refiner. Revise based on feedback. Return only the revised output." |
| `convergence` | 收敛判断 | "You are a convergence judge. Respond with exactly: CONVERGED or NOT_CONVERGED" |

##### 单次迭代 LLM 成本

```
 generate          → 1 LLM call
 feedback          → 1 LLM call (失败 → retry 1 → 空反馈)
 refine            → 1 LLM call (失败 → retry 1 → 保留上一轮)
 convergence_check → 0~1 LLM call
                     ├─ similarity:     0 次
                     ├─ llm_judged:     1 次
                     └─ hybrid:         0~1 次

 每轮合计：3 ~ 4 次 LLM 调用
```

安全阀：`max_iterations`（默认 5，上限 10）+ `max_total_llm_calls`（默认 15，上限 30）。

##### 模型分离

```
generator_model = "gpt-4o"      # 贵模型做生成
feedback_model  = "gpt-4o-mini" # 便宜模型做反馈
```

核心洞察：反馈不需要和生成一样强的能力。用 GPT-4o 写代码，GPT-4o-mini 查 bug，省 ~10x 成本。

##### 收敛策略工程实现

| 策略 | 每轮额外成本 | 可靠性 | 实现方式 |
|------|------------|--------|---------|
| `similarity` | ~0（embedding） | 依赖 embedding 质量 | `packages.rag.embeddings.embed_texts` → `_cosine_similarity()` → fallback 精确匹配 |
| `llm_judged` | +1 LLM call | 最高 | LLM 接收最新反馈，返回 CONVERGED / NOT_CONVERGED |
| `hybrid` | 0~1 LLM call | 高 | sequential AND：先相似度检查（免费），低于阈值才调 LLM judge |

##### 收敛判断细节（llm_judged 路径）

```
_check_llm_judged(latest_feedback):
  if not latest_feedback.strip():         ← 空反馈直接收敛
      return True, "llm_judged"

  result = _call_llm(
      system="You are a convergence judge...",
      user=f"Latest feedback:\n{latest_feedback}"
  )
  if "CONVERGED" in result.strip().upper():
      return True, "llm_judged"
  else:
      return False, "llm_judged"
```

关键：当 feedback 为空（例如 LLM 失败降级）时，直接收敛，不产生 LLM 调用。

##### LLM 调用计数

所有 LLM 调用通过 `counter: list[int]` 引用传递统一计数（orchestrator.py 第 272-275 行）：

```python
counter: list[int] = [0]          # Python 闭包传 int 不会同步，用 list 包装

_call_llm(..., counter=counter):  # 每次真实调用后 counter[0] += 1
```

generate / feedback / refine / convergence_check 全部透传 `counter`，`convergence_check` 的 hybrid 路径不再遗漏计数。

##### 错误处理和降级

| 故障点 | 行为 | 效果 |
|--------|------|------|
| `feedback()` 异常 | retry 1 → 空反馈 | 不断送整次请求 |
| `refine()` 异常 | retry 1 → 保留上一轮输出 | 不断送 |
| 超时 | 截断返回当前最佳结果 | 已有结果不丢 |
| LLM 调用数达到硬上限 | 截断，reason="max_calls" | 不崩溃 |
| 所有异常 | catch → SelfRefineResult(success=False, error=str(exc)) | 请求永远不 crash |

    │
    ▼
┌──────────────────────────────────────────────────────┐
│         POST /v1/agent/self-refine                    │
│         apps/gateway/agent/routes.py                  │
│  (租户校验 → 限流 → 配额 → API Key → 解析配置)        │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│              run_self_refine(prompt, config)           │
│              packages/agent/self_refine/               │
│                                                        │
│   ┌─────────┐    ┌──────────┐    ┌─────────┐         │
│   │generate │───▶│feedback  │───▶│ refine  │──┐      │
│   └─────────┘    └──────────┘    └─────────┘  │      │
│       ▲                                       │      │
│       │         ┌────────────────┐            │      │
│       └─────────│convergence_chk│◀────────────┘      │
│                 └────────────────┘                   │
│                         │                            │
│                  ┌──────┴──────┐                     │
│                  │  converged? │                     │
│                  └──────┬──────┘                     │
│                    Yes  │  No                        │
│                    ┌────┘  └─────▶ next iteration    │
│                    ▼                                 │
│              return SelfRefineResult                 │
└──────────────────────────────────────────────────────┘
```

### 单次迭代的成本

每轮迭代最多消耗 **3 ~ 4 次 LLM 调用**：

```
 generate(prompt)           → 初始输出          [1 LLM call]
      │
 feedback(prompt, output)   → 自我反馈          [1 LLM call]
      │ (失败 → 重试 1 次 → 降级空反馈)
 refine(prompt, output, fb) → 修正输出          [1 LLM call]
      │ (失败 → 重试 1 次 → 保留上一轮)
 convergence_check()        → 判断是否收敛      [0~1 LLM call]
      ├─ similarity 策略:          0 次
      ├─ llm_judged 策略:          1 次
      └─ hybrid 策略:              0~1 次
```

默认 `max_iterations=5` + `max_total_llm_calls=15` 双重兜底。

### 配置参数（SelfRefineConfig）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_iterations` | 5 (上限 10) | 最大迭代轮数 |
| `generator_model` | None | 生成器模型，可分离 |
| `feedback_model` | None | 反馈器模型，None 时复用 generator_model |
| `convergence_strategy` | "hybrid" | 收敛策略：llm_judged / similarity / hybrid |
| `convergence_threshold` | 0.85 | similarity 模式阈值 |
| `max_total_llm_calls` | 15 (上限 30) | 单次请求 LLM 调用硬上限 |
| `feedback_dimensions` | 5 维度 | 结构化反馈维度列表 |
| `temperature` | 0.3 | LLM 生成温度 |
| `timeout_seconds` | 120.0 | 超时截断 |

### 收敛策略

1. **llm_judged**：LLM 接收「当前输出 + 上一轮反馈」判断是否还有新改进点
2. **similarity**：当前轮与上一轮输出语义相似度 >= threshold 即收敛（依赖 Embedding 服务）
3. **hybrid**（sequential AND）：similarity 快速检查 → 若 >= threshold 则收敛（跳过 LLM judge）→ 否则 LLM judge 确认

### 结构化反馈维度

- `correctness`：事实正确性、逻辑漏洞
- `clarity`：表达清晰度、歧义
- `completeness`：是否遗漏关键信息
- `consistency`：内部一致性、与 prompt 要求一致
- `actionability`：输出是否可执行（针对代码/JSON）

### 错误处理

- `feedback()` 失败 → 重试 1 次 → 降级返回空反馈 → 不断送整次请求
- `refine()` 失败 → 重试 1 次 → 保留上一轮输出继续
- 超时 → 截断返回当前最佳结果
- LLM 调用数达到硬上限 → 截断

## API

```
POST /v1/agent/self-refine
Content-Type: application/json
X-Tenant-Id: <tenant_id>
Authorization: Bearer <token>

{
  "tenant_id": "...",
  "session_id": "...",
  "goal": "要解决的问题",
  "model": "可选模型名",
  "self_refine_config": {
    "max_iterations": 5,
    "generator_model": null,
    "feedback_model": null,
    "convergence_strategy": "hybrid",
    "max_total_llm_calls": 15
  }
}
```

响应包含迭代轨迹（每轮的 feedback + refine 结果）：

```json
{
  "tenant_id": "admin",
  "session_id": "sr-1",
  "model": "gpt-4o",
  "final_message": "def binary_search(arr, target):\n    left, right = 0, len(arr) - 1\n    ...",
  "self_refine_result": {
    "iterations_completed": 2,
    "converged": true,
    "convergence_reason": "no_improvement_needed",
    "total_llm_calls": 7,
    "execution_time_ms": 4520.5,
    "trace": [
      {
        "iteration": 1,
        "feedback": "缺少边界条件检查...",
        "feedback_dimension": "correctness",
        "output_after_refine": "def binary_search(arr, target):\n    if not arr: return -1\n    ..."
      },
      {
        "iteration": 2,
        "feedback": "NO_IMPROVEMENT_NEEDED",
        "feedback_dimension": "clarity"
      }
    ]
  }
}
```

## 文件结构

```
# 核心引擎
packages/agent/self_refine/
├── __init__.py        # 公开 API: run_self_refine, SelfRefineConfig, SelfRefineResult
├── config.py          # SelfRefineConfig dataclass + 参数校验
├── models.py          # FeedbackRound / SelfRefineResult dataclass
└── orchestrator.py    # 核心循环: generate → feedback → refine → convergence_check

# API 层（合并到现有文件，不独立）
apps/gateway/agent/routes.py            # POST /v1/agent/self-refine（第 500 行）
packages/contracts/agent_schemas.py     # Pydantic 版 SelfRefineConfig / Result

# 测试与质量
tests/test_self_refine.py               # 23 个单元测试
eval/self_refine_quality_gate.py        # 质量门禁（30 题 GSM8K 简化集）

# 文档
docs/adr/0008-self-refine.md            # ADR
```

## 已知限制

1. **上下文增长**：每轮 refinement 将「当前输出 + 历史反馈」追加到 prompt 中，5 轮后 context 线性膨胀。极端情况可能触及 token 限制。未来优化：只传增量 diff。
2. **Similarity 策略依赖 Embedding 服务**：需要 Phase P 的 Embedding 服务可用，否则回退到字符串精确匹配。
3. **统计门禁脆弱性**：30 题的 benchmark margin of error ~+/-9%，门禁采用 no-regression（而非固定百分比）避免 flake。

## 与自进化 Agent（Phase R）的区别

| 维度 | Self-Refine (Phase W) | 自进化 Agent (Phase R) |
|------|----------------------|----------------------|
| 范围 | 单次请求内 | 跨 session |
| 持久化 | 无 | 经验库 + 策略补丁 |
| 反馈 | LLM 自我反馈 | LLM 反思 + HITL 审批 |
| 输出 | 修正后的输出 | 策略补丁注入未来规划 |
