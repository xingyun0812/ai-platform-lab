# Phase X.5 — Memory Classification 实施计划

**关联 Issue**：[#222](https://github.com/xingyun0812/ai-platform-lab/issues/222)
**父 PRD**：[docs/02-phase-x5-memory-classification.md](../docs/02-phase-x5-memory-classification.md)
**前置**：Phase X（L1~L5 已交付）

---

## 概述

在现有 Memory Governance 写入路径中插入 L0 记忆分类器——规则 + LLM 双轨分类，产出 4 类标签（preference/factual/ephemeral/noise），自动决定存储 scope、TTL 和权重调整。

**预计工期**：2.5 周

---

## 任务拆解

### X5a — 规则分类器（Rule Classifier）

**目标**：实现基于关键词/模式的零依赖规则分类器，能快速拦截明显噪音和识别明确的偏好/事实类记忆。

**文件**：
- `packages/memory/classifier/__init__.py` — `run_classifier()` 编排函数 + `ClassResult` dataclass
- `packages/memory/classifier/config.py` — `RulePatterns` dataclass（noise_keywords、preference_indicators、factual_indicators、noise_max_length、rule_confidence_threshold）
- `packages/memory/classifier/rules.py` — `rule_classify(content, patterns) → ClassResult | None` 纯函数

**规则逻辑**：
```
if content.strip() in noise_keywords or len(content) <= noise_max_length:
    return ClassResult("noise", confidence=1.0)
elif any(kw in content for kw in preference_indicators):
    return ClassResult("preference", confidence=0.9)
elif any(kw in content for kw in factual_indicators):
    return ClassResult("factual", confidence=0.9)
else:
    return None  # uncertain → 交给 LLM
```

**测试**：`tests/test_memory_classifier_rules.py`（~12 tests）

### X5b — LLM 分类器

**目标**：实现 LLM 分类器，处理规则分类器无法确定的记忆内容。复用 Phase X verify.py 的 LLM 调用模式。

**文件**：
- `packages/memory/classifier/llm.py` — `llm_classify(content, config) → ClassResult`

**LLM 调用**：
- 复用 `forward_with_model_router`（同 verify.py）
- 系统 prompt + `response_format: json_object`
- 200ms 硬超时，超时或 LLM 不可用 → 返回 `ClassResult("ephemeral", confidence=0.5, source="default")`
- 解析 LLM 返回的 JSON `{"class": "preference"|"factual"|"ephemeral", "confidence": 0.0-1.0, "reason": "..."}`

**测试**：`tests/test_memory_classifier_llm.py`（~10 tests）

### X5c — 集成到 MemoryStore

**目标**：将分类器集成到 InMemoryMemoryStore.add() 和 PostgresMemoryStore.add() 中，分类结果影响 scope/TTL。

**修改文件**：`packages/memory/store.py`

**集成点**：在 L1 quality_filter 之后、L2 dedup 之前插入：
```python
# L0: Memory Classification
if self._governance_config.classifier_enabled:
    from packages.memory.classifier import run_classifier
    result = run_classifier(record.content, self._governance_config)
    if result.class_label == "noise":
        logger.warning("classifier rejected noise memory %s", record.memory_id)
        self._metrics.record_classified(class_label="noise", source=result.source)
        return record.memory_id
    # Apply class to record
    record.metadata["class"] = result.class_label
    record.metadata["class_confidence"] = result.confidence
    record.metadata["class_source"] = result.source
    if result.class_label == "ephemeral":
        record.scope = "session"
        record.expires_at = time.time() + 86400  # 24h TTL
        record.metadata["feedback_bonus"] = -0.1
    elif result.class_label == "preference":
        record.scope = "user"
        record.expires_at = None
        record.metadata["feedback_bonus"] = 0.2
```

### X5d — Config + Metrics 扩展

**修改文件**：
- `packages/memory/config.py` — 添加 classifier 配置字段
- `packages/memory/metrics.py` — 添加分类器计数器
- `apps/gateway/settings.py` — 添加 env var
- `packages/memory/__init__.py` — 导出 classifier 模块

**Config 字段**（见 PRD 第 218-233 行）

**Metrics 指标**：
| 方法 | 指标名 |
|------|--------|
| `record_classified(class_label, source)` | `memory_classified_total{class,source}` |
| `record_classifier_latency(source, latency_ms)` | `memory_classifier_latency_ms` |
| `record_classifier_llm_calls()` | `memory_classifier_llm_calls` |
| `record_classifier_llm_error()` | `memory_classifier_llm_errors` |
| `record_classifier_rule_matched(pattern)` | `memory_classifier_rule_matched{pattern}` |

### X5e — 分类覆盖 API + 集成测试

**目标**：提供管理端手动纠正分类的 API，以及完整 E2E 集成测试。

**修改文件**：
- `apps/gateway/memory_routes.py` — `PATCH /{memory_id}/classify` 端点

**端点**：
```
PATCH /internal/memory/{memory_id}/classify
Body: {"class": "preference"|"factual"|"ephemeral"}
Effect: Updates metadata["class"], adjusts scope/expires_at
Auth: platform_admin
```

**测试文件**：
- `tests/test_memory_classifier.py` — 集成测试（~15 tests，覆盖规则+LLM+store 全链路）
- `tests/test_memory_routes_classifier.py` — API 测试（~6 tests）

---

## 文件清单

### 新增

| 文件 | 说明 |
|------|------|
| `packages/memory/classifier/__init__.py` | Package init + run_classifier() 编排 |
| `packages/memory/classifier/config.py` | RulePatterns dataclass |
| `packages/memory/classifier/rules.py` | 规则分类器 |
| `packages/memory/classifier/llm.py` | LLM 分类器 |
| `tests/test_memory_classifier_rules.py` | 规则分类器测试 |
| `tests/test_memory_classifier_llm.py` | LLM 分类器测试 |
| `tests/test_memory_classifier.py` | 集成测试 |
| `tests/test_memory_routes_classifier.py` | API 测试 |

### 修改

| 文件 | 变更 |
|------|------|
| `packages/memory/store.py` | 集成 L0 分类器到 add() |
| `packages/memory/config.py` | +classifier 配置字段 |
| `packages/memory/metrics.py` | +分类器指标 |
| `packages/memory/__init__.py` | +classifier 导出 |
| `apps/gateway/memory_routes.py` | +classify override 端点 |
| `apps/gateway/settings.py` | +env var |

---

## 依赖关系

```
X5a (Rule) + X5b (LLM) → X5c (Store 集成) → X5e (Override API + Tests)
                ↕
           X5d (Config + Metrics)
```

实施顺序：X5a + X5b 可并行 → X5d (Config) 可在 X5a 之前做 → X5c (集成) → X5e (API + 全链路测试)

---

## 验收门禁

```bash
# 分类器单元测试
python -m pytest tests/test_memory_classifier_rules.py tests/test_memory_classifier_llm.py -q

# 全链路集成测试
python -m pytest tests/test_memory_classifier.py tests/test_memory_routes_classifier.py -q

# 回归测试（确保 Phase X 不退化）
python -m pytest tests/test_memory_dedup.py tests/test_memory_weight.py tests/test_memory_purge.py tests/test_memory_verify.py tests/test_memory_governance_config.py -q

# 格式检查
ruff check packages/memory/ apps/gateway/memory_routes.py tests/
ruff format --check packages/memory/ apps/gateway/memory_routes.py tests/
```