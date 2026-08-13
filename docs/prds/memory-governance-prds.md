# PRD-1: 长期记忆准入过滤 + 语义聚类去重

> **Issue 标签**：`phase-r-memory-governance`、`memory-dedup`、`memory-quality`
> **依赖**：无（独立，可最先启动）
> **设计文档**：docs/adr/ADR-0009-memory-dedup.md（新）

---

## Problem Statement

当前 MemoryStore 和 ExperienceStore 在**写入端没有任何质量拦截机制**。以下场景会导致记忆库被污染：

1. 同一错误场景反复发生，每次写一条几乎相同的经验记录（task_signature 是 goal 的 SHA1，但 goal 措辞稍有变化就会生成不同 signature → 重复存储）
2. Agent 的临时推理过程、中间产物、无效对话直接被写入了长期记忆
3. 纯噪声数据（空字符串、纯标点、长度 < 10 字符）与高价值经验无差别存储
4. `build_experience_record()` 不做任何内容校验，`store_experience()` 直接入库

长期来看这会导致：语义召回的 top-k 结果被重复/低质数据占据，高价值经验被稀释。

## Solution

在写入端增加两层治理：
- **L1 准入过滤**：`quality_filter()` — 拦截低质数据，不入库
- **L2 语义去重**：`dedup_filter()` — embedding 相似度高于阈值时合并/跳过

两层过滤器组成 pipeline，写入路径变为：

```
build_experience_record()
  → quality_filter()           # L1: 拦截低质
  → dedup_filter(embedding)    # L2: 语义去重
  → store_experience()         # 入库
```

## User Stories

1. 作为 AI Platform Lab 维护者，我希望低质数据（空内容、纯重复、幻觉痕迹）在写入前被拦截，以便记忆库保持高信噪比
2. 作为 AI Platform Lab 维护者，我希望高度相似的同主题经验自动去重而非重复堆积，以便召回 top-k 始终展示有区分度的结果
3. 作为 AI Platform Lab 维护者，我希望 quality_filter 的规则和阈值可通过配置调整，以便在不修改代码的前提下适配不同严苛程度
4. 作为 AI Platform Lab 维护者，我希望去重操作有 metrics 记录（拦截量、合并量），以便衡量治理效果
5. 作为使用 Planner 的终端用户，我看到的「历史经验」不包含重复条目，以便 prompt 不被冗余信息浪费

## Implementation Decisions

### quality_filter(record) → (pass: bool, reason: str)

拦截规则（AND 逻辑，任一命中即 reject）：

| 规则 | 说明 | 默认阈值 |
|------|------|----------|
| `min_content_length` | content 最短长度 | 20 字符 |
| `has_substance` | 不只是标点/空白/纯数字 | — |
| `no_llm_hallucination_markers` | 不含已知幻觉模式（如 "I cannot"、纯道歉） | 可选 |
| `not_duplicate_of_input` | 内容不与 prompt/goal 完全相同（常见于 generate 失败时的回声） | — |

- 在 `ExperienceStore.store()` 以及 `MemoryStore.add()` 中插入拦截
- 拦截时返回 `(False, reason)`，调用方可决定是否记录 metrics 或 log warning
- 配置项放在 `SelfRefineConfig` 或新建 `MemoryGovernanceConfig`

### dedup_filter(record, existing_records) → (action: str, merged_id: str | None)

| 条件 | 动作 |
|------|------|
| embedding 相似度 ≥ 0.95 | `skip` — 完全重复，不写入 |
| 0.85 ≤ 相似度 < 0.95 | `merge_lessons` — 聚合 lessons（新经验的 lessons 追加到已有记录后） |
| 相似度 < 0.85 | `store` — 正常入库 |

- 需要 `MemoryStore` 和 `ExperienceStore` 均支持按 embedding 召回
- merge 时更新已有记录的 `lessons`、`access_count`、`last_accessed_at`
- 当前 `ExperienceRecord` 无 `access_count` 字段 → 新增（见 PRD-2）

## 受影响文件

| 文件 | 改动 |
|------|------|
| `packages/memory/store.py` | `MemoryStore.add()` 内增加 quality_filter 调用 |
| `packages/agent/experience_store.py` | `ExperienceStore.store()` 内增加 quality_filter + dedup_filter 调用 |
| `packages/agent/experience_store.py` | 新增 `quality_filter()` 函数 |
| `packages/agent/experience_store.py` | 新增 `dedup_filter()` 函数 |
| `packages/memory/__init__.py` | 导出新增接口 |
| `packages/memory/metrics.py` | 增加拦截/去重 counter |

