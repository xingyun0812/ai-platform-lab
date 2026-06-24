# Phase G — Embedding 独立服务（#35）

> **目标**：将 Embedding 从 RAG 内联调用中抽取为独立微服务模块，提供统一的 provider 抽象、模型注册表和 REST API。

---

## 1. 设计要点

构建思路、使用链路与逐文件代码说明见 [phase-g-build-and-code-guide.md](./phase-g-build-and-code-guide.md)。

### 1.1 Provider 抽象

| Provider | 说明 | 使用场景 |
|----------|------|---------|
| `StubProvider` | 基于 MD5 哈希的确定性向量，无 LLM 调用 | 测试、CI、降级 |
| `OpenAIProvider` | 调用 OpenAI Embeddings API（httpx async） | 生产，text-embedding-3-small/large/ada-002 |
| `custom` | 扩展预留（实现 `EmbeddingProvider.embed` 即可） | 自托管模型 |

**降级逻辑**（`provider_factory`）：

```
provider == "stub" → StubProvider
provider == "openai" + 无 LLM_API_KEY → StubProvider（warning log）
provider == "openai" + 有 LLM_API_KEY → OpenAIProvider
其他未知 provider → StubProvider（warning log）
```

### 1.2 数据模型

```python
@dataclass
class EmbeddingModel:
    model_id: str            # 唯一标识（如 "text-embedding-3-small"）
    name: str                # 显示名
    provider: str            # "openai" | "stub" | "custom"
    dimensions: int          # 向量维度
    max_input_tokens: int    # 最大输入 token 数
    created_at: float        # Unix 时间戳
    metadata: dict           # 扩展字段（定价、legacy 标志等）

@dataclass
class EmbeddingRequest:
    model_id: str
    texts: list[str]
    tenant_id: str = "system"

@dataclass
class EmbeddingResponse:
    model_id: str
    embeddings: list[list[float]]
    dimensions: int
    usage: dict   # total_texts, cached_texts, computed_texts
    cached: bool  # True = 全部命中缓存
```

### 1.3 EmbeddingRegistry（注册表）

- 线程安全（`threading.RLock`）
- 启动时从 `config/embedding_models.yaml` 加载默认配置
- Admin API 修改写入 `data/embedding_models_overrides.json`（overrides 覆盖 YAML）
- CRUD：`register_model` / `get_model` / `list_models` / `remove_model`
- 全局单例：`init_registry()` / `get_registry()` / `reset_for_tests()`

### 1.4 EmbeddingService（服务层）

- 统一 `async embed(request)` 接口
- **LRU 缓存**：text sha256 → embedding，maxsize=10000（可配置）
  - `OrderedDict` 实现，每次访问 `move_to_end`，满时淘汰最旧
  - 缓存键：`sha256("{model_id}:{text}")`，跨模型隔离
- **批量优化**：同一请求中，已缓存文本不再调用 provider；未缓存文本批量送给 provider
- `embed_one(model_id, text)` 便捷单文本接口
- `cache_stats()` 返回 size / hits / misses / hit_rate
- 全局单例：`init_embedding_service()` / `get_embedding_service()` / `reset_embedding_service_for_tests()`

### 1.5 StubProvider 设计

```
text → hashlib.md5(text.encode() + str(i).encode()).digest()
     → 每 16 bytes 解为 4 个 float32
     → 重复直到填满 dimensions
     → L2 归一化 → 单位向量
```

同一文本 + 同一维度 → 始终相同向量，满足测试幂等性。

### 1.6 集成点

当前 RAG pipeline（`packages/rag/`）内联调用 OpenAI embeddings，可替换为：

```python
from packages.embedding.service import get_embedding_service

svc = get_embedding_service()
if svc:
    vector = await svc.embed_one("text-embedding-3-small", chunk_text)
else:
    # fallback: 直接调用原有逻辑
    ...
```

---

## 2. REST API

路由前缀：`/internal/embeddings`

| Method | Path | 权限 | 说明 |
|--------|------|------|------|
| GET | `/internal/embeddings/models` | 任何已认证 | 列出所有 embedding 模型 |
| GET | `/internal/embeddings/models/{model_id}` | 任何已认证 | 模型详情 |
| POST | `/internal/embeddings/models` | admin | 注册新模型 |
| DELETE | `/internal/embeddings/models/{model_id}` | admin | 删除模型 |
| POST | `/internal/embeddings/embed` | 任何已认证 | 生成 embedding |
| GET | `/internal/embeddings/cache/stats` | 任何已认证 | 缓存统计 |
| DELETE | `/internal/embeddings/cache` | admin | 清除缓存 |

### 使用示例

