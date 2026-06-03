# 第 2 周：RAG 数据管道

学习计划见 [AI中台学习执行手册](./AI中台学习执行手册.md) 第 2 周。  
构建思路、使用链路与逐文件代码说明见 [rag-build-and-code-guide.md](./rag-build-and-code-guide.md)。  
第 1 周 Gateway 见 [gateway-build-and-code-guide.md](./gateway-build-and-code-guide.md)。  
第 3 周对外问答见 [week3-rag-query.md](./week3-rag-query.md)。

---

## 目标

把「文档进知识库」做成平台能力：**指定路径或上传** → **异步任务** → chunk → embed → 写入 **Qdrant**；支持 **`kb_id` + `version`**（版本手动 bump）。

---

## 架构与数据流

```mermaid
flowchart LR
  subgraph in["输入"]
    P["source_uri 相对路径"]
    U["multipart 上传"]
  end
  subgraph gw["gateway"]
    API["POST /internal/index"]
    BG["BackgroundTasks"]
    PIPE["pipeline.run_index_task"]
  end
  subgraph core["packages/rag"]
    CH["chunker"]
    EM["embeddings 直连上游"]
    VS["vector_store → Qdrant"]
  end
  P --> API
  U --> API
  API --> BG --> PIPE
  PIPE --> CH --> EM --> VS
```

```mermaid
sequenceDiagram
  participant C as 客户端
  participant G as Gateway
  participant Q as Qdrant
  participant L as LLM Embeddings API

  C->>G: POST /internal/index
  G-->>C: task_id pending
  G->>G: 后台 run_index_task
  G->>G: 读文件 UTF-8 → chunk
  G->>L: POST /embeddings
  G->>Q: delete kb_id+version 旧点
  G->>Q: upsert 新向量
  G->>G: task success/failed
  C->>G: GET /internal/index/tasks/{id}
  C->>G: POST /internal/retrieve
  G->>L: query embedding
  G->>Q: 向量检索
  G-->>C: chunks[] 含 chunk_id/kb_id/version
```

---

## 前置条件

1. Python 3.11+，已 `pip install -e .`
2. `.env` 中配置 **`LLM_API_KEY`**（embedding 与第 1 周共用上游）
3. 启动 Qdrant：

```bash
docker compose --profile vectors up -d
```

4. 启动网关：

```bash
uvicorn apps.gateway.main:app --reload --host 127.0.0.1 --port 8000
```

鉴权与第 1 周相同：`X-Tenant-Id` + `Authorization: Bearer <租户 token>`（建议用 `admin`）。

---

## 配置

| 来源 | 项 | 说明 |
|------|-----|------|
| `.env` | `QDRANT_URL` | 默认 `http://127.0.0.1:6333` |
| `.env` | `EMBEDDING_MODEL` | 默认 `text-embedding-3-small` |
| `.env` | `EMBEDDING_DIMENSIONS` | 默认 `1536`，须与模型一致 |
| `.env` | `RAG_DATA_ROOT` | 默认 `./data/rag` |
| `config/rag.yaml` | `chunk_size` / `chunk_overlap` | 可被环境变量覆盖 |

Embedding **直连**上游 `/embeddings`（与 gateway 共用 `LLM_BASE_URL` / `LLM_API_KEY`），全仓库统一一种方式。

---

## API 说明

### 1. 提交索引任务（指定路径）

`POST /internal/index`

```json
{
  "kb_id": "lab-demo",
  "version": 1,
  "source_uri": "samples/hello.txt"
}
```

`source_uri` 为相对 `RAG_DATA_ROOT` 的路径，禁止 `..` 穿越。

**响应（202 语义，HTTP 200）示例：**

```json
{
  "task_id": "uuid",
  "status": "pending",
  "kb_id": "lab-demo",
  "version": 1,
  "source_uri": "samples/hello.txt"
}
```

### 2. 上传并索引

`POST /internal/index/upload`（`multipart/form-data`）

- `kb_id`、`version`：表单字段  
- `file`：文件  

保存到 `data/rag/uploads/{kb_id}/v{version}/` 后异步索引。

### 3. 查询任务状态

`GET /internal/index/tasks/{task_id}`

