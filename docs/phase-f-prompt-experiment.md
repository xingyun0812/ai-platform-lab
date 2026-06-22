# Phase F — Prompt A/B 实验（#30）

> **目标**：基于 #29 版本化能力，实现 prompt 的流量分桶、指标对比、自动胜出。

对标「Agent 平台架构全景」中的「能力中台 — Prompt 管理（A/B 测试）」能力。

---

构建思路、使用链路与逐文件代码说明见 [phase-f-build-and-code-guide.md](./phase-f-build-and-code-guide.md)。

## 1. 设计要点

### 1.1 数据模型

```python
@dataclass
class ExperimentVariant:
    version: int       # registry 中的 prompt 版本
    percent: int       # 0-100，流量占比（所有 variant 之和必须 = 100）

@dataclass
class Experiment:
    experiment_id: str      # 自动生成 "exp-{prompt_id}-{8位hash}"
    prompt_id: str
    variants: list[ExperimentVariant]
    tenant_id: str = "global"
    status: str = "running"  # running | stopped | promoted
    min_samples: int = 100    # 自动胜出所需最小样本数（每 variant）
    success_metric: str = "quality"  # quality | latency | tokens
    winner_margin: float = 0.1          # 自动胜出相对改进阈值
    winner_version: int | None = None
    created_at: float
    stopped_at: float | None
    created_by: str
```

### 1.2 分桶策略

**确定性分桶**：同一 `bucket_key` 永远分到同一 variant，保证用户体验一致。

```python
hash(experiment_id + bucket_key) → 0-99
按累计 percent 边界落桶
```

RAG query 使用 `tenant_id + query` 作为 `bucket_key`，保证同一用户同一问题始终看到同一版本。

### 1.3 指标体系

每个 `(experiment_id, version)` 维护：

| 指标 | 说明 |
|------|------|
| `requests` | 流量分配次数 |
| `latencies_ms` | LLM 调用延迟（P95） |
| `tokens_used` | 累计 token 消耗 |
| `errors` | 错误次数 |
| `quality_scores` | 用户反馈（0-1，可选） |

### 1.4 自动胜出

当满足条件时自动标记 winner：

1. 所有 variant 的 `requests ≥ min_samples`
2. 在 `success_metric` 上，最优 variant 相对次优的改进比例 `≥ winner_margin`

**不自动 set_active**：仅停止实验 + 标记 winner_version；admin 需显式 `promote` 才切换 active。

### 1.5 存储

| 文件 | 用途 | git 跟踪 |
|------|------|---------|
| `data/prompt_experiments.json` | 实验 + 指标 | ❌ |

启动时加载；写入时全量持久化。

---

## 2. REST API

| Method | Path | 权限 | 说明 |
|--------|------|------|------|
| POST | `/internal/prompts/{prompt_id}/experiments` | platform_admin | 创建实验 |
| GET | `/internal/prompts/{prompt_id}/experiments` | 任何已认证 | 列出所有实验 |
| GET | `/internal/prompts/{prompt_id}/experiments/current` | 任何已认证 | 当前运行中 |
| GET | `/internal/prompts/{prompt_id}/experiments/{exp_id}` | 任何已认证 | 详情 + 各 variant 指标 |
| POST | `/internal/prompts/{prompt_id}/experiments/{exp_id}/stop` | platform_admin | 停止实验 |
| POST | `/internal/prompts/{prompt_id}/experiments/{exp_id}/promote` | platform_admin | 提升 winner 为 active |
| POST | `/internal/prompts/{prompt_id}/experiments/{exp_id}/feedback` | 任何已认证 | 记录质量反馈 |

### 使用示例

```bash
# 1. 创建 50/50 实验，比较 v1 与 v2
curl -s -X POST http://127.0.0.1:8000/internal/prompts/rag_query/experiments \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "variants": [
      {"version": 1, "percent": 50},
      {"version": 2, "percent": 50}
    ],
    "min_samples": 100,
    "success_metric": "quality",
    "winner_margin": 0.1
  }'
# → {"experiment_id": "exp-rag_query-a1b2c3d4", "status": "running", ...}

# 2. 查询当前运行中
curl -s http://127.0.0.1:8000/internal/prompts/rag_query/experiments/current \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me"

# 3. 查看详情 + 各 variant 指标
curl -s http://127.0.0.1:8000/internal/prompts/rag_query/experiments/exp-rag_query-a1b2c3d4 \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me"
# → {"variants": [{"version": 1, "metrics": {"requests": 50, "quality_avg": 0.85}}, ...]}

# 4. 记录质量反馈（用户评分）
curl -s -X POST http://127.0.0.1:8000/internal/prompts/rag_query/experiments/exp-.../feedback \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{"version": 1, "score": 0.9}'

# 5. 手动停止
curl -s -X POST http://127.0.0.1:8000/internal/prompts/rag_query/experiments/exp-.../stop \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me"

# 6. 提升胜出版本为 active
curl -s -X POST http://127.0.0.1:8000/internal/prompts/rag_query/experiments/exp-.../promote \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me"
```