```bash
# 1. 列出模型
curl -s http://127.0.0.1:8000/internal/embeddings/models \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me"

# 2. 注册自定义模型
curl -s -X POST http://127.0.0.1:8000/internal/embeddings/models \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{"model_id": "my-model", "provider": "stub", "dimensions": 256}'

# 3. 生成 embedding
curl -s -X POST http://127.0.0.1:8000/internal/embeddings/embed \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{"model_id": "stub-embedding", "texts": ["hello world", "foo bar"]}'

# 响应示例
{
  "model_id": "stub-embedding",
  "embeddings": [[0.12, -0.34, ...], [0.56, 0.78, ...]],
  "dimensions": 1536,
  "usage": {"total_texts": 2, "cached_texts": 0, "computed_texts": 2},
  "cached": false
}

# 4. 缓存统计
curl -s http://127.0.0.1:8000/internal/embeddings/cache/stats \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me"

# 5. 清除缓存
curl -s -X DELETE http://127.0.0.1:8000/internal/embeddings/cache \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me"
```

---

## 3. 配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `EMBEDDING_SERVICE_ENABLED` | `true` | 总开关 |
| `EMBEDDING_MODELS_CONFIG_PATH` | `config/embedding_models.yaml` | YAML 默认模型配置 |
| `EMBEDDING_MODELS_OVERRIDES_PATH` | `data/embedding_models_overrides.json` | 运行时覆盖 |
| `EMBEDDING_CACHE_MAX_SIZE` | `10000` | LRU 缓存最大条数 |
| `EMBEDDING_DEFAULT_MODEL` | `text-embedding-3-small` | 默认 embedding 模型 |

---

## 4. settings.py 集成指南（DO NOT edit settings.py 直接；由 parent agent 负责）

在 `apps/gateway/settings.py` 的 `Settings` 类中添加：

```python
embedding_service_enabled: bool = Field(
    default=True,
    validation_alias="EMBEDDING_SERVICE_ENABLED",
    description="启用独立 Embedding 服务",
)
embedding_models_config_path: Path = Field(
    default=REPO_ROOT / "config" / "embedding_models.yaml",
    validation_alias="EMBEDDING_MODELS_CONFIG_PATH",
    description="Embedding 模型 YAML",
)
embedding_models_overrides_path: Path = Field(
    default=REPO_ROOT / "data" / "embedding_models_overrides.json",
    validation_alias="EMBEDDING_MODELS_OVERRIDES_PATH",
    description="Embedding 模型 overrides",
)
embedding_cache_max_size: int = Field(
    default=10000,
    validation_alias="EMBEDDING_CACHE_MAX_SIZE",
    description="Embedding 缓存最大条数",
)
embedding_default_model: str = Field(
    default="text-embedding-3-small",
    validation_alias="EMBEDDING_DEFAULT_MODEL",
    description="默认 embedding 模型",
)
```

---

## 5. main.py 集成指南

在 `apps/gateway/main.py` 的 startup 部分添加：

```python
from apps.gateway.embedding_routes import router as embedding_router

# 在 lifespan / startup 中
if settings.embedding_service_enabled:
    from packages.embedding import init_embedding_service
    init_embedding_service(
        registry_yaml_path=settings.embedding_models_config_path,
        registry_overrides_path=settings.embedding_models_overrides_path,
        cache_max_size=settings.embedding_cache_max_size,
    )

app.include_router(embedding_router)
```

---

## 6. .env.example 新增内容

```bash
# ── Embedding 独立服务 ──────────────────────────────────────────────
EMBEDDING_SERVICE_ENABLED=true
EMBEDDING_MODELS_CONFIG_PATH=config/embedding_models.yaml
EMBEDDING_MODELS_OVERRIDES_PATH=data/embedding_models_overrides.json
EMBEDDING_CACHE_MAX_SIZE=10000
EMBEDDING_DEFAULT_MODEL=text-embedding-3-small
```

---

## 7. README 更新内容（新增章节）

