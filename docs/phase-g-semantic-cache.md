# Phase G — 语义缓存（#34）

> **目标**：在 Gateway 层拦截 `/v1/chat/completions`，相似请求复用历史响应，直接降本。

对标「Agent 平台架构全景」中的「模型服务层 — 语义缓存」能力。这是**成本管控飞轮**的关键一环。

---

构建思路、使用链路与逐文件代码说明见 [phase-g-build-and-code-guide.md](./phase-g-build-and-code-guide.md)。

## 1. 设计要点

### 1.1 双模式命中策略

| 模式 | 命中策略 | 适用场景 | 依赖 |
|------|---------|---------|------|
| `exact` | SHA256(tenant_id + model + normalized_messages) 精确匹配 | 无 embedding 服务 / 高一致性场景 | 无 |
| `semantic` | 上次 user message 的 embedding 余弦相似度 ≥ 阈值 | 默认推荐 / 容忍近义复述 | LLM_API_KEY（embedding 服务） |

`semantic` 模式下若 embedding 服务不可用，自动降级为 `exact`，保证可用性。

### 1.2 存储后端

| 后端 | 触发条件 | 特点 |
|------|---------|------|
| `InMemorySemanticCache` | `REDIS_URL` 不可达 | 进程内 LRU + TTL，单实例 |
| `RedisSemanticCache` | `REDIS_URL` 可达 | 跨实例共享，Hash + TTL |

Redis 数据结构：
- `ai_platform:sem_cache:{tenant_id}:exact` — Hash，cache_key → JSON entry（精确命中先查这里）
- `ai_platform:sem_cache:{tenant_id}:sem` — Hash，cache_key → JSON entry（含 embedding，语义匹配遍历）

### 1.3 跳过缓存的场景

- `stream=true`（流式响应不可缓存）
- `temperature > SEMANTIC_CACHE_MAX_TEMPERATURE`（默认 0.3，保证只缓存确定性生成）
- `model ∈ SEMANTIC_CACHE_SKIP_MODELS`（如 reasoning 模型）
- 上游响应非 2xx（不缓存错误）

### 1.4 多租户隔离

按 `tenant_id` 分桶，缓存条目互不可见。每个租户独立 LRU 上限与 TTL。

---

## 2. 配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `SEMANTIC_CACHE_ENABLED` | `false` | 总开关 |
| `SEMANTIC_CACHE_MODE` | `semantic` | `exact` / `semantic` |
| `SEMANTIC_CACHE_SIMILARITY_THRESHOLD` | `0.92` | 余弦相似度阈值 |
| `SEMANTIC_CACHE_TTL_SECONDS` | `3600` | 缓存有效期 |
| `SEMANTIC_CACHE_MAX_ENTRIES_PER_TENANT` | `256` | 单租户最大条目数 |
| `SEMANTIC_CACHE_SKIP_MODELS` | `""` | 逗号分隔，跳过的模型名 |
| `SEMANTIC_CACHE_MAX_TEMPERATURE` | `0.3` | 跳过高 temperature 请求 |

---

## 3. 使用

### 3.1 启用（最小配置）

```bash
# .env
SEMANTIC_CACHE_ENABLED=true
SEMANTIC_CACHE_MODE=exact          # 无 LLM_API_KEY 时也能用
# SEMANTIC_CACHE_MODE=semantic     # 有 embedding 服务时降本更多
```

### 3.2 验证

```bash
# 1. 单元测试
python3 tests/test_semantic_cache.py
# 期望：11/11 passed

# 2. 启用后访问 /metrics 查看命中率
curl -s http://127.0.0.1:8000/metrics | grep semantic_cache
# 输出示例：
# semantic_cache_hits_total{tenant_id="demo-a",model="chat-fast"} 42
# semantic_cache_misses_total{tenant_id="demo-a",model="chat-fast"} 158
# semantic_cache_tokens_saved_total{tenant_id="demo-a",model="chat-fast"} 12350
# semantic_cache_lookup_latency_ms_p95{tenant_id="demo-a",model="chat-fast"} 2.34

# 3. 命中标记：响应体 _platform.cache_hit=true
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "X-Tenant-Id: demo-a" \
  -H "Authorization: Bearer sk-tenant-demo-a-change-me" \
  -d '{"model":"chat-fast","messages":[{"role":"user","content":"你好"}]}' \
  | jq '._platform.cache_hit, ._platform.cache_mode'
```