## Testing Decisions

**测试原则**：只测外部行为（拦截与否、去重与否），不测内部实现细节。

| 测试 | 文件 |
|------|------|
| quality_filter 拦截空字符串、纯标点、回声内容 | `tests/test_experience_quality.py` |
| quality_filter 放行正常内容 | 同上 |
| dedup_filter 完全重复（≥0.95）直接 skip | `tests/test_experience_dedup.py` |
| dedup_filter 高度相似（0.85-0.95）合并 lessons | 同上 |
| dedup_filter 低相似度正常入库 | 同上 |
| 全链路集成：build → quality → dedup → store | `tests/test_experience_run_store_plan_e2e.py`（扩展现有 test class） |

现有测试参考：`tests/test_experience_persistence.py`（23 个测试，覆盖 store/retrieve/delete 的 Postgres + InMemory 双后端）

## Out of Scope

- 历史数据的回溯去重（数据迁移脚本可以考虑但不在首次 PRD 范围内）
- 离线巡检清理（见 PRD-2）
- 召回的二次校验（见 PRD-3）
- 用户可配置的细粒度规则引擎

## Further Notes

- 去重的 embedding 复用 `compute_task_embedding()` 的现有逻辑——`experience_store.py` 已有 embedding 计算能力
- 默认去重阈值 `0.85` 和 `0.95` 可先在 PRD review 时调整，上线后观察 recall precision trade-off
- 建议在 MemoryMetrics 中增加 `memory_dedup_skipped_total` / `memory_dedup_merged_total` / `memory_quality_rejected_total` 三个 counter

---

# PRD-2: 动态权重衰减 + 定时归档清理

> **Issue 标签**：`phase-r-memory-governance`、`memory-decay`、`memory-cleanup`
> **依赖**：PRD-1（推荐在其之后，但非硬依赖）
> **设计文档**：docs/adr/ADR-0010-memory-decay-cleanup.md（新）

---

## Problem Statement

当前 MemoryStore 和 ExperienceStore 对记忆/经验记录**不做任何动态加权或生命周期管理**：

1. `MemoryRecord` 有 `expires_at`（TTL）但无 `access_count` / `last_accessed_at`，无法区分"高频使用的高价值记忆"和"从未被检索过的死数据"
2. 检索时仅按 embedding similarity 排序，不考虑使用频次和时效性，低频高相关数据可能被完全淹没
3. 没有离线巡检任务——过期记录只在查询时被动过滤，数据库仍保留大量死数据
4. 低权重记录与高价值记录权重一致，消耗相同的存储和检索资源
5. 高价值案例没有归档机制，可能随着 TTL 过期被动删除

## Solution

### 1. 权重模型

每条记录增加三个字段，组合成 `weight = f(recency, frequency, relevance, feedback)`：

| 字段 | 类型 | 说明 | 初始值 |
|------|------|------|--------|
| `access_count` | int | 被检索/匹配到的次数 | 0 |
| `last_accessed_at` | float | 最近一次被检索的时间戳 | created_at |
| `weight` | float | 综合权重（计算得出） | 1.0 |

检索时排序因子：`final_score = embedding_similarity * 0.7 + normalized_weight * 0.3`

### 2. 巡检脚本

新增 `scripts/cleanup_memory.py`，支持 cron 调度：

| 动作 | 条件 | 说明 |
|------|------|------|
| `purge_expired` | expires_at 已过期的记录 | 物理删除 |
| `archive_high_value` | weight ≥ 阈值且过期 > 30 天 | 移动到 `agent_memories_archive` 表 |
| `delete_low_weight` | weight < 0.1 且 created_at > 90 天 | 物理删除（无人问津的死数据） |

## User Stories

1. 作为 AI Platform Lab 维护者，我希望高频使用的记忆自动获得更高检索权重，以便 agent 优先参考经过验证的经验
2. 作为 AI Platform Lab 维护者，我希望长期未使用的低权重记忆被自动清理，以减少存储膨胀
3. 作为 AI Platform Lab 维护者，我希望高价值案例在 TTL 过期后被归档而非删除，以保留审计和回查能力
4. 作为 AI Platform Lab 维护者，我希望巡检结果有 metrics + 日志输出，以便监控清理效果
5. 作为 AI Platform Lab 维护者，我希望巡检可被 cron 调度（如每日凌晨），以做到无人值守

