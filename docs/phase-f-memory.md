# Phase F — 长记忆持久化（#31）

> **目标**：将 Agent 会话历史从「内存 Session」升级为跨会话持久化的长期记忆。

对标「Agent 平台架构全景」中的「能力中台 — 长记忆管理」能力。这是 **#33 上下文压缩** 的前置依赖。

---

构建思路、使用链路与逐文件代码说明见 [phase-f-build-and-code-guide.md](./phase-f-build-and-code-guide.md)。

## 1. 设计要点

### 1.1 三级 Scope 隔离

| Scope | 用途 | 生命周期 | scope_id |
|-------|------|---------|---------|
| `session` | 单会话短期 | 会话结束即归档 | session_id |
| `user` | 跨会话中期 | 用户级偏好/历史 | user_id |
| `tenant` | 租户级共享 | 团队共享知识 | tenant_id |

### 1.2 数据模型

```python
@dataclass
class MemoryRecord:
    memory_id: str          # "mem-{uuid16}"
    tenant_id: str
    scope: str               # session | user | tenant
    scope_id: str
    content: str             # 记忆内容（文本，可为摘要）
    summary: str | None      # 可选二级摘要
    embedding: list[float] | None  # 可选向量（用于语义检索）
    metadata: dict           # 附加元数据（source, trace_id, turn_count 等）
    created_at: float
    expires_at: float | None  # None = 永不过期
```

### 1.3 存储后端

| 后端 | 触发条件 | 特点 |
|------|---------|------|
| `PostgresMemoryStore` | `DATABASE_URL` 可达 | 持久化主存，跨实例共享 |
| `InMemoryMemoryStore` | 兜底 | 进程内，重启丢失 |

### 1.4 检索模式

| 模式 | 说明 | 依赖 |
|------|------|------|
| `keyword` | content LIKE 模糊匹配 + 分词命中率 | 无（默认） |
| `semantic` | embedding cosine similarity | embedding 服务 |

无 `query_embedding` 时自动降级为 keyword 模式。

### 1.5 自动摘要触发

Agent runner 在每 `MEMORY_SUMMARIZE_EVERY_N_TURNS` 轮（默认 8）自动：
1. 调用 `summarize_messages()` 将当前会话历史压缩
2. 持久化为 `MemoryRecord`（scope=session, scope_id=session_id）
3. 失败时静默降级（不影响主流程）

摘要 prompt 优先从 prompt registry 取（`prompt_id="memory_summarize"`），否则用 fallback 模板。

---

## 2. REST API

| Method | Path | 权限 | 说明 |
|--------|------|------|------|
| POST | `/internal/memory` | platform_admin | 创建记忆 |
| GET | `/internal/memory/{memory_id}` | 任何已认证 | 获取单条 |
| POST | `/internal/memory/search` | 任何已认证 | 搜索（按 scope + scope_id + query） |
| GET | `/internal/memory/list?scope=&scope_id=&limit=` | 任何已认证 | 列出 |
| DELETE | `/internal/memory/{memory_id}` | platform_admin | 删除 |

### 使用示例

```bash
# 1. 创建用户级记忆
curl -s -X POST http://127.0.0.1:8000/internal/memory \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "scope": "user",
    "scope_id": "user-123",
    "content": "用户偏好：喜欢简洁回答，不喜欢过多解释",
    "metadata": {"source": "manual"}
  }'

# 2. 搜索
curl -s -X POST http://127.0.0.1:8000/internal/memory/search \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "scope": "user",
    "scope_id": "user-123",
    "query": "偏好",
    "top_k": 5
  }'
# → {"results": [...], "count": 1}

# 3. 列出
curl -s "http://127.0.0.1:8000/internal/memory/list?scope=user&scope_id=user-123&limit=10" \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me"

# 4. 删除
curl -s -X DELETE http://127.0.0.1:8000/internal/memory/mem-abc123 \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me"
```

---

## 3. Postgres Schema

`PostgresMemoryStore` 启动时自动建表：

```sql
CREATE TABLE IF NOT EXISTS agent_memories (
    memory_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    content TEXT NOT NULL,
    summary TEXT,
    embedding JSONB,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_mem_scope
    ON agent_memories (tenant_id, scope, scope_id);
```

无需手动迁移，首次启动自动执行。

---

## 4. 配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `MEMORY_STORE_ENABLED` | `true` | 总开关 |
| `AGENT_MEMORY_MODEL` | `None` | 摘要模型；未配置则回退 `DEFAULT_MODEL` |
| `MEMORY_SUMMARIZE_EVERY_N_TURNS` | `8` | 自动摘要触发周期 |
| `MEMORY_DEFAULT_TTL_SECONDS` | `0` | 默认 TTL；0 = 永不过期 |
| `MEMORY_SEARCH_TOP_K` | `5` | 检索返回 top_k |

---

## 5. 集成点

### 5.1 Agent Runner

