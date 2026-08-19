# Phase R-5 — Memory Governance（记忆治理）

> **状态**：✅ **已交付**（2026-08-13）
> **Issues**：[#205](https://github.com/xingyun0812/ai-platform-lab/issues/205) [#206](https://github.com/xingyun0812/ai-platform-lab/issues/206) [#207](https://github.com/xingyun0812/ai-platform-lab/issues/207)
> **PRD**：[docs/prds/memory-governance-prds.md](../docs/prds/memory-governance-prds.md)
> **ADR**：ADR-0009-memory-dedup.md（待创建）

## 概述

Memory Governance 是 Phase R（自进化 Agent）的治理增强层，在 MemoryStore 和 ExperienceStore 的写入端增加两层过滤：

- **L1 准入过滤**：`quality_filter()` — 拦截低质数据，不入库
- **L2 语义去重**：`dedup_filter()` — embedding 相似度高于阈值时合并/跳过
- **LLM Rerank**：`rerank_experiences()` — 召回后二次校验相关性

## 架构

```
写入路径：
build_experience_record()
  → quality_filter()          # L1: 拦截低质（短内容、纯标点、回声）
  → dedup_filter()            # L2: embedding 相似度去重
       ├─ ≥0.95   → skip（完全重复）
       ├─ 0.85~0.95 → merge_lessons（合并 lessons 到已有记录）
       └─ <0.85   → store（正常入库）

读取路径：
retrieve_similar_experiences()
  → cosine similarity 排序
  → _apply_weighted_score()  # sim*0.7 + norm_weight*0.3
  → rerank_experiences()     # 可选 LLM judge 二次过滤
  → top_k 返回
```

## 核心实现

### quality_filter（L1 准入过滤）

拦截规则（AND 逻辑，任一命中即 reject）：

| 规则 | 说明 | 默认阈值 |
|------|------|---------|
| min_content_length | content 长度过短 | 20 字符 |
| has_substance | 纯标点/空白/数字 | 检查 isalpha / CJK 范围 |
| not_duplicate_of_input | 内容与 prompt 完全相同（回声） | 精确匹配 |

MemoryStore 和 ExperienceStore 各自有独立的 `quality_filter()` 实现，逻辑一致，作用于各自的数据模型。

```python
def quality_filter(record, *, config, input_message=None):
    if not cfg.quality_filter_enabled:
        return True, ""
    if len(content) < cfg.min_content_length:
        return False, f"content too short"
    if not _has_letter(content):
        return False, "no substance"
    if input_message and content == input_message:
        return False, "echo guard"
    return True, ""
```

### dedup_filter（L2 语义去重）

基于 embedding 余弦相似度的去重策略：

| 相似度范围 | 动作 | 效果 |
|-----------|------|------|
| ≥ 0.95 | skip | 完全重复，不写入 |
| 0.85 ~ 0.95 | merge_lessons | 追加 lessons 到已有记录 |
| < 0.85 | store | 正常入库 |

```python
def dedup_filter(embedding, existing_records, *, config=None):
    cfg = config or MemoryGovernanceConfig()
    # ... 遍历 existing_records 算 cosine
    if max_sim >= cfg.dedup_skip_threshold:   # 0.95
        return "skip", max_sim_id
    elif max_sim >= cfg.dedup_merge_threshold: # 0.85
        return "merge_lessons", max_sim_id
    else:
        return "store", None
```

### rerank_experiences（LLM Judge 二次校验）

召回后用 LLM 对每条经验做相关性判断：

```
rerank_experiences(goal, experiences, max_relevant=2):
  1. 检查缓存（goal_hash + exp_ids_hash，5 分钟 TTL）
  2. 构造 LLM prompt（来自 prompts.yaml experience_rerank）
  3. LLM 返回 JSON: [{"index": 0, "relevant": true, "reason": "..."}, ...]
  4. 解析 JSON → 保留 marked relevant 的经验
  5. 截断到 max_relevant 条
  6. 异常/解析失败 → fail-open（全部保留）
```

集成路径：
- `planner.generate_plan()`：检索经验 → rerank → 注入 prompt
- `self_evolve.format_approved_strategy_context()`：策略 patches → rerank → 注入 planner

### 加权排序

检索结果的排序从单一 cosine similarity 改为加权评分：

```
final_score = similarity * 0.7 + normalized_weight * 0.3
normalized_weight = min(1.0, weight / max_weight_in_results)
```

`weight` 字段默认 1.0，可通过 `access_count`（访问频率）和 `last_accessed_at`（时效性）更新。

### 配置（MemoryGovernanceConfig）

```python
@dataclass
class MemoryGovernanceConfig:
    # quality_filter 规则
    quality_filter_enabled: bool = True
    min_content_length: int = 20

    # dedup_filter 阈值
    dedup_skip_threshold: float = 0.95
    dedup_merge_threshold: float = 0.85

    # rerank 开关
    rerank_enabled: bool = True
```

## 指标

| 指标名 | 类型 | 说明 |
|--------|------|------|
| `memory_quality_rejected_total` | counter | MemoryStore quality_filter 拦截次数 |
| `experience_quality_rejected_total` | counter | ExperienceStore quality_filter 拦截次数 |
| `experience_dedup_skipped_total` | counter | dedup 完全重复跳过 |
| `experience_dedup_merged_total` | counter | dedup lessons 合并 |
| `experience_stores_total` | counter | 经验入库次数 |
| `experience_retrieves_total` | counter | 经验检索次数 |
| `experience_store_errors_total` | counter | 存储错误次数 |

Metrics 通过 Prometheus 文本格式暴露。

## 文件结构

```
# 配置
packages/memory/config.py                      # MemoryGovernanceConfig

# MemoryStore 治理
packages/memory/store.py                       # quality_filter + _apply_weighted_score
packages/memory/metrics.py                     # memory_quality_rejected_total

# ExperienceStore 治理
packages/agent/experience_store.py             # quality_filter + dedup_filter + rerank_experiences
packages/agent/__init__.py                     # 新增导出
packages/agent/self_evolve.py                  # format_approved_strategy_context rerank 集成
packages/agent/planner.py                      # generate_plan rerank 集成

# Prompts
config/prompts.yaml                            # experience_rerank prompt 模板

# 测试与质量
tests/test_memory_quality.py                   # 15 tests — quality_filter 单元测试
tests/test_experience_quality.py               # 15 tests — Experience quality_filter
tests/test_experience_dedup.py                 # 13 tests — dedup_filter 三分支
tests/test_experience_rerank.py                # 19 tests — rerank + 缓存 + fail-open
tests/test_memory_governance_e2e.py            # 20 tests — E2E 全链路

# 文档
docs/prds/memory-governance-prds.md            # PRD
docs/02-phase-r-05-memory-governance.md        # 本文档
```

## 已知限制

1. **权重公式未实现**：PRD-2 的完整权重计算（recency*0.4 + frequency*0.3 + relevance*0.2 + feedback*0.1）尚未实现。`weight` 字段默认 1.0，加权排序暂时无实际效果。
2. **清理脚本未实现**：PRD-2 的 `scripts/cleanup_memory.py`（过期清理、归档、删除）未实现。
3. **Postgres 治理测试缺失**：治理 pipeline 的测试只覆盖了 InMemory 实现，PostgresExperienceStore 的 quality_filter/dedup_filter 集成未单独测试。
4. **测试隔离问题**：全局 store/metrics 单例导致跨测试文件并行执行时有 3-4 个假失败。

## 与 Phase R 自进化 Agent 的关系

| 维度 | Phase R（自进化） | Phase R-5（治理） |
|------|-------------------|-------------------|
| 核心 | 经验沉淀 + 策略自动生成 | 经验质量管控 |
| 写入 | 直接 store | quality_filter + dedup_filter 拦截 |
| 读取 | task_signature / embedding 检索 | 加权排序 + LLM judge rerank |
| 产出 | StrategyPatch | 高质量经验沉淀 |