## Implementation Decisions

### 1. 数据结构变更

- `MemoryRecord` 增加 `access_count: int = 0`、`last_accessed_at: float | None = None`、`weight: float = 1.0`
- `ExperienceRecord` 增加同样的三个字段
- `agent_memories` 表新增列（Postgres 侧带 default）
- `experience_store` 的 Postgres 表新增列

### 2. 检索加权

- `MemoryStore.search()` 和 `ExperienceStore.retrieve_similar()` 中，排序公式改为：
  `score = embedding_sim * 0.7 + normalized_weight * 0.3`
- `normalized_weight` 在每次检索时计算：`min(1.0, weight / max_weight_in_results)`
- 每次 `get()` / `search()` 命中后自动 `access_count += 1`、`last_accessed_at = now()`

权重计算公式（可选，可在 `MemoryGovernanceConfig` 中配置）：
```
weight = 0.4 * recency_score + 0.3 * frequency_score + 0.2 * relevance_boost + 0.1 * feedback_boost
```

### 3. 巡检脚本

```bash
# cron: 每天 3:00 AM 执行
0 3 * * * cd /app && python scripts/cleanup_memory.py --purge-expired --archive --delete-low-weight
```

- `--dry-run` 模式：仅输出将要执行的操作，不实际删除
- 每个操作独立 flag，可组合或单独运行
- 输出 JSON 报告到 stdout（可被日志系统采集）

## 受影响文件

| 文件 | 改动 |
|------|------|
| `packages/memory/store.py` | `MemoryRecord` 加 access_count/last_accessed_at/weight；检索加权 |
| `packages/agent/experience_store.py` | `ExperienceRecord` 加三个字段；检索加权 |
| `packages/memory/metrics.py` | 增加清理/归档 counter |
| `scripts/cleanup_memory.py` | **新文件**：巡检主入口 |
| `docker-compose.yml` | 可选：增加 cleanup cron service |

## Testing Decisions

**测试原则**：巡检脚本的行为可预测、可断言；权重计算有确定性的数学验证。

| 测试 | 文件 |
|------|------|
| `access_count` 在检索后自动 +1 | `tests/test_memory.py` |
| `weight` 组合公式正确性 | `tests/test_memory_decay.py`（新建） |
| 检索排序：高 weight 的记录排名上升 | 同上 |
| 巡检 `--dry-run` 不产生副作用 | `tests/test_cleanup_script.py`（新建） |
| 巡检 `--purge-expired` 物理删除过期的 | 同上 |
| 巡检 `--archive` 将高价值移动到归档表 | 同上 |
| 巡检 `--delete-low-weight` 删除死数据 | 同上 |

## Out of Scope

- 用户反馈维度（`feedback_boost`）的实现（字段保留，系数先置 0）
- 在线 weight 重计算（仅每次检索时更新）
- Redis 热缓存的 TTL 同步

## Further Notes

- `feedback_boost` 字段保留但系数先置 0，留给 Phase J（反馈飞轮）后续对接
- 巡检脚本支持 `--since` 参数，可灵活控制检查范围而非全表扫描
- 归档表 `agent_memories_archive` 使用与主表相同的 schema，方便查询

---

# PRD-3: 召回二次校验（Retrieval Re-ranking）

> **Issue 标签**：`phase-r-memory-governance`、`retrieval-rerank`、`llm-judge`
> **依赖**：无（独立）
> **设计文档**：docs/adr/ADR-0011-memory-rerank.md（新）

---

## Problem Statement

当前 Planner 和 Agent 的记忆/经验召回是**一次性的**：

1. `retrieve_similar_experiences()` 返回 top-k 后，直接拼接进 planner prompt 的 `【历史经验】` 块
2. 没有 LLM 校验步骤来判断"这些召回的经验是否真的和当前任务相关"
3. 老旧/过时的策略 patch（`format_approved_strategy_context`）同样不加过滤直接注入
4. 当记忆库积累了大量经验后，embedding similarity 召回不可避免地会混入"语义相似但上下文无关"的记录

这导致：agent 的 prompt 被不相关的"历史经验"污染，可能干扰而不是帮助决策。

## Solution