`packages/agent/runner.py`：
- `_maybe_persist_memory()` — 在 `save_session_state` 后按周期触发
- 自动 summarize + add 到 store
- 响应体 `_platform.memory_persisted=true` 标记是否触发

### 5.2 网关启动

`apps/gateway/main.py`：
- `create_app()` 初始化 `MemoryStore`
- 挂载 `/internal/memory/*` 路由
- `/metrics` 端点附加 `memory_*` 指标

### 5.3 摘要服务

`packages/memory/summarize.py`：
- `summarize_messages()` — 调用 LLM 压缩历史
- prompt 模板优先取 registry（`memory_summarize`），否则 fallback
- 失败时降级为简单截断

---

## 6. 指标

| 指标 | 类型 | 说明 |
|------|------|------|
| `memory_adds_total{tenant_id,scope}` | counter | 写入次数 |
| `memory_searches_total{tenant_id,scope}` | counter | 检索次数 |
| `memory_cache_hits_total{tenant_id,scope}` | counter | 缓存命中（预留） |
| `memory_cache_misses_total{tenant_id,scope}` | counter | 缓存未命中（预留） |
| `memory_store_errors_total{tenant_id,scope}` | counter | 存储异常次数 |
| `memory_search_latency_ms_p95{tenant_id,scope}` | gauge | 检索延迟 P95 |

---

## 7. 测试与验收

```bash
# 1. 单元测试（12 个用例）
python3 tests/test_memory.py
# 期望：12/12 passed

# 2. 验收冒烟（PF 段）
python3 eval/acceptance_smoke.py
# 期望：
# PF  长记忆 add/search + metrics   PASS
# PF  长记忆 REST API CRUD          PASS
```

---

## 8. 代码导航

```
packages/memory/
├── __init__.py          # 包导出
├── metrics.py            # MemoryMetrics（Prometheus 文本）
├── store.py              # 核心实现
│   ├── MemoryRecord         # 数据类
│   ├── MemoryStore (ABC)    # 抽象基类
│   ├── InMemoryMemoryStore  # 进程内
│   ├── PostgresMemoryStore  # Postgres 持久化
│   ├── init_memory_store()  # 工厂（自动选后端）
│   └── get_memory_store()   # 全局访问
└── summarize.py         # 摘要服务
    └── summarize_messages()  # 调用 LLM 压缩历史

apps/gateway/memory_routes.py    # REST API
packages/agent/runner.py         # Agent 集成（自动 summarize）
```

---

## 9. 已知限制（面试时主动说）

1. **无 Redis 热缓存**：当前只有 Postgres + 进程内；高频读场景应加 Redis 缓存层。
2. **语义检索为内存计算**：`search()` 在内存中打分（取 top_k*4 候选后排序），适合中小规模。大规模应升级为 Qdrant/pgvector。
3. **无 TTL 自动清理**：`expires_at` 仅在查询时过滤；无后台清理任务。生产应加定时 job。
4. **摘要 prompt 无独立版本**：当前用 fallback 模板；可在 `config/prompts.yaml` 添加 `memory_summarize` id 走 registry 管理。
5. **仅 Agent 自动触发**：RAG query 不自动写记忆（语义不同）；可手动通过 API 写入。
6. **无 PII 脱敏**：写入 content 前未做敏感信息检测（属 Phase I #43 范畴）。

---

## 10. 后续演进

| Issue | 内容 | 依赖 |
|-------|------|------|
| #33 | 上下文压缩策略（滑窗 + LLM 摘要 + Token 感知注入） | #31 ✅ |
| 未来 | Redis 热缓存层 | — |
| 未来 | pgvector / Qdrant 语义检索 | #35 |
| 未来 | TTL 后台清理 job | — |
| 未来 | PII 脱敏（写入前检测） | #43 |
| 未来 | 跨会话记忆检索注入 Agent system prompt | Console V2 |

---

## 11. 面试讲法

1. **为什么需要长记忆**：Session 是单会话的，重启即丢；用户偏好、历史决策需要跨会话保留，否则每次对话从零开始。
2. **三级 Scope 设计**：session 短期 / user 中期 / tenant 共享，覆盖不同生命周期需求。
3. **双后端 + 自动降级**：Postgres 持久化主存，不可达时进程内兜底，保证可用性。
4. **自动摘要触发**：Agent 每 N 轮自动 summarize，无需人工介入；失败静默降级不影响主流程。
5. **诚实边界**：当前无 Redis 热缓存、无 TTL 清理 job、语义检索为内存计算（适合中小规模）。

参考代码：
- `packages/memory/store.py:60` — MemoryRecord 数据类
- `packages/memory/store.py:90` — InMemoryMemoryStore
- `packages/memory/store.py:200` — PostgresMemoryStore
- `packages/memory/summarize.py:30` — summarize_messages
- `packages/agent/runner.py:50` — _maybe_persist_memory
- `apps/gateway/memory_routes.py` — REST API