---

## 3. 集成点

### 3.1 RAG Query

`apps/gateway/rag/query_service.py`：
- `_resolve_rag_prompt_template(bucket_key=...)` — 启用实验时按分桶取版本
- LLM 调用后 `record_request()` 记录 latency/tokens/error
- 触发 `maybe_auto_winner()` 检查是否达到自动胜出条件
- 响应体 `_platform.experiment` 暴露当前实验信息

### 3.2 Gateway 启动

`apps/gateway/main.py`：
- `create_app()` 初始化 `ExperimentStore`
- 挂载 `/internal/prompts/{id}/experiments/*` 路由

---

## 4. 配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `PROMPT_EXPERIMENT_ENABLED` | `true` | 总开关 |
| `PROMPT_EXPERIMENTS_PATH` | `data/prompt_experiments.json` | 存储 JSON 路径 |
| `PROMPT_EXPERIMENT_DEFAULT_MIN_SAMPLES` | `100` | 默认最小样本数 |
| `PROMPT_EXPERIMENT_DEFAULT_MARGIN` | `0.1` | 默认胜出阈值（10%） |

---

## 5. 测试与验收

```bash
# 1. 单元测试（17 个用例）
python3 tests/test_prompt_experiment.py
# 期望：17/17 passed

# 2. 验收冒烟（PF 段）
python3 eval/acceptance_smoke.py
# 期望：
# PF  A/B 实验创建 + 分桶 + 自动胜出   PASS
# PF  A/B 实验 REST API               PASS
```

---

## 6. 代码导航

```
packages/prompt/experiment.py
├── ExperimentVariant          # (version, percent)
├── Experiment                 # 实验定义
├── VariantMetrics             # 单 variant 指标
├── ExperimentStore            # 存储 + 指标 + 自动胜出
│   ├── create_experiment()       # 创建（校验 percent 之和=100 + 同 prompt 仅一个 running）
│   ├── pick_variant()            # 确定性分桶
│   ├── record_request()          # 记录 latency/tokens/error
│   ├── record_quality()          # 记录质量反馈
│   ├── maybe_auto_winner()       # 自动胜出检查
│   ├── stop_experiment()         # 停止
│   ├── promote_winner()          # 标记为 promoted
│   └── load() / _persist()       # JSON 持久化
├── init_experiment_store()     # 全局单例
└── get_experiment_store()

packages/prompt/registry.py
└── render_with_experiment()    # 按实验分桶渲染

apps/gateway/prompt_experiment_routes.py   # REST API
apps/gateway/rag/query_service.py          # RAG 集成
```

---

## 7. 已知限制（面试时主动说）

1. **单进程内存**：`ExperimentStore` 进程内单例；多实例部署时需改为 Redis 共享存储。
2. **持久化频率**：每次 `record_request` 都全量写 JSON，高频场景需优化为批量 flush 或追加日志。
3. **无统计显著性检验**：当前仅用 `winner_margin` 相对阈值，未做 t-test/chi-square 等显著性检验。生产场景应引入。
4. **仅 RAG 接入**：Agent runner 未接入（Agent 有工具调用，质量信号复杂，暂不参与 A/B）。
5. **反馈数据来源**：`record_quality` 需要外部系统（如点踩/点赞按钮）主动上报；当前无自动采集。
6. **同 prompt 仅一个 running**：限制并行实验；如需多因素正交实验，需扩展为多变量实验设计。

---

## 8. 后续演进

| Issue | 内容 | 依赖 |
|-------|------|------|
| 未来 | Redis 共享存储（多实例一致） | — |
| 未来 | 统计显著性检验（t-test） | — |
| 未来 | 多变量正交实验（MAB） | — |
| 未来 | Agent runner 接入 A/B | — |
| 未来 | 自动反馈采集（点踩/点赞 UI） | Console V2 (#46) |

---

## 9. 面试讲法

1. **为什么需要 A/B**：Prompt 迭代不能靠直觉；版本化（#29）只能切换，无法量化对比；A/B 提供数据驱动决策。
2. **确定性分桶**：同一用户同一问题始终看到同一版本，避免用户体验跳动。
3. **三类胜出指标**：quality（用户反馈）/ latency（P95）/ tokens（成本），覆盖效果、性能、成本三维度。
4. **自动胜出 + 手动 promote**：自动停止实验防止流量浪费，但切换 active 需人工确认，避免误判。
5. **诚实边界**：当前无统计显著性检验（仅相对阈值）；多实例需 Redis 化；仅 RAG 接入。

参考代码：
- `packages/prompt/experiment.py:170` — ExperimentStore
- `packages/prompt/experiment.py:280` — pick_variant 分桶
- `packages/prompt/experiment.py:330` — maybe_auto_winner 自动胜出
- `apps/gateway/prompt_experiment_routes.py:90` — 创建实验 API
- `apps/gateway/rag/query_service.py:172` — RAG 集成分桶
