# 第 3 周：RAG 服务化与质量底线

学习计划见 [AI中台学习执行手册](./AI中台学习执行手册.md) 第 3 周。  
构建思路与代码导读见 [rag-query-build-and-code-guide.md](./rag-query-build-and-code-guide.md)。  
第 2 周索引与对内检索见 [week2-rag-pipeline.md](./week2-rag-pipeline.md)。

---

## 目标

对外 `POST /v1/rag/query`：**检索 → 拼上下文 → LLM 回答**；空检索 / 低分 **拒答**（业务 `error.code`，HTTP 422）；返回 **引用**（`citations` / `chunk_id`）与 **分阶段耗时**。

---

## 架构

```mermaid
flowchart LR
  C["客户端"] --> Q["POST /v1/rag/query"]
  Q --> R["retrieve_chunks"]
  R --> F["min_score 过滤"]
  F -->|无依据| X["422 RAG_NO_EVIDENCE / RAG_LOW_CONFIDENCE"]
  F -->|通过| P["rag_prompt.txt"]
  P --> L["forward_chat_completions"]
  L --> C
```

---

## 前置条件

1. 第 2 周环境：Qdrant、`LLM_API_KEY`、网关已启动。  
2. 已对 `lab-demo` 索引至少一个版本（见 [week2-rag-pipeline.md](./week2-rag-pipeline.md)）。

---

## API：`POST /v1/rag/query`

鉴权与第 1 周相同：`X-Tenant-Id` + `Authorization: Bearer`。

### 请求体

```json
{
  "tenant_id": "admin",
  "kb_id": "lab-demo",
  "version": 1,
  "query": "RAG 数据管道是什么",
  "top_k": 5,
  "min_score": 0.35,
  "model": null
}
```

| 字段 | 说明 |
|------|------|
| `tenant_id` | 须与头 `X-Tenant-Id` 一致 |
| `version` | 可选，省略用最新已索引版本 |
| `min_score` | 可选，省略用 `RAG_MIN_SCORE` / `config/rag.yaml` |
| `model` | 可选，省略用 `RAG_QUERY_MODEL` 或 `DEFAULT_MODEL` |

### 成功响应（HTTP 200）

```json
{
  "tenant_id": "admin",
  "kb_id": "lab-demo",
  "version": 1,
  "query": "...",
  "answer": "...",
  "citations": [
    {"chunk_id": "lab-demo:1:0:0", "kb_id": "lab-demo", "version": 1, "source_uri": "samples/hello.txt", "score": 0.82}
  ],
  "timings": {"retrieve_ms": 120.5, "llm_ms": 800.2, "total_ms": 950.0},
  "model": "gpt-4o-mini",
  "min_score": 0.35,
  "trace_id": "..."
}
```

### 业务拒答（HTTP 422，`error.code`）

| code | 含义 |
|------|------|
| `RAG_NO_EVIDENCE` | 检索零条 |
| `RAG_LOW_CONFIDENCE` | 有候选但均低于 `min_score` |
| `RAG_KB_NOT_FOUND` | 知识库尚无索引版本 |

配额用尽为 **HTTP 429** `QUOTA_EXCEEDED`（仅在即将调用 LLM 前扣减，拒答不消耗配额）。

---

## Prompt 模板

文件：[config/rag_prompt.txt](../config/rag_prompt.txt)，占位符 `{context}`、`{query}`。  
修改后重启网关（settings 启动时读路径，模板每次请求读取文件）。

---

## 演示命令

```bash
export GW=http://127.0.0.1:8000
export H1="X-Tenant-Id: admin"
export H2="Authorization: Bearer sk-tenant-admin-change-me"

# 应命中
curl -s "$GW/v1/rag/query" \
  -H "Content-Type: application/json" -H "$H1" -H "$H2" \
  -d '{"tenant_id":"admin","kb_id":"lab-demo","version":1,"query":"RAG 数据管道是什么"}' | jq .

# 应拒答（库外问题）
curl -s "$GW/v1/rag/query" \
  -H "Content-Type: application/json" -H "$H1" -H "$H2" \
  -d '{"tenant_id":"admin","kb_id":"lab-demo","query":"2026年诺贝尔物理学奖得主"}' | jq .

# 极高阈值 → RAG_LOW_CONFIDENCE
curl -s "$GW/v1/rag/query" \
  -H "Content-Type: application/json" -H "$H1" -H "$H2" \
  -d '{"tenant_id":"admin","kb_id":"lab-demo","query":"RAG","min_score":0.99}' | jq .

# 观察 timings（连打 10 次对比 retrieve_ms / llm_ms）
for i in $(seq 1 10); do
  curl -s "$GW/v1/rag/query" -H "Content-Type: application/json" -H "$H1" -H "$H2" \
    -d '{"tenant_id":"admin","kb_id":"lab-demo","query":"第 2 周"}' | jq '.timings';
done
```

---

## 评测集

[eval/baseline.jsonl](../eval/baseline.jsonl)：**35 条**，`expect` 为 `hit` / `refuse`，部分用例带 `min_score`、`top_k` 以观察阈值与 top_k 行为（第 5 周 `eval/run.py` 将消费此文件）。

---

## 验收对照

| 手册验收项 | 验证方式 |
|------------|----------|
| 库外问题拒答 | `refuse-*` curl 或 baseline |
| trace 区分检索/总耗时 | 响应 `timings` + 日志 `rag_query` |
| 改 top_k / 阈值可观察差异 | `threshold-02` vs `threshold-01` |

---

## 已知限制

- **仅向量检索**；BM25 混合检索 TODO（见 `config/rag.yaml` 注释）。  
- 拒答依赖检索分数阈值，非 LLM 二次判断。  
- `eval/run.py` 第 5 周实现，当前仅维护 JSONL。

---

*文档版本：v1 | 第 3 周实现。*
