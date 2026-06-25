# 模型能力报告样例

> 生成时间：2025-01-15 08:30:00 UTC  共 3 个模型
> 运行模式：mock=True（预设 scoring 逻辑，非真实 LLM 调用）

---

## 总览

| 模型 | context_mgmt | long_memory | tool_use | planning | overall | 最强维度 | 最弱维度 |
|------|:------------:|:-----------:|:--------:|:--------:|:-------:|:--------:|:--------:|
| `gpt-4o` | 0.900 | 0.800 | 0.850 | 0.750 | **0.825** | context | planning |
| `claude-3-5-sonnet` | 0.800 | 0.850 | 0.800 | 0.900 | **0.838** | planning | context |
| `chat-fast` | 0.700 | 0.600 | 0.750 | 0.650 | **0.675** | tool | memory |

---

## 各模型详情

### `gpt-4o`

- **Profile ID**: `prof_a1b2c3d4e5f6`
- **测评时间**: 2025-01-15 08:28:00 UTC
- **综合得分**: 0.825
- **最强维度**: context
- **最弱维度**: planning

| 维度 | 得分 | 评级 |
|------|:----:|:----:|
| context_mgmt | 0.900 | 优秀 ★★★★★ |
| long_memory | 0.800 | 良好 ★★★★ |
| tool_use | 0.850 | 优秀 ★★★★★ |
| planning | 0.750 | 良好 ★★★★ |

**说明**：gpt-4o 在长上下文 needle-in-haystack 场景（needle 正确召回率 90%）和工具调用（schema 准确率 85%）表现优异，适合需要精确信息提取和 Function Calling 的场景。

---

### `claude-3-5-sonnet`

- **Profile ID**: `prof_b2c3d4e5f6g7`
- **测评时间**: 2025-01-15 08:28:30 UTC
- **综合得分**: 0.838
- **最强维度**: planning
- **最弱维度**: context

| 维度 | 得分 | 评级 |
|------|:----:|:----:|
| context_mgmt | 0.800 | 良好 ★★★★ |
| long_memory | 0.850 | 优秀 ★★★★★ |
| tool_use | 0.800 | 良好 ★★★★ |
| planning | 0.900 | 优秀 ★★★★★ |

**说明**：claude-3-5-sonnet 在任务规划（DAG 结构合理性 + 步骤数覆盖率 90%）和跨 session 记忆检索（keyword 召回 85%）表现最佳，适合复杂多步骤 Agent 任务和需要长期记忆的场景。

---

### `chat-fast`

- **Profile ID**: `prof_c3d4e5f6g7h8`
- **测评时间**: 2025-01-15 08:29:00 UTC
- **综合得分**: 0.675
- **最强维度**: tool
- **最弱维度**: memory

| 维度 | 得分 | 评级 |
|------|:----:|:----:|
| context_mgmt | 0.700 | 中等 ★★★ |
| long_memory | 0.600 | 中等 ★★★ |
| tool_use | 0.750 | 良好 ★★★★ |
| planning | 0.650 | 中等 ★★★ |

**说明**：chat-fast 综合得分偏低，但响应速度最快（通常 < 500ms），适合对延迟敏感且任务复杂度较低的场景，如简单问答和单步工具调用。

---

## 任务适用推荐

- **长上下文处理**：推荐 `gpt-4o`（context_mgmt=0.900）
- **跨会话记忆检索**：推荐 `claude-3-5-sonnet`（long_memory=0.850）
- **工具调用 / Function Calling**：推荐 `gpt-4o`（tool_use=0.850）
- **复杂任务规划**：推荐 `claude-3-5-sonnet`（planning=0.900）

---

## 降级链建议

综合能力降级链：`claude-3-5-sonnet` → `gpt-4o` → `chat-fast`

**维度专项降级链**：

| 场景 | 首选 | 备选 1 | 备选 2 |
|------|------|--------|--------|
| 长上下文处理 | `gpt-4o` | `claude-3-5-sonnet` | `chat-fast` |
| 记忆检索 | `claude-3-5-sonnet` | `gpt-4o` | `chat-fast` |
| 工具调用 | `gpt-4o` | `claude-3-5-sonnet` | `chat-fast` |
| 任务规划 | `claude-3-5-sonnet` | `gpt-4o` | `chat-fast` |

---

## benchmark 评分说明

### context_mgmt（长上下文召回）
- 10 个 case，上下文长度 4k / 8k / 12k / 16k tokens
- 在长文本中间插入 needle（如项目代号、联系方式等）
- Scoring：`needle_keyword in response.lower()` → 1.0 / 0.0
- 最终分 = 10 case 的平均分

### long_memory（跨 session 记忆检索）
- 10 个 case，给定 session A 的记忆摘要，session B 提问
- Scoring：`matched_keywords / total_keywords`（关键词部分匹配）
- 最终分 = 10 case 的平均分

### tool_use（工具调用准确率）
- 10 个 case，给定工具列表 + 任务描述
- Scoring：工具名正确 0.4 + 参数键存在 0.3 + 参数值关键词匹配 0.3
- 最终分 = 10 case 的平均分

### planning（任务规划合理性）
- 10 个 case，给定目标，要求输出 Plan JSON
- Scoring：步骤数在合理范围 0.35 + DAG 无环 0.35 + 有依赖关系 0.30
- 最终分 = 10 case 的平均分

---

## Router 反哺示例

```bash
# 当 payload 带 required_capability 字段时，Router 自动选能力最强的模型
curl -X POST /v1/chat/completions \
  -H "X-Tenant-Id: tenant-001" \
  -H "Authorization: Bearer your-token" \
  -d '{
    "model": "chat-fast",
    "required_capability": "planning",
    "messages": [{"role": "user", "content": "帮我规划一个微服务架构迁移项目"}]
  }'

# 响应中 model_used 会是 capability routing 选出的最强模型
# → model_used: "claude-3-5-sonnet"（planning 维度得分最高）
```

---

*本报告由 `POST /internal/harness/capability-report` 自动生成，基于 mock 模式 benchmark 数据。*