在检索 → 注入之间增加 **LLM 二次校验步骤**：

```
用户请求 → 计算 goal embedding
  → vector retrieval (top-k=5)
  → LLM judge: 筛选与当前任务真正相关的记录 (k=2)
  → 仅注入通过校验的记录
```

## User Stories

1. 作为使用 Planner 的终端用户，我不想在 prompt 中看到与当前任务无关的「历史经验」，以避免上下文被污染
2. 作为 AI Platform Lab 维护者，我希望 Agent 引用的每条经验都经过了"是否真的与此任务相关"的判断，以提升决策质量
3. 作为 AI Platform Lab 维护者，我希望 LLM judge 的 prompt 可配置（通过 prompt registry），以便在不修改代码的前提下调优筛选标准
4. 作为 AI Platform Lab 维护者，我希望 LLM judge 在 embedding 服务或 LLM 不可用时自动降级为全量注入，以确保不阻塞主流程

## Implementation Decisions

### 1. Rerank 函数

```python
async def rerank_experiences(
    goal: str,
    experiences: list[ExperienceRecord],
    max_relevant: int = 2,
    model: str | None = None,
) -> list[ExperienceRecord]:
    """LLM judge 从候选经验中筛选与 goal 真正相关的条目。

    LLM judge prompt:
      你是经验筛选助手。以下是当前任务目标以及一些历史经验。
      请判断每条经验是否真正与当前任务相关。
      输出 JSON 格式：[{"index": 0, "relevant": true, "reason": "..."}, ...]

    Returns:
        最多 max_relevant 条经验。
    """
```

### 2. 集成点

- `planner.py` 中 `generate_plan()` 在 `retrieve_similar_experiences()` 之后、拼接 `【历史经验】` 之前插入 `rerank_experiences()`
- `self_evolve.py` 的 `format_approved_strategy_context()` 中插入类似的校验
- 降级：LLM judge 失败（API 不可达、超时、解析失败）→ 返回全部候选经验（fail-open）

### 3. Prompt 模板

- 通过 prompt registry 提供 `experience_rerank` 模板
- 回退到硬编码默认 prompt（同上）
- 要求 LLM 输出结构化 JSON，便于解析

### 4. 性能考虑

- LLM judge 每次检索增加一次小 LLM 调用（cost ~100-200 token input）
- 可选的 cache：对完全相同的 `(goal_hash, experience_ids)` 组合缓存 judge 结果 5 分钟

## 受影响文件

| 文件 | 改动 |
|------|------|
| `packages/agent/experience_store.py` | 新增 `rerank_experiences()` 函数 |
| `packages/agent/planner.py` | `generate_plan()` 集成 rerank 步骤 |
| `packages/agent/self_evolve.py` | `format_approved_strategy_context()` 可选集成 |
| `packages/agent/perf_metrics.py` | 增加 rerank 调用 counter + latency |

## Testing Decisions

**测试原则**：LLM judge 输出用 mock 替代，不依赖真实 LLM 调用。核心测 rerank 函数的逻辑正确性和降级行为。

| 测试 | 文件 |
|------|------|
| mock LLM judge 返回全部 relevant → 全部保留 | `tests/test_experience_rerank.py`（新建） |
| mock LLM judge 返回部分 relevant → 仅保留对应的 | 同上 |
| mock LLM judge 返回空 → 保留全部（fail-open 降级） | 同上 |
| LLM judge 调用失败/超时 → 保留全部（fail-open） | 同上 |
| planner 集成：经验块中仅包含通过校验的条目 | `tests/test_planner.py` |
| rerank 调用计入 `total_llm_calls` | 同上 |

## Out of Scope

- rerank 结果的持久化缓存（短期价值小）
- 基于用户反馈的 rerank 偏好学习
- 多轮 rerank（一次 LLM 调用足够）

## Further Notes

- LLM judge 成本可控：默认 `max_relevant=2`，一次小模型调用（如 GPT-4o-mini）处理 5 条候选经验
- `fail-open` 是核心设计决策：rerank 是 quality-of-life 优化，不能成为可用性瓶颈
- 建议在 `MemoryGovernanceConfig` 中增加 `rerank_enabled: bool = True` 开关，可随时关闭
- 此 PRD 与 Phase R 的 `capability_profile` 中的 `long_memory` 维度打分互补：前者评模型的记忆能力，后者评注入记忆的质量