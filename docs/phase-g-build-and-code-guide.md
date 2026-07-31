# Phase G 构建思路与代码导读：模型服务增强

> 规格书：[semantic-cache](./phase-g-semantic-cache.md) · [embedding](./phase-g-embedding.md)

---

## 目录

1. [构建思路](#1-构建思路)
2. [使用链路](#2-使用链路)
3. [代码导读（按文件）](#3-代码导读按文件)
4. [设计决策](#4-设计决策)
5. [操作命令](#5-操作命令)
6. [自测用例](#6-自测用例)

---

## 1. 构建思路

Phase G 在 Phase F 的能力中台补全之后，做 **模型服务增强**：语义缓存降本、Embedding 独立服务化。两个能力分别从"成本控制"和"服务治理"两个维度提升平台的工程成熟度。

| Issue | 能力 | 核心文件 | 接入方式 |
|-------|------|----------|----------|
| #34 | 语义缓存 | `packages/semantic_cache/store.py` | `/v1/chat/completions` 中拦截；quota 之后、上游之前 |
| #35 | Embedding 独立服务 | `packages/embedding/service.py` | REST API `/internal/embeddings/*` + 被 RAG/Memory 调用 |

### #34 — 语义缓存

**问题**：用户反复问相同或近义问题（如"你好"→"你好呀"），每次都要调 LLM，浪费 token 和成本。

**方案**：在 Gateway 的 `/v1/chat/completions` 路径上加一道缓存，quota 检查之后、上游调用之前查，命中直接返回历史响应。

**双模式命中策略**：

| 模式 | 命中策略 | 适用场景 |
|------|---------|---------|
| `exact` | SHA256(tenant_id + model + normalized_messages) | 零依赖，高一致性 |
| `semantic` | Embedding 余弦相似度 >= threshold（默认 0.92） | 容忍近义复述，降本更多 |

`semantic` 模式下 embedding 服务不可用时自动降级 `exact`，保证可用性。

**跳过缓存的场景**：

| 条件 | 原因 |
|------|------|
| `stream=true` | 流式响应不能缓存 |
| `temperature > 0.3` | 高随机性输出不应缓存 |
| `model` 在黑名单中 | reasoning 模型每次输出不同 |
| 上游响应非 2xx | 不缓存错误 |

**双后端**：

| 后端 | 条件 | 特点 |
|------|------|------|
| `InMemorySemanticCache` | `REDIS_URL` 不可达 | 进程内 LRU + TTL，单实例 |
| `RedisSemanticCache` | `REDIS_URL` 可达 | Hash + TTL，跨实例共享 |

**多租户隔离**：按 `tenant_id` 分桶，缓存条目互不可见。

**可观测**：`semantic_cache_*` Prometheus 指标，Grafana 面板可视化命中率和节省 token。

### #35 — Embedding 独立服务

**问题**：之前 Embedding 是在 RAG pipeline 内部联调 OpenAI 的，RAG 代码里直接调 OpenAI SDK。换模型、加缓存、限流都得改 RAG 代码，耦合严重。同时 Memory 搜索、语义缓存等新功能也需要 embedding，没法复用。

**方案**：抽取为独立微服务模块，三个核心抽象：

1. **Provider 抽象**：StubProvider（确定性 MD5 哈希，测试用）+ OpenAIProvider（调 OpenAI API）+ 扩展预留
2. **EmbeddingRegistry**：模型配置注册表（YAML + JSON overrides，与 Prompt/MCP 同模式）
3. **EmbeddingService**：统一 embed 接口 + LRU 缓存 + 批量混合 hit/miss

**Provider 降级逻辑**：

```
provider == "stub"                    → StubProvider
provider == "openai" + 无 LLM_API_KEY → StubProvider（warning log）
provider == "openai" + 有 Key        → OpenAIProvider
其他                                  → StubProvider（warning log）
```

**LRU 缓存**：`OrderedDict` 实现，maxsize=10000，缓存键 `sha256("{model_id}:{text}")` 跨模型隔离。

**批量优化**：同一请求中已缓存的文本不再调 provider，只对 miss 部分计算，减少 token 消耗。

---

## 2. 使用链路

### 2.1 语义缓存命中与写入

```mermaid
sequenceDiagram
  participant C as Client
  participant GW as Gateway
  participant SC as SemanticCache
  participant LLM as 上游

  C->>GW: POST /v1/chat/completions
  GW->>GW: 鉴权 → 限流 → 配额
  GW->>SC: lookup(tenant, model, messages)
  alt 命中
    SC-->>GW: CacheLookupResult(similarity=1.0)
    GW-->>C: 200 _platform.cache_hit=true
  else 跳过
    SC-->>GW: "stream=true" 或 "temperature>0.3"
    GW->>LLM: 转发（但不缓存结果）
  else 未命中
    GW->>LLM: 正常转发
    LLM-->>GW: 200
    GW->>SC: store(tenant, model, messages, response, tokens)
    GW-->>C: 200 _platform.cache_hit=false
  end
```

### 2.2 Embedding 服务调用

```mermaid
sequenceDiagram
  participant C as Client / RAG / Memory
  participant API as /internal/embeddings/embed
  participant SVC as EmbeddingService
  participant CACHE as _LRUCache
  participant PROV as Provider

  C->>API: POST embed(model_id, texts=[...])
  API->>SVC: embed(request)
  SVC->>CACHE: 遍历 texts，逐条查询
  alt 命中
    CACHE-->>SVC: embedding
  else 未命中
    SVC->>PROV: provider.embed(miss_items)
    PROV-->>SVC: vectors
    SVC->>CACHE: set(key, vector)
  end
  SVC-->>API: EmbeddingResponse(embeddings, usage={cached, computed})
  API-->>C: 200
```

---

## 3. 代码导读（按文件）

### `packages/semantic_cache/store.py`（#34 语义缓存核心）

**537 行，命中策略 + 双后端 + 跳过逻辑。**

**消息归一化 `normalize_messages()`**：
```python
def normalize_messages(messages):
    # 去除空白
    # 拼接 role:content 格式
    # OpenAI 多模态格式（content 为 list）中只抽 text 段
    return "\n".join(parts)
```

**Key 生成 `build_cache_key()`**：
```python
def build_cache_key(*, tenant_id, model, normalized):
    # SHA256(tenant_id + "|" + model + "|" + normalized)
    return h.hexdigest()
```

**核心类 `SemanticCache`**：
| 方法 | 职责 |
|------|------|
| `lookup()` | 返回 `CacheLookupResult`（命中）/ `None`（未命中）/ `str`（跳过原因） |
| `store()` | 写缓存（semantic 模式额外存 embedding） |
| `_should_skip()` | 检查 stream/temperature/model 黑名单 |
| `_maybe_embed()` | semantic 模式下调 embed_texts 生成 query embedding |

**`InMemorySemanticCache._lookup_impl()`**：
```python
def _lookup_impl(tenant_id, cache_key, normalized):
    # 1) exact match → cache_key 查 OrderedDict → 命中返回
    # 2) semantic match → 遍历该租户所有条目算 cosine_similarity
    #    → 最高分 >= threshold 返回
```

**`RedisSemanticCache`**：
- Key 结构：`ai_platform:sem_cache:{tenant_id}:exact` / `:sem`
- Hash 存储 JSON 序列化的 `CacheEntry`
- 语义匹配：`hgetall(tenant_id)` → 客户端遍历算相似度（O(N)，适合中小流量）
- 同时写 exact 和 semantic 两份（精确命中先走 exact）

**跳过逻辑 `_should_skip()`**：
```python
def _should_skip(tenant_id, model, temperature, stream):
    if stream:           return "stream=true"
    if model in skip:    return "model in skip_list"
    if temp > max_temp:  return "temperature > max"
    return None  # 不跳过
```

### `packages/semantic_cache/metrics.py`（#34 指标）

**123 行，进程内 Prometheus 文本导出。**

| 指标 | 类型 | 含义 |
|------|------|------|
| `semantic_cache_hits_total{tenant_id,model}` | counter | 命中次数 |
| `semantic_cache_misses_total{tenant_id,model}` | counter | 未命中次数 |
| `semantic_cache_tokens_saved_total{tenant_id,model}` | counter | 累计节省 token 数 |
| `semantic_cache_store_errors_total{tenant_id,model}` | counter | 存储异常 |
| `semantic_cache_lookup_latency_ms_p95{tenant_id,model}` | gauge | 查询延迟 P95 |

`prometheus_text()` 生成 Prometheus 格式文本，按 `(tenant_id, model)` 分组，`/metrics` 端点直接挂载。

### `packages/embedding/models.py`（#35 数据模型 + 注册表）

**250 行。**

**`EmbeddingModel`** 核心字段：
| 字段 | 含义 |
|------|------|
| `model_id` | 唯一标识（如 `text-embedding-3-small`） |
| `provider` | `openai` / `stub` / `custom` |
| `dimensions` | 向量维度 |
| `modalities` | `["text"]` 或 `["text", "image"]` |

**`EmbeddingRequest` / `EmbeddingResponse`**：请求和响应 dataclass。

**`EmbeddingRegistry`**：
- YAML + JSON overrides 双层加载（与 PromptRegistry 同模式）
- `register_model()` / `get_model()` / `list_models()` / `remove_model()`
- `_persist()` 写入 JSON overrides
- 全局单例 `init_registry()` / `get_registry()`

### `packages/embedding/providers.py`（#35 Provider 抽象）

**146 行，三种 provider 实现。**

**`StubProvider`**：
```python
def _hash_to_vector(self, text, dimensions):
    # MD5(text + str(i)) → 每个字节 → [-1,1] 浮点数
    # 填充到 dimensions → L2 归一化
    # 同一文本 + 同一维度 → 始终相同向量
```
用 MD5 而非 hash() 保证跨进程确定性。测试可断言 `results[0] == results[2]`，不依赖 LLM。

**`OpenAIProvider`**：
```python
async def embed(self, items, model):
    # POST {base_url}/embeddings
    # 逐个 item 调用（当前无批量分片）
    # 支持 text-embedding-3-small/large/ada-002
```

**`provider_factory()`**：根据 `model.provider` + `LLM_API_KEY` 是否存在自动决策降级。

### `packages/embedding/service.py`（#35 服务层 + LRU 缓存）

**237 行。**

**`_LRUCache`**：
- `OrderedDict` 实现 O(1) LRU
- 线程安全（`threading.Lock`）
- `get()` 命中时 `move_to_end` 刷新
- `set()` 满时 `popitem(last=False)` 淘汰最旧
- `stats()` 返回 size/hits/misses/hit_rate

**`EmbeddingService.embed()`**：
```python
async def embed(self, request):
    # 1) 查 registry 获取模型配置
    # 2) validate_modalities 检查输入模态
    # 3) provider_factory 选 provider
    # 4) 遍历 texts，查 LRU 缓存（hit 和 miss 分离）
    # 5) 只对 miss 的文本调 provider.embed()
    # 6) 结果回填缓存
    # 7) 拼 EmbeddingResponse(embeddings, usage)
```

**`embed_one()`** — 单文本快捷方法，RAG 和 Memory 模块直接调。

### `apps/gateway/embedding_routes.py`（#35 REST API）

**252 行，7 个端点。**

| 端点 | 方法 | 权限 |
|------|------|------|
| `/internal/embeddings/models` | GET | 任何认证 |
| `/internal/embeddings/models/{id}` | GET | 任何认证 |
| `/internal/embeddings/models` | POST | admin |
| `/internal/embeddings/models/{id}` | DELETE | admin |
| `/internal/embeddings/embed` | POST | 任何认证 |
| `/internal/embeddings/cache/stats` | GET | 任何认证 |
| `/internal/embeddings/cache` | DELETE | admin |

### `config/embedding_models.yaml`（#35 种子模型配置）

4 个种子模型：`Qwen/Qwen3-Embedding-8B`（4096 维，内网网关默认）、`text-embedding-3-small`（1536 维，OpenAI 官方）、`stub-embedding`（4096 维，测试用）、`stub-multimodal`（4096 维，图文测试）。

### 读代码顺序

```
semantic_cache/store.py → metrics.py →
embedding/models.py → providers.py → service.py → embedding_routes.py
```

---

## 4. 设计决策

| 决策 | 选型 | 理由 |
|------|------|------|
| 语义缓存命中位置 | quota 之后、上游之前 | 配额拦截后不浪费缓存查询，缓存命中后不走上游 |
| 双模式 exact/semantic | 配置切换，semantic 自动降级 exact | 零依赖也能用，有 embedding 降本更多 |
| 跳过缓存条件 | stream/temperature/模型黑名单 | 避免缓存非确定性输出，防止错误复现 |
| Redis 语义匹配 O(N) | 客户端遍历 Hash | 中小流量够用；大规模升级 Qdrant |
| Embedding Provider 工厂 | provider + LLM_API_KEY 自动决策 | 无 Key 自动降级 Stub，CI 零外部依赖 |
| LRU 缓存用 OrderedDict | 进程内 O(1) | 无需 Redis，部署简单 |
| 批量混合 hit/miss | 只调 provider 计算 miss 部分 | 减少 token 消耗，提升响应速度 |
| StubProvider 用 MD5 哈希 | 确定性向量 | 测试幂等，不依赖 LLM |
| YAML + JSON overrides 注册表 | 与 Prompt/MCP 同模式 | 统一基建模式，降低学习成本 |

---

## 5. 操作命令

```bash
# #34 启用语义缓存（最小配置）
export SEMANTIC_CACHE_ENABLED=true
export SEMANTIC_CACHE_MODE=exact  # 无需 LLM Key

# #34 查看命中率
curl -s http://127.0.0.1:8000/metrics | grep semantic_cache

# #34 验证命中标记
curl -s -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "X-Tenant-Id: demo-a" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"model":"chat-fast","messages":[{"role":"user","content":"你好"}]}' | jq '._platform.cache_hit'

# #34 语义模式（需 LLM_API_KEY）
export SEMANTIC_CACHE_MODE=semantic
# 近义 query "你好" vs "你好呀" → 可能命中

# #35 列出 embedding 模型
curl -s http://127.0.0.1:8000/internal/embeddings/models \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" | jq .

# #35 生成 embedding
curl -s -X POST http://127.0.0.1:8000/internal/embeddings/embed \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"model_id":"stub-embedding","texts":["hello world","foo bar"]}' | jq .

# #35 查看缓存统计
curl -s http://127.0.0.1:8000/internal/embeddings/cache/stats \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" | jq .

# #35 注册自定义模型
curl -s -X POST http://127.0.0.1:8000/internal/embeddings/models \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"model_id":"my-model","provider":"stub","dimensions":256}' | jq .

# #35 清除缓存（admin）
curl -s -X DELETE http://127.0.0.1:8000/internal/embeddings/cache \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" | jq .

# 单元测试
python3 tests/test_semantic_cache.py
python3 tests/test_embedding.py

# 验收
python eval/acceptance_smoke.py
```

---

## 6. 自测用例

| # | 输入 | 预期 |
|---|------|------|
| 1 | 相同 chat 请求两次 | 第二次 `cache_hit=true` |
| 2 | semantic 模式近义 query | 可能命中（similarity >= 0.92） |
| 3 | `stream=true` | 不走缓存，跳过原因 `stream=true` |
| 4 | `temperature > 0.3` | 跳过缓存 |
| 5 | GET /metrics | `semantic_cache_*` 指标 |
| 6 | POST embed stub-embedding | 返回 4096 维向量 |
| 7 | 相同 text 两次 embed | 第二次 usage.cached_texts > 0 |
| 8 | GET embedding models | 返回 4 个种子模型含 Qwen3 |
| 9 | Redis 语义缓存（REDIS_URL 可达） | 跨 gateway 实例共享缓存 |
| 10 | embedding 服务不可用时 semantic | 自动降级 exact 匹配 |