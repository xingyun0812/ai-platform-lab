# 本地 LLM 联调配置

> **用途**：把 Gateway / Agent / SDK / RAG 接到内网 OpenAI 兼容聚合网关。  
> **模型清单**：[`config/upstream_models.yaml`](../config/upstream_models.yaml)  
> **安全**：真实 Key 只写在根目录 `.env`（已 gitignore）。

---

## 上游网关

| 项 | 值 |
|----|-----|
| Base URL | `http://10.212.129.94:8090/v1` |
| Chat | `POST /v1/chat/completions` |
| Embedding | `POST /v1/embeddings` |
| Rerank | `POST /v1/rerank` |

---

## `.env` 推荐配置

```bash
LLM_BASE_URL=http://10.212.129.94:8090/v1
LLM_API_KEY=sk-your-key-here

# Chat 默认
DEFAULT_MODEL=deepseek-v4-flash
AGENT_MODEL=deepseek-v4-flash
RAG_QUERY_MODEL=deepseek-v4-flash

# Embedding（RAG 索引/检索）
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B
EMBEDDING_DIMENSIONS=4096
QDRANT_COLLECTION=ai_platform_lab_qwen8b

# Rerank（可选）
RAG_RERANK_API_URL=http://10.212.129.94:8090/v1/rerank
RAG_RERANK_MODEL=Qwen3-Reranker-8B
# RAG_RERANK_ENABLED=true
# RAG_RERANK_MODE=api
```

> **注意**：Embedding 为 **4096 维**，与旧 `text-embedding-3-small`（1536）不兼容。换新 `QDRANT_COLLECTION` 或清空 Qdrant 后重新索引。

---

## 模型一览

### Chat（别名 → 上游 id）

| 别名 | 上游模型 | 用途 |
|------|---------|------|
| `chat-fast` | `deepseek-v4-flash` | 默认快模型 |
| `chat-thinking` | `deepseek-v4-flash-thinking` | 推理 |
| `chat-minimax` | `minimax-m2.7` | 备选 |
| `chat-deepseek-r1` | `deepseek-ai/DeepSeek-R1` | 强推理 |
| `chat-qwen3-32b` | `Qwen/Qwen3-32B` | Qwen 文本 |
| `chat-qwen3-235b` | `Qwen/Qwen3-235B-A22B-Instruct` | 大模型 |
| … | 见 `config/models.yaml` | |

### Embedding

| 模型 | 维度 | API |
|------|------|-----|
| `Qwen/Qwen3-Embedding-8B` | **4096** | `/v1/embeddings` |

### Rerank

| 模型 | API | 说明 |
|------|-----|------|
| `Qwen3-Reranker-8B` | `/v1/rerank` | 勿用 `Qwen/` 前缀 |

---

## 快速验证

```bash
# Embedding
curl -s http://10.212.129.94:8090/v1/embeddings \
  -H "Authorization: Bearer $LLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3-Embedding-8B","input":"hello"}' | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d['data'][0]['embedding']))"
# 期望输出: 4096

# Rerank
curl -s http://10.212.129.94:8090/v1/rerank \
  -H "Authorization: Bearer $LLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen3-Reranker-8B","query":"hello","documents":["hi","bye"]}'

# Chat（经 Gateway）
uvicorn apps.gateway.main:app --host 127.0.0.1 --port 8000
./eval/platform_demo.sh --with-llm
```

---

## 相关文件

| 文件 | 内容 |
|------|------|
| `config/upstream_models.yaml` | 网关模型全集 + 说明 |
| `config/models.yaml` | Chat 别名与 fallback |
| `config/embedding_models.yaml` | Embedding 注册表 |
| `config/rag.yaml` | Rerank 默认 model 名 |