| status | 含义 |
|--------|------|
| `pending` | 已创建，等待后台执行 |
| `running` | 正在 chunk / embed / 写入 |
| `success` | 完成，`chunks_indexed` 有值 |
| `failed` | 失败，`error` 有原因（如非 UTF-8、文件不存在） |

### 4. 列出知识库版本

`GET /internal/kb/{kb_id}/versions`

返回已写入 Qdrant 的 `versions` 列表与 `latest`。

### 5. 对内检索

`POST /internal/retrieve`

```json
{
  "kb_id": "lab-demo",
  "version": 1,
  "query": "RAG 数据管道",
  "top_k": 5
}
```

- 省略 `version` 时使用该 `kb_id` 的 **最新已索引版本**（`max(version)`）。
- 每条 `chunks[]` 含：`chunk_id`、`kb_id`、`version`、`source_uri`、`offset`、`text`、`score`。

---

## 演示命令（可复制）

公共头（admin）：

```bash
export GW=http://127.0.0.1:8000
export H1="X-Tenant-Id: admin"
export H2="Authorization: Bearer sk-tenant-admin-change-me"
```

**索引 v1：**

```bash
curl -s "$GW/internal/index" \
  -H "Content-Type: application/json" -H "$H1" -H "$H2" \
  -d '{"kb_id":"lab-demo","version":1,"source_uri":"samples/hello.txt"}' | jq .
```

记下 `task_id`，轮询直到 `success`：

```bash
curl -s "$GW/internal/index/tasks/<task_id>" -H "$H1" -H "$H2" | jq .
```

**索引 v2（换文件）：**

```bash
curl -s "$GW/internal/index" \
  -H "Content-Type: application/json" -H "$H1" -H "$H2" \
  -d '{"kb_id":"lab-demo","version":2,"source_uri":"samples/hello-v2.txt"}' | jq .
```

**检索指定 v1：**

```bash
curl -s "$GW/internal/retrieve" \
  -H "Content-Type: application/json" -H "$H1" -H "$H2" \
  -d '{"kb_id":"lab-demo","version":1,"query":"第 2 周","top_k":3}' | jq .
```

**检索默认最新（应为 v2 内容）：**

```bash
curl -s "$GW/internal/retrieve" \
  -H "Content-Type: application/json" -H "$H1" -H "$H2" \
  -d '{"kb_id":"lab-demo","query":"默认最新","top_k":3}' | jq .
```

**故意失败（损坏文件）：**

```bash
printf '\xff\xfe' > data/rag/samples/bad.bin
curl -s "$GW/internal/index" \
  -H "Content-Type: application/json" -H "$H1" -H "$H2" \
  -d '{"kb_id":"lab-demo","version":99,"source_uri":"samples/bad.bin"}' | jq .
# 查询 task → status=failed, error 含 UTF-8
```

---

## 验收对照（手册）

| 验收项 | 如何验证 |
|--------|----------|
| 同一 `kb_id` v1→v2 两次索引，检索可指定版本或默认最新 | 上节演示命令 |
| 损坏文件 → `failed` 且可查错误 | `bad.bin` + `GET .../tasks/{id}` |
| 检索结果每条含 `kb_id`、`version`、`chunk_id` | `POST /internal/retrieve` 响应字段 |

---

## 代码结构

| 路径 | 职责 |
|------|------|
| `packages/rag/chunker.py` | 固定窗口切分，`source_uri` + `offset` |
| `packages/rag/embeddings.py` | 直连 `/embeddings` |
| `packages/rag/vector_store.py` | Qdrant 写入 / 检索 / 列版本 |
| `apps/gateway/rag/task_store.py` | 内存任务表 |
| `apps/gateway/rag/pipeline.py` | 索引编排 |
| `apps/gateway/rag/routes.py` | `/internal/*` 路由 |
| `packages/contracts/rag_schemas.py` | 请求/响应模型 |

任务在 **gateway 进程内** `BackgroundTasks` 执行；`apps/worker` 保留说明入口，第 5 周后可拆队列。

---

## 已知限制

- 任务状态仅存内存，**进程重启丢失**。
- 仅支持 **UTF-8 文本** 文件索引。
- 无 BM25；混合检索留第 3 周。
- Qdrant 未启动时索引/检索返回 `VECTOR_STORE_ERROR` / `RETRIEVE_ERROR`。

---

*文档版本：v1 | 对应仓库第 2 周实现。*
