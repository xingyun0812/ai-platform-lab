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

## 对比 stub vs api

```bash
RAG_RERANK_MODE=stub python eval/run.py run --run-id before-rerank
RAG_RERANK_MODE=api  python eval/run.py run --run-id after-rerank
python eval/run.py compare eval/runs/before-rerank.json eval/runs/after-rerank.json
```

## 测试

```bash
python3 tests/test_rerank_providers.py
```
