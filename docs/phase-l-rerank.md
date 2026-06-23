# Phase L #54 — 真 Rerank Provider

> **状态**：✅ stub / api / local 三模式可切换

## 模式

| 模式 | 环境变量 | 说明 |
|------|----------|------|
| `stub` | `RAG_RERANK_MODE=stub` | 词面重合（默认，无外部依赖） |
| `api` | `RAG_RERANK_API_URL` + Key | HTTP POST `{query, documents}` → `{results:[{index, score}]}` |
| `local` | `RAG_RERANK_MODE=local` | 占位，当前回退 stub |

## 启用

```bash
RAG_RERANK_ENABLED=true
RAG_RERANK_MODE=stub   # 或 api
```

响应 `_platform.rerank` 含 `provider` / `mode`。

## 对比 stub vs api（Qwen3-Reranker-8B）

内网网关已配置时（见 [local-llm-setup.md](./local-llm-setup.md)）：

```bash
# .env
RAG_RERANK_ENABLED=true
RAG_RERANK_MODE=api
RAG_RERANK_API_URL=http://10.212.129.94:8090/v1/rerank
RAG_RERANK_MODEL=Qwen3-Reranker-8B
```

本地快速对比（无需 Gateway）：

```bash
set -a && source .env && set +a
python3 - <<'PY'
from packages.contracts.rag_schemas import RetrievedChunk
from packages.rag.rerank import rerank_chunks
import os
chunks = [
    RetrievedChunk(chunk_id="a", kb_id="k", version=1, source_uri="s", offset=0, text="无关", score=0.9),
    RetrievedChunk(chunk_id="b", kb_id="k", version=1, source_uri="s", offset=0, text="RAG 数据管道", score=0.5),
]
cfg = {"api_url": os.environ["RAG_RERANK_API_URL"], "model": os.environ["RAG_RERANK_MODEL"], "api_key": os.environ["LLM_API_KEY"]}
for mode in ("stub", "api"):
    top, ms = rerank_chunks("RAG 数据管道", chunks, top_n=2, mode=mode, provider_config=cfg if mode=="api" else None)
    print(mode, "top=", top[0].chunk_id, "ms=", round(ms, 1))
PY
```

RAG eval 全量对比：

```bash
RAG_RERANK_MODE=stub python eval/run.py run --run-id before-rerank
RAG_RERANK_MODE=api  python eval/run.py run --run-id after-rerank
python eval/run.py compare eval/runs/before-rerank.json eval/runs/after-rerank.json
```

## 测试

```bash
python3 tests/test_rerank_providers.py
```