### 3.3 验收冒烟

`eval/acceptance_smoke.py` 已加入 `PG` 段检查：

```
PG  语义缓存 hit/miss + metrics   PASS   hits=1 misses=1
```

---

## 4. 指标

| 指标 | 类型 | 说明 |
|------|------|------|
| `semantic_cache_hits_total{tenant_id,model}` | counter | 命中次数 |
| `semantic_cache_misses_total{tenant_id,model}` | counter | 未命中次数 |
| `semantic_cache_tokens_saved_total{tenant_id,model}` | counter | 累计节省 token 数 |
| `semantic_cache_store_errors_total{tenant_id,model}` | counter | 存储异常次数 |
| `semantic_cache_lookup_latency_ms_p95{tenant_id,model}` | gauge | 查询延迟 P95 |

**命中率公式**：`hits / (hits + misses)`

---

## 5. 架构与代码导航

```
packages/semantic_cache/
├── __init__.py         # 包导出
├── metrics.py          # SemanticCacheMetrics（Prometheus 文本导出）
└── store.py            # 核心实现
    ├── normalize_messages()      # 消息归一化
    ├── build_cache_key()          # SHA256 确定性 key
    ├── cosine_similarity()        # 余弦相似度
    ├── SemanticCacheConfig        # 配置类
    ├── SemanticCache (ABC)        # 抽象基类
    ├── InMemorySemanticCache      # 进程内 LRU + TTL
    ├── RedisSemanticCache         # Redis 跨实例
    ├── init_semantic_cache()      # 工厂：自动选后端
    └── get_semantic_cache()       # 全局访问
```

**接入点**：`apps/gateway/main.py`
- `create_app()` 启动时根据 `SEMANTIC_CACHE_ENABLED` 初始化缓存
- `/v1/chat/completions` 在 quota 检查后、上游调用前查询缓存
- 上游成功响应后写入缓存（带 usage_tokens）
- `/metrics` 端点附加 `semantic_cache_*` 指标

---

## 6. 已知限制（面试时主动说）

1. **Redis 语义匹配为 O(N) 遍历**：当前实现遍历该租户所有 semantic 条目计算相似度，适合中小流量。大规模场景应升级为 Qdrant 向量库检索。
2. **无 negative cachinging**：未命中不缓存「无响应」状态。
3. **embedding 与 LLM 共用 Key**：尚未独立 embedding 服务治理（属 Phase G #35 范畴）。
4. **无自动 invalidate**：依赖 TTL 过期，不支持手动失效特定条目（后续可加 admin API）。
5. **仅缓存 `/v1/chat/completions`**：RAG `/v1/rag/query` 与 Agent `/v1/agent/run` 未接入（Agent 有工具调用，缓存语义复杂，暂不缓存）。

---

## 7. 后续演进

| Issue | 内容 | 依赖 |
|-------|------|------|
| #35 | Embedding 独立服务治理（独立 Key + SLA） | — |
| #36 | 多模态 Embedding 支持（图文混合） | #35 |
| 未来 | 接入 Qdrant 做语义检索（替换 Redis O(N)） | #35 |
| 未来 | admin invalidate API | — |
| 未来 | RAG query 缓存（需考虑 kb 版本） | — |

---

## 8. 面试讲法

1. **为什么需要语义缓存**：LLM 调用成本高，相似问题重复调用浪费 token；语义缓存能直接命中近义请求，降本 30%+。
2. **双模式设计**：exact 模式无依赖也能用；semantic 模式自动降级，保证可用性。
3. **跳过策略**：stream/temperature/模型黑名单，避免缓存非确定性输出。
4. **多租户隔离**：按 tenant_id 分桶，互不可见。
5. **可观测**：`semantic_cache_*` metrics 暴露命中率、节省 token、延迟，Grafana 可视化。
6. **诚实边界**：Redis 语义匹配 O(N) 遍历适合中小流量；大规模需升级为向量库检索。

参考代码：
- `packages/semantic_cache/store.py:184` — InMemorySemanticCache
- `packages/semantic_cache/store.py:236` — RedisSemanticCache
- `apps/gateway/main.py:238` — 缓存查询接入点
- `apps/gateway/main.py:175` — metrics 端点