```markdown
### Phase G — Embedding 独立服务 (#35)

独立的 Embedding 微服务模块，支持 OpenAI/stub/custom provider 抽象、
模型注册表（YAML + JSON overrides）和 LRU 缓存。

**接口**：`/internal/embeddings/*`  
**文档**：`docs/phase-g-embedding.md`  
**测试**：`python3 tests/test_embedding.py`  # 18/18 passed
```

---

## 8. roadmap.md 更新

在 roadmap 中添加：

```markdown
| Phase G | #35 | Embedding 独立服务 | ✅ 完成 |
```

---

## 9. 测试与验收

```bash
# 单元测试（18 个用例）
python3 tests/test_embedding.py
# 期望：18/18 passed

# 语法检查
python3 -c "import ast; ast.parse(open('packages/embedding/models.py').read())"
python3 -c "import ast; ast.parse(open('packages/embedding/providers.py').read())"
python3 -c "import ast; ast.parse(open('packages/embedding/service.py').read())"
python3 -c "import ast; ast.parse(open('packages/embedding/__init__.py').read())"
python3 -c "import ast; ast.parse(open('apps/gateway/embedding_routes.py').read())"
```

---

## 10. 代码导航

```
packages/embedding/
├── __init__.py           # 包导出（所有公开符号）
├── models.py             # 数据模型 + 注册表
│   ├── EmbeddingModel       # 模型配置 dataclass
│   ├── EmbeddingRequest     # 请求 dataclass
│   ├── EmbeddingResponse    # 响应 dataclass
│   ├── EmbeddingRegistry    # YAML + JSON overrides 注册表
│   ├── init_registry()      # 全局单例初始化
│   ├── get_registry()       # 全局单例获取
│   └── reset_for_tests()    # 测试重置
├── providers.py          # Provider 抽象 + 实现
│   ├── EmbeddingProvider    # 抽象基类
│   ├── StubProvider         # 确定性哈希向量
│   ├── OpenAIProvider       # OpenAI Embeddings API
│   └── provider_factory()   # 根据模型配置选择提供商
└── service.py            # 服务层
    ├── _LRUCache            # OrderedDict 实现的 LRU
    ├── EmbeddingService     # 统一服务 + 缓存
    ├── init_embedding_service()           # 全局单例初始化
    ├── get_embedding_service()            # 全局单例获取
    └── reset_embedding_service_for_tests() # 测试重置

apps/gateway/embedding_routes.py  # REST API（7 个端点）
config/embedding_models.yaml      # 种子模型配置
tests/test_embedding.py           # 18 个测试用例
```

---

## 11. 已知限制（面试时主动说）

1. **无批量优化**：单次 `/embed` 请求的多文本虽然缓存部分优化，但传给 OpenAI 的批次大小未做分片（OpenAI 限制 2048 个 input per 请求）；生产应加 `chunk_batch` 分片。
2. **缓存不分布式**：当前 LRU 缓存是进程内 `OrderedDict`；多实例部署时缓存不共享。生产应用 Redis + 向量压缩（float32 序列化）。
3. **无 OpenAI 限速重试**：OpenAI rate limit（429）直接抛出；生产应加指数退避 + tenacity 重试。
4. **无多模态支持**：仅文本 embedding；图像/音频 embedding（如 CLIP）需额外 provider 实现。→ **Phase P P1 已补** text+image inputs + stub-multimodal，见 [phase-p-multimodal-embedding.md](./phase-p-multimodal-embedding.md)。
5. **无异步 Provider 池**：多个 provider 实例串行调用；生产应加 provider pool + 并发控制。
6. **无维度验证**：注册模型时 `dimensions` 只做 `> 0` 检查；若 OpenAI API 返回的实际维度与配置不匹配，调用方会静默使用错误维度。

---

## 12. 面试讲法

1. **为什么独立服务**：RAG pipeline 内联 embedding 导致耦合——换模型、加缓存、限流都要改 RAG 代码。抽取为独立服务后，RAG 只管调 `embed_one`，embedding 内部可透明替换。
2. **Provider 工厂模式**：`provider_factory` 根据配置 + 环境变量决策，无 API Key 自动降级 StubProvider，满足测试环境零外部依赖。
3. **LRU 缓存设计**：用 `OrderedDict` + `move_to_end` 实现 O(1) LRU；缓存键用 `sha256("{model_id}:{text}")` 确保跨模型隔离。Embedding 请求里批量混合 hit/miss，只对 miss 部分调 provider，减少 token 消耗。
4. **注册表模式**：沿用 MCP/MultiAgent 的 YAML + JSON overrides 双层加载——YAML 进 git 做审计，JSON overrides 在运行时由 Admin API 写入。
5. **StubProvider 确定性**：MD5 哈希保证同文本同维度输出相同向量，测试可做 `assert results[0] == results[2]`，不依赖 LLM 调用，CI 速度快。
6. **诚实边界**：缓存进程内不分布式、无批量分片、无 429 重试、无多模态——这些是生产化需补的，面试时主动说，展示系统思维。

参考代码：
- `packages/embedding/models.py:60` — EmbeddingRegistry
- `packages/embedding/providers.py:40` — StubProvider._hash_to_vector
- `packages/embedding/providers.py:105` — provider_factory
- `packages/embedding/service.py:30` — _LRUCache
- `packages/embedding/service.py:70` — EmbeddingService.embed（批量缓存优化）
- `apps/gateway/embedding_routes.py` — REST API